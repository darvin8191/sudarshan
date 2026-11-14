"""Are the AI assistant's nginx lines in effect? A read-only check (M19.3).

    python -m sudarshan.ops.nginx_check --host <the app's address, as typed in the browser>

It asks nginx on this machine (127.0.0.1:443) for a handful of addresses, with the app's own
address as the Host, and reads only the status of each answer. It opens no nginx file, runs no
command, changes nothing, and sends no credential. A call from this machine is not from
Anthropic's range, so with ``sudarshan-mcp.conf`` installed the assistant's addresses answer
"refused" (403) here, while the consent page and the app itself are let through.

Exit 0 when every line is as expected, 1 when one is not, 2 when nginx did not answer at all.
``--host`` is required and has no default: nginx may serve other sites on this server, and this
check must only ever ask for SUDARSHAN's.
"""

from __future__ import annotations

import argparse
import http.client
import re
import ssl
import sys
from collections.abc import Callable

ADDRESS = ("127.0.0.1", 443)
TIMEOUT_S = 5.0
REFUSED = 403
#: path -> whether nginx must refuse a call from this machine.
EXPECTED = (
    ("/", False, "the app itself"),
    ("/mcp", True, "the assistant's calls"),
    ("/oauth/register", True, "registering an AI app"),
    ("/oauth/token", True, "an AI app's passes"),
    ("/.well-known/oauth-authorization-server", True, "how an AI app signs in"),
    ("/.well-known/oauth-protected-resource", True, "how an AI app signs in"),
    ("/oauth/authorize", False, "the consent page, for your own browser"),
    ("/mcp/healthz", False, "the health line, for this machine"),
)
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?")


def status_of(path: str, host: str, address=ADDRESS, timeout: float = TIMEOUT_S) -> int | None:
    """The status nginx answers for ``GET path`` with ``host``; None when it did not answer."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False  # asked by address on this machine; only the status is read
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(*address, timeout=timeout, context=context)
    try:
        connection.request("GET", path, headers={"Host": host, "User-Agent": "sudarshan-check"})
        return connection.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def judge(statuses: dict[str, int | None]) -> tuple[int, list[str]]:
    """The lines to print and the exit code, from each path's status."""
    if statuses.get("/") is None:
        return 2, ["nginx did not answer on this server for that address. Nothing was checked."]
    lines, wrong = [], 0
    for path, must_refuse, what in EXPECTED:
        status = statuses.get(path)
        if status is None:
            ok, said = False, "no answer"
        elif must_refuse:
            ok = status == REFUSED
            said = (
                "refused here, as it should be"
                if ok
                else "NOT refused: the lines are not in effect"
            )
        else:
            ok = status != REFUSED
            said = "let through" if ok else "refused, but it should be let through"
        wrong += not ok
        lines.append(f"{'ok ' if ok else 'NO '} {path:<42} {said} ({what})")
    lines.append(
        "The assistant's nginx lines are in effect."
        if not wrong
        else "The assistant's nginx lines are not all in effect. See the README: "
        '"nginx lines for the AI assistant".'
    )
    return (1 if wrong else 0), lines


def main(argv: list[str] | None = None, ask: Callable[[str, str], int | None] = status_of) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.ops.nginx_check")
    parser.add_argument("--host", required=True, help="the app's address, as typed in the browser")
    args = parser.parse_args(argv)
    host = args.host.removeprefix("https://").strip("/")
    if not _HOST.fullmatch(host):
        sys.stdout.write("That is not an address: give the app's address, without https://.\n")
        return 2
    statuses: dict[str, int | None] = {"/": ask("/", host)}
    if statuses["/"] is not None:
        statuses.update({path: ask(path, host) for path, _refuse, _what in EXPECTED[1:]})
    code, lines = judge(statuses)
    sys.stdout.write("\n".join(lines) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
