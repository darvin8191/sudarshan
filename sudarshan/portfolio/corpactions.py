"""The nightly read of Upstox's corporate actions (phase P7, lane C: M7.36, design p7 6.2).

Upstox lists a company's dividends, bonus issues, splits and rights issues by ISIN
(``GET /v2/fundamentals/{isin}/corporate-actions``, under Fundamentals: open to the read-only
Analytics Token, with no static IP). They are public facts, kept in ``ca_event`` for everyone
(``migrations.SHARED_TABLES``); what the owner decided about one is his own (``ca_decision``).

* **Which instruments.** The ISINs the Upstox account holds (its last closing record and the
  holdings last read), the listed shares the custom tabs have entries for, and the shares in
  Upstox's trade history (a split of a share sold since still changes its lots in the tax paper).
* **When.** Once a night from 23:45, after the NAV job, or before 08:00 when the night's run was
  missed; never in market hours. An instrument is read once a week in rotation, about a seventh
  of them a night; one with an event inside 14 days is read every night; one never read comes
  first (``core.corpactions.tonight``).
* **Inside its own budget.** This is not market data, so it does not go through the feed's
  request governor (whose list is market-data GETs). It spends from a ``Budget`` of its own, one
  API name, 1 a second, 20 a minute and 200 in 30 minutes (a tenth of Upstox's per-API figures),
  paced so that the minute bucket is never the stop, with the same 60 second pause on a 429 and
  the same pause on a refused token as the live book (``UpstoxReader``), through its token and
  its one HTTP client. No thread, task or client of its own.
* **Nothing is guessed.** A row that cannot be read is left out and counted in the read's note;
  an answer that is not Upstox's shape changes nothing; an event Upstox no longer lists is marked
  gone and kept, and comes back if it is listed again.
* **Nothing changes a lot here.** A bonus or a split is only stored. It reaches the lots when the
  owner accepts it (``ops/ca_ops.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

#: The one rule for "held the day before the ex-date" is the tax core's.
from sudarshan.tax.dividends import entitled_qty_u

from .budget import PAUSE_S, Budget
from .core import corpactions as core
from .core.days import add_days, day_of_instant, financial_year
from .core.kinds import CA_DECISIONS, REPORT_YEARS
from .core.ledger import replay
from .core.model import BadEntry, Entry, Refusal
from .core.words import date as day_words
from .store import Invalid, NotFound, Refused, Scope, Tx
from .upstox import _signature, minute_of_day

log = logging.getLogger("sudarshan.portfolio.corpactions")

OWNER = Scope.owner()
API = "corporate_actions"
PATH = "/v2/fundamentals/{isin}/corporate-actions"
#: 1 a second, 20 a minute, 200 in 30 minutes: a tenth of Upstox's figures for one API.
LIMITS = ((1, 1.0), (20, 60.0), (200, 1800.0))
#: Seconds between two reads of a run (under 20 a minute), and the most reads of one run.
PACE_S = 3.1
MAX_READS = 150
#: The run begins at 23:45 IST; a night that was missed is made up before 08:00.
RUN_AT_MIN = 23 * 60 + 45
CATCH_UP_BEFORE_MIN = 8 * 60
#: A run ends after this many reads in a row that Upstox did not answer.
SILENT_STOP = 3
#: One read on demand a minute (the owner opening an instrument's actions).
ON_DEMAND_EVERY_MS = 60_000
ISIN = re.compile(r"IN[A-Z0-9]{10}")
#: Outcomes that end a run: asking again tonight would only repeat them.
STOPS = ("slow", "busy", "no_token", "token_refused")
WORDS = {
    "none": "Upstox lists no corporate actions for this instrument.",
    "slow": "Upstox asked SUDARSHAN to slow down; the rest is read tomorrow night.",
    "busy": "SUDARSHAN has read Upstox as often as it allows for now; the rest is read "
    "tomorrow night.",
    "no_token": "SUDARSHAN cannot find its Upstox token on this server.",
    "token_refused": "Upstox did not accept SUDARSHAN's read-only token.",
    "silent": "Upstox did not answer; the rest is read tomorrow night.",
}


def isin_of_key(instrument_key: Any) -> str | None:
    """The ISIN inside an Upstox instrument key (``NSE_EQ|INE...``); None when it holds none."""
    tail = instrument_key.rpartition("|")[2] if isinstance(instrument_key, str) else ""
    return tail if ISIN.fullmatch(tail) else None


def event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(core.identity(event).encode("utf-8")).hexdigest()


def upstox_isins(tx: Tx, held_now: list[dict[str, Any]] | None = None) -> set[str]:
    """The ISINs of the Upstox account: held at its last closing record or at the last read of
    its holdings, or in its trade history."""
    found = {row.get("isin") for row in held_now or ()}
    days = [row["day"] for row in tx.rows("upstox_eod", "day")]
    if days:
        lines = tx.rows("upstox_eod_holding", "instrument_key", day=days[-1])
        found |= {isin_of_key(line["instrument_key"]) for line in lines}
    for record in tx.report_list("trades"):
        rows = (record.get("payload") or {}).get("rows") or []
        found |= {row.get("isin") for row in rows if isinstance(row, dict)}
    return {isin for isin in found if isinstance(isin, str) and ISIN.fullmatch(isin)}


def known_isins(tx: Tx, held_now: list[dict[str, Any]] | None = None) -> set[str]:
    """Every ISIN whose corporate actions matter to the owner (the module's first point)."""
    used = {row["asset_id"] for row in tx.rows("activity")}
    tabs = {a["isin"] for a in tx.rows("asset", kind="listed") if a["id"] in used}
    return upstox_isins(tx, held_now) | {i for i in tabs if i and ISIN.fullmatch(i)}


def due_day(now: int) -> int | None:
    """The night a run at ``now`` belongs to (the day it began on), or None outside its hours."""
    minute, today = minute_of_day(now), day_of_instant(now)
    if minute >= RUN_AT_MIN:
        return today
    return add_days(today, -1) if minute < CATCH_UP_BEFORE_MIN else None


class CorpActions:
    """Reads corporate actions through the live book's token and client, and keeps them."""

    def __init__(
        self,
        service: Any,
        upstox: Any,
        *,
        budget: Budget | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        mono: Callable[[], float] = time.monotonic,
    ):
        self.service, self.upstox = service, upstox
        self.budget = budget or Budget(LIMITS)
        self._sleep, self._mono = sleep, mono
        self._last = float("-inf")
        self._asked_at: int | None = None

    # ---- one call --------------------------------------------------------------------------
    async def _call(self, isin: str) -> tuple[str, Any]:
        """``("ok", payload)`` or ``("<why>", None)``; spends one read of the budget."""
        upstox = self.upstox
        if not ISIN.fullmatch(isin):
            return "none", None
        if upstox._refused:
            if _signature(upstox.token_file) == upstox._refused_at:
                return "token_refused", None
            upstox._refused = False
            upstox._told.discard("401")
        if self.budget.paused_for(API):
            return "slow", None
        token = upstox._token()
        if token is None:
            return "no_token", None
        if not self.budget.take(API):
            return "busy", None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            answer = await upstox.client.get(
                upstox.base_url + PATH.format(isin=isin), headers=headers
            )
        except httpx.HTTPError as exc:
            log.warning("Upstox corporate actions did not answer (%s)", type(exc).__name__)
            return "silent", None
        finally:
            del token, headers
        status = answer.status_code
        if status == 401:
            upstox._refused, upstox._refused_at = True, _signature(upstox.token_file)
            log.warning("Upstox refused the read-only token; reads pause until it changes.")
            return "token_refused", None
        if status == 429 or upstox._rate_coded(answer):
            self.budget.pause(API, PAUSE_S)
            log.warning("Upstox asked for a pause on corporate actions: %d seconds", int(PAUSE_S))
            return "slow", None
        if status >= 500:
            log.warning("Upstox corporate actions answered with a server fault (%d)", status)
            return "silent", None
        if status != 200:
            return "none", None
        try:
            return "ok", json.loads(answer.text, parse_float=Decimal)
        except ValueError:
            return "unreadable", None

    # ---- one instrument --------------------------------------------------------------------
    async def read_one(self, isin: str, now: int) -> str:
        """Read one ISIN and keep what it lists. The outcome: ``ok``, ``none`` (Upstox has
        nothing under this ISIN; asked again in a week), ``unreadable`` or ``silent`` (nothing
        changed; asked again tomorrow), or one of ``STOPS``."""
        outcome, payload = await self._call(isin)
        if outcome == "ok":
            try:
                parsed = core.parse_answer(isin, payload)
            except ValueError:
                outcome = "unreadable"
        if outcome == "unreadable":
            log.warning("a corporate-actions answer could not be read; nothing was changed")
        if outcome not in ("ok", "none"):
            return outcome

        def write(tx: Tx) -> None:
            if outcome == "none":
                tx.ca_read_put(isin, now, "none", WORDS["none"])
                return
            listed = []
            for event in parsed["events"]:
                listed.append(event_hash(event))
                tx.ca_event_put({**event, "raw_hash": listed[-1]}, now)
            tx.ca_events_gone(isin, listed, now)
            left = sum(parsed["skipped"].values())
            note = f"{left} rows could not be read and were left out." if left else None
            tx.ca_read_put(isin, now, "ok", note)

        await self._store(lambda store: store.write(OWNER, write))
        return outcome

    async def _store(self, call: Callable[[Any], Any]) -> Any:
        store = self.service.store
        return await asyncio.get_running_loop().run_in_executor(None, lambda: call(store))

    # ---- the night's run -------------------------------------------------------------------
    def _held_now(self) -> list[dict[str, Any]]:
        memory = getattr(self.upstox, "_memory", {}).get("holdings")
        return list(getattr(memory, "data", None) or [])

    async def plan(self, now: int) -> list[str]:
        today, held = day_of_instant(now), self._held_now()

        def look(tx: Tx) -> list[str]:
            reads = {r["isin"]: r["read_at"] for r in tx.ca_reads()}
            days = {k: None if v is None else day_of_instant(v) for k, v in reads.items()}
            soon = tx.ca_events(from_day=today, to_day=add_days(today, core.NEAR_DAYS))
            near = {event["isin"] for event in soon}
            return core.tonight(sorted(known_isins(tx, held)), days, near, today, MAX_READS)

        return await self._store(lambda store: store.read(OWNER, look))

    async def _pace(self) -> None:
        wait = self._last + PACE_S - self._mono()
        if wait > 0:
            await self._sleep(wait)
        self._last = self._mono()

    async def run(self, now: int) -> tuple[str, str]:
        """One night's reads. Returns ``(status, note)`` for the job log."""
        todo = await self.plan(now)
        counts = {"ok": 0, "none": 0, "failed": 0}
        silent, stop = 0, None
        for isin in todo:
            await self._pace()
            outcome = await self.read_one(isin, self.service.clock())
            if outcome in STOPS:
                stop = outcome
                break
            counts[outcome if outcome in counts else "failed"] += 1
            silent = silent + 1 if outcome == "silent" else 0
            if silent >= SILENT_STOP:
                stop = "silent"
                break
        note = (
            f"{counts['ok']} of {len(todo)} instruments read, {counts['none']} with nothing "
            f"listed, {counts['failed']} not read"
        )
        if stop is not None:
            note = f"{note}. {WORDS[stop]}"
        # ``kinds.JOB_STATUSES``: a night that read nothing of what was due has failed.
        return ("failed" if todo and not counts["ok"] + counts["none"] else "ok"), note

    # ---- on demand -------------------------------------------------------------------------
    async def read_now(self, isin: str, now: int) -> str:
        """One read the owner asked for: only an instrument that matters to him, and at most one
        a minute. ``wait`` when it is too soon, ``unknown`` for any other ISIN."""
        held = self._held_now()
        known = await self._store(lambda s: s.read(OWNER, lambda tx: known_isins(tx, held)))
        if isin not in known:
            return "unknown"
        if self._asked_at is not None and 0 <= now - self._asked_at < ON_DEMAND_EVERY_MS:
            return "wait"
        self._asked_at = now
        return await self.read_one(isin, now)


# ---- what was held, and the owner's decisions (design p7 6.3) -----------------------------------
UPSTOX_SCOPE = "upstox"
RATIO_MAX = 1_000_000
SAY = {
    "kind": "Only a bonus or a split is confirmed here.",
    "scope": "Choose the Upstox account or one of your tabs.",
    "ratio": "Choose the ratio as it is shown, or the other way round.",
    "ratio_missing": "Enter the ratio as two whole numbers above zero.",
    "no_ratio": "Upstox gave no ratio for this one. Enter it from the company's notice.",
}


class Holdings:
    """What the Upstox account and each custom tab held the day before a day, read once."""

    def __init__(self, tx: Tx, today: int):
        self.tx, self.today = tx, today
        self.assets = {a["isin"]: a for a in tx.rows("asset", kind="listed") if a["isin"]}
        self.tabs = [t for t in tx.rows("tab") if not t["system"] and not t["archived"]]
        self.record_days = [row["day"] for row in tx.rows("upstox_eod", "day")]
        self._records: dict[int, dict[str, int]] = {}
        self._moves: dict[int, dict[tuple[int, int], list[tuple[int, int]]]] = {}

    def name(self, isin: str) -> str | None:
        asset = self.assets.get(isin)
        return asset["name"] if asset else None

    def upstox(self, isin: str, ex_day: int) -> int | None:
        """The Upstox account's quantity the day before ``ex_day``; None when no closing record
        can say (``core.eve_record``)."""
        day = core.eve_record(self.record_days, ex_day, self.today)
        if day is None:
            return None
        if day not in self._records:
            held: dict[str, int] = {}
            for line in self.tx.rows("upstox_eod_holding", "instrument_key", day=day):
                key = isin_of_key(line["instrument_key"])
                if key is not None:
                    held[key] = held.get(key, 0) + line["qty"] + (line["t1_qty"] or 0)
            self._records[day] = held
        return self._records[day].get(isin, 0)

    def moves(self, tab_id: int) -> dict[tuple[int, int], list[tuple[int, int]]]:
        """``(account, asset) -> [(day, quantity change)]`` of a tab's ledger."""
        if tab_id not in self._moves:
            found: dict[tuple[int, int], list[tuple[int, int]]] = {}
            try:
                ledger = replay([Entry.of(row) for row in self.tx.entries(tab_id)])
            except (Refusal, BadEntry):  # a ledger that does not replay says nothing here
                ledger = None
            for move in ledger.journal if ledger is not None else ():
                if move.qty_u and move.asset_id is not None:
                    key = (move.account_id, move.asset_id)
                    found.setdefault(key, []).append((move.entry.trade_date, move.qty_u))
            self._moves[tab_id] = found
        return self._moves[tab_id]

    def tab(self, tab_id: int, isin: str, ex_day: int) -> dict[int, int]:
        """A tab's quantity per account the day before ``ex_day`` (accounts holding none left
        out). The rule is the tax core's: a purchase on the ex-date does not count."""
        asset = self.assets.get(isin)
        if asset is None:
            return {}
        held = {
            account: entitled_qty_u(changes, ex_day)
            for (account, asset_id), changes in self.moves(tab_id).items()
            if asset_id == asset["id"]
        }
        return {account: qty for account, qty in held.items() if qty > 0}

    def entry_of(self, tab_id: int, event: dict[str, Any]) -> int | None:
        """The tab's own split or bonus entry for this event, when the owner already made one."""
        asset = self.assets.get(event["isin"])
        for row in self.tx.entries(tab_id) if asset else ():
            if (row["status"], row["kind"], row["asset_id"], row["trade_date"]) == (
                "posted", event["kind"].upper(), asset["id"], event["ex_day"],
            ):  # fmt: skip
                return row["id"]
        return None


def _way(event: dict[str, Any], num: int, den: int, held: int | None) -> dict[str, Any]:
    first, second = event["ratio_num"], event["ratio_den"]
    out = {"ratio_num": num, "ratio_den": den,
           "words": core.sentence(event["kind"], first, second, num, den, held)}  # fmt: skip
    return {**out, **core.effect(event["kind"], held, num, den)} if held else out


def _expected(event: dict[str, Any], scope: str, held: int | None, **more: Any) -> dict[str, Any]:
    """One holding's side of an event waiting for the owner: the proposed reading on his own
    quantity, and the other way round when there is one."""
    ways = core.readings(event["kind"], event["ratio_num"], event["ratio_den"])
    out: dict[str, Any] = {"scope": scope, "held_qty_u": held, **more}
    if not ways:
        return {
            **out,
            "ratio_num": None,
            "ratio_den": None,
            "words": SAY["no_ratio"],
            "other": None,
        }
    out.update(_way(event, *ways[0], held))
    out["other"] = _way(event, *ways[1], held) if len(ways) > 1 else None
    return out


def _entries(event: dict[str, Any], asset_id: int, held: dict[int, int], way) -> list[dict]:
    """The entries that would put the proposed reading into a tab, for the entry form's dry run:
    one split for every account, or one bonus for each account that holds the share."""
    base = {"kind": event["kind"].upper(), "trade_date": event["ex_day"], "asset_id": asset_id}
    if way is None:
        return []
    if event["kind"] == "split":
        return [{**base, "ratio_num": way[0], "ratio_den": way[1]}]
    return [
        {**base, "account_id": account,
         "qty_u": core.effect("bonus", qty, *way)["added_qty_u"]}
        for account, qty in sorted(held.items())
    ]  # fmt: skip


def reach_from(today: int) -> int:
    """The first day of the oldest financial year Upstox's reports reach."""
    return (financial_year(today) - REPORT_YEARS + 1) * 10000 + 401


def waiting(tx: Tx, event: dict[str, Any], holdings: Holdings, decided: set[str],
            upstox: set[str]) -> list[dict[str, Any]]:  # fmt: skip
    """The holdings an event still waits for the owner's tap on. The Upstox account: when it
    held the share the day before, or, where no closing record reaches that day, when the share
    is one of the account's and the event is inside the years its reports reach. A tab: when it
    held the share the day before and has no entry for the event yet."""
    out, today = [], holdings.today
    if UPSTOX_SCOPE not in decided:
        held = holdings.upstox(event["isin"], event["ex_day"])
        unknown = held is None and event["isin"] in upstox and event["ex_day"] >= reach_from(today)
        if held or unknown:
            out.append(_expected(event, UPSTOX_SCOPE, held))
    for tab in holdings.tabs:
        scope = f"tab:{tab['id']}"
        held_by = holdings.tab(tab["id"], event["isin"], event["ex_day"])
        if scope in decided or not held_by or holdings.entry_of(tab["id"], event) is not None:
            continue
        ways = core.readings(event["kind"], event["ratio_num"], event["ratio_den"])
        asset_id = holdings.assets[event["isin"]]["id"]
        out.append(_expected(
            event, scope, sum(held_by.values()), tab_id=tab["id"], name=tab["name"],
            entries=_entries(event, asset_id, held_by, ways[0] if ways else None),
        ))  # fmt: skip
    return out


def _shown(event: dict[str, Any], holdings: Holdings, decisions: list[dict]) -> dict[str, Any]:
    """An event as the phone gets it (types.ts ``CaEvent``): public facts, a name, decisions."""
    keep = ("id", "event_id", "scope", "state", "ratio_num", "ratio_den", "activity_id", "version")
    shown = {key: value for key, value in event.items() if key != "raw_hash"}
    shown["name"] = holdings.name(event["isin"])
    shown["decisions"] = [{key: row[key] for key in keep} for row in decisions]
    return shown


def listing(tx: Tx, today: int, *, scope: str | None = None, from_day: int, to_day: int,
            held_now: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """``ca_list``: the events between two days with the owner's decisions, the bonuses and
    splits still waiting for his tap (whatever their day, up to today), and when each
    instrument was last read. ``scope`` narrows the decisions and the waiting list to one."""
    holdings, upstox = Holdings(tx, today), upstox_isins(tx, held_now)
    by_event: dict[int, list[dict]] = {}
    for row in tx.rows("ca_decision"):
        if scope in (None, row["scope"]):
            by_event.setdefault(row["event_id"], []).append(row)
    to_confirm = []
    for event in tx.ca_events(to_day=today):
        if event["kind"] not in core.LOT_KINDS:
            continue
        decided = {row["scope"] for row in tx.rows("ca_decision", event_id=event["id"])}
        found = [w for w in waiting(tx, event, holdings, decided, upstox)
                 if scope in (None, w["scope"])]  # fmt: skip
        if found:
            shown = _shown(event, holdings, by_event.get(event["id"], []))
            to_confirm.append({**shown, "expected": found})
    events = tx.ca_events(from_day=from_day, to_day=to_day, gone=True)
    return {
        "events": [_shown(e, holdings, by_event.get(e["id"], [])) for e in events],
        "to_confirm": to_confirm,
        "reads": tx.ca_reads(),
    }


def _event(tx: Tx, event_id: int) -> dict[str, Any]:
    found = [event for event in tx.ca_events(gone=True) if event["id"] == event_id]
    if not found:
        raise NotFound()
    return found[0]


def _ratio(event: dict[str, Any], num: Any, den: Any) -> tuple[int, int]:
    for value in (num, den):
        if value.__class__ is not int or not 0 < value <= RATIO_MAX:
            raise Invalid(SAY["ratio_missing"])
    ways = core.readings(event["kind"], event["ratio_num"], event["ratio_den"])
    if ways and (num, den) not in ways:
        raise Invalid(SAY["ratio"])
    return num, den


def decide(tx: Tx, event_id: int, args: dict[str, Any], today: int, now: int) -> dict[str, Any]:
    """``ca_decide``: the owner's one tap on a bonus or a split for one holding. ``applied``
    takes one of the event's two readings (any ratio when Upstox gave none) and only once the
    ex-date is here; for a tab it records the entry the owner added and never writes one.
    ``dismissed`` leaves the lots as they are. A decision is changed from its version only."""
    event = _event(tx, event_id)
    scope, state = args.get("scope"), args.get("state")
    if event["kind"] not in core.LOT_KINDS:
        raise Invalid(SAY["kind"])
    if state not in CA_DECISIONS:
        raise Invalid(SAY["scope"])
    tab = None
    if scope != UPSTOX_SCOPE:
        tab_id = scope[4:] if isinstance(scope, str) and scope.startswith("tab:") else ""
        tab = tx.get("tab", int(tab_id)) if tab_id.isdigit() else None
        if tab is None or tab["system"]:
            raise Invalid(SAY["scope"])
    values: dict[str, Any] = {"state": state, "ratio_num": None, "ratio_den": None,
                              "activity_id": None}  # fmt: skip
    if state == "applied":
        kind = event["kind"]
        if event["ex_day"] > today:
            raise Refused(f"This {kind} has not happened yet. Confirm it on or after "
                          f"{day_words(event['ex_day'])}.", reason="early")  # fmt: skip
        values["ratio_num"], values["ratio_den"] = _ratio(event, args.get("ratio_num"),
                                                          args.get("ratio_den"))  # fmt: skip
        if tab is not None:
            entry = Holdings(tx, today).entry_of(tab["id"], event)
            if entry is None:
                raise Refused(f"Add the {kind} to {tab['name']} first, then confirm it here.",
                              reason="no_entry")  # fmt: skip
            values["activity_id"] = entry
    have = tx.rows("ca_decision", event_id=event_id, scope=scope)
    if have:
        return tx.change("ca_decision", have[0]["id"], args.get("version"), values, now)
    request_id = args.get("request_id") if isinstance(args.get("request_id"), str) else None
    if request_id and (again := tx.by_request("ca_decision", request_id)) is not None:
        return again
    return tx.insert("ca_decision", {**values, "event_id": event_id, "scope": scope}, now,
                     request_id)  # fmt: skip


def accepted_actions(tx: Tx) -> list[dict[str, Any]]:
    """For the tax adapter: the Upstox account's bonuses and splits as ``tax.model.Action``
    rows, in ex-date order. One the owner applied is ``accepted`` with the reading he chose; one
    he has not decided is passed with the proposed reading and ``accepted`` false, so the tax
    core changes no lot for it and says so where it would have mattered; one he dismissed, or
    that has no ratio, is left out."""
    decided = {row["event_id"]: row for row in tx.rows("ca_decision", scope=UPSTOX_SCOPE)}
    out = []
    for event in tx.ca_events(gone=True):
        if event["kind"] not in core.LOT_KINDS:
            continue
        row = decided.get(event["id"])
        ways = core.readings(event["kind"], event["ratio_num"], event["ratio_den"])
        if row is not None and row["state"] == "applied":
            out.append(core.action_row(event, row["ratio_num"], row["ratio_den"], True))
        elif row is None and ways and event["gone_at"] is None:
            out.append(core.action_row(event, *ways[0], False))
    return out


def ensure(service: Any) -> CorpActions:
    """The service's one reader, built on first use."""
    reader = getattr(service, "_corpactions", None)
    if reader is None:
        reader = service._corpactions = CorpActions(service, service.ensure_upstox())
    return reader


async def corporate_actions_step(service: Any, now: int) -> str | None:
    """Run the night's reads once, when they are due and not yet done."""
    night = due_day(now)
    if service.store is None or night is None:
        return None
    store, loop = service.store, asyncio.get_running_loop()
    done = await loop.run_in_executor(
        None, lambda: store.read(OWNER, lambda tx: tx.job_get(API, night))
    )
    if done is not None:
        return None
    try:
        status, note = await ensure(service).run(now)
    except Exception:
        log.exception("the nightly read of corporate actions failed")
        status, note = "failed", "Corporate actions could not be read tonight."
    await loop.run_in_executor(
        None,
        lambda: store.write(
            OWNER, lambda tx: tx.job_put(API, night, status, note, service.clock())
        ),
    )
    log.info("corporate actions %s: %s", status, note)
    return status


def register_jobs() -> None:
    from .service import job
    from .timers import run_forever

    @job(API)
    async def corporate_actions_job(service) -> None:
        await run_forever(service, API, corporate_actions_step)


__all__ = [
    "CorpActions",
    "Holdings",
    "LIMITS",
    "UPSTOX_SCOPE",
    "accepted_actions",
    "corporate_actions_step",
    "due_day",
    "ensure",
    "event_hash",
    "isin_of_key",
    "known_isins",
    "listing",
    "decide",
    "register_jobs",
    "upstox_isins",
    "waiting",
]
