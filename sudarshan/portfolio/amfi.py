"""Mutual-fund NAVs from AMFI's public daily file (M7.24, design 5.4, amendment P6).

``NAVAll.txt`` is fetched once at 23:30 IST and once more at 07:30 if that failed: one request each,
30 seconds, ``If-Modified-Since``. Its layout is not described on AMFI's page, so the parser reads
what it can recognise and drops the rest, and an answer with no scheme in it is a failure that
leaves the NAVs already held.

    Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;NAV;Date
    Open Ended Schemes(Equity Scheme - Large Cap Fund)      <- sets the category
    Some Mutual Fund                                        <- any other text line sets the AMC
    119551;INF209KA12Z1;INF209KA13Z9;Some Fund - Direct Plan;123.4500;04-Nov-2026

A NAV of ``N.A.`` (or one that is not a positive number) is skipped. About 15,000 schemes go to
``mf_scheme``; funds the owner holds also get ``price_eod`` rows with ``basis = nav`` at the NAV's
own date (the NAV settle). Records older than 3 trading days never change.

With the switch ``SUDARSHAN_PORTFOLIO_AMFI`` off nothing is fetched and a fund is a manual asset.
The module never writes the downloaded file anywhere and logs only counts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from email.utils import format_datetime
from typing import Any

import httpx

from .core.days import day_of_instant, from_date
from .core.kinds import PRICE_SCALE
from .store import Scope, Tx
from .timers import Schedule, run_forever

log = logging.getLogger("sudarshan.portfolio.amfi")

OWNER = Scope.owner()
TIMEOUT_S = 30.0
MAX_BYTES = 40_000_000
FETCH_AT_MIN = 23 * 60 + 30
RETRY_AT_MIN = 7 * 60 + 30
MONTHS = {m: n for n, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}  # fmt: skip
WORDS = {
    "off": "Mutual-fund prices are off. Enter a fund's NAV by hand.",
    "empty": "AMFI's list of schemes is not loaded yet. It is fetched every night around 11:30 PM.",
}


# ---- parsing ---------------------------------------------------------------------------------
def parse_date(text: str) -> int | None:
    parts = text.strip().split("-")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
        return None
    month = MONTHS.get(parts[1][:3].lower())
    if month is None or len(parts[2]) != 4:
        return None
    try:
        return from_date(date(int(parts[2]), month, int(parts[0])))
    except ValueError:
        return None


def parse_nav(text: str) -> int | None:
    """A NAV as price_u (4 decimals, half away from zero); None for N.A. or anything else."""
    try:
        value = Decimal(text.strip())
    except ArithmeticError:
        return None
    if not value.is_finite() or value <= 0:
        return None
    made = int((value * PRICE_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return made or None


def _isin(text: str) -> str | None:
    text = text.strip().upper()
    return text if len(text) == 12 and text.isalnum() else None


def parse_navall(text: str) -> list[dict[str, Any]]:
    """The schemes in the file. Unrecognised lines are dropped; none is an error here."""
    category = amc = None
    out: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ";" not in line:
            if "Schemes(" in line.replace(" (", "(") or line.lower().startswith(
                ("open ended", "close ended", "interval fund")
            ):
                category = line[:200]
            else:
                amc = line[:200]
            continue
        fields = [f.strip() for f in line.split(";")]
        if len(fields) < 6 or not fields[0].isdigit():
            continue  # the header row and anything else
        nav, when = parse_nav(fields[4]), parse_date(fields[5])
        if nav is None or when is None or not fields[3]:
            continue
        code = fields[0]
        out[code] = {
            "amfi_code": code,
            "isin_growth": _isin(fields[1]),
            "isin_reinvest": _isin(fields[2]),
            "name": fields[3][:300],
            "category": category,
            "amc": amc,
            "nav_u": nav,
            "nav_date": when,
            "prev_nav_u": None,
            "prev_nav_date": None,
        }
    return list(out.values())


# ---- the NAV settle: held funds get a price_eod at the NAV's own date -------------------------
def apply_navs(tx: Tx, today: int, sched: Schedule, now: int) -> int:
    """For each fund in the book, a closing record of basis ``nav`` at the date of its newest NAV
    (and of the one before it when none is there); a record frozen by age is left. Returns how
    many records were written."""
    written = 0
    for asset in tx.rows("asset", kind="mf"):
        scheme = tx.mf_get(asset["amfi_code"]) if asset["amfi_code"] else None
        if scheme is None or scheme["nav_u"] is None or scheme["nav_date"] is None:
            continue
        pairs = [(scheme["nav_date"], scheme["nav_u"], scheme["prev_nav_u"])]
        if scheme["prev_nav_u"] is not None and scheme["prev_nav_date"] is not None:
            pairs.append((scheme["prev_nav_date"], scheme["prev_nav_u"], None))
        for day, price, prev in pairs:
            if sched.frozen(day, today):
                continue
            have = tx.price_eod_get(asset["id"], day)
            if have is not None and (have["basis"], have["price_u"]) == ("nav", price):
                continue
            if have is not None and have["basis"] != "nav" and day != scheme["nav_date"]:
                continue
            tx.price_eod_put(
                asset["id"], day,
                {"price_u": price, "prev_close_u": prev, "basis": "nav", "source": "amfi",
                 "late": 0, "at": scheme["fetched_at"]},
                now,
            )  # fmt: skip
            written += 1
    return written


# ---- the fetch --------------------------------------------------------------------------------
async def fetch(
    client: httpx.AsyncClient, url: str, since_ms: int | None
) -> tuple[str, str | None]:
    """``("ok", text)``, ``("same", None)`` for 304, or ``("failed", why)`` in a sentence."""
    headers = {"Accept": "text/plain"}
    if since_ms:
        headers["If-Modified-Since"] = format_datetime(
            datetime.fromtimestamp(since_ms / 1000, UTC), usegmt=True
        )
    chunks: list[bytes] = []
    size = 0
    try:
        async with client.stream(
            "GET", url, headers=headers, timeout=TIMEOUT_S, follow_redirects=False
        ) as answer:
            if answer.status_code == 304:
                return "same", None
            if answer.status_code != 200:
                log.warning("AMFI's list was refused (%d)", answer.status_code)
                return "failed", "AMFI did not give its list."
            async for chunk in answer.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:  # stop reading: the rest is never held in memory
                    return "failed", "AMFI's list was far bigger than expected and was not read."
                chunks.append(chunk)
            encoding = answer.encoding or "utf-8"
    except httpx.HTTPError as exc:
        log.warning("AMFI's list was not fetched (%s)", type(exc).__name__)
        return "failed", "AMFI did not answer."
    return "ok", b"".join(chunks).decode(encoding, errors="replace")


async def fetch_and_store(service: Any, now: int) -> tuple[str, str]:
    """One attempt: fetch, parse, store, and settle the held funds. Returns (status, detail)."""
    store, client = service.store, service.amfi_client()
    since = await asyncio.get_running_loop().run_in_executor(
        None, lambda: store.read(OWNER, lambda tx: tx.mf_fetched_at())
    )
    state, body = await fetch(client, service.settings.amfi_url, since)
    if state == "failed":
        return "failed", body or ""
    if state == "same":
        return "ok", "AMFI had no newer list."
    schemes = await asyncio.get_running_loop().run_in_executor(None, parse_navall, body)
    if not schemes:
        log.error("AMFI's list had no scheme this program could read; it was not stored")
        return "failed", "AMFI's list was in a form SUDARSHAN could not read."
    sched, today = (
        Schedule(service.ensure_calendar(), eod_after_min=service.settings.eod_after_min),
        day_of_instant(now),
    )

    def write(tx: Tx) -> tuple[int, int]:
        return tx.mf_put_many(schemes, now), apply_navs(tx, today, sched, now)

    stored, settled = await asyncio.get_running_loop().run_in_executor(
        None, lambda: store.write(OWNER, write)
    )
    return "ok", f"{len(schemes)} schemes read, {stored} stored, {settled} NAV records"


def due_day(
    now: int, row: dict[str, Any] | None, previous_row: dict[str, Any] | None
) -> int | None:
    """The day a NAV fetch should fill now, or None. At 23:30 the day's own; from 07:30 the
    day before's, if it never ran or failed before 07:30. ``row`` is today's job row,
    ``previous_row`` yesterday's."""
    from .core.days import MS_PER_DAY, add_days

    local = (now + 19_800_000) % MS_PER_DAY // 60_000
    today = day_of_instant(now)
    if local >= FETCH_AT_MIN:
        return today if row is None else None
    if local >= RETRY_AT_MIN:
        yesterday = add_days(today, -1)
        morning = now - (local - RETRY_AT_MIN) * 60_000
        if previous_row is None or (
            previous_row["status"] == "failed" and previous_row["at"] < morning
        ):
            return yesterday
    return None


async def fetch_step(service: Any, now: int) -> str | None:
    from .core.days import add_days

    if service.store is None or not service.settings.amfi:
        return None
    today = day_of_instant(now)
    rows = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: service.store.read(
            OWNER,
            lambda tx: (
                tx.job_get("nav_fetch", today),
                tx.job_get("nav_fetch", add_days(today, -1)),
            ),
        ),
    )
    day = due_day(now, *rows)
    if day is None:
        return None
    status, detail = await fetch_and_store(service, now)
    for name in ("nav_fetch", "nav_settle"):
        shown = status if name == "nav_fetch" or status == "ok" else "skipped"
        await asyncio.get_running_loop().run_in_executor(
            None, lambda name=name, shown=shown: service.store.write(
                OWNER, lambda tx: tx.job_put(name, day, shown, detail, now))
        )  # fmt: skip
    log.info("NAV fetch %s: %s", status, detail)
    return status


def register_jobs() -> None:
    from .service import job

    @job("nav_fetch")
    async def nav_job(service) -> None:
        if not service.settings.amfi:
            log.info("Mutual-fund NAVs are off (SUDARSHAN_PORTFOLIO_AMFI); funds are manual")
            return
        await run_forever(service, "nav_fetch", fetch_step)
