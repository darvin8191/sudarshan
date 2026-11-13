"""The limits of the integrator's exception (design: Integrator's exception, limits 1 and 2).

Watchlists v2 keeps its store and master reads inside OpenAlgo on condition that it starts no
thread, timer or loop, keeps one generation of the master and stays small. Each test here fails
when a later PR loosens one of those; do not relax them to land a feature.
"""

from __future__ import annotations

import ast
import gc
import threading
import tracemalloc
import weakref
from pathlib import Path

import pytest

from sudarshan.watchlists import dynamic, master, search, tvimport

ROOT = Path(__file__).resolve().parents[3]
BANNED_MODULES = ("concurrent", "asyncio", "apscheduler", "sched", "multiprocessing", "subprocess")
BANNED_ATTRS = {("threading", "Thread"), ("threading", "Timer")}


def scan(source: str) -> list[str]:
    """What a source file does that the exception forbids."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in BANNED_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in BANNED_MODULES:
                found.append(node.module)
            if node.module == "threading":
                found += [
                    f"threading.{a.name}" for a in node.names if a.name in ("Thread", "Timer")
                ]
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in BANNED_ATTRS:
                found.append(f"{node.value.id}.{node.attr}")
    return found


def watched_files():
    files = [p for p in (ROOT / "sudarshan" / "watchlists").rglob("*.py") if "tests" not in p.parts]
    bridge = ROOT / "blueprints" / "sudarshan_watchlists.py"
    return files + ([bridge] if bridge.exists() else [])


def test_the_scanner_catches_each_forbidden_thing():
    for bad in (
        "import threading\nthreading.Thread(target=f).start()",
        "from threading import Timer",
        "import asyncio",
        "from concurrent.futures import ThreadPoolExecutor",
        "import apscheduler.schedulers.background",
        "import sched",
        "import multiprocessing",
        "import subprocess",
    ):
        assert scan(bad), bad
    assert not scan("import threading\nlock = threading.Lock()\nimport time")


def test_no_thread_timer_executor_loop_or_scheduler_in_the_module():
    files = watched_files()
    assert files
    for path in files:
        assert scan(path.read_text(encoding="utf-8")) == [], path


# A fake master that looks like OpenAlgo's snapshot: rows in by_exchange, and the lookups.
class Row:
    def __init__(self, symbol, exchange, typ="EQ", underlying=None, expiry=None, strike=None):
        self.symbol, self.exchange, self.instrumenttype = symbol, exchange, typ
        self.underlying, self.expiry, self.strike = underlying, expiry, strike
        self.name, self.token, self.lotsize = symbol, f"{exchange}|{symbol}", 25


def snapshot(tag: str, stocks: int = 10, strikes: int = 5):
    cash = [Row(f"{tag}S{i}", "NSE") for i in range(stocks)]
    opts = [
        Row(f"{tag}U{k}{t}", "NFO", t, f"{tag}U", "24-NOV-26", float(25000 + 50 * k))
        for k in range(strikes)
        for t in ("CE", "PE")
    ]
    futs = [Row(f"{tag}UFUT", "NFO", "FUT", f"{tag}U", "24-NOV-26")]
    snap = Snap(
        by_exchange={"NSE": tuple(cash), "NFO": tuple(opts + futs)},
        by_symbol_exchange={(r.symbol, r.exchange): r for r in cash + opts + futs},
        by_token={r.token: r for r in cash + opts + futs},
    )
    return snap, cash + opts + futs


class Snap:
    """A snapshot as OpenAlgo's is: a plain object (so it can be weakly referenced)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class Box:
    """A source whose snapshot can be swapped, as OpenAlgo's is at each load."""

    def __init__(self, snap):
        self.snap = snap

    def __call__(self):
        return self.snap


def run_every_op(m: master.Master):
    m.generation()
    m.index()
    m.expiries("AU")
    m.expiries("AU", "futures")
    m.strikes("AU", "2026-11-24")
    m.known("NSE", "AS1")
    m.by_token("NSE|AS1")
    ix = m.index()
    search.search(ix, "au 25000 ce", now_ms=0)
    search.search(ix, "as1", now_ms=0)
    search.parse("nifty next week 26000 pe")
    tvimport.parse("###A,NSE:AS1", lambda e, s: True)
    dynamic.pick_expiries(["2026-11-24"], 0)


def run_every_registered_op(monkeypatch, tmp_path):
    """Every op the bridge registers, switched on and off, through the real blueprint."""
    from watchlists_bridge_app import csrf_headers, login_session, make_app, path_of, reset_users

    import blueprints.sudarshan_watchlists as bridge
    from sudarshan.watchlists import contract, engine, ops

    monkeypatch.setenv(contract.DB_ENV, str(tmp_path / "wl.db"))
    reset_users()
    client = make_app(ops.discover(), real=True).test_client()
    login_session(client)
    headers = csrf_headers(client)
    registered = [op for op in contract.OPS if op.name in ops.discover()]
    assert registered
    for switch in ("on", "off"):
        monkeypatch.setenv(contract.SWITCH_ENV, switch)
        bridge._record.clear()
        for op in registered:
            r = client.open(path_of(op), method=op.method, json={}, headers=headers)
            assert r.status_code in (200, 400, 503), (op.name, r.status_code)
    engine.dispose()


def test_no_thread_starts_for_any_registered_op(monkeypatch, tmp_path):
    before = {t.ident for t in threading.enumerate()}
    run_every_registered_op(monkeypatch, tmp_path)
    assert {t.ident for t in threading.enumerate()} == before


def test_no_thread_starts_for_any_op_or_a_swap_of_the_master():
    a, _ = snapshot("A")
    b, _ = snapshot("B")
    box = Box(a)
    m = master.Master(box)
    before = {t.ident for t in threading.enumerate()}
    run_every_op(m)
    box.snap = b
    run_every_op(m)
    box.snap = a
    run_every_op(m)
    assert {t.ident for t in threading.enumerate()} == before


def test_one_generation_and_the_old_rows_die_with_it():
    a, a_rows = snapshot("A")
    b, _ = snapshot("B")
    box = Box(a)
    m = master.Master(box)
    assert m.generation() is not None
    ref = weakref.ref(a_rows[0])
    box.snap = b
    assert m.generation() is not None
    del a, a_rows
    gc.collect()
    assert ref() is None, "the old snapshot's rows are still held"
    assert master.generations_alive() == 1


def test_swapping_twice_still_leaves_one():
    a, _ = snapshot("A")
    b, _ = snapshot("B")
    box = Box(a)
    m = master.Master(box)
    for snap in (a, b, a, b):
        box.snap = snap
        m.generation()
        gc.collect()
        assert master.generations_alive() == 1


def test_the_maps_hold_the_snapshots_own_rows_never_copies():
    a, rows = snapshot("A")
    m = master.Master(Box(a))
    ix = m.index()
    ids = {id(r) for r in rows}
    refs = [r for lst in ix.cash.values() for r in lst]
    refs += [r for lst in ix.futures.values() for _, r in lst]
    refs += [r for by in ix.options.values() for lst in by.values() for r in lst]
    refs += [r for _, r in ix.names]
    assert refs and all(id(r) in ids for r in refs)


def test_a_snapshot_that_lapses_drops_the_generation():
    a, _ = snapshot("A")
    box = Box(a)
    m = master.Master(box)
    assert m.generation() is not None
    box.snap = None
    assert m.generation() is None and m.index() is None
    gc.collect()
    assert master.generations_alive() == 0
    assert not m.known("NSE", "AS1") and m.by_token("x") is None


def test_the_build_yields_between_slices_and_builds_once_for_concurrent_callers():
    a, rows = snapshot("A", stocks=40, strikes=60)
    pauses = []
    box = Box(a)
    m = master.Master(box, slice_rows=25, pause=pauses.append)
    start = threading.Barrier(6)
    out = []

    def go():
        start.wait()
        out.append(m.generation())

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert m.builds == 1
    assert len({id(g) for g in out}) == 1
    assert len(pauses) >= len(rows) // 25 and set(pauses) == {0}


def test_lookups_read_the_snapshot_and_keep_nothing():
    a, rows = snapshot("A")
    m = master.Master(Box(a))
    assert m.known("NSE", "AS1") and not m.known("BSE", "AS1")
    assert m.by_token("NSE|AS1") is rows[1]
    assert m.expiries("AU") == ["2026-11-24"] and m.expiries("AU", "futures") == ["2026-11-24"]
    assert m.strikes("AU", "2026-11-24") == [25000.0, 25050.0, 25100.0, 25150.0, 25200.0]
    assert m.expiries("NOPE") == [] and m.strikes("AU", "2026-12-01") == []


def test_derived_structures_stay_under_15_mb_on_120000_rows():
    cash = [Row(f"S{i}", "NSE") for i in range(3000)]
    rows = list(cash)
    for u in range(600):
        for e in ("24-NOV-26", "01-DEC-26", "29-DEC-26"):
            for k in range(33):
                for t in ("CE", "PE"):
                    rows.append(Row(f"U{u}{e}{k}{t}", "NFO", t, f"U{u}", e, 25000.0 + 50 * k))
    assert len(rows) >= 120_000
    snap = Snap(by_exchange={"NSE": tuple(cash), "NFO": tuple(rows[3000:])},
                           by_symbol_exchange={}, by_token={})  # fmt: skip
    m = master.Master(Box(snap))
    gc.collect()
    tracemalloc.start()
    try:
        base, _ = tracemalloc.get_traced_memory()
        gen = m.generation()
        now, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert gen is not None and gen.rows == len(rows)
    assert now - base < 15 * 1024 * 1024, f"{(now - base) / 1e6:.1f} MB"


@pytest.mark.parametrize("n", [1])
def test_the_scanned_tree_is_not_empty(n):
    assert len(watched_files()) >= n


# -- one master for the process (limit 2 across requests, not inside one object) ----------------
def constructs_a_master(source: str) -> list[int]:
    """The lines where a module builds a ``Master`` of its own instead of using ``shared()``."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "Master":
                lines.append(node.lineno)
    return lines


def test_no_module_of_the_feature_builds_a_master_of_its_own():
    """Every reader (refs/list.py now; built-in lists, follow rows, search and the members route
    later) uses ``master.shared()``; a ``Master()`` per request is a generation per request."""
    assert constructs_a_master("from x import master\nm = master.Master()") == [2]
    assert constructs_a_master("m = Master(source)") == [1]
    assert not constructs_a_master("from x import master\nm = master.shared()")
    for path in watched_files():
        assert constructs_a_master(path.read_text(encoding="utf-8")) == [], path


@pytest.fixture
def shared_over(monkeypatch):
    """The shared master over a snapshot of the test's (the module's own reader is replaced)."""
    box = Box(None)
    monkeypatch.setattr(master, "openalgo_snapshot", box)
    shared = master.shared()
    shared.drop()
    gc.collect()
    yield box, shared
    box.snap = None
    shared.drop()
    gc.collect()


def test_the_shared_master_is_one_object_and_two_readers_share_one_build(shared_over):
    box, shared = shared_over
    box.snap, _ = snapshot("A", stocks=50, strikes=40)
    builds = shared.builds
    first, second = master.shared(), master.shared()  # two requests, as refs/list.py asks
    assert first is second is shared
    assert first.index() is second.index() is not None
    gc.collect()
    assert shared.builds == builds + 1 and master.generations_alive() == 1


def test_two_requests_in_flight_through_the_bridge_share_one_build_and_one_generation(
    shared_over, monkeypatch, tmp_path
):
    from watchlists_bridge_app import login_session, make_app, reset_users

    from sudarshan.watchlists import contract

    box, shared = shared_over
    box.snap, _ = snapshot("R", stocks=200, strikes=100)
    monkeypatch.setenv(contract.DB_ENV, str(tmp_path / "wl.db"))
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    builds = shared.builds
    inside = threading.Barrier(2, timeout=30)
    seen = []

    def strikes(args):  # a read op over the master, as the strikes and search ops will be
        ix = master.shared().index()
        inside.wait()  # both requests are in flight here, each holding what it was handed
        seen.append((id(ix), master.generations_alive()))
        inside.wait()
        return {"strikes": len(ix.options)}

    reset_users()
    app = make_app({"strikes": strikes})
    answers = []

    def ask():
        client = app.test_client()
        login_session(client)
        answers.append(client.get(contract.API_PREFIX + "/strikes?u=RU&x=NFO").status_code)

    requests = [threading.Thread(target=ask) for _ in range(2)]
    for t in requests:
        t.start()
    for t in requests:
        t.join(60)
    assert answers == [200, 200]
    assert shared.builds == builds + 1, "each request built the maps"
    assert len({ix for ix, _ in seen}) == 1, "the requests were handed different maps"
    assert [alive for _, alive in seen] == [1, 1], (
        "a request in flight held a generation of its own"
    )


def test_the_generation_goes_when_openalgo_publishes_the_next_snapshot_without_a_call(
    shared_over,
):
    """Not at the next request: the moment the old snapshot is freed, its rows go with it."""
    box, shared = shared_over
    old, old_rows = snapshot("A")
    box.snap = old
    assert shared.generation() is not None
    row = weakref.ref(old_rows[0])
    box.snap, _ = snapshot("B")  # OpenAlgo publishes the next snapshot ...
    del old, old_rows  # ... and nothing else holds the old one
    gc.collect()
    assert master.generations_alive() == 0, "the old generation outlived its snapshot"
    assert row() is None, "the maps kept the old snapshot's rows alive"
    assert shared.generation() is not None and master.generations_alive() == 1


def test_a_snapshot_freed_while_a_request_holds_its_maps_is_dropped_from_the_master(shared_over):
    box, shared = shared_over
    old, _ = snapshot("A")
    box.snap = old
    held = shared.generation()  # a request in flight
    box.snap, _ = snapshot("B")
    del old
    gc.collect()
    assert held.index is not None  # the request still finishes with what it was handed
    assert shared._gen is None  # but the master no longer offers it
    del held
    gc.collect()
    assert master.generations_alive() == 0


EVENTLET_BUILD = """
import eventlet
eventlet.monkey_patch()
import os, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ.update(API_KEY_PEPPER="f" * 64, APP_KEY="test-only-app-key-" + "0" * 32,
                  DATABASE_URL="sqlite:///" + tmp + "/oa.db", LOG_DIR=tmp + "/log")
sys.path[:0] = [{root!r}, {tests!r}]
from test_exception import snapshot
from sudarshan.watchlists import master
snap, _ = snapshot("E", stocks=40000, strikes=10)
master.openalgo_snapshot = lambda: snap
m = master.shared()
if {silenced}:
    m._pause = lambda s: None
ticks, during = [0], []
def other():
    while True:
        ticks[0] += 1
        eventlet.sleep(0)
build = m._build
def counted(s):  # how often the other greenlet ran while the maps were being built
    start = ticks[0]
    try:
        return build(s)
    finally:
        during.append(ticks[0] - start)
m._build = counted
eventlet.spawn(other)
eventlet.sleep(0)
pile = [eventlet.spawn(m.generation) for _ in range(2)]
got = [p.wait() for p in pile]
print("RESULT", m.builds, len({{id(g) for g in got}}), sum(during))
"""


def run_build_under_eventlet(silenced: bool) -> tuple[int, int, int]:
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(EVENTLET_BUILD).format(
        root=str(ROOT), tests=str(Path(__file__).parent), silenced=silenced
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120, cwd=ROOT)  # fmt: skip
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line is not None, done.stderr[-800:]
    _, builds, generations, ticks = line.split()
    return int(builds), int(generations), int(ticks)


def test_under_eventlet_two_greenlets_share_one_build_that_yields_to_the_hub():
    pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    builds, generations, ticks = run_build_under_eventlet(silenced=False)
    assert (builds, generations) == (1, 1) and ticks >= 8
    # the control: with the yield taken out, nothing else runs during the build
    builds, generations, ticks = run_build_under_eventlet(silenced=True)
    assert (builds, generations, ticks) == (1, 1, 0)
