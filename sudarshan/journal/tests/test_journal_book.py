"""The journal's writes (JB4): the day replay through the store, trips that keep their ids and the
owner's words, orphans, labels and charges; trip updates under their version; tags and playbooks
(design 4.2, 5.1, 6.2, 8.2; mutation targets 22 and 24)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sudarshan.journal import store
from sudarshan.journal.book import (
    Facts,
    create_playbook,
    create_tag,
    replay_tx,
    update_playbook,
    update_tag,
    update_trip,
)
from sudarshan.journal.core.money import u_to_paise
from sudarshan.journal.errors import ApiError
from sudarshan.journal.ops import REGISTRY
from sudarshan.journal.store import Invalid, JournalStore, NotFound, Refused, Scope, VersionConflict

OWNER = Scope.owner()
NOW = 1_794_300_000_000
VECTORS = json.loads((Path(__file__).parent / "vectors" / "pairing.json").read_text())["vectors"]


@pytest.fixture
def made(tmp_path):
    opened = JournalStore(tmp_path / store.DB_FILENAME, now=NOW)
    yield opened
    opened.close()
    store.stop()


def as_fill(f: dict, source: str = "stream_read") -> dict:
    """A pairing vector's fill as the store keeps it."""
    mode = f.get("mode") or "live"
    return {
        "mode": mode, "day": f["day"], "trade_id": f["trade_id"],
        "order_id": f.get("order_id") or f"o-{f['trade_id']}", "exchange": f["exchange"],
        "symbol": f["symbol"], "instrument_key": f.get("instrument_key", "NSE_FO|1"),
        "product": f["product"], "action": f["action"], "qty": f["qty"], "price_u": f["price_u"],
        "value_paise": u_to_paise(f["qty"] * f["price_u"]), "exch_ts_ms": f["exch_ts_ms"],
        "order_ts_ms": None, "source": f.get("source", source),
    }  # fmt: skip


def put(made, fills, days=None, facts=None, mode="live"):
    rows = [as_fill(f) for f in fills]
    made.write(OWNER, lambda tx: tx.fills_upsert(rows, NOW))
    wanted = days or {r["day"] for r in rows if r["mode"] == mode}
    return made.write(OWNER, lambda tx: replay_tx(tx, mode, wanted, NOW, facts or Facts()))


def trips(made, mode="live"):
    return made.read(OWNER, lambda tx: tx.rows("round_trip", order="opened_ms,id", mode=mode))


def legs(made, trip_id):
    return made.read(OWNER, lambda tx: tx.legs_of(trip_id))


def fill(trade_id, action, qty, price_rs, clock, day=20261109, **more):
    h, m = (int(x) for x in clock.split(":"))
    base = 1_794_198_660_000 - (10 * 3600 + 60) * 1000  # 00:00 IST on 9 Nov 2026
    at = base + ((day - 20261109) * 86_400 + h * 3600 + m * 60) * 1000
    return {"trade_id": trade_id, "exchange": "NFO", "symbol": "NIFTY25NOV2625000CE",
            "product": "MIS", "action": action, "qty": qty, "price_u": int(price_rs * 10_000),
            "exch_ts_ms": at, "day": day, **more}  # fmt: skip


# ---- the replay gives what the pure pairing gives ----------------------------------------------
SHAPE = ("side", "status", "qty_max", "entry_qty", "entry_avg_u", "exit_qty", "exit_avg_u",
         "gross_paise", "hold_s", "product_class", "open_day", "close_day")  # fmt: skip


def shape(made, trip):
    out = {k: trip[k] for k in SHAPE}
    out["legs"] = [[g["trade_id"], g["leg_role"], g["leg_qty"]] for g in legs(made, trip["id"])]
    return out


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_every_pairing_vector_through_the_store(made, vector):
    """The same vectors as the pure pairing (JA1), matched the same way: by side and first leg."""
    modes = {f.get("mode") or "live" for f in vector["fills"]}
    days = {f["day"] for f in vector["fills"]}
    for mode in modes:
        rows = [as_fill(f) for f in vector["fills"] if (f.get("mode") or "live") == mode]
        made.write(OWNER, lambda tx, rows=rows: tx.fills_upsert(rows, NOW))
        made.write(OWNER, lambda tx, mode=mode: replay_tx(tx, mode, days, NOW, Facts()))
    got = [shape(made, t) for mode in sorted(modes) for t in trips(made, mode)]
    want = [dict(t) for t in vector["trips"]]
    key = lambda t: (t["side"], t["legs"][0][0])  # noqa: E731
    assert sorted(got, key=key) == sorted(want, key=key), vector["note"]


def test_a_late_fill_with_an_earlier_time_gives_the_trips_it_would_have_live(made):
    """P8 through the store: the close check brings a fill timed before those recorded live."""
    live = [fill("a2", "BUY", 65, 98, "10:05"), fill("a3", "SELL", 130, 101, "10:20")]
    put(made, live)
    late = [fill("a1", "BUY", 65, 100, "10:01")]
    put(made, late)
    (trip,) = trips(made)
    assert (trip["entry_avg_u"], trip["gross_paise"], trip["status"]) == (990_000, 26_000, "closed")
    assert trip["opened_ms"] == fill("a1", "BUY", 65, 100, "10:01")["exch_ts_ms"]


def test_a_trip_that_grows_keeps_its_id_its_version_and_the_owners_note(made):
    put(made, [fill("a1", "BUY", 65, 100, "10:01")])
    (opened,) = trips(made)
    made.write(OWNER, lambda tx: update_trip(tx, opened["id"], 1, {"notes": "planned"}, NOW))
    put(made, [fill("a2", "BUY", 65, 98, "10:05")])
    put(made, [fill("a3", "SELL", 130, 101, "10:20")])
    (closed,) = trips(made)
    assert closed["id"] == opened["id"] and closed["status"] == "closed"
    assert closed["version"] == 2  # the owner's one change; the replays move nothing
    note = made.read(OWNER, lambda tx: tx.note_of(opened["id"]))
    assert note["notes"] == "planned"


def test_a_changed_trip_with_a_note_is_kept_as_an_orphan_and_one_without_goes(made):
    """The coordinator's JB4 rule: a trip that changed (it opens at another instant now) keeps
    the owner's words as an orphan; one nobody wrote on is replaced."""
    put(made, [fill("a2", "BUY", 65, 98, "10:05"), fill("a3", "SELL", 65, 101, "10:20")])
    put(made, [fill("b2", "BUY", 65, 50, "11:05", symbol="NIFTY25NOV2625100CE"),
               fill("b3", "SELL", 65, 51, "11:20", symbol="NIFTY25NOV2625100CE")])  # fmt: skip
    noted, plain = trips(made)
    made.write(OWNER, lambda tx: update_trip(tx, noted["id"], 1, {"notes": "chased it"}, NOW))
    put(made, [fill("a1", "BUY", 65, 97, "10:01")])  # now the trip opens at 10:01
    put(made, [fill("b1", "BUY", 65, 49, "11:01", symbol="NIFTY25NOV2625100CE")])
    rows = {t["id"]: t for t in trips(made)}
    assert rows[noted["id"]]["status"] == "orphan" and legs(made, noted["id"]) == []
    assert rows[noted["id"]]["gross_paise"] == noted["gross_paise"]  # its figures as they were
    assert made.read(OWNER, lambda tx: tx.note_of(noted["id"]))["notes"] == "chased it"
    assert plain["id"] not in rows  # nobody wrote on it: replaced
    live = [t for t in rows.values() if t["status"] != "orphan"]
    assert len(live) == 2  # the A trip as it is now and the B trip as it is now
    assert sorted(t["symbol"][-7:] for t in live) == ["25000CE", "25100CE"]
    assert all(t["id"] not in (noted["id"], plain["id"]) for t in live)


def test_a_withdrawn_fill_orphans_a_noted_trip_and_its_return_restores_it(made):
    put(made, [fill("a1", "BUY", 65, 100, "10:01"), fill("a2", "SELL", 65, 101, "10:20")])
    (trip,) = trips(made)
    made.write(OWNER, lambda tx: update_trip(tx, trip["id"], 1, {"risk_paise": 50_000}, NOW))
    made.write(OWNER, lambda tx: tx.fills_withdraw("live", 20261109, ["a2"], NOW))
    made.write(OWNER, lambda tx: replay_tx(tx, "live", {20261109}, NOW, Facts()))
    rows = trips(made)
    assert [r["status"] for r in rows if r["id"] == trip["id"]] == ["orphan"]
    assert [r for r in rows if r["id"] != trip["id"] and r["status"] == "open"]  # a2 alone
    put(made, [fill("a1", "BUY", 65, 100, "10:01", source="close_reconcile")])
    back = {r["id"]: r for r in trips(made)}
    assert back[trip["id"]]["status"] == "closed" and back[trip["id"]]["risk_paise"] == 50_000
    assert len(back) == 1  # the open trip of a2 alone went: nobody wrote on it


def test_a_carried_trip_is_paired_with_its_earlier_days(made):
    """P6 through the store: a CNC buy on day 1 and its sale on day 4 are one trip."""
    buy = fill("c1", "BUY", 10, 2500, "10:00", day=20261109, exchange="NSE", symbol="ACME",
               product="CNC")  # fmt: skip
    put(made, [buy])
    sale = fill("c2", "SELL", 10, 2600, "11:00", day=20261112, exchange="NSE", symbol="ACME",
                product="CNC")  # fmt: skip
    put(made, [sale], days={20261112})
    (trip,) = trips(made)
    assert (trip["status"], trip["open_day"], trip["close_day"]) == ("closed", 20261109, 20261112)
    assert trip["gross_paise"] == 100_000


def test_live_and_sandbox_fills_never_make_one_trip(made):
    put(made, [fill("x1", "BUY", 65, 100, "10:01")])
    put(made, [fill("x1", "SELL", 65, 101, "10:02", mode="sandbox")], mode="sandbox")
    assert [t["status"] for t in trips(made)] == ["open"]
    assert [t["side"] for t in trips(made, "sandbox")] == ["short"]


def test_the_strategy_is_the_first_entrys_label_and_follows_it(made):
    put(made, [fill("a1", "BUY", 65, 100, "10:01", order_id="o1"),
               fill("a2", "SELL", 65, 101, "10:20", order_id="o2")])  # fmt: skip
    assert trips(made)[0]["strategy"] == "Manual"
    made.write(OWNER, lambda tx: tx.order_ref_note("live", "o1", NOW, NOW))
    made.write(
        OWNER, lambda tx: tx.order_ref_set("live", "o1", {"strategy": "ORB", "surface": "api"}, NOW)
    )
    made.write(OWNER, lambda tx: replay_tx(tx, "live", {20261109}, NOW, Facts()))
    assert trips(made)[0]["strategy"] == "ORB"


def test_charges_are_estimated_per_trip_and_the_net_is_gross_less_them(made):
    put(made, [fill("a1", "BUY", 65, 100, "10:01"), fill("a2", "SELL", 65, 112.5, "10:20")])
    (trip,) = trips(made)
    assert trip["gross_paise"] == 81_250
    assert trip["charges_est_paise"] > 6_000  # two orders at Rs 30 on Plus, and the rest
    assert trip["net_paise"] == trip["gross_paise"] - trip["charges_est_paise"]
    put(made, [fill("m1", "BUY", 1, 5000, "10:30", exchange="MCX", symbol="GOLDM05DEC26FUT",
                    product="NRML")])  # fmt: skip
    gold = next(t for t in trips(made) if t["exchange"] == "MCX")
    assert gold["charges_est_paise"] == 0  # not modelled: nothing guessed


def test_expiry_flags_come_from_the_masters_facts_and_stay_when_unread(made):
    facts = Facts(expiries={"NSE_FO|1": 20261109}, index_days={20261109}, checked_days={20261109})
    put(made, [fill("a1", "BUY", 65, 100, "10:01")], facts=facts)
    (trip,) = trips(made)
    assert trip["own_expiry_day"] is True and trip["index_expiry_day"] is True
    put(made, [fill("a2", "SELL", 65, 101, "10:20")])  # the master unread this time
    (trip,) = trips(made)
    assert trip["own_expiry_day"] is True and trip["index_expiry_day"] is True


def test_replaying_twice_changes_nothing(made):
    put(made, [fill("a1", "BUY", 65, 100, "10:01"), fill("a2", "SELL", 65, 101, "10:20")])
    before = trips(made)
    again = made.write(OWNER, lambda tx: replay_tx(tx, "live", {20261109}, NOW + 5, Facts()))
    assert again["new"] == 0 and again["kept"] == 1
    after = trips(made)
    assert [{k: v for k, v in t.items() if k != "updated_at"} for t in after] == [
        {k: v for k, v in t.items() if k != "updated_at"} for t in before
    ]


# ---- the owner's fields --------------------------------------------------------------------------
@pytest.fixture
def trip_id(made):
    put(made, [fill("a1", "BUY", 65, 100, "10:01"), fill("a2", "SELL", 65, 101, "10:20")])
    return trips(made)[0]["id"]


def test_a_trip_update_writes_the_trip_and_its_note_and_moves_the_version(made, trip_id):
    tags = made.read(OWNER, lambda tx: tx.rows("tag"))
    general = made.read(OWNER, lambda tx: tx.rows("playbook"))[0]
    changes = {"strategy_override": "  Breakout  retest ", "setup_id": general["id"],
               "risk_paise": 50_000, "notes": "Entered late.", "mistakes": [tags[0]["id"], tags[0]["id"]],
               "checklist": json.dumps({"Stop placed": True, "Size within limit": False}),
               "mood": "calm"}  # fmt: skip
    version = made.write(OWNER, lambda tx: update_trip(tx, trip_id, 1, changes, NOW))
    trip = made.read(OWNER, lambda tx: tx.get("round_trip", trip_id))
    note = made.read(OWNER, lambda tx: tx.note_of(trip_id))
    assert version == 2 and trip["version"] == 2
    assert (trip["strategy_override"], trip["setup_id"], trip["risk_paise"]) == (
        "Breakout retest", general["id"], 50_000,
    )  # fmt: skip
    assert note["mistakes"] == [tags[0]["id"]] and note["mood"] == "calm"
    assert json.loads(note["checklist"]) == {"Size within limit": False, "Stop placed": True}
    cleared = made.write(OWNER, lambda tx: update_trip(
        tx, trip_id, 2, {"strategy_override": None, "mood": None}, NOW))  # fmt: skip
    trip = made.read(OWNER, lambda tx: tx.get("round_trip", trip_id))
    assert cleared == 3 and trip["strategy_override"] is None
    assert made.read(OWNER, lambda tx: tx.note_of(trip_id))["notes"] == "Entered late."


def test_a_trip_update_from_a_stale_version_writes_nothing(made, trip_id):
    """Mutation target 24."""
    made.write(OWNER, lambda tx: update_trip(tx, trip_id, 1, {"notes": "first"}, NOW))
    with pytest.raises(VersionConflict) as caught:
        made.write(OWNER, lambda tx: update_trip(tx, trip_id, 1, {"notes": "second"}, NOW))
    assert str(caught.value) == (
        "This trade's notes were changed on another screen. Nothing was saved; open it again."
    )
    assert made.read(OWNER, lambda tx: tx.note_of(trip_id))["notes"] == "first"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"strategy_override": "x" * 41}, "strategy_override"),
        ({"setup_id": 999}, "setup_id"),
        ({"risk_paise": 0}, "risk_paise"),
        ({"risk_paise": 12.5}, "risk_paise"),
        ({"notes": "n" * 4_001}, "notes"),
        ({"mistakes": [999]}, "mistakes"),
        ({"mistakes": "FOMO"}, "mistakes"),
        ({"checklist": "{not json"}, "checklist"),
        ({"checklist": json.dumps({"a": "yes"})}, "checklist"),
        ({"mood": "angry"}, "mood"),
    ],
)
def test_a_trip_update_that_cannot_be_used_names_its_field_and_writes_nothing(made, trip_id,
                                                                              changes, field):  # fmt: skip
    with pytest.raises(Invalid) as caught:
        made.write(OWNER, lambda tx: update_trip(tx, trip_id, 1, {"notes": "x", **changes}, NOW))
    assert caught.value.field == field
    assert made.read(OWNER, lambda tx: tx.note_of(trip_id)) is None
    assert made.read(OWNER, lambda tx: tx.get("round_trip", trip_id))["version"] == 1


def test_a_label_tag_is_not_a_mistake(made, trip_id):
    label = made.write(OWNER, lambda tx: create_tag(tx, "label", "Gap day", "info", None, NOW))
    with pytest.raises(Invalid):
        made.write(OWNER, lambda tx: update_trip(tx, trip_id, 1, {"mistakes": [label["id"]]}, NOW))


def test_an_update_of_a_trip_that_is_gone_says_so(made):
    with pytest.raises(NotFound) as caught:
        made.write(OWNER, lambda tx: update_trip(tx, 999, 1, {"notes": "x"}, NOW))
    assert "not in the journal any more" in str(caught.value)


# ---- tags and playbooks --------------------------------------------------------------------------
def test_a_tag_is_made_once_per_request_and_its_name_is_its_own(made):
    first = made.write(OWNER, lambda tx: create_tag(tx, "label", " Gap  day ", "gold", "r1", NOW))
    again = made.write(OWNER, lambda tx: create_tag(tx, "label", "Other", "gold", "r1", NOW))
    assert again["id"] == first["id"] and first["name"] == "Gap day" and first["colour"] == "gold"
    with pytest.raises(Refused) as caught:
        made.write(OWNER, lambda tx: create_tag(tx, "label", "GAP DAY", "info", None, NOW))
    assert str(caught.value) == "A tag with that name is already there."
    made.write(OWNER, lambda tx: create_tag(tx, "mistake", "Gap day", "loss", None, NOW))
    with pytest.raises(Invalid):
        made.write(OWNER, lambda tx: create_tag(tx, "label", "x" * 41, "info", None, NOW))


def test_a_tag_is_renamed_and_archived_from_its_version(made):
    tag = made.write(OWNER, lambda tx: create_tag(tx, "label", "Gap day", "info", None, NOW))
    changed = made.write(
        OWNER, lambda tx: update_tag(tx, tag["id"], 1, {"name": "Gap up", "archived": True}, NOW)
    )
    assert (changed["name"], changed["archived"], changed["version"]) == ("Gap up", True, 2)
    with pytest.raises(VersionConflict):
        made.write(OWNER, lambda tx: update_tag(tx, tag["id"], 1, {"name": "x"}, NOW))
    fomo = made.read(OWNER, lambda tx: tx.rows("tag", name="FOMO"))[0]
    with pytest.raises(Refused):  # a mistake may share a label's name, not another mistake's
        made.write(OWNER, lambda tx: update_tag(tx, fomo["id"], 1, {"name": "chased"}, NOW))


def test_playbooks_have_at_most_twelve_rules_and_their_own_names(made):
    made_one = made.write(OWNER, lambda tx: create_playbook(
        tx, {"name": "ORB", "rules": [" First 15 min range "], "default_risk_paise": 75_000}, "p1", NOW))  # fmt: skip
    assert made_one["rules"] == ["First 15 min range"] and made_one["description"] == ""
    with pytest.raises(Invalid) as caught:
        made.write(
            OWNER, lambda tx: create_playbook(tx, {"name": "Many", "rules": ["r"] * 13}, None, NOW)
        )
    assert caught.value.field == "rules"
    with pytest.raises(Refused):
        made.write(OWNER, lambda tx: create_playbook(tx, {"name": "general"}, None, NOW))
    changed = made.write(OWNER, lambda tx: update_playbook(
        tx, made_one["id"], 1, {"name_alt": "ओआरबी", "archived": True}, NOW))  # fmt: skip
    assert changed["name_alt"] == "ओआरबी" and changed["archived"] and changed["version"] == 2


# ---- the ops -------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, opened):
        self.store, self.clock = opened, lambda: NOW


def call(op, ctx, **args):
    return asyncio.run(REGISTRY[op](ctx, args))


def test_the_trips_op_lists_open_first_then_newest_closed_with_its_filters(made):
    put(made, [fill("a1", "BUY", 65, 100, "10:01", order_id="o1"),
               fill("a2", "SELL", 65, 101, "10:20", order_id="o1x"),
               fill("b1", "BUY", 65, 50, "11:01", symbol="NIFTY25NOV2625100CE", order_id="o2"),
               fill("c1", "BUY", 65, 60, "09:30", symbol="NIFTY25NOV2625200CE", order_id="o3"),
               fill("c2", "SELL", 65, 59, "12:00", symbol="NIFTY25NOV2625200CE", order_id="o3x")])  # fmt: skip
    ctx = Ctx(made)
    listed = call("trips", ctx)
    assert [t["status"] for t in listed] == ["open", "closed", "closed"]
    assert [t["symbol"][-7:] for t in listed] == ["25100CE", "25200CE", "25000CE"]
    assert set(listed[0]) == set(__import__("sudarshan.journal.views", fromlist=["TRIP"]).TRIP)
    closed_a = next(t for t in listed if t["symbol"].endswith("25000CE"))
    fomo = made.read(OWNER, lambda tx: tx.rows("tag", name="FOMO"))[0]
    made.write(OWNER, lambda tx: update_trip(
        tx, closed_a["id"], 1, {"strategy_override": "Scalp", "mistakes": [fomo["id"]]}, NOW))  # fmt: skip
    assert [t["id"] for t in call("trips", ctx, strategy="scalp")] == [closed_a["id"]]
    assert [t["id"] for t in call("trips", ctx, tag=fomo["id"])] == [closed_a["id"]]
    assert len(call("trips", ctx, status="closed", limit=1)) == 1
    assert call("trips", ctx, mode="sandbox") == []
    with pytest.raises(ApiError):
        call("trips", ctx, limit=0)


def test_trip_get_carries_the_legs_the_note_and_the_orders_latency(made, trip_id):
    made.write(OWNER, lambda tx: tx.order_ref_note("live", "o-a1", NOW, NOW))
    made.write(OWNER, lambda tx: tx.order_ref_set(
        "live", "o-a1", {"strategy": "ORB", "surface": "api", "rtt_ms": 412}, NOW))  # fmt: skip
    ctx = Ctx(made)
    call("trip_update", ctx, id=trip_id, version=1, notes="Good one.")
    detail = call("trip_get", ctx, id=trip_id)
    assert [(leg["trade_id"], leg["role"], leg["qty"]) for leg in detail["legs"]] == [
        ("a1", "entry", 65), ("a2", "exit", 65),
    ]  # fmt: skip
    assert detail["note"]["notes"] == "Good one." and detail["version"] == 2
    assert [o["order_id"] for o in detail["orders"]] == ["o-a1"]
    assert detail["orders"][0]["rtt_ms"] == 412
    with pytest.raises(ApiError) as caught:
        call("trip_get", ctx, id=99)
    assert caught.value.status == 404


def test_the_update_ops_answer_done_and_their_refusals_in_words(made, trip_id):
    ctx = Ctx(made)
    assert call("trip_update", ctx, id=trip_id, version=1, mood="tired") == {
        "id": trip_id, "version": 2, "words": None,
    }  # fmt: skip
    with pytest.raises(ApiError) as stale:
        call("trip_update", ctx, id=trip_id, version=1, mood="calm")
    assert stale.value.status == 409 and stale.value.code == "version_conflict"
    with pytest.raises(ApiError) as bad:
        call("trip_update", ctx, id=trip_id, version=2, mood="angry")
    assert bad.value.status == 400 and bad.value.field == "mood"
    made_tag = call("tag_create", ctx, kind="label", name="Expiry", colour="brand", request_id="t1")
    assert call("tag_create", ctx, kind="label", name="Expiry", request_id="t1") == made_tag
    with pytest.raises(ApiError) as taken:
        call("tag_create", ctx, kind="label", name="expiry")
    assert taken.value.status == 422 and taken.value.code == "refused"
    assert any(t["name"] == "Expiry" for t in call("tags_list", ctx))
    book = call("playbook_create", ctx, name="Gap fill", rules=["Gap over 1%"])
    call("playbook_update", ctx, id=book["id"], version=1, archived=True)
    assert [p["archived"] for p in call("playbooks_list", ctx) if p["id"] == book["id"]] == [True]
