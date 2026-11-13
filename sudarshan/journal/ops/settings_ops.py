"""``settings_get`` and ``settings_set`` (JB7, M15.10; design 6.3, cross-design Y38).

The owner's report settings are one document with one version: the trading capital, a switch per
report, the charges plan and the report language (English only in this release; the field
exists). The answer adds what the Reports page shows beside them:

* ``alerts_on``: false while nothing delivers the reports (Y38): the alerts service does not
  answer its /health on this server, or a report kept in the last day is still waiting for its
  outcome half an hour after it was made (the alerts' report desk is switched off).
* ``rate_card``: the charges card in use, its version and the day its newest rate applies from.
* ``times``: when each report is built, as the page prints them: the day ones for today, or for
  the next trading day when today has no session.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from ..core import charges, kinds
from ..core.money import ist_day, to_date
from ..errors import ApiError
from ..reports import clock_text
from ..schedule import FY_AT, schedule_for
from ..views import done
from . import op
from .common import OWNER, choice, flag, need, whole, work

FIELDS = ("capital_base_paise", "eod_on", "brief_on", "weekly_on", "monthly_on", "fy_on",
          "report_language", "charges_plan", "version")  # fmt: skip
SWITCHES = ("eod_on", "brief_on", "weekly_on", "monthly_on", "fy_on")
LANGUAGES = ("en",)
CAPITAL_MAX_PAISE = 10**13
#: A report not told about this long after it was made means no desk is delivering.
DESK_GRACE_MS = 30 * 60_000
DESK_WINDOW_MS = 24 * 3_600_000
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")  # fmt: skip
ENGLISH_ONLY = "Reports are in English only in this release."
HEALTH_TIMEOUT = httpx.Timeout(1.0, connect=0.5)


class AlertsProbe:
    """Asks the alerts service's /health whether it runs (a part: one client, closed with the
    service). Its health holds nothing secret."""

    def __init__(self, url: str):
        self.url = url.rstrip("/") + "/health"
        self.client = httpx.AsyncClient(timeout=HEALTH_TIMEOUT, trust_env=False)

    async def up(self) -> bool:
        try:
            answer = await self.client.get(self.url, follow_redirects=False)
            doc = answer.json()
        except (httpx.HTTPError, ValueError):
            return False
        return (
            answer.status_code == 200
            and isinstance(doc, dict)
            and (doc.get("service") == "sudarshan-alerts")
        )

    async def aclose(self) -> None:
        await self.client.aclose()


def _ordinal(n: int) -> str:
    tail = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{tail}"


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def report_times(service: Any, today: int) -> dict[str, Any]:
    """When each report is built, in the words the Reports page prints."""
    plan = schedule_for(service)
    day = to_date(today)
    for _ in range(15):
        if plan.trading(day):
            break
        day += timedelta(days=1)
    weekday, weekly_min = service.settings.weekly_at
    mday, monthly_min = service.settings.monthly_at
    month, fy_day, fy_min = FY_AT
    trading = plan.trading(day)
    return {
        "brief": clock_text(plan.brief_at(day)) if trading else None,
        "eod": clock_text(plan.eod_at(day)) if trading else None,
        "check": clock_text(plan.check_at(day)) if trading else None,
        "weekly": f"{WEEKDAY_NAMES[weekday]} {_hhmm(weekly_min)}",
        "monthly": f"the {_ordinal(mday)} at {_hhmm(monthly_min)}",
        "fy": f"{fy_day} {MONTH_NAMES[month - 1]} {_hhmm(fy_min)}",
    }


def rate_card(today: int, plan: str) -> dict[str, Any]:
    """The charges card in use: its version and the day its newest rate applies from."""
    dates = [r.from_day for r in charges.RATE_CARD if r.from_day <= today]
    dates += [r.from_day for r in charges.BROKERAGE_CARD if r.plan == plan and r.from_day <= today]
    return {"version": charges.CARD_VERSION, "from_day": max(dates, default=charges.CARD_START)}


async def alerts_on(service: Any, now: int) -> bool:
    probe = service.part("alerts", lambda svc: AlertsProbe(svc.settings.alerts_url))
    if not await probe.up():
        return False

    def waiting(tx) -> bool:
        return any(
            r["told_ms"] is None and now - DESK_WINDOW_MS <= r["built_ms"] <= now - DESK_GRACE_MS
            for r in tx.rows("report", order="built_ms,id")
        )

    return not await work(lambda: service.store.read(OWNER, waiting))


@op("settings_get")
async def settings_get(ctx, args):
    store = need(ctx)
    now = ctx.clock()
    found = await work(lambda: store.read(OWNER, lambda tx: tx.settings()))
    today = ist_day(now)
    return {
        **{name: found.get(name) for name in FIELDS},
        "alerts_on": await alerts_on(ctx, now),
        "rate_card": rate_card(today, found.get("charges_plan") or charges.DEFAULT_PLAN),
        "times": report_times(ctx, today),
    }


@op("settings_set")
async def settings_set(ctx, args):
    store = need(ctx)
    version = whole(args, "version", low=0)
    changes: dict[str, Any] = {}
    if "capital_base_paise" in args:
        value = args["capital_base_paise"]
        changes["capital_base_paise"] = (
            None if value is None
            else whole(args, "capital_base_paise", low=1, high=CAPITAL_MAX_PAISE)
        )  # fmt: skip
    for name in SWITCHES:
        if name in args:
            changes[name] = flag(args, name)
    if "charges_plan" in args:
        changes["charges_plan"] = choice(args, "charges_plan", kinds.CHARGE_PLANS)
    if "report_language" in args:
        if args["report_language"] not in LANGUAGES:
            raise ApiError(422, "refused", ENGLISH_ONLY, "report_language")
        changes["report_language"] = args["report_language"]
    now = ctx.clock()

    def write(tx):
        current = {k: v for k, v in tx.settings().items() if k != "version"}
        return tx.settings_save({**current, **changes}, version, now)["version"]

    new_version = await work(lambda: store.write(OWNER, write))
    return done(None, new_version, None)
