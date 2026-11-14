"""The journal's writes: the day replay, and the owner's own fields (JB4, M14.1, M14.4, M14.5).

**The replay** (design 5.1, as the coordinator's JB4 reads it). After a read wrote or corrected
fills, every instrument and product class those fills touch is paired again with
``core/pairing.py``, from the start of the earliest trip still reaching that moment (a carried
trip begins days before), so a late fill with an earlier exchange time lands where it belongs
(P8). The new trips are matched to the stored ones by their identity, the instant they opened and
their side:

* a stored trip that is made again keeps its id, its version and everything the owner wrote on
  it, and takes the new figures (more legs, its close, a corrected average);
* a stored trip that is not made again has changed: with nothing of the owner's on it, it goes
  (with its legs and price path); with a note, a strategy of the owner's, a playbook or a planned
  risk, it is kept as ``orphan`` with its figures as they were and no legs, so the owner never
  loses what he wrote. If the same trip is made again later (a withdrawn fill comes back), the
  orphan is matched and comes back to life;
* a new trip takes its strategy from the label of its first entry's order.

**What the journal did not see** (review B1). A carry key's first fill is the journal's first
sight of it: the account's overnight positions (a derivative) or holdings (a share) are read once
then, through the portfolio, and what was held is kept in ``key_opening``. The pairing starts from
it (``pairing.held_before``), so the trip that runs off a position held before the journal is
``unmatched``, never an open trade on the other side; a share's sale past what the journal saw
bought is unmatched even with nothing read. A read that does not happen is tried again at the
next replay of the key. An open trip whose contract's own expiry day has passed is ``expired``
(the close check marks the day's own at 16:05, ``expire_open``); the replay keeps a trip the check
marked so, unless a later fill closes it.

Charges are estimated per trip from its orders (``core/charges.py``: brokerage per order, the
rest on each order's turnover), so net = gross - est. charges once it closes. An order shared by
two trips (a reversal) is priced once, whole, and each trip takes its share by quantity, in the
pairing's order, so the day's trips add up to the day's estimate (review M1); whether the trip's
segment is estimated at all is kept beside it (``charges_modelled``). Whether the trip opened
on its own expiry and on an index expiry comes from the master contract, read before the
transaction; a fact that could not be read keeps what the trip had.

**The owner's fields** (trip_update, tags, playbooks): each change names the version it was
decided on and moves it by one; a stale one writes nothing (mutation target 24). The replay never
moves a version.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sudarshan.portfolio.core.kinds import QTY_SCALE

from .core import charges as charges_core
from .core import kinds, pairing
from .core.money import ist_day
from .openalgo_ro import MANUAL, RecordsUnavailable, records_for
from .portfolio import PortfolioUnavailable, link_for
from .store import Invalid, NotFound, Refused, Scope, Tx

log = logging.getLogger("sudarshan.journal.book")

OWNER = Scope.owner()
#: What a trip carries from the pairing, written again at every replay.
PAIRED = (
    "exchange", "symbol", "product", "product_class", "side", "qty_max", "entry_qty", "exit_qty",
    "entry_value_u", "exit_value_u", "entry_avg_u", "exit_avg_u", "opened_ms", "closed_ms",
    "open_day", "close_day", "status", "gross_paise", "hold_s",
)  # fmt: skip
#: What the owner writes on a trip; any of it set keeps the trip as an orphan instead of losing it.
OWNERS_FIELDS = ("strategy_override", "setup_id", "risk_paise")


@dataclass
class Facts:
    """What the master contract says, read before the transaction: each instrument key's own
    expiry, and which of ``checked_days`` an index option expires on. ``plan`` is the charges
    estimate's plan from the settings. ``openings`` are what was held of each carry key read at
    its first sight, ``(exchange, symbol, product class) -> (qty, source)``, as of the start of
    ``opening_day`` (the read's IST day, at ``opening_ms``); the transaction keeps them."""

    expiries: dict[str, int | None] = field(default_factory=dict)
    index_days: set[int] = field(default_factory=set)
    checked_days: set[int] = field(default_factory=set)
    plan: str = charges_core.DEFAULT_PLAN
    openings: dict[tuple[str, str, str], tuple[int, str]] = field(default_factory=dict)
    opening_day: int | None = None
    opening_ms: int | None = None


def _pclass(product: str) -> str | None:
    try:
        return pairing.product_class(product)
    except ValueError:
        return None


def keys_touched(tx: Tx, mode: str, days: Iterable[int]) -> dict[tuple[str, str, str], int]:
    """Each (exchange, symbol, product class) with a fill on ``days``, and its earliest fill."""
    found: dict[tuple[str, str, str], int] = {}
    for day in sorted(set(days)):
        for fill in tx.fills_of(mode, day=day):
            pclass = _pclass(fill["product"])
            if pclass is None:
                continue
            key = (fill["exchange"], fill["symbol"], pclass)
            found[key] = min(found.get(key, fill["exch_ts_ms"]), fill["exch_ts_ms"])
    return found


def expiry_of(exchange: str, symbol: str, instrument_key: str | None,
              expiries: Mapping[str, int | None]) -> int | None:  # fmt: skip
    """A contract's own expiry day: the master's, else the one its symbol carries (the master
    drops a contract once it has expired). A share has none."""
    if str(exchange).upper() in pairing.CASH_EXCHANGES:
        return None
    return expiries.get(instrument_key or "") or pairing.symbol_expiry(symbol)


def owned(tx: Tx, trip: Mapping[str, Any]) -> bool:
    """Whether the owner wrote anything on a trip: a note, a strategy, a playbook, a risk."""
    if any(trip.get(name) is not None for name in OWNERS_FIELDS):
        return True
    return tx.note_of(int(trip["id"])) is not None


OrderKey = tuple[str, int]
#: An order a trip holds part of: the order's every fill of the day, the units of it before the
#: trip's share, and the order's quantity (review M1).
OrderShare = tuple[list[Mapping[str, Any]], int, int]


def _row(fill: Mapping[str, Any], qty: int, refs: Mapping[str, Mapping[str, Any]],
         mode: str) -> dict[str, Any]:  # fmt: skip
    ref = refs.get(fill["order_id"]) or {}
    return {
        "mode": mode, "order_id": fill["order_id"], "day": fill["day"], "action": fill["action"],
        "qty": qty, "price_u": fill["price_u"], "exchange": fill["exchange"],
        "symbol": fill["symbol"], "product": fill["product"], "surface": ref.get("surface"),
    }  # fmt: skip


def portions(legs: Iterable[Mapping[str, Any]],
             by_trade: Mapping[str, Mapping[str, Any]]) -> dict[OrderKey, int]:  # fmt: skip
    """A trip's units of each of its orders, by (order id, day)."""
    out: dict[OrderKey, int] = {}
    for leg in legs:
        fill = by_trade[leg["trade_id"]]
        key = (str(fill["order_id"]), int(fill["day"]))
        out[key] = out.get(key, 0) + int(leg["qty"])
    return out


def charges_of(legs: Iterable[Mapping[str, Any]], by_trade: Mapping[str, Mapping[str, Any]],
               refs: Mapping[str, Mapping[str, Any]], mode: str, plan: str,
               orders: Mapping[OrderKey, OrderShare] | None = None) -> dict[str, Any]:  # fmt: skip
    """The charges estimate of a trip's orders (``ChargeEstimate``). ``legs`` carry ``trade_id``
    and ``qty``. With ``orders`` (each order of the trip: its every fill, the units of it before
    this trip's, its quantity) each order is priced whole and the trip takes its share by
    quantity, so a reversal's order pays one brokerage between its two trips (review M1); without
    them, each leg's part of its fill is priced as an order of its own."""
    legs = list(legs)
    if orders is None:
        rows = [_row(by_trade[leg["trade_id"]], int(leg["qty"]), refs, mode) for leg in legs]
        return charges_core.estimate(rows, plan)
    shared = {}
    for key, take in portions(legs, by_trade).items():
        fills, before, whole = orders[key]
        rows = [_row(f, int(f["qty"]), refs, mode) for f in fills]
        shared[key] = (rows, before, take, whole)
    return charges_core.estimate_shared(shared, plan)


def order_context(tx: Tx, mode: str, order_ids: Iterable[str]) -> dict[OrderKey, list]:
    """Every fill of each order, by (order id, day)."""
    out: dict[OrderKey, list] = {}
    for fill in tx.fills_of_orders(mode, order_ids):
        out.setdefault((str(fill["order_id"]), int(fill["day"])), []).append(fill)
    return out


def trip_values(trip: Mapping[str, Any], by_trade: Mapping[str, Mapping[str, Any]],
                refs: Mapping[str, Mapping[str, Any]], mode: str, facts: Facts,
                orders: Mapping[OrderKey, OrderShare] | None = None) -> dict[str, Any]:  # fmt: skip
    """The columns of a stored trip from a paired one (the owner's fields excepted)."""
    values = {name: trip[name] for name in PAIRED}
    # An unmatched trip may hold no entry the journal saw: its first leg names it then.
    first = next((leg for leg in trip["legs"] if leg["role"] == "entry"), trip["legs"][0])
    fill = by_trade[first["trade_id"]]
    ref = refs.get(fill["order_id"]) or {}
    values["mode"] = mode
    values["instrument_key"] = fill.get("instrument_key") or ""
    values["strategy"] = ref.get("strategy") or MANUAL
    estimate = charges_of(trip["legs"], by_trade, refs, mode, facts.plan, orders)
    est = int(estimate["parts"]["total_paise"])
    values["charges_est_paise"] = est
    values["charges_modelled"] = bool(estimate["modelled"])
    values["net_paise"] = trip["gross_paise"] - est if trip["status"] == "closed" else None
    key = values["instrument_key"]
    if key and key in facts.expiries:
        values["own_expiry_day"] = facts.expiries[key] == trip["open_day"]
    if trip["open_day"] in facts.checked_days:
        values["index_expiry_day"] = trip["open_day"] in facts.index_days
    return values


def _start(tx: Tx, mode: str, key: tuple[str, str, str], anchor: int,
           fills: list[dict[str, Any]], opening: Mapping[str, Any] | None,
           facts: Facts) -> int:  # fmt: skip
    """What was held of a key before the first of ``fills``: from its kept opening, only when
    the window begins at the key's first fill the journal holds (after that every earlier trip
    ended at 0); else 0."""
    exchange, symbol, pclass = key
    if opening is None or tx.fill_before(mode, exchange, symbol, pclass, anchor):
        return 0
    first = next((f for f in fills if f["source"] != "withdrawn"), None)
    expiry = expiry_of(exchange, symbol, (first or {}).get("instrument_key"), facts.expiries)
    return pairing.held_before(
        opening, fills, cash=pairing.cash_carry(exchange, pclass), expiry_day=expiry
    )


def replay_tx(tx: Tx, mode: str, days: Iterable[int], now: int, facts: Facts) -> dict[str, int]:
    """Pair again every instrument the fills of ``days`` touch, and bring the stored trips to
    what the pairing makes now (see the module's words). Returns counts."""
    counts = {"keys": 0, "new": 0, "kept": 0, "orphaned": 0, "forgotten": 0, "unmatched": 0,
              "expired": 0}  # fmt: skip
    today = ist_day(now)
    for key, (qty, source) in facts.openings.items():
        if facts.opening_day is not None and facts.opening_ms is not None:
            tx.opening_put(mode, key, qty, facts.opening_day, source, facts.opening_ms, now)
    openings = tx.openings_of(mode)
    for (exchange, symbol, pclass), from_ms in keys_touched(tx, mode, days).items():
        counts["keys"] += 1
        reaching = tx.trips_of_key(mode, exchange, symbol, pclass, from_ms)
        # An orphan never pulls the window back (it may be months old); one inside the window
        # can still be matched and come back.
        anchor = min([from_ms, *(t["opened_ms"] for t in reaching if t["status"] != "orphan")])
        stored = [t for t in reaching if t["opened_ms"] >= anchor]
        fills = [
            f
            for f in tx.fills_of(mode, since_ms=anchor, key=(exchange, symbol))
            if _pclass(f["product"]) == pclass
        ]
        held = _start(tx, mode, (exchange, symbol, pclass), anchor, fills,
                      openings.get((exchange, symbol, pclass)), facts)  # fmt: skip
        made = pairing.pair_fills(fills, {(mode, exchange, symbol, pclass): held})
        by_trade = {f["trade_id"]: f for f in fills}
        refs = tx.order_refs(mode, {f["order_id"] for f in fills})
        # Each order priced whole: the units of it before the window belong to earlier trips,
        # then each trip of the window takes the next of them, in the pairing's order.
        whole = order_context(tx, mode, {f["order_id"] for f in fills})
        given = {k: sum(int(f["qty"]) for f in v if int(f["exch_ts_ms"]) < anchor)
                 for k, v in whole.items()}  # fmt: skip
        # Trips that open at one instant on one side (fills in the same millisecond) are
        # matched in the order they were made, so a replay never swaps their ids.
        known: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for t in sorted(stored, key=lambda t: int(t["id"])):
            known.setdefault((t["opened_ms"], t["side"]), []).append(t)
        matched: set[int] = set()
        for trip in made:
            shares: dict[OrderKey, OrderShare] = {}
            for key, take in portions(trip["legs"], by_trade).items():
                fills_of_order = whole.get(key) or [by_trade[leg["trade_id"]] for leg in
                                                    trip["legs"]]  # fmt: skip
                total = sum(int(f["qty"]) for f in fills_of_order)
                shares[key] = (fills_of_order, given.get(key, 0), total)
                given[key] = given.get(key, 0) + take
            values = trip_values(trip, by_trade, refs, mode, facts, shares)
            hits = [t for t in known.get((trip["opened_ms"], trip["side"]), [])
                    if t["id"] not in matched]  # fmt: skip
            hit = hits[0] if hits else None
            if values["status"] == pairing.OPEN:
                expiry = expiry_of(exchange, symbol, values["instrument_key"], facts.expiries)
                if hit is not None and hit["status"] == pairing.EXPIRED:
                    values.update(status=pairing.EXPIRED, close_day=hit["close_day"])
                elif pairing.past_expiry(exchange, expiry, today, after_close=False):
                    values.update(status=pairing.EXPIRED, close_day=expiry)
            counts["unmatched"] += values["status"] == pairing.UNMATCHED
            counts["expired"] += values["status"] == pairing.EXPIRED
            if hit is not None:
                trip_id = int(hit["id"])
                tx.touch("round_trip", trip_id, values, now)
                matched.add(trip_id)
                counts["kept"] += 1
            else:
                trip_id = tx.trip_insert(values, now)
                counts["new"] += 1
            legs = [(int(by_trade[leg["trade_id"]]["id"]), leg["role"], leg["qty"])
                    for leg in trip["legs"]]  # fmt: skip
            tx.legs_replace(trip_id, legs, now)
        for trip in stored:
            if trip["id"] in matched or trip["status"] == "orphan":
                continue
            if owned(tx, trip):
                tx.touch("round_trip", int(trip["id"]), {"status": "orphan"}, now)
                tx.legs_replace(int(trip["id"]), [], now)
                counts["orphaned"] += 1
            else:
                tx.trip_forget(int(trip["id"]))
                counts["forgotten"] += 1
    return counts


async def _thread(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _whole(qty_u: Any) -> int:
    """A quantity in ``qty_u`` (x 10^6) as whole units, toward zero; 0 for anything else."""
    if not isinstance(qty_u, int) or isinstance(qty_u, bool):
        return 0
    return qty_u // QTY_SCALE if qty_u >= 0 else -((-qty_u) // QTY_SCALE)


async def _rows(read) -> list[Mapping[str, Any]] | None:
    """The rows of one portfolio read, or None when it did not happen (asked again later)."""
    try:
        answer = await read()
    except PortfolioUnavailable:
        return None
    if answer.get("state") != "ok" or not isinstance(answer.get("data"), list):
        return None
    return [r for r in answer["data"] if isinstance(r, Mapping)]


async def read_openings(service: Any, need: Mapping[tuple[str, str, str], str]) -> Facts:
    """What was held of each carry key at first sight (``need``: key -> instrument key), as of
    the start of today: a derivative from Upstox's overnight quantity in a carry product, a share
    from its holdings (settled and yesterday's). Each read is one call for every key; a key whose
    read did not happen is left out, so the next replay asks again."""
    now = service.clock()
    facts = Facts(opening_day=ist_day(now), opening_ms=now)
    link = link_for(service)
    shares = {k: ik for k, ik in need.items() if pairing.cash_carry(k[0], k[2])}
    others = {k: ik for k, ik in need.items() if k not in shares}
    if others and (rows := await _rows(link.positions)) is not None:
        held: dict[str, int] = {}
        for row in rows:
            if str(row.get("product") or "").upper() in ("I", "CO", "MIS"):
                continue
            ik = str(row.get("instrument_key") or "")
            held[ik] = held.get(ik, 0) + _whole(row.get("overnight_qty_u"))
        for key, ik in others.items():
            facts.openings[key] = (held.get(ik, 0), "positions")
    if shares and (rows := await _rows(link.holdings)) is not None:
        held = {}
        for row in rows:
            ik = str(row.get("instrument_key") or "")
            held[ik] = held.get(ik, 0) + _whole(row.get("qty_u")) + _whole(row.get("t1_qty_u"))
        for key, ik in shares.items():
            facts.openings[key] = (held.get(ik, 0), "holdings")
    return facts


async def gather_facts(service: Any, mode: str, days: Iterable[int]) -> Facts:
    """The master's facts for the fills of ``days``, the settings' plan, and what was held of
    each live carry key seen for the first time, read off the loop."""
    store, wanted = service.store, sorted(set(days))

    def read(tx):
        fills = [f for d in wanted for f in tx.fills_of(mode, day=d)]
        need: dict[tuple[str, str, str], str] = {}
        if mode == "live":
            kept = tx.openings_of(mode)
            for f in fills:
                key = (f["exchange"], f["symbol"], _pclass(f["product"]))
                if key[2] == "carry" and key not in kept and f["source"] != "withdrawn":
                    need.setdefault(key, f.get("instrument_key") or "")
        keys = {f["instrument_key"] for f in fills} - {""}
        return keys, tx.settings().get("charges_plan") or charges_core.DEFAULT_PLAN, need

    keys, plan, need = await _thread(lambda: store.read(OWNER, read))
    facts = await read_openings(service, need) if need else Facts()
    facts.plan = plan if plan in kinds.CHARGE_PLANS else charges_core.DEFAULT_PLAN
    records = records_for(service)
    try:
        listings = await _thread(lambda: records.instruments(keys))
        facts.expiries = {k: listings[k].expiry_day for k in keys if k in listings}
    except RecordsUnavailable:
        pass
    try:
        facts.index_days = await _thread(lambda: records.index_expiry_days(wanted))
        facts.checked_days = set(wanted)
    except RecordsUnavailable:
        pass
    return facts


async def replay(service: Any, mode: str, days: Iterable[int]) -> dict[str, int]:
    """The replay of ``days`` for ``mode``, in one store transaction; counts logged."""
    wanted = sorted(set(days))
    if not wanted or service.store is None:
        return {}
    facts = await gather_facts(service, mode, wanted)
    now, store = service.clock(), service.store
    counts = await _thread(
        lambda: store.write(OWNER, lambda tx: replay_tx(tx, mode, wanted, now, facts))
    )
    log.info(
        "%s trips paired again over %d days: %d instruments, %d new, %d kept, %d orphaned, "
        "%d gone, %d not seen opened, %d ended at expiry", mode, len(wanted), counts["keys"],
        counts["new"], counts["kept"], counts["orphaned"], counts["forgotten"],
        counts["unmatched"], counts["expired"],
    )  # fmt: skip
    return counts


async def expire_open(service: Any, now: int) -> int:
    """The close check's part (review B1): every open trip whose contract can no longer trade
    (its own expiry day has passed, or it is today and its derivatives closed at 15:30) becomes
    ``expired``, ended on its expiry day and out of every figure. Returns how many."""
    store, today = service.store, ist_day(now)

    def read(tx):
        return [t for t in tx.rows("round_trip", status=pairing.OPEN)
                if str(t["exchange"]).upper() not in pairing.CASH_EXCHANGES]  # fmt: skip

    trips = await _thread(lambda: store.read(OWNER, read))
    if not trips:
        return 0
    keys = {t["instrument_key"] for t in trips} - {""}
    try:
        listings = await _thread(lambda: records_for(service).instruments(keys))
        expiries = {k: listings[k].expiry_day for k in keys if k in listings}
    except RecordsUnavailable:
        expiries = {}
    due = {}
    for trip in trips:
        expiry = expiry_of(trip["exchange"], trip["symbol"], trip["instrument_key"], expiries)
        if pairing.past_expiry(trip["exchange"], expiry, today, after_close=True):
            due[int(trip["id"])] = expiry
    if not due:
        return 0

    def write(tx):
        done = 0
        for trip_id, expiry in due.items():
            row = tx.get("round_trip", trip_id)
            if row is not None and row["status"] == pairing.OPEN:
                tx.touch("round_trip", trip_id, {"status": pairing.EXPIRED, "close_day": expiry},
                         now)  # fmt: skip
                done += 1
        return done

    done = await _thread(lambda: store.write(OWNER, write))
    log.info("%d open trades ended at their contract's expiry", done)
    return done


# ---- the owner's fields ------------------------------------------------------------------------
WORDS = {
    "trip_gone": "This trade is not in the journal any more. Open the list again.",
    "strategy": "A strategy name may be at most 40 characters.",
    "setup": "That playbook is not in the journal. Pick another one.",
    "risk": "The planned risk must be a whole rupee amount above zero, or left empty.",
    "notes": "Notes may be at most 4,000 characters.",
    "mistakes": "Pick mistakes from the list; one of them is not in the journal.",
    "checklist": "The checklist could not be read. Open the trade again and tick it once more.",
    "mood": "Pick one of the six moods, or none.",
    "tag_name": "A tag needs a name of at most 40 characters.",
    "tag_taken": "A tag with that name is already there.",
    "tag_gone": "This tag is no longer there; it may have been deleted on another screen.",
    "playbook_name": "A playbook needs a name of at most 40 characters.",
    "playbook_taken": "A playbook with that name is already there.",
    "playbook_gone": "This playbook is no longer there; it may have been deleted on another screen.",
    "rules": "A playbook has at most 12 checklist items, each at most 120 characters.",
    "description": "A description may be at most 1,000 characters.",
}
MAX_RISK_PAISE = 10**12
MAX_MISTAKES = 20
CHECKLIST_ITEMS = 40
CHECKLIST_TEXT = 4_000
RULE_TEXT = 120
DESCRIPTION_TEXT = 1_000


def checklist_text(value: str) -> str:
    """The checklist as the trade sheet writes it and as it is kept: JSON text of the rules and
    their answers, ``[{"rule": "Stop placed", "yes": true}, ...]``, at most 40 of them; empty
    text clears it."""
    if not isinstance(value, str) or len(value) > CHECKLIST_TEXT:
        raise Invalid(WORDS["checklist"], "checklist")
    if not value.strip():
        return ""
    try:
        found = json.loads(value)
    except ValueError:
        raise Invalid(WORDS["checklist"], "checklist") from None
    if not isinstance(found, list) or len(found) > CHECKLIST_ITEMS:
        raise Invalid(WORDS["checklist"], "checklist")
    items = []
    for item in found:
        rule = item.get("rule") if isinstance(item, dict) else None
        yes = item.get("yes") if isinstance(item, dict) else None
        if not isinstance(rule, str) or not rule.strip() or len(rule) > RULE_TEXT:
            raise Invalid(WORDS["checklist"], "checklist")
        if not isinstance(yes, bool):
            raise Invalid(WORDS["checklist"], "checklist")
        items.append({"rule": " ".join(rule.split()), "yes": yes})
    return json.dumps(items, ensure_ascii=False, separators=(",", ":")) if items else ""


def update_trip(tx: Tx, trip_id: int, version: int, changes: Mapping[str, Any], now: int) -> int:
    """The owner's change of a trip and its note, from ``version``; returns the new version.
    Every field is checked first, so a refusal writes nothing."""
    trip = tx.get("round_trip", trip_id)
    if trip is None:
        raise NotFound(WORDS["trip_gone"])
    own: dict[str, Any] = {}
    note: dict[str, Any] = {}
    if "strategy_override" in changes:
        value = changes["strategy_override"]
        if value is not None and (not isinstance(value, str) or len(value.strip()) > 40):
            raise Invalid(WORDS["strategy"], "strategy_override")
        own["strategy_override"] = (" ".join(value.split()) or None) if value else None
    if "setup_id" in changes:
        value = changes["setup_id"]
        if value is not None and (value.__class__ is not int or tx.get("playbook", value) is None):
            raise Invalid(WORDS["setup"], "setup_id")
        own["setup_id"] = value
    if "risk_paise" in changes:
        value = changes["risk_paise"]
        if value is not None and (value.__class__ is not int or not 0 < value <= MAX_RISK_PAISE):
            raise Invalid(WORDS["risk"], "risk_paise")
        own["risk_paise"] = value
    if "notes" in changes:
        value = changes["notes"]
        if not isinstance(value, str) or len(value) > kinds.LIMITS["notes"]:
            raise Invalid(WORDS["notes"], "notes")
        note["notes"] = value
    if "mistakes" in changes:
        value = changes["mistakes"]
        if (
            not isinstance(value, list)
            or len(value) > MAX_MISTAKES
            or not all(v.__class__ is int for v in value)
        ):
            raise Invalid(WORDS["mistakes"], "mistakes")
        ids = list(dict.fromkeys(value))
        for tag_id in ids:
            tag = tx.get("tag", tag_id)
            if tag is None or tag["kind"] != "mistake":
                raise Invalid(WORDS["mistakes"], "mistakes")
        note["mistakes"] = ids
    if "checklist" in changes:
        note["checklist"] = checklist_text(changes["checklist"])
    if "mood" in changes:
        value = changes["mood"]
        if value is not None and value not in kinds.MOODS:
            raise Invalid(WORDS["mood"], "mood")
        note["mood"] = value
    done = tx.change("round_trip", trip_id, version, own, now, "version_conflict_trip")
    if note:
        tx.note_put(trip_id, note, now)
    return int(done["version"])


def _name(value: Any, word: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
        raise Invalid(WORDS[word], "name")
    return " ".join(value.split())


def _taken(tx: Tx, table: str, name: str, keep_id: int | None = None, **equal: Any) -> bool:
    for row in tx.rows(table, **equal):
        if row["name"].casefold() == name.casefold() and row["id"] != keep_id:
            return True
    return False


def create_tag(tx: Tx, kind: str, name: str, colour: str, request_id: str | None,
               now: int) -> dict[str, Any]:  # fmt: skip
    if request_id is not None:
        made = tx.by_request("tag", request_id)
        if made is not None:
            return made
    clean = _name(name, "tag_name")
    if _taken(tx, "tag", clean, kind=kind):
        raise Refused(WORDS["tag_taken"], "name")
    return tx.insert("tag", {"kind": kind, "name": clean, "colour": colour}, now, request_id)


def update_tag(tx: Tx, tag_id: int, version: int, changes: Mapping[str, Any],
               now: int) -> dict[str, Any]:  # fmt: skip
    tag = tx.get("tag", tag_id)
    if tag is None:
        raise NotFound(WORDS["tag_gone"])
    values: dict[str, Any] = {}
    if "name" in changes:
        values["name"] = _name(changes["name"], "tag_name")
        if _taken(tx, "tag", values["name"], tag_id, kind=tag["kind"]):
            raise Refused(WORDS["tag_taken"], "name")
    for key in ("colour", "archived"):
        if key in changes:
            values[key] = changes[key]
    return tx.change("tag", tag_id, version, values, now)


def _rules(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > kinds.LIMITS["playbook_rules"]:
        raise Invalid(WORDS["rules"], "rules")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > RULE_TEXT:
            raise Invalid(WORDS["rules"], "rules")
        out.append(" ".join(item.split()))
    return out


def _playbook_values(changes: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "name" in changes:
        values["name"] = _name(changes["name"], "playbook_name")
    if "name_alt" in changes:
        alt = changes["name_alt"]
        if alt is not None and (not isinstance(alt, str) or len(alt.strip()) > 40):
            raise Invalid(WORDS["playbook_name"], "name_alt")
        values["name_alt"] = (alt.strip() or None) if alt else None
    if "description" in changes:
        text = changes["description"]
        if not isinstance(text, str) or len(text) > DESCRIPTION_TEXT:
            raise Invalid(WORDS["description"], "description")
        values["description"] = text.strip()
    if "rules" in changes:
        values["rules"] = _rules(changes["rules"])
    if "default_risk_paise" in changes:
        risk = changes["default_risk_paise"]
        if risk is not None and (risk.__class__ is not int or not 0 < risk <= MAX_RISK_PAISE):
            raise Invalid(WORDS["risk"], "default_risk_paise")
        values["default_risk_paise"] = risk
    if "archived" in changes:
        values["archived"] = changes["archived"]
    return values


def create_playbook(tx: Tx, changes: Mapping[str, Any], request_id: str | None,
                    now: int) -> dict[str, Any]:  # fmt: skip
    if request_id is not None:
        made = tx.by_request("playbook", request_id)
        if made is not None:
            return made
    values = _playbook_values({"description": "", "rules": [], **changes})
    if "name" not in values:
        raise Invalid(WORDS["playbook_name"], "name")
    if _taken(tx, "playbook", values["name"]):
        raise Refused(WORDS["playbook_taken"], "name")
    return tx.insert("playbook", values, now, request_id)


def update_playbook(tx: Tx, playbook_id: int, version: int, changes: Mapping[str, Any],
                    now: int) -> dict[str, Any]:  # fmt: skip
    if tx.get("playbook", playbook_id) is None:
        raise NotFound(WORDS["playbook_gone"])
    values = _playbook_values(changes)
    if "name" in values and _taken(tx, "playbook", values["name"], playbook_id):
        raise Refused(WORDS["playbook_taken"], "name")
    return tx.change("playbook", playbook_id, version, values, now)
