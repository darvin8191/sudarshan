"""The Analytics Token as the portfolio's own reads find it (M13.12, M19.5), and the start-up probe
(cross-design Y41).

**The token check** is published in ``/health`` as ``analytics_token``: a state and a time and
nothing else, for the server's watch and the System page. It is judged only from the reads the
token is known to make, the ones the Upstox tab shows (``upstox.JUDGES``: holdings and positions).
Each of those Upstox answers says it: ``ok`` when Upstox answered the read (200), ``failed`` when
it refused the token (401). No call is made for it: the Upstox tab, the family view and the
closing record after each trading day read them anyway. Nothing else decides it, the probe below
included: whether Upstox lets this token read the account's orders is not known, and a refusal
there may only mean that it may not (v0.16.0 review H1). The last result is kept in the service's
state folder (``token-check``), written when it says something new and once a day otherwise, so a
restart shows it. A token file that changed after the read has not been judged: the state is
``unknown`` until the next read, never the old token's result.

**The start-up probe.** Two designs (the family view, the journal) depend on one fact nobody has
verified: whether the read-only Analytics Token may call ``retrieve-all``, ``get-trades-for-day``
and the order history. A moment after the first start of a day, this logs the HTTP status each
answered and nothing from the body, so the owner reads the line with
``journalctl -u sudarshan-portfolio | grep "Analytics Token probe"``. It spends one read of each
API's own budget as the owner, sends the token the way the reader does, and changes no state of
the reader or of the token check: what it found is that line and nothing else.

At most once a day (v0.10.0 review L7): the IST day of the last probe Upstox answered is kept in
the service's state folder (``probe-day``), and a later start that day asks nothing. A probe that
reached no answer (no token, no network) is not counted, so the next start tries again.

The order history is asked for an order id that cannot exist: an answer that is not 401 or 403
tells that the token may read the endpoint at all; the status is what is logged, never a body.

**Asked again** (v0.17.0 verification V2). A holdings or positions read Upstox refused pauses
every read until the token file changes; a refusal that was Upstox's passing fault would keep the
line failing until the next release. So while it holds, the job ``token_retry`` lets one holdings
read through (``UpstoxReader.ask_again``) 15 minutes after the last refusal or try, if that falls
in market hours (09:15 to 15:30 on a trading day by the exchange's calendar, a special day's own
window), else at the first of the next session's start and a trading day's 08:00 (before the
morning brief) or 15:45 (before the closing record), v0.17.0 review L1: never sooner than 15
minutes after the last, and through the reader's budget and 429 pause. A refusal on a Friday at
15:40 is asked again on Monday at 08:00, not 65 hours later at 09:15. The first 200 ends the pause
and tells the check ``ok``: the watch then says the token is accepted again.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import IST

from .core.days import day_of_instant
from .upstox import API_OF, _signature

log = logging.getLogger("sudarshan.portfolio.probe")

#: A moment after start, so the feed link and the store are up before the first read.
DELAY_S = 20.0
#: The IST day (YYYYMMDD) of the last probe Upstox answered, in the service's state folder.
DAY_FILE = "probe-day"
#: The token check's last result: {"at": epoch seconds, "state": "ok" or "failed", "sig": the
#: token file's time and size then}. Never the token, a status or anything Upstox said.
RESULT_FILE = "token-check"
OK, FAILED, UNKNOWN = "ok", "failed", "unknown"
PROBES = (
    ("orders", "/v2/order/retrieve-all", {}),
    ("trades", "/v2/order/trades/get-trades-for-day", {}),
    ("order_history", "/v2/order/history", {"order_id": "0"}),
)
#: A refused token's holdings read is asked again at most this often (verification V2).
RETRY_EVERY_MS = 15 * 60_000
#: The session whose hours are the market's for it: 09:15 to 15:30 on a usual trading day.
MARKET = "NSE_EQ"
#: Two more tries on a trading day, in IST minutes (v0.17.0 review L1): 08:00, before the morning
#: brief and the session, and 15:45, before the closing record (15:55; 16:00 from 7 Dec 2026).
EXTRA_TRIES_MIN = (8 * 60, 15 * 60 + 45)
#: How many days ahead a try is looked for, as ``sessions.next_session`` looks for a session.
HORIZON_DAYS = 15


async def probe(upstox: Any) -> dict[str, int | str]:
    """The status each endpoint answered, or ``"no token"``, ``"no answer"`` or ``"budget"``.
    Logs one line; the body of an answer is never read."""
    found: dict[str, int | str] = {}
    token = upstox._token()
    if token is None:
        found = {name: "no token" for name, _, _ in PROBES}
    else:
        for name, path, params in PROBES:
            if not upstox.budget.take(API_OF.get(name, name)):
                found[name] = "budget"
                continue
            try:
                answer = await upstox.client.get(
                    upstox.base_url + path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.HTTPError:
                found[name] = "no answer"
                continue
            found[name] = answer.status_code
    del token
    log.info(
        "Analytics Token probe: retrieve-all %s, get-trades-for-day %s, order history %s",
        found["orders"], found["trades"], found["order_history"],
    )  # fmt: skip
    return found


def probed_on(folder: Path) -> int | None:
    """The day of the last probe Upstox answered; None when none is recorded or it cannot be read."""
    try:
        return int((folder / DAY_FILE).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _write(folder: Path, name: str, text: str) -> bool:
    """``text`` as the whole of ``folder/name`` (600), or the old file. Never raises."""
    path, tmp = folder / name, folder / f".{name}.new"
    try:
        fd = os.open(
            tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("the portfolio's %s could not be recorded: %s", name, exc.strerror)
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True


def note_probed(folder: Path, day: int) -> None:
    """Record ``day`` as probed. Never raises: a day that cannot be written costs one more probe."""
    _write(folder, DAY_FILE, f"{day}\n")


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_result(folder: Path) -> dict[str, Any] | None:
    """The last result as ``note_result`` kept it; None when there is none or the file is cut off
    or not in that shape (it is then as if nothing had been judged)."""
    try:
        with open(folder / RESULT_FILE, encoding="ascii") as handle:
            kept = json.loads(handle.read(4096))
    except (OSError, ValueError):
        return None
    if not isinstance(kept, dict) or kept.get("state") not in (OK, FAILED):
        return None
    sig = kept.get("sig")
    if sig is not None and not (isinstance(sig, list) and len(sig) == 2 and all(map(_whole, sig))):
        return None
    if not _whole(kept.get("at")) or kept["at"] <= 0:
        return None
    return {"at": kept["at"], "state": kept["state"], "sig": sig}


def note_result(folder: Path, result: dict[str, Any]) -> None:
    """Keep the token check's result. Never raises."""
    _write(folder, RESULT_FILE, json.dumps(result, separators=(",", ":")) + "\n")


def heard(service: Any, state: str, at_ms: int, sig: tuple[int, int] | None) -> None:
    """A holdings or positions read Upstox answered (``UpstoxReader.on_verdict``): shown at once,
    and kept when it says something new (another state, another token file) or on a new day, so
    a tab open all day writes the file once. Never raises."""
    result = {"at": at_ms // 1000, "state": state, "sig": None if sig is None else list(sig)}
    old, service.token_check = service.token_check, result
    if (
        old is not None
        and (old["state"], old["sig"]) == (state, result["sig"])
        and day_of_instant(old["at"] * 1000) == day_of_instant(at_ms)
    ):
        return
    note_result(Path(service.settings.state_dir), result)


def view(result: dict[str, Any] | None, token_file: Path) -> dict[str, Any]:
    """What ``/health`` says of the token: ``state`` (ok, failed, unknown) and ``at``, the epoch
    seconds of the last holdings or positions read Upstox answered (None when there was none).

    ``changed`` is there when the token file is not the one that read used: the state is then
    unknown, whatever the old token's was."""
    if result is None:
        return {"state": UNKNOWN, "at": None}
    now = _signature(Path(token_file))
    changed = (None if now is None else list(now)) != result["sig"]
    shown: dict[str, Any] = {"state": UNKNOWN if changed else result["state"], "at": result["at"]}
    if changed:
        shown["changed"] = True
    return shown


async def at_start(service: Any, delay_s: float = DELAY_S) -> dict[str, int | str] | None:
    """The job: the kept token check shown from the first moment; then, a moment after start, the
    probe, unless Upstox answered one today already (then None). The day is recorded only when
    Upstox answered at least one of the three. What the probe found is logged, never judged."""
    folder = Path(service.settings.state_dir)
    if service.token_check is None:
        service.token_check = read_result(folder)
    await asyncio.sleep(delay_s)
    today = day_of_instant(service.clock())
    if probed_on(folder) == today:
        log.debug("the Analytics Token probe ran today already")
        return None
    found = await probe(service.ensure_upstox())
    if any(_whole(status) for status in found.values()):
        note_probed(folder, today)
    return found


def next_try(calendar: Any, since: int) -> int | None:
    """When a refused token's holdings read is asked again: 15 minutes after ``since`` (the last
    refusal or try, epoch ms) if that falls in the market's hours of its day, else the first of
    the next session's start and a trading day's 08:00 or 15:45 (review L1) that is at least 15
    minutes after ``since``; None when the calendar gives none in its horizon. So a refusal at
    15:20 is asked again at 15:45, and one at 15:40 at the next trading day's 08:00."""
    at = since + RETRY_EVERY_MS
    day = datetime.fromtimestamp(at / 1000, IST).date()
    hours = sessions.session(
        MARKET, day, trading_day=calendar.is_trading_day(day), window=calendar.day_window(day)
    )
    if hours is not None and hours.start <= at < hours.end:
        return at
    found = sessions.next_session(
        MARKET, at, is_trading_day=calendar.is_trading_day, window_of=calendar.day_window
    )
    start = None if found is None else found.start
    return min((when for when in (start, _slot(calendar, at)) if when is not None), default=None)


def _slot(calendar: Any, at: int, minutes: tuple[int, ...] = EXTRA_TRIES_MIN) -> int | None:
    """The first of ``minutes`` (IST) on a trading day at or after ``at``; None past the horizon."""
    first = datetime.fromtimestamp(at / 1000, IST).date()
    for offset in range(HORIZON_DAYS + 1):
        day = first + timedelta(days=offset)
        if calendar.is_trading_day(day):
            for minute in minutes:
                when = sessions.epoch_ms(day, minute)
                if when >= at:
                    return when
    return None


async def retry_step(service: Any, now: int) -> bool:
    """One holdings read when its time has come while Upstox refuses the token itself; True when
    it was asked. Nothing is read while the token is not refused, or before the reader exists."""
    reader = getattr(service, "upstox", None)
    since = None if reader is None else reader.paused_since()
    if since is None:
        return False
    due = next_try(service.ensure_calendar(), since)
    if due is None or now < due:
        return False
    await reader.ask_again()
    return True


def register_job() -> None:
    from .service import job
    from .timers import run_forever

    @job("probe")
    async def probe_job(service) -> None:
        try:
            await at_start(service)
        except Exception:
            log.exception("the Analytics Token probe could not run")

    @job("token_retry")
    async def token_retry_job(service) -> None:
        await run_forever(service, "token_retry", retry_step)
