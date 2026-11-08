"""One auto-login attempt: decide, log in to Upstox, hand the token to OpenAlgo, report.

Run by ``sudarshan-autologin.service`` (woken by its timer at 08:35, 08:45 and 08:55 IST on
weekdays) and, for "Test now", by OpenAlgo as a child process (``--test``). Every start decides for
itself whether to act, so a deploy restarting the unit, a catch-up start after a boot, a weekend
or an NSE holiday costs nothing:

  scheduled  trading day, inside the window (08:30-15:40), switched on, not paused, and Upstox
             no longer accepts the login OpenAlgo holds (OpenAlgo checks it with one read-only
             call); during market hours (to the 15:40 F&O close) never without that check
  test       any day and time, switch on or off, but not while paused; refused 09:00-15:40 on a
             trading day while the stored login works (Test now replaces today's login)

Market hours are the usual ones on a usual day. A day listed in the extra-days file with its own
window (a Muhurat or special Saturday session) trades in that window, so both rules above follow
it: Test now is open that morning and refused through the session (schedule.market_spans).

A pause begins when Upstox refuses the login for any reason other than "too many requests" and
ends only when the owner saves the TOTP key or the PIN again: the same refused pair is never sent
twice by a timer, a restart, the switch or Test now.

The TOTP key, the PIN, the TOTP code and the access token are never logged or printed.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import signal
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

import httpx

from sudarshan.vault import (
    HANDOFF_INFO,
    SecretUnreadable,
    Vault,
    VaultKeyError,
    fernet_for,
    pepper_from,
)

from . import messages
from .config import ConfigError, Settings, read_openalgo_env, setup_logging
from .handoff import LinkError, OpenAlgoLink, call_max_s
from .schedule import IST, TradingCalendar, minute_of_day, trading_session_start
from .totp import InvalidSecret, normalize_secret, seconds_left, totp
from .upstox import CALL_MAX_S as UPSTOX_CALL_MAX_S
from .upstox import LoginResult, request_token

log = logging.getLogger("sudarshan.autologin")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_BUSY = 75  # EX_TEMPFAIL: another attempt holds the lease (SuccessExitStatus in the unit)

KIND_SCHEDULED = "scheduled"
KIND_TEST = "test"
# Names of the vault rows that hold the TOTP key and the PIN (names, not values).
SECRET_TOTP = "upstox_totp_secret"  # pragma: allowlist secret
SECRET_PIN = "upstox_pin"  # pragma: allowlist secret

#: A crashed run's lease expires after this; a healthy run takes a few seconds. Longer than the
#: unit's TimeoutStartSec plus TimeoutStopSec and the launcher's limit (a test pins both), so no
#: second run can start while a slow one may still be talking to Upstox.
LEASE_TTL = timedelta(seconds=300)
#: Our own pace, well inside Upstox's 1/s, 10/min, 60/30 min for this endpoint.
MIN_SPACING = timedelta(seconds=60)
ATTEMPT_WINDOW = timedelta(minutes=30)
MAX_ATTEMPTS_PER_WINDOW = 10
#: A code with less life left than this waits for the next 30 s step instead of racing it.
TOTP_MIN_LEFT_S = 5.0
TOTP_STEP_MARGIN_S = 0.25
#: Pauses between hand-off retries when OpenAlgo does not answer (the token is already issued).
REPORT_RETRIES_S = (2.0, 5.0)
#: How long the first call to OpenAlgo keeps asking again while OpenAlgo answers that it is busy
#: or is not up yet (a boot, or a deploy restarting it), and the pause between two asks. Nothing
#: has been sent to Upstox yet, so waiting is free.
PREFLIGHT_PATIENCE_S = {KIND_SCHEDULED: 75.0, KIND_TEST: 10.0}
PREFLIGHT_RETRY_S = 5.0
PREFLIGHT_RETRY_KINDS = frozenset({"busy", "unreachable"})


def run_budget_s(kind: str) -> float:
    """The longest one run can take when every call uses up its timeouts and every retry happens.

    The preflight's patience plus its last call, the wait for a fresh TOTP step, the Upstox call,
    then the hand-off with its retries (a failure's single message to OpenAlgo is shorter). The
    launcher, the unit's TimeoutStartSec and LEASE_TTL are all longer, and a test holds them to it.
    """
    preflight = PREFLIGHT_PATIENCE_S[kind] + call_max_s("preflight")
    hand_off = sum(REPORT_RETRIES_S) + (1 + len(REPORT_RETRIES_S)) * call_max_s("report")
    return preflight + TOTP_MIN_LEFT_S + TOTP_STEP_MARGIN_S + UPSTOX_CALL_MAX_S + hand_off


@dataclass(frozen=True)
class Outcome:
    outcome: str  # ok | failed | skipped | refused
    code: str
    message: str
    exit_code: int


def rate_refusal(vault: Vault, state, now: datetime) -> str | None:
    """Why a call to Upstox must not be made now, or None. Shared with OpenAlgo's "Test now"."""
    if state.cooldown_until and state.cooldown_until > now:
        return "cooling_down"
    recent = vault.attempts_since(now - ATTEMPT_WINDOW)
    if recent and now - recent[-1].at < MIN_SPACING:
        return "too_soon"
    if len(recent) >= MAX_ATTEMPTS_PER_WINDOW:
        return "too_many"
    return None


class AutoLoginRun:
    """One run. Dependencies are passed in so tests drive it against fakes."""

    def __init__(
        self,
        *,
        vault: Vault,
        calendar: TradingCalendar,
        link: OpenAlgoLink,
        upstox: Callable[[str, str, str], LoginResult],
        api_key: str,
        kind: str,
        run_id: str,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.vault = vault
        self.calendar = calendar
        self.link = link
        self.upstox = upstox
        self.api_key = (api_key or "").strip()
        self.kind = kind
        self.run_id = run_id
        self.clock = clock or (lambda: datetime.now(IST))
        self.sleep = sleep

    @property
    def scheduled(self) -> bool:
        return self.kind == KIND_SCHEDULED

    def now(self) -> datetime:
        return self.clock()

    # -- entry --------------------------------------------------------------------------------

    def execute(self) -> int:
        if not self.vault.acquire_lease(self.run_id, now=self.now(), ttl=LEASE_TTL):
            log.info("another auto-login attempt is running; this one stops")
            return EXIT_BUSY
        try:
            outcome = self._run()
        except Exception:
            log.exception("auto-login run stopped unexpectedly")
            outcome = self._crashed()
        finally:
            with contextlib.suppress(Exception):
                self.vault.prune_attempts(self.now())
            with contextlib.suppress(Exception):
                self.vault.release_lease(self.run_id)
        log.info("auto-login %s run finished: %s (%s)", self.kind, outcome.outcome, outcome.code)
        return outcome.exit_code

    # -- the decision chain -------------------------------------------------------------------

    def _run(self) -> Outcome:
        state = self.vault.state()
        if self.scheduled and not state.enabled:
            return self._finish("skipped", "disabled")

        # OpenAlgo first: a day on which the owner already logged in by hand needs nothing,
        # and must not produce a failure message for a setup problem either.
        try:
            info = self._preflight()
        except LinkError as exc:
            log.error("OpenAlgo preflight failed: %s", exc.kind)
            code = {"rejected": "handoff_rejected", "busy": "openalgo_busy"}.get(
                exc.kind, "openalgo_unreachable"
            )
            return self._failed(code, setup=code == "handoff_rejected", can_notify=False)
        if not info.get("user"):
            return self._failed("no_user", setup=True)
        if info.get("broker") != "upstox":
            return self._failed("wrong_broker", setup=True)
        stop = self._keep_the_stored_login(info)
        if stop is not None:
            return stop

        if state.paused_code:
            # Upstox refused the saved key and PIN. Only a newly saved one ends the pause, so the
            # same ones are never sent again by a timer, a restart or Test now.
            if not self.scheduled:
                return self._finish("refused", "test_paused")
            # Told already this session, when the pause began or at an earlier wake-up.
            told = (
                state.last_run_at is not None
                and state.last_run_at >= trading_session_start(self.now())
                and state.last_code in ("paused", state.paused_code)
            )
            return self._failed(
                "paused",
                message=messages.paused_message(state.paused_code),
                setup=True,
                can_notify=not told,
            )
        try:
            totp_secret = self.vault.get_secret(SECRET_TOTP)
            pin = self.vault.get_secret(SECRET_PIN)
        except SecretUnreadable:
            return self._failed("secrets_unreadable", setup=True)
        if not totp_secret or not pin:
            if not self.scheduled:
                return self._finish("refused", "secrets_missing")
            return self._failed("secrets_missing", setup=True)
        try:
            totp_secret = normalize_secret(totp_secret)
        except InvalidSecret:
            return self._failed("secret_invalid", setup=True)
        if not self.api_key:
            return self._failed("not_configured", setup=True)

        refusal = rate_refusal(self.vault, state, self.now())
        if refusal:
            if self.scheduled:
                return self._finish("skipped", refusal)
            return self._finish("refused", refusal)

        at = self._code_time()
        code = totp(totp_secret, at.timestamp())
        attempt_id = self.vault.start_attempt(kind=self.kind, at=at)
        log.info("asking Upstox for today's token (%s run)", self.kind)
        result = self.upstox(self.api_key, code, pin)
        if not result.ok:
            self.vault.finish_attempt(attempt_id, outcome="failed", code=result.code)
            extra = {}
            if result.pause:
                extra["paused_code"] = result.code
            if result.cooldown_s:
                extra["cooldown_until"] = self.now() + timedelta(seconds=result.cooldown_s)
            message = messages.paused_message(result.code) if result.pause else None
            return self._failed(result.code, message=message, setup=result.pause, status=extra)
        self.vault.finish_attempt(attempt_id, outcome="ok", code="ok")
        log.info("Upstox issued a token; handing it to OpenAlgo")
        return self._hand_over(result.token)

    def _keep_the_stored_login(self, info: dict) -> Outcome | None:
        """Stop here when a TOTP login could end a login that still works (it would revoke it).

        ``token`` is OpenAlgo's check of the stored token with Upstox: works, dead, unknown (the
        check failed) or none. ``fresh_session``: a login on record since 03:30 IST.

          scheduled  works: nothing to do. unknown: trust a fresh login; with none on record,
                     log in before 09:15 but never during market hours. dead or none: log in.
          test       works or unknown, 09:00-15:40 on a trading day: refused, because Test now
                     replaces today's login. Otherwise it goes ahead.
        """
        token = info.get("token", "unknown")
        fresh = bool(info.get("fresh_session"))
        now = self.now()
        if not self.scheduled:
            if token in ("works", "unknown") and self.calendar.blocks_test_now(now):
                return self._finish("refused", "market_hours")
            return None
        if token == "works" or (token == "unknown" and fresh):
            return self._finish("skipped", "already_logged_in")
        if token == "unknown" and self.calendar.in_market_hours(now):
            return self._failed("token_unchecked", setup=True)
        return None

    def _preflight(self) -> dict:
        """OpenAlgo's answer, asking again every few seconds while it says it is busy.

        Bounded twice: by the clock (a slow answer uses up the patience too) and by a count of
        tries, so a clock that does not move cannot keep a run here.
        """
        patience = PREFLIGHT_PATIENCE_S[self.kind]
        give_up = self.now() + timedelta(seconds=patience)
        retries_left = int(patience // PREFLIGHT_RETRY_S)
        while True:
            try:
                return self.link.preflight()
            except LinkError as exc:
                too_late = self.now() + timedelta(seconds=PREFLIGHT_RETRY_S) > give_up
                if exc.kind not in PREFLIGHT_RETRY_KINDS or retries_left <= 0 or too_late:
                    raise
                retries_left -= 1
                log.info(
                    "OpenAlgo answered %s; asking again in %.0f s", exc.kind, PREFLIGHT_RETRY_S
                )
                self.sleep(PREFLIGHT_RETRY_S)

    def _code_time(self) -> datetime:
        at = self.now()
        left = seconds_left(at.timestamp())
        if left < TOTP_MIN_LEFT_S:
            self.sleep(left + TOTP_STEP_MARGIN_S)
            at = self.now()
        return at

    def _hand_over(self, token: str) -> Outcome:
        at = self.now()
        text = messages.telegram_ok(at, test=not self.scheduled)
        delays = (0.0, *REPORT_RETRIES_S)
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                self.sleep(delay)
            try:
                answer = self.link.report(
                    outcome="ok", kind=self.kind, run_id=self.run_id, token=token, telegram=text
                )
            except LinkError as exc:
                log.warning("hand-off to OpenAlgo failed (try %d): %s", attempt, exc.kind)
                if exc.kind == "rejected":
                    return self._failed("handoff_rejected", setup=True, can_notify=False)
                continue
            if answer.get("applied"):
                log.info("OpenAlgo stored the new Upstox login")
                return self._finish(
                    "ok",
                    "ok",
                    status={"last_success_at": at, "paused_code": None, "cooldown_until": None},
                )
            log.error("OpenAlgo did not store the new login")
            return self._failed("store_failed", can_notify=False)
        return self._failed("handoff_failed", can_notify=False)

    # -- recording and telling the owner ------------------------------------------------------

    def _finish(
        self, outcome: str, code: str, *, message: str | None = None, status: dict | None = None
    ) -> Outcome:
        text = message or messages.message_for(code)
        self.vault.update_status(
            last_run_id=self.run_id,
            last_run_at=self.now(),
            last_run_kind=self.kind,
            last_outcome=outcome,
            last_code=code,
            last_message=text,
            **(status or {}),
        )
        exit_code = EXIT_FAILED if outcome == "failed" else EXIT_OK
        return Outcome(outcome, code, text, exit_code)

    def _failed(
        self,
        code: str,
        *,
        message: str | None = None,
        setup: bool = False,
        can_notify: bool = True,
        status: dict | None = None,
    ) -> Outcome:
        state = self.vault.state()
        # A setup problem is told once per trading session, not at every wake-up.
        repeat = (
            setup
            and self.scheduled
            and state.last_code == code
            and state.last_run_at is not None
            and state.last_run_at >= trading_session_start(self.now())
        )
        outcome = self._finish("failed", code, message=message, status=status)
        if can_notify and not repeat:
            next_try = None
            if self.scheduled and not setup:
                next_try = self.calendar.later_slot_today(self.now())
            text = messages.telegram_failed(
                outcome.message, at=self.now(), test=not self.scheduled, next_try=next_try
            )
            self._tell(text)
        return outcome

    def _crashed(self) -> Outcome:
        try:
            return self._failed("crashed")
        except Exception:
            log.exception("could not record the failed run")
            return Outcome("failed", "crashed", messages.message_for("crashed"), EXIT_FAILED)

    def _tell(self, text: str) -> None:
        try:
            self.link.report(outcome="failed", kind=self.kind, run_id=self.run_id, telegram=text)
        except LinkError as exc:
            log.warning("could not ask OpenAlgo to send the Telegram message: %s", exc.kind)


# -- process entry --------------------------------------------------------------------------------


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m sudarshan.autologin")
    parser.add_argument(
        "--test", action="store_true", help="one attempt now, at any time (the Test now button)"
    )
    parser.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _terminate(signum, frame):
    raise SystemExit(128 + signum)  # unwinds through finally: the lease is released


def main(argv=None, *, env=None, clock=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    setup_logging()
    previous = None
    with contextlib.suppress(ValueError):  # not in the main thread (tests)
        previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        return _main(args, env, clock)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _main(args, env, clock) -> int:
    try:
        settings = Settings.from_env(env)
    except (ConfigError, ValueError) as exc:
        log.error("auto-login settings are invalid: %s", exc)
        return EXIT_CONFIG
    kind = KIND_TEST if args.test else KIND_SCHEDULED
    run_id = (args.run_id or uuid.uuid4().hex)[:40]
    calendar = TradingCalendar(settings.holidays_file, settings.extra_days_file)
    clock = clock or (lambda: datetime.now(IST))
    now = clock()
    if kind == KIND_SCHEDULED:
        if not calendar.is_trading_day(now.astimezone(IST).date()):
            log.info("not a trading day: nothing to do")
            return EXIT_OK
        start, end = settings.window
        if not start <= minute_of_day(now) < end:
            log.info("outside the auto-login window: nothing to do")
            return EXIT_OK
    try:
        env_values = read_openalgo_env(settings.env_file)
        pepper = pepper_from(env_values)
    except (ConfigError, VaultKeyError) as exc:
        log.error("cannot read OpenAlgo's settings: %s", exc)
        return EXIT_CONFIG
    if not settings.vault_path.exists():
        # OpenAlgo creates the vault the first time the Auto login page is opened; until then there
        # is nothing to do (and this unit may not write outside the vault's directory anyway).
        log.info("auto-login is not set up yet: no TOTP key or PIN has been saved")
        return EXIT_OK
    vault = Vault(settings.vault_path, pepper=pepper)
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as local_client,
        httpx.Client(follow_redirects=False) as upstox_client,
    ):
        run = AutoLoginRun(
            vault=vault,
            calendar=calendar,
            link=OpenAlgoLink(
                settings.openalgo_url, fernet_for(pepper, HANDOFF_INFO), local_client
            ),
            upstox=partial(request_token, upstox_client, settings.upstox_base),
            api_key=env_values.get("BROKER_API_KEY", ""),
            kind=kind,
            run_id=run_id,
            clock=clock,
        )
        return run.execute()
