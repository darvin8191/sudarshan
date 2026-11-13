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
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from .core.days import day_of_instant
from .upstox import API_OF

log = logging.getLogger("sudarshan.portfolio.probe")

#: A moment after start, so the feed link and the store are up before the first read.
DELAY_S = 20.0
#: The IST day (YYYYMMDD) of the last probe Upstox answered, in the service's state folder.
DAY_FILE = "probe-day"
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


def note_probed(folder: Path, day: int) -> None:
    """Record ``day`` as probed. Never raises: a day that cannot be written costs one more probe."""
    path, tmp = folder / DAY_FILE, folder / f".{DAY_FILE}.new"
    try:
        fd = os.open(
            tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{day}\n")
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("the probe's day could not be recorded: %s", exc.strerror)
        with contextlib.suppress(OSError):
            tmp.unlink()


async def at_start(service: Any, delay_s: float = DELAY_S) -> dict[str, int | str] | None:
    """The job: a moment after start, the probe, unless Upstox answered one today already (then
    None). The day is recorded only when Upstox answered at least one of the three."""
    await asyncio.sleep(delay_s)
    folder = Path(service.settings.state_dir)
    today = day_of_instant(service.clock())
    if probed_on(folder) == today:
        log.debug("the Analytics Token probe ran today already")
        return None
    found = await probe(service.ensure_upstox())
    if any(isinstance(status, int) for status in found.values()):
        note_probed(folder, today)
    return found


def register_job() -> None:
    from .service import job

    @job("probe")
    async def probe_job(service) -> None:
        try:
            await at_start(service)
        except Exception:
            log.exception("the Analytics Token probe could not run")
