"""F3: family accounts: argon2id, the dummy verify, TOTP accepted once, invites, sessions, the device
limit, lockout, the owner's member ops and the notices and revokes they leave (design 2.2, 7.2).

The flows run with a fast argon2 (a few KiB, one pass) so that the file stays quick; the production
parameters are pinned on their own, and the dummy verify is held both by its structure (one verify
of the same parameters for a name nobody has) and by its relative work (CPU time, not wall time).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
import time
from pathlib import Path

import pyotp
import pytest
from argon2 import PasswordHasher, Type
from family_testkit import close, open_store

from sudarshan.family import accounts, lockout, sessions
from sudarshan.family.accounts import Accounts, Hasher
from sudarshan.family.errors import FamilyError, Invalid, LimitReached, VersionConflict

VECTORS = json.loads((Path(__file__).parent / "vectors" / "accounts.json").read_text())
PASSWORD = "a long synthetic passphrase"
PHONE = accounts.device_from(VECTORS["devices"][0][0], "a" * 64)
OTHER_PHONE = accounts.device_from(VECTORS["devices"][1][0], "b" * 64)
T0 = 1_763_100_000_000  # Fri 14 Nov 2026, 11:30 IST: any fixed instant works
MIN, DAY = 60_000, 86_400_000


def fast() -> PasswordHasher:
    return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=16,
                          type=Type.ID)  # fmt: skip


class Spy(Hasher):
    """Counts every verify and what it was checked against."""

    def __init__(self, argon=None):
        super().__init__(argon or fast())
        self.checked: list[str | None] = []

    def verify_now(self, stored, password):
        self.checked.append(stored)
        return super().verify_now(stored, password)


class Clock:
    def __init__(self, at: int = T0):
        self.at = at

    def __call__(self) -> int:
        return self.at

    def step(self, ms: int = 30_000) -> int:
        self.at += ms
        return self.at


class Rig:
    """A store, a clock and Accounts with recorders for its two hooks."""

    def __init__(self, folder: Path):
        self.store = open_store(folder)
        self.clock = Clock()
        self.hasher = Spy()
        self.notices: list[tuple[str, dict, dict]] = []
        self.revoked: list[str] = []
        self.acc = Accounts(
            self.store, clock=self.clock, hasher=self.hasher,
            notify=lambda kind, member, **facts: self.notices.append((kind, member, facts)),
            revoked=self.revoked.extend,
        )  # fmt: skip

    def run(self, coro):
        return asyncio.run(coro)

    def code(self, secret: str) -> str:
        return pyotp.TOTP(secret).at(self.clock() // 1000)

    def joined(self, handle: str = "member_a", role: str = "viewer", name: str = "Member A"):
        card = self.run(self.acc.member_create({"display_name": name, "role": role}))
        invite = self.run(self.acc.invite_create({"member_id": card["id"]}))
        secret = self.run(self.acc.join_info(invite["code"]))["totp_key"]
        done = self.run(self.acc.join(invite["code"], handle, PASSWORD, self.code(secret), PHONE))
        return card, secret, done

    def login(self, handle="member_a", password=PASSWORD, secret=None, code=None, phone=PHONE):
        self.clock.step()
        typed = code if code is not None else (self.code(secret) if secret else "000000")
        return self.run(self.acc.login(handle, password, typed, phone))

    def refused(self, coro) -> FamilyError:
        with pytest.raises(FamilyError) as caught:
            self.run(coro)
        return caught.value

    def rows(self, table: str, **where):
        return self.store.read_now(lambda tx: tx.rows(table, **where))


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path / "state")
    yield made
    close(made.store)
    accounts.stop_hasher()


# ---- passwords and the dummy verify (mutation target 5) ----------------------------------------------
def test_argon2id_is_pinned_and_the_dummy_has_the_same_parameters():
    real = Hasher()
    try:
        made = real.hash_now(PASSWORD)
        assert made.startswith("$argon2id$v=19$m=65536,t=3,p=1$")
        assert real.dummy().split("$")[1:4] == made.split("$")[1:4]
        assert real.verify_now(made, PASSWORD) and not real.verify_now(made, PASSWORD + "x")
        assert not real.verify_now(None, PASSWORD)
    finally:
        accounts.stop_hasher()


def test_a_name_nobody_has_costs_one_verify_of_the_dummy_hash(rig):
    rig.joined()
    rig.run(rig.acc.member_create({"display_name": "Member B", "role": "child"}))  # not joined
    for handle in ("nobody_here", "x", "member_b"):
        rig.hasher.checked.clear()
        assert rig.refused(rig.acc.login(handle, PASSWORD, "123456", PHONE)).code == "signin_failed"
        assert rig.hasher.checked == [None], handle  # one verify, of the dummy
    rig.hasher.checked.clear()
    assert rig.refused(rig.acc.login("member_a", "wrong but long enough", "0", PHONE)).code == (
        "signin_failed"
    )
    assert len(rig.hasher.checked) == 1 and rig.hasher.checked[0].startswith("$argon2id$")


def test_the_dummy_verify_does_the_same_work_as_a_real_one():
    """Relative work, not wall time: the CPU time of the verifying thread, which a loaded box does
    not inflate the way it inflates wall time. A box so loaded that even CPU time is unsteady is
    skipped and says so; the structural test above holds the dummy verify either way."""
    if os.getloadavg()[0] > 3 * (os.cpu_count() or 1):
        pytest.skip("the box is too loaded to compare CPU time; the structural test still runs")
    real = Hasher()
    stored = real.hash_now(PASSWORD)
    real.dummy()
    spent: dict[str, list[float]] = {"real": [], "dummy": []}
    for _ in range(5):
        for kind, target in (("real", stored), ("dummy", None)):
            began = time.thread_time()
            real.verify_now(target, "not the password at all")
            spent[kind].append(time.thread_time() - began)
    ratio = statistics.median(spent["dummy"]) / statistics.median(spent["real"])
    assert 0.8 <= ratio <= 1.25, spent


# ---- rules --------------------------------------------------------------------------------------------
def test_handle_rules():
    for typed, kept in VECTORS["handles"]["valid"]:
        assert accounts.handle_of(typed) == kept
    for typed in VECTORS["handles"]["invalid"]:
        with pytest.raises(Invalid) as caught:
            accounts.handle_of(typed)
        assert caught.value.field == "handle"


def test_password_length_rules():
    for n in VECTORS["passwords"]["valid_lengths"]:
        assert accounts.check_password("p" * n) == "p" * n
    for n in VECTORS["passwords"]["invalid_lengths"]:
        with pytest.raises(Invalid):
            accounts.check_password("p" * n)
    with pytest.raises(Invalid):
        accounts.check_password(12345678901234)


@pytest.mark.parametrize(("ua", "label"), VECTORS["devices"])
def test_the_device_label_from_the_browser(ua, label):
    assert accounts.device_from(ua).label == label


def test_lock_ends_read_in_india():
    assert accounts.clock_words(T0 + 15 * MIN, T0) == "11:45"
    assert accounts.clock_words(T0 + DAY, T0) == "11:30 on 15 Nov"


# ---- codes (mutation target 4) ----------------------------------------------------------------------
@pytest.mark.parametrize(("at", "code"), VECTORS["totp"]["cases"])
def test_totp_rfc_6238_vectors(at, code):
    secret = VECTORS["totp"]["secret"]
    assert accounts.totp_step(secret, code, at * 1000, 0) == at // 30


def test_a_code_is_good_one_step_either_side_and_once():
    secret = VECTORS["totp"]["secret"]
    at = 1_234_567_890
    code = pyotp.TOTP(secret).at(at)
    step = at // 30
    assert accounts.totp_step(secret, code, (at - 30) * 1000, 0) == step
    assert accounts.totp_step(secret, code, (at + 30) * 1000, 0) == step
    assert accounts.totp_step(secret, code, (at + 60) * 1000, 0) is None
    assert accounts.totp_step(secret, code, at * 1000, step) is None  # that step was accepted
    assert accounts.totp_step(secret, code, at * 1000, step - 1) == step
    for odd in ("", "12345", "1234567", "abcdef", None, 123456):
        assert accounts.totp_step(secret, odd, at * 1000, 0) is None
    assert accounts.totp_step(secret, code[:3] + " " + code[3:], at * 1000, 0) == step


def test_a_code_signs_in_once(rig):
    _, secret, _ = rig.joined()
    rig.clock.step()
    code = rig.code(secret)
    assert rig.run(rig.acc.login("member_a", PASSWORD, code, PHONE)).session_id
    rig.clock.step(5_000)  # still the same step
    refused = rig.refused(rig.acc.login("member_a", PASSWORD, code, PHONE))
    assert refused.code == "signin_failed"
    assert rig.rows("credential")[0]["failed_count"] == 1


# ---- lockout (mutation target 6) ---------------------------------------------------------------------
@pytest.mark.parametrize(("failures", "seconds"), VECTORS["lockout"])
def test_lockout_vectors(failures, seconds):
    assert lockout.lock_seconds(failures) == seconds


@pytest.mark.parametrize(("failures", "level"), VECTORS["levels"])
def test_lockout_levels(failures, level):
    assert lockout.level(failures) == level


def test_lockout_15_minutes_then_1_hour_then_24_hours(rig):
    _, secret, _ = rig.joined()

    def wrong() -> FamilyError:
        rig.clock.step(1_000)
        return rig.refused(rig.acc.login("member_a", "a wrong passphrase!", "000000", PHONE))

    for _ in range(4):
        assert wrong().code == "signin_failed"
    fifth = wrong()
    assert fifth.code == "locked" and fifth.status == 429
    until = rig.rows("credential")[0]["locked_until"]
    assert until == rig.clock.at + 15 * MIN
    when = accounts.clock_words(until, rig.clock.at)
    assert str(fifth) == f"Too many wrong attempts. Try again at {when}."
    kind, _member, facts = rig.notices[-1]
    assert kind == "lockout" and facts["attempts"] == "5" and facts["until"] == str(until // 1000)
    assert facts["device"] == PHONE.label
    checked = len(rig.hasher.checked)
    assert wrong().code == "locked"  # during the lock: refused at once, not counted, not verified
    assert len(rig.hasher.checked) == checked and rig.rows("credential")[0]["failed_count"] == 5
    rig.clock.at = until
    for _ in range(4):
        assert wrong().code == "signin_failed"
    assert wrong().code == "locked"
    assert rig.rows("credential")[0]["locked_until"] == rig.clock.at + 60 * MIN
    rig.clock.at = rig.rows("credential")[0]["locked_until"]
    for _ in range(4):
        assert wrong().code == "signin_failed"
    assert wrong().code == "locked"
    assert rig.rows("credential")[0]["locked_until"] == rig.clock.at + DAY
    assert rig.rows("credential")[0]["lock_level"] == 3
    kinds = [r["kind"] for r in rig.rows("audit")]
    assert kinds.count("lockout") == 3 and kinds.count("login_failed") == 15
    rig.clock.at = rig.rows("credential")[0]["locked_until"]
    assert rig.login(secret=secret).session_id  # a good sign-in clears the count
    assert rig.rows("credential")[0]["failed_count"] == 0


def test_a_wrong_code_with_the_right_password_counts_and_says_the_same(rig):
    _, secret, _ = rig.joined()
    refused = rig.refused(rig.acc.login("member_a", PASSWORD, "000000", PHONE))
    assert str(refused) == "That sign-in name, password or code did not match."
    assert rig.rows("audit", kind="login_failed")[0]["detail"] == {"part": "code", "failures": 1}


# ---- invites (M2.9) -----------------------------------------------------------------------------------
def test_an_invite_lasts_two_hours_and_works_once(rig):
    card = rig.run(rig.acc.member_create({"display_name": "Member A", "role": "viewer"}))
    invite = rig.run(rig.acc.invite_create({"member_id": card["id"]}))
    assert invite["expires_at"] == rig.clock.at + 2 * 60 * MIN
    assert invite["path"] == "/family/join/" + invite["code"] and len(invite["code"]) >= 26
    row = rig.rows("invite")[0]
    assert row["code_hash"] == hashlib.sha256(invite["code"].encode()).hexdigest()
    assert invite["code"] not in json.dumps(rig.rows("invite"))
    rig.clock.at = invite["expires_at"] - 1
    info = rig.run(rig.acc.join_info(invite["code"]))
    assert info["display_name"] == "Member A" and info["otpauth_uri"].startswith("otpauth://totp/")
    assert info["rules"] == {"handle_min": 3, "handle_max": 20, "password_min": 12}
    rig.clock.at = invite["expires_at"]
    assert rig.refused(rig.acc.join_info(invite["code"])).code == "invite_gone"
    again = rig.run(rig.acc.invite_create({"member_id": card["id"]}))
    secret = rig.run(rig.acc.join_info(again["code"]))["totp_key"]
    rig.run(rig.acc.join(again["code"], "member_a", PASSWORD, rig.code(secret), PHONE))
    gone = rig.refused(rig.acc.join(again["code"], "member_z", PASSWORD, rig.code(secret), PHONE))
    assert gone.code == "invite_gone" and gone.status == 410


def test_one_unused_invite_per_member_and_a_retry_gets_the_same_link(rig):
    card = rig.run(rig.acc.member_create({"display_name": "Member A", "role": "viewer"}))
    first = rig.run(rig.acc.invite_create({"member_id": card["id"], "request_id": "r1"}))
    assert rig.run(rig.acc.invite_create({"member_id": card["id"], "request_id": "r1"})) == first
    second = rig.run(rig.acc.invite_create({"member_id": card["id"]}))
    assert rig.refused(rig.acc.join_info(first["code"])).code == "invite_gone"
    assert rig.run(rig.acc.join_info(second["code"]))["expires_at"] == second["expires_at"]
    assert [r["kind"] for r in rig.rows("audit")].count("invite_revoked") == 1
    open_rows = [r for r in rig.rows("invite") if r["used_at"] is None and r["revoked_at"] is None]
    assert len(open_rows) == 1


def test_joining_checks_the_name_the_password_and_the_code(rig):
    rig.joined("member_a")
    card = rig.run(rig.acc.member_create({"display_name": "Member B", "role": "viewer"}))
    invite = rig.run(rig.acc.invite_create({"member_id": card["id"]}))
    secret = rig.run(rig.acc.join_info(invite["code"]))["totp_key"]
    code = rig.code(secret)
    taken = rig.refused(rig.acc.join(invite["code"], "Member_A", PASSWORD, code, PHONE))
    assert (taken.code, taken.field, str(taken)) == (
        "invalid",
        "handle",
        "That sign-in name is taken. Choose another.",
    )
    assert rig.refused(rig.acc.join(invite["code"], "b", PASSWORD, code, PHONE)).field == "handle"
    short = rig.refused(rig.acc.join(invite["code"], "member_b", "short", code, PHONE))
    assert short.field == "password"
    wrong = rig.refused(rig.acc.join(invite["code"], "member_b", PASSWORD, "000000", PHONE))
    assert (wrong.code, wrong.field) == ("code_mismatch", "totp")
    done = rig.run(rig.acc.join(invite["code"], "member_b", PASSWORD, code, PHONE))  # still usable
    assert done.session.member["status"] == "active" and done.session.member["handle"] == "member_b"
    assert ("invite_used", done.session.member["id"]) in [(k, m["id"]) for k, m, _ in rig.notices]
    kinds = [r["kind"] for r in rig.rows("audit", member_id=card["id"])]
    assert kinds[-2:] == ["invite_used", "login_ok"]


def test_an_invite_for_a_member_who_joined_is_refused(rig):
    card, _, _ = rig.joined()
    refused = rig.refused(rig.acc.invite_create({"member_id": card["id"]}))
    assert (refused.code, str(refused)) == ("invalid", "This member has already joined. Use "
                                            "Reset sign-in to send a new invite.")  # fmt: skip


# ---- sessions (M2.10; mutation targets 2 and 3) ----------------------------------------------------------
def test_the_session_id_is_kept_only_as_its_hash(rig, tmp_path):
    _, _, done = rig.joined()
    row = rig.rows("viewer_session")[0]
    assert row["id_hash"] == hashlib.sha256(done.session_id.encode()).hexdigest()
    assert done.session_id not in json.dumps(rig.rows("viewer_session"))
    assert done.session_id not in json.dumps(rig.rows("audit"))
    for name in ("family.db", "family.db-wal"):
        path = tmp_path / "state" / name
        if path.exists():
            assert done.session_id.encode() not in path.read_bytes()
    assert rig.run(rig.acc.check(done.session_id)).member_id == done.session.member_id
    assert rig.refused(rig.acc.check(sessions.new_id())).code == "signed_out"
    assert rig.refused(rig.acc.check(row["id_hash"])).code == "signed_out"  # the hash is no key


def test_last_seen_moves_at_most_once_a_minute_and_the_idle_end_with_it(rig):
    _, _, done = rig.joined()
    first = rig.rows("viewer_session")[0]
    rig.clock.step(10_000)
    rig.run(rig.acc.check(done.session_id))
    assert rig.rows("viewer_session")[0]["last_seen_at"] == first["last_seen_at"]
    rig.clock.step(50_000)  # 60 s after the sign-in
    rig.run(rig.acc.check(done.session_id))
    moved = rig.rows("viewer_session")[0]
    assert moved["last_seen_at"] == rig.clock.at
    assert moved["idle_expires_at"] == rig.clock.at + 14 * DAY


def test_14_days_unused_ends_a_session_and_use_keeps_it_to_30(rig):
    _, secret, done = rig.joined()
    rig.clock.step(14 * DAY - 1)
    rig.run(rig.acc.check(done.session_id))  # used just in time: the idle end moves
    rig.clock.step(14 * DAY - 1)
    rig.run(rig.acc.check(done.session_id))
    rig.clock.step(14 * DAY)
    refused = rig.refused(rig.acc.check(done.session_id))
    assert str(refused) == "Your sign-in has expired. Sign in again." and refused.status == 401
    assert [r["kind"] for r in rig.rows("audit")].count("session_expired") == 1
    assert rig.refused(rig.acc.check(done.session_id)).code == "signed_out"
    kept = rig.login(secret=secret)
    for _ in range(29):
        rig.clock.step(DAY)
        rig.run(rig.acc.check(kept.session_id))
    rig.clock.step(DAY)
    assert str(rig.refused(rig.acc.check(kept.session_id))).startswith("Your sign-in has expired")


def test_a_fourth_device_signs_out_the_oldest(rig):
    _, secret, first = rig.joined()
    second = rig.login(secret=secret)
    third = rig.login(secret=secret)
    rig.revoked.clear()
    fourth = rig.login(secret=secret)
    assert rig.revoked == [first.session.id_hash]
    assert rig.refused(rig.acc.check(first.session_id)).code == "signed_out"
    for kept in (second, third, fourth):
        rig.run(rig.acc.check(kept.session_id))
    line = rig.rows("audit", kind="session_revoked")[0]
    assert line["detail"] == {"why": "device_limit"}
    assert line["session_hash16"] == first.session.id_hash[:16]


def test_logout_and_logout_everywhere(rig):
    _, secret, first = rig.joined()
    second = rig.login(secret=secret)
    rig.run(rig.acc.logout(first.session))
    assert rig.revoked == [first.session.id_hash]
    assert (
        str(rig.refused(rig.acc.check(first.session_id))) == "You were signed out on this device."
    )
    rig.run(rig.acc.check(second.session_id))
    third = rig.login(secret=secret)
    rig.run(rig.acc.logout(third.session, everywhere=True))
    for gone in (second, third):
        assert rig.refused(rig.acc.check(gone.session_id)).code == "signed_out"


def test_a_password_change_signs_out_the_other_devices(rig):
    _, secret, first = rig.joined()
    second = rig.login(secret=secret)
    rig.clock.step()
    wrong = rig.refused(rig.acc.change_password(first.session, "not it at all!", "another long one",
                                                rig.code(secret)))  # fmt: skip
    assert (wrong.code, wrong.field) == ("invalid", "current")
    assert rig.rows("credential")[0]["failed_count"] == 1
    rig.clock.step()
    rig.run(rig.acc.change_password(first.session, PASSWORD, "another long one", rig.code(secret)))
    assert rig.revoked[-1:] == [second.session.id_hash]
    rig.run(rig.acc.check(first.session_id))
    assert rig.refused(rig.acc.check(second.session_id)).code == "signed_out"
    old = rig.refused(rig.acc.login("member_a", PASSWORD, rig.code(secret), PHONE))
    assert old.code == "signin_failed"
    rig.clock.step()
    assert rig.run(rig.acc.login("member_a", "another long one", rig.code(secret), PHONE))


def test_devices_list_the_current_first_and_rename(rig):
    _, secret, first = rig.joined()
    second = rig.login(secret=secret, phone=OTHER_PHONE)
    listed = rig.run(rig.acc.devices(second.session))
    assert [d["current"] for d in listed] == [True, False]
    assert listed[0]["label"] == OTHER_PHONE.label and len(listed[0]["id"]) == 16
    assert listed[0]["place"] == "b" * 8
    rig.run(rig.acc.rename(second.session, first.session.device_id, "Kitchen tablet"))
    assert rig.run(rig.acc.devices(first.session))[0]["label"] == "Kitchen tablet"
    assert rig.refused(rig.acc.rename(second.session, first.session.device_id, "")).field == "label"
    rig.run(rig.acc.sign_out_device(second.session, first.session.device_id))
    assert rig.refused(rig.acc.check(first.session_id)).code == "signed_out"


def test_a_sign_in_from_a_new_phone_is_a_notice_and_a_known_one_is_not(rig):
    _, secret, _ = rig.joined()
    rig.notices.clear()
    rig.login(secret=secret)  # the phone it joined on
    assert rig.notices == []
    rig.login(secret=secret, phone=OTHER_PHONE)
    assert [(k, f) for k, _m, f in rig.notices] == [("new_device", {"device": OTHER_PHONE.label})]


# ---- the owner's member ops ---------------------------------------------------------------------------
def test_members_take_their_roles_defaults_and_y24_is_refused(rig):
    viewer = rig.run(rig.acc.member_create({"display_name": " Member  A ", "role": "viewer"}))
    child = rig.run(rig.acc.member_create({"display_name": "Member B", "role": "child"}))
    assert viewer["display_name"] == "Member A" and viewer["status"] == "invited"
    assert viewer["privacy"] == {"pnl": True, "qty": True, "funds": False, "orders": True}
    assert not any(child["privacy"].values())
    y24 = {"pnl": True, "qty": False, "funds": False, "orders": False}
    refused = rig.refused(rig.acc.member_create({"display_name": "C", "role": "viewer",
                                                 "privacy": y24}))  # fmt: skip
    assert str(refused) == "Live P&L is worked out from quantities. Show quantities to show P&L."
    assert rig.refused(rig.acc.member_create({"display_name": "", "role": "viewer"})).field == (
        "display_name"
    )
    assert rig.refused(rig.acc.member_create({"display_name": "D", "role": "owner"})).field == (
        "role"
    )


def test_at_most_four_members_and_a_retried_create_makes_one(rig):
    first = rig.run(rig.acc.member_create({"display_name": "M1", "role": "viewer",
                                           "request_id": "q1"}))  # fmt: skip
    again = rig.run(rig.acc.member_create({"display_name": "M1", "role": "viewer",
                                           "request_id": "q1"}))  # fmt: skip
    assert again["id"] == first["id"]
    for n in (2, 3, 4):
        rig.run(rig.acc.member_create({"display_name": f"M{n}", "role": "child"}))
    with pytest.raises(LimitReached):
        rig.run(rig.acc.member_create({"display_name": "M5", "role": "child"}))
    rig.run(rig.acc.member_remove({"id": first["id"], "version": 1}))
    rig.run(rig.acc.member_create({"display_name": "M5", "role": "child"}))  # room again


def test_a_privacy_change_is_audited_before_and_after_and_names_no_one(rig):
    card, _, _ = rig.joined()
    hidden = {"pnl": False, "qty": False, "funds": False, "orders": False}
    after = rig.run(rig.acc.member_update({"id": card["id"], "version": 2, "privacy": hidden,
                                           "display_name": "Member A2"}))  # fmt: skip
    assert after["privacy"] == hidden and after["version"] == 3
    line = rig.rows("audit", kind="privacy_changed")[0]
    assert line["detail"]["before"]["pnl"] is True and line["detail"]["after"] == hidden
    assert rig.rows("audit", kind="member_updated")[0]["detail"] == {"fields": ["display_name"]}
    with pytest.raises(VersionConflict):
        rig.run(rig.acc.member_update({"id": card["id"], "version": 2, "language": "gu"}))
    text = json.dumps(rig.rows("audit"))
    assert "member_a" not in text and "Member A" not in text


def test_a_paused_member_is_signed_out_and_told_after_a_right_sign_in(rig):
    card, secret, done = rig.joined()
    rig.run(rig.acc.member_status({"id": card["id"], "version": 2, "status": "suspended"}))
    assert rig.revoked == [done.session.id_hash]
    paused = rig.refused(rig.acc.check(done.session_id))
    assert str(paused) == "The account owner has paused this sign-in. Ask them to resume it."
    assert rig.refused(rig.acc.login("member_a", "a wrong passphrase", "000000", PHONE)).code == (
        "signin_failed"
    )
    rig.clock.step()
    told = rig.refused(rig.acc.login("member_a", PASSWORD, rig.code(secret), PHONE))
    assert "paused" in str(told)
    rig.run(rig.acc.member_status({"id": card["id"], "version": 3, "status": "active"}))
    assert rig.login(secret=secret).session_id


def test_removing_a_member_frees_the_name_and_tells_their_phone(rig):
    card, secret, done = rig.joined()
    rig.run(rig.acc.member_remove({"id": card["id"], "version": 2}))
    assert rig.revoked == [done.session.id_hash]
    assert str(rig.refused(rig.acc.check(done.session_id))) == (
        "This member was removed by the account owner."
    )
    assert rig.rows("credential") == [] and rig.rows("member")[0]["handle"] is None
    kind, member, _ = rig.notices[-1]
    assert kind == "member_removed" and member["handle"] == "member_a"
    assert rig.refused(rig.acc.login("member_a", PASSWORD, rig.code(secret), PHONE)).code == (
        "signin_failed"
    )
    assert rig.run(rig.acc.member_list()) == []
    rig.joined("member_a", name="Member C")  # the name can be chosen again


def test_a_sign_in_reset_revokes_everything_and_the_new_invite_works(rig):
    card, secret, done = rig.joined()
    invite = rig.run(rig.acc.signin_reset({"member_id": card["id"], "version": 2,
                                           "request_id": "s1"}))  # fmt: skip
    assert rig.run(rig.acc.signin_reset({"member_id": card["id"], "version": 2,
                                         "request_id": "s1"})) == invite  # fmt: skip
    assert rig.revoked == [done.session.id_hash] and rig.rows("credential") == []
    assert rig.refused(rig.acc.login("member_a", PASSWORD, rig.code(secret), PHONE)).code == (
        "signin_failed"
    )
    fresh = rig.run(rig.acc.join_info(invite["code"]))["totp_key"]
    assert fresh != secret
    rig.run(rig.acc.join(invite["code"], "member_a", "a new long passphrase", rig.code(fresh),
                         PHONE))  # fmt: skip
    cards = rig.run(rig.acc.member_list())
    assert cards[0]["status"] == "active" and cards[0]["devices"] == 1


def test_the_owner_lists_and_signs_out_a_members_devices(rig):
    card, secret, first = rig.joined()
    second = rig.login(secret=secret, phone=OTHER_PHONE)
    listed = rig.run(rig.acc.device_list({"member_id": card["id"]}))
    assert {d["id"] for d in listed} == {first.session.device_id, second.session.device_id}
    rig.run(rig.acc.device_revoke({"member_id": card["id"], "id": first.session.device_id}))
    assert rig.revoked == [first.session.id_hash]
    assert rig.refused(rig.acc.check(first.session_id)).code == "signed_out"
    assert rig.refused(rig.acc.device_revoke({"member_id": card["id"], "id": "f" * 16})).code == (
        "not_found"
    )
    shown = rig.run(rig.acc.member_list())[0]
    assert shown["devices"] == 1 and shown["last_seen_at"] == rig.clock.at
    assert shown["locked_until"] is None and shown["telegram_linked"] is False
