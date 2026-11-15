"""T3: signals.db, its migrations and ``migrate --status`` (store.py, migrations.py, migrate.py)."""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import stat
import threading

import pytest

from sudarshan.signals import contract as c
from sudarshan.signals import diff, migrate, migrations
from sudarshan.signals.store import SignalsStore, StoreError, VersionConflict

AT = 1794800712250  # Mon 16 Nov 2026 09:15:12 IST (invented)


def doc(seq=1, at=AT, readings=None, alerts=None, **over) -> c.Snapshot:
    body = {
        "v": 1, "run": "r1", "seq": seq, "at": at, "next_by": at + 300_000,
        "signals": readings if readings is not None else [
            {"module": "regime", "instrument": "NIFTY", "state": "DISTRIBUTION",
             "label": "FII distribution", "bias": "bear", "confidence": 0.71, "as_of": at - 1000,
             "since": at - 86_400_000, "horizon": "swing", "quality": "ok",
             "why": {"en": "Invented.", "gu": "Invented too."}},
        ],
        "alerts": alerts if alerts is not None else [
            {"id": "a-1", "module": "regime", "instrument": "NIFTY", "level": "watch",
             "title": "Watch this", "text": "Invented.", "lang": "en"},
            {"id": "a-2", "module": "regime", "instrument": None, "level": "info",
             "title": "For the app", "text": "", "lang": None},
        ],
    }  # fmt: skip
    body.update(over)
    snap = c.read_document(json.dumps(body).encode(), now_ms=at + 500, written_ms=at)
    assert isinstance(snap, c.Snapshot), snap
    return snap


def accept(store: SignalsStore, snap: c.Snapshot, prior=None, gap=0, relay_on=True):
    out = diff.apply(prior or {}, snap, snap.at + 500)
    stored = store.accept(snap, name=f"{snap.at}-{snap.seq}.json", received_ms=snap.at + 500,
                          current=out.current, changes=out.changes, gap=gap,
                          relay_on=relay_on)  # fmt: skip
    return out, stored


@pytest.fixture
def store(tmp_path):
    made = SignalsStore(tmp_path / "state" / "signals.db")
    yield made
    made.close()


def test_a_new_file_is_private_migrated_and_seeded(tmp_path):
    path = tmp_path / "state" / "signals.db"
    made = SignalsStore(path)
    try:
        assert made.applied == [1]
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o750
        assert made.settings() == {"lang": "hi", "relay": True, "night": True, "version": 1}
        assert made.last() is None and made.current() == {}
    finally:
        made.close()
    again = SignalsStore(path)
    assert again.applied == [] and again.settings()["version"] == 1
    again.close()


def test_a_file_from_a_newer_release_is_refused_in_words(tmp_path):
    path = tmp_path / "signals.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {migrations.LATEST + 1}")
    with pytest.raises(migrations.SchemaTooNew) as caught:
        SignalsStore(path)
    assert str(caught.value) == c.WORDS["store_newer"] and caught.value.found == 2


def test_migrate_status_changes_nothing(tmp_path, capsys):
    path = tmp_path / "signals.db"
    assert migrate.main(["--status", "--path", str(path)]) == 0
    assert not path.exists()
    assert "version: file none, code 1" in capsys.readouterr().out
    SignalsStore(path).close()
    assert migrate.main(["--status", "--path", str(path)]) == 0
    assert "pending: none" in capsys.readouterr().out
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 9")
    assert migrate.main(["--status", "--path", str(path)]) == 2
    assert c.WORDS["store_newer"] in capsys.readouterr().out


def test_migrate_refuses_while_the_service_holds_its_port(tmp_path, capsys):
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        assert migrate.main(["--path", str(tmp_path / "x.db"), "--port", str(port)]) == 2
    assert "running" in capsys.readouterr().out and not (tmp_path / "x.db").exists()
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    assert migrate.main(["--path", str(tmp_path / "y.db"), "--port", str(port)]) == 0
    assert "applied: [1]" in capsys.readouterr().out


def test_an_accepted_document_is_one_transaction(store):
    out, stored = accept(store, doc())
    assert [ch["kind"] for ch in stored.changes] == ["first"]
    change = stored.changes[0]
    assert change["relay"] == "pending" and change["id"] == 1 and change["at_ms"] == AT
    assert [(a["id"], a["relay"]) for a in stored.alerts] == [("a-1", "pending"),
                                                              ("a-2", "skipped")]  # fmt: skip
    assert store.current() == out.current
    last = store.last()
    assert (last["seq"], last["published_ms"], last["fresh_until_ms"]) == (
        1, AT, AT + 360_000)  # fmt: skip
    # the same alert id again is ignored, a new one is kept; a gap is counted
    out2, stored2 = accept(store, doc(seq=4, alerts=[
        {"id": "a-1", "module": "regime", "instrument": "NIFTY", "level": "act", "title": "x",
         "text": "", "lang": None},
        {"id": "a-3", "module": "regime", "instrument": "NIFTY", "level": "act", "title": "y",
         "text": "", "lang": None}]), prior=out.current, gap=2)  # fmt: skip
    assert stored2.changes == [] and [a["id"] for a in stored2.alerts] == ["a-3"]
    assert store.counts(AT)["gaps"] == 2


def test_changes_with_the_relay_off_are_skipped(store):
    _, stored = accept(store, doc(), relay_on=False)
    assert {ch["relay"] for ch in stored.changes} == {"skipped"}
    assert {a["relay"] for a in stored.alerts} == {"skipped"}
    assert store.pending_relay() == ([], [])


def test_refused_files_and_counters(store):
    store.record("x.json", received_ms=AT, size=12, verdict="refused", reason="label",
                 counter="refused")  # fmt: skip
    store.record("y.json", received_ms=AT, size=12, verdict="refused", reason="json",
                 counter="refused")  # fmt: skip
    store.count("strangers", AT)
    got = store.counts(AT)
    assert got["refused"] == 2 and got["strangers"] == 1 and got["gaps"] == 0
    assert store.counts(AT + 86_400_000)["refused"] == 0  # a new IST day starts again at 0


def test_the_timeline_pages_newest_first(store):
    prior = {}
    for n in range(1, 121):
        state = "UP" if n % 2 else "DOWN"
        snap = doc(seq=n, at=AT + n * 1000, alerts=[], readings=[
            {"module": "regime", "instrument": "NIFTY" if n % 3 else "BANKNIFTY", "state": state,
             "label": state.title(), "bias": "bull" if state == "UP" else "bear",
             "as_of": AT, "horizon": "swing", "quality": "ok"}])  # fmt: skip
        prior = accept(store, snap, prior)[0].current
    page = store.timeline()
    assert len(page["changes"]) == 50 and page["changes"][0]["id"] > page["changes"][-1]["id"]
    older = store.timeline(before=page["next_before"])
    assert older["changes"][0]["id"] == page["changes"][-1]["id"] - 1
    everything, before = [], None
    while True:
        got = store.timeline(before=before, limit=200)
        everything += got["changes"]
        before = got["next_before"]
        if before is None:
            break
    assert len(everything) == len({row["id"] for row in everything})
    nifty = store.timeline(instrument="NIFTY", limit=200)["changes"]
    assert nifty and {row["instrument"] for row in nifty} == {"NIFTY"}
    recent = store.timeline(after_ms=AT + 100_000, limit=200)["changes"]
    assert recent and min(row["at_ms"] for row in recent) >= AT + 100_000
    one = store.change_get(everything[-1]["id"])
    assert one == everything[-1] and set(one) == {f.name for f in c.record("Change")}
    assert store.change_get(10_000) is None


def test_settings_change_only_from_the_version_they_were_read_at(store):
    new = store.settings_set(1, {"lang": "gu", "relay": False})
    assert new == {"lang": "gu", "relay": False, "night": True, "version": 2}
    with pytest.raises(VersionConflict) as caught:
        store.settings_set(1, {"lang": "en"})
    assert str(caught.value) == c.WORDS["version_conflict"] and store.settings() == new


def test_the_relays_seam(store):
    _, stored = accept(store, doc())
    changes, alerts = store.pending_relay()
    assert [ch["id"] for ch in changes] == [1] and [a["id"] for a in alerts] == ["a-1"]
    assert store.mark_relay("change", [1], "handed") == 1
    assert store.mark_relay("alert", ["a-1"], "expired") == 1
    assert store.pending_relay() == ([], [])
    with pytest.raises(ValueError):
        store.mark_relay("doc", [1], "handed")


def test_lapses_are_stored_never_relayed(store):
    snap = doc(readings=[
        {"module": "gap_bias", "instrument": "NIFTY", "state": "GAP_UP", "label": "Gap up",
         "bias": "bull", "as_of": AT, "valid_until": AT + 60_000, "horizon": "open",
         "quality": "ok"}], alerts=[])  # fmt: skip
    out, _ = accept(store, snap)
    current, changes = diff.lapses(out.current, AT + 60_000)
    made = store.store_lapses(current, changes, AT + 61_000)
    assert [(row["kind"], row["relay"]) for row in made] == [("lapsed", "skipped")]
    assert store.current()[("gap_bias", "NIFTY")].lapsed


def test_state_at_answers_only_inside_a_fresh_window(store):
    out, _ = accept(store, doc())
    got = store.state_at(AT + 1000)
    assert got["fresh"] and got["readings"] == [{
        "module": "regime", "instrument": "NIFTY", "state": "DISTRIBUTION",
        "label": "FII distribution", "bias": "bear", "confidence": 0.71,
        "since_ms": AT - 86_400_000, "lapsed": False}]  # fmt: skip
    assert set(got["readings"][0]) == {f.name for f in c.record("ReadingAt")}
    assert store.state_at(AT - 1) == {"ts_ms": AT - 1, "fresh": False, "readings": []}
    late = AT + 360_000
    assert store.state_at(late) == {"ts_ms": late, "fresh": False, "readings": []}


def test_prune_keeps_the_timeline(store):
    accept(store, doc())
    store.record("old.json", received_ms=AT, size=1, verdict="refused", counter="refused")
    gone = store.prune(AT + 31 * 86_400_000)
    assert gone["docs"] == 2 and store.timeline()["changes"]
    assert store.prune(AT + 41 * 86_400_000)["counters"] >= 1


def test_the_store_thread_ends_with_the_store(tmp_path):
    made = SignalsStore(tmp_path / "s.db")

    async def use():
        return await made.call(made.settings)

    assert asyncio.run(use())["lang"] == "hi"
    assert any(t.name.startswith("signals-store") for t in threading.enumerate())
    made.close()
    assert not any(t.name.startswith("signals-store") for t in threading.enumerate())


def test_a_store_that_cannot_be_made_says_so(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StoreError) as caught:
        SignalsStore(blocker / "signals.db")
    assert str(caught.value) == c.WORDS["store_failed"]
