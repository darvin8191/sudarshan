"""The worker's decisions: when it logs in, when it refuses, what it records and tells the owner.

OpenAlgo is a FakeLink and Upstox a recording callable here; test_autologin_e2e.py runs the same
worker against a real Flask app and a fake Upstox HTTP server.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta

import pytest
from autologin_fakes import (
    FAKE_API_KEY,
    FAKE_PIN,
    FAKE_TOKEN,
    FAKE_TOTP_SECRET,
    MONDAY_0835,
    FakeClock,
    FakeLink,
)

from sudarshan.autologin import worker
from sudarshan.autologin.handoff import LinkError
from sudarshan.autologin.schedule import IST, TradingCalendar
from sudarshan.autologin.totp import totp
from sudarshan.autologin.upstox import LoginResult
from sudarshan.autologin.worker import (
    EXIT_BUSY,
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_OK,
    SECRET_PIN,
    SECRET_TOTP,
    AutoLoginRun,
)
from sudarshan.vault import Vault


class FakeUpstoxCall:
    def __init__(self, *results):
        self.results = deque(results)
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, api_key, code, pin):
        self.calls.append((api_key, code, pin))
        result = self.results.popleft() if self.results else None
        if isinstance(result, Exception):
            raise result
        return result or LoginResult(ok=True, code="ok", token=FAKE_TOKEN, http_status=200)


@pytest.fixture
def ready_vault(vault):
    vault.set_secret(SECRET_TOTP, FAKE_TOTP_SECRET)
    vault.set_secret(SECRET_PIN, FAKE_PIN)
    vault.set_enabled(True)
    return vault


@pytest.fixture
def calendar(holidays_file):
    return TradingCalendar(holidays_file)


def make_run(
    vault,
    calendar,
    *,
    link=None,
    upstox=None,
    kind="scheduled",
    at=MONDAY_0835,
    api_key=FAKE_API_KEY,
):
    clock = FakeClock(at)
    sleeps: list[float] = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.advance(seconds=seconds)

    run = AutoLoginRun(
        vault=vault,
        calendar=calendar,
        link=link or FakeLink(),
        upstox=upstox or FakeUpstoxCall(),
        api_key=api_key,
        kind=kind,
        run_id=f"run-{kind}-{at:%H%M%S}",
        clock=clock,
        sleep=sleep,
    )
    run.sleeps = sleeps
    return run


# -- success ----------------------------------------------------------------------------------


def test_scheduled_success(ready_vault, calendar, caplog):
    caplog.set_level(logging.DEBUG)
    link, upstox = FakeLink(), FakeUpstoxCall()
    run = make_run(ready_vault, calendar, link=link, upstox=upstox)
    assert run.execute() == EXIT_OK

    code = totp(FAKE_TOTP_SECRET, MONDAY_0835.timestamp())
    assert upstox.calls == [(FAKE_API_KEY, code, FAKE_PIN)]
    assert [op for op, _ in link.calls] == ["preflight", "report"]
    (report,) = link.reports("ok")
    assert report["token"] == FAKE_TOKEN and report["kind"] == "scheduled"
    assert report["telegram"].endswith("Upstox auto-login OK (08:35 IST).")
    assert "SUDARSHAN" in report["telegram"]

    state = ready_vault.state()
    assert (state.last_outcome, state.last_code) == ("ok", "ok")
    assert state.last_success_at == MONDAY_0835 and state.last_message == "Logged in to Upstox."
    assert state.lease_owner is None
    (attempt,) = ready_vault.recent_attempts()
    assert (attempt.outcome, attempt.kind) == ("ok", "scheduled")
    for secret in (FAKE_TOTP_SECRET, FAKE_PIN, FAKE_TOKEN, code):
        assert secret not in caplog.text


def test_success_clears_an_earlier_cooldown(ready_vault, calendar):
    ready_vault.update_status(cooldown_until=MONDAY_0835 - timedelta(minutes=1))
    assert make_run(ready_vault, calendar, kind="test").execute() == EXIT_OK
    state = ready_vault.state()
    assert state.paused_code is None and state.cooldown_until is None


def test_test_now_is_refused_while_paused(ready_vault, calendar):
    """A refused key and PIN are not sent again by Test now either, until one is saved again."""
    ready_vault.update_status(paused_code="invalid_credentials")
    link, upstox = FakeLink(), FakeUpstoxCall()
    assert (
        make_run(ready_vault, calendar, link=link, upstox=upstox, kind="test").execute() == EXIT_OK
    )
    assert upstox.calls == [] and link.reports() == []
    state = ready_vault.state()
    assert (state.last_outcome, state.last_code) == ("refused", "test_paused")
    assert "Save the TOTP key or the PIN again first" in state.last_message

    ready_vault.update_status(paused_code=None)  # what saving a secret does
    assert (
        make_run(ready_vault, calendar, link=link, upstox=upstox, kind="test").execute() == EXIT_OK
    )
    assert len(upstox.calls) == 1


def test_code_is_not_used_in_its_last_seconds(ready_vault, calendar):
    at = MONDAY_0835.replace(second=28)  # 2 s left in this step
    upstox = FakeUpstoxCall()
    run = make_run(ready_vault, calendar, upstox=upstox, at=at)
    assert run.execute() == EXIT_OK
    assert run.sleeps and 2.0 < run.sleeps[0] < 3.0
    (_, code, _) = upstox.calls[0]
    assert code == totp(FAKE_TOTP_SECRET, (at + timedelta(seconds=30)).timestamp())


# -- nothing to do ----------------------------------------------------------------------------


def test_switched_off_scheduled_run_records_and_stops(ready_vault, calendar):
    ready_vault.set_enabled(False)
    link, upstox = FakeLink(), FakeUpstoxCall()
    assert make_run(ready_vault, calendar, link=link, upstox=upstox).execute() == EXIT_OK
    assert upstox.calls == [] and link.calls == []
    state = ready_vault.state()
    assert (state.last_outcome, state.last_code) == ("skipped", "disabled")


def test_test_run_works_while_switched_off(ready_vault, calendar):
    ready_vault.set_enabled(False)
    upstox = FakeUpstoxCall()
    assert make_run(ready_vault, calendar, upstox=upstox, kind="test").execute() == EXIT_OK
    assert len(upstox.calls) == 1
    assert ready_vault.state().enabled is False


def test_already_logged_in_today_is_left_alone(ready_vault, calendar):
    link, upstox = FakeLink(fresh=True), FakeUpstoxCall()
    assert make_run(ready_vault, calendar, link=link, upstox=upstox).execute() == EXIT_OK
    assert upstox.calls == [] and link.reports() == []
    assert ready_vault.state().last_code == "already_logged_in"


@pytest.mark.parametrize(
    ("at", "allowed"),
    [
        (datetime(2026, 10, 19, 9, 0, 10, tzinfo=IST), False),  # the pre-open
        (datetime(2026, 10, 19, 9, 14, 10, tzinfo=IST), False),
        (datetime(2026, 10, 19, 9, 15, tzinfo=IST), False),
        (datetime(2026, 10, 19, 12, 0, tzinfo=IST), False),
        (datetime(2026, 10, 19, 15, 29, 40, tzinfo=IST), False),
        (datetime(2026, 10, 19, 15, 30, 10, tzinfo=IST), False),  # cash closed, F&O trades on
        (datetime(2026, 10, 19, 15, 39, 40, tzinfo=IST), False),
        (datetime(2026, 10, 19, 15, 40, 10, tzinfo=IST), True),  # the F&O close (since 3 Aug 2026)
        (datetime(2026, 10, 19, 8, 59, 10, tzinfo=IST), True),
        (datetime(2026, 10, 18, 11, 0, 10, tzinfo=IST), True),  # Sunday
        (datetime(2026, 11, 9, 11, 0, 10, tzinfo=IST), True),  # holiday in the fake file
    ],
)
def test_test_run_never_replaces_a_live_session_in_market_hours(ready_vault, calendar, at, allowed):
    upstox = FakeUpstoxCall()
    run = make_run(
        ready_vault, calendar, link=FakeLink(fresh=True), upstox=upstox, kind="test", at=at
    )
    assert run.execute() == EXIT_OK
    assert bool(upstox.calls) is allowed
    if not allowed:
        state = ready_vault.state()
        assert (state.last_outcome, state.last_code) == ("refused", "market_hours")


MONDAY_1000 = MONDAY_0835.replace(hour=10, minute=0, second=10)


@pytest.mark.parametrize(
    ("token", "fresh", "at", "logs_in", "code"),
    [
        # A stored login Upstox still accepts is never replaced, whatever the record says.
        ("works", False, MONDAY_0835, False, "already_logged_in"),
        ("works", False, MONDAY_1000, False, "already_logged_in"),
        # A dead or missing one is replaced, in market hours too: it cannot be used anyway.
        ("dead", True, MONDAY_1000, True, "ok"),
        ("none", False, MONDAY_1000, True, "ok"),
        # The check could not tell: trust a login since 03:30; without one, log in before 09:15
        # (the stored token cannot be today's) but never during market hours.
        ("unknown", True, MONDAY_1000, False, "already_logged_in"),
        ("unknown", False, MONDAY_0835, True, "ok"),
        ("unknown", False, MONDAY_1000, False, "token_unchecked"),
    ],
)
def test_scheduled_runs_never_end_a_login_that_may_still_work(
    ready_vault, calendar, token, fresh, at, logs_in, code
):
    link, upstox = FakeLink(fresh=fresh, token=token), FakeUpstoxCall()
    make_run(ready_vault, calendar, link=link, upstox=upstox, at=at).execute()
    assert bool(upstox.calls) is logs_in
    assert ready_vault.state().last_code == code


def test_an_unchecked_login_in_market_hours_is_told_once(ready_vault, calendar):
    link = FakeLink(token="unknown")
    for minutes in (0, 10, 20):
        at = MONDAY_1000 + timedelta(minutes=minutes)
        assert make_run(ready_vault, calendar, link=link, at=at).execute() == EXIT_FAILED
    (report,) = link.reports("failed")
    assert "could not confirm whether the stored login still works" in report["telegram"]
    assert report["telegram"].endswith("Please log in by hand now.")


@pytest.mark.parametrize(
    ("token", "at", "allowed"),
    [
        ("unknown", MONDAY_1000, False),  # cannot tell: Test now could end a working login
        ("dead", MONDAY_1000, True),  # nothing to lose: Test now can fix it
        ("none", MONDAY_1000, True),
        ("works", MONDAY_0835.replace(hour=18), True),  # after the close it may replace it
    ],
)
def test_test_runs_follow_the_stored_login_check(ready_vault, calendar, token, at, allowed):
    upstox = FakeUpstoxCall()
    run = make_run(
        ready_vault, calendar, link=FakeLink(token=token), upstox=upstox, kind="test", at=at
    )
    run.execute()
    assert bool(upstox.calls) is allowed


# -- Upstox says no -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "pause", "text"),
    [
        ("invalid_credentials", True, "did not accept the saved TOTP key or PIN"),
        ("totp_not_enabled", True, "TOTP is not turned on"),
        ("not_individual_app", True, "individual (personal) API app"),
        ("upstox_refused", True, "Upstox refused the login"),
    ],
)
def test_credential_problems_pause_scheduled_runs(ready_vault, calendar, code, pause, text):
    link = FakeLink()
    upstox = FakeUpstoxCall(LoginResult(ok=False, code=code, pause=pause, http_status=401))
    assert make_run(ready_vault, calendar, link=link, upstox=upstox).execute() == EXIT_FAILED
    state = ready_vault.state()
    assert (state.last_outcome, state.last_code, state.paused_code) == ("failed", code, code)
    (report,) = link.reports("failed")
    assert text in report["telegram"]
    assert report["telegram"].count("paused") == 1  # said once
    assert "save the TOTP key or the PIN again" in report["telegram"]
    assert "Please log in by hand before 09:15." in report["telegram"]
    assert "try again" not in report["telegram"]  # no retry with the same wrong secrets
    assert state.last_message.count("paused") == 1

    # 08:45 and 08:55: paused, so Upstox is not asked again, and the owner is not told again.
    for minutes in (10, 20):
        upstox_later = FakeUpstoxCall()
        later = make_run(
            ready_vault,
            calendar,
            link=link,
            upstox=upstox_later,
            at=MONDAY_0835 + timedelta(minutes=minutes),
        )
        assert later.execute() == EXIT_FAILED
        assert upstox_later.calls == []
        assert ready_vault.state().last_code == "paused"
    assert len(link.reports("failed")) == 1

    # Next trading day: one reminder, then quiet again.
    tuesday = MONDAY_0835 + timedelta(days=1)
    assert make_run(ready_vault, calendar, link=link, at=tuesday).execute() == EXIT_FAILED
    assert (
        make_run(ready_vault, calendar, link=link, at=tuesday + timedelta(minutes=10)).execute()
        == EXIT_FAILED
    )
    reminders = link.reports("failed")[1:]
    assert len(reminders) == 1 and "Auto-login is paused until" in reminders[0]["telegram"]
    assert reminders[0]["telegram"].count("paused") == 1


def test_rate_limited_sets_a_cooldown_and_retries_at_the_next_slot(ready_vault, calendar):
    link = FakeLink()
    upstox = FakeUpstoxCall(LoginResult(ok=False, code="rate_limited", cooldown_s=300))
    assert make_run(ready_vault, calendar, link=link, upstox=upstox).execute() == EXIT_FAILED
    state = ready_vault.state()
    assert state.cooldown_until == MONDAY_0835 + timedelta(seconds=300)
    assert state.paused_code is None
    (report,) = link.reports("failed")
    assert "Will try again at 08:45" in report["telegram"]

    upstox_later = FakeUpstoxCall()
    later = make_run(
        ready_vault,
        calendar,
        link=link,
        upstox=upstox_later,
        at=MONDAY_0835 + timedelta(minutes=10),
    )
    assert later.execute() == EXIT_OK and len(upstox_later.calls) == 1


@pytest.mark.parametrize(
    ("at", "action"),
    [
        (
            MONDAY_0835,
            "Will try again at 08:45. If that fails too, please log in by hand before 09:15.",
        ),
        (MONDAY_0835.replace(minute=55), "Please log in by hand before 09:15."),
        (MONDAY_0835.replace(hour=10, minute=5), "Please log in by hand now."),
    ],
)
def test_network_failure_message_says_what_happens_next(ready_vault, calendar, at, action):
    link = FakeLink()
    upstox = FakeUpstoxCall(LoginResult(ok=False, code="network"))
    assert make_run(ready_vault, calendar, link=link, upstox=upstox, at=at).execute() == EXIT_FAILED
    (report,) = link.reports("failed")
    assert report["telegram"].startswith(
        "सुदर्शन / SUDARSHAN\nUpstox auto-login failed. Could not reach Upstox."
    )
    assert report["telegram"].endswith(action)
    assert ready_vault.state().paused_code is None


def test_test_run_failure_message(ready_vault, calendar):
    link = FakeLink()
    upstox = FakeUpstoxCall(LoginResult(ok=False, code="upstox_unavailable"))
    assert (
        make_run(ready_vault, calendar, link=link, upstox=upstox, kind="test").execute()
        == EXIT_FAILED
    )
    (report,) = link.reports("failed")
    assert report["telegram"] == (
        "सुदर्शन / SUDARSHAN\nTest: Upstox auto-login failed. "
        "Upstox's login service is not working right now."
    )


# -- setup problems -----------------------------------------------------------------------------


def test_missing_secrets(vault, calendar):
    vault.set_enabled(True)
    vault.set_secret(SECRET_TOTP, FAKE_TOTP_SECRET)  # PIN not saved
    link, upstox = FakeLink(), FakeUpstoxCall()
    assert make_run(vault, calendar, link=link, upstox=upstox).execute() == EXIT_FAILED
    assert upstox.calls == []
    assert vault.state().last_code == "secrets_missing"
    (report,) = link.reports("failed")
    assert "The TOTP key or the PIN is not saved yet." in report["telegram"]
    # Test now: refused, not a failure, and no Telegram message
    assert make_run(vault, calendar, link=link, kind="test").execute() == EXIT_OK
    assert vault.state().last_outcome == "refused"
    assert len(link.reports("failed")) == 1


def test_a_saved_key_that_is_not_a_valid_key(ready_vault, calendar):
    ready_vault.set_secret(SECRET_TOTP, "not a key!")  # only an old version or a hand edit
    link, upstox = FakeLink(), FakeUpstoxCall()
    assert make_run(ready_vault, calendar, link=link, upstox=upstox).execute() == EXIT_FAILED
    assert upstox.calls == []
    state = ready_vault.state()
    assert state.last_code == "secret_invalid" and "not a valid key" in state.last_message
    assert "encryption key" not in link.reports("failed")[0]["telegram"]


def test_secrets_saved_under_another_pepper(ready_vault, calendar):
    rotated = Vault(ready_vault.path, pepper="c3" * 32)  # fake, as after rotate_pepper.py
    link, upstox = FakeLink(), FakeUpstoxCall()
    assert make_run(rotated, calendar, link=link, upstox=upstox).execute() == EXIT_FAILED
    assert upstox.calls == []
    assert rotated.state().last_code == "secrets_unreadable"
    assert "Save them again" in link.reports("failed")[0]["telegram"]


@pytest.mark.parametrize(
    ("link", "api_key", "code"),
    [
        (FakeLink(broker="zerodha"), FAKE_API_KEY, "wrong_broker"),
        (FakeLink(user=False), FAKE_API_KEY, "no_user"),
        (FakeLink(), "", "not_configured"),
    ],
)
def test_configuration_problems(ready_vault, calendar, link, api_key, code):
    upstox = FakeUpstoxCall()
    run = make_run(ready_vault, calendar, link=link, upstox=upstox, api_key=api_key)
    assert run.execute() == EXIT_FAILED
    assert upstox.calls == []
    assert ready_vault.state().last_code == code


@pytest.mark.parametrize(
    ("kind", "code"), [("unreachable", "openalgo_unreachable"), ("rejected", "handoff_rejected")]
)
def test_openalgo_not_answering_before_login(ready_vault, calendar, kind, code):
    link, upstox = FakeLink(), FakeUpstoxCall()
    link.preflight_error = LinkError(kind, 403 if kind == "rejected" else None)
    assert make_run(ready_vault, calendar, link=link, upstox=upstox).execute() == EXIT_FAILED
    assert upstox.calls == []  # never revoke a working token we could not hand over
    assert ready_vault.state().last_code == code
    assert link.reports() == []  # the Telegram path runs through OpenAlgo itself


def test_preflight_asks_again_while_openalgo_is_busy(ready_vault, calendar):
    link, upstox = FakeLink(), FakeUpstoxCall()
    link.preflight_errors.extend([LinkError("busy", 429)] * 2)
    run = make_run(ready_vault, calendar, link=link, upstox=upstox)
    assert run.execute() == EXIT_OK
    assert run.sleeps == [5.0, 5.0]
    assert [op for op, _ in link.calls] == ["preflight"] * 3 + ["report"]
    assert len(upstox.calls) == 1


@pytest.mark.parametrize(("kind", "waits"), [("scheduled", 15), ("test", 2)])
def test_preflight_stops_asking_in_time(ready_vault, calendar, kind, waits):
    link, upstox = FakeLink(), FakeUpstoxCall()
    link.preflight_error = LinkError("busy", 429)
    run = make_run(ready_vault, calendar, link=link, upstox=upstox, kind=kind)
    assert run.execute() == EXIT_FAILED
    assert run.sleeps == [worker.PREFLIGHT_RETRY_S] * waits  # 75 s scheduled, 10 s test
    assert upstox.calls == [] and link.reports() == []
    state = ready_vault.state()
    assert state.last_code == "openalgo_busy" and "kept asking to wait" in state.last_message


def test_preflight_does_not_wait_on_a_refusal(ready_vault, calendar):
    link = FakeLink()
    link.preflight_error = LinkError("rejected", 404)
    run = make_run(ready_vault, calendar, link=link)
    assert run.execute() == EXIT_FAILED
    assert run.sleeps == [] and ready_vault.state().last_code == "handoff_rejected"


def test_preflight_patience_is_bounded_without_a_moving_clock(ready_vault, calendar):
    link = FakeLink()
    link.preflight_error = LinkError("busy", 429)
    run = make_run(ready_vault, calendar, link=link)
    run.sleep = lambda seconds: None  # the clock never moves
    assert run.execute() == EXIT_FAILED
    patience = worker.PREFLIGHT_PATIENCE_S["scheduled"]
    assert len(link.calls) == 1 + int(patience // worker.PREFLIGHT_RETRY_S)


# -- our own pace -------------------------------------------------------------------------------


def test_spacing_between_attempts(ready_vault, calendar):
    attempt = ready_vault.start_attempt(kind="test", at=MONDAY_0835 - timedelta(seconds=30))
    ready_vault.finish_attempt(attempt, outcome="failed", code="network")
    upstox = FakeUpstoxCall()
    assert make_run(ready_vault, calendar, upstox=upstox, kind="test").execute() == EXIT_OK
    assert upstox.calls == []
    state = ready_vault.state()
    assert (state.last_outcome, state.last_code) == ("refused", "too_soon")
    assert make_run(ready_vault, calendar, upstox=upstox).execute() == EXIT_OK  # scheduled: skipped
    assert ready_vault.state().last_outcome == "skipped" and upstox.calls == []


def test_attempt_cap_per_30_minutes(ready_vault, calendar):
    for minutes_ago in range(29, 9, -2):  # ten attempts in the last 30 minutes
        ready_vault.start_attempt(kind="test", at=MONDAY_0835 - timedelta(minutes=minutes_ago))
    upstox = FakeUpstoxCall()
    assert make_run(ready_vault, calendar, upstox=upstox, kind="test").execute() == EXIT_OK
    assert upstox.calls == [] and ready_vault.state().last_code == "too_many"


def test_cooldown_blocks_test_runs(ready_vault, calendar):
    ready_vault.update_status(cooldown_until=MONDAY_0835 + timedelta(minutes=2))
    upstox = FakeUpstoxCall()
    assert make_run(ready_vault, calendar, upstox=upstox, kind="test").execute() == EXIT_OK
    assert upstox.calls == [] and ready_vault.state().last_code == "cooling_down"


def test_one_run_at_a_time(ready_vault, calendar):
    from datetime import timedelta as td

    assert ready_vault.acquire_lease("other-run", now=MONDAY_0835, ttl=td(seconds=180))
    upstox = FakeUpstoxCall()
    assert make_run(ready_vault, calendar, upstox=upstox).execute() == EXIT_BUSY
    assert upstox.calls == [] and ready_vault.state().last_run_at is None


# -- handing the token over ---------------------------------------------------------------------


def test_hand_off_retries_when_openalgo_is_briefly_away(ready_vault, calendar):
    link = FakeLink()
    link.report_answers.extend([LinkError("unreachable"), LinkError("unreachable")])
    run = make_run(ready_vault, calendar, link=link)
    assert run.execute() == EXIT_OK
    assert run.sleeps == [2.0, 5.0]
    assert len(link.reports("ok")) == 3


def test_hand_off_gives_up_and_says_so(ready_vault, calendar):
    link = FakeLink()
    link.report_answers.extend([LinkError("unreachable")] * 3)
    run = make_run(ready_vault, calendar, link=link)
    assert run.execute() == EXIT_FAILED
    state = ready_vault.state()
    assert state.last_code == "handoff_failed" and state.last_success_at is None
    assert "was not saved" in state.last_message
    assert link.reports("failed") == []


def test_openalgo_could_not_store_the_token(ready_vault, calendar):
    link = FakeLink()
    link.report_answers.append({"status": "error", "applied": False, "message": "x"})
    assert make_run(ready_vault, calendar, link=link).execute() == EXIT_FAILED
    assert ready_vault.state().last_code == "store_failed"
    assert link.reports("failed") == []  # OpenAlgo already told the owner


def test_hand_off_refused(ready_vault, calendar):
    link = FakeLink()
    link.report_answers.append(LinkError("rejected", 403))
    run = make_run(ready_vault, calendar, link=link)
    assert run.execute() == EXIT_FAILED
    assert ready_vault.state().last_code == "handoff_rejected" and run.sleeps == []


def test_unexpected_error_is_recorded_and_the_lease_released(ready_vault, calendar):
    link = FakeLink()
    upstox = FakeUpstoxCall(RuntimeError("boom"))
    assert make_run(ready_vault, calendar, link=link, upstox=upstox).execute() == EXIT_FAILED
    state = ready_vault.state()
    assert state.last_code == "crashed" and state.lease_owner is None
    assert "stopped unexpectedly" in link.reports("failed")[0]["telegram"]


# -- process entry: what a timer or a deploy restart may trigger --------------------------------


def _entry_env(tmp_path, openalgo_env_file, holidays_file, **extra):
    return {
        "SUDARSHAN_OPENALGO_DIR": str(tmp_path),
        "SUDARSHAN_OPENALGO_ENV_FILE": str(openalgo_env_file),
        "SUDARSHAN_VAULT_DB": str(tmp_path / "db" / "entry_vault.db"),
        "SUDARSHAN_NSE_HOLIDAYS_FILE": str(holidays_file),
        "SUDARSHAN_OPENALGO_URL": "http://127.0.0.1:9",
        **extra,
    }


@pytest.mark.parametrize(
    "at",
    [
        datetime(2026, 10, 17, 8, 35, tzinfo=IST),  # Saturday
        datetime(2026, 10, 18, 8, 35, tzinfo=IST),  # Sunday
        datetime(2026, 11, 9, 8, 35, tzinfo=IST),  # NSE holiday (fake file)
        datetime(2026, 10, 19, 8, 29, tzinfo=IST),  # before the window
        datetime(2026, 10, 19, 15, 40, tzinfo=IST),  # after it (the F&O close)
        datetime(2026, 10, 19, 16, 5, tzinfo=IST),  # a deploy restarting the unit after close
        datetime(2026, 10, 19, 2, 0, tzinfo=IST),  # Persistent= catching up at night
    ],
)
def test_scheduled_start_outside_its_time_does_nothing(
    tmp_path, openalgo_env_file, holidays_file, at
):
    env = _entry_env(tmp_path, openalgo_env_file, holidays_file)
    assert worker.main([], env=env, clock=lambda: at) == EXIT_OK
    assert not (tmp_path / "db" / "entry_vault.db").exists()


def test_an_extra_trading_day_on_a_weekend_gets_its_login(
    tmp_path, openalgo_env_file, holidays_file, caplog
):
    sunday = datetime(2026, 10, 18, 8, 35, tzinfo=IST)
    caplog.set_level(logging.INFO)
    env = _entry_env(tmp_path, openalgo_env_file, holidays_file)
    assert worker.main([], env=env, clock=lambda: sunday) == EXIT_OK
    assert "not a trading day" in caplog.text
    caplog.clear()
    extra = tmp_path / "nse_extra_trading_days.txt"
    extra.write_text("# a session on a Sunday\n2026-10-18\n")
    env["SUDARSHAN_NSE_EXTRA_DAYS_FILE"] = str(extra)
    assert worker.main([], env=env, clock=lambda: sunday) == EXIT_OK
    assert "not a trading day" not in caplog.text and "not set up yet" in caplog.text


def test_no_vault_yet_means_nothing_to_do(tmp_path, openalgo_env_file, holidays_file, caplog):
    """Before the first saved key or PIN there is no vault, and the unit may not create one."""
    caplog.set_level(logging.INFO)
    env = _entry_env(tmp_path, openalgo_env_file, holidays_file)
    assert worker.main([], env=env, clock=lambda: MONDAY_0835) == EXIT_OK
    assert not (tmp_path / "db").exists()
    assert "not set up yet" in caplog.text


def test_bad_settings_stop_the_worker(tmp_path, openalgo_env_file, holidays_file):
    env = _entry_env(
        tmp_path,
        openalgo_env_file,
        holidays_file,
        SUDARSHAN_UPSTOX_API_BASE="http://example.invalid",
    )
    assert worker.main(["--test"], env=env) == EXIT_CONFIG
    env = _entry_env(
        tmp_path, openalgo_env_file, holidays_file, SUDARSHAN_OPENALGO_URL="http://10.9.9.9:5000"
    )
    assert worker.main(["--test"], env=env) == EXIT_CONFIG


def test_unreadable_env_file_stops_the_worker(tmp_path, holidays_file):
    env = _entry_env(tmp_path, tmp_path / "missing.env", holidays_file)
    assert worker.main(["--test"], env=env) == EXIT_CONFIG


def test_entry_restores_the_sigterm_handler(tmp_path, openalgo_env_file, holidays_file):
    import signal

    before = signal.getsignal(signal.SIGTERM)
    worker.main(
        [],
        env=_entry_env(tmp_path, openalgo_env_file, holidays_file),
        clock=lambda: datetime(2026, 10, 17, 8, 35, tzinfo=IST),
    )
    assert signal.getsignal(signal.SIGTERM) is before
