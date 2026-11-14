"""Review M2: the rule ``lockout.py`` writes down, pinned.

A stranger who knows a member's sign-in name can hold back sign-ins from phones the member has not
used, never from the member's own phone; a back-off grows from 1 minute to at most 15; a name nobody
has is answered, timed and counted exactly as a member's; one network area that fails too often is
held back; the phone token dies with a password change or a reset. Run with a fast argon2 (the
production parameters are pinned in test_family_accounts.py).
"""

from __future__ import annotations

import asyncio

import pytest
from test_family_accounts import DAY, MIN, PASSWORD, Rig

from sudarshan.family import accounts, lockout
from sudarshan.family.contract import AREA_LIMIT, LOCKOUT
from sudarshan.family.errors import FamilyError

ANDROID = "Mozilla/5.0 (Linux; Android 15; K) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile"
STRANGER = accounts.device_from("Mozilla/5.0 (Linux; Android 14; X) Chrome/130.0.0.0 Mobile",
                                "c" * 64, area="area-of-the-stranger")  # fmt: skip
WRONG = "not the password at all"


@pytest.fixture
def rig(tmp_path):
    from family_testkit import close

    made = Rig(tmp_path / "state")
    yield made
    close(made.store)
    accounts.stop_hasher()


def own_phone(done, area: str = "area-of-home") -> accounts.Device:
    """The phone a member joined or signed in on: it sends back the phone cookie it was given."""
    return accounts.device_from(ANDROID, "a" * 64, area=area, phone=done.phone)


def attempt(rig: Rig, handle: str, password: str, device, code: str = "000000"):
    """One sign-in: the outcome, or the refusal as (code, status, sentence)."""
    try:
        return rig.run(rig.acc.login(handle, password, code, device))
    except FamilyError as exc:
        return (exc.code, exc.status, str(exc))


# ---- counted per name, and per phone -------------------------------------------------------------
def test_a_stranger_holds_back_new_phones_never_the_members_own(rig):
    """The review's probe P3, the other way round: five wrong sign-ins from a stranger lock the
    name for phones the member has not used; the member's own phone signs in all the same."""
    _, secret, joined = rig.joined()
    assert joined.phone and lockout.tokens_of(joined.phone)
    base = rig.clock.at
    for n in range(LOCKOUT["after"]):
        rig.clock.step(1_000)
        found = attempt(rig, "member_a", WRONG, STRANGER)
        assert found[0] == ("locked" if n == LOCKOUT["after"] - 1 else "signin_failed")
    rig.clock.step(1_000)
    new_phone = accounts.device_from(ANDROID, "d" * 64, area="area-of-home")
    assert attempt(rig, "member_a", PASSWORD, new_phone, rig.code(secret))[0] == "locked"
    rig.clock.step(1_000)
    mine = own_phone(joined)
    assert attempt(rig, "member_a", WRONG, mine)[0] == "signin_failed"  # its own count, not 429
    rig.clock.at = base + 31_000  # the next code step; the 1-minute lock still holds
    done = attempt(rig, "member_a", PASSWORD, mine, rig.code(secret))
    assert done.session_id and done.phone != joined.phone  # a fresh token for the next time
    assert rig.run(rig.acc.member_list())[0]["locked_until"] is not None  # still, for strangers
    line = rig.rows("audit", kind="login_ok")[-1]["detail"]
    assert line["known_phone"] is True


def test_the_members_own_phone_has_its_own_back_off(rig):
    _, secret, joined = rig.joined()
    mine = own_phone(joined)
    found = [attempt(rig, "member_a", WRONG, mine)[0] for _ in range(LOCKOUT["after"])]
    assert found == ["signin_failed"] * 4 + ["locked"]
    rig.clock.step()
    assert attempt(rig, "member_a", PASSWORD, STRANGER, rig.code(secret)).session_id  # elsewhere


# ---- the same for a name nobody has ----------------------------------------------------------------
def test_a_name_nobody_has_is_answered_timed_and_counted_as_a_members(rig):
    rig.joined()
    steps: dict[str, list[str]] = {"member_a": [], "nobody_xyz": []}
    store = rig.store
    read, write = store.read, store.write

    def counted(label, fn):
        async def call(work):
            steps[label].append(fn.__name__)
            return await fn(work)

        return call

    answers: dict[str, list[tuple]] = {"member_a": [], "nobody_xyz": []}
    verifies: dict[str, int] = {"member_a": 0, "nobody_xyz": 0}
    for _ in range(LOCKOUT["after"] + 2):
        rig.clock.step(1_000)
        for name in answers:  # the same instant for both: the same lock end in the sentence
            store.read, store.write = counted(name, read), counted(name, write)
            before = len(rig.hasher.checked)
            answers[name].append(attempt(rig, name, WRONG, STRANGER))
            verifies[name] += len(rig.hasher.checked) - before
    store.read, store.write = read, write
    assert answers["member_a"] == answers["nobody_xyz"]
    assert [a[1] for a in answers["member_a"]] == [401] * 4 + [429] * 3
    assert verifies["member_a"] == verifies["nobody_xyz"] == LOCKOUT["after"]  # none while locked
    assert steps["member_a"] == steps["nobody_xyz"]  # one read and one write per tried password
    counts = {(r["failures"], r["locked_until"]) for r in rig.rows("signin_lock")}
    assert len(rig.rows("signin_lock")) == 2 and len(counts) == 1  # the same count, twice


def test_names_that_break_the_rule_share_one_count(rig):
    for typed in ("x", "a-b", "  ", "mémber", "y"):
        rig.clock.step(1_000)
        found = attempt(rig, typed, WRONG, STRANGER)
    assert found[0] == "locked"  # the fifth of them, whichever names
    assert attempt(rig, "a_valid_name", WRONG, STRANGER)[0] == "signin_failed"
    assert len(rig.rows("signin_lock")) == 2


# ---- forgetting and clearing -----------------------------------------------------------------------
def test_a_count_is_forgotten_a_day_after_its_last_failure_and_its_lock(rig):
    for _ in range(LOCKOUT["after"]):
        rig.clock.step(1_000)
        attempt(rig, "nobody_xyz", WRONG, STRANGER)
    last = rig.clock.at
    assert rig.store.write_now(lambda tx: lockout.prune(tx, last + DAY - 1)) == 0
    assert rig.store.write_now(lambda tx: lockout.prune(tx, last + MIN + DAY + 1)) == 1
    assert rig.rows("signin_lock") == []


def test_the_owner_sees_the_lock_and_a_reset_or_a_removal_clears_the_counts(rig):
    card, _, joined = rig.joined()
    for _ in range(LOCKOUT["after"]):
        rig.clock.step(1_000)
        attempt(rig, "member_a", WRONG, STRANGER)
    attempt(rig, "member_a", WRONG, own_phone(joined))
    until = [r["locked_until"] for r in rig.rows("signin_lock") if r["failures"] == 5][0]
    assert rig.run(rig.acc.member_list())[0]["locked_until"] == until
    assert {r["member_id"] for r in rig.rows("signin_lock")} == {card["id"]}
    rig.run(rig.acc.signin_reset({"member_id": card["id"], "version": 2}))
    assert rig.rows("signin_lock") == []
    other, _, _ = rig.joined("member_b", name="Member B")
    for _ in range(3):
        attempt(rig, "member_b", WRONG, STRANGER)
    rig.run(rig.acc.member_remove({"id": other["id"], "version": 2}))
    assert rig.rows("signin_lock") == []


# ---- the network area ------------------------------------------------------------------------------
def test_one_area_failing_too_often_is_held_back_and_other_areas_are_not(rig):
    _, secret, joined = rig.joined()
    limit = AREA_LIMIT["failures"]
    began = rig.clock.at
    for n in range(limit):  # a name each: no name's own count locks
        rig.clock.step(1_000)
        assert attempt(rig, f"nobody_{n:02d}", WRONG, STRANGER)[0] == "signin_failed"
    checked = len(rig.hasher.checked)
    rig.clock.step()
    held = attempt(rig, "member_a", PASSWORD, STRANGER, rig.code(secret))
    assert held[0] == "locked" and len(rig.hasher.checked) == checked  # no password tried
    elsewhere = accounts.device_from(ANDROID, "e" * 64, area="another-area")
    assert attempt(rig, "member_a", PASSWORD, elsewhere, rig.code(secret)).session_id
    rig.clock.step()
    home_of_stranger = own_phone(joined, area="area-of-the-stranger")
    assert attempt(rig, "member_a", PASSWORD, home_of_stranger, rig.code(secret)).session_id
    rig.clock.at = began + 1_000 + AREA_LIMIT["window_s"] * 1000  # the oldest is 10 minutes old
    assert attempt(rig, "nobody_99", WRONG, STRANGER)[0] == "signin_failed"


@pytest.mark.parametrize(
    ("address", "area"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:bbbb:cccc:dddd:2", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("fe80::1%eth0", "fe80::/64"),
        ("not an address", "not an address"),
    ],
)
def test_an_area_is_an_ipv4_address_or_an_ipv6_64(address, area):
    assert lockout.area_of(address) == area


def test_the_areas_kept_in_memory_are_bounded():
    areas = lockout.Areas(failures=2, window_ms=1_000, size=3)
    for n in range(10):
        areas.failed(f"a{n}", 100)
    assert len(areas) == 3
    areas.failed("b", 100)
    areas.failed("b", 200)
    assert areas.held("b", 300) == 1_100 and areas.held("b", 1_100) is None
    assert areas.held(None, 300) is None


# ---- the phone token -------------------------------------------------------------------------------
def test_a_password_change_or_a_reset_makes_the_old_tokens_worthless(rig):
    card, secret, joined = rig.joined()
    base = rig.clock.at
    for _ in range(LOCKOUT["after"]):  # strangers lock the name until base + 65 s
        rig.clock.step(1_000)
        attempt(rig, "member_a", WRONG, STRANGER)
    rig.clock.at = base + 31_000
    changed = rig.run(rig.acc.change_password(joined.session, PASSWORD, "a newer passphrase",
                                              rig.code(secret), own_phone(joined)))  # fmt: skip
    assert changed.phone and changed.phone != joined.phone
    rig.clock.at = base + 32_000  # the old token is no longer the member's phone: held back
    assert attempt(rig, "member_a", "a newer passphrase", own_phone(joined),
                   rig.code(secret))[0] == "locked"  # fmt: skip
    rig.clock.at = base + 61_000  # the next code step, the lock still on
    fresh = accounts.device_from(ANDROID, "a" * 64, phone=changed.phone)
    assert attempt(rig, "member_a", "a newer passphrase", fresh, rig.code(secret)).session_id
    rig.run(rig.acc.signin_reset({"member_id": card["id"], "version": 2}))
    keys = rig.store.secrets
    assert lockout.known_phone(keys, changed.phone, card["id"], 0, rig.clock.at) is None


def test_a_token_that_is_another_members_altered_or_old_counts_as_none(rig):
    card, _, joined = rig.joined()
    keys, now = rig.store.secrets, rig.clock.at
    changed_at = rig.rows("credential")[0]["password_changed_at"]
    assert lockout.known_phone(keys, joined.phone, card["id"], changed_at, now)
    assert lockout.known_phone(keys, joined.phone, card["id"] + 1, changed_at, now) is None
    token = lockout.tokens_of(joined.phone)[0]
    altered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert lockout.known_phone(keys, altered, card["id"], changed_at, now) is None
    later = now + lockout.PHONE_MS + 1_000
    assert lockout.known_phone(keys, joined.phone, card["id"], changed_at, later) is None
    for odd in ("", "x" * 700, "1.2.3", None):
        assert lockout.known_phone(keys, odd, card["id"], changed_at, now) is None


def test_a_shared_phone_keeps_a_token_for_each_member_four_at_most(rig):
    keys = rig.store.secrets
    cookie = None
    for member_id in range(1, 7):
        cookie = lockout.with_token(cookie, lockout.phone_token(keys, member_id, 5, rig.clock.at))
    kept = [int(t.split(".")[0]) for t in lockout.tokens_of(cookie)]
    assert kept == [6, 5, 4, 3]
    again = lockout.with_token(cookie, lockout.phone_token(keys, 4, 5, rig.clock.at))
    assert [int(t.split(".")[0]) for t in lockout.tokens_of(again)] == [4, 6, 5, 3]
    assert lockout.known_phone(keys, again, 3, 5, rig.clock.at)


def test_a_right_sign_in_after_the_count_locked_meanwhile_is_refused(rig):
    """Decided once, in the transaction: a right password whose check finished after another
    attempt locked the count does not sign in. The store thread and the password thread each take
    work in turn, so the wrong attempt, sent first, is settled first."""
    _, secret, _ = rig.joined()
    for _ in range(LOCKOUT["after"] - 1):
        rig.clock.step(1_000)
        attempt(rig, "member_a", WRONG, STRANGER)
    rig.clock.step()
    code = rig.code(secret)

    async def both():
        wrong = asyncio.create_task(rig.acc.login("member_a", WRONG, "000000", STRANGER))
        right = asyncio.create_task(rig.acc.login("member_a", PASSWORD, code, STRANGER))
        return await asyncio.gather(wrong, right, return_exceptions=True)

    wrong, right = rig.run(both())
    assert (wrong.code, right.code) == ("locked", "locked")
    assert len(rig.rows("viewer_session")) == 1  # the join's session only
