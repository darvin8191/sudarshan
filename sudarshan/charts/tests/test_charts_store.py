"""The chart store (M4.18, T7a): the file made on first use, the revision-checked write, the
bounds on a body and a key, the limits per kind, the compare-and-set under real threads, and the
descriptors it leaves behind (none)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from contextlib import closing

import pytest
from sqlalchemy.exc import IntegrityError

from sudarshan.charts import schema, store
from sudarshan.tests.fdwatch import FdWatch

OWNER = "owner"
LEVEL = {"version": 2, "drawings": [{"id": "d1", "tool": "horizontal-line"}]}


def text_of(body) -> str:
    return store.check_body("drawings", body)


def nest(levels: int) -> list:
    """A list nested ``levels`` deep: 1 is ``[]``, 2 is ``[[]]``."""
    value: list = []
    for _ in range(levels - 1):
        value = [value]
    return value


# -- the file ------------------------------------------------------------------------------------
def test_the_file_is_made_on_first_use_with_schema_one_and_nothing_seeded(chart_db):
    assert not chart_db.exists()
    assert store.read(OWNER, "drawings", "NSE:INFY") is None
    with closing(sqlite3.connect(chart_db)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert names == {"chart_doc", "chart_meta"}
        assert db.execute("SELECT name, value FROM chart_meta").fetchall() == [("schema", "1")]
        assert db.execute("SELECT count(*) FROM chart_doc").fetchone() == (0,)


def test_a_newer_file_is_refused_untouched_and_in_words(chart_db):
    store.read(OWNER, "drawings", "NSE:INFY")
    store.dispose()
    with closing(sqlite3.connect(chart_db)) as db, db:
        db.execute("UPDATE chart_meta SET value = '2' WHERE name = 'schema'")
    before = chart_db.read_bytes()
    with pytest.raises(store.RecordTooNew) as refused:
        store.read(OWNER, "drawings", "NSE:INFY")
    assert "newer release" in str(refused.value)
    assert chart_db.read_bytes() == before


def test_a_file_that_cannot_be_opened_is_answered_in_words(chart_db):
    chart_db.parent.mkdir(parents=True)
    chart_db.write_bytes(b"not a database at all" * 100)
    with pytest.raises(store.RecordUnreadable) as refused:
        store.read(OWNER, "drawings", "NSE:INFY")
    assert "could not be opened" in str(refused.value)


# -- reads and writes ------------------------------------------------------------------------------
def test_a_new_document_is_kept_and_read_back_as_written(chart_db):
    saved = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL), at=1_794_000_000_000)
    assert (saved.revision, saved.updated_ms, saved.body) == (1_794_000_000_000,) * 2 + (None,)
    got = store.read(OWNER, "drawings", "NSE:INFY")
    assert got.revision == saved.revision and json.loads(got.body) == LEVEL


def test_have_leaves_the_body_out_only_when_it_is_the_revision_held(chart_db):
    saved = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL))
    assert store.read(OWNER, "drawings", "NSE:INFY", have=saved.revision).body is None
    assert store.read(OWNER, "drawings", "NSE:INFY", have=saved.revision - 1).body is not None


def test_a_write_from_the_revision_held_replaces_it(chart_db):
    first = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL), at=1_000)
    moved = {"version": 2, "drawings": []}
    second = store.write(OWNER, "drawings", "NSE:INFY", first.revision, text_of(moved), at=2_000)
    assert second.revision == 2_000
    assert json.loads(store.read(OWNER, "drawings", "NSE:INFY").body) == moved


def test_a_write_from_a_revision_gone_is_stale_with_the_newer_document_and_changes_nothing(
    chart_db,
):
    first = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL), at=1_000)
    newer = {"version": 2, "drawings": [{"id": "d2", "tool": "ray"}]}
    second = store.write(OWNER, "drawings", "NSE:INFY", first.revision, text_of(newer), at=2_000)
    with pytest.raises(store.Stale) as stale:
        store.write(OWNER, "drawings", "NSE:INFY", first.revision, text_of(LEVEL), at=3_000)
    assert stale.value.current.revision == second.revision
    assert json.loads(stale.value.current.body) == newer
    assert json.loads(store.read(OWNER, "drawings", "NSE:INFY").body) == newer


def test_a_new_document_where_one_exists_is_stale(chart_db):
    first = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL))
    with pytest.raises(store.Stale) as stale:
        store.write(OWNER, "drawings", "NSE:INFY", 0, text_of({"version": 2, "drawings": []}))
    assert stale.value.current.revision == first.revision
    assert json.loads(stale.value.current.body) == LEVEL


def test_a_revision_named_for_a_document_the_server_has_not_is_stale_with_none(chart_db):
    with pytest.raises(store.Stale) as stale:
        store.write(OWNER, "drawings", "NSE:INFY", 1_000, text_of(LEVEL))
    assert stale.value.current is None
    assert store.read(OWNER, "drawings", "NSE:INFY") is None


def test_a_revision_never_moves_back_and_is_never_below_the_writes_time(chart_db):
    first = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL), at=5_000)
    late_clock = store.write(OWNER, "drawings", "NSE:INFY", first.revision, text_of(LEVEL), at=10)
    assert late_clock.revision == 5_001  # a clock that went back still moves the revision on
    later = store.write(
        OWNER, "drawings", "NSE:INFY", late_clock.revision, text_of(LEVEL), at=9_000
    )
    assert later.revision == 9_000


def test_a_file_put_back_by_a_rollback_never_repeats_a_revision_a_device_holds(chart_db, tmp_path):
    first = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL), at=1_000)
    store.dispose()
    backup = tmp_path / "backup.db"
    shutil.copyfile(chart_db, backup)  # the deploy's backup, taken after the first write
    held = store.write(OWNER, "drawings", "NSE:INFY", first.revision, text_of(LEVEL), at=2_000)
    store.dispose()
    shutil.copyfile(backup, chart_db)  # the rollback puts it back
    other = {"version": 2, "drawings": []}
    after = store.write(OWNER, "drawings", "NSE:INFY", first.revision, text_of(other), at=3_000)
    assert after.revision != held.revision and after.revision > held.revision


def test_each_user_kind_and_key_is_a_document_of_its_own(chart_db):
    a = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL))
    store.write("someone-else", "drawings", "NSE:INFY", 0, text_of({"version": 2, "drawings": []}))
    store.write(OWNER, "drawings", "NSE:TCS", 0, text_of({"version": 3, "drawings": []}))
    assert store.read(OWNER, "drawings", "NSE:INFY").revision == a.revision
    assert json.loads(store.read(OWNER, "drawings", "NSE:INFY").body) == LEVEL
    assert store.read(OWNER, "phone_set", "NSE:INFY") is None


def test_a_new_key_past_its_kinds_limit_is_refused_and_a_kept_one_still_saves(
    chart_db, monkeypatch
):
    monkeypatch.setitem(store.LIMITS, "drawings", 2)
    a = store.write(OWNER, "drawings", "NSE:A", 0, text_of(LEVEL))
    store.write(OWNER, "drawings", "NSE:B", 0, text_of(LEVEL))
    with pytest.raises(store.Full):
        store.write(OWNER, "drawings", "NSE:C", 0, text_of(LEVEL))
    assert store.read(OWNER, "drawings", "NSE:C") is None
    store.write(OWNER, "drawings", "NSE:A", a.revision, text_of({"version": 2, "drawings": []}))
    empty_set = store.check_body("phone_set", {"version": 1, "studies": []})
    store.write(OWNER, "phone_set", "phone", 0, empty_set)  # another kind counts on its own


def test_the_file_itself_refuses_a_body_past_the_limit(chart_db):
    """A writer that skipped ``check_body`` still cannot store a body past 5 MB: the file holds
    the check too, and its refusal is not mistaken for another device's write."""
    huge = '{"version":1,"s":"' + "a" * schema.MAX_BODY_BYTES + '"}'
    with pytest.raises(IntegrityError):
        store.write(OWNER, "template", "t", 0, huge)
    kept = store.write(OWNER, "template", "t", 0, '{"version":1}')
    with pytest.raises(IntegrityError):
        store.write(OWNER, "template", "t", kept.revision, huge)


# -- what a body and a key may be ----------------------------------------------------------------
def test_a_body_is_stored_as_compact_json_text():
    assert store.check_body("drawings", {"version": 2, "drawings": [], "x": "é"}) == (
        '{"version":2,"drawings":[],"x":"é"}'
    )


def test_a_body_at_the_engines_byte_limit_is_taken_and_one_byte_more_is_too_big():
    empty = len('{"version":1,"s":""}')
    exact = {"version": 1, "s": "a" * (schema.MAX_BODY_BYTES - empty)}
    assert len(store.check_body("template", exact).encode()) == schema.MAX_BODY_BYTES
    with pytest.raises(store.TooBig):
        store.check_body("template", {"version": 1, "s": "a" * (schema.MAX_BODY_BYTES - empty + 1)})
    wide = {"version": 1, "s": "é" * ((schema.MAX_BODY_BYTES - empty) // 2 + 1)}  # bytes, not chars
    with pytest.raises(store.TooBig):
        store.check_body("template", wide)


def test_nesting_is_taken_to_the_engines_depth_and_refused_past_it():
    assert store.check_body("template", {"version": 1, "n": nest(store.MAX_DEPTH)})
    with pytest.raises(store.Invalid):
        store.check_body("template", {"version": 1, "n": nest(store.MAX_DEPTH + 1)})
    with pytest.raises(store.Invalid):  # far past it: walked without recursion, no stack error
        store.check_body("template", {"version": 1, "n": nest(50_000)})


def test_values_are_taken_to_the_engines_count_and_refused_past_it():
    fits = {"version": 1, "n": [0] * (store.MAX_NODES - 3)}  # the record, version and list count
    assert store.check_body("template", fits)
    with pytest.raises(store.Invalid):
        store.check_body("template", {"version": 1, "n": [0] * (store.MAX_NODES - 2)})


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_a_number_that_is_not_finite_is_refused(number):
    with pytest.raises(store.Invalid):
        store.check_body("template", {"version": 1, "n": [number]})


@pytest.mark.parametrize(
    "kind, body",
    [
        ("drawings", {"version": 2, "drawings": []}),
        ("drawings", {"version": 3, "drawings": [{"id": "a"}], "groups": []}),
        ("phone_set", {"version": 1, "studies": [{"id": "ema", "settings": {"length": 20}}]}),
        ("workspace", {"version": 1, "revision": 4, "workspaces": [], "templates": [],
                       "recentWorkspaceIds": [], "activeWorkspaceId": None, "autosave": False}),
        ("template", {"version": 1}),
    ],
)  # fmt: skip
def test_each_kinds_shape_is_taken(kind, body):
    assert json.loads(store.check_body(kind, body)) == body


@pytest.mark.parametrize(
    "kind, body",
    [
        ("drawings", []),
        ("drawings", {"version": 2}),
        ("drawings", {"version": True, "drawings": []}),
        ("drawings", {"version": 0, "drawings": []}),
        ("drawings", {"version": 2, "drawings": {}}),
        ("phone_set", {"version": 2, "studies": []}),
        ("phone_set", {"version": 1, "studies": [{"id": "", "settings": {}}]}),
        ("phone_set", {"version": 1, "studies": [{"id": "ema", "settings": []}]}),
        ("phone_set", {"version": 1, "studies": [{"id": "ema", "settings": {}}] * 9}),
        ("workspace", {"version": 1, "workspaces": [], "templates": []}),
        ("workspace", {"version": 1, "revision": 1, "workspaces": [{}] * 101, "templates": []}),
        ("workspace", {"version": 2, "revision": 1, "workspaces": [], "templates": []}),
        ("template", {"version": "1"}),
        ("template", "text"),
    ],
)
def test_a_body_not_in_its_kinds_shape_is_refused(kind, body):
    with pytest.raises(store.Invalid) as bad:
        store.check_body(kind, body)
    assert bad.value.field == "body"


@pytest.mark.parametrize(
    "key", ["NSE:INFY", "NSE_INDEX:NIFTY%2050", "phone", "oa-trading%3Aowner", "a" * 200]
)
def test_a_key_as_the_screens_build_it_is_taken(key):
    assert store.check_key(key) == key


@pytest.mark.parametrize("key", ["", " ", "NSE INFY", "a/b", "a\\b", 'a"b', "a" * 201, None, 7])
def test_any_other_key_is_refused(key):
    with pytest.raises(store.Invalid) as bad:
        store.check_key(key)
    assert bad.value.field == "key"


# -- under real threads ----------------------------------------------------------------------------
def _race(writes) -> list:
    """Run each write at the same moment on a real thread; what each one did."""
    barrier = threading.Barrier(len(writes))
    results: list = [None] * len(writes)

    def run(n, write):
        barrier.wait()
        try:
            results[n] = write()
        except store.Stale as stale:
            results[n] = stale

    threads = [threading.Thread(target=run, args=(n, w)) for n, w in enumerate(writes)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return results


@pytest.mark.parametrize("round_", range(10))
def test_two_writes_from_one_revision_never_both_land(chart_db, round_):
    first = store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL))
    bodies = [{"version": 2, "drawings": [{"id": f"from-{n}"}]} for n in range(2)]
    results = _race([
        lambda b=b: store.write(OWNER, "drawings", "NSE:INFY", first.revision, text_of(b))
        for b in bodies
    ])  # fmt: skip
    won = [r for r in results if isinstance(r, store.Doc)]
    lost = [r for r in results if isinstance(r, store.Stale)]
    assert len(won) == 1 and len(lost) == 1, results
    assert lost[0].current.revision == won[0].revision
    stored = store.read(OWNER, "drawings", "NSE:INFY")
    assert stored.revision == won[0].revision and json.loads(stored.body) == json.loads(
        lost[0].current.body
    )


@pytest.mark.parametrize("round_", range(10))
def test_two_new_documents_under_one_key_never_both_land(chart_db, round_):
    results = _race([
        lambda n=n: store.write(OWNER, "drawings", f"NSE:R{round_}", 0,
                                text_of({"version": 2, "drawings": [{"id": str(n)}]}))
        for n in range(2)
    ])  # fmt: skip
    assert sorted(type(r).__name__ for r in results) == ["Doc", "Stale"], results


def test_reads_and_writes_leave_no_descriptor_open(chart_db):
    store.write(OWNER, "drawings", "NSE:INFY", 0, text_of(LEVEL))  # the file and its engine
    watch = FdWatch()
    revision = store.read(OWNER, "drawings", "NSE:INFY").revision
    for n in range(200):
        revision = store.write(OWNER, "drawings", "NSE:INFY", revision, text_of(LEVEL)).revision
        store.read(OWNER, "drawings", "NSE:INFY", have=revision if n % 2 else None)
        with pytest.raises(store.Stale):
            store.write(OWNER, "drawings", "NSE:INFY", revision - 1, text_of(LEVEL))
    watch.assert_clean()
