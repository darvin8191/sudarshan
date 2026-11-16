"""Not-checked instruments are kept compact (gap audit GA7.4).

Before, a run wrote one ``run_change`` row and one ``run_value`` row for every instrument it could
not judge, at every candle close: a screen on 200 stocks warming up wrote 400 rows a run. Now a
run keeps them as groups, by their reason in words, in one row; the page reads the count, the
reasons and the groups from it; a run recorded the old way is read the same; and an instrument not
checked is never a miss.
"""

from __future__ import annotations

from test_screener_results import call, make, record
from test_screener_results import service as service  # noqa: F401  (the fixture)
from test_screener_runs import due_for

from sudarshan.screener import runs, words
from sudarshan.screener.runs import NOT_CHECKED_KEY, Judged, Member
from sudarshan.screener.store import OWNER
from sudarshan.screener.types import HIT, NEEDS_HISTORY, NO_FEED, NOT_CHECKED, Outcome, Why

NO_PRICE = "Not checked: no price from Upstox yet"


def stock(n: int) -> Member:
    return Member(f"NSE_EQ|{n:03d}", f"STOCK{n:03d}", "NSE", "NSE_EQ")


def unjudged(n: int, why: Why) -> Judged:
    return Judged(stock(n), Outcome(NOT_CHECKED, (why,)), (None, None))


def hit(n: int, rank: int) -> Judged:
    return Judged(stock(n), Outcome(HIT, (), {}, rank), (rank * 100, 9000))


def table(svc, name: str, run_id: int) -> int:
    from sqlalchemy import text

    return svc.store.read(
        OWNER,
        lambda tx: tx.conn.execute(
            text(f"SELECT COUNT(*) FROM {name} WHERE run_id = :r"), {"r": run_id}
        ).scalar(),
    )


def test_two_hundred_instruments_not_checked_are_one_row_not_four_hundred(service):  # noqa: F811
    screen = make(service)
    history = Why(NEEDS_HISTORY, (151, "15", 25))
    found = (
        [hit(0, 300), hit(1, 200)]
        + [unjudged(n, history) for n in range(2, 150)]
        + [unjudged(n, Why(NO_FEED)) for n in range(150, 202)]
    )
    report = record(service, screen, 1, found)
    assert report.not_checked == 200 and report.matched == 2
    assert table(service, "run_change", report.run_id) == 2, "a change row for each not checked"
    assert table(service, "run_value", report.run_id) == 3, "a value row for each not checked"
    values = service.store.read(OWNER, lambda tx: tx.run_values(report.run_id))
    groups = values[NOT_CHECKED_KEY]["groups"]
    assert [(g["count"], g["reason"]) for g in groups] == [
        (148, words.not_checked_words([history])),
        (52, NO_PRICE),
    ], "the largest group first, each with its reason in words"
    assert groups[0]["symbols"] == [f"STOCK{n:03d}" for n in range(2, 7)]

    got = call(service, "results", id=screen["id"])
    assert got["run"]["not_checked"] == 200
    assert got["not_checked"]["count"] == 200
    assert got["not_checked"]["reasons"] == [g["reason"] for g in groups]
    assert got["not_checked"]["groups"] == groups
    assert [r["symbol"] for r in got["rows"]] == ["STOCK000", "STOCK001"]
    # the past run's sheet reads the same groups, and its match set is the two matches
    past = call(service, "run", id=report.run_id)
    assert past["not_checked"] == got["not_checked"]
    assert [r["symbol"] for r in past["rows"]] == ["STOCK000", "STOCK001"]


def test_not_checked_is_never_a_miss(service):  # noqa: F811
    screen = make(service)
    record(service, screen, 1, [hit(0, 300), hit(1, 200)])
    # the next run cannot judge STOCK001: it stays a member, with no miss and no "out"
    report = record(service, screen, 2, [hit(0, 300), unjudged(1, Why(NO_FEED))])
    assert (report.matched, report.missing, report.exited, report.not_checked) == (1, 0, 0, 1)
    rows = {
        r["symbol"]: r
        for r in service.store.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"]))
    }
    assert rows["STOCK001"]["status"] == "active" and rows["STOCK001"]["misses"] == 0
    changes = service.store.read(OWNER, lambda tx: tx.run_changes(report.run_id))
    assert changes == [], "an instrument not checked was written as a change"
    got = call(service, "results", id=screen["id"])
    assert [r["symbol"] for r in got["rows"]] == ["STOCK000", "STOCK001"], "it left the results"
    assert got["not_checked"]["groups"] == [
        {"reason": NO_PRICE, "count": 1, "symbols": ["STOCK001"]}
    ]


def test_many_reasons_of_a_few_instruments_each_stay_a_small_row():
    found = [(f"S{n:03d}", f"Not checked: reason {n % 40:02d}") for n in range(120)]
    groups = runs.not_checked_groups(found)
    assert len(groups) == runs.GROUPS_KEPT and sum(g["count"] for g in groups) == 120
    assert groups[-1]["reason"] == runs.OTHER_REASONS
    assert groups[-1]["count"] == 120 - 3 * (runs.GROUPS_KEPT - 1)
    assert all(len(g["symbols"]) <= runs.GROUP_NAMES for g in groups)


def test_a_run_recorded_the_old_way_reads_the_same(service):  # noqa: F811
    """A database written by an earlier release: a change and a value for each instrument."""
    screen = make(service)
    due = due_for(screen, 1)

    def old(tx):
        run = tx.run_insert(
            {
                "screen_id": screen["id"],
                "screen_version": screen["version"],
                "run_key": str(due.key),
                "session": due.key.session,
                "candle_start": due.boundary.start,
                "candle_end": due.boundary.end,
                "ran_at": due.boundary.at,
                "status": "done",
                "engine_version": runs.ENGINE_VERSION,
            }
        )
        names = [stock(n) for n in range(3)]
        tx.run_changes_put(
            run["id"],
            [
                {
                    "key": m.key,
                    "symbol": m.symbol,
                    "exchange": m.exchange,
                    "change": "not_checked",
                    "rank": None,
                }
                for m in names
            ],
        )
        tx.run_values_put(run["id"], {m.key: {"_not_checked": NO_PRICE} for m in names})
        tx.run_finish(run["id"], {}, matched=0, entered=0, exited=0, missing=0, not_checked=3)
        return run["id"]

    run_id = service.store.write(OWNER, old)
    for got in (call(service, "results", id=screen["id"]), call(service, "run", id=run_id)):
        assert got["not_checked"] == {
            "count": 3,
            "reasons": [NO_PRICE],
            "groups": [
                {"reason": NO_PRICE, "count": 3, "symbols": ["STOCK000", "STOCK001", "STOCK002"]}
            ],
        }
        assert got["rows"] == []


def test_once_the_values_are_gone_the_count_is_still_said(service):  # noqa: F811
    from sqlalchemy import text

    screen = make(service)
    report = record(service, screen, 1, [hit(0, 300), unjudged(1, Why(NO_FEED))])
    service.store.write(
        OWNER, lambda tx: tx.conn.execute(text("DELETE FROM run_value"))
    )  # the 7-day retention
    past = call(service, "run", id=report.run_id)
    assert past["not_checked"] == {"count": 1, "reasons": [], "groups": []}
    assert [r["symbol"] for r in past["rows"]] == ["STOCK000"]
