"""One run of one screen (SV6): evaluation in slices against a fake source, the recorded run and
its changes, membership transitions across runs, alerts and the outbox, a superseded run, rewind
semantics and the digest payload."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from screener_helpers import flat, ladder, ms, series

from sudarshan.alerts.market.sessions import key_day, sessions_of
from sudarshan.autologin.handoff import HandoffRejected, open_sealed
from sudarshan.screener import alerting as al
from sudarshan.screener import digests, spec
from sudarshan.screener import scheduler as sch
from sudarshan.screener import store as store_module
from sudarshan.screener.engine import Context, Subject
from sudarshan.screener.runs import Judged, Member, evaluate, record
from sudarshan.screener.store import OWNER, ScreenerStore, Tx
from sudarshan.screener.types import HIT, MISS, NOT_CHECKED, Outcome
from sudarshan.vault import ALERTS_SCREENER_DIGEST_INFO, fernet_for

DAY = "2026-10-28"
SESSION = 20261028
SESSIONS = sessions_of(key_day(SESSION), trading_day=True)
NOW = ms(DAY, "10:15:05")
SCREEN = sch.Screen(0, 1, "close", 15, frozenset({"NSE_EQ"}))

#: Close above 100.00 on the 15-minute candle, ranked by close, into list 7.
SPEC = {
    "schema": 1,
    "name": "Above hundred",
    "universe": {"kind": "fno_stocks"},
    "run": {"on": "close", "tf": 15},
    "where": {
        "id": "c1",
        "left": {"field": "close", "tf": 15},
        "op": "gt",
        "right": {"num": "100"},
    },
    "rank": {"by": {"field": "close", "tf": 15}, "order": "desc"},
    "target": {"list_id": 7, "grace": {"mode": "runs", "n": 2}},
    "alerts": {"on": True},
}
MEMBERS = [
    Member("NSE_EQ|A", "AAA", "NSE", "NSE_EQ"),
    Member("NSE_EQ|B", "BBB", "NSE", "NSE_EQ"),
    Member("NSE_EQ|C", "CCC", "NSE", "NSE_EQ"),
]


class FakeSource:
    """Closes per key per run; a key with None has no prices yet."""

    def __init__(self, closes: dict[str, int | None]):
        self.closes = closes
        self.asked: list[str] = []

    def members(self, universe):
        return MEMBERS

    def subject(self, member, plan, boundary):
        self.asked.append(member.key)
        close = self.closes.get(member.key)
        if close is None:
            return None
        count = (boundary - ms(DAY, "09:15")) // 900_000  # candles ending at the boundary
        closes = [close - 100, close - 50] + [close] * max(1, count - 2)
        return Subject(member.symbol, {15: series(15, ladder(DAY, "09:15", 15, closes))}, cp=9000)

    def context(self, boundary, session):
        return Context(boundary, session, 20261027)

    def prices(self, member):
        close = self.closes.get(member.key)
        return (close, 9000)


@pytest.fixture
def opened(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db")
    yield made
    made.close()
    store_module.stop()


def make_screen(opened, raw=SPEC, at: int | None = None) -> dict:
    """The screen made at ``at`` (a moment before NOW by default)."""
    stored = spec.check(raw)
    made = NOW - 1 if at is None else at
    return opened.write(OWNER, lambda tx: tx.screen_insert(stored["name"], stored, "active", made))


def due_for(screen: dict, k: int, late=False, last=False) -> sch.Due:
    screen_sch = sch.Screen(screen["id"], screen["version"], "close", 15, frozenset({"NSE_EQ"}))
    boundary = sch.boundaries(screen_sch, SESSIONS)[k]
    key = sch.RunKey(screen["id"], screen["version"], SESSION, "15", k)
    return sch.Due(key, screen_sch, boundary, late, last)


def judge_all(screen: dict, source: FakeSource, k: int) -> list[Judged]:
    plan = spec.compile_plan(screen["spec"])
    boundary = sch.boundaries(SCREEN, SESSIONS)[k]
    ctx = source.context(boundary.end, SESSION)
    return asyncio.run(evaluate(plan, MEMBERS, source, ctx))


def run_once(opened, screen, source, k, now, **extra):
    judged = judge_all(screen, source, k)
    due = due_for(screen, k, **extra)
    return opened.write(
        OWNER,
        lambda tx: record(tx, tx.screen(screen["id"]), tx.state(screen["id"]), due, judged, now),
    )


def test_evaluation_judges_every_member_and_yields_between_slices():
    source = FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 9000, "NSE_EQ|C": None})
    plan = spec.compile_plan(spec.check(SPEC))
    boundary = sch.boundaries(SCREEN, SESSIONS)[3]
    yields: list[int] = []
    many = [Member(f"NSE_EQ|{n}", f"S{n}", "NSE", "NSE_EQ") for n in range(60)]
    judged = asyncio.run(
        evaluate(
            plan, many, source, Context(boundary.end, SESSION), yielder=lambda: yields.append(1)
        )
    )
    assert len(judged) == 60 and len(yields) == 2, "a yield after each full slice of 25"
    judged = asyncio.run(evaluate(plan, MEMBERS, source, Context(boundary.end, SESSION)))
    kinds = {j.member.key: j.outcome.kind for j in judged}
    assert kinds == {"NSE_EQ|A": HIT, "NSE_EQ|B": MISS, "NSE_EQ|C": NOT_CHECKED}
    assert judged[2].outcome.why[0].code == "no_feed"
    assert judged[0].prices == (12000, 9000)


def test_the_first_run_records_the_whole_set_enters_members_and_tells_once(opened):
    screen = make_screen(opened)
    source = FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 11000, "NSE_EQ|C": None})
    report = run_once(opened, screen, source, 4, NOW)
    assert report.status == "done" and (report.matched, report.entered, report.not_checked) == (
        2,
        2,
        1,
    )
    assert report.told is True and report.why == "told" and report.list_id == 7
    run = opened.read(OWNER, lambda tx: tx.run(report.run_id))
    assert (
        run["matched"] == 2
        and run["entered"] == 2
        and run["marks"]["alerts"].startswith("Alert raised: 2 entered")
    )
    changes = opened.read(OWNER, lambda tx: tx.run_changes(report.run_id))
    assert [(c["key"], c["change"], c["rank"]) for c in changes] == [
        ("NSE_EQ|C", "not_checked", None), ("NSE_EQ|A", "in", 0), ("NSE_EQ|B", "in", 1),
    ]  # fmt: skip
    values = opened.read(OWNER, lambda tx: tx.run_values(report.run_id))
    assert set(values) == {"NSE_EQ|A", "NSE_EQ|B", "NSE_EQ|C"}
    assert values["NSE_EQ|A"]["c1"]["l"] == "120.00" and values["NSE_EQ|A"]["_ltp"] == 12000
    assert values["NSE_EQ|C"] == {"_not_checked": "Not checked: no price from Upstox yet"}
    rows = opened.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"], list_id=7))
    assert {r["key"]: (r["status"], r["entry_price"], r["first_seen_run"]) for r in rows} == {
        "NSE_EQ|A": ("active", 12000, report.run_id), "NSE_EQ|B": ("active", 11000, report.run_id),
    }  # fmt: skip
    told = opened.read(OWNER, lambda tx: tx.told(screen["id"], SESSION))
    assert told["NSE_EQ|A"]["told"] == 1 and told["NSE_EQ|A"]["last_run"] == 4
    outbox = opened.read(OWNER, lambda tx: tx.outbox_due(NOW))
    kinds = sorted(r["kind"] for r in outbox)
    assert kinds == ["digest", "list_sync", "push"]
    digest = next(r for r in outbox if r["kind"] == "digest")["payload"]
    assert digest["run_key"] == report.run_key and digest["matches"] == 2
    assert [e["symbol"] for e in digest["entered"]] == ["AAA", "BBB"]
    assert digest["entered"][0] == {
        "key": "NSE_EQ|A",
        "symbol": "AAA",
        "exchange": "NSE",
        "ltp": 12000,
        "change": "33.33",
    }
    state = opened.read(OWNER, lambda tx: tx.state(screen["id"]))
    assert state["last_run_key"] == report.run_key and state["alerts_paused"] is False


def test_later_runs_record_differences_a_miss_then_an_exit_and_nothing_new_is_not_told(opened):
    screen = make_screen(opened)
    first = run_once(opened, screen, FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 11000}), 4, NOW)
    # B falls below: a miss inside the grace (one run)
    second = run_once(
        opened, screen, FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 9000}), 5, NOW + 900_000
    )
    assert (second.matched, second.missing, second.exited, second.told) == (1, 1, 0, False)
    changes = opened.read(OWNER, lambda tx: tx.run_changes(second.run_id))
    assert [(c["key"], c["change"]) for c in changes] == [
        ("NSE_EQ|B", "out"),
        ("NSE_EQ|C", "not_checked"),
    ], "differences only"
    assert (
        opened.read(OWNER, lambda tx: tx.run(second.run_id))["marks"]["alerts"]
        == "No alert: nothing new"
    )
    # still below: the grace ran out, B exits
    third = run_once(
        opened, screen, FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 9000}), 6, NOW + 1_800_000
    )
    assert (third.matched, third.missing, third.exited) == (1, 0, 1)
    rows = {
        r["key"]: r for r in opened.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"]))
    }
    assert rows["NSE_EQ|B"]["status"] == "exited" and rows["NSE_EQ|B"]["exited_at"] is not None
    assert rows["NSE_EQ|A"]["hits"] == 3
    third_changes = opened.read(OWNER, lambda tx: tx.run_changes(third.run_id))
    assert [c["change"] for c in third_changes] == ["not_checked"], "B was already out at the miss"
    # A matches again: nothing new, so no digest; the first run's digest is the only one
    digests_rows = opened.read(
        OWNER, lambda tx: [r for r in tx.outbox_due(NOW + 10**7, 100) if r["kind"] == "digest"]
    )
    assert len(digests_rows) == 1 and digests_rows[0]["ref"] == first.run_key


def test_a_screen_edited_meanwhile_gets_a_superseded_row_and_nothing_else(opened):
    screen = make_screen(opened)
    source = FakeSource({"NSE_EQ|A": 12000})
    judged = judge_all(screen, source, 4)
    due = due_for(screen, 4)
    opened.write(OWNER, lambda tx: tx.screen_change(screen["id"], 1, {"name": "renamed"}, NOW))
    report = opened.write(
        OWNER,
        lambda tx: record(tx, tx.screen(screen["id"]), tx.state(screen["id"]), due, judged, NOW),
    )
    assert report.status == "superseded"
    run = opened.read(OWNER, lambda tx: tx.run(report.run_id))
    assert run["status"] == "superseded" and run["screen_version"] == 1 and run["matched"] == 0
    assert opened.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"])) == []
    assert opened.read(OWNER, lambda tx: tx.outbox_count()) == 0
    # the same key again is returned as recorded, never run twice
    again = opened.write(
        OWNER,
        lambda tx: record(
            tx, tx.screen(screen["id"]), tx.state(screen["id"]), due, judged, NOW + 1
        ),
    )
    assert again.status == "superseded" and again.run_id == report.run_id


def test_a_late_run_is_marked_and_a_pause_after_trigger_pauses_the_screens_alerts(opened):
    raw = {**SPEC, "alerts": {"on": True, "after_trigger": "pause"}}
    screen = make_screen(opened, raw)
    report = run_once(opened, screen, FakeSource({"NSE_EQ|A": 12000}), 4, NOW + 120_000, late=True)
    marks = opened.read(OWNER, lambda tx: tx.run(report.run_id))["marks"]
    assert marks["late"] == NOW + 120_000 and report.told is True
    assert opened.read(OWNER, lambda tx: tx.state(screen["id"]))["alerts_paused"] is True
    digest = next(
        r
        for r in opened.read(OWNER, lambda tx: tx.outbox_due(NOW + 10**6))
        if r["kind"] == "digest"
    )
    assert digest["payload"]["late_at"] == NOW + 120_000 and digest["payload"]["pause"] is True
    second = run_once(
        opened, screen, FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 11000}), 5, NOW + 10**6
    )
    assert second.told is False and second.why == "paused"


def test_a_screen_without_a_target_keeps_members_under_list_zero_and_syncs_no_list(opened):
    raw = {k: v for k, v in SPEC.items() if k != "target"}
    raw["target"] = None
    screen = make_screen(opened, raw)
    assert screen["spec"]["target"] is None
    report = run_once(opened, screen, FakeSource({"NSE_EQ|A": 12000}), 4, NOW)
    assert report.list_id == 0 and report.entered == 1
    kinds = sorted(r["kind"] for r in opened.read(OWNER, lambda tx: tx.outbox_due(NOW)))
    assert kinds == ["digest", "push"]


def test_the_digest_seals_under_the_alerts_purpose_only_and_carries_x3_units():
    fernet = fernet_for("p" * 48, ALERTS_SCREENER_DIGEST_INFO)
    decision_payload = digests.build(
        screen_id=3, screen_name="Breakout", run_key="3:v1:20261028:15:4", at=NOW - 5000, session=SESSION,
        decision=al.Decision(True, "told", entered=(al.Named("NSE_EQ|A", "AAA", "NSE"),), matches=4),
        prices={"NSE_EQ|A": (10125, 10000)},
    )  # fmt: skip
    assert (
        decision_payload["entered"][0]["ltp"] == 10125
        and decision_payload["entered"][0]["change"] == "1.25"
    )
    assert isinstance(decision_payload["entered"][0]["change"], str)
    blob = digests.sealed(fernet, decision_payload)
    opened = open_sealed(fernet, blob, op=digests.OP, seen={})
    assert opened["principal"] == "owner" and opened["args"]["screen"] == "Breakout"
    with pytest.raises(HandoffRejected):
        open_sealed(
            fernet_for("p" * 48, b"sudarshan/screener/api/v1"), blob, op=digests.OP, seen={}
        )
    assert digests.change_pct(None, 100) is None and digests.change_pct(100, 0) is None
    assert digests.change_pct(9875, 10000) == "-1.25" and Decimal(
        digests.change_pct(10001, 10000)
    ) == Decimal("0.01")
