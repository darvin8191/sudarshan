"""The outside heartbeat: the one thing that can tell the owner the whole server is silent (M19.5).

Everything else the watch tells goes through OpenAlgo and the alerts service, on this server. So
each run also asks for one web address the owner has set up with a heartbeat service
(``SUDARSHAN_WATCH_PING_URL`` in ``/etc/sudarshan/watch.env``): the plain link while notices can
go out, and its failure form (``SUDARSHAN_WATCH_PING_FAIL_URL``, else the link with ``/fail``
added) while OpenAlgo or the alerts service is not answering or the watch could not keep its own
files. When no request arrives for the time the owner chose there, that service tells him through
its own channel. Without a link the watch does everything else.

The link is a random address that says nothing about the account, but it is still kept out of
every log line, error and report. Nothing is sent with the request: no body, no figure, no name.
One try, 5 seconds, no retry: the next run is the retry.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from . import checks

PING_ENV = "SUDARSHAN_WATCH_PING_URL"
FAIL_ENV = "SUDARSHAN_WATCH_PING_FAIL_URL"
TIMEOUT_S = 5.0
#: The checks that carry the notices: while one of them is failing, a notice cannot go out.
NOTICE_PATH = ("openalgo", "service_alerts")
NOT_SET, BAD_LINK, SENT, SENT_FAILURE, NOT_SENT = (
    "not_set",
    "bad_link",
    "sent",
    "sent_failure",
    "not_sent",
)
#: What the System page says for each (a later release shows it).
WORDS = {
    NOT_SET: "No outside heartbeat is set up: nobody is told if the whole server stops.",
    BAD_LINK: "The outside heartbeat's link in /etc/sudarshan/watch.env is not a web address "
    "that can be used. It must start with https://.",
    SENT: "The outside heartbeat was sent.",
    SENT_FAILURE: "The outside heartbeat was told that notices cannot go out.",
    NOT_SENT: "The outside heartbeat could not be reached just now.",
}
_LINK = re.compile(r"https://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?(/[A-Za-z0-9._~%/-]{0,200})?")
_LOCAL = re.compile(r"https://(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.|\[)", re.IGNORECASE)


def usable(link: str) -> bool:
    """A plain https address of an outside host: no user, no query, no fragment, no space."""
    return bool(_LINK.fullmatch(link)) and not _LOCAL.match(link)


def links(env: Mapping[str, str]) -> tuple[str, str] | str:
    """The link and its failure form from the unit's environment, or NOT_SET or BAD_LINK."""
    link = (env.get(PING_ENV) or "").strip().strip("'\"")
    if not link:
        return NOT_SET
    fail = (env.get(FAIL_ENV) or "").strip().strip("'\"") or link.rstrip("/") + "/fail"
    return (link, fail) if usable(link) and usable(fail) else BAD_LINK


def notices_can_go(results: list[checks.Result]) -> bool:
    """False while OpenAlgo or the alerts service is failing (two runs without an answer)."""
    return not any(r.name in NOTICE_PATH and r.level == checks.FAILING for r in results)


def request(link: str, timeout: float = TIMEOUT_S) -> bool:
    """One GET of the link; True when the service answered 2xx. Never raises, never logs."""
    try:
        ask = urllib.request.Request(link, headers={"User-Agent": "sudarshan-watch"})  # noqa: S310
        with urllib.request.urlopen(ask, timeout=timeout) as answer:  # noqa: S310
            answer.read(1024)
            return 200 <= answer.status < 300
    except urllib.error.HTTPError as refused:
        refused.close()
        return False
    except (OSError, ValueError):
        return False


def beat(env: Mapping[str, str], healthy: bool, send: Callable[[str], bool] = request) -> str:
    """Send this run's heartbeat; returns what happened (a key of WORDS)."""
    found = links(env)
    if isinstance(found, str):
        return found
    link, fail = found
    try:
        arrived = bool(send(link if healthy else fail))
    except Exception:
        arrived = False
    if not arrived:
        return NOT_SENT
    return SENT if healthy else SENT_FAILURE
