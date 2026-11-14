"""The report desk in sudarshan-alerts (M15.7, JC3): the journal's reports on Telegram, once, on the
reports lane, text then file, and the outcome told back to the journal in the owner's words.

Most cases drive ``ReportDesk.sweep`` against in-memory stand-ins for the journal, OpenAlgo's text
path and its file route, with the real alerts.db store and the real Pacer. The last ones run the
real links over HTTP against the fakes in reports_fakes.py. Every key, name and figure is a fake.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import logging
import os
import re
import time
import types
from datetime import datetime

import httpx
import pytest
from alerts_fakes import API_KEY as OWNER_KEY
from alerts_fakes import FakeProxy, wait_until
from reports_fakes import (
    API_KEY,
    DAY_TEXT,
    OWNER,
    PDF,
    FakeJournalApi,
    FakeOpenAlgo,
    report_record,
    sha,
)

from sudarshan.alerts import outbox, reports_desk
from sudarshan.alerts.health import health_document
from sudarshan.alerts.notices import LanePace, NoticeDesk
from sudarshan.alerts.outbox import DROP, DROP_REASONS, RETRY, SENT, UNCONFIRMED
from sudarshan.alerts.owner import Owner
from sudarshan.alerts.reports_desk import (
    JOURNAL_DOWN,
    JOURNAL_NOT_RUNNING,
    JOURNAL_OFF,
    JOURNAL_PORT_HELD,
    JOURNAL_SILENT,
    JOURNAL_SWITCHED_OFF,
    LINK_LINE,
    NO_SEAL,
    ON,
    OUTCOMES,
    SWITCHED_OFF_HERE,
    JournalLink,
    JournalWatch,
    OpenAlgoFiles,
    ReportDesk,
    off_reason,
    report_desk_for,
)
from sudarshan.alerts.service import AlertService
from sudarshan.alerts.store import Store
from sudarshan.autologin.schedule import IST
from sudarshan.ops.events import record
from sudarshan.tests.fdwatch import FdWatch
from sudarshan.vault import JOURNAL_API_INFO, JOURNAL_DELIVERY_INFO, fernet_for

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 25
NAME = "SUDARSHAN_Day-report_20261109.pdf"


def sync(test):
    """Runs an async test in a loop of its own, as the other alerts tests do (no async plugin)."""

    @functools.wraps(test)
    def wrapper(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return wrapper


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(reports_desk, "RETRY_S", (0.05,) * 5)
    monkeypatch.setattr(reports_desk, "POLL_S", 0.05)
    monkeypatch.setattr(reports_desk, "GIVE_WAY_S", 0.02)
    monkeypatch.setattr(reports_desk, "SLOT_POLL_S", 0.01)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.01)


def write_report(spool, report_id: int, *, data: bytes | None = PDF, at: float | None = None,
                 event_id: str | None = None, kind: str = "eod", **facts) -> str:  # fmt: skip
    """One line of the journal's spool, as the journal writes it: ids and a sha256, no text."""
    event_id = event_id or f"journal-report-{report_id}"
    fields = {"report": str(report_id), "report_kind": kind, **facts}
    if data is not None:
        fields.setdefault("sha256", sha(data))
    assert record(spool, kind="report", event_id=event_id, at=at or time.time(), run=event_id,
                  src="journal", **fields)  # fmt: skip
    return event_id


class Journal:
    """The journal's three delivery ops, in memory: ``call(op, args)`` -> (kind, data)."""

    def __init__(self):
        self.reports: dict[int, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.next: dict[str, list[tuple[str, object]]] = {}  # op -> answers used first

    def add(self, report_id: int, text: str = DAY_TEXT, data: bytes | None = PDF,
            name: str = NAME) -> None:  # fmt: skip
        self.reports[report_id] = report_record(report_id, text, data, name)

    @property
    def deliveries(self) -> list[dict]:
        return [args for op, args in self.calls if op == "delivery"]

    async def call(self, op: str, args: dict):
        self.calls.append((op, dict(args)))
        if self.next.get(op):
            return self.next[op].pop(0)
        if op == "delivery":
            return "ok", {"id": args["report_id"], "version": 2}
        found = self.reports.get(args["id"])
        if found is None:
            return "gone", None
        if op == "report_get":
            return "ok", {"id": found["id"], "kind": "eod", "text": found["text"]}
        data = found.get("file_bytes")
        return "ok", {
            "id": found["id"], "file_name": found.get("file_name"),
            "file_mime": found.get("file_mime"),
            "file_sha256": sha(data) if data else None,
            "file_b64": base64.b64encode(data).decode() if data else None,
        }  # fmt: skip


class Text:
    """OpenAlgoTelegram.send_with_reason: every text kept, answered from ``answers`` or SENT."""

    def __init__(self, log: list):
        self.texts: list[str] = []
        self.answers: list[tuple[str, str]] = []
        self.log = log

    async def __call__(self, text: str):
        self.texts.append(text)
        self.log.append("text")
        return self.answers.pop(0) if self.answers else (SENT, "")


class Files:
    """OpenAlgoFiles: every file kept, answered from ``answers`` or "sent"."""

    def __init__(self, log: list):
        self.sent: list[tuple[str, bytes, str, str]] = []
        self.answers: list[str] = []
        self.log = log

    async def send(self, name, data, mime, caption):
        self.sent.append((name, data, mime, caption))
        self.log.append("file")
        return self.answers.pop(0) if self.answers else "sent"


class Watch:
    """JournalWatch.ask in memory: the answers in turn, the last one for good; ``asks`` counts."""

    def __init__(self, *answers: tuple[str, str | None]):
        self.answers = list(answers) or [(ON, None)]
        self.asks = 0

    async def ask(self):
        self.asks += 1
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class Rig:
    """A desk with its stand-ins, its spool and its store, and the order of what it sent."""

    def __init__(self, tmp_path, *, pacer=None, store=None, watch=None):
        self.spool = tmp_path / "journal-events.jsonl"
        self.store = store or Store(tmp_path / "alerts.db")
        self.order: list[str] = []
        self.journal, self.text, self.files = Journal(), Text(self.order), Files(self.order)
        self.pacer = pacer or outbox.Pacer()
        self.owner_ok = True
        self.desk = ReportDesk(
            self.spool, self.store, self.journal, self.text, self.files,
            lambda: self.owner_ok, lambda: datetime.now(IST), self.pacer, watch=watch,
        )  # fmt: skip

    def lane_starts(self) -> int:
        return sum(1 for _, lane in self.pacer._starts if lane == outbox.REPORTS)

    async def sweeps(self, count: int = 1) -> None:
        for _ in range(count):
            await self.desk.sweep()


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    made.store.close()


def delivered(rig, status: str, code: str | None = None, report_id: int = 7) -> dict:
    return {
        "report_id": report_id, "channel": "telegram", "status": status,
        "reason": OUTCOMES[code][1] if code else None,
    }  # fmt: skip


# -- the happy path ----------------------------------------------------------------------------


@sync
async def test_a_report_goes_text_then_file_and_the_journal_is_told(rig):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    await rig.sweeps()
    assert rig.order == ["text", "file"]
    assert rig.text.texts == [DAY_TEXT]
    assert rig.files.sent == [(NAME, PDF, "application/pdf", "सुदर्शन / SUDARSHAN: Day report")]
    assert [op for op, _ in rig.journal.calls] == [
        "report_get", "report_file", "delivery",
    ]  # fmt: skip
    assert rig.journal.calls[0] == ("report_get", {"id": 7})
    assert rig.journal.deliveries == [delivered(rig, "sent")]
    assert rig.lane_starts() == 2  # one slot for the text and one for the file


@sync
async def test_a_report_without_a_file_is_one_message(rig):
    rig.journal.add(8, text="सुदर्शन · SUDARSHAN\nMon 9 Nov · Morning brief", data=None)
    write_report(rig.spool, 8, data=None, kind="brief")
    await rig.sweeps()
    assert rig.order == ["text"] and LINK_LINE not in rig.text.texts[0]
    assert [op for op, _ in rig.journal.calls] == ["report_get", "delivery"]
    assert rig.journal.deliveries == [delivered(rig, "sent", report_id=8)]
    assert rig.lane_starts() == 1


@sync
async def test_lines_that_are_not_the_journals_reports_are_left_alone(rig):
    record(rig.spool, kind="installed", event_id="deploy-1", at=time.time(), run="deploy-1",
           src="deploy", tag="v0.11.0")  # fmt: skip
    record(rig.spool, kind="report", event_id="family-9", at=time.time(), run="family-9",
           src="family", report="9")  # fmt: skip
    await rig.sweeps(2)
    assert rig.order == [] and rig.journal.calls == []


# -- once --------------------------------------------------------------------------------------


@sync
async def test_claims_once_across_sweeps_and_a_restart(rig, tmp_path):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    await rig.sweeps(3)
    again = Rig(tmp_path, store=rig.store)  # a restart: a new desk on the same alerts.db
    again.journal.add(7)
    await again.sweeps(3)
    assert rig.order == ["text", "file"] and again.order == []
    assert len(rig.journal.deliveries) == 1 and again.journal.calls == []


@sync
async def test_a_claim_held_by_another_sender_is_left_until_it_is_stale(rig, monkeypatch):
    rig.journal.add(7)
    event_id = write_report(rig.spool, 7)
    assert rig.store.claim_notices([f"report:{event_id}"], time.time(), 300)
    await rig.sweeps()
    assert rig.order == []
    monkeypatch.setattr(reports_desk, "CLAIM_S", 0.0)  # that sender stopped mid-send
    await rig.sweeps()
    assert rig.order == ["text", "file"]


@sync
async def test_a_restart_after_the_text_sends_only_the_file(rig, tmp_path):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    rig.files.answers = ["retry"]  # OpenAlgo busy at the file: the text has gone
    await rig.sweeps()
    assert rig.order == ["text", "file"] and rig.journal.deliveries == []
    again = Rig(tmp_path, store=rig.store)
    again.journal.add(7)
    await again.sweeps(2)
    assert again.order == ["file"]  # never the text a second time
    assert again.journal.deliveries == [delivered(again, "sent")]


@sync
async def test_a_stop_before_the_text_went_gives_the_claim_back(rig):
    rig.journal.add(7)
    event_id = write_report(rig.spool, 7)
    key = f"report:{event_id}"
    rig.pacer.orders_waiting = lambda: True  # the desk holds the claim and waits for a slot
    task = asyncio.create_task(rig.desk.sweep())
    await asyncio.sleep(0.2)
    assert rig.store.notice_states([key])[key][0] == "claimed"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert rig.store.notice_states([key]) == {} and rig.order == []


# -- the reports lane --------------------------------------------------------------------------


@sync
async def test_no_slot_while_order_alerts_wait(rig):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    waiting = {"orders": True}
    rig.pacer.orders_waiting = lambda: waiting["orders"]
    task = asyncio.create_task(rig.desk.run())
    await asyncio.sleep(0.4)
    assert rig.order == [] and rig.lane_starts() == 0
    waiting["orders"] = False
    await asyncio.sleep(0.4)
    await rig.desk.close(1.0)
    await asyncio.wait_for(task, 2)
    assert rig.order == ["text", "file"]


@sync
async def test_no_slot_while_market_alerts_wait_and_none_between_text_and_file(rig):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    waiting = {"market": False}
    rig.pacer.set_waiting(outbox.MARKET, lambda: waiting["market"])

    async def text_then_market(text):
        rig.order.append("text")
        waiting["market"] = True  # a market alert arrives while the text goes out
        return SENT, ""

    rig.desk.send_text = text_then_market
    task = asyncio.create_task(rig.desk.sweep())
    await asyncio.sleep(0.3)
    assert rig.order == ["text"]  # the file waits for the market lane
    waiting["market"] = False
    await asyncio.wait_for(task, 2)
    assert rig.order == ["text", "file"]


class Clock:
    """The pacer's clock under the test's hand: the desk's waits move it on, never real time
    (review M4: a stall on a loaded runner can no longer fail a bound)."""

    def __init__(self, start: float = 1_000.0):
        self.now = start
        self._real = asyncio.sleep

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        await self._real(0)


def on_clock(rig) -> Clock:
    clock = Clock()
    rig.pacer.clock = clock
    rig.desk.sleep = clock.sleep
    return clock


@sync
async def test_four_slots_a_minute(rig):
    times = []
    for report_id in range(1, 6):
        rig.journal.add(report_id, text=f"सुदर्शन · SUDARSHAN\nbrief {report_id}", data=None)
        write_report(rig.spool, report_id, data=None, kind="brief", at=time.time() + report_id)
    clock = on_clock(rig)

    async def stamped(text):
        times.append(clock())
        return SENT, ""

    rig.desk.send_text = stamped
    start = clock()
    for _ in range(5):
        await rig.desk.sweep()
    assert len(times) == 5
    assert all(t - start < 4 * outbox.MIN_GAP_S for t in times[:4])  # the gap between each
    assert times[4] - times[0] >= outbox.MINUTE_S  # the fifth waited for the lane's minute


@sync
async def test_a_report_with_a_file_takes_two_of_the_four(rig):
    for report_id in (1, 2, 3):
        rig.journal.add(report_id)
        write_report(rig.spool, report_id, at=time.time() + report_id)
    clock = on_clock(rig)
    start = clock()
    await rig.sweeps(2)
    assert rig.order == ["text", "file", "text", "file"]
    assert clock() - start < 4 * outbox.MIN_GAP_S  # no wait for a minute yet
    await rig.sweeps()
    assert clock() - start >= outbox.MINUTE_S  # the third report's text waited for a slot
    assert len(rig.journal.deliveries) == 3


@sync
async def test_quiet_hours_hold_a_notice_but_never_a_report(rig, tmp_path):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    notices_spool = tmp_path / "notices.jsonl"
    record(notices_spool, kind="installed", event_id="deploy-2", at=time.time() - 120,
           run="deploy-2", src="deploy", tag="v0.11.0")  # fmt: skip
    told = []

    async def send(text):
        told.append(text)
        return SENT

    notice_desk = NoticeDesk(
        [notices_spool], rig.store, send, lambda: True, lambda: datetime.now(IST),
        market_open=lambda now: True, pace=LanePace(rig.pacer),
    )  # fmt: skip
    await notice_desk.sweep()
    await rig.sweeps()
    assert told == []  # the market trades: the notice waits for the close
    assert rig.order == ["text", "file"]  # the report goes


# -- text only, not sent, and what the journal is told ------------------------------------------


@sync
async def test_an_older_openalgo_gives_text_only_and_the_text_carries_the_link(rig):
    rig.journal.add(7, text="सुदर्शन · SUDARSHAN\nMon 9 Nov · Day report\nNet +₹3,712.50")
    write_report(rig.spool, 7)
    rig.files.answers = ["to:route"]  # /notify-file answered 404: no such route
    await rig.sweeps()
    assert rig.text.texts[0].endswith("\n" + LINK_LINE)
    assert rig.text.texts[0].count(LINK_LINE) == 1
    assert rig.journal.deliveries == [delivered(rig, "text_only", "to:route")]


@pytest.mark.parametrize(
    ("text_answers", "file_answers", "status", "code"),
    [
        ([(DROP, DROP_REASONS[409])], [], "not_sent", "ns:stop"),
        ([(DROP, DROP_REASONS[404])], [], "not_sent", "ns:link"),
        ([(DROP, outbox.REFUSED)], [], "not_sent", "ns:ref"),
        ([(UNCONFIRMED, "")] * 3, [], "not_sent", "ns:conf"),
        ([], ["to:stop"], "text_only", "to:stop"),
        ([], ["to:link"], "text_only", "to:link"),
        ([], ["to:size"], "text_only", "to:size"),
        ([], ["unconfirmed"] * 3, "text_only", "to:conf"),
        ([(UNCONFIRMED, "")], ["unconfirmed", "sent"], "sent", None),
    ],
    ids=["bot-stopped", "not-linked", "refused", "text-unconfirmed", "file-bot-stopped",
         "file-not-linked", "file-too-big", "file-unconfirmed", "confirmed-on-a-retry"],
)  # fmt: skip
@sync
async def test_the_delivery_call_carries_the_right_status_and_words(
    rig, text_answers, file_answers, status, code
):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    rig.text.answers, rig.files.answers = list(text_answers), list(file_answers)
    await rig.sweeps(4)
    assert rig.journal.deliveries == [delivered(rig, status, code)]
    if status == "not_sent":
        assert rig.files.sent == []


def test_the_reasons_are_the_owners_words():
    assert OUTCOMES["ns:stop"] == ("not_sent", "OpenAlgo's Telegram bot is stopped")
    assert OUTCOMES["ns:link"] == ("not_sent", "the owner's chat is not linked")
    assert OUTCOMES["ns:conf"] == ("not_sent", "Telegram did not confirm it after 3 tries")
    for code, (status, reason) in OUTCOMES.items():
        assert status in ("sent", "text_only", "not_sent"), code
        # a clause the page follows "Not sent: " with: no full stop, no status code
        assert reason is None or not reason.endswith("."), code
        assert reason is None or not re.search(r"\b[45][0-9]{2}\b", reason), code
        assert len(code) <= 10  # alerts.db's state column


@sync
async def test_a_sha256_mismatch_is_not_sent_and_is_told_in_words(rig):
    """Bytes that match neither the event's sha256 nor the one the journal gives with them."""
    rig.journal.add(7, data=PDF)
    broken = PDF + b"changed on the way"
    rig.journal.next["report_file"] = [("ok", {
        "id": 7, "file_name": NAME, "file_mime": "application/pdf", "file_sha256": sha(PDF),
        "file_b64": base64.b64encode(broken).decode()})]  # fmt: skip
    write_report(rig.spool, 7, data=PDF)
    await rig.sweeps()
    assert rig.order == ["text"] and rig.files.sent == []
    assert rig.journal.deliveries == [delivered(rig, "text_only", "to:sha")]
    assert "did not match" in OUTCOMES["to:sha"][1]


@sync
async def test_a_file_rebuilt_after_its_line_was_written_goes_as_the_journal_now_has_it(rig):
    """Review L11: the report was rebuilt before the desk sent it, so its file's sha256 is not
    the line's any more; the journal gives the bytes with their own sha256 and they match it."""
    rebuilt = PDF + b"rebuilt before delivery"
    rig.journal.add(7, data=rebuilt)
    write_report(rig.spool, 7, data=PDF)
    await rig.sweeps()
    assert rig.order == ["text", "file"] and rig.files.sent[0][1] == rebuilt
    assert rig.journal.deliveries == [delivered(rig, "sent")]


@sync
async def test_an_older_line_of_a_report_announced_again_is_passed_over(rig):
    """A part period announced, then the whole one (journal review M2) before the desk sent the
    first: the newer line sends the report as it is now, once."""
    rig.journal.add(7)
    first = write_report(rig.spool, 7, event_id="report-7-1", at=time.time() - 60)
    write_report(rig.spool, 7, event_id="report-7-2")
    await rig.sweeps(2)
    assert rig.order == ["text", "file"]  # once
    assert rig.journal.deliveries == [delivered(rig, "sent")]
    states = rig.store.notice_states([reports_desk.KEY_PREFIX + first])
    assert states[reports_desk.KEY_PREFIX + first][0] == reports_desk.DROPPED


@sync
async def test_a_file_that_cannot_go_is_never_posted(rig):
    rig.journal.add(7, data=PNG, name="SUDARSHAN_Day-report_20261109.pdf")  # a PNG named .pdf
    write_report(rig.spool, 7, data=PNG)
    await rig.sweeps()
    assert rig.files.sent == []
    assert rig.journal.deliveries == [delivered(rig, "text_only", "to:bad")]


@sync
async def test_a_report_the_journal_no_longer_has_is_not_sent(rig):
    write_report(rig.spool, 7)
    await rig.sweeps()
    assert rig.order == []
    assert rig.journal.deliveries == [delivered(rig, "not_sent", "ns:gone")]


@sync
async def test_the_journal_down_gives_the_claim_back_and_it_goes_later(rig):
    rig.journal.add(7)
    event_id = write_report(rig.spool, 7)
    rig.journal.next["report_get"] = [("down", None)]
    await rig.sweeps()
    assert rig.order == [] and rig.store.notice_states([f"report:{event_id}"]) == {}
    await rig.sweeps()
    assert rig.order == ["text", "file"]


@sync
async def test_the_journal_down_when_told_is_asked_again(rig):
    rig.journal.add(7)
    write_report(rig.spool, 7)
    rig.journal.next["delivery"] = [("down", None), ("off", None)]
    await rig.sweeps(3)
    assert rig.order == ["text", "file"]  # sent once, told on the third look
    assert [op for op, _ in rig.journal.calls].count("delivery") == 3


@sync
async def test_an_old_report_is_not_sent_and_the_journal_is_told_once(rig):
    rig.journal.add(7)
    rig.journal.add(8)
    write_report(rig.spool, 7, at=time.time() - 25 * 3600)
    write_report(rig.spool, 8, at=time.time() - 8 * 24 * 3600)  # history: nobody waits for it
    await rig.sweeps(3)
    assert rig.order == []
    assert rig.journal.deliveries == [delivered(rig, "not_sent", "ns:old")]


def on_clock_of(rig) -> dict:
    """The desk's own (service) clock under the test's hand, in epoch seconds."""
    seen = {"now": time.time()}
    rig.desk.clock = lambda: datetime.fromtimestamp(seen["now"], IST)
    return seen


@sync
async def test_a_report_too_old_to_send_names_the_side_that_did_not_answer(rig):
    """The builder's open item and review L5: the reason named the alerts service even when it
    was the journal that did not answer. It names the side the desk last waited on, and says
    only that it could not be sent within a day when the desk does not know."""
    seen = on_clock_of(rig)
    rig.journal.add(7)
    write_report(rig.spool, 7, data=None, kind="brief", at=seen["now"] - 10)
    rig.journal.next["report_get"] = [("down", None)]
    await rig.sweeps()  # the journal did not give the text
    seen["now"] += 25 * 3600
    await rig.sweeps()
    assert rig.journal.deliveries == [delivered(rig, "not_sent", "ns:old:j")]
    assert OUTCOMES["ns:old:j"][1] == (
        "it could not be sent within a day: the journal did not give it to the alerts service"
    )
    assert OUTCOMES["ns:old"][1] == "it could not be sent within a day"  # no guess
    assert all(len(code) <= 10 for code in OUTCOMES)  # alerts.db's state column


@sync
async def test_a_report_whose_text_telegram_would_not_take_says_so_when_too_old(rig):
    seen = on_clock_of(rig)
    rig.journal.add(7, data=None)
    write_report(rig.spool, 7, data=None, kind="brief", at=seen["now"] - 10)
    rig.text.answers = [(RETRY, "")]
    await rig.sweeps()
    seen["now"] += 25 * 3600
    await rig.sweeps()
    assert rig.journal.deliveries == [delivered(rig, "not_sent", "ns:old:t")]


@sync
async def test_one_report_that_cannot_move_does_not_hold_the_ones_after_it(rig):
    """Review M3: report 1's file call always fails; within three looks the desk moves on and
    report 2 (the Day report) goes; report 1 is tried again on its own pause."""
    seen = on_clock_of(rig)
    rig.journal.add(1, name="SUDARSHAN_Monthly-statement_202611.pdf")
    rig.journal.add(2)
    write_report(rig.spool, 1, kind="monthly", at=seen["now"] - 30)
    write_report(rig.spool, 2, at=seen["now"] - 20)
    real, broken = rig.journal.call, {"on": True}

    async def call(op, args):
        if op == "report_file" and args.get("id") == 1 and broken["on"]:
            rig.journal.calls.append((op, dict(args)))
            return "down", None
        return await real(op, args)

    rig.journal.call = call
    for _ in range(3):
        await rig.desk.sweep()
    told = {d["report_id"]: d["status"] for d in rig.journal.deliveries}
    assert told == {2: "sent"}  # the Day report went, its text and its file
    assert rig.text.texts.count(DAY_TEXT) == 2  # report 1's text went once, then report 2's
    asked = [a["id"] for op, a in rig.journal.calls if op == "report_file"]
    assert asked.count(1) == 3  # tried three times, now on its own pause
    broken["on"] = False
    await rig.desk.sweep()  # still on its pause: not asked
    assert [a["id"] for op, a in rig.journal.calls if op == "report_file"].count(1) == 3
    seen["now"] += 1  # past its pause (the tests' RETRY_S is 0.05 s)
    await rig.desk.sweep()
    told = {d["report_id"]: d["status"] for d in rig.journal.deliveries}
    assert told == {2: "sent", 1: "sent"}


@sync
async def test_without_the_owners_key_nothing_is_claimed(rig):
    rig.journal.add(7)
    event_id = write_report(rig.spool, 7)
    rig.owner_ok = False
    await rig.sweeps()
    assert rig.order == [] and rig.store.notice_states([f"report:{event_id}"]) == {}


@sync
async def test_a_line_that_names_no_report_is_dropped(rig):
    record(rig.spool, kind="report", event_id="journal-x", at=time.time(), run="journal-x",
           src="journal", report="seven", report_kind="eod")  # fmt: skip
    await rig.sweeps(2)
    assert rig.order == [] and rig.journal.calls == []
    assert rig.store.notice_states(["report:journal-x"])["report:journal-x"][0] == "dropped"


def test_a_long_text_is_cut_on_a_line_and_keeps_the_link():
    lines = "\n".join(f"line {n} " + "x" * 60 for n in range(200))
    text = reports_desk._text_of({"text": lines}, has_file=True)
    assert len(text) <= reports_desk.TEXT_MAX
    assert text.endswith("\n...\n" + LINK_LINE)
    assert reports_desk._text_of({"text": "  "}, has_file=False) is None
    assert reports_desk._text_of({"text": DAY_TEXT}, has_file=True) == DAY_TEXT


# -- it follows the journal: one switch, read from the journal's /health -----------------------


def test_the_desk_is_built_unless_alerts_env_says_off_and_it_asks_the_journal(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("SUDARSHAN_JOURNAL_SPOOL", str(tmp_path / "spool.jsonl"))
    monkeypatch.delenv("SUDARSHAN_JOURNAL_API_URL")
    telegram = types.SimpleNamespace(send_with_reason=None, owner=lambda: None,
                                     reload_owner=lambda: None)  # fmt: skip
    service = types.SimpleNamespace(
        settings=types.SimpleNamespace(journal=False, openalgo_url="http://127.0.0.1:5000"),
        store=None, _owner_ready=lambda: True, clock=lambda: datetime.now(IST),
        outbox=types.SimpleNamespace(pacer=outbox.Pacer()),
    )  # fmt: skip
    assert report_desk_for(service, telegram, client=None) is None
    assert off_reason(service.settings) == SWITCHED_OFF_HERE
    service.settings.journal = True
    desk = report_desk_for(service, telegram, client=None)
    assert isinstance(desk, ReportDesk) and desk.lane == outbox.REPORTS
    assert desk.spool == tmp_path / "spool.jsonl"
    assert desk.journal.base_url == "http://127.0.0.1:5586"
    assert isinstance(desk.watch, JournalWatch)
    assert desk.watch.url == "http://127.0.0.1:5586/health"
    assert desk.health() == {"state": "starting", "on": False, "reason": None, "waiting": 0,
                             "last_delivery_ms": None}  # fmt: skip
    token = desk.journal.fernet.encrypt(b"x")
    fernet_for(os.environ["API_KEY_PEPPER"], JOURNAL_DELIVERY_INFO).decrypt(token)
    monkeypatch.setenv("SUDARSHAN_JOURNAL_API_URL", "http://example.invalid:5586")
    elsewhere = report_desk_for(service, telegram, None)
    assert elsewhere.journal.base_url == "http://127.0.0.1:5586"
    assert elsewhere.watch.url == "http://127.0.0.1:5586/health"
    monkeypatch.delenv("API_KEY_PEPPER")  # no key to seal with: no desk, and /health says why
    with caplog.at_level(logging.ERROR, logger="sudarshan.alerts"):
        assert report_desk_for(service, telegram, None) is None
    assert off_reason(service.settings) == NO_SEAL and "cannot be sealed" in caplog.text


@sync
async def test_nothing_goes_while_the_journal_is_switched_off_and_it_goes_once_it_is_on(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(reports_desk, "ASK_EVERY_S", 0.0)
    rig = Rig(tmp_path, watch=Watch((JOURNAL_OFF, JOURNAL_SWITCHED_OFF), (ON, None)))
    rig.journal.add(7)
    event_id = write_report(rig.spool, 7)
    await rig.sweeps()
    assert rig.order == [] and rig.journal.calls == []  # not even asked for the text
    assert rig.store.notice_states([f"report:{event_id}"]) == {}  # and nothing claimed
    assert rig.desk.health() == {"state": "journal_off", "on": False,
                                 "reason": JOURNAL_SWITCHED_OFF, "waiting": 1,
                                 "last_delivery_ms": None}  # fmt: skip
    before = int(time.time() * 1000)
    await rig.sweeps()
    assert rig.order == ["text", "file"]
    assert rig.journal.deliveries == [delivered(rig, "sent")]
    await rig.sweeps()
    block = rig.desk.health()
    assert (block["state"], block["on"], block["reason"], block["waiting"]) == ("on", True, None, 0)
    assert before <= block["last_delivery_ms"] <= int(time.time() * 1000)
    rig.store.close()


@sync
async def test_a_journal_that_does_not_answer_holds_every_report_says_so_once_and_is_asked_again(
    tmp_path, caplog
):
    silent = (JOURNAL_DOWN, JOURNAL_SILENT)
    watch = Watch(silent, silent, silent, (ON, None))
    rig = Rig(tmp_path, watch=watch)
    rig.journal.add(7)
    write_report(rig.spool, 7)
    with caplog.at_level(logging.INFO, logger="sudarshan.alerts"):
        for _ in range(3):
            wait = await rig.desk.sweep()
            assert rig.order == [] and rig.journal.calls == []
            assert 0 < wait <= 0.06 and rig.desk.health()["state"] == "journal_down"
            await asyncio.sleep(wait)
        await rig.desk.sweep()  # asked again after the pause: it answers, and the report goes
    assert rig.order == ["text", "file"] and watch.asks == 4
    said = [(r.levelname, r.getMessage()) for r in caplog.records
            if r.getMessage().startswith("Reports:")]  # fmt: skip
    assert said == [
        ("WARNING", "Reports: none are delivered: the journal service is not answering. The "
                    "journal is asked again after a pause."),
        ("INFO", "Reports: the journal says it is on, so its reports are delivered"),
    ]  # fmt: skip
    rig.store.close()


@sync
async def test_the_journal_is_asked_once_a_minute_at_most_while_its_answer_holds(
    tmp_path, monkeypatch
):
    watch = Watch((ON, None))
    rig = Rig(tmp_path, watch=watch)
    for report_id in (7, 8, 9):  # text only: three of the lane's four slots a minute
        rig.journal.add(report_id, data=None)
        write_report(rig.spool, report_id, data=None, at=time.time() + report_id)
    await rig.sweeps(5)
    assert len(rig.journal.deliveries) == 3 and watch.asks == 1
    monkeypatch.setattr(reports_desk, "ASK_EVERY_S", 0.0)
    await rig.sweeps(2)
    assert watch.asks == 3
    rig.store.close()


@pytest.mark.parametrize(
    ("health", "said"),
    [
        ((200, {"service": "sudarshan-journal", "journal_state": {"enabled": True}}), (ON, None)),
        ((200, {"service": "sudarshan-journal", "journal_state": {"enabled": False}}),
         (JOURNAL_OFF, JOURNAL_SWITCHED_OFF)),
        ((200, {"service": "sudarshan-journal", "journal_state": {
            "enabled": False, "reason": "Your trade journal was written by a newer release."}}),
         (JOURNAL_OFF, "Your trade journal was written by a newer release.")),
        ((200, {"service": "sudarshan-portfolio", "journal_state": {"enabled": True}}),
         (JOURNAL_DOWN, JOURNAL_SILENT)),
        ((200, {"service": "sudarshan-journal"}), (JOURNAL_DOWN, JOURNAL_SILENT)),
        ((200, {"service": "sudarshan-journal", "journal_state": {"enabled": "yes"}}),
         (JOURNAL_OFF, JOURNAL_SWITCHED_OFF)),
        ((503, {"service": "sudarshan-journal", "journal_state": {"enabled": True}}),
         (JOURNAL_DOWN, JOURNAL_SILENT)),
        ((200, ["not", "a", "document"]), (JOURNAL_DOWN, JOURNAL_SILENT)),
    ],
    ids=["on", "off", "kept-off", "not-the-journal", "no-state", "not-true", "busy", "odd"],
)  # fmt: skip
@sync
async def test_each_answer_of_the_journals_health_is_read(health, said):
    async with FakeJournalApi() as journal_api, httpx.AsyncClient(trust_env=False) as client:
        journal_api.health = health
        assert await JournalWatch(journal_api.url, client).ask() == said
        assert journal_api.health_asks == 1


@sync
async def test_a_journal_port_nobody_or_another_user_holds_is_not_asked(monkeypatch):
    async with FakeJournalApi() as journal_api, httpx.AsyncClient(trust_env=False) as client:
        watch = JournalWatch(journal_api.url, client)
        for held, said in (
            (reports_desk.loopback.NOBODY, (JOURNAL_DOWN, JOURNAL_NOT_RUNNING)),
            (reports_desk.loopback.OTHER, (JOURNAL_DOWN, JOURNAL_PORT_HELD)),
        ):
            monkeypatch.setattr(reports_desk.loopback, "holder", lambda url, tables=None, h=held: h)
            assert await watch.ask() == said
        assert journal_api.health_asks == 0


# -- over HTTP: the real links against OpenAlgo's routes and the journal's API -----------------


@contextlib.asynccontextmanager
async def wired(tmp_path, *, watch: bool = False):
    """The desk with the real links over HTTP; ``watch`` asks the journal's /health as well, on
    the same client, as the running service's desk does."""
    async with FakeOpenAlgo() as openalgo, FakeJournalApi() as journal_api, httpx.AsyncClient(
        trust_env=False, follow_redirects=False
    ) as client:  # fmt: skip
        owner = Owner(OWNER, API_KEY)
        telegram = outbox.OpenAlgoTelegram(openalgo.url, client, lambda: owner, lambda: owner)
        store = Store(tmp_path / "alerts.db")
        desk = ReportDesk(
            tmp_path / "journal-events.jsonl", store,
            JournalLink(journal_api.url, fernet_for(os.environ["API_KEY_PEPPER"],
                                                    JOURNAL_DELIVERY_INFO), client),
            telegram.send_with_reason,
            OpenAlgoFiles(openalgo.url, client, lambda: owner, lambda: owner),
            lambda: True, lambda: datetime.now(IST), outbox.Pacer(),
            watch=JournalWatch(journal_api.url, client) if watch else None,
        )  # fmt: skip
        yield types.SimpleNamespace(openalgo=openalgo, journal=journal_api, desk=desk,
                                    spool=desk.spool)  # fmt: skip
        store.close()


@sync
async def test_over_http_text_then_file_with_the_seal_and_the_multipart_fields(tmp_path):
    async with wired(tmp_path) as wire:
        wire.journal.reports[7] = report_record(7)
        write_report(wire.spool, 7)
        await wire.desk.sweep()
        assert wire.openalgo.order == ["text", "file"]
        (text,) = wire.openalgo.texts
        assert text == {"apikey": API_KEY, "username": OWNER, "message": DAY_TEXT,
                        "wait_for_delivery": True}  # fmt: skip
        (sent,) = wire.openalgo.files
        assert sent == {
            "form": {"apikey": API_KEY, "username": OWNER, "wait_for_delivery": "true",
                     "caption": "सुदर्शन / SUDARSHAN: Day report"},
            "name": NAME, "mime": "application/pdf", "data": PDF,
        }  # fmt: skip
        assert [op for op, _ in wire.journal.calls] == ["report_get", "report_file", "delivery"]
        assert set(wire.journal.principals) == {"sudarshan-alerts"}
        assert wire.journal.deliveries == [
            {"report_id": 7, "channel": "telegram", "status": "sent", "reason": None}
        ]


@sync
async def test_over_http_an_older_openalgo_without_the_route_is_text_only(tmp_path):
    async with wired(tmp_path) as wire:
        wire.journal.reports[7] = report_record(7)
        write_report(wire.spool, 7)
        wire.openalgo.file_route = False  # the app answers its JSON 404 for an unknown /api/ path
        await wire.desk.sweep()
        assert wire.openalgo.order == ["text"] and LINK_LINE in wire.openalgo.texts[0]["message"]
        assert wire.journal.deliveries[0]["status"] == "text_only"
        assert wire.journal.deliveries[0]["reason"] == OUTCOMES["to:route"][1]


@sync
async def test_over_http_a_seal_under_another_purpose_is_refused_by_the_journal(tmp_path):
    async with wired(tmp_path) as wire:
        wire.desk.journal.fernet = fernet_for(os.environ["API_KEY_PEPPER"], JOURNAL_API_INFO)
        wire.journal.reports[7] = report_record(7)
        write_report(wire.spool, 7)
        await wire.desk.sweep()
        assert wire.journal.refused == ["seal"] and wire.openalgo.order == []


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ((200, {"status": "success", "message": "File sent successfully"}), "sent"),
        ((200, {"status": "success", "message": "File queued for delivery"}), "unconfirmed"),
        ((404, {"status": "error", "code": "not_linked", "message": "Not linked."}), "to:link"),
        ((404, {"status": "error", "message": "Not found", "path": "/x"}), "to:route"),
        ((409, {"status": "error", "code": "bot_stopped", "message": "Stopped."}), "to:stop"),
        ((413, {"status": "error", "code": "size", "message": "Too large."}), "to:size"),
        ((502, {"status": "error", "code": "not_taken", "message": "Not taken."}), "unconfirmed"),
        ((400, {"status": "error", "code": "caption", "message": "Too long."}), "to:ref"),
        ((429, {"status": "error", "message": "Too many requests."}), "retry"),
        ((500, {"status": "error", "code": "failed", "message": "Failed."}), "retry"),
        ((401, {"status": "error", "code": "key", "message": "Key."}), "retry"),
    ],
    ids=["sent", "queued", "not-linked", "no-route", "bot-stopped", "too-big", "not-taken",
         "refused", "busy", "failing", "key"],
)  # fmt: skip
@sync
async def test_each_answer_of_the_file_route_is_read(answer, outcome):
    async with FakeOpenAlgo() as openalgo, httpx.AsyncClient(trust_env=False) as client:
        openalgo.file_answers = [answer, answer]
        owner = Owner(OWNER, API_KEY)
        files = OpenAlgoFiles(openalgo.url, client, lambda: owner, lambda: owner)
        assert await files.send(NAME, PDF, "application/pdf", "caption") == outcome


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        ((200, {"status": "success", "data": {"text": "x"}}), "ok"),
        ((200, {"status": "success", "data": {"enabled": False}}), "off"),
        ((404, {"status": "error", "code": "not_found",
                "message": "This report is not in the archive."}), "gone"),
        ((404, {"status": "error", "code": "not_found", "message": "not found"}), "down"),
        ((501, {"status": "error", "code": "not_ready", "message": "Not ready."}), "down"),
        ((503, {"status": "error", "code": "busy", "message": "Busy."}), "down"),
        ((403, {"status": "error", "code": "forbidden", "message": "Forbidden."}), "refused"),
        ((200, {"hello": "world"}), "down"),
    ],
    ids=["ok", "off", "gone", "unknown-op", "not-ready", "busy", "sealed", "no-envelope"],
)  # fmt: skip
@sync
async def test_each_answer_of_the_journal_is_read(answer, kind):
    async with FakeJournalApi() as journal_api, httpx.AsyncClient(trust_env=False) as client:
        journal_api.answers["report_get"] = answer
        link = JournalLink(
            journal_api.url, fernet_for(os.environ["API_KEY_PEPPER"], JOURNAL_DELIVERY_INFO), client
        )
        assert (await link.call("report_get", {"id": 1}))[0] == kind


@sync
async def test_the_journal_not_running_is_down_without_a_request(monkeypatch):
    async with httpx.AsyncClient(trust_env=False) as client:
        link = JournalLink(
            "http://127.0.0.1:9",
            fernet_for(os.environ["API_KEY_PEPPER"], JOURNAL_DELIVERY_INFO),
            client,
        )
        monkeypatch.setattr(
            reports_desk.loopback, "holder", lambda url, tables=None: reports_desk.loopback.NOBODY
        )
        assert await link.call("delivery", {"report_id": 1}) == ("down", None)
        monkeypatch.setattr(
            reports_desk.loopback, "holder", lambda url, tables=None: reports_desk.loopback.OTHER
        )
        assert await link.call("delivery", {"report_id": 1}) == ("down", None)


# -- inside the running service ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alerts_env", "journal_on"),
    [(None, True), (None, False), ("off", True)],
    ids=["the-journal-on", "the-journal-off", "off-in-alerts-env"],
)
def test_the_running_service_delivers_reports_while_the_journal_says_it_is_on(
    settings_for, tmp_path, monkeypatch, alerts_env, journal_on
):
    spool = tmp_path / "journal-events.jsonl"
    monkeypatch.setenv("SUDARSHAN_JOURNAL_SPOOL", str(spool))

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo, FakeJournalApi() as journal:
            monkeypatch.setenv("SUDARSHAN_JOURNAL_API_URL", journal.url)
            journal.enabled = journal_on
            journal.reports[7] = report_record(7)
            write_report(spool, 7)
            overrides = {} if alerts_env is None else {"SUDARSHAN_JOURNAL": alerts_env}
            settings = settings_for(proxy, openalgo, **overrides)
            svc = AlertService(settings, owner_reader=lambda: Owner(OWNER, OWNER_KEY))
            task = asyncio.create_task(svc.run())
            if alerts_env is None and journal_on:
                await wait_until(lambda: journal.deliveries, timeout=10)
                assert openalgo.order == ["text", "file"]
                assert journal.deliveries[0]["status"] == "sent"
                block = health_document(svc)["report_desk"]
                assert block["state"] == "on" and block["on"] and block["reason"] is None
                assert isinstance(block["last_delivery_ms"], int)
            elif alerts_env is None:
                await wait_until(
                    lambda: svc.reports is not None and svc.reports.state != "starting"
                )
                await asyncio.sleep(0.3)
                assert journal.calls == [] and openalgo.order == [] and journal.health_asks >= 1
                assert health_document(svc)["report_desk"] == {
                    "state": "journal_off", "on": False, "reason": JOURNAL_SWITCHED_OFF,
                    "waiting": 1, "last_delivery_ms": None,
                }  # fmt: skip
            else:
                await wait_until(lambda: proxy.subscribed)
                await asyncio.sleep(0.5)
                assert svc.reports is None and journal.calls == [] and openalgo.order == []
                assert journal.health_asks == 0  # not even asked
                assert health_document(svc)["report_desk"] == {
                    "state": "off", "on": False, "reason": SWITCHED_OFF_HERE,
                    "waiting": None, "last_delivery_ms": None,
                }  # fmt: skip
            svc.stop("test")
            await asyncio.wait_for(task, 10)
            assert svc.state == "stopped"

    asyncio.run(scenario())


# -- descriptors -------------------------------------------------------------------------------


@sync
async def test_deliveries_leave_no_descriptor_behind(tmp_path, monkeypatch):
    """Spool reads, alerts.db rows, the journal's and OpenAlgo's sockets, and the journal's
    /health asked before every look (M15.7): all closed again."""
    monkeypatch.setattr(outbox, "MINUTE_S", 0.2)
    monkeypatch.setattr(reports_desk, "ASK_EVERY_S", 0.0)
    async with wired(tmp_path, watch=True) as wire:
        for report_id in range(1, 8):
            wire.journal.reports[report_id] = report_record(report_id)
        write_report(wire.spool, 1)
        await wire.desk.sweep()  # warm-up: the first connection of each kind, the store's file
        watch = FdWatch()
        for report_id in range(2, 8):
            write_report(wire.spool, report_id, at=time.time() + report_id)
        for _ in range(6):
            await wire.desk.sweep()
        assert len(wire.journal.deliveries) == 7 and len(wire.openalgo.files) == 7
        assert wire.journal.health_asks == 7  # one before each look
        await watch.aassert_clean()
