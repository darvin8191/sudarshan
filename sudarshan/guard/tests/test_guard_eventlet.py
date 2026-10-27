"""The risk gate under a real eventlet hub: no stall, and a real thread is never left waiting.

Production runs ``gunicorn --worker-class eventlet -w 1``: one process, one hub. The gate is
called from greenlets (every request) and, through the Telegram bot, from a real OS thread (the
kill switch's activation). So its lock must be a real one, and it must never be held across a
wait. These tests measure that under a real hub, by whether a ticking greenlet keeps running
and how long things take: ``uv run app.py`` never patches anything, so none of this can fail on
the development server (CLAUDE.md "It cannot be caught locally"). The first test shows the
defect a green lock would bring, so the others cannot pass vacuously.

Each case runs in a subprocess: ``monkey_patch()`` is global and cannot be undone.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip(
    "eventlet", reason="eventlet is installed by the production installer, not by pyproject"
)

REPO = Path(__file__).resolve().parents[3]

PREAMBLE = """
import eventlet
eventlet.monkey_patch()

import json, os, sys, threading, time
sys.path.insert(0, {repo!r})
import eventlet.patcher
_orig = eventlet.patcher.original("threading")
ticks = []

def ticker():
    while True:
        ticks.append(time.monotonic())
        eventlet.sleep(0.05)

eventlet.spawn(ticker)
eventlet.sleep(0.1)

def ticks_between(start, end):
    return sum(1 for t in ticks if start < t < end)

def gate_on_a_slow_broker(tmp):
    from datetime import UTC, datetime
    from sudarshan.guard import gate, store
    from sudarshan.guard.store import KillSwitchState

    class Book:
        rows = [{{"instrument_token": "NSE_FO|CE", "product": "I", "quantity": 65}}]
        def live_cached_rows(self, auth):
            return None
        def live_rows(self, auth):
            eventlet.sleep(0.05)  # a positions read: cooperative network I/O
            return list(self.rows)
        def instrument_token(self, symbol, exchange):
            return "NSE_FO|" + symbol

    gate.GATE = gate.Gate(store=store.RiskStore(os.path.join(tmp, "r.db")), read=Book())
    gate.GATE.set_state(KillSwitchState())

    class Sent:
        status = 200
        status_code = 200

    @gate.upstox_place
    def place(data, auth):
        time.sleep(float(data.get("hold", 0)))  # green: a slow Upstox call
        return Sent(), {{"status": "success"}}, "OID"

    on = KillSwitchState(on=True, since=datetime.now(UTC))
    return gate, place, on

def order(action="BUY", hold=0):
    return {{"symbol": "CE", "exchange": "NFO", "action": action, "quantity": "65",
             "product": "MIS", "hold": hold}}
"""


def run(body: str, tmp_path) -> dict:
    script = PREAMBLE.format(repo=str(REPO)) + textwrap.dedent(body)
    env = {
        "PATH": "/usr/bin:/bin",
        "SUDARSHAN_RISK_GUARD": "on",
        "API_KEY_PEPPER": "e" * 64,
        "APP_KEY": "test-only-app-key-" + "4" * 32,
        "DATABASE_URL": f"sqlite:///{tmp_path}/openalgo.db",
        "LOG_DIR": f"{tmp_path}/log",
    }
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env=env,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_green_lock_would_strand_the_real_thread(tmp_path):
    """The defect itself: a greenlet holds a green lock across a sleep; a real OS thread (the
    Telegram bot turning the switch on) never gets it."""
    result = run(
        """
        lock = threading.Lock()  # green after monkey_patch
        got = []
        def real_side():
            with lock:
                got.append(1)
        def green_side():
            with lock:
                eventlet.sleep(0.3)
        g = eventlet.spawn(green_side)
        eventlet.sleep(0.05)
        t = _orig.Thread(target=real_side, daemon=True)
        t.start()
        deadline = time.monotonic() + 3
        while t.is_alive() and time.monotonic() < deadline:
            eventlet.sleep(0.02)
        g.wait()
        print(json.dumps({"got": bool(got)}))
        """,
        tmp_path,
    )
    assert result == {"got": False}


def test_the_gate_s_lock_is_a_real_one(tmp_path):
    result = run(
        """
        from sudarshan.guard import gate
        print(json.dumps({"real": type(gate.Gate()._lock) is type(_orig.Lock()),
                          "green": type(gate.Gate()._lock) is type(threading.Lock())}))
        """,
        tmp_path,
    )
    assert result == {"real": True, "green": False}


def test_twenty_slow_orders_go_together_and_the_hub_keeps_running(tmp_path):
    result = run(
        f"""
        gate, place, on = gate_on_a_slow_broker({str(tmp_path)!r})
        start = time.monotonic()
        pool = [eventlet.spawn(place, order(hold=1.0), "token") for _ in range(20)]
        answers = [g.wait() for g in pool]
        end = time.monotonic()
        print(json.dumps({{"sent": sum(a[2] == "OID" for a in answers), "elapsed": end - start,
                           "ticks": ticks_between(start, end)}}))
        """,
        tmp_path,
    )
    assert result["sent"] == 20
    assert result["elapsed"] < 3.0  # side by side, not one after another (20 s)
    assert result["ticks"] >= 15  # a live hub ticks about 20 times a second


def test_refusals_and_their_audit_rows_never_stall_the_hub(tmp_path):
    result = run(
        f"""
        gate, place, on = gate_on_a_slow_broker({str(tmp_path)!r})
        gate.GATE.set_state(on)
        start = time.monotonic()
        pool = [eventlet.spawn(place, order("BUY"), "token") for _ in range(30)]
        answers = [g.wait() for g in pool]
        end = time.monotonic()
        refused = sum(a[0].status == 403 for a in answers)
        gate.GATE.flush_notes()
        rows = gate.GATE._the_store().history()
        print(json.dumps({{"refused": refused, "elapsed": end - start, "rows": len(rows),
                           "ticks": ticks_between(start, end),
                           "count": sum(row.count for row in rows)}}))
        """,
        tmp_path,
    )
    assert result["refused"] == 30 and result["count"] == 30
    assert result["rows"] <= 3  # merged: refusals racing to write first may each start a row
    assert result["elapsed"] < 5.0
    assert result["ticks"] >= result["elapsed"] * 10  # never stalled


def test_a_real_thread_turns_the_switch_on_while_greenlets_are_sending(tmp_path):
    """The bot's thread takes the gate's lock while ten slow orders are being sent: it gets it
    at once, sees all ten in flight, and a greenlet arriving after it is refused."""
    result = run(
        f"""
        gate, place, on = gate_on_a_slow_broker({str(tmp_path)!r})
        pool = [eventlet.spawn(place, order(hold=2.0), "token") for _ in range(10)]
        eventlet.sleep(0.3)
        box = {{}}
        def bot():
            started = time.monotonic()
            box["turned"] = gate.GATE.turn_on(on)
            box["took"] = time.monotonic() - started
        t = _orig.Thread(target=bot, daemon=True)
        start = time.monotonic()
        t.start()
        while t.is_alive() and time.monotonic() - start < 5:
            eventlet.sleep(0.01)
        late = place(order("BUY"), "token")
        answers = [g.wait() for g in pool]
        end = time.monotonic()
        outcome, _state, tokens = box["turned"]
        print(json.dumps({{"outcome": outcome, "in_flight": len(tokens), "took": box["took"],
                           "late": late[0].status, "sent": sum(a[2] == "OID" for a in answers),
                           "ticks": ticks_between(start, end), "elapsed": end - start}}))
        """,
        tmp_path,
    )
    assert result["outcome"] == "on" and result["in_flight"] == 10 and result["sent"] == 10
    assert result["took"] < 0.5
    assert result["late"] == 403
    assert result["ticks"] >= result["elapsed"] * 10
