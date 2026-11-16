"""J3 (M15.1): the morning brief's TRINETRA lines, read through ``journal/signals_ro.py``.

The reader asks sudarshan-signals under the journal purpose only (``state``, ``state_at``), to a
port this account holds; the brief says nothing while the view is off, two lines while TRINETRA's
readings are fresh, else the view's own sentence. Against a fake listener on 127.0.0.1; every
reading is invented.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from http.server import BaseHTTPRequestHandler

import pytest
from journal_fakes import PEPPER, _Server, free_port
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_capture import scenario

from sudarshan.autologin.handoff import NonceBook, open_sealed
from sudarshan.journal import reports, signals_ro
from sudarshan.journal.signals_ro import SignalsReader, SignalsUnavailable, brief_input
from sudarshan.signals import contract as c
from sudarshan.vault import SIGNALS_API_INFO, SIGNALS_JOURNAL_INFO, fernet_for

JOURNAL_KEY = fernet_for(PEPPER, SIGNALS_JOURNAL_INFO)
NOW = 1794800712250  # Mon 16 Nov 2026 09:15:12 IST (invented)


def reading(module="regime", instrument="NIFTY", **over) -> dict:
    out = {"module": module, "instrument": instrument, "state": "DIST", "label": "FII distribution",
           "bias": "bear", "confidence": 0.712, "quality": "ok", "lapsed": False}  # fmt: skip
    return out | over


def state(status="fresh", readings=None, words="TRINETRA · updated 08:44:10") -> dict:
    return {"now_ms": NOW, "lang": "hi", "notes": [], "alerts": [],
            "freshness": {"status": status, "words": words},
            "readings": [reading()] if readings is None else readings}  # fmt: skip


class FakeSignals(_Server):
    """sudarshan-signals' listener as the journal reaches it: the journal purpose's key only."""

    def __init__(self, key=JOURNAL_KEY):
        super().__init__()
        self.key, self.seen = key, NonceBook(100)
        self.answer: dict = {"status": "success", "data": state()}
        self.asked: list[dict] = []

    def handler(fake):  # noqa: N805 - a closure over the fake, not a method
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                op = self.path.removeprefix("/v1/")
                try:
                    message = open_sealed(fake.key, body, op=op, seen=fake.seen, max_bytes=4096)
                except Exception:
                    return self._send(403, {"status": "error", "code": "forbidden",
                                            "message": c.WORDS["sealed"]})  # fmt: skip
                fake.asked.append(message)
                if message.get("principal") != "sudarshan-journal":
                    return self._send(403, {"status": "error", "code": "principal",
                                            "message": c.WORDS["forbidden"]})  # fmt: skip
                return self._send(200, fake.answer)

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler


@pytest.fixture
def signals():
    fake = FakeSignals()
    url = fake.start()
    yield fake, url
    fake.stop()


def read(reader: SignalsReader, op: str = "state", **args):
    async def go():
        try:
            return await reader.call(op, **args)
        finally:
            await reader.aclose()

    return asyncio.run(go())


# ---- the reader ---------------------------------------------------------------------------------


def test_the_reader_asks_state_and_state_at_under_the_journal_purpose_only(signals):
    fake, url = signals
    assert read(SignalsReader(url, JOURNAL_KEY)) == state()
    read(SignalsReader(url, JOURNAL_KEY), "state_at", ts=NOW)
    assert [(m["op"], m["principal"], m["args"]) for m in fake.asked] == [
        ("state", "sudarshan-journal", {}),
        ("state_at", "sudarshan-journal", {"ts": NOW}),
    ]
    with pytest.raises(ValueError):
        read(SignalsReader(url, JOURNAL_KEY), "settings_set", version=1)  # reads only, ever


def test_a_read_that_cannot_be_had_says_why_in_the_owners_words(signals):
    fake, url = signals
    with pytest.raises(SignalsUnavailable) as down:
        read(SignalsReader(f"http://127.0.0.1:{free_port()}", JOURNAL_KEY))
    assert str(down.value) == c.WORDS["not_answering"]
    with pytest.raises(SignalsUnavailable) as refused:  # the bridge's key opens nothing here
        read(SignalsReader(url, fernet_for(PEPPER, SIGNALS_API_INFO)))
    assert refused.value.kind == "refused" and "different keys" in str(refused.value)
    fake.answer = {"status": "error", "code": "busy", "message": c.WORDS["busy"]}
    with pytest.raises(SignalsUnavailable) as failed:
        read(SignalsReader(url, JOURNAL_KEY))
    assert failed.value.kind == "failed"
    with pytest.raises(SignalsUnavailable) as keyless:
        read(SignalsReader(url, None))
    assert keyless.value.kind == "refused"


def test_nothing_is_asked_of_a_port_another_account_holds(signals, tmp_path):
    fake, url = signals
    port = int(url.rsplit(":", 1)[1])
    table = tmp_path / "tcp"
    table.write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
        f"   0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
        "  4242        0 1 1 0000000000000000 100 0 0 10 0\n"
    )
    with pytest.raises(SignalsUnavailable) as held:
        read(SignalsReader(url, JOURNAL_KEY, tables=(table,), uid=lambda: 1000))
    assert held.value.kind == "held" and fake.asked == []


# ---- what the brief says of an answer -----------------------------------------------------------


def test_fresh_readings_give_the_regime_and_the_gap_bias_of_both_indices():
    answer = state(readings=[
        reading(),
        reading("gap_bias", label="Gap down", confidence=0.64, lapsed=True),
        reading("swing_eod", label="Reversal likely"),  # the brief does not name it
        reading(instrument="BANKNIFTY", label="Range", bias="neutral", confidence=None,
                quality="degraded"),
    ])  # fmt: skip
    got = brief_input(answer)
    assert got == {"readings": {
        "NIFTY": {
            "regime": {"label": "FII distribution", "bias": "bear", "confidence": "0.71",
                       "quality": "ok", "lapsed": False},
            "gap_bias": {"label": "Gap down", "bias": "bear", "confidence": "0.64",
                         "quality": "ok", "lapsed": True},
        },
        "BANKNIFTY": {
            "regime": {"label": "Range", "bias": "neutral", "confidence": None,
                       "quality": "degraded", "lapsed": False},
        },
    }}  # fmt: skip


def test_not_fresh_or_switched_off_gives_the_views_own_sentence():
    stale = "TRINETRA has not published since 10:42. This is its last reading, 14 minutes old."
    assert brief_input(state("stale", words=stale)) == {"sentence": stale}
    assert brief_input(state("never", readings=[], words=c.WORDS["never"])) == {
        "sentence": c.WORDS["never"],
    }  # fmt: skip
    off = {"enabled": False, "message": c.WORDS["not_set_up"]}
    assert brief_input(off) == {"sentence": c.WORDS["not_set_up"]}


# ---- the brief, built --------------------------------------------------------------------------


class Reader:
    """The service's reader, as a test puts its own in ``service.parts``."""

    def __init__(self, answer=None, fails: str | None = None):
        self.answer, self.fails, self.calls = answer, fails, 0

    async def state(self):
        self.calls += 1
        if self.fails:
            raise SignalsUnavailable(self.fails)
        return self.answer


def brief_text(rig, reader: Reader, switched_on: bool = True) -> str:
    rig.svc.settings = dataclasses.replace(rig.svc.settings, signals=switched_on)
    rig.svc.parts["signals"] = reader
    tue = 20261110
    inputs = scenario(rig, lambda: reports.brief_inputs(rig.svc, tue, rig.now))
    from sudarshan.journal.core import words

    return words.brief(**inputs)


def test_the_brief_says_nothing_of_trinetra_while_the_view_is_switched_off(rig):
    reader = Reader(state())
    text = brief_text(rig, reader, switched_on=False)
    assert "TRINETRA" not in text and reader.calls == 0


def test_the_brief_carries_two_lines_when_fresh_after_the_markets(rig):
    lines = brief_text(rig, Reader(state(readings=[reading(), reading("gap_bias",
                                                  label="Gap down", confidence=0.64)]))).split("\n")  # fmt: skip
    assert lines[2] == "Market open 09:15-15:30 (F&O to 15:40)"
    assert lines[3] == (
        "TRINETRA NIFTY: regime FII distribution (bearish, confidence 0.71), gap bias Gap down "
        "(bearish, confidence 0.64)"
    )
    assert lines[4] == "TRINETRA BANKNIFTY: regime not published, gap bias not published"


def test_the_brief_says_the_freshness_sentence_when_not_fresh_or_not_answering(rig):
    stale = "TRINETRA has not published since 15 Nov 15:58. This is its last reading, 16 hours old."
    lines = brief_text(rig, Reader(state("stale", words=stale))).split("\n")
    assert lines[3] == stale and not lines[4].startswith("TRINETRA")
    lines = brief_text(rig, Reader(fails="down")).split("\n")
    assert lines[3] == c.WORDS["not_answering"]


def test_the_service_builds_its_reader_with_the_journal_purposes_key(rig):
    rig.svc.parts.pop("signals", None)
    rig.svc.signals_key = JOURNAL_KEY
    built = signals_ro.reader_for(rig.svc)
    assert isinstance(built, SignalsReader) and built.fernet is JOURNAL_KEY
    assert built.url == rig.svc.settings.signals_url == "http://127.0.0.1:5588"
