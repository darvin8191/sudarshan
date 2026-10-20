"""The auto-login stores a token the way a manual Upstox login does, and keeps doing so.

``_apply_token`` repeats the persistence half of ``utils.auth_utils.handle_auth_success`` rather
than calling it, because that function also writes a browser session cookie, which an unattended
login must not create. Two tests keep the copy honest:

* behaviour: both run against the same recorders and must make the same calls, in the same order,
  with the same token arguments (the only differences are the documented device label and type);
* upstream drift: the set of calls inside ``handle_auth_success`` is pinned. When OpenAlgo adds a
  step there, this fails and says what to re-check, instead of the auto-login silently skipping it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from autologin_app import OWNER, FakeSocketIO, Recorder
from autologin_fakes import FAKE_TOKEN
from flask import Flask

import blueprints.sudarshan_autologin as bp_module
import database.auth_db as auth_db
import extensions
import utils.auth_utils as auth_utils

REPO = Path(__file__).resolve().parents[3]

#: Everything handle_auth_success calls, as of OpenAlgo 2.0.2.8. Browser-only: get_real_ip,
#: get_session_expiry_time, is_ajax_request, jsonify, redirect, request.headers.get,
#: set_session_login_time, url_for. Persistence (mirrored by _apply_token): the rest.
HANDLE_AUTH_SUCCESS_CALLS = {
    "get_active_sessions",
    "get_real_ip",
    "get_session_expiry_time",
    "is_ajax_request",
    "jsonify",
    "len",
    "log_login_attempt",
    "logger.error",
    "logger.info",
    "redirect",
    "register_session",
    "request.headers.get",
    "secrets.token_hex",
    "set_session_login_time",
    "should_download_master_contract",
    "socketio.emit",
    "try_start_cached_master_contract_load",
    "try_start_master_contract_download",
    "upsert_auth",
    "url_for",
}


def _calls_in(path: Path, function: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    names = set()
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        func, parts = call.func, []
        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if isinstance(func, ast.Name):
            parts.append(func.id)
            names.add(".".join(reversed(parts)))
    return names


def test_handle_auth_success_has_not_changed_under_us():
    found = _calls_in(REPO / "utils" / "auth_utils.py", "handle_auth_success")
    assert found == HANDLE_AUTH_SUCCESS_CALLS, (
        "utils.auth_utils.handle_auth_success changed upstream. Compare it with "
        "blueprints/sudarshan_autologin._apply_token, mirror any new persistence step there, "
        f"then update HANDLE_AUTH_SUCCESS_CALLS. Added: {sorted(found - HANDLE_AUTH_SUCCESS_CALLS)}, "
        f"removed: {sorted(HANDLE_AUTH_SUCCESS_CALLS - found)}"
    )


def _recorders(monkeypatch, module, log, *, should_download):
    """Point every persistence call of ``module``'s login path at shared recorders."""
    targets = {
        "upsert_auth": Recorder("upsert_auth", log, 1),
        "should_download_master_contract": Recorder(
            "should_download_master_contract", log, (should_download, "reason")
        ),
        "try_start_master_contract_download": Recorder(
            "try_start_master_contract_download", log, True
        ),
        "try_start_cached_master_contract_load": Recorder(
            "try_start_cached_master_contract_load", log, True
        ),
    }
    for name, recorder in targets.items():
        monkeypatch.setattr(module, name, recorder)
    sessions = Recorder("get_active_sessions", log, [{"session_id": "s"}])
    register = Recorder("register_session", log, True)
    attempt = Recorder("log_login_attempt", log)
    socket = FakeSocketIO(log)
    if module is auth_utils:  # handle_auth_success imports these at call time
        monkeypatch.setattr(auth_db, "register_session", register)
        monkeypatch.setattr(auth_db, "get_active_sessions", sessions)
        monkeypatch.setattr(auth_db, "log_login_attempt", attempt)
        monkeypatch.setattr(extensions, "socketio", socket)
    else:
        monkeypatch.setattr(module, "register_session", register)
        monkeypatch.setattr(module, "get_active_sessions", sessions)
        monkeypatch.setattr(module, "log_login_attempt", attempt)
        monkeypatch.setattr(module, "socketio", socket)
        monkeypatch.setattr(module, "remove_session", Recorder("remove_session", log))


def _manual_upstox_login(monkeypatch, *, should_download):
    """What brlogin's generic OAuth branch does for Upstox after a successful code exchange."""
    log: list = []
    _recorders(monkeypatch, auth_utils, log, should_download=should_download)
    app = Flask("parity")
    app.secret_key = "test-only"  # pragma: allowlist secret
    with app.test_request_context(
        "/upstox/callback", headers={"Accept": "application/json", "User-Agent": "phone"}
    ):
        auth_utils.handle_auth_success(FAKE_TOKEN, OWNER, "upstox", feed_token=None)
    return log


def _auto_login(monkeypatch, vault, *, should_download):
    log: list = []
    _recorders(monkeypatch, bp_module, log, should_download=should_download)
    monkeypatch.setattr(bp_module, "_vault", lambda: vault)
    bp_module._apply_token(OWNER, FAKE_TOKEN)
    return [entry for entry in log if entry[0] != "remove_session"]


@pytest.mark.parametrize("should_download", [True, False])
def test_same_calls_same_order_same_token_arguments(monkeypatch, vault, should_download):
    manual = _manual_upstox_login(monkeypatch, should_download=should_download)
    monkeypatch.undo()
    auto = _auto_login(monkeypatch, vault, should_download=should_download)

    assert [name for name, _, _ in auto] == [name for name, _, _ in manual]
    by_name = {name: (args, kwargs) for name, args, kwargs in manual}
    for name, args, kwargs in auto:
        if name == "upsert_auth":
            assert (
                (args, kwargs)
                == by_name[name]
                == (
                    (OWNER, FAKE_TOKEN, "upstox"),
                    {"feed_token": None, "user_id": None},
                )
            )
        elif name in (
            "should_download_master_contract",
            "try_start_master_contract_download",
            "try_start_cached_master_contract_load",
            "get_active_sessions",
            "socketio.emit",
        ):
            assert (args, kwargs) == by_name[name]
        elif name == "register_session":
            manual_kwargs = by_name[name][1]
            assert kwargs["username"] == manual_kwargs["username"] == OWNER
            assert kwargs["broker"] == manual_kwargs["broker"] == "upstox"
            assert kwargs["device_info"] == bp_module.AUTOLOGIN_DEVICE
            assert len(kwargs["session_id"]) == len(manual_kwargs["session_id"]) == 64
        elif name == "log_login_attempt":
            manual_kwargs = by_name[name][1]
            for key in ("username", "status", "broker"):
                assert kwargs[key] == manual_kwargs[key]
            assert (manual_kwargs["login_type"], kwargs["login_type"]) == ("oauth", "auto_totp")


def test_the_upstox_oauth_callback_passes_no_feed_token_or_user_id():
    """brlogin's generic branch, which Upstox takes, hands handle_auth_success no user id."""
    source = (REPO / "blueprints" / "brlogin.py").read_text(encoding="utf-8")
    generic = source.split('    else:\n        code = request.args.get("code")', 1)[1]
    assert "auth_token, error_message = auth_function(code)" in generic
    special = 'if broker in ["angel", "compositedge", "pocketful", "definedge", "dhan", "motilal", "rmoney", "iiflcapital"]'
    assert special in source and '"upstox"' not in special
    assert (
        'return handle_auth_success(auth_token, session["user"], broker, feed_token=feed_token)'
        in source
    )
