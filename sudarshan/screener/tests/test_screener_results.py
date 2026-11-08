"""What the Results tab and the rewind sheet show (release review MEDIUM-1, MEDIUM-2).

Results list a run's matches best ranked first, as the screen ranks them (never in symbol order),
and say how many more matched and are waiting for room. A past run's sheet shows that run's whole
match set: from its values while they are kept (7 days), else replayed from the day's first run
and its changes (100 days).
"""

from __future__ import annotations

import asyncio

import pytest
from test_screener_runs import NOW, SPEC, due_for

from sudarshan.screener import runs, spec, words
from sudarshan.screener import store as store_module
from sudarshan.screener.ops import REGISTRY
from sudarshan.screener.runs import Judged, Member
from sudarshan.screener.service import Service
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.screener.tests.test_screener_service import FERNET, settings_for
from sudarshan.screener.types import HIT, MISS, Outcome


@pytest.fixture
def service(tmp_path):
    svc = Service(settings_for(1, SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path)), FERNET, jobs={})
    svc.store = ScreenerStore(tmp_path / "screener.db")
    svc.clock = lambda: NOW
    yield svc
    svc.store.close()
    store_module.stop()


def call(svc, op, **args):
    return asyncio.run(REGISTRY[op](svc, args))


def member(symbol: str) -> Member:
    return Member(f"NSE_EQ|{symbol[0]}", symbol, "NSE", "NSE_EQ")


def judged(ranks: dict[str, int | None], missed: tuple[str, ...] = ()) -> list[Judged]:
    """Matches with their rank values (the 15-min close in paise), and misses."""
    out = [
        Judged(member(s), Outcome(HIT, (), {}, r), ((r or 0) * 100, 9000)) for s, r in ranks.items()
    ]
    return out + [Judged(member(s), Outcome(MISS, (), {}, None), (0, 9000)) for s in missed]


def record(svc, screen, k: int, found: list[Judged]):
    due = due_for(screen, k)
    return svc.store.write(
        OWNER,
        lambda tx: runs.record(
            tx, tx.screen(screen["id"]), tx.state(screen["id"]), due, found, due.boundary.at
        ),
    )


def make(svc, **extra) -> dict:
    stored = spec.check(dict(SPEC, **extra))
    return svc.store.write(OWNER, lambda tx: tx.screen_insert("S", stored, "active", NOW))


# ---- MEDIUM-2: results in the screen's rank ------------------------------------------------------
def test_results_are_ranked_by_the_screens_rank(service):
    screen = make(service)  # SPEC ranks by the 15-min close, highest first
    record(service, screen, 1, judged({"AAA": 100, "BBB": 200, "CCC": 300}))
    rows = call(service, "results", id=screen["id"])["rows"]
    assert [(r["rank"], r["symbol"]) for r in rows] == [(1, "CCC"), (2, "BBB"), (3, "AAA")], (
        "results are in symbol order"
    )
    # the next run reorders them: AAA leads now, all three still match
    record(service, screen, 2, judged({"AAA": 900, "BBB": 200, "CCC": 300}))
    rows = call(service, "results", id=screen["id"])["rows"]
    assert [r["symbol"] for r in rows] == ["AAA", "CCC", "BBB"]
    # lowest first, when the screen says so
    low = make(service, rank={"by": {"field": "close", "tf": 15}, "order": "asc"}, target=None)
    record(service, low, 1, judged({"AAA": 100, "BBB": 200, "CCC": 300}))
    assert [r["symbol"] for r in call(service, "results", id=low["id"])["rows"]] == [
        "AAA",
        "BBB",
        "CCC",
    ]


def test_matches_the_list_could_not_take_are_said_to_be_waiting(service):
    screen = make(service, target={"list_id": 7, "max_adds_per_run": 1})
    record(service, screen, 1, judged({"AAA": 100, "BBB": 200, "CCC": 300}))
    run = call(service, "runs", id=screen["id"])["runs"][0]
    assert run["matches"] == 3 and run["entered"] == 1
    assert "2 more matched, waiting for the next check (at most 1 added a check)" in run["marks"], (
        "the matches waiting for room never reach the page"
    )
    assert words.mark_words({"waiting": {"count": 4, "full": True, "max_adds": 10}}) == (
        "4 more matched, waiting: the list is full",
    )


# ---- MEDIUM-1: a past run's whole match set --------------------------------------------------------
def test_a_past_runs_set_is_the_whole_match_set(service):
    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100, "BBB": 200}))
    record(service, screen, 2, judged({"AAA": 100, "BBB": 200, "CCC": 300}))
    record(service, screen, 3, judged({"AAA": 100, "CCC": 300}, missed=("BBB",)))
    page = call(service, "runs", id=screen["id"])["runs"]
    second, third = page[1], page[0]
    answer = call(service, "run", id=second["id"])
    assert answer["run"]["matches"] == 3
    assert [r["symbol"] for r in answer["rows"]] == ["CCC", "BBB", "AAA"], (
        "rewind shows only what changed in that run"
    )
    assert [r["symbol"] for r in call(service, "run", id=third["id"])["rows"]] == ["CCC", "AAA"]

    # after 7 days the values are gone: the set is replayed from the day's changes
    service.store.write(OWNER, lambda tx: tx.conn.execute(tx._t("run_value").delete()))
    replayed = call(service, "run", id=second["id"])
    assert sorted(r["symbol"] for r in replayed["rows"]) == ["AAA", "BBB", "CCC"]
    assert replayed["values_note"] == "Values are kept for 7 days; the list for 100 days."
    later = call(service, "run", id=third["id"])
    assert sorted(r["symbol"] for r in later["rows"]) == ["AAA", "CCC"]


# ---- MEDIUM-10: a new target list is honoured -------------------------------------------------------
def test_an_edit_to_another_list_moves_the_screen_to_it(service):
    made = make(service, target={"list_id": None, "create_name": "Screener: S"})
    service.store.write(
        OWNER, lambda tx: tx.state_put(made["id"], NOW, list_id=11, list_name="Screener: S")
    )
    record(service, made, 1, judged({"AAA": 100}))  # into the list it made (11)
    screen = service.store.read(OWNER, lambda tx: tx.screen(made["id"]))
    edited = call(
        service, "update", id=screen["id"], version=screen["version"], action="edit",
        spec=dict(SPEC, target={"list_id": 9}),
    )  # fmt: skip
    after = service.store.read(OWNER, lambda tx: tx.screen(made["id"]))
    record(service, after, 2, judged({"AAA": 100, "BBB": 200}))
    rows = service.store.read(OWNER, lambda tx: tx.memberships(screen_id=made["id"], list_id=9))
    assert {r["symbol"] for r in rows} == {"AAA", "BBB"}, "the new target list is ignored"
    queued = service.store.read(OWNER, lambda tx: tx.outbox_due(NOW + 10**9, 50))
    assert any(p["kind"] == "list_sync" and p["payload"]["list_id"] == 9 for p in queued)
    assert edited["list_id"] == 9


# ---- MEDIUM-9: what became of a run's digest --------------------------------------------------------
class AlertsLink:
    """sudarshan-alerts as the service's sealed link reaches it: refuses, or never answers."""

    def __init__(self, status):
        self.status, self.digests = status, 0

    async def digest(self, payload):
        from sudarshan.screener.link import LinkError

        self.digests += 1
        raise LinkError(self.status, "refused" if self.status else "digest: connecterror")

    async def push(self, items):
        return 1


@pytest.mark.parametrize(
    ("status", "line", "retried"),
    [
        (409, "No alert sent: market alerts are switched off on the server", False),
        (None, "No alert sent: market alerts did not answer for 5 minutes", True),
    ],
)
def test_a_digest_market_alerts_did_not_take_turns_alert_raised_into_why(
    service, status, line, retried
):
    from sudarshan.screener import outbox

    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100}))
    run = call(service, "runs", id=screen["id"])["runs"][0]
    assert run["alerts"].startswith("Alert raised")
    link = AlertsLink(status)
    clock = {"now": due_for(screen, 1).boundary.at + 1000}  # just after the run queued it
    sender = outbox.Sender(service.store, link, lambda: clock["now"])
    for _ in range(12):  # each try as its backoff comes due, past the digest's 5 minutes
        asyncio.run(sender.tick())
        clock["now"] += 61_000
    run = call(service, "runs", id=screen["id"])["runs"][0]
    assert run["alerts"] == line, "the History says an alert was raised that never went"
    assert (link.digests > 1) is retried, "a digest refused for good is tried again"


# ---- LOW-2: a run's push fits one sealed request ----------------------------------------------------
def test_a_runs_push_with_many_exits_fits_the_internal_seal(service):
    from sudarshan.autologin.handoff import seal
    from sudarshan.screener import contract
    from sudarshan.screener import membership as mem
    from sudarshan.vault import SCREENER_INTERNAL_INFO, fernet_for

    screen = make(service, target=None)
    many = [f"S{n:04d}LONGNAME" for n in range(300)]

    def held(tx):
        for symbol in many:
            row = mem.Row(f"NSE_EQ|{symbol}", symbol, "NSE", mem.MISSING, misses=1, first_seen_at=1)
            tx.membership_put(
                screen["id"], 0, {n: getattr(row, n) for n in mem.Row.__dataclass_fields__}, 1
            )

    service.store.write(OWNER, held)
    found = [
        Judged(Member(f"NSE_EQ|{s}", s, "NSE", "NSE_EQ"), Outcome(MISS, (), {}, None), (0, 9000))
        for s in many
    ]
    report = record(service, screen, 2, found)
    assert report.exited == 300
    push = next(
        p["payload"]
        for p in service.store.read(OWNER, lambda tx: tx.outbox_due(NOW + 10**9, 50))
        if p["kind"] == "push"
    )
    blob = seal(fernet_for("f" * 64, SCREENER_INTERNAL_INFO), "push", principal="owner",
                args={"items": [{"event": contract.EVENT_RUN, "data": push}]})  # fmt: skip
    assert len(blob) <= contract.INTERNAL_MAX_BYTES, (
        f"a run's push seals to {len(blob)} bytes: refused before it is sent, never retried"
    )
    assert push["counts"]["exited"] == 300 and push["more_exited"] == 300 - len(push["exited"])


# ---- LOW-7: a pin a check overtook ----------------------------------------------------------------
def test_a_pin_a_check_overtook_says_the_instrument_changed(service):
    from sudarshan.screener.ops import ApiError

    screen = make(service)
    record(service, screen, 1, judged({"AAA": 100}))
    row = call(service, "membership", screen=screen["id"])["rows"][0]
    record(service, screen, 2, judged({"AAA": 120}))  # the next check moves the row
    with pytest.raises(ApiError) as refused:
        call(service, "membership_update", id=row["id"], version=row["version"], action="pin")
    assert refused.value.code == "version_conflict"
    assert refused.value.message == (
        "This instrument changed with the latest check. Nothing was saved; look at it again, then "
        "repeat the change."
    ), "a pin a run overtook says the screen was changed on another screen"
