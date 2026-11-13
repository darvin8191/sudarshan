"""``python -m sudarshan.family.check``: what the family account can and cannot reach (P6 2.8;
cross-design Y2.3, Y3.2, Y25).

The deploy tool runs it as ``sudarshan-family`` at every run once ``/etc/sudarshan-family`` exists,
and writes ``/etc/sudarshan-family/provisioned``, the file the family unit waits for, only after it
passes. It passes (exit 0) only when, as the account running it:

- it can open none of these: OpenAlgo's ``.env``; any file in its ``db/``, ``log/`` and
  ``db/sudarshan-vault/``; anything under ``/etc/sudarshan/``, ``/var/lib/sudarshan-alerts`` and
  ``/var/lib/sudarshan-portfolio``; ``/var/lib/sudarshan-feed/subscriptions.json``. A folder it can
  enter but not list fails as well, since its files could still be opened by name;
- it can list ``/etc/sudarshan-family`` and open every file in it, and it runs the app's Python and
  imports ``sudarshan.family`` (it is running, so it does);
- a connection to 127.0.0.1:5555 (OpenAlgo's market-data bus) and to 127.0.0.1:8765 (its WebSocket
  proxy) is refused by the firewall the family set-up installs. That firewall rejects the packet as
  "administratively prohibited", so the connection fails at once with EHOSTUNREACH (EACCES over
  IPv6; EPERM is taken too, as some kernels report a dropped packet that way). "Connection refused"
  there means no rule stopped it, and fails; so do an answer and no answer at all. (A rule that only
  drops the packet is not enough to prove: the kernel then retries the connection until it times
  out, which cannot be told from a port too busy to answer.);
- a connection to 127.0.0.1:5583 (the portfolio service, which the family view reads through) is not
  refused by the firewall: refused by the port itself (nothing listening there) or accepted both pass.

Output: one line per check, ``PASS <what>: <detail>`` or ``FAIL <what>: <detail>``. Nothing secret is
printed or read: a file is opened and closed at once, never read, and only paths are named.

The paths are arguments, or environment variables, with the server's paths as defaults, so a test
runs it against a folder of its own; the connection is made by a function a test can replace.
"""

from __future__ import annotations

import argparse
import errno
import os
import pwd
import socket
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import sudarshan.family  # noqa: F401  the check proves the import works: it runs from this package

REPO_ROOT = Path(__file__).resolve().parents[2]
RING_ETC = "/etc/sudarshan"
FAMILY_ETC = "/etc/sudarshan-family"
RING_VAR = "/var/lib"
LOOPBACK = "127.0.0.1"
#: Ports the family account must not reach, and the one it must.
BLOCKED = ((5555, "OpenAlgo's market-data bus"), (8765, "OpenAlgo's WebSocket proxy"))
OPEN = (5583, "the portfolio service")
CONNECT_TIMEOUT_S = 3.0
#: What a firewall's refusal looks like to the connecting program (see the module's docstring).
FIREWALL_ERRNOS = frozenset({errno.EHOSTUNREACH, errno.EACCES, errno.EPERM})
#: A walk that meets more entries than this stops and fails, rather than run on.
MAX_ENTRIES = 20_000
#: A finding names at most this many paths.
NAMED = 3

Connect = Callable[[str, int, float], int]


def tcp_connect(host: str, port: int, timeout: float) -> int:
    """0 when a TCP connection to ``host:port`` opens (it is closed at once), else the errno it
    failed with (ETIMEDOUT when nothing answered in ``timeout`` seconds)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
        except TimeoutError:
            return errno.ETIMEDOUT
        except OSError as exc:
            return exc.errno or errno.EIO
        return 0
    finally:
        sock.close()


def can_open(path: str) -> bool:
    """True when this account can open ``path`` for reading. Nothing is read; a pipe does not block."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
    except OSError:
        return False
    os.close(fd)
    return True


@dataclass
class Walk:
    """What one walk found this account can reach, and how many entries it may still meet."""

    opened: list[str]
    hidden: list[str]  # folders it can enter but not list: their files could be opened by name
    budget: int = MAX_ENTRIES

    @property
    def exhausted(self) -> bool:
        return self.budget < 0


def walk(path: str, found: Walk) -> None:
    """Every file under ``path`` (a file or a folder) this account can open goes in ``found``.

    Links are followed, as anyone holding the account would follow them; a folder met twice (a
    loop of links) is walked once. Each entry met counts against ``found.budget``. A list of what is
    left to look at, not recursion, so a deep tree cannot exhaust the stack.
    """
    seen: set[tuple[int, int]] = set()
    todo = [path]
    while todo and not found.exhausted:
        here = todo.pop()
        found.budget -= 1
        try:
            info = os.stat(here)
        except OSError:
            continue  # not there, or not reachable from here: nothing under it opens by this path
        if not stat.S_ISDIR(info.st_mode):
            if can_open(here):
                found.opened.append(here)
            continue
        if (info.st_dev, info.st_ino) in seen:
            continue
        seen.add((info.st_dev, info.st_ino))
        try:
            with os.scandir(here) as scan:
                names = sorted(entry.name for entry in scan)
        except PermissionError:
            if os.access(here, os.X_OK):
                found.hidden.append(here)
            continue
        except OSError:
            continue
        todo.extend(os.path.join(here, name) for name in reversed(names))


def _named(paths: Sequence[str]) -> str:
    shown = ", ".join(paths[:NAMED])
    return shown if len(paths) <= NAMED else f"{shown} and {len(paths) - NAMED} more"


@dataclass(frozen=True)
class Places:
    """Where the server keeps what the account must not reach, and its own folder."""

    app_dir: Path
    ring_etc: Path
    family_etc: Path
    ring_var: Path

    def closed(self) -> tuple[tuple[str, Path], ...]:
        """Each place the account must not read, with the words a line names it by."""
        return (
            ("OpenAlgo's .env", self.app_dir / ".env"),
            ("OpenAlgo's db folder", self.app_dir / "db"),
            ("OpenAlgo's log folder", self.app_dir / "log"),
            ("the owner vault", self.app_dir / "db" / "sudarshan-vault"),
            ("the ring's settings and keys", self.ring_etc),
            ("the alerts' state", self.ring_var / "sudarshan-alerts"),
            ("the portfolio's state", self.ring_var / "sudarshan-portfolio"),
            ("the feed's claims", self.ring_var / "sudarshan-feed" / "subscriptions.json"),
        )


def closed_lines(places: Places) -> list[tuple[bool, str]]:
    """One (passed, line) for each place the account must not read."""
    result = []
    for what, path in places.closed():
        found = Walk([], [])
        walk(str(path), found)
        if found.exhausted:
            result.append((False, f"FAIL {what}: more than {MAX_ENTRIES} entries under {path}"))
        elif found.opened:
            result.append((False, f"FAIL {what}: this account can open {_named(found.opened)}"))
        elif found.hidden:
            result.append(
                (False, f"FAIL {what}: this account can enter {_named(found.hidden)} without "
                        "listing it, so the files in it could be opened by name")
            )  # fmt: skip
        else:
            result.append((True, f"PASS {what}: closed to this account ({path})"))
    return result


def family_line(folder: Path) -> tuple[bool, str]:
    """The account can list its own folder and open every file in it."""
    what = "the family folder"
    try:
        with os.scandir(folder) as scan:
            entries = sorted(scan, key=lambda e: e.name)
    except FileNotFoundError:
        return False, f"FAIL {what}: {folder} is not there; run the family set-up"
    except OSError:
        return False, f"FAIL {what}: this account cannot list {folder}"
    closed = []
    files = 0
    for entry in entries:
        try:
            regular = entry.is_file()  # a link to a file counts as the file
        except OSError:
            regular = False
        if not regular:
            continue
        files += 1
        if not can_open(entry.path):
            closed.append(entry.path)
    if closed:
        return False, f"FAIL {what}: this account cannot open {_named(closed)}"
    return True, f"PASS {what}: this account opens all {files} files in {folder}"


def python_line() -> tuple[bool, str]:
    try:
        account = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        account = f"uid {os.getuid()}"
    return True, f"PASS the app's Python: it runs as {account} and imports sudarshan.family"


def _failed_with(code: int) -> str:
    return errno.errorcode.get(code, str(code))


def blocked_line(port: int, what: str, connect: Connect) -> tuple[bool, str]:
    """A port the account must not reach: refused by the firewall, at once."""
    where = f"{what} ({LOOPBACK}:{port})"
    code = connect(LOOPBACK, port, CONNECT_TIMEOUT_S)
    if code in FIREWALL_ERRNOS:
        return True, f"PASS {where}: refused by the firewall"
    if code == 0:
        return False, f"FAIL {where}: this account connected to it; no firewall rule stops it"
    if code == errno.ECONNREFUSED:
        return False, f"FAIL {where}: the port itself refused it, so no firewall rule stops it"
    if code == errno.ETIMEDOUT:
        return (
            False,
            f"FAIL {where}: no answer in {CONNECT_TIMEOUT_S:.0f} s, so it is not proven blocked",
        )
    return (
        False,
        f"FAIL {where}: the connection failed with {_failed_with(code)}, not the firewall's refusal",
    )


def open_line(port: int, what: str, connect: Connect) -> tuple[bool, str]:
    """A port the account must reach: not refused by the firewall."""
    where = f"{what} ({LOOPBACK}:{port})"
    code = connect(LOOPBACK, port, CONNECT_TIMEOUT_S)
    if code == 0:
        return True, f"PASS {where}: not blocked (it answered)"
    if code == errno.ECONNREFUSED:
        return True, f"PASS {where}: not blocked (nothing is listening there now)"
    if code in FIREWALL_ERRNOS:
        return False, f"FAIL {where}: refused by the firewall, and the family view reads through it"
    if code == errno.ETIMEDOUT:
        return False, f"FAIL {where}: no answer in {CONNECT_TIMEOUT_S:.0f} s"
    return False, f"FAIL {where}: the connection failed with {_failed_with(code)}"


def run(places: Places, connect: Connect = tcp_connect) -> list[tuple[bool, str]]:
    """Every check, in the order the lines are printed."""
    results = closed_lines(places)
    results.append(family_line(places.family_etc))
    results.append(python_line())
    results.extend(blocked_line(port, what, connect) for port, what in BLOCKED)
    results.append(open_line(*OPEN, connect))
    return results


def places_from(argv: Sequence[str] | None, env: Mapping[str, str]) -> Places:
    parser = argparse.ArgumentParser(
        prog="python -m sudarshan.family.check",
        description="What the family account can and cannot reach. Exit 0 only when all pass.",
    )
    parser.add_argument("--app-dir", default=env.get("SUDARSHAN_OPENALGO_DIR") or str(REPO_ROOT))
    parser.add_argument("--ring-etc", default=env.get("SUDARSHAN_ETC_DIR") or RING_ETC)
    parser.add_argument("--family-etc", default=env.get("SUDARSHAN_FAMILY_ETC_DIR") or FAMILY_ETC)
    parser.add_argument("--ring-var", default=env.get("SUDARSHAN_RING_VAR") or RING_VAR)
    args = parser.parse_args(argv)
    return Places(
        Path(args.app_dir), Path(args.ring_etc), Path(args.family_etc), Path(args.ring_var)
    )


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    connect: Connect = tcp_connect,
    out: TextIO | None = None,
) -> int:
    places = places_from(argv, os.environ if env is None else env)
    out = out if out is not None else sys.stdout
    results = run(places, connect)
    for _passed, line in results:
        print(line, file=out)
    out.flush()
    return 0 if all(passed for passed, _line in results) else 1


if __name__ == "__main__":
    sys.exit(main())
