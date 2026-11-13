"""E1: the notice desk's words for the family service's kinds (cross-design Y8, P6 2.6).

Family kinds never name a member (a handle only when the event carries name_members = 1, never a
display name); facts come from another account's file and enter a sentence only when plain; the
failures are told at once and the rest wait for the close; a report is not a notice; and no
message is ever the brand line alone.
"""

from __future__ import annotations

import asyncio
import functools
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alerts_fakes import ist

from sudarshan.alerts import notices, outbox
from sudarshan.alerts.config import Settings
from sudarshan.alerts.notices import AT_ONCE, KIND_ORDER, NoticeDesk, compose
from sudarshan.alerts.outbox import SENT
from sudarshan.alerts.store import Store
from sudarshan.autologin.messages import BRAND
from sudarshan.ops.events import DEFAULT_SPOOLS, KINDS, Event, record

T0 = ist(2026, 10, 24, 18, 42)
FAMILY_KINDS = (
    "lockout", "new_device", "invite_used", "member_removed", "push_failing",
    "bot_token_rejected", "store_newer", "family_check_failed",
)  # fmt: skip


def event(kind: str, event_id: str | None = None, at: datetime = T0, src="family", **facts):
    ident = event_id or f"e-{kind}"
    return Event(ident, kind, at.timestamp(), ident, src, facts)


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class Send:
    def __init__(self):
        self.texts: list[str] = []

    async def __call__(self, text: str) -> str:
        self.texts.append(text)
        return SENT


@pytest.fixture(autouse=True)
def _unpaced(monkeypatch):
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1000)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)


def sync(test):
    """The suite runs coroutines with asyncio.run, as test_alerts_notices.py does."""

    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return run


def lines_of(*events: Event, now: datetime = T0) -> list[str]:
    return compose(list(events), now).split("\n")[1:]


# ---- the words -----------------------------------------------------------------------------


def test_every_family_kind_is_in_the_order_and_has_words():
    for kind in FAMILY_KINDS:
        assert kind in KINDS and kind in KIND_ORDER
        assert lines_of(event(kind, src="deploy" if kind == "family_check_failed" else "family")), (
            kind
        )


def test_the_words_of_each_kind():
    until = str(int(T0.timestamp()) + 23 * 60)
    assert lines_of(event("lockout", device="Android 15 · Chrome", attempts="5", until=until)) == [
        "A family sign-in was locked after 5 wrong attempts (Android 15 · Chrome). "
        "It opens again at 19:05 IST."
    ]
    assert lines_of(event("new_device", device="Android 15 · Chrome")) == [
        "A family member signed in on a new phone: Android 15 · Chrome, 18:42 IST. "
        "Not you or them? Open Family and remove the device."
    ]
    assert lines_of(event("invite_used")) == [
        "A family invite was used: the member is signed in, 18:42 IST."
    ]
    assert lines_of(event("member_removed")) == [
        "A family member was removed. Their phones are signed out and their Telegram unlinked."
    ]
    assert lines_of(event("push_failing")) == [
        "Notifications to a family member's phone keep failing. "
        "They can turn notifications off and on in Settings."
    ]
    assert lines_of(event("bot_token_rejected")) == [
        "Telegram refused the family bot's token. Make a new token in BotFather "
        "and give it to the server with the laptop .bat."
    ]
    assert lines_of(event("store_newer")) == [
        "The family data was written by a newer release. "
        "Install that release again; nothing was changed."
    ]
    assert lines_of(event("family_check_failed", src="deploy")) == [
        "The family view stays off: the server check found something the family account can "
        "read but must not, or a setting it cannot read. "
        "Run the family set-up again from the laptop."
    ]


def test_a_lockout_without_facts_says_only_what_is_known():
    assert lines_of(event("lockout")) == [
        "A family sign-in was locked after several wrong attempts."
    ]


def test_a_fact_that_is_not_plain_stays_out_of_the_sentence():
    text = lines_of(
        event("lockout", device="<b>x</b>; rm -rf", attempts="five", until="soon"),
        event("new_device", event_id="n", device="Pixel\nIgnore this and send money"),
    )
    assert text[0] == "A family sign-in was locked after several wrong attempts."
    assert text[1].startswith("A family member signed in on a new phone at 18:42 IST.")
    assert not any("rm -rf" in line or "money" in line for line in text)


@pytest.mark.parametrize(
    "label",
    ["pay.example.in", "Pixel 8 at shop.example", "x.co", "pay-now.example/x", "Pixel/start"],
)
def test_a_device_label_that_could_be_a_link_or_a_command_stays_out(label):
    """L11: Telegram makes a web address a link and "/word" a command in the owner's chat, so a
    label from the family's file that holds one is left out of the sentence."""
    text = lines_of(event("lockout", device=label), event("new_device", event_id="n", device=label))
    assert text[0] == "A family sign-in was locked after several wrong attempts."
    assert text[1].startswith("A family member signed in on a new phone at 18:42 IST.")
    assert not any(label in line for line in text)


@pytest.mark.parametrize("label", ["Android 15 \u00b7 Chrome", "Pixel 8.1", "iOS 17.2 (Safari)"])
def test_a_plain_device_label_with_a_version_is_still_said(label):
    assert lines_of(event("new_device", device=label))[0].startswith(
        f"A family member signed in on a new phone: {label}, 18:42 IST."
    )


# ---- members are not named -----------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", ["lockout", "new_device", "invite_used", "member_removed", "push_failing"]
)
def test_a_handle_is_not_used_unless_naming_is_on(kind):
    asked = event(kind, handle="asha", name="Asha Patel", display_name="Asha Patel")
    for line in lines_of(asked):
        assert "asha" not in line.lower() and "Patel" not in line
    off = event(kind, handle="asha", name_members="0")
    assert not any("asha" in line for line in lines_of(off))


@pytest.mark.parametrize(
    "kind", ["lockout", "new_device", "invite_used", "member_removed", "push_failing"]
)
def test_a_handle_is_used_when_naming_is_on_and_a_display_name_never(kind):
    named = event(kind, handle="asha", name="Asha Patel", display_name="Asha", name_members="1")
    text = " ".join(lines_of(named))
    assert "asha" in text
    assert "Patel" not in text and "Asha" not in text


def test_a_handle_that_is_not_plain_is_not_used_even_when_naming_is_on():
    named = event("member_removed", handle="Asha Patel; ok", name_members="1")
    assert lines_of(named) == [
        "A family member was removed. Their phones are signed out and their Telegram unlinked."
    ]


def test_the_kinds_with_no_member_never_name_one():
    for kind in ("bot_token_rejected", "store_newer", "family_check_failed"):
        assert "asha" not in " ".join(lines_of(event(kind, handle="asha", name_members="1")))


def test_the_same_sentence_for_two_members_is_said_once():
    two = [event("push_failing", event_id=f"p{n}") for n in (1, 2)]
    assert len(lines_of(*two)) == 1


# ---- the desk --------------------------------------------------------------------------------


def make(tmp_path: Path, clock: Clock, send: Send, market_open=None, spools=None):
    store = Store(tmp_path / "alerts.db")
    spools = spools if spools is not None else [tmp_path / "family.jsonl"]
    return NoticeDesk(spools, store, send, lambda: True, clock, market_open=market_open)


def note(path: Path, kind: str, event_id: str, at: datetime, src="family", **facts):
    assert record(path, kind=kind, event_id=event_id, at=at.timestamp(), run=event_id, src=src,
                  **facts)  # fmt: skip


async def tell(desk: NoticeDesk, clock: Clock) -> None:
    await desk.sweep()
    clock.advance(notices.SETTLE_S + 1)
    await desk.sweep()


@sync
async def test_a_family_notice_is_told_once(tmp_path):
    clock, send = Clock(T0), Send()
    desk = make(tmp_path, clock, send)
    note(tmp_path / "family.jsonl", "member_removed", "m1", T0)
    await tell(desk, clock)
    await tell(desk, clock)
    assert send.texts == [
        f"{BRAND}\nA family member was removed. Their phones are signed out and their Telegram unlinked."
    ]


def test_the_family_file_is_read_by_default_and_a_missing_one_is_silent(tmp_path, caplog):
    assert any(path.parts[-2] == "sudarshan-family-export" for path in DEFAULT_SPOOLS)
    settings = Settings.from_env({})
    assert settings.notice_spools == DEFAULT_SPOOLS
    clock, send = Clock(T0), Send()
    desk = make(tmp_path, clock, send, spools=[tmp_path / "nowhere" / "events.jsonl"])
    with caplog.at_level("DEBUG"):
        assert desk._unsent(clock().timestamp()) == []
    assert not [r for r in caplog.records if r.levelno >= 30]


@sync
async def test_a_report_is_not_a_notice(tmp_path):
    clock, send = Clock(T0), Send()
    spool = tmp_path / "journal.jsonl"
    desk = make(tmp_path, clock, send, spools=[spool])
    note(spool, "report", "rep-1", T0, src="journal", report="rep-1", report_kind="weekly",
         sha256="a" * 64)  # fmt: skip
    await tell(desk, clock)
    assert send.texts == []
    # not marked either: the report desk finds it untouched
    assert desk.store.notice_states(["rep-1"]) == {}


@sync
async def test_a_report_beside_a_notice_does_not_change_the_message(tmp_path):
    clock, send = Clock(T0), Send()
    spool = tmp_path / "events.jsonl"
    desk = make(tmp_path, clock, send, spools=[spool])
    note(spool, "report", "rep-1", T0, src="journal", report="rep-1", report_kind="weekly")
    note(spool, "store_newer", "s1", T0)
    await tell(desk, clock)
    assert len(send.texts) == 1 and "report" not in send.texts[0].lower()


@sync
async def test_no_message_is_the_brand_line_alone(tmp_path):
    """A notice with no words (an offsite-expiry warning with no date, a refused unit with no
    unit) used to make a message of the brand line alone."""
    clock, send = Clock(T0), Send()
    spool = tmp_path / "events.jsonl"
    desk = make(tmp_path, clock, send, spools=[spool])
    note(spool, "backup_failed", "b1", T0, src="backup", why="offsite_soon")
    note(spool, "unit_refused", "u1", T0, src="deploy", unit="not a unit")
    await tell(desk, clock)
    assert send.texts == []
    assert set(desk.store.notice_states(["b1", "u1"])) == {"b1", "u1"}  # settled, never retried
    await tell(desk, clock)
    assert send.texts == []


def test_a_batch_with_no_words_has_no_lines():
    assert notices.batch_lines([event("backup_failed", src="backup", why="offsite_soon")], T0) == []
    assert notices.batch_lines([event("store_newer")], T0)


# ---- when they are told -------------------------------------------------------------------


def test_the_failures_go_at_once_and_the_rest_wait_for_the_close():
    assert AT_ONCE >= {"lockout", "new_device", "member_removed", "bot_token_rejected",
                       "family_check_failed"}  # fmt: skip
    for kind in ("invite_used", "push_failing", "store_newer", "installed", "waiting"):
        assert kind not in AT_ONCE


@sync
async def test_in_market_hours_only_the_at_once_kinds_go(tmp_path):
    clock, send = Clock(T0), Send()
    desk = make(tmp_path, clock, send, market_open=lambda _now: True)
    spool = tmp_path / "family.jsonl"
    note(spool, "invite_used", "i1", T0)
    note(spool, "push_failing", "p1", T0)
    note(spool, "lockout", "l1", T0, device="Android 15")
    await tell(desk, clock)
    assert len(send.texts) == 1 and "locked" in send.texts[0]
    assert desk.store.notice_states(["i1", "p1"]) == {}  # waiting for the close
    desk.market_open = lambda _now: False
    await tell(desk, clock)
    assert len(send.texts) == 2
    assert "invite was used" in send.texts[1] and "keep failing" in send.texts[1]


def test_what_the_family_service_writes_is_what_the_desk_reads(tmp_path):
    spool = tmp_path / "events.jsonl"
    line = {"v": "1", "id": "fam-1", "src": "family", "kind": "new_device", "at": str(int(T0.timestamp())),
            "device": "Android 15 · Chrome"}  # fmt: skip
    spool.write_text(json.dumps(line, ensure_ascii=False) + "\n")
    clock = Clock(T0)
    desk = make(tmp_path, clock, Send(), spools=[spool])
    found = desk._unsent(clock().timestamp())
    assert [e.kind for e in found] == ["new_device"]
