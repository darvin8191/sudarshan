"""A tab as the phone reads it, and its performance (M7.23, M7.30; design 4.5-4.9 and 5.6).

Everything is worked out on demand, inside one read of the store, from the entries and the prices:
the ledger replayed (up to ``as_of`` when one is asked), the day's lines priced, the figures
added up by ``core/valuation`` and ``core/allocation``, the history by ``core/history``. Nothing
here is a rule; each sum is the core's. This module only fetches what the core is given and
shapes what it returns.

**Prices are a seam.** Prices belong to the price book (PB5), which is not built yet. Every price
this module uses comes through ``Prices``; the default answers a listed share or a fund with "No
price yet" and a manual asset with the owner's newest value, so a tab opens, shows its cost for
what is not priced, and says so. PB5 hands ``Views`` its own ``Prices`` and nothing else changes.

The daily series is the one costly part. It is kept per ``(tab, version, day, prices)``: a change
to any entry moves the tab's version and a new price moves the price stamp, so a series is never
served for entries or prices it was not worked out from.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from . import layout, serialize
from .book import _names, _with_aliases
from .core import daylines, freshness, history, performance, valuation
from .core import filters as filters_mod
from .core.allocation import Facts, Owner, allocate_all
from .core.days import add_days, day_of_instant, is_day, is_weekday
from .core.filters import BadFilter, Filters
from .core.kinds import BENCHMARKS, HOLDING_KINDS
from .core.ledger import replay
from .core.model import Entry, Refusal
from .core.money import round_div
from .core.valuation import Fixed, Row
from .store import Invalid, NotFound, PortfolioStore, Scope, StoreError, Tx

log = logging.getLogger(__name__)

WORDS = {
    "read": (
        "Your portfolio could not be worked out just now. Try again in a minute; if it keeps "
        "failing, check the service on the server."
    ),
    "future": "Choose a day that has already begun.",
    "no_value_then": "Value on that day is not known.",
    "view_gone": "This view is no longer there; it may have been deleted on another screen.",
}
NO_PRICE: dict[str, Any] = {
    "price_u": None,
    "prev_close_u": None,
    "day": None,
    "at": None,
    "source": None,
    "basis": None,
    "late": False,
}


#: What a loan listed with no balance to subtract says it is (state ``zero`` or ``later``).
ZERO_LOAN_WORDS = "Entered at 0, so it lowers nothing. Update the balance once you owe something."
LATER_LOAN_WORDS = "Its balance starts on {day}. It lowers net worth from that day, not before."


class Prices:
    """The one seam to the price book (PB5): what an asset is worth now, and what it closed at.

    The defaults read what the store already holds and invent nothing: a manual asset is worth its
    owner's newest value on or before today; any other asset has no price now. Closing prices for
    the history come from the owner's values and, when the price book writes them, its closing
    records.
    """

    def now(self, tx: Tx, asset: Mapping[str, Any], today: int) -> dict[str, Any]:
        """The ``PriceInfo`` of an asset now. THE seam: PB5 answers listed shares and funds.
        A fund with a NAV the owner entered by hand (design 4.5) is priced by it here."""
        if asset["kind"] not in ("manual", "mf"):
            return dict(NO_PRICE)
        return manual_point(tx, asset, today, from_day=None) or dict(NO_PRICE)

    def closes(self, tx: Tx, assets: list[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
        """Each asset's closing prices as ``history.Quotes.of`` reads them."""
        found = {}
        for asset in assets:
            pairs = {}
            for table in ("price_eod", "price_point"):  # the owner's own value wins on a day
                for row in tx.rows(table, "day", asset_id=asset["id"]):
                    pairs[row["day"]] = row["price_u"]
            found[asset["id"]] = {"kind": asset["kind"], "prices": sorted(pairs.items())}
        return found

    def index_closes(self, tx: Tx) -> dict[str, list[tuple[int, int]]]:
        found = tx.index_closes()
        return {name: found.get(name, []) for name in BENCHMARKS}

    def last_session_day(self, today: int, now: int) -> int:
        """The latest trading day whose session has begun. Without the exchange's calendar, the
        latest weekday; the price book knows the holidays."""
        day = today
        while not is_weekday(day):
            day = add_days(day, -1)
        return day

    def auction(
        self, asset: Mapping[str, Any], today: int
    ) -> tuple[bool, int | None, int | None, int | None]:
        """``(cas, auction_from, auction_until, auction_close)`` of a listed stock (P24): whether
        it closes by auction and its window in epoch ms. The price book knows the keys."""
        return False, None, None, None

    def stamp(self, tx: Tx) -> tuple:
        """Changes whenever a price the history reads does."""
        return tx.price_stamp()


def manual_point(
    tx: Tx, asset: Mapping[str, Any], today: int, from_day: int | None
) -> dict[str, Any] | None:
    """The owner's newest own value of an asset on or before ``today`` as a ``PriceInfo``, or None;
    with ``from_day``, only a value dated that day or later (the day of the fund's published NAV:
    the owner's own value wins on a day, here as in the history of closing prices)."""
    points = [p for p in tx.rows("price_point", "day", asset_id=asset["id"]) if p["day"] <= today]
    if from_day is not None:
        points = [p for p in points if p["day"] >= from_day]
    if not points:
        return None
    last = points[-1]
    return {
        "price_u": last["price_u"],
        "prev_close_u": None,
        "day": last["day"],
        "at": last["updated_at"],
        "source": "manual",
        "basis": "manual",
        "late": False,
    }


class SeriesCache:
    """Daily series kept by ``(tab, version, day, prices)``; the oldest goes at ``size``."""

    def __init__(self, size: int = 32):
        self._size, self._lock, self._kept = size, threading.Lock(), OrderedDict()

    def get(self, key: tuple) -> Any:
        with self._lock:
            found = self._kept.get(key)
            if found is not None:
                self._kept.move_to_end(key)
            return found

    def put(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._kept[key] = value
            self._kept.move_to_end(key)
            while len(self._kept) > self._size:
                self._kept.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._kept)


class Views:
    def __init__(
        self, store: PortfolioStore, prices: Prices | None = None, cache: SeriesCache | None = None
    ):
        self.store = store
        self.prices = prices or Prices()
        self.cache = cache or SeriesCache()

    # ---- the tab ------------------------------------------------------------------------------
    def tab_view(self, scope: Scope, tab_id: int, args: Mapping[str, Any], *, now: int) -> dict:
        """types.ts ``TabView``: holdings with the live-math inputs, totals, bars, layout, views."""

        def run(tx: Tx):
            tab = tx.get("tab", tab_id)
            if tab is None:
                raise NotFound()
            today = day_of_instant(now)
            saved = layout.views_of(tx, tab_id)
            filters = self._filters(args, saved, today)
            live = self._live(tx, tab, filters, today, now)
            held = [(live.facts[aid], live.core[aid]) for aid in live.rows_by]
            accounts = {a["id"]: Owner(a["name"], a["member_id"]) for a in tx.rows("account")}
            members = {m["id"]: m["name"] for m in tx.rows("member")}
            named = sorted({e.asset_id for e in live.entries if e.asset_id is not None})
            # M7.19 V2: the tab's loans lower its net worth and enter no return figure.
            liabilities = self._liabilities(tx, tab, today, filters.as_of, now)
            loose = self._loose_loans(tx, tab, today, filters.as_of, now)
            # A loan belongs to the whole tab: against a part of it (an account, a kind, a tag, the
            # lots held over or under a year) net worth would subtract all of it from some of it.
            narrowed = bool(filters.accounts or filters.classes or filters.tags or filters.held)
            loans_hidden = narrowed and bool(liabilities)
            if narrowed:
                liabilities, loose = [], []
            live.fixed["liabilities_paise"] = sum(each["outstanding_paise"] for each in liabilities)
            return {
                "tab": serialize.tab(tab),
                "today": today,
                "as_of": filters.as_of,
                "read_at": now,
                "filters": filters.as_dict(),
                "rows": live.rows,
                "fixed": live.fixed,
                "totals": live.totals,
                "assets": [
                    serialize.asset(_with_aliases(tx, live.assets[a]))
                    for a in named
                    if a in live.assets
                ],
                "liabilities": liabilities,
                "loans_hidden": loans_hidden,
                "loose_loans": loose,
                "allocation": allocate_all(held, accounts, members),
                "notes": self._notes(live),
                "layout": layout.layout_state(tx, tab),
                "views": saved,
            }

        return self.store.read(scope, run)

    def _filters(self, args: Mapping[str, Any], saved: list[dict], today: int) -> Filters:
        chosen = None
        if args.get("view") is not None:
            chosen = next((v for v in saved if v["id"] == args["view"]), None)
            if chosen is None:
                raise NotFound(WORDS["view_gone"])
        elif not any(
            args.get(k) is not None for k in ("accounts", "classes", "tags", "held", "as_of")
        ):
            chosen = next((v for v in saved if v["is_default"]), None)
        base = chosen["spec"]["filters"] if chosen else {}
        raw = {
            key: args[key] if args.get(key) is not None else base.get(key)
            for key in ("accounts", "classes", "tags", "held", "as_of")
        }
        raw = {k: v for k, v in raw.items() if v is not None}
        try:
            found = Filters.of(raw)
        except BadFilter as bad:
            raise Invalid(str(bad)) from None
        if found.as_of is not None and (not is_day(found.as_of) or found.as_of > today):
            raise Invalid(WORDS["future"])
        return found

    def _live(
        self, tx: Tx, tab: Mapping[str, Any], filters: Filters, today: int, now: int
    ) -> SimpleNamespace:
        """The rows, the sums beside them and the totals of a tab under these filters."""
        entries = [Entry.of(r) for r in tx.entries(tab["id"])]
        names, decimals = _names(tx)
        try:
            ledger = replay(entries, as_of=filters.as_of, names=names, decimals=decimals)
        except Refusal:
            log.exception("a saved tab no longer replays")
            raise StoreError(WORDS["read"]) from None
        on = today if filters.as_of is None else filters.as_of
        assets = {a["id"]: a for a in tx.rows("asset")}
        used = sorted({a for (_, a) in ledger.positions if a is not None})
        infos, prev = self._prices(tx, [assets[a] for a in used], today, filters.as_of)
        lines = daylines.lines_of(ledger, on, prev)
        facts = {a: Facts.of(assets[a]) for a in used}
        picked = filters_mod.select(filters, lines, ledger.positions, facts, today)
        rows, core, closed = [], {}, []
        for aid, found in picked.lines.items():
            info, asset = infos[aid], assets[aid]
            mode = (
                valuation.MODE_NONE
                if picked.today_off
                else valuation.today_mode(asset["kind"], info["basis"], info["day"], today)
            )
            core_row = Row(mode, info["price_u"], prev.get(aid), tuple(found))
            if not core_row.held:
                closed.append(core_row)
                continue
            core[aid] = core_row
            rows.append(self._row(tx, tab, asset, info, mode, found, today, now, filters.as_of))
        keep = _keeps(filters, facts)
        fixed = self._fixed(ledger, keep, closed, picked.today_off)
        core_fixed = Fixed(
            fixed["realised_paise"], fixed["income_paise"], fixed["fees_paise"],
            fixed["money_in_paise"], fixed["money_out_paise"],
            fixed["closed_today_paise"], fixed["closed_today_base_paise"],
        )  # fmt: skip
        totals = valuation.tab_totals(core.values(), core_fixed).as_dict()
        return SimpleNamespace(
            rows=rows, fixed=fixed, totals=totals, core=core, rows_by=list(core), facts=facts,
            assets=assets, entries=entries, ledger=ledger,
        )  # fmt: skip

    def _prices(self, tx, assets, today, as_of):
        """``(PriceInfo by asset, previous close by asset)`` now, or at the closes of ``as_of``."""
        infos, prev = {}, {}
        if as_of is None:
            for asset in assets:
                info = self.prices.now(tx, asset, today)
                infos[asset["id"]] = info
                prev[asset["id"]] = valuation.previous_close_u(
                    info["price_u"], info["prev_close_u"], info["day"], today
                )
            return infos, prev
        closes = self.prices.closes(tx, assets)
        for asset in assets:
            quotes = history.Quotes.of(closes[asset["id"]])
            price = history.price_on(quotes, as_of)
            infos[asset["id"]] = {
                **NO_PRICE,
                "price_u": price,
                "day": as_of if price is not None else None,
                "basis": "close" if price is not None else None,
            }
            prev[asset["id"]] = None
        return infos, prev

    def _row(self, tx, tab, asset, info, mode, found, today, now, as_of) -> dict[str, Any]:
        if as_of is not None:
            fresh = freshness.Freshness(
                freshness.OLD if info["price_u"] is None else freshness.FRESH,
                WORDS["no_value_then"] if info["price_u"] is None else None,
            )
        else:
            fresh = freshness.of_price(
                info,
                today=today,
                last_session_day=self.prices.last_session_day(today, now),
                old_days=tab["manual_old_days"],
                noun="NAV" if asset["kind"] == "mf" else "Value",  # a NAV entered by hand
            )
        cas, auction_from, auction_until, auction_close = (
            self.prices.auction(asset, today)
            if asset["kind"] == "listed"
            else (False, None, None, None)
        )
        return {
            "asset": serialize.asset_ref(asset),
            "price": info,
            "fresh": fresh.as_dict(),
            "today_mode": mode,
            "cas": cas,
            "auction_from": auction_from,
            "auction_until": auction_until,
            "auction_close": auction_close,
            "lines": [line.as_dict() for line in found],
        }

    @staticmethod
    def _fixed(ledger, keep, closed, today_off: bool) -> dict[str, int | None]:
        positions = [p for (acc, aid), p in ledger.positions.items() if keep(acc, aid)]
        shim = SimpleNamespace(
            journal=[m for m in ledger.journal if keep(m.account_id, m.asset_id)]
        )
        sums = performance.totals(performance.flows_of(shim))
        paise, base = (0, 0) if today_off else valuation.closed_today(closed)
        return {
            "realised_paise": sum(p.realised_paise for p in positions),
            "income_paise": sum(p.income_paise for p in positions),
            "fees_paise": sum(p.fees_paise for p in positions),
            "money_in_paise": sums.in_paise,
            "money_out_paise": sums.out_paise,
            "closed_today_paise": paise,
            "closed_today_base_paise": base,
            "liabilities_paise": 0,
        }

    def _liabilities(self, tx, tab, today, as_of, now) -> list[dict[str, Any]]:
        """types.ts ``Liability`` for each loan linked to the tab: its outstanding balance on the
        newest day on or before ``as_of`` (today), as the owner last set it, and how old that is.

        * A loan whose balance has come down to 0 after it was owed is paid off and not listed.
        * A loan entered at 0 is listed as owing nothing, and one dated after the day is listed
          with the day its balance starts, each with words for what it means (state ``zero`` and
          ``later``); neither lowers net worth.
        * On a past day (``as_of``) a loan with no balance yet did not exist then and is left out.
        """
        on = today if as_of is None else as_of
        found = []
        for asset_id in tx.liabilities_of(tab["id"]):
            asset = tx.get("asset", asset_id)
            if asset is not None and asset["kind"] == "liability":
                shown = self._loan_line(tx, asset, tab["manual_old_days"], today, as_of, on)
                if shown is not None:
                    found.append(shown)
        return found

    def _loose_loans(self, tx, tab, today, as_of, now) -> list[dict[str, Any]]:
        """Loans that are on no tab (entered in v0.5.0, before a loan had one), offered on the
        Summary of every tab the owner can move one to, so that none is out of reach."""
        if as_of is not None or tab["system"] or tab["kind"] == "upstox" or tab["archived"]:
            return []
        found = []
        for asset in tx.rows("asset"):
            if asset["kind"] == "liability" and not tx.tabs_of_liability(asset["id"]):
                shown = self._loan_line(
                    tx, asset, tab["manual_old_days"], today, None, today, loose=True
                )
                if shown is not None:
                    found.append(shown)
        return found

    @staticmethod
    def _loan_line(tx, asset, old_days, today, as_of, on, loose: bool = False):
        points = tx.price_points(asset["id"])
        known = [p for p in points if p["day"] <= on]
        state, words_ = "owing", None
        if known:
            latest = known[-1]
            if latest["price_u"] <= 0:
                if not loose and any(p["price_u"] > 0 for p in known):
                    return None  # it was owed and is paid off
                state, words_ = "zero", ZERO_LOAN_WORDS
        else:
            later = [p for p in points if p["day"] > on]
            if as_of is not None or not later:
                return None  # it did not exist on that day
            latest = later[0]
            state = "later"
            words_ = LATER_LOAN_WORDS.format(day=freshness.when(latest["day"], today))
        fresh = (
            freshness.Freshness(freshness.FRESH, None)
            if state == "later"
            else freshness.manual(latest["day"], on, old_days, noun="Balance")
        )
        return {
            "asset_id": asset["id"],
            "name": asset["name"],
            "outstanding_paise": round_div(latest["price_u"], 100) if state == "owing" else 0,
            "day": latest["day"],
            "state": state,
            "words": words_,
            "fresh": fresh.as_dict(),
        }

    @staticmethod
    def _notes(live) -> list[dict[str, Any]]:
        notes = []
        for row in live.rows:
            ref, fresh = row["asset"], row["fresh"]
            if (
                fresh["state"] == freshness.OLD
                and ref["kind"] in ("mf", "manual")
                and fresh["words"]
            ):
                notes.append(
                    {
                        "asset_id": ref["id"],
                        "name": ref["name"],
                        "kind": "nav" if ref["kind"] == "mf" else "manual",
                        "words": fresh["words"],
                        "price_day": row["price"]["day"],
                    }
                )
        return notes

    # ---- performance ----------------------------------------------------------------------------
    def performance(self, scope: Scope, tab_id: int, *, now: int) -> dict:
        """types.ts ``PerformanceReport``: money figures, XIRR or absolute return, TWR, the index
        comparison, the daily series and the figures by financial year, on today."""

        def run(tx: Tx):
            tab = tx.get("tab", tab_id)
            if tab is None:
                raise NotFound()
            today = day_of_instant(now)
            live = self._live(tx, tab, Filters(), today, now)
            ledger = live.ledger
            flows = performance.flows_of(ledger)
            out = performance.summary(flows, live.totals["value_paise"], today)
            used = [
                live.assets[a] for a in sorted({a for (_, a) in ledger.positions if a is not None})
            ]
            used = [a for a in used if a["kind"] in HOLDING_KINDS]
            key = (tab_id, tab["version"], today, self.prices.stamp(tx))
            days = self.cache.get(key)
            if days is None:
                quotes = {
                    aid: history.Quotes.of(q) for aid, q in self.prices.closes(tx, used).items()
                }
                days = history.daily(ledger, quotes, today)
                self.cache.put(key, days)
            closes = self.prices.index_closes(tx)
            span = days.span()
            out["twr"] = days.twr().as_dict()
            out["benchmarks"] = [
                history.benchmark(name, flows, closes.get(name, ()), today, None, span).as_dict()
                for name in BENCHMARKS
            ]
            out["series"] = [point.as_dict() for point in days.series]
            out["by_fy"] = [fy.as_dict() for fy in performance.by_financial_year(ledger)]
            out["income"] = {
                **performance.income_report(ledger, today),
                # M7.29: yield on cost is the income the lots still held earned in the twelve
                # months, on those lots' cost.
                **performance.income_summary(ledger, today),
            }
            out["series_words"] = days.words
            return out

        return self.store.read(scope, run)

    # ---- state and summary ----------------------------------------------------------------------
    def state(self, scope: Scope, *, now: int, version: str) -> dict:
        def run(tx: Tx):
            jobs = {
                r["name"]: {k: r[k] for k in ("name", "day", "status", "detail", "at")}
                for r in tx.job_runs()
            }
            nav = tx.nav_day()
            return {
                "service_version": version,
                "schema_version": tx.user_version(),
                "now": now,
                "today": day_of_instant(now),
                "tabs": [serialize.tab(r) for r in tx.rows("tab", "position,id")],
                "members": [serialize.member(r) for r in tx.rows("member")],
                "accounts": [serialize.account(r) for r in tx.rows("account")],
                "jobs": list(jobs.values()),
                "amfi": {"on": nav is not None, "nav_day": nav},
            }

        return self.store.read(scope, run)

    def summary(self, scope: Scope, *, now: int) -> dict:
        """The dashboard card: for each pinned tab, the live-math inputs; the Upstox tab's own
        holdings come from the Upstox part (not built here), so its entry carries none."""

        def run(tx: Tx):
            today = day_of_instant(now)
            entries = []
            for tab in tx.rows("tab", "position,id"):
                if tab["archived"] or not (tab["pinned"] or tab["system"]):
                    continue
                live = None
                if not tab["system"]:
                    found = self._live(tx, tab, Filters(), today, now)
                    live = {"rows": found.rows, "fixed": found.fixed, "totals": found.totals}
                entries.append({"tab": serialize.tab(tab), "live": live, "upstox": None})
            return {"read_at": now, "today": today, "entries": entries}

        return self.store.read(scope, run)


def _keeps(filters: Filters, facts: Mapping[int, Facts]):
    """Whether a position (account, asset) is inside the filters, for the sums that do not move
    with a price: a position with no asset (a fee, interest) is inside unless a kind or tag is chosen."""

    def keep(account_id, asset_id) -> bool:
        if account_id is not None and not filters.account_ok(account_id):
            return False
        if asset_id is None:
            return not (filters.classes or filters.tags)
        return filters.asset_ok(facts[asset_id]) if asset_id in facts else True

    return keep
