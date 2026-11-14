"""The watch's notices in the owner's words (M19.5, P8 7.1): each check's failure, warning and
recovery, what is told at once and what waits for the close, and the watch's own file read by the
desk end to end."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

import pytest
from alerts_fakes import ist

from sudarshan.alerts import notices, outbox
from sudarshan.alerts.notices import NoticeDesk, compose
from sudarshan.alerts.outbox import SENT
from sudarshan.alerts.store import Store
from sudarshan.autologin.messages import BRAND
from sudarshan.ops import events
from sudarshan.ops.events import Event
from sudarshan.ops.watch import checks, run

T0 = ist(2026, 11, 16, 10, 42)  # a Monday, the market trading


def event(kind: str, check: str, at: datetime = T0, **facts):
    ident = f"watch-{check}-{kind}"
    return Event(ident, kind, at.timestamp(), ident, "watch", {"check": check, **facts})


def said(kind: str, check: str, **facts) -> str:
    text = compose([event(kind, check, **facts)], T0)
    assert text.splitlines()[0] == BRAND
    return "\n".join(text.splitlines()[1:])


def test_a_silent_service_names_what_stopped_and_when():
    assert said("watch_failing", "service_feed", service="feed", minutes="4") == (
        "SUDARSHAN's price feed stopped answering at 10:38 IST. Prices, market alerts and the "
        "screener are stopped. The server restarts it by itself; if this message comes again, "
        "have the server looked at."
    )
    for name in checks.SERVICES:
        text = said("watch_failing", f"service_{name}", service=name, minutes="4")
        assert "stopped answering at 10:38 IST" in text and notices.RESTARTS in text
        assert f"sudarshan-{name}" not in text and "service_" not in text
        back = said("watch_fine", f"service_{name}", was="failing")
        assert back.startswith("SUDARSHAN's ") and back.endswith(
            "is answering again since 10:42 IST."
        )


def test_the_login_the_feed_and_the_instrument_list():
    assert said("watch_failing", "login", when="before_open", minutes="4") == (
        "There is no Upstox login for today. Orders, charts and order alerts will not work "
        "until you connect Upstox in the app."
    )
    stream = said("watch_failing", "login", when="in_session", minutes="6")
    assert stream.startswith("Order updates from Upstox have not been arriving since 10:36 IST.")
    assert "connect Upstox" in stream
    assert said("watch_fine", "login", was="failing") == (
        "Upstox is connected again since 10:42 IST: order updates are arriving."
    )
    feed = said("watch_failing", "feed", minutes="4")
    assert "has not been connected to Upstox since 10:38 IST" in feed
    assert "not Real-time" in feed, "never 'Live' for the feed, and never Real-time without ticks"
    assert said("watch_failing", "instrument_list") == (
        "Today's instrument list was not refreshed. New weekly contracts may be missing and "
        "expired ones may still show. Open Master Contract and press Download."
    )
    assert said("watch_fine", "instrument_list", was="failing") == (
        "Today's instrument list is now refreshed."
    )


def test_the_disk_the_memory_and_the_certificate():
    assert said("watch_warning", "disk", free_gb="12.4", cause="space") == (
        "The server's disk is getting full: 12.4 GB free. Nothing has stopped; have old files "
        "cleared before it runs out."
    )
    assert said("watch_failing", "disk", free_gb="3.1", cause="space") == (
        "The server's disk is almost full: 3.1 GB free. Backups and the databases can stop "
        "working. Free space on it now."
    )
    assert "room left for new files" in said("watch_failing", "disk", free_gb="40.0", cause="files")
    assert "short of room for new files" in said("watch_warning", "disk", cause="files")
    assert "short of memory" in said("watch_warning", "memory", available_mb="900")
    assert said("watch_failing", "certificate", hours="31") == (
        "The server's security certificate runs out in 31 hours and has not renewed. Phones "
        "will refuse the app after that."
    )
    assert "has run out" in said("watch_failing", "certificate", hours="0")
    assert said("watch_fine", "certificate", was="failing") == (
        "The server's security certificate was renewed."
    )


def test_a_fact_that_is_not_plain_is_left_out_and_an_unknown_check_says_nothing():
    text = said("watch_failing", "disk", free_gb="<b>3</b>", cause="space")
    assert text.startswith("The server's disk is almost full. ") and "<" not in text
    assert "within two days" in said("watch_failing", "certificate", hours="soon")
    assert "stopped answering at 10:42 IST" in said("watch_failing", "service_feed", minutes="x")
    for kind in ("watch_failing", "watch_warning", "watch_fine"):
        assert notices.batch_lines([event(kind, "something_new")], T0) == []
        assert notices.batch_lines([event(kind, "service_other")], T0) == []


def _every_sentence() -> list[str]:
    out = []
    for name in (*(f"service_{n}" for n in checks.SERVICES), "feed", "instrument_list"):
        out.append(said("watch_failing", name, minutes="4"))
        out.append(said("watch_fine", name, was="failing"))
    for when in ("before_open", "in_session"):
        out.append(said("watch_failing", "login", when=when, minutes="4"))
    for kind in ("watch_failing", "watch_warning"):
        for cause in ("space", "files"):
            out.append(said(kind, "disk", free_gb="4.0", cause=cause))
    out += [said("watch_warning", "memory"), said("watch_fine", "memory", was="warning")]
    out += [said("watch_failing", "certificate", hours=h) for h in ("31", "0")]
    out += [said("watch_fine", name, was="failing") for name in ("login", "disk", "certificate")]
    return out


def test_every_sentence_is_for_a_trader():
    sentences = _every_sentence()
    assert all(sentences), "every check and level the watch can tell has words"
    for text in sentences:
        assert not re.search(r"\b[1-5]\d\d\b", text), text
        low = text.lower()
        for word in ("traceback", "exception", "http", "errno", "systemd", "json", "/health",
                     "127.0.0.1", "timeout", "paper", "trinetra"):  # fmt: skip
            assert word not in low, (word, text)
        assert not re.search(r"\b(port|unit|socket|listener)s?\b", low), text
        assert not re.search(r"\barm(ed|ing)?\b", low), text
        assert not re.search(r"\blive\b", low), text
        assert all(ord(ch) < 0x2000 for ch in text), "no emoji or symbols"
        assert text.endswith(".")


def test_what_is_told_in_market_hours():
    assert notices._at_once(event("watch_failing", "disk"))
    assert notices._at_once(event("watch_fine", "disk", was="failing"))
    assert not notices._at_once(event("watch_fine", "disk", was="warning"))
    assert not notices._at_once(event("watch_warning", "disk"))
    assert {"watch_failing", "watch_warning", "watch_fine"} <= set(notices.KIND_ORDER)
    assert events.WATCH_SPOOL in events.DEFAULT_SPOOLS


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


def test_from_the_watchs_run_to_one_telegram_message_and_its_recovery(tmp_path, monkeypatch):
    """The real run writes its own file; the real desk reads it, in market hours."""
    monkeypatch.setattr(notices, "NOTICES_PER_MINUTE", 1000)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)
    settings = run.Settings(tmp_path / "watch", tmp_path, tmp_path / ".env")
    clock, send = Clock(T0), Send()
    fine = {
        "ist_minute": 642,
        "ist_date": "2026-11-16",
        "window_open": True,
        "health": {"feed": {"state": "connected", "market_window": {"open": True}},
                   "alerts": {"state": "connected"}, "screener": {"state": "running"}},
        "enabled": {"screener": True},
        "openalgo": True,
        "master_date": "2026-11-16",
        "disk": {"free_gb": 40.0, "inodes_free_pct": 80.0},
        "mem_available_mb": 4000,
        "cert_hours_left": 200.0,
    }  # fmt: skip
    down = {**fine, "health": {**fine["health"], "screener": None},
            "disk": {"free_gb": 12.0, "inodes_free_pct": 80.0}}  # fmt: skip

    def watch(readings):
        at = clock().timestamp()
        seen = {**readings, "now": at}
        assert run.run_once(settings, clock=lambda: at, gather=lambda *a: seen) == 0

    desk = NoticeDesk(
        [settings.spool], Store(tmp_path / "alerts.db"), send, lambda: True, clock,
        market_open=lambda at: True,
    )  # fmt: skip

    async def scenario():
        watch(fine)
        for _ in range(2):  # two runs without an answer; the disk warning is in the first
            clock.advance(120)
            watch(down)
        await desk.sweep()
        clock.advance(notices.SETTLE_S + 1)
        await desk.sweep()
        assert len(send.texts) == 1, send.texts
        assert send.texts[0].splitlines() == [
            BRAND,
            "SUDARSHAN's screener stopped answering at 10:42 IST. Screens are not running and "
            "their alerts are stopped. " + notices.RESTARTS,
        ], "the disk warning waits for the close; the failure does not"
        clock.advance(120)
        watch({**fine, "disk": down["disk"]})
        await desk.sweep()
        clock.advance(notices.SETTLE_S + 1)
        await desk.sweep()
        assert (
            send.texts[1].splitlines()[1]
            == "SUDARSHAN's screener is answering again since 10:48 IST."
        )
        await desk.sweep()
        assert len(send.texts) == 2, "each told once"

    asyncio.run(scenario())
