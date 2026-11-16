"""The TRINETRA page's answers (P8 U1), recorded from OpenAlgo's real bridge in front of the real
sudarshan-signals: ``frontend/src/sudarshan/signals/bridge.answers.json``.

The page's vitest suites and its headless check (``frontend/e2e/sd-trinetra.spec.ts``) answer the
page from that file, so the phone is tested against the bridge and the service as built, not
against answers written by hand. Here the service runs in this process on a free port with a fixed
clock and reads invented documents from a throw-away letterbox; the real blueprint forwards each
request to it, sealed, as on the server. The test records every answer again and fails on any
difference: after a deliberate change to the bridge, the service or the documents below, run it
once with ``SUDARSHAN_WRITE_ANSWERS=1`` to write the file, then run the page's tests. Nothing of
TRINETRA's is read anywhere: every document is invented.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from signals_bridge_app import VIEWER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_signals as bp_module
from sudarshan.signals import contract, diff, perf
from sudarshan.signals.service import Service
from sudarshan.signals.store import SignalsStore
from utils.thread_safe_cache import LockedTTLCache

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "signals" / "tests"))
from signals_fakes import KEYS, Box, free_port, settings_for  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ANSWERS = REPO / "frontend" / "src" / "sudarshan" / "signals" / "bridge.answers.json"
JSON_HEADERS = {"Accept": "application/json"}
SPECS = {spec.name: spec for spec in contract.OPS}

DAY0 = 1794853800000  # Tue 17 Nov 2026 00:00 IST (an invented trading day)
MIN = 60_000


def ist(hour: int, minute: int, second: int = 0, day: int = 0) -> int:
    """Epoch ms of an India time on DAY0 (``day`` days from it)."""
    return DAY0 + day * 86_400_000 + (hour * 3600 + minute * 60 + second) * 1000


NOW = ist(9, 5)  # the page reads the state at 09:05, inside the gap bias's window
LATER = ist(9, 20)  # past the last document's next_by and the gap bias's 09:15

WHY_DISTRIBUTION = {
    "en": "Invented: index futures turned net short over three sessions and the put-call ratio "
    "fell.\nA close above 26,120 would undo it.",
    "hi": "Invented: teen sessions se index futures mein net short, aur PCR gira.\n26,120 ke upar "
    "close isko palat dega.",
    "gu": "Invented: ત્રણ સત્રથી ઇન્ડેક્સ ફ્યુચર્સમાં નેટ શોર્ટ, અને PCR ઘટ્યો.",
}


def reading(module, instrument, state, label, bias, *, as_of, **more) -> dict:
    out = {"module": module, "instrument": instrument, "state": state, "label": label,
           "bias": bias, "as_of": as_of, "horizon": "swing", "quality": "ok"}  # fmt: skip
    out.update(more)
    return out


AT1 = ist(15, 20, day=-1)  # Mon 16 Nov, after the close
AT2 = NOW - 20_000  # Tue 17 Nov 09:04:40
BANK_RANGE = reading(
    "regime", "BANKNIFTY", "NEUTRAL_RANGE", "Range-bound", "neutral", as_of=AT1 - 1000,
    confidence=0.55, since=ist(10, 10, day=-5),
    why={"en": "Invented: no side has held the index for a week.", "hi": None, "gu": None},
)  # fmt: skip
SWING_TOP = reading(
    "swing_eod", "NIFTY", "TOP_WATCH", "Swing top forming", "bear", as_of=AT1 - 1000,
    confidence=0.58, since=ist(15, 15, day=-1),
    levels={"trigger": 25850.0, "invalid": 26200.0, "target": 25400.0},
    why={"en": "Invented: a lower high on the daily close.", "hi": "Invented: daily close par "
         "lower high.", "gu": None},
)  # fmt: skip
DOC1 = {
    "v": 1, "run": "invented-run", "seq": 1, "at": AT1, "next_by": AT1 + 60 * MIN,
    "signals": [
        reading("regime", "NIFTY", "ACCUMULATION", "FII accumulation", "bull", as_of=AT1 - 1000,
                score=0.35, confidence=0.64, since=ist(11, 30, day=-4),
                why={"en": "Invented: index futures net long.", "hi": None, "gu": None}),
        BANK_RANGE,
        SWING_TOP,
    ],
    "alerts": [],
}  # fmt: skip
DOC2 = {
    "v": 1, "run": "invented-run", "seq": 2, "at": AT2, "next_by": AT2 + 5 * MIN,
    "signals": [
        reading("regime", "NIFTY", "DISTRIBUTION", "FII distribution", "bear", as_of=AT2 - 1000,
                score=-0.42, confidence=0.71, since=ist(9, 4),
                levels={"trigger": 25900.0, "invalid": 26120.0, "target": None},
                inputs=[{"name": "FII index futures, net", "value": "-1.2 lakh contracts"},
                        {"name": "Put-call ratio", "value": "0.82"}],
                why=WHY_DISTRIBUTION),
        BANK_RANGE,
        reading("gap_bias", "NIFTY", "GAP_DOWN_MILD", "Mild gap down", "bear", as_of=ist(8, 44),
                confidence=0.62, since=ist(8, 45), valid_until=ist(9, 15), horizon="open",
                why={"en": "Invented: GIFT NIFTY trades below yesterday's close.", "hi": None,
                     "gu": None}),
        reading("gap_bias", "BANKNIFTY", "FLAT_OPEN", "Flat open", "neutral", as_of=ist(8, 44),
                confidence=0.51, since=ist(8, 45), valid_until=ist(9, 15), horizon="open"),
        SWING_TOP,
        reading("swing_intraday", "BANKNIFTY", "REVERSAL_UP", "Intraday reversal up", "bull",
                as_of=AT2 - 1000, confidence=0.66, since=ist(9, 0), horizon="intraday",
                quality="degraded"),
        reading("breadth", "NIFTY", "BROAD_WEAK", "Breadth weak", "bear", as_of=AT2 - 1000,
                horizon="intraday"),
    ],
    "alerts": [
        {"id": "invented-0001", "module": "regime", "instrument": "NIFTY", "level": "watch",
         "title": "NIFTY regime turned to FII distribution",
         "text": "Invented alert text for the tests.", "lang": "en"},
    ],
}  # fmt: skip


IST = timezone(timedelta(hours=5, minutes=30))


def day_at(day: date, hour: int, minute: int) -> int:
    return int(datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute),
                                IST).timestamp() * 1000)  # fmt: skip


def seed_scores(folder: Path) -> None:
    """Three months of invented changes and index closes in a store the service then opens, so
    the performance op scores them as on the server (perf.py): the NIFTY regime turns bull, bear
    and neutral in turn at each close, 32 times each (judged); the gap bias at 12 opens (too few
    to judge). Weekdays from 15 Jun 2026, no holidays."""
    days, day = [], date(2026, 6, 15)
    while len(days) < 102:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    closes = [round(24000 + 400 * math.sin(n * 0.9) + 6 * n, 2) for n in range(len(days))]
    folder.mkdir(parents=True, exist_ok=True)
    store = SignalsStore(folder / contract.DB_FILENAME)
    try:
        current, seq, levels = {}, 0, []
        for n, day in enumerate(days[:96]):
            docs = []
            if 1 <= n <= 12:
                docs.append((day_at(day, 8, 50), [reading(
                    "gap_bias", "NIFTY", "GAP", "Gap call", "bull" if n % 2 else "bear",
                    as_of=day_at(day, 8, 49), valid_until=day_at(day, 9, 15), horizon="open")]))
                open_px = round(closes[n - 1] * (1 + 0.003 * math.cos(n)), 2)
                levels.append({"day": perf.day_key(day), "instrument": "NIFTY",
                               "open_px": open_px, "prev_close_px": closes[n - 1],
                               "at_ms": day_at(day, 9, 16)})
            bias = ("bull", "bear", "neutral")[n % 3]
            docs.append((day_at(day, 15, 20), [reading(
                "regime", "NIFTY", bias.upper(), f"Invented {bias}", bias,
                as_of=day_at(day, 15, 19))]))
            for at, signals in docs:
                seq += 1
                raw = json.dumps({"v": 1, "run": "invented-score", "seq": seq, "at": at,
                                  "next_by": at + 3_600_000, "signals": signals}).encode()
                snap = contract.read_document(raw, now_ms=at, written_ms=at)
                assert isinstance(snap, contract.Snapshot), snap
                out = diff.apply(current, snap, at)
                store.accept(snap, name=f"{at}-{seq}.json", received_ms=at, current=out.current,
                             changes=out.changes)  # fmt: skip
                current = out.current
        levels += [{"day": perf.day_key(day), "instrument": "NIFTY", "close_px": closes[n],
                    "at_ms": day_at(day, 15, 30)} for n, day in enumerate(days)]  # fmt: skip
        store.levels_put(levels)
    finally:
        store.close()


def until(check, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("the condition did not come true in time")


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def make_service(folder: Path, port: int, clock: dict, **more) -> tuple[Service, Box]:
    box = Box(folder / "in")
    settings = settings_for(folder, port, **more)
    svc = Service(settings, KEYS, hooks={}, jobs={}, letterbox=box.reader, scan_s=0.02,
                  setup_every_s=0.05)  # fmt: skip
    svc.clock = lambda: clock["now"]
    return svc, box


@contextmanager
def running(svc: Service, port: int):
    """The service in a thread of its own, with its own event loop, stopped on the way out."""
    held: dict = {}
    started = threading.Event()

    def main() -> None:
        async def go() -> None:
            held["loop"] = asyncio.get_running_loop()
            started.set()
            await svc.run()

        asyncio.run(go())

    thread = threading.Thread(target=main, name="signals-page-answers", daemon=True)
    thread.start()
    assert started.wait(10)
    try:
        until(lambda: svc.state in ("running", "off") and listening(port))
        yield svc
    finally:
        held["loop"].call_soon_threadsafe(svc.stop)
        thread.join(15)
        assert not thread.is_alive()


def ask(client, op: str, args: dict | None = None) -> dict:
    """One request to the real bridge, built from the contract's route for ``op``."""
    spec = SPECS[op]
    args = dict(args or {})
    path = contract.API_PREFIX + spec.route.replace("<id>", str(args.get("id", "")))
    query = {name: str(value) for name, value in args.items() if name != "id"}
    if spec.method == "GET":
        response = client.get(path, query_string=query, headers=JSON_HEADERS)
    else:
        query = {}
        response = client.put(path, json=args, headers={**JSON_HEADERS, **csrf_headers(client)})
    return {"op": op, "args": args, "method": spec.method, "path": path, "query": query,
            "status": response.status_code, "answer": response.get_json()}  # fmt: skip


def record(tmp_path: Path, monkeypatch) -> dict:
    reset_users()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=8, ttl=600))
    for name in [key for key in os.environ if key.lower().endswith("_proxy")]:
        monkeypatch.delenv(name, raising=False)
    out: dict = {}
    try:
        clock = {"now": NOW}
        port = free_port()
        monkeypatch.setenv("SUDARSHAN_SIGNALS_API_URL", f"http://127.0.0.1:{port}")
        svc, box = make_service(tmp_path / "on", port, clock)
        owner = make_app().test_client()
        login_session(owner)
        with running(svc, port):
            for doc in (DOC1, DOC2):
                box.drop(doc, written_ms=doc["at"])
                until(lambda doc=doc: (svc.last or {}).get("seq") == doc["seq"] and not box.names())
            out["state"] = ask(owner, "state")
            out["timeline"] = ask(owner, "timeline", {"limit": 50})
            out["timeline_first"] = ask(owner, "timeline", {"limit": 3})
            older = out["timeline_first"]["answer"]["data"]["next_before"]
            out["timeline_older"] = ask(owner, "timeline", {"before": older, "limit": 3})
            newest = out["timeline"]["answer"]["data"]["changes"][0]["id"]
            out["change"] = ask(owner, "change_get", {"id": newest})
            out["change_missing"] = ask(owner, "change_get", {"id": 9999})
            out["settings"] = ask(owner, "settings_get")
            version = out["settings"]["answer"]["data"]["version"]
            out["settings_saved"] = ask(owner, "settings_set", {"version": version, "lang": "gu"})
            out["settings_conflict"] = ask(owner, "settings_set", {"version": version,
                                                                   "lang": "en"})  # fmt: skip
            out["performance"] = ask(owner, "performance")
            out["bad_module"] = ask(owner, "timeline", {"module": "NOPE"})
            out["bad_request"] = ask(owner, "timeline", {"limit": "ten"})
            clock["now"] = LATER
            out["state_stale"] = ask(owner, "state")
            out["unauthorized"] = ask(make_app().test_client(), "state")
            viewer = make_app().test_client()
            login_session(viewer, VIEWER)
            out["forbidden"] = ask(viewer, "state")
        out["not_answering"] = ask(owner, "state")  # the service has stopped: nobody on the port

        port = free_port()
        monkeypatch.setenv("SUDARSHAN_SIGNALS_API_URL", f"http://127.0.0.1:{port}")
        seed_scores(tmp_path / "scored" / "state")
        svc, _box = make_service(tmp_path / "scored", port, clock)
        with running(svc, port):
            out["performance_scored"] = ask(owner, "performance")

        for name, more in (("off", {"enabled": False}), ("not_set_up", {"unset": True})):
            port = free_port()
            monkeypatch.setenv("SUDARSHAN_SIGNALS_API_URL", f"http://127.0.0.1:{port}")
            svc, _box = make_service(tmp_path / name, port, clock, **more)
            with running(svc, port):
                out[name] = ask(owner, "state")
    finally:
        http.close()
    return {
        "about": "Recorded by sudarshan/tests/test_signals_page_answers.py from OpenAlgo's real "
        "bridge in front of the real sudarshan-signals, reading invented documents at a fixed "
        "clock. Do not edit by hand: re-record with SUDARSHAN_WRITE_ANSWERS=1.",
        "now_ms": NOW,
        "later_ms": LATER,
        "answers": out,
    }


def test_the_pages_answers_are_the_bridges_as_built(tmp_path, monkeypatch):
    got = record(tmp_path, monkeypatch)
    if os.environ.get("SUDARSHAN_WRITE_ANSWERS") == "1":
        ANSWERS.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    kept = json.loads(ANSWERS.read_text(encoding="utf-8"))
    assert kept == got, (
        "the bridge or the service answers the page differently from bridge.answers.json: "
        "re-record with SUDARSHAN_WRITE_ANSWERS=1 and run the page's tests"
    )


def test_the_recording_covers_every_op_and_every_way_the_page_is_refused():
    kept = json.loads(ANSWERS.read_text(encoding="utf-8"))["answers"]
    assert {entry["op"] for entry in kept.values()} == {spec.name for spec in contract.OPS}
    codes = {entry["answer"]["error"]["code"] for entry in kept.values()
             if entry["answer"]["status"] == "error"}  # fmt: skip
    assert codes == {"unauthorized", "forbidden", "bad_request", "not_found",
                     "version_conflict", "unavailable"}  # fmt: skip
    offs = {entry["answer"].get("message") for entry in kept.values()
            if entry["answer"].get("enabled") is False}  # fmt: skip
    assert offs == {contract.WORDS["off"], contract.WORDS["not_set_up"]}
    text = ANSWERS.read_text(encoding="utf-8")
    assert "Invented" in text and "invented-run" not in text  # TRINETRA's run id is never shown
