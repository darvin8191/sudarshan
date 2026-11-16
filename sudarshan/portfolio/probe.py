"""The start-up probe (cross-design Y41): can the Analytics Token read the account's orders?

Two designs (the family view, the journal) depend on one fact nobody has verified: whether the
read-only Analytics Token may call ``retrieve-all``, ``get-trades-for-day`` and the order history.
A moment after the first start of a day, this logs the HTTP status each answered and nothing from
the body, so the owner reads the line with
``journalctl -u sudarshan-portfolio | grep "Analytics Token probe"``. It spends one read of each
API's own budget as the owner, sends the token the way the reader does, and changes no state of
the reader (a 401 here does not pause the reads of the Upstox tab).

At most once a day (v0.10.0 review L7): the IST day of the last probe Upstox answered is kept in
the service's state folder (``probe-day``), and a later start that day asks nothing. A probe that
reached no answer (no token, no network) is not counted, so the next start tries again.

The order history is asked for an order id that cannot exist: an answer that is not 401 or 403
tells that the token may read the endpoint at all; the status is what is logged, never a body.

**What the check found is published** (M13.12, M19.5): ``/health`` carries ``analytics_token``, a
state and a time and nothing else, for the server's watch and the System page. The result of the
last check Upstox answered is kept beside the day (``probe-result``), so a restart later that day
still shows it. No call is added for this: it is the result of the check above. ``failed`` is
"Upstox refused the token at every read that answered" (401); a read the token may not make
(403) is not a refused token. A token file that changed after the check has not been checked:
the state is ``unknown`` until the next check, never the old token's result.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from .core.days import day_of_instant
from .upstox import API_OF, _signature

log = logging.getLogger("sudarshan.portfolio.probe")

#: A moment after start, so the feed link and the store are up before the first read.
DELAY_S = 20.0
#: The IST day (YYYYMMDD) of the last probe Upstox answered, in the service's state folder.
DAY_FILE = "probe-day"
#: The last check Upstox answered: {"at": epoch seconds, "state": "ok" or "failed", "sig": the
#: token file's time and size then}. Never the token, a status or anything Upstox said.
RESULT_FILE = "probe-result"
OK, FAILED, UNKNOWN = "ok", "failed", "unknown"
PROBES = (
    ("orders", "/v2/order/retrieve-all", {}),
    ("trades", "/v2/order/trades/get-trades-for-day", {}),
    ("order_history", "/v2/order/history", {"order_id": "0"}),
)


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
        log.debug("the probe's %s could not be recorded: %s", name, exc.strerror)
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True


def note_probed(folder: Path, day: int) -> None:
    """Record ``day`` as probed. Never raises: a day that cannot be written costs one more probe."""
    _write(folder, DAY_FILE, f"{day}\n")


def judged(found: dict[str, int | str]) -> str | None:
    """What a probe says of the token: ``failed`` when Upstox refused it (401) at every read that
    answered, ``ok`` when any read answered otherwise, None when none answered."""
    answered = [status for status in found.values() if isinstance(status, int)]
    if not answered:
        return None
    return FAILED if all(status == 401 for status in answered) else OK


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_result(folder: Path) -> dict[str, Any] | None:
    """The last answered check as ``note_result`` kept it; None when there is none or the file
    is cut off or not in that shape (it is then as if no check had been kept)."""
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
    """Keep the result of a check Upstox answered. Never raises."""
    _write(folder, RESULT_FILE, json.dumps(result, separators=(",", ":")) + "\n")


def view(result: dict[str, Any] | None, missed_at: int | None, token_file: Path) -> dict[str, Any]:
    """What ``/health`` says of the token: ``state`` (ok, failed, unknown) and ``at``, the epoch
    seconds of the last check Upstox answered (None when there was none).

    ``changed`` is there when the token file is not the one that was checked: the state is then
    unknown, whatever the old token's was. ``missed_at`` is there when a later check got no
    answer at all (no token file, no network, no budget), so the result is older than the
    service's last start."""
    if result is None:
        shown: dict[str, Any] = {"state": UNKNOWN, "at": None}
    else:
        now = _signature(Path(token_file))
        changed = (None if now is None else list(now)) != result["sig"]
        shown = {"state": UNKNOWN if changed else result["state"], "at": result["at"]}
        if changed:
            shown["changed"] = True
    if missed_at is not None and (result is None or missed_at > result["at"]):
        shown["missed_at"] = missed_at
    return shown


async def at_start(service: Any, delay_s: float = DELAY_S) -> dict[str, int | str] | None:
    """The job: a moment after start, the probe, unless Upstox answered one today already (then
    None). The day is recorded only when Upstox answered at least one of the three, and so is
    the result ``/health`` shows; what an earlier start found is shown from the first moment."""
    folder = Path(service.settings.state_dir)
    service.token_check = read_result(folder)
    await asyncio.sleep(delay_s)
    today = day_of_instant(service.clock())
    if probed_on(folder) == today:
        log.debug("the Analytics Token probe ran today already")
        return None
    upstox = service.ensure_upstox()
    checked = _signature(Path(upstox.token_file))
    found = await probe(upstox)
    state, at = judged(found), service.clock() // 1000
    if state is None:
        service.token_missed_at = at
        return found
    note_probed(folder, today)
    result = {"at": at, "state": state, "sig": None if checked is None else list(checked)}
    note_result(folder, result)
    service.token_check, service.token_missed_at = result, None
    return found


def register_job() -> None:
    from .service import job

    @job("probe")
    async def probe_job(service) -> None:
        try:
            await at_start(service)
        except Exception:
            log.exception("the Analytics Token probe could not run")
