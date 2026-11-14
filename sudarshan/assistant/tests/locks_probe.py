"""What the real app registers under each setting of the assistant's locks (M17.9, P8 10.2 item 5).

test_assistant_locks.py runs this in an interpreter of its own, in an empty temporary folder, with
the environment the G5 walk uses:

    python -m sudarshan.assistant.tests.locks_probe <report.json>

It builds OpenAlgo's app from app.py's own ``create_app`` (the G5 probe's loader and stand-ins:
no thread, no connection, no process) once per case below, and writes what ``POST /mcp`` and
``GET /auth/csrf-token`` (a route every start has) answered and whether the MCP rules exist. It judges nothing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sudarshan.family.tests import g5_probe

#: name -> the lock settings of that start (None: the key is absent from the environment).
CASES = {
    "upstream_defaults": {"OPENALGO_MCP_READ_ONLY": None, "MCP_OAUTH_WRITE_SCOPE_ENABLED": None},
    "tools_not_read_only": {
        "OPENALGO_MCP_READ_ONLY": "0",
        "MCP_OAUTH_WRITE_SCOPE_ENABLED": "False",
    },
    "write_scope_on": {"OPENALGO_MCP_READ_ONLY": "1", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "True"},
    "locked": {"OPENALGO_MCP_READ_ONLY": "1", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "False"},
}


def _ask(client, token: str) -> int:
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {token}"},
        base_url=g5_probe.BASE_URL,
    ).status_code


def cutoff_on_the_real_transport(app) -> dict:
    """M17.9 (P8 10.2 item 6): a token that worked a second ago is refused once access is ended,
    an open stream closes, and a token issued after it works."""
    import time

    from sudarshan.assistant import cutoff
    from utils.oauth_tokens import issue_access_token

    if not os.environ.get("SUDARSHAN_RISK_DB", "").startswith(f"{Path.cwd().resolve()}/"):
        raise SystemExit("SUDARSHAN_RISK_DB does not point into this folder: refusing to run")
    seen: dict = {}
    with app.app_context():
        token, _ttl, _jti = issue_access_token(
            user_id=1, client_id="probe", scope="read:market read:account"
        )
    client = app.test_client()
    seen["before"] = _ask(client, token)
    stream = client.get(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        base_url=g5_probe.BASE_URL,
        buffered=False,
    )
    chunks = iter(stream.response)
    seen["stream_opened"] = stream.status_code == 200 and b"connected" in next(chunks)
    done = cutoff.end_all_access("probe", revoke=False)
    seen["cutoff_recorded"] = done["cutoff"]
    started = time.monotonic()
    seen["stream_rest"] = b"".join(chunks).decode()
    seen["stream_closed_in_s"] = round(time.monotonic() - started, 2)
    stream.close()
    refused = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {token}"},
        base_url=g5_probe.BASE_URL,
    )
    seen["after"] = refused.status_code
    seen["after_challenge"] = refused.headers.get("WWW-Authenticate", "")
    seen["stream_after"] = client.get(
        "/mcp", headers={"Authorization": f"Bearer {token}"}, base_url=g5_probe.BASE_URL
    ).status_code
    time.sleep(1.1)  # tokens carry whole seconds: one from the next second is a new sign-in's
    with app.app_context():
        later, _ttl, _jti = issue_access_token(
            user_id=1, client_id="probe", scope="read:market read:account"
        )
    seen["new_token"] = _ask(client, later)
    return seen


def main(argv: list[str]) -> int:
    report_path = Path(argv[1])
    g5_probe.refuse_unless_isolated(Path.cwd().resolve())
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.main.load_dotenv = dotenv.load_dotenv
    g5_probe.guard_the_machine()
    module = g5_probe.load_app_module()
    g5_probe.stub_start_up(module)
    report = {}
    for name, settings in CASES.items():  # the locked case last: it imports the MCP server
        for key, value in settings.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        app = g5_probe.build_app(module, react=False)
        if not report:
            g5_probe.create_tables(module, app)
        app.db_ready.set()
        client = app.test_client()
        call = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            base_url=g5_probe.BASE_URL,
        )
        report[name] = {
            "mcp_rules": sorted(
                rule.rule
                for rule in app.url_map.iter_rules()
                if rule.rule == "/mcp" or rule.rule.startswith(("/oauth/", "/.well-known/oauth"))
            ),
            "mcp_status": call.status_code,
            "mcp_challenge": call.headers.get("WWW-Authenticate", ""),
            "app_status": client.get("/auth/csrf-token", base_url=g5_probe.BASE_URL).status_code,
        }
        if name == "locked":
            report[name]["cutoff"] = cutoff_on_the_real_transport(app)
            from utils.mcp_tool_registry import _load_mcpserver_module

            mcpserver = _load_mcpserver_module()
            report[name]["tools_registered"] = sorted(mcpserver.ACTIVE_TOOL_NAMES)
            report[name]["write_tools_registered"] = sorted(
                meta.name
                for meta in mcpserver.TOOL_META.values()
                if meta.registered and not meta.read_only
            )
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
