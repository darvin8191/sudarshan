"""SUDARSHAN (M2.20): Upstox auto-login - the owner's settings API and the worker's hand-off.

This blueprint is a SUDARSHAN addition to OpenAlgo; the worker itself lives in
``sudarshan/autologin/`` (see its README). OpenAlgo's own login flow is not changed.

Owner routes (OpenAlgo session + CSRF, like the other owner-only JSON endpoints):
    GET    /sudarshan/autologin/status
    POST   /sudarshan/autologin/totp-secret   {"secret": "..."}    DELETE clears it
    POST   /sudarshan/autologin/pin           {"pin": "123456"}    DELETE clears it
    POST   /sudarshan/autologin/enabled       {"enabled": true}
    POST   /sudarshan/autologin/test          one attempt now (child process, waited for)

Worker routes (no session and no CSRF cookie: each body is a sealed Fernet token only a process
that can read OpenAlgo's .env can make, see ``sudarshan/autologin/handoff.py``). They answer 404
to anything that came through nginx, and have no rate limit (see ``_open_sealed_request``):
    POST   /sudarshan/autologin/internal/preflight   owner, broker, and does the login still work?
    POST   /sudarshan/autologin/internal/report      apply a new Upstox token and/or tell the owner

Secrets are write-only here: they can be set, cleared and reported as set, never read back.

Why the token is applied here and not by the worker: this process keeps the broker token in
memory (``auth_cache``, which even caches "no session"), loads the symbol cache after the master
contract download and runs the order-update adapter. ``upsert_auth`` and the master contract hook
refresh those only in the process that calls them, so the worker hands the token to this process
and ``_apply_token`` makes the same calls a manual Upstox login makes in ``handle_auth_success``.
"""

import os
import re
import secrets
import uuid
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, session

from database.auth_db import (
    Auth,
    decrypt_token,
    get_active_sessions,
    log_login_attempt,
    register_session,
    remove_session,
    upsert_auth,
)
from database.auth_db import db_session as auth_db_session
from database.user_db import find_user_by_username
from extensions import socketio
from limiter import limiter
from sudarshan.autologin import messages
from sudarshan.autologin.config import is_loopback_url
from sudarshan.autologin.handoff import (
    MAX_SEALED_BYTES,
    SEAL_TTL_S,
    HandoffRejected,
    open_sealed,
)
from sudarshan.autologin.launcher import run_test_child
from sudarshan.autologin.schedule import (
    EXTRA_TRADING_DAYS_FILE,
    HOLIDAYS_FILE,
    IST,
    SCHEDULE,
    UPSTOX_TOKEN_EXPIRY,
    TradingCalendar,
    trading_session_start,
)
from sudarshan.autologin.totp import InvalidSecret, normalize_secret
from sudarshan.autologin.upstox import UPSTOX_API_BASE, check_token
from sudarshan.autologin.worker import (
    EXIT_BUSY,
    SECRET_PIN,
    SECRET_TOTP,
    rate_refusal,
)
from sudarshan.vault import HANDOFF_INFO, Vault, fernet_for, move_legacy_vault, pepper_from
from utils.auth_utils import (
    should_download_master_contract,
    try_start_cached_master_contract_load,
    try_start_master_contract_download,
)
from utils.httpx_client import get_httpx_client
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import check_session_validity
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_autologin_bp = Blueprint(
    "sudarshan_autologin", __name__, url_prefix="/sudarshan/autologin"
)

BROKER = "upstox"
#: How the auto-login appears in Security -> Active sessions and Login activity.
AUTOLOGIN_DEVICE = "SUDARSHAN auto-login (Upstox TOTP)"
LOGIN_TYPE = "auto_totp"
MAX_TOKEN_LEN = 8192
MAX_TEXT_LEN = 700
_PIN = re.compile(r"[0-9]{6}")  # not \d: that also matches non-ASCII digits

TOTP_INVALID = (
    "The TOTP key should be 16 to 64 characters long, letters A to Z and digits 2 to 7, "
    "exactly as the Upstox app shows it."
)
PIN_INVALID = "The PIN must be exactly 6 digits."
OWNER_ONLY = "Only the account owner can change auto-login."

#: Set by nginx on every proxied location (the first three, at least): a request carrying any
#: of them did not come from this server's worker, which calls 127.0.0.1:5000 directly.
PROXY_HEADERS = (
    "X-Forwarded-For",
    "X-Real-IP",
    "Forwarded",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
)

# Nonces of accepted hand-off requests, kept past their expiry so none is accepted twice.
_seen_nonces = LockedTTLCache(maxsize=256, ttl=2 * SEAL_TTL_S)
# Which sealed routes have warned about a rejected request in the last 10 minutes.
_rejections_warned = LockedTTLCache(maxsize=4, ttl=600)
# Built once on first use. Each factory only constructs (derives a key, stores a path); the vault
# opens its file later, on first use, outside LazyInit's lock.
_vault_lazy = LazyInit(lambda: Vault(pepper=pepper_from(os.environ)), "sudarshan-autologin-vault")
_handoff_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), HANDOFF_INFO), "sudarshan-autologin-handoff"
)
_calendar_lazy = LazyInit(
    lambda: TradingCalendar(
        os.environ.get("SUDARSHAN_NSE_HOLIDAYS_FILE") or HOLIDAYS_FILE,
        os.environ.get("SUDARSHAN_NSE_EXTRA_DAYS_FILE") or EXTRA_TRADING_DAYS_FILE,
    ),
    "sudarshan-autologin-calendar",
)


def _vault() -> Vault:
    return _vault_lazy.get()


def _handoff():
    return _handoff_lazy.get()


def _calendar() -> TradingCalendar:
    return _calendar_lazy.get()


def _now() -> datetime:
    return datetime.now(IST)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(IST).isoformat(timespec="seconds") if value else None


def _configured_broker() -> str:
    """The broker this OpenAlgo logs in to, read the way the login page reads it."""
    match = re.search(r"/([^/]+)/callback$", os.getenv("REDIRECT_URL", "") or "")
    return match.group(1).lower() if match else ""


def _logged_in_since_token_expiry(username: str, now: datetime) -> bool:
    """A login on record since the last 03:30 IST, when Upstox's daily tokens stop working.

    Not OpenAlgo's own ``has_login_this_trading_session``: its boundary is 03:00, so a login at
    03:10 would count as today's although its token died at 03:30.
    """
    since = trading_session_start(now, rollover=UPSTOX_TOKEN_EXPIRY)
    for item in get_active_sessions(username):
        try:
            at = datetime.fromisoformat(item.get("login_time") or "")
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=IST)  # stored as IST wall-clock time, zone stripped
        if at >= since:
            return True
    return False


def _holds_fresh_token(username: str, now: datetime | None = None) -> bool:
    """OpenAlgo holds an Upstox login made since the last 03:30 IST.

    ``is_revoked`` alone is not enough: the row is only revoked by the auto-expiry sweep, which
    needs a browser request. A login since the token expiry is what proves the token is today's.
    """
    auth = Auth.query.filter_by(name=username).first()
    if auth is None or auth.is_revoked or auth.broker != BROKER:
        return False
    return _logged_in_since_token_expiry(username, now or _now())


def _upstox_base() -> str | None:
    base = (os.environ.get("SUDARSHAN_UPSTOX_API_BASE") or UPSTOX_API_BASE).rstrip("/")
    if base.startswith("https://") or is_loopback_url(base):
        return base
    logger.error("Auto-login: SUDARSHAN_UPSTOX_API_BASE must use https://; token not checked")
    return None


def _check_stored_token(username: str) -> str:
    """Whether Upstox still accepts the token OpenAlgo holds: works, dead, unknown or none.

    One read-only call (Upstox's funds endpoint, which OpenAlgo's plugin already uses). The
    worker uses the answer so that no TOTP login ever ends a token that still works.
    """
    auth = Auth.query.filter_by(name=username).first()
    if auth is None or auth.is_revoked or auth.broker != BROKER or not auth.auth:
        return "none"
    try:
        token = decrypt_token(auth.auth)
    except Exception:
        return "none"  # unreadable here means unusable by OpenAlgo too
    if not token:
        return "none"
    base = _upstox_base()
    if base is None:
        return "unknown"
    return check_token(get_httpx_client(), base, token)


def _clean_text(value) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ch == "\n" or ch >= " ")
    return text.strip()[:MAX_TEXT_LEN]


def _owner_only(view):
    """The logged-in session must be OpenAlgo's owner (admin) account."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return jsonify({"status": "error", "message": OWNER_ONLY}), 403
        return view(*args, **kwargs)

    return wrapper


@sudarshan_autologin_bp.errorhandler(429)
def _rate_limited(error):
    return jsonify(
        {"status": "error", "message": "Too many requests. Wait a minute, then try again."}
    ), 429


# -- owner routes -----------------------------------------------------------------------------


def _test_refusal(vault: Vault, state, now: datetime, fresh: bool) -> str | None:
    """Why "Test now" cannot run right now (the worker checks all of this again)."""
    if vault.lease_active(now):
        return "busy"
    totp_info, pin_info = vault.secret_info(SECRET_TOTP), vault.secret_info(SECRET_PIN)
    if not (totp_info.is_set and pin_info.is_set):
        return "secrets_missing"
    if not (totp_info.readable and pin_info.readable):
        return "secrets_unreadable"
    if state.paused_code:
        return "test_paused"
    if fresh and _calendar().blocks_test_now(now):
        return "market_hours"
    return rate_refusal(vault, state, now)


def _secret_view(info) -> dict:
    return {"set": info.is_set, "updated_at": _iso(info.updated_at), "readable": info.readable}


@sudarshan_autologin_bp.route("/status", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("60 per minute")
def status():
    vault, now, owner = _vault(), _now(), find_user_by_username()
    state = vault.state()
    fresh = _holds_fresh_token(owner.username)
    calendar = _calendar()
    next_run = None
    if state.enabled and not state.paused_code:
        next_run = calendar.next_slot(now, skip_today=fresh)
    refusal = _test_refusal(vault, state, now, fresh)
    last_run = None
    if state.last_run_at:
        last_run = {
            "at": _iso(state.last_run_at),
            "kind": state.last_run_kind,
            "outcome": state.last_outcome,
            "message": state.last_message,
        }
    recent = [
        {
            "at": _iso(item.at),
            "kind": item.kind,
            "outcome": item.outcome,
            "message": messages.message_for(item.code)
            if item.code != "started"
            else "This attempt did not finish.",
        }
        for item in vault.recent_attempts(5)
    ]
    broker = _configured_broker()
    return jsonify(
        {
            "status": "success",
            "data": {
                "enabled": state.enabled,
                "broker_supported": broker == BROKER,
                "connected_today": fresh,
                "secrets": {
                    "totp_secret": _secret_view(vault.secret_info(SECRET_TOTP)),
                    "pin": _secret_view(vault.secret_info(SECRET_PIN)),
                },
                # The cause only: the page says that auto-login is paused and how to resume.
                "paused": {"message": messages.message_for(state.paused_code)}
                if state.paused_code
                else None,
                "last_run": last_run,
                "last_success_at": _iso(state.last_success_at),
                "running": vault.lease_active(now),
                "next_run": _iso(next_run),
                "schedule": list(SCHEDULE),
                "test": {
                    "allowed": refusal is None,
                    "reason": messages.message_for(refusal) if refusal else None,
                },
                "recent": recent,
            },
        }
    )


def _resume_after_new_secret(vault: Vault) -> None:
    """A newly saved TOTP key or PIN is the owner's answer to a pause; the next refusal pauses
    again. Switching auto-login off and on is not an answer: it would send the same pair again."""
    if vault.state().paused_code:
        vault.update_status(paused_code=None)
        logger.info("Auto-login: pause ended by a newly saved secret")


@sudarshan_autologin_bp.route("/totp-secret", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("10 per minute")
@limiter.limit("50 per hour")
def set_totp_secret():
    raw = (request.get_json(silent=True) or {}).get("secret")
    try:
        secret = normalize_secret(raw)
    except InvalidSecret:
        return jsonify({"status": "error", "message": TOTP_INVALID}), 400
    vault = _vault()
    vault.set_secret(SECRET_TOTP, secret)
    _resume_after_new_secret(vault)
    logger.info("Auto-login: TOTP key saved")
    return jsonify({"status": "success", "message": "TOTP key saved."})


@sudarshan_autologin_bp.route("/totp-secret", methods=["DELETE"])
@check_session_validity
@_owner_only
@limiter.limit("10 per minute")
def clear_totp_secret():
    _vault().clear_secret(SECRET_TOTP)
    logger.info("Auto-login: TOTP key removed")
    return jsonify({"status": "success", "message": "TOTP key removed."})


@sudarshan_autologin_bp.route("/pin", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("10 per minute")
@limiter.limit("50 per hour")
def set_pin():
    pin = (request.get_json(silent=True) or {}).get("pin")
    if not isinstance(pin, str) or not _PIN.fullmatch(pin):
        return jsonify({"status": "error", "message": PIN_INVALID}), 400
    vault = _vault()
    vault.set_secret(SECRET_PIN, pin)
    _resume_after_new_secret(vault)
    logger.info("Auto-login: PIN saved")
    return jsonify({"status": "success", "message": "PIN saved."})


@sudarshan_autologin_bp.route("/pin", methods=["DELETE"])
@check_session_validity
@_owner_only
@limiter.limit("10 per minute")
def clear_pin():
    _vault().clear_secret(SECRET_PIN)
    logger.info("Auto-login: PIN removed")
    return jsonify({"status": "success", "message": "PIN removed."})


@sudarshan_autologin_bp.route("/enabled", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("20 per minute")
def set_enabled():
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"status": "error", "message": "Send on or off."}), 400
    _vault().set_enabled(enabled)  # a pause stays: see _resume_after_new_secret
    logger.info("Auto-login switched %s", "on" if enabled else "off")
    return jsonify(
        {"status": "success", "message": "Auto-login is on." if enabled else "Auto-login is off."}
    )


@sudarshan_autologin_bp.route("/test", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("2 per minute")
@limiter.limit("10 per hour")
def test_now():
    vault, now, owner = _vault(), _now(), find_user_by_username()
    state = vault.state()
    refusal = _test_refusal(vault, state, now, _holds_fresh_token(owner.username))
    if refusal:
        return jsonify(
            {"status": "error", "code": refusal, "message": messages.message_for(refusal)}
        ), 409
    run_id = uuid.uuid4().hex
    logger.info("Auto-login: Test now started")
    exit_status = run_test_child(run_id)
    state = vault.state()
    if state.last_run_id == run_id:
        result = {
            "outcome": state.last_outcome,
            "message": state.last_message,
            "at": _iso(state.last_run_at),
        }
        if state.last_outcome == "refused":
            return jsonify({"status": "error", "message": state.last_message, "data": result}), 409
        return jsonify({"status": "success", "data": result})
    if exit_status == EXIT_BUSY:
        return jsonify({"status": "error", "message": messages.message_for("busy")}), 409
    if exit_status is None:
        logger.error("Auto-login test overran its time limit and was stopped")
        return jsonify({"status": "error", "message": messages.message_for("test_overran")}), 504
    logger.error("Auto-login test ended without a result (exit status %s)", exit_status)
    return jsonify({"status": "error", "message": messages.message_for("crashed")}), 500


# -- worker routes ------------------------------------------------------------------------------


def _note_rejection(op: str, reason: str) -> None:
    """Junk must not flood the log: debug always, one warning per op every 10 minutes."""
    logger.debug("Auto-login: rejected a %s request (%s)", op, reason)
    with _rejections_warned.lock:
        first = _rejections_warned.get(op, MISSING) is MISSING
        if first:
            _rejections_warned[op] = True
    if first:
        logger.warning(
            "Auto-login: rejected a %s request that was not from this server's worker (%s); "
            "more are logged at debug level for 10 minutes",
            op,
            reason,
        )


def _open_sealed_request(op: str):
    """``(message, None)`` for a genuine sealed request, else ``(None, response)``.

    These routes are for the worker on this server, which calls 127.0.0.1:5000 directly. A
    request that came through nginx carries its forwarding headers (nginx sets them on every
    proxied location) and is answered 404 before its body is read. There is no rate limit here:
    behind nginx every client shares one address, so a limit would let anyone use it up and
    block the worker; a forged request costs one HMAC check and is refused.
    """
    if any(header in request.headers for header in PROXY_HEADERS):
        return None, (jsonify({"status": "error", "message": "Not found"}), 404)
    length = request.content_length
    if length is None:
        return None, (jsonify({"status": "error", "message": "Length required"}), 411)
    if length > MAX_SEALED_BYTES:
        return None, (jsonify({"status": "error", "message": "Too large"}), 413)
    try:
        message = open_sealed(_handoff(), request.get_data(cache=False), op=op, seen=_seen_nonces)
    except HandoffRejected as exc:
        _note_rejection(op, str(exc))
        return None, (jsonify({"status": "error", "message": "Forbidden"}), 403)
    return message, None


@sudarshan_autologin_bp.route("/internal/preflight", methods=["POST"])
def internal_preflight():
    _, refused = _open_sealed_request("preflight")
    if refused:
        return refused
    owner, broker = find_user_by_username(), _configured_broker()
    ready = owner is not None and broker == BROKER
    return jsonify(
        {
            "status": "success",
            "user": owner is not None,
            "broker": broker,
            "fresh_session": ready and _holds_fresh_token(owner.username),
            "token": _check_stored_token(owner.username) if ready else "none",
        }
    )


def _apply_token(username: str, auth_token: str) -> None:
    """Persist a new Upstox token exactly as a manual Upstox login does.

    The same calls, in the same order, as ``utils.auth_utils.handle_auth_success`` (Upstox passes
    no feed token and no user id there either), minus what only exists for a browser: the Flask
    session cookie and the cookie lifetime. ``test_autologin_parity.py`` pins both lists, so an
    upstream change to ``handle_auth_success`` fails a test instead of drifting silently.

    The active-session row matters beyond display: the 03:00 auto-expiry of a stale browser cookie
    revokes the shared broker token unless a login after the rollover is on record
    (``utils.session._has_fresher_session``), and OpenAlgo's restart scan starts the order-update
    adapter only for such a login. The previous auto-login row is replaced, so there is one.
    """
    vault = _vault()
    previous = vault.state().active_session_id
    if previous:
        remove_session(previous)
    session_id = secrets.token_hex(32)
    if not register_session(
        username=username,
        session_id=session_id,
        device_info=AUTOLOGIN_DEVICE,
        ip_address=None,
        broker=BROKER,
    ):
        logger.warning("Auto-login: could not record the active session for %s", username)
    vault.update_status(active_session_id=session_id)

    active = get_active_sessions(username)
    socketio.emit("active_sessions_update", {"count": len(active), "sessions": active})

    log_login_attempt(
        username=username,
        ip_address=None,
        device_info=AUTOLOGIN_DEVICE,
        status="success",
        login_type=LOGIN_TYPE,
        broker=BROKER,
    )

    try:
        inserted_id = upsert_auth(username, auth_token, BROKER, feed_token=None, user_id=None)
        if not inserted_id:
            raise RuntimeError("upsert_auth stored nothing")
    except Exception:
        # No login on record without the token behind it: the next wake-up would otherwise take
        # yesterday's stored token for today's and skip the day. A commit that failed (a write
        # lock held past the retry budget, a full disk) leaves the session unusable until it is
        # rolled back, and remove_session would fail on it and leave the row in place.
        try:
            auth_db_session.rollback()
        except Exception:
            logger.exception("Auto-login: could not roll back the failed store")
        remove_session(session_id)
        vault.update_status(active_session_id=None)
        raise

    should_download, reason = should_download_master_contract(BROKER)
    logger.info("Auto-login: master contract check for %s: %s", BROKER, reason)
    if should_download:
        try_start_master_contract_download(BROKER, reset_status=True)
    else:
        try_start_cached_master_contract_load(BROKER)


def _notify_owner(username: str, text: str) -> bool:
    """One Telegram message through OpenAlgo's alert service, like ``/api/v1/telegram/notify``.

    It follows the same gate as OpenAlgo's other automatic messages: nothing is sent while the
    Telegram bot is stopped (issue #1577).
    """
    if not text:
        return False
    try:
        from database.telegram_db import get_telegram_user_by_username
        from services.telegram_alert_service import TelegramAlertService, alert_executor

        service = TelegramAlertService()
        if not service.is_bot_active():
            logger.info("Auto-login: Telegram bot is stopped; message not sent")
            return False
        user = get_telegram_user_by_username(username)
        telegram_id = user.get("telegram_id") if user else None
        if not telegram_id:
            logger.info("Auto-login: no Telegram account linked to %s; message not sent", username)
            return False
        alert_executor.submit(service.send_alert_sync, telegram_id, text)
        return True
    except Exception:
        logger.exception("Auto-login: could not queue the Telegram message")
        return False


@sudarshan_autologin_bp.route("/internal/report", methods=["POST"])
def internal_report():
    message, refused = _open_sealed_request("report")
    if refused:
        return refused
    owner = find_user_by_username()
    if owner is None:
        return jsonify(
            {"status": "error", "applied": False, "message": messages.message_for("no_user")}
        ), 409
    outcome = message.get("outcome")
    text = _clean_text(message.get("telegram"))

    if outcome == "failed":
        _notify_owner(owner.username, text)
        return jsonify({"status": "success", "applied": False})
    if outcome != "ok":
        return jsonify({"status": "error", "applied": False, "message": "Unknown report"}), 400

    token = message.get("token")
    if not isinstance(token, str) or not token.strip() or len(token) > MAX_TOKEN_LEN:
        return jsonify({"status": "error", "applied": False, "message": "No token"}), 400
    if _configured_broker() != BROKER:
        return jsonify(
            {"status": "error", "applied": False, "message": messages.message_for("wrong_broker")}
        ), 409
    try:
        _apply_token(owner.username, token.strip())
    except Exception:
        logger.exception("Auto-login: the new Upstox login could not be stored")
        reason = messages.message_for("store_failed")
        _notify_owner(
            owner.username,
            messages.telegram_failed(
                reason, at=_now(), test=message.get("kind") == "test", next_try=None
            ),
        )
        return jsonify({"status": "error", "applied": False, "message": reason}), 500
    logger.info("Auto-login: new Upstox login stored for %s", owner.username)
    _notify_owner(owner.username, text)
    return jsonify({"status": "success", "applied": True})


@sudarshan_autologin_bp.record_once
def _move_a_legacy_vault(state):
    """The first version kept the vault in db/ itself; it now has its own directory, the only
    place the auto-login unit may write. Two file checks when there is nothing to move."""
    try:
        if move_legacy_vault():
            logger.info("Auto-login: moved the vault into db/sudarshan-vault/")
    except Exception:
        logger.exception("Auto-login: could not move the vault into db/sudarshan-vault/")


@sudarshan_autologin_bp.record_once
def _exempt_worker_routes_from_csrf(state):
    """The worker has no browser session to forge; its requests are authenticated by the seal."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(internal_preflight)
        csrf.exempt(internal_report)
