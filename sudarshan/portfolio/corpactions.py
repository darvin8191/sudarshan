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
* **Only a clean answer is a read.** "Nothing listed" is a 200 with an empty list, or a 404 (no
  such instrument at Upstox), and nothing else. A refusal (403) ends the night: the token is not
  allowed to read this list. Any other request Upstox turns down (a 400, say, for one ISIN it
  does not cover) and an answer that cannot be read are failed reads of that instrument, and
  three in a row end the night. Nothing failed is kept as read; an instrument never read that
  failed is tried again each night after the others, the one tried longest ago first.
* **A night fails only on a stop, or when nothing was read in the rotation week** (v0.14.0
  verification M5). It has failed when a stop ended it before anything was read (a refusal, a
  pause, no token, three failed reads in a row of instruments that answered before), or when
  it read nothing and nothing has been read for ``core.ROTATION_DAYS``. A share that always
  fails does not fail the night, and behind a good read its failure does not count towards
  three in a row. A failed night says why in words (``read_status``), and the phone never
  shows it as an empty calendar.
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
from .core.days import add_days, day_of_instant, days_between, financial_year
from .core.kinds import CA_DECISIONS, REPORT_YEARS
from .core.ledger import replay
from .core.model import BadEntry, Entry, Refusal
from .core.words import date as day_words
from .core.words import quantity
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
STOPS = ("slow", "busy", "no_token", "token_refused", "refused")
#: Outcomes that are a failed read of one instrument: nothing is kept as read.
FAILED = ("silent", "unreadable", "rejected")
WORDS = {
    "none": "Upstox lists no corporate actions for this instrument.",
    "refused": "Upstox did not allow SUDARSHAN to read its list of events with the read-only "
    "token.",
    "rejected": "Upstox turned down the request for this share's events.",
    "unreadable": "Upstox's answer was not in a form SUDARSHAN can read.",
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


def holdings_days(tx: Tx) -> list[int]:
    """The days of the Upstox account's closing records that hold its holdings, oldest first.
    A record whose holdings could not be read (only its positions or its funds answered, so
    ``holdings_value_paise`` is null and it has no holding lines) is not a holdings record: read
    as one, it would say that the account held nothing that day (v0.13.0 verification M2)."""
    return [row["day"] for row in tx.rows("upstox_eod", "day")
            if row["holdings_value_paise"] is not None]  # fmt: skip


def upstox_isins(tx: Tx, held_now: list[dict[str, Any]] | None = None) -> set[str]:
    """The ISINs of the Upstox account: held at its last closing record or at the last read of
    its holdings, or in its trade history."""
    found = {row.get("isin") for row in held_now or ()}
    days = holdings_days(tx)
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
        if status == 403:
            # Design p7 6.4 item 8: whether Fundamentals answers this token from the server is
            # not known until the first night. A refusal is never "nothing listed".
            log.warning("Upstox refused to list corporate actions for the read-only token (403)")
            return "refused", None
        if status == 404:
            return "none", None  # Upstox has nothing under this ISIN
        if status != 200:
            log.warning("Upstox turned down a corporate-actions request (%d)", status)
            return "rejected", None
        try:
            return "ok", json.loads(answer.text, parse_float=Decimal)
        except ValueError:
            return "unreadable", None

    # ---- one instrument --------------------------------------------------------------------
    async def read_one(self, isin: str, now: int) -> str:
        """Read one ISIN and keep what it lists. The outcome: ``ok`` (a clean answer with events),
        ``none`` (an empty list, or no such instrument at Upstox; asked again in a week), one of
        ``FAILED`` (nothing changed; an instrument never read before is marked as tried, with
        the time it was tried in ``read_at``, and asked again the next night after the others)
        or one of ``STOPS`` (nothing written)."""
        outcome, payload = await self._call(isin)
        if outcome == "ok":
            try:
                parsed = core.parse_answer(isin, payload)
            except ValueError:
                outcome = "unreadable"
        if outcome == "unreadable":
            log.warning("a corporate-actions answer could not be read; nothing was changed")
        if outcome in STOPS:
            return outcome
        # "Nothing listed" is a clean answer with an empty list, and nothing else.
        empty = outcome == "ok" and not parsed["events"] and not sum(parsed["skipped"].values())
        outcome = "none" if empty else outcome

        def write(tx: Tx) -> None:
            if outcome in FAILED:
                # The last good read stands; only an instrument with none is marked as tried,
                # with when (never a read: its state says failed), so that the rotation puts it
                # after the others and the one tried longest ago first.
                if not any(r["isin"] == isin and r["state"] != "failed" for r in tx.ca_reads()):
                    tx.ca_read_put(isin, now, "failed", WORDS[outcome])
                return
            if payload is None:  # no such instrument at Upstox (or not an ISIN: nothing asked)
                tx.ca_read_put(isin, now, "none", WORDS["none"])
                return
            listed = []
            for event in parsed["events"]:
                listed.append(event_hash(event))
                tx.ca_event_put({**event, "raw_hash": listed[-1]}, now)
            tx.ca_events_gone(isin, listed, now)
            left = sum(parsed["skipped"].values())
            note = f"{left} rows could not be read and were left out." if left else None
            if empty:
                tx.ca_read_put(isin, now, "none", WORDS["none"])
            else:
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
        return (await self._plan(now))[0]

    async def _plan(self, now: int) -> tuple[list[str], set[str], bool]:
        """Tonight's ISINs, the ones among them that failed before and were never read, and
        whether anything was read in the rotation week up to tonight. The watchlists' shares
        (GA8.1) come after everything the owner holds, has traded or keeps in a tab, in the reads
        left: a long watchlist takes longer to come round and never adds a read."""
        from . import watchlists

        today, held = day_of_instant(now), self._held_now()

        def order(isins: set[str], tried: dict[str, int], days, near, room: int) -> list[str]:
            first = core.tonight(sorted(isins - set(tried)), days, near, today, room)
            # One that failed before and was never read comes last, the one tried longest ago
            # first: it cannot hold up the rest, and none of them waits behind the others for
            # ever (v0.14.0 verification M5).
            again = sorted(isins & set(tried), key=lambda isin: (tried[isin], isin))
            return (first + again)[:room]

        def look(tx: Tx) -> tuple[list[str], set[str], bool]:
            rows = tx.ca_reads()
            reads = {r["isin"]: r["read_at"] for r in rows if r["state"] != "failed"}
            tried = {r["isin"]: r["read_at"] or 0 for r in rows if r["state"] == "failed"}
            days = {k: None if v is None else day_of_instant(v) for k, v in reads.items()}
            soon = tx.ca_events(from_day=today, to_day=add_days(today, core.NEAR_DAYS))
            near = {event["isin"] for event in soon}
            known = known_isins(tx, held)
            todo = order(known, tried, days, near, MAX_READS)
            watched = set(watchlists.shares(tx)) - known
            todo += order(watched, tried, days, near, MAX_READS - len(todo))
            week = any(days_between(day, today) < core.ROTATION_DAYS
                       for day in days.values() if day is not None)  # fmt: skip
            return todo, set(tried) & set(todo), week

        return await self._store(lambda store: store.read(OWNER, look))

    async def _pace(self) -> None:
        wait = self._last + PACE_S - self._mono()
        if wait > 0:
            await self._sleep(wait)
        self._last = self._mono()

    async def run(self, now: int) -> tuple[str, str]:
        """One night's reads. Returns ``(status, note)`` for the job log."""
        todo, again, week = await self._plan(now)
        counts = {"ok": 0, "none": 0, "failed": 0}
        why: dict[str, int] = {}
        in_a_row, stop, fresh = 0, None, 0
        for isin in todo:
            await self._pace()
            outcome = await self.read_one(isin, self.service.clock())
            if outcome in STOPS:
                stop = outcome
                break
            counts[outcome if outcome in counts else "failed"] += 1
            if outcome not in FAILED:
                in_a_row = 0
                continue
            why[outcome] = why.get(outcome, 0) + 1
            fresh += isin not in again
            # One that failed before says nothing of Upstox behind a good read tonight.
            if isin not in again or not counts["ok"] + counts["none"]:
                in_a_row += 1
            if in_a_row >= SILENT_STOP:
                stop = outcome
                break
        note = (
            f"{counts['ok']} of {len(todo)} instruments read, {counts['none']} with nothing "
            f"listed, {counts['failed']} not read"
        )
        # ``kinds.JOB_STATUSES``: a night that read nothing has failed when a stop ended it, or
        # an instrument that answered before failed, or nothing was read in the rotation week;
        # never for the shares that always fail. Its note says why in the words
        # ``read_status`` shows the owner.
        failed = (
            bool(todo)
            and not counts["ok"] + counts["none"]
            and (stop in STOPS or fresh > 0 or not week)
        )
        reason = stop or (max(sorted(why), key=why.get) if why and failed else None)
        if reason is not None:
            note = f"{note}. {WORDS[reason]}"
        return ("failed" if failed else "ok"), note

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


# ---- whether the list was read at all (what the phone says before, and after a failed, read) ----
FIRST_READ = (
    "SUDARSHAN reads Upstox's corporate actions for the first time tonight after 23:45. Nothing "
    "is listed until then."
)
NOT_READ = "Upstox's list could not be read {night}."
RETRY = "SUDARSHAN tries again {when} after 23:45."


def read_status(tx: Tx, today: int) -> dict[str, Any]:
    """Whether Upstox's list was ever read, and how the last night went: ``state`` is ``never``
    (no night has run yet), ``failed`` (the last night read nothing of what was due) or ``ok``;
    ``words`` is the sentence the phone shows in place of an empty list, None when the last
    night read. An empty calendar means "nothing listed" only under ``ok``."""
    runs = [row for row in tx.job_runs() if row["name"] == API]
    last = runs[-1] if runs else None
    if last is None:
        if any(row["state"] != "failed" for row in tx.ca_reads()):  # read on demand only
            return {"state": "ok", "night": None, "words": None}
        return {"state": "never", "night": None, "words": FIRST_READ}
    if last["status"] != "failed":
        return {"state": "ok", "night": last["day"], "words": None}
    if last["day"] >= today:  # the run of this very evening, after 23:45
        night, when = "tonight", "tomorrow night"
    elif add_days(last["day"], 1) == today:
        night, when = "last night", "tonight"
    else:
        night, when = f"on {day_words(last['day'])}", "tonight"
    detail = last["detail"] or ""
    why = next((text for text in WORDS.values() if text in detail), "")
    parts = (NOT_READ.format(night=night), why, RETRY.format(when=when))
    return {"state": "failed", "night": last["day"], "words": " ".join(p for p in parts if p)}


# ---- the screener's ex-dates (M9.13, gap audit GA8.3) -------------------------------------------
#: From a week back (an ex-date on a weekend or a holiday just passed takes effect at the next
#: session) to as far on as the screener counts sessions (``screener/events.py``, REACH_DAYS).
EX_DATES_BACK_DAYS, EX_DATES_AHEAD_DAYS = 7, 400
#: A share last read cleanly longer ago than two rotation weeks is left out: its list may be old.
EX_DATES_FRESH_DAYS = 2 * core.ROTATION_DAYS + 1


def ex_dates(tx: Tx, today: int) -> dict[str, Any]:
    """The screener's one read (``ops/ca_ops.ex_dates``): each share whose list was read cleanly
    in the last ``EX_DATES_FRESH_DAYS`` (the nightly read covers what the owner holds, has traded
    or keeps in a tab, and the shares on his watchlists), with the kind and ex-date of each event
    the calendar shows in the span. A share given with no events lists none in it; a share left
    out is one SUDARSHAN does not know about. Public facts only: no quantity, tab or decision."""
    oldest = add_days(today, -EX_DATES_FRESH_DAYS)
    read = {
        row["isin"]: row["read_at"]
        for row in tx.ca_reads()
        if row["state"] in ("ok", "none") and row["read_at"]
        and day_of_instant(row["read_at"]) >= oldest
    }  # fmt: skip
    listed: dict[str, list[list[Any]]] = {isin: [] for isin in read}
    span = (add_days(today, -EX_DATES_BACK_DAYS), add_days(today, EX_DATES_AHEAD_DAYS))
    for event in tx.ca_events(from_day=span[0], to_day=span[1]) if read else ():
        if event["isin"] in listed:
            listed[event["isin"]].append([event["kind"], event["ex_day"]])
    shares = [{"isin": isin, "events": listed[isin]} for isin in sorted(read)]
    return {"as_of": max(read.values(), default=None), "shares": shares}


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


def share_names(tx: Tx, held_now: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """A name for each ISIN: the scrip name of Upstox's trade history, else the name the Upstox
    holding itself carries (``held_now``), and over both the holding's own in the owner's tabs
    (under each ISIN it has had). A share held only at Upstox has a name too (review M2)."""
    names = {
        r["isin"]: r["scrip_name"]
        for record in tx.report_list("trades")
        for r in (record.get("payload") or {}).get("rows") or []
        if r.get("isin") and r.get("scrip_name")
    }
    for row in held_now or ():
        if row.get("isin") and row.get("name") and row["name"] != row.get("instrument_key"):
            names[row["isin"]] = row["name"]
    assets = {a["id"]: a["name"] for a in tx.rows("asset") if a.get("name")}
    for alias in tx.rows("asset_alias", "id", kind="isin"):
        if alias["asset_id"] in assets:
            names[alias["value"]] = assets[alias["asset_id"]]
    names.update({a["isin"]: a["name"] for a in tx.rows("asset") if a.get("isin") and a["name"]})
    return names


class Holdings:
    """What the Upstox account and each custom tab held the day before a day, read once."""

    def __init__(self, tx: Tx, today: int, held_now: list[dict[str, Any]] | None = None,
                 now: int | None = None):  # fmt: skip
        self.tx, self.today, self.now = tx, today, now
        self.assets = {a["isin"]: a for a in tx.rows("asset", kind="listed") if a["isin"]}
        self.tabs = [t for t in tx.rows("tab") if not t["system"] and not t["archived"]]
        self.record_days = holdings_days(tx)
        self.names = share_names(tx, held_now)
        self._records: dict[int, dict[str, int]] = {}
        self._moves: dict[int, dict[tuple[int, int], list[tuple[int, int]]]] = {}
        self._lots: Any = None
        self._traded: dict[str, list[tuple[int, int]]] | None = None

    def name(self, isin: str) -> str | None:
        return self.names.get(isin)

    def _record(self, day: int) -> dict[str, int]:
        if day not in self._records:
            held: dict[str, int] = {}
            for line in self.tx.rows("upstox_eod_holding", "instrument_key", day=day):
                key = isin_of_key(line["instrument_key"])
                if key is not None:
                    held[key] = held.get(key, 0) + line["qty"] + (line["t1_qty"] or 0)
            self._records[day] = held
        return self._records[day]

    def upstox(self, isin: str, ex_day: int) -> int | None:
        """The Upstox account's quantity the day before ``ex_day``; None when no closing record
        can say (``core.eve_record``)."""
        day = core.eve_record(self.record_days, ex_day, self.today)
        return None if day is None else self._record(day).get(isin, 0)

    def lots(self):
        """The Upstox account's tax lots (``lotcheck.TaxLots``), built on first use."""
        if self._lots is None:
            from . import lotcheck  # it reads the tax adapter, which reads this module

            self._lots = lotcheck.TaxLots(self.tx, self.today, self.now or 0)
        return self._lots

    def upstox_from_trades(self, isin: str, ex_day: int) -> int | None:
        """Where no closing record reaches the eve of ``ex_day``: the quantity the tax lots hold
        then, from Upstox's trade history and the purchases entered (the same lots a bonus or a
        split would change). None when the year's trades are not read in full."""
        return self.lots().qty(isin, add_days(ex_day, -1))

    def upstox_maybe(self, isin: str, ex_day: int) -> bool:
        """Where no closing record reaches the eve of ``ex_day``: whether the records still say
        the share may have been held then. The first closing record from the ex-date on, taken
        back by the trades between (what was bought since was not held on the eve; what was
        sold since was), or a lot in the trade history. Used only to decide whether a dividend
        is listed as not counted; never for an amount."""
        after = [day for day in self.record_days if day >= ex_day]
        if not after:
            return True  # no record at all: nothing says it was not held
        if self._traded is None:
            self._traded = {}
            for record in self.tx.report_list("trades"):
                for row in (record.get("payload") or {}).get("rows") or []:
                    if isinstance(row, dict) and row.get("isin") and row.get("segment") == "EQ":
                        sign = -1 if row.get("transaction_type") == "SELL" else 1
                        self._traded.setdefault(row["isin"], []).append(
                            (row["trade_date"], sign * row["qty_u"])
                        )
        since = sum(q for day, q in self._traded.get(isin, ()) if ex_day <= day <= after[0])
        if self._record(after[0]).get(isin, 0) - since > 0:
            return True
        return bool(self.upstox_from_trades(isin, ex_day))

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
            upstox: set[str]) -> tuple[list[dict[str, Any]], bool]:  # fmt: skip
    """The holdings an event still waits for the owner's tap on, and whether the Upstox account
    was left out because nothing can tell yet whether it held the share (review M3).

    The Upstox account: only when it held the share the day before, by its closing record or,
    where no record reaches that day, by the tax lots of its trade history; the quantity is
    always shown. A tab: when it held the share the day before and has no entry for the event
    yet. A share the records show was not held on the day is not asked about: applying the
    event would change no lot. Left out (counted, not asked) is only the case where no record
    reaches the day and the lots cannot say yet (the year's share trades are not read in full)."""
    out, left_out, today = [], False, holdings.today
    if UPSTOX_SCOPE not in decided:
        held, source = holdings.upstox(event["isin"], event["ex_day"]), "record"
        if held is None and event["isin"] in upstox:
            held, source = holdings.upstox_from_trades(event["isin"], event["ex_day"]), "trades"
        if held:
            out.append(_expected(event, UPSTOX_SCOPE, held, held_from=source))
        elif held is None and event["isin"] in upstox and event["ex_day"] >= reach_from(today):
            left_out = True
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
    return out, left_out and not out


def _shown(event: dict[str, Any], holdings: Holdings, decisions: list[dict]) -> dict[str, Any]:
    """An event as the phone gets it (types.ts ``CaEvent``): public facts, a name, decisions."""
    keep = ("id", "event_id", "scope", "state", "ratio_num", "ratio_den", "activity_id", "version")
    shown = {key: value for key, value in event.items() if key != "raw_hash"}
    shown["name"] = holdings.name(event["isin"])
    shown["decisions"] = [{key: row[key] for key in keep} for row in decisions]
    return shown


def decisions(tx: Tx) -> dict[int, list[dict[str, Any]]]:
    """Every decision of the owner's, by the event it was made on."""
    found: dict[int, list[dict[str, Any]]] = {}
    for row in tx.rows("ca_decision"):
        found.setdefault(row["event_id"], []).append(row)
    return found


def standing(group: list[dict[str, Any]], decided: dict[int, list[dict]], scope: str):
    """The owner's decision on an event for one holding, whichever wording of the event it was
    made on: an applied one before a dismissed one, the earliest first. None when undecided."""
    rows = [row for event in group for row in decided.get(event["id"], ())
            if row["scope"] == scope]  # fmt: skip
    rows.sort(key=lambda row: (row["state"] != "applied", row["id"]))
    return rows[0] if rows else None


def listing(tx: Tx, today: int, *, scope: str | None = None, from_day: int, to_day: int,
            held_now: list[dict[str, Any]] | None = None, now: int | None = None,
            ) -> dict[str, Any]:  # fmt: skip
    """``ca_list``: the events between two days with the owner's decisions, the bonuses and
    splits still waiting for his tap (whatever their day, up to today), every one he confirmed
    for the Upstox account (``confirmed``, whatever its day and whether or not Upstox still
    lists it: v0.14.0 verification N1), and when each instrument was last read. ``scope``
    narrows the decisions and the waiting list to one.

    An event is one per company and ex-date, however often Upstox rewords it (``core.groups``):
    a decision made on one wording stands for every other, so a relisted event is never asked a
    second time. ``left_out_count`` is how many bonuses and splits of the account's shares are
    not asked about yet because nothing can tell whether the share was held on their day (no
    closing record reaches it and the year's share trades are not read in full); ``lot_check``
    is the tax lots against the Upstox holdings (``lotcheck.check``)."""
    from . import lotcheck

    holdings, upstox = Holdings(tx, today, held_now, now), upstox_isins(tx, held_now)
    decided = decisions(tx)
    by_event = {key: [r for r in rows if scope in (None, r["scope"])]
                for key, rows in decided.items()}  # fmt: skip
    to_confirm, left_out = [], 0
    for group in core.groups(tx.ca_events(to_day=today, gone=True)):
        live = [event for event in group if event["gone_at"] is None]
        if not live:
            continue
        event = live[-1]  # the wording Upstox lists now
        done = {row["scope"] for one in group for row in decided.get(one["id"], ())}
        found, unseen = waiting(tx, event, holdings, done, upstox)
        found = [w for w in found if scope in (None, w["scope"])]
        left_out += unseen
        if found:
            shown = _shown(event, holdings, by_event.get(event["id"], []))
            to_confirm.append({**shown, "expected": found})
    kept = []
    for group in core.groups(tx.ca_events(gone=True)) if scope in (None, UPSTOX_SCOPE) else ():
        done = confirmed(holdings, group, decided, upstox)
        if done is None:
            continue
        made_on = next(e for e in group if e["id"] == done["event_id"])
        live = [event for event in group if event["gone_at"] is None]
        event = live[-1] if live else made_on  # dated as the tax lots date it
        kept.append({
            "event_id": event["id"], "isin": event["isin"], "name": holdings.name(event["isin"]),
            "kind": event["kind"], "ex_day": event["ex_day"], "ratio_num": event["ratio_num"],
            "ratio_den": event["ratio_den"], "gone_at": None if live else made_on["gone_at"],
            "decided": done,
        })  # fmt: skip
    kept.sort(key=lambda row: (-row["ex_day"], row["event_id"]))
    events = tx.ca_events(from_day=from_day, to_day=to_day, gone=True)
    return {
        "events": [_shown(e, holdings, by_event.get(e["id"], [])) for e in events],
        "to_confirm": to_confirm,
        "confirmed": kept,
        "left_out_count": left_out,
        "reads": tx.ca_reads(),
        "read_status": read_status(tx, today),
        "lot_check": lotcheck.check(tx, today, now or 0, holdings.lots(), insights=True),
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
    # One event per company and ex-date: a decision made on another wording of this event
    # stands for it, and is changed there, never made a second time here.
    group = next(
        g for g in core.groups(tx.ca_events(gone=True)) if any(e["id"] == event_id for e in g)
    )
    earlier = standing(group, decisions(tx), scope)
    if earlier is not None:
        raise Refused(_already(event, earlier), reason="decided")
    return tx.insert("ca_decision", {**values, "event_id": event_id, "scope": scope}, now,
                     request_id)  # fmt: skip


def _already(event: dict[str, Any], row: dict[str, Any]) -> str:
    """Why a relisted event is not decided again, with what was decided the first time."""
    kind = event["kind"]
    if row["state"] != "applied":
        return (f"You already left your lots as they are for this {kind}. Upstox lists it "
                "again in other words; nothing more is needed.")  # fmt: skip
    num, den = row["ratio_num"], row["ratio_den"]
    how = (f"{num} new for every {den} held" if kind == "bonus"
           else f"every {den} made {num}")  # fmt: skip
    return (f"This {kind} is already applied ({how}). Upstox lists it again in other words; "
            "applying it again would count it twice.")  # fmt: skip


def confirmed(holdings: Holdings, group: list[dict[str, Any]], decided: dict[int, list[dict]],
              upstox: set[str]) -> dict[str, Any] | None:  # fmt: skip
    """What the owner confirmed for the Upstox account on a bonus or a split (types.ts
    ``CaDecided``; v0.14.0 verification M3), None when nothing is: the decision, made on
    whichever wording of the event, with its version; what it did, in words; and the choices
    To confirm offered (``expected``), with the same sentences on the quantity held the day
    before. The Calendar's "Change what I confirmed" sends ``ca_decide`` from that version: a
    reading taken the wrong way round, or left out when it was right, is never final."""
    row = standing(group, decided, UPSTOX_SCOPE)
    if row is None:
        return None
    event = next(e for e in group if e["id"] == row["event_id"])
    held, more = holdings.upstox(event["isin"], event["ex_day"]), {"held_from": "record"}
    if held is None and event["isin"] in upstox:
        held = holdings.upstox_from_trades(event["isin"], event["ex_day"])
        more = {"held_from": "trades"}
    kind, num, den = event["kind"], row["ratio_num"], row["ratio_den"]
    if row["state"] != "applied":
        words = f"You left your lots as they are for this {kind}."
    else:
        how = f"{num} new for every {den} held" if kind == "bonus" else f"every {den} made {num}"
        if held:
            after = core.effect(kind, held, num, den)["after_qty_u"]
            how += f": {quantity(held)} became {quantity(after)}"
        words = f"You applied this {kind} as {how}."
    choices = _expected(event, UPSTOX_SCOPE, held or None, **(more if held else {}))
    return {"event_id": event["id"], "version": row["version"], "state": row["state"],
            "ratio_num": num, "ratio_den": den, "words": words, "expected": choices}  # fmt: skip


def accepted_actions(tx: Tx) -> list[dict[str, Any]]:
    """For the tax adapter: the Upstox account's bonuses and splits as ``tax.model.Action``
    rows, in ex-date order, one per event however many wordings of it Upstox has listed
    (``core.groups``; review H3). One the owner applied is ``accepted`` with the reading he
    chose, once, dated as Upstox lists it now; one he has not decided is passed with the
    proposed reading and ``accepted`` false, so the tax core changes no lot for it and says so
    where it would have mattered; one he dismissed, or that has no ratio, is left out."""
    decided = decisions(tx)
    out = []
    for group in core.groups(tx.ca_events(gone=True)):
        row = standing(group, decided, UPSTOX_SCOPE)
        live = [event for event in group if event["gone_at"] is None]
        if row is not None and row["state"] == "applied":
            event = live[-1] if live else next(e for e in group if e["id"] == row["event_id"])
            out.append(core.action_row(event, row["ratio_num"], row["ratio_den"], True))
        elif row is None and live:
            ways = core.readings(live[-1]["kind"], live[-1]["ratio_num"], live[-1]["ratio_den"])
            if ways:
                out.append(core.action_row(live[-1], *ways[0], False))
    return sorted(out, key=lambda action: action["ex_day"])


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
    from . import watchlists

    try:
        # The watchlists' shares first (GA8.1), so tonight's rotation reads the lists of tonight.
        watched = await watchlists.refresh(service, now)
        status, note = await ensure(service).run(now)
        note = f"{note}; {watched}; {await loop.run_in_executor(None, _sync_dividends, service)}"
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


def _sync_dividends(service: Any) -> str:
    """After the read: the expected dividends of what was held (dividends.py, M7.13)."""
    from . import dividends

    now = service.clock()
    held = ensure(service)._held_now()
    today = day_of_instant(now)

    def run(tx: Tx) -> tuple[int, dict[str, Any]]:
        from . import lotcheck

        # The night runs only with the Calendar switched on.
        return dividends.sync(tx, today, now, held), lotcheck.check(tx, today, now, insights=True)

    changed, check = service.store.write(OWNER, run)
    differ = len(check["rows"])  # the nightly check of the lots against the holdings
    said = f"{changed} dividend lines made or changed"
    if check["state"] == "ok":
        said += f"; lots and holdings differ for {differ} shares"
    return said


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
    "decisions",
    "read_status",
    "share_names",
    "standing",
    "register_jobs",
    "upstox_isins",
    "waiting",
]
