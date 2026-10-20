"""Before the owner's API key goes to a port on this server: who is listening there?

OpenAlgo and its WebSocket proxy run as the same user as this service (``sudarshan``). A port on
127.0.0.1 can be taken by any local user while OpenAlgo is down (a deploy, a crash), and the key
would then go to that program. So before every connection that carries the key, the listening
sockets on that port are looked up in ``/proc/net/tcp`` and ``/proc/net/tcp6`` (state 0A,
LISTEN), and the key is sent only when every listener a connection to the loopback address could
reach belongs to this process's own user. The answer can change between the check and the
connection; the check narrows that window to moments, it does not close it.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

TCP_TABLES = (Path("/proc/net/tcp"), Path("/proc/net/tcp6"))
LISTEN = "0A"
#: Addresses a connection to 127.0.0.1 or ::1 can reach, as /proc/net/tcp{,6} spells them
#: (each 32-bit word in the host's byte order, little-endian on the server).
REACHABLE = {
    "0100007F",  # 127.0.0.1
    "00000000",  # 0.0.0.0
    "00000000000000000000000001000000",  # ::1
    "00000000000000000000000000000000",  # ::
    "0000000000000000FFFF00000100007F",  # ::ffff:127.0.0.1
}


def listener_uids(port: int, tables=TCP_TABLES) -> set[int] | None:
    """The users owning loopback-reachable LISTEN sockets on ``port``; None if unreadable."""
    uids: set[int] = set()
    readable = False
    for table in tables:
        try:
            lines = table.read_text(encoding="ascii", errors="replace").splitlines()[1:]
        except FileNotFoundError:
            continue  # no IPv6 on this host
        except OSError:
            return None
        readable = True
        for line in lines:
            fields = line.split()
            if len(fields) < 8 or fields[3] != LISTEN:
                continue
            address, _, port_hex = fields[1].partition(":")
            try:
                if int(port_hex, 16) == port and address.upper() in REACHABLE:
                    uids.add(int(fields[7]))
            except ValueError:
                continue
    return uids if readable else None


OURS, NOBODY, OTHER, UNKNOWN = "ours", "nobody", "other", "unknown"


def _port(url: str) -> int:
    parts = urlsplit(url)
    return parts.port or (443 if parts.scheme in ("https", "wss") else 80)


def holder(url: str, tables=TCP_TABLES) -> str:
    """Who listens on ``url``'s port: OURS (this user only), NOBODY, OTHER or UNKNOWN."""
    uids = listener_uids(_port(url), tables)
    if uids is None:
        return UNKNOWN
    if not uids:
        return NOBODY
    return OURS if uids == {os.getuid()} else OTHER


def refusal(url: str, tables=TCP_TABLES) -> str | None:
    """None when the key may go to ``url``; otherwise why not, in words."""
    port = _port(url)
    return {
        OURS: None,
        NOBODY: f"nothing listens on port {port} of this server",
        OTHER: f"port {port} of this server is held by another user's program",
        UNKNOWN: f"it cannot be checked who listens on port {port} of this server",
    }[holder(url, tables)]
