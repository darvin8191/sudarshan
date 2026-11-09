"""The dashboard in screener.db (M9.16, B1): step 4, and the store's dashboard and widget methods
(dashboards design 3.3 and 3.4). Widgets are addressed by (screen_id, kind), never by id; every
write names the dashboard's version and moves it or writes nothing; version 0 makes the dashboard
from the suggestion, inside the same transaction, as the owner's first change."""

from __future__ import annotations

import ast
import inspect
import re

import pytest

from sudarshan.screener import migrate, migrations, store
from sudarshan.screener.store import (
    OWNER,
    NotFound,
    ScreenerStore,
    Tx,
    VersionConflict,
    WidgetRefused,
)

NOW = 1_761_600_000_000
LATER = NOW + 60_000
SPEC = {"schema": 1, "name": "Breakout", "universe": {"kind": "fno_stocks"}, "where": []}


@pytest.fixture
def opened(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db")
    yield made
    made.close()
    store.stop()


def _screens(made, count, status="active", who=OWNER):
    def go(tx):
        return [tx.screen_insert(f"S{i}", SPEC, status, NOW)["id"] for i in range(count)]

    return made.write(who, go)


def _keys(made, who=OWNER):
    return [(w["screen_id"], w["kind"]) for w in made.read(who, Tx.widgets)]


def _write(made, fn, who=OWNER):
    return made.write(who, fn)


def _insert(made, version, screen_id, kind="table", who=OWNER, **extra):
    fields = {"screen_id": screen_id, "kind": kind, **extra}
    return made.write(who, lambda tx: tx.widget_insert(version, fields, LATER))


# ---- step 4 --------------------------------------------------------------------------------
def test_step_four_is_idempotent_ddl_without_check_constraints():
    for statement in migrations.V4:
        head = " ".join(statement.split()[:6])
        assert "IF NOT EXISTS" in head, head
        assert "CHECK" not in statement.upper()
    assert migrations.STEPS[-1][0] == migrations.LATEST == 4
    assert {"dashboard", "dashboard_widget"} <= set(migrations.OWNER_TABLES)
    assert {"sort_by", "value_clauses"} <= store.JSON_COLUMNS


def test_a_fresh_file_applies_one_to_four_and_opened_again_none(tmp_path):
    path = tmp_path / "screener.db"
    first = ScreenerStore(path)
    try:
        assert first.applied == [1, 2, 3, 4]
        with first.engine.connect() as conn:
            assert {"dashboard", "dashboard_widget"} <= set(store.tables_of(conn))
            assert migrations.version(conn) == 4
    finally:
        first.close()
    second = ScreenerStore(path)
    try:
        assert second.applied == []
    finally:
        second.close()
        store.stop()


def _populated_schema_three(path):
    """A file as release 3 left it: screens, runs, memberships and keyed pauses, no dashboard."""
    made = ScreenerStore(path)

    def fill(tx):
        sid = tx.screen_insert("Old", SPEC, "active", NOW)["id"]
        tx.run_insert(
            {
                "screen_id": sid, "screen_version": 1, "run_key": f"{sid}:v1:20251028:15:a",
                "session": 20251028, "candle_start": NOW - 1, "candle_end": NOW, "ran_at": NOW,
                "matched": 1, "entered": 1, "exited": 0, "missing": 0, "not_checked": 0,
                "marks": None, "status": "done", "engine_version": 1,
            }
        )  # fmt: skip
        tx.membership_put(
            sid,
            7,
            {"key": "NSE_EQ|A", "symbol": "A", "exchange": "NSE", "status": "active",
             "first_seen_at": NOW, "last_seen_at": NOW},
            NOW,
        )  # fmt: skip
        tx.pause_open(20251028, NOW, "feed", key="NSE_EQ|A", end=NOW + 5)
        tx.pause_open(20251028, NOW, "feed")

    made.write(OWNER, fill)
    with made.writer.begin() as conn:
        conn.exec_driver_sql("DROP TABLE dashboard_widget")
        conn.exec_driver_sql("DROP TABLE dashboard")
        conn.exec_driver_sql("PRAGMA user_version = 3")
    made.close()
    store.stop()


def _counts(path):
    made = ScreenerStore(path)
    try:
        with made.engine.connect() as conn:
            return {
                name: conn.exec_driver_sql(f"SELECT count(*) FROM {name}").scalar()
                for name in ("screen", "run", "membership", "pause")
            }
    finally:
        made.close()
        store.stop()


def test_a_populated_schema_three_file_applies_only_step_four_and_keeps_every_row(tmp_path):
    path = tmp_path / "screener.db"
    _populated_schema_three(path)
    again = ScreenerStore(path)
    try:
        assert again.applied == [4]
        with again.engine.connect() as conn:
            kept = {
                name: conn.exec_driver_sql(f"SELECT count(*) FROM {name}").scalar()
                for name in ("screen", "run", "membership", "pause")
            }
            keyed = conn.exec_driver_sql("SELECT count(*) FROM pause WHERE key IS NOT NULL")
            assert keyed.scalar() == 1
        assert kept == {"screen": 1, "run": 1, "membership": 1, "pause": 2}
        # set back to 3 with the tables present: step 4 runs again without error and loses nothing
        again.write(OWNER, lambda tx: tx.widget_insert(0, {"screen_id": 1, "kind": "count"}, NOW))
        with again.writer.begin() as conn:
            conn.exec_driver_sql("PRAGMA user_version = 3")
    finally:
        again.close()
        store.stop()
    third = ScreenerStore(path)
    try:
        assert third.applied == [4]
        assert [(w["screen_id"], w["kind"]) for w in third.read(OWNER, Tx.widgets)] == [
            (1, "table"),
            (1, "count"),
        ]
        assert third.read(OWNER, Tx.dashboard)["version"] == 2
    finally:
        third.close()
        store.stop()
    assert _counts(path) == {"screen": 1, "run": 1, "membership": 1, "pause": 2}


def test_status_on_a_schema_three_file_names_step_four_and_leaves_its_bytes_alone(tmp_path, capsys):
    path = tmp_path / "screener.db"
    _populated_schema_three(path)
    before = path.read_bytes()
    assert migrate.main(["--status", "--path", str(path)]) == 1
    assert "pending steps: 4" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert not list(path.parent.glob("screener.db-*")), "--status left a journal behind"


def test_a_file_from_a_newer_release_is_refused(tmp_path):
    path = tmp_path / "screener.db"
    made = ScreenerStore(path)
    with made.writer.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 5")
    made.close()
    with pytest.raises(migrations.SchemaTooNew) as caught:
        ScreenerStore(path)
    assert caught.value.found == 5 and caught.value.known == 4
    assert not re.search(r"[A-Z][a-z]+Error|sqlite|version \d", str(caught.value))
    store.stop()


def test_the_new_methods_are_held_to_the_principal_filter():
    tree = ast.parse(inspect.getsource(Tx))
    seen = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            tables = {
                sub.args[0].value
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "_t"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
            }
            if tables & {"dashboard", "dashboard_widget"}:
                text = ast.unparse(node)
                seen[node.name] = "_mine" in text or "principal" in text
    assert set(seen) >= {
        "dashboard", "widgets", "widgets_of_screen_delete",
        "widget_change", "widget_delete", "widgets_order",
    }  # fmt: skip
    assert all(seen.values()), seen


# ---- the suggestion and version 0 ----------------------------------------------------------
def test_with_no_row_the_layout_is_a_suggestion_of_active_screens_newest_first(opened):
    ids = _screens(opened, 3)
    paused = _screens(opened, 1, "paused")
    layout = opened.read(OWNER, Tx.layout)
    assert layout["suggested"] is True and layout["version"] == 0
    assert [w["screen_id"] for w in layout["widgets"]] == ids[::-1]
    assert paused[0] not in [w["screen_id"] for w in layout["widgets"]]
    assert [w["position"] for w in layout["widgets"]] == [0, 1, 2]
    assert all(
        w["kind"] == "table" and w["rows"] == 10 and w["sort_by"] is None
        and w["value_clauses"] is None
        for w in layout["widgets"]
    )  # fmt: skip
    assert opened.read(OWNER, Tx.dashboard) is None and _keys(opened) == []


def test_the_suggestion_holds_at_most_twenty_the_newest(opened):
    ids = _screens(opened, 25)
    shown = [w["screen_id"] for w in opened.read(OWNER, Tx.suggestion)]
    assert shown == ids[::-1][:20]


def test_the_first_change_at_version_zero_makes_the_dashboard_from_the_suggestion(opened):
    a, b, c = _screens(opened, 3)
    paused = _screens(opened, 1, "paused")[0]
    row = _insert(opened, 0, paused, "count", rows=5)
    assert row["version"] == 2 and row["name"] == store.DASHBOARD_NAME
    assert _keys(opened) == [(c, "table"), (b, "table"), (a, "table"), (paused, "count")]
    widget = opened.read(OWNER, Tx.widgets)[-1]
    assert widget["rows"] == 5 and widget["position"] == 3
    assert opened.read(OWNER, Tx.layout)["suggested"] is False


def test_a_move_at_version_zero_keeps_the_rest_of_what_he_was_looking_at(opened):
    a, b, c = _screens(opened, 3)
    opened.write(
        OWNER, lambda tx: tx.widgets_order(0, [(a, "table"), (b, "table"), (c, "table")], LATER)
    )
    assert _keys(opened) == [(a, "table"), (b, "table"), (c, "table")]


def test_a_change_and_a_delete_at_version_zero_start_from_the_suggestion_too(opened):
    a, b = _screens(opened, 2)
    opened.write(OWNER, lambda tx: tx.widget_change(0, a, "table", {"rows": 20}, LATER))
    assert [(w["screen_id"], w["rows"]) for w in opened.read(OWNER, Tx.widgets)] == [
        (b, 10),
        (a, 20),
    ]
    other = ScreenerStore(opened.path.parent / "other.db")
    try:
        x, y = _screens(other, 2)
        other.write(OWNER, lambda tx: tx.widget_delete(0, y, "table", LATER))
        assert _keys(other) == [(x, "table")]
        assert other.read(OWNER, Tx.dashboard)["version"] == 2
    finally:
        other.close()


def test_the_second_first_change_is_told_and_writes_nothing(opened):
    a, b = _screens(opened, 2)
    first = _insert(opened, 0, a, "count")
    assert first["version"] == 2
    with pytest.raises(VersionConflict):
        _insert(opened, 0, b, "count")
    with pytest.raises(VersionConflict):
        opened.write(OWNER, lambda tx: tx.widget_delete(0, a, "table", LATER))
    with pytest.raises(VersionConflict):
        opened.write(OWNER, lambda tx: tx.dashboard_create("Dashboard", [], LATER))
    assert opened.read(OWNER, Tx.dashboard)["version"] == 2
    assert (b, "count") not in _keys(opened)


def test_a_refused_first_change_leaves_no_dashboard_row_behind(opened):
    a = _screens(opened, 1)[0]
    with pytest.raises(WidgetRefused):
        _insert(opened, 0, a, "table")  # already in the suggestion
    with pytest.raises(NotFound):
        _insert(opened, 0, 999, "table")
    with pytest.raises(NotFound):
        opened.write(OWNER, lambda tx: tx.widget_delete(0, 999, "table", LATER))
    with pytest.raises(VersionConflict):
        opened.write(OWNER, lambda tx: tx.widgets_order(0, [(999, "table")], LATER))
    assert opened.read(OWNER, Tx.dashboard) is None


def test_two_phones_at_version_zero_one_wins_and_the_other_is_told(opened):
    import threading

    a, b = _screens(opened, 2)
    outcomes = []
    barrier = threading.Barrier(2)

    def phone(screen_id):
        barrier.wait()
        try:
            _insert(opened, 0, screen_id, "count")
            outcomes.append("ok")
        except VersionConflict:
            outcomes.append("told")

    threads = [threading.Thread(target=phone, args=(s,)) for s in (a, b)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["ok", "told"]
    assert len(opened.read(OWNER, Tx.widgets)) == 3


# ---- versions ------------------------------------------------------------------------------
def test_every_write_moves_the_version_and_a_stale_one_writes_nothing(opened):
    a, b, c = _screens(opened, 3)
    one = _insert(opened, 0, a, "count")["version"]
    two = opened.write(OWNER, lambda tx: tx.widget_change(one, a, "count", {"rows": 5}, LATER))
    assert (one, two["version"]) == (2, 3)
    three = opened.write(OWNER, lambda tx: tx.widget_delete(3, a, "count", LATER))["version"]
    before = opened.read(OWNER, Tx.widgets)
    for stale in (one, two["version"], three + 1, 99):
        with pytest.raises(VersionConflict):
            _insert(opened, stale, b, "count")
        with pytest.raises(VersionConflict):
            opened.write(
                OWNER,
                lambda tx, stale=stale: tx.widget_change(stale, c, "table", {"rows": 20}, LATER),
            )
        with pytest.raises(VersionConflict):
            opened.write(OWNER, lambda tx, stale=stale: tx.widget_delete(stale, c, "table", LATER))
        with pytest.raises(VersionConflict):
            opened.write(
                OWNER,
                lambda tx, stale=stale: tx.widgets_order(
                    stale, [(w["screen_id"], w["kind"]) for w in before], LATER
                ),
            )
    assert opened.read(OWNER, Tx.widgets) == before
    assert opened.read(OWNER, Tx.dashboard)["version"] == three


def test_a_widget_is_addressed_by_screen_and_kind_and_its_id_stays_inside(opened):
    a = _screens(opened, 1)[0]
    _insert(
        opened, 0, a, "count", sort_by={"by": "ltp", "order": "desc"}, value_clauses=["c1", "c2"]
    )
    widget = opened.read(OWNER, Tx.widgets)[-1]
    assert set(widget) == {
        "screen_id", "kind", "position", "rows", "sort_by", "value_clauses",
        "created_at", "updated_at",
    }  # fmt: skip
    assert widget["sort_by"] == {"by": "ltp", "order": "desc"}
    assert widget["value_clauses"] == ["c1", "c2"]
    assert "id" not in opened.read(OWNER, Tx.layout)["widgets"][0]


# ---- limits --------------------------------------------------------------------------------
def test_a_twenty_first_widget_is_refused_with_its_sentence(opened):
    ids = _screens(opened, 20)
    row = opened.write(OWNER, lambda tx: tx.dashboard_create(
        "Dashboard", [{"screen_id": i} for i in ids], NOW))  # fmt: skip
    extra = _screens(opened, 1)[0]
    with pytest.raises(WidgetRefused) as caught:
        _insert(opened, row["version"], extra)
    assert "20 widgets" in str(caught.value) and not re.search(
        r"[A-Z][a-z]+Error", str(caught.value)
    )
    with pytest.raises(WidgetRefused):
        _insert(opened, row["version"], ids[0], "count")  # still the 21st
    assert len(opened.read(OWNER, Tx.widgets)) == 20
    assert opened.read(OWNER, Tx.dashboard)["version"] == row["version"]


def test_the_suggestion_at_twenty_refuses_a_twenty_first_on_the_first_change(opened):
    _screens(opened, 20)
    extra = _screens(opened, 1, "paused")[0]
    with pytest.raises(WidgetRefused):
        _insert(opened, 0, extra, "count")
    assert opened.read(OWNER, Tx.dashboard) is None


def test_a_screen_twice_for_one_kind_is_refused_and_one_of_each_kind_is_allowed(opened):
    a = _screens(opened, 1)[0]
    row = _insert(opened, 0, a, "count")
    assert (a, "table") in _keys(opened) and (a, "count") in _keys(opened)
    with pytest.raises(WidgetRefused) as caught:
        _insert(opened, row["version"], a, "count")
    assert "already on the dashboard as a count" in str(caught.value)
    # changing a table into a count that already exists is the same refusal
    with pytest.raises(WidgetRefused):
        opened.write(
            OWNER, lambda tx: tx.widget_change(row["version"], a, "table", {"kind": "count"}, LATER)
        )
    assert opened.read(OWNER, Tx.dashboard)["version"] == row["version"]


def test_a_widget_may_change_its_kind_rows_sort_and_values(opened):
    a = _screens(opened, 1)[0]
    row = opened.write(OWNER, lambda tx: tx.widget_change(
        0, a, "table", {"kind": "count", "rows": 5, "sort_by": {"by": "value", "order": "asc", "clause": "c9"},
                        "value_clauses": ["c9"]}, LATER))  # fmt: skip
    widget = opened.read(OWNER, Tx.widgets)[0]
    assert (widget["kind"], widget["rows"]) == ("count", 5)
    assert widget["sort_by"]["clause"] == "c9" and widget["value_clauses"] == ["c9"]
    assert row["version"] == 2 and widget["updated_at"] == LATER
    opened.write(
        OWNER,
        lambda tx: tx.widget_change(2, a, "count", {"sort_by": None, "value_clauses": None}, LATER),
    )
    assert opened.read(OWNER, Tx.widgets)[0]["sort_by"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "chart"},
        {"rows": 7},
        {"rows": True},
        {"rows": "10"},
        {"sort_by": {"by": "name", "order": "asc"}},
        {"sort_by": {"by": "ltp", "order": "up"}},
        {"sort_by": {"by": "ltp", "order": "asc", "clause": "c1"}},
        {"sort_by": {"by": "value", "order": "asc"}},
        {"sort_by": {"by": "ltp", "order": "asc", "extra": 1}},
        {"sort_by": "ltp"},
        {"value_clauses": ["a", "b", "c"]},
        {"value_clauses": ["a", "a"]},
        {"value_clauses": [1]},
        {"value_clauses": "a"},
        {"colour": "red"},
        {"screen_id": 99},
    ],
)
def test_kind_rows_sort_and_values_are_checked_in_code(opened, fields):
    a = _screens(opened, 1)[0]
    with pytest.raises(ValueError):
        opened.write(OWNER, lambda tx: tx.widget_change(0, a, "table", fields, LATER))
    assert opened.read(OWNER, Tx.dashboard) is None


def test_every_row_count_and_sort_the_design_names_is_accepted(opened):
    a = _screens(opened, 1)[0]
    version = 0
    for rows in (5, 10, 20):
        for by in ("change_pct", "ltp", "entered"):
            for order in ("asc", "desc"):
                fields = {"rows": rows, "sort_by": {"by": by, "order": order}}
                version = opened.write(
                    OWNER, lambda tx, v=version, f=fields: tx.widget_change(v, a, "table", f, LATER)
                )["version"]
    assert version == 19


# ---- order and delete ----------------------------------------------------------------------
def test_the_order_is_the_whole_list_and_nothing_else(opened):
    a, b, c = _screens(opened, 3)
    row = _insert(opened, 0, a, "count")
    version = row["version"]
    now = _keys(opened)
    assert now == [(c, "table"), (b, "table"), (a, "table"), (a, "count")]
    for bad in (
        now[:-1],
        [*now, (99, "table")],
        [*now[:-1], now[0]],
        [*now, now[0]],
        [],
        [(k, "count") for k, _ in now],
    ):
        with pytest.raises(VersionConflict):
            opened.write(OWNER, lambda tx, bad=bad: tx.widgets_order(version, bad, LATER))
    assert _keys(opened) == now and opened.read(OWNER, Tx.dashboard)["version"] == version
    wanted = [(a, "count"), (c, "table"), (a, "table"), (b, "table")]
    moved = opened.write(OWNER, lambda tx: tx.widgets_order(version, wanted, LATER))
    assert moved["version"] == version + 1
    assert _keys(opened) == wanted
    assert [w["position"] for w in opened.read(OWNER, Tx.widgets)] == [0, 1, 2, 3]


def test_deleting_a_widget_closes_the_gap_in_position(opened):
    ids = _screens(opened, 5)
    keys = [(i, "table") for i in ids[::-1]]
    row = opened.write(OWNER, lambda tx: tx.widget_delete(0, ids[2], "table", LATER))
    assert _keys(opened) == [k for k in keys if k != (ids[2], "table")]
    assert [w["position"] for w in opened.read(OWNER, Tx.widgets)] == [0, 1, 2, 3]
    row = opened.write(OWNER, lambda tx: tx.widget_delete(row["version"], ids[4], "table", LATER))
    assert [w["position"] for w in opened.read(OWNER, Tx.widgets)] == [0, 1, 2]
    with pytest.raises(NotFound):
        opened.write(OWNER, lambda tx: tx.widget_delete(row["version"], ids[4], "table", LATER))
    with pytest.raises(NotFound):
        opened.write(
            OWNER, lambda tx: tx.widget_change(row["version"], ids[4], "table", {"rows": 5}, LATER)
        )
    assert opened.read(OWNER, Tx.dashboard)["version"] == row["version"]


def test_a_new_widget_goes_last_after_a_delete(opened):
    a, b, c = _screens(opened, 3)
    row = opened.write(OWNER, lambda tx: tx.widget_delete(0, b, "table", LATER))
    _insert(opened, row["version"], b, "count")
    assert _keys(opened) == [(c, "table"), (a, "table"), (b, "count")]
    assert [w["position"] for w in opened.read(OWNER, Tx.widgets)] == [0, 1, 2]


# ---- a deleted screen ----------------------------------------------------------------------
def test_deleting_a_screen_takes_its_widgets_and_moves_the_version_in_one_transaction(opened):
    a, b, c = _screens(opened, 3)
    row = _insert(opened, 0, b, "count")
    assert _keys(opened) == [(c, "table"), (b, "table"), (a, "table"), (b, "count")]
    assert opened.write(OWNER, lambda tx: tx.screen_remove(b, LATER)) is True
    assert _keys(opened) == [(c, "table"), (a, "table")]
    assert [w["position"] for w in opened.read(OWNER, Tx.widgets)] == [0, 1]
    assert opened.read(OWNER, Tx.dashboard)["version"] == row["version"] + 1
    assert opened.read(OWNER, lambda tx: tx.screen(b)) is None


def test_a_screen_without_widgets_leaves_the_version_alone(opened):
    a, b = _screens(opened, 2)
    row = _insert(opened, 0, a, "count")
    other = _screens(opened, 1, "paused")[0]
    assert opened.write(OWNER, lambda tx: tx.widgets_of_screen_delete(other, LATER)) == 0
    opened.write(OWNER, lambda tx: tx.screen_remove(other, LATER))
    assert opened.read(OWNER, Tx.dashboard)["version"] == row["version"]
    # no dashboard row at all: nothing to move, the suggestion simply no longer lists it
    third = ScreenerStore(opened.path.parent / "third.db")
    try:
        x, y = _screens(third, 2)
        third.write(OWNER, lambda tx: tx.screen_remove(x, LATER))
        assert third.read(OWNER, Tx.dashboard) is None
        assert [w["screen_id"] for w in third.read(OWNER, Tx.layout)["widgets"]] == [y]
    finally:
        third.close()


def test_a_failing_screen_removal_rolls_the_widgets_back_with_it(opened):
    a = _screens(opened, 1)[0]
    row = _insert(opened, 0, a, "count")

    def boom(tx):
        tx.screen_remove(a, LATER)
        raise RuntimeError("after the removal")

    with pytest.raises(RuntimeError):
        opened.write(OWNER, boom)
    assert (a, "count") in _keys(opened)
    assert opened.read(OWNER, Tx.dashboard)["version"] == row["version"]


# ---- principals ----------------------------------------------------------------------------
def test_each_principal_has_its_own_dashboard_and_cannot_touch_the_others(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db", principals=("owner", "viewer"))
    try:
        mine = _screens(made, 2, who="owner")
        theirs = _screens(made, 1, who="viewer")
        _insert(made, 0, mine[0], "count")
        assert made.read("viewer", Tx.dashboard) is None
        assert [w["screen_id"] for w in made.read("viewer", Tx.suggestion)] == theirs
        with pytest.raises(NotFound):
            _insert(made, 0, mine[0], "count", who="viewer")
        _insert(made, 0, theirs[0], "count", who="viewer")
        assert _keys(made, "owner") == [(mine[1], "table"), (mine[0], "table"), (mine[0], "count")]
        assert _keys(made, "viewer") == [(theirs[0], "table"), (theirs[0], "count")]
        made.write("viewer", lambda tx: tx.screen_remove(theirs[0], LATER))
        assert made.read("owner", Tx.dashboard)["version"] == 2
    finally:
        made.close()
        store.stop()
