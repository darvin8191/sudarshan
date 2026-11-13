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

Charges are estimated per trip from its legs (``core/charges.py``: brokerage per order, the rest
on each order's turnover), so net = gross - est. charges once it closes. Whether the trip opened
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

from .core import charges as charges_core
from .core import kinds, pairing
from .openalgo_ro import MANUAL, RecordsUnavailable, records_for
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
    estimate's plan from the settings."""

    expiries: dict[str, int | None] = field(default_factory=dict)
    index_days: set[int] = field(default_factory=set)
    checked_days: set[int] = field(default_factory=set)
    plan: str = charges_core.DEFAULT_PLAN


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


def owned(tx: Tx, trip: Mapping[str, Any]) -> bool:
    """Whether the owner wrote anything on a trip: a note, a strategy, a playbook, a risk."""
    if any(trip.get(name) is not None for name in OWNERS_FIELDS):
        return True
    return tx.note_of(int(trip["id"])) is not None


def _charges(trip: Mapping[str, Any], by_trade: Mapping[str, Mapping[str, Any]],
             refs: Mapping[str, Mapping[str, Any]], mode: str, plan: str) -> int:  # fmt: skip
    rows = []
    for leg in trip["legs"]:
        fill = by_trade[leg["trade_id"]]
        ref = refs.get(fill["order_id"]) or {}
        rows.append({
            "mode": mode, "order_id": fill["order_id"], "day": fill["day"],
            "action": fill["action"], "qty": leg["qty"], "price_u": fill["price_u"],
            "exchange": fill["exchange"], "symbol": fill["symbol"], "product": fill["product"],
            "surface": ref.get("surface"),
        })  # fmt: skip
    return int(charges_core.estimate(rows, plan)["parts"]["total_paise"])


def trip_values(trip: Mapping[str, Any], by_trade: Mapping[str, Mapping[str, Any]],
                refs: Mapping[str, Mapping[str, Any]], mode: str,
                facts: Facts) -> dict[str, Any]:  # fmt: skip
    """The columns of a stored trip from a paired one (the owner's fields excepted)."""
    values = {name: trip[name] for name in PAIRED}
    first = next(leg for leg in trip["legs"] if leg["role"] == "entry")
    fill = by_trade[first["trade_id"]]
    ref = refs.get(fill["order_id"]) or {}
    values["mode"] = mode
    values["instrument_key"] = fill.get("instrument_key") or ""
    values["strategy"] = ref.get("strategy") or MANUAL
    est = _charges(trip, by_trade, refs, mode, facts.plan)
    values["charges_est_paise"] = est
    values["net_paise"] = trip["gross_paise"] - est if trip["status"] == "closed" else None
    key = values["instrument_key"]
    if key and key in facts.expiries:
        values["own_expiry_day"] = facts.expiries[key] == trip["open_day"]
    if trip["open_day"] in facts.checked_days:
        values["index_expiry_day"] = trip["open_day"] in facts.index_days
    return values


def replay_tx(tx: Tx, mode: str, days: Iterable[int], now: int, facts: Facts) -> dict[str, int]:
    """Pair again every instrument the fills of ``days`` touch, and bring the stored trips to
    what the pairing makes now (see the module's words). Returns counts."""
    counts = {"keys": 0, "new": 0, "kept": 0, "orphaned": 0, "forgotten": 0}
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
        made = pairing.pair_fills(fills)
        by_trade = {f["trade_id"]: f for f in fills}
        refs = tx.order_refs(mode, {f["order_id"] for f in fills})
        known = {(t["opened_ms"], t["side"]): t for t in stored}
        matched: set[int] = set()
        for trip in made:
            values = trip_values(trip, by_trade, refs, mode, facts)
            hit = known.get((trip["opened_ms"], trip["side"]))
            if hit is not None and hit["id"] not in matched:
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


async def gather_facts(service: Any, mode: str, days: Iterable[int]) -> Facts:
    """The master's facts for the fills of ``days`` and the settings' plan, read off the loop."""
    store, wanted = service.store, sorted(set(days))

    def read(tx):
        keys = {f["instrument_key"] for d in wanted for f in tx.fills_of(mode, day=d)}
        return keys - {""}, tx.settings().get("charges_plan") or charges_core.DEFAULT_PLAN

    keys, plan = await _thread(lambda: store.read(OWNER, read))
    facts = Facts(plan=plan if plan in kinds.CHARGE_PLANS else charges_core.DEFAULT_PLAN)
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
        "%d gone", mode, len(wanted), counts["keys"], counts["new"], counts["kept"],
        counts["orphaned"], counts["forgotten"],
    )  # fmt: skip
    return counts


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
    """The checklist as saved: a JSON object of item to yes or no, at most 40 items."""
    if not isinstance(value, str) or len(value) > CHECKLIST_TEXT:
        raise Invalid(WORDS["checklist"], "checklist")
    try:
        found = json.loads(value or "{}")
    except ValueError:
        raise Invalid(WORDS["checklist"], "checklist") from None
    if (
        not isinstance(found, dict)
        or len(found) > CHECKLIST_ITEMS
        or not all(isinstance(k, str) and len(k) <= RULE_TEXT for k in found)
        or not all(isinstance(v, bool) for v in found.values())
    ):
        raise Invalid(WORDS["checklist"], "checklist")
    return json.dumps(found, sort_keys=True, ensure_ascii=False)


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
