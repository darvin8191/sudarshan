"""The tax lots against the Upstox holdings (design p7 4.3, 6.3; review H3).

A bonus or a split reaches the tax lots through the owner's one tap, on a ratio whose direction
Upstox does not say. So after any applied action, and every night, the quantity the lots hold of
each share is compared with what the Upstox account held, and a difference is said in words: on a
"Check" card that names the share and both figures, and in the working paper, which marks that
share's sales "to check".

* **Like against like.** Upstox's trade history is read once a night, and a day's trades may reach
  it only the next day (design 6.4 item 1). So the record compared is the last closing record at
  least ``SETTLE_DAYS`` before the day the history was read, and the lots are replayed up to that
  day: never today's holdings against yesterday's trades.
* **Only what one record can say.** A closing record is taken at 15:55, when that day's own trades
  may still sit in the day's positions rather than in the holdings, and a bonus's or a split's new
  shares reach the demat account some days after the ex-date. So a share traded on the day
  compared, or with a bonus or a split in the ``core.EVE_REACH_DAYS`` up to it, is left out of
  that night's comparison and compared on a later one.
* **It translates and never decides.** The lots are the tax core's own replay
  (``sudarshan.tax.lots``) of the inputs the working paper uses (``tax_adapter.inputs``).
* **A share with no purchase in the records is not a mismatch.** Upstox keeps three financial
  years of trades; a share bought before them and never sold since has no lot at all. Those are
  only counted, unless a bonus or a split was applied to them.
* **The one fix it can prove.** Where an applied bonus or split explains a difference, and its
  other reading (or leaving the lots as they were) would make the lots and the holdings agree,
  the row carries that change (``fix``): the phone offers it as one tap through ``ca_decide``.
* **Only what the paper computes.** An ETF, a bond or a fund has no lots here and is passed over.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sudarshan.tax import lots as tax_lots
from sudarshan.tax.classify import is_equity_share
from sudarshan.tax.units import fy_first, fy_label

from . import corpactions, tax_adapter
from .core import corpactions as core
from .core.days import add_days, day_of_instant, days_between, financial_year
from .core.kinds import REPORT_YEARS
from .core.words import date as day_words
from .core.words import quantity
from .store import Tx

#: The record compared is at least this many days older than the last read of the trades.
SETTLE_DAYS = 2
WAITING_TRADES = (
    "The check of the tax lots against your Upstox holdings waits until Upstox's share trades "
    "of this year are read in full."
)
WAITING_RECORD = (
    "The check of the tax lots against your Upstox holdings waits for a closing record of the "
    "Upstox account from two days or more before its trades were last read."
)


class TaxLots:
    """The Upstox account's tax lots, replayed up to any day. Built once per transaction: the
    inputs are the working paper's own for the running year (every complete year of history)."""

    def __init__(self, tx: Tx, today: int, now: int):
        upstox = next((a for a in tx.rows("account") if a["kind"] == "upstox"), None)
        self.account = upstox["id"] if upstox else None
        self.given = self.read_at = None
        #: The first year of share trades the lots are replayed from, and the earlier years
        #: Upstox keeps that are not read in full (a purchase in them is missing until they are).
        self.first_year, self.unread = financial_year(today), []
        if upstox is None or (upstox.get("tax_mode") or "reports") != "reports":
            return
        fy = financial_year(today)
        given = tax_adapter.inputs(tx, upstox["member_id"], fy, today, now)
        shares = [s for s in given.sources if (s["kind"], s["segment"]) == ("trades", "EQ")]
        if shares and shares[0]["state"] == "ok":
            self.given, self.read_at = given, shares[0]["read_at"]
            self.first_year = (tax_adapter.read_years(tx, fy) or [fy])[0]
            self.unread = [year for year in range(fy - REPORT_YEARS + 1, self.first_year)
                           if not tax_adapter.read_years(tx, year)]  # fmt: skip

    @property
    def ready(self) -> bool:
        """Whether the year's share trades are read in full: without them the lots say nothing."""
        return self.given is not None

    def isins(self) -> set[str]:
        """Every share the replay could hold a lot of."""
        if self.given is None:
            return set()
        found = {t.instrument for t in self.given.trades if isinstance(t.instrument, str)}
        return found | {lot.instrument for lot in self.given.opening}

    def traded_on(self, day: int) -> set[str]:
        """The shares the Upstox account traded on ``day``."""
        if self.given is None:
            return set()
        return {t.instrument for t in self.given.trades
                if t.day == day and t.account == self.account}  # fmt: skip

    def qty(self, isin: str, up_to: int, actions: Any = None) -> int | None:
        """The quantity the lots hold of ``isin`` at the close of ``up_to``: its trades and
        purchases up to that day and the actions the owner applied with an ex-date up to it
        (``actions`` in their place, to try another reading). None when the lots cannot say."""
        given, mine = self.given, self.account
        if given is None:
            return None
        book = tax_lots.replay(
            [t for t in given.trades
             if t.instrument == isin and t.account == mine and t.day <= up_to],
            [lot for lot in given.opening
             if lot.instrument == isin and lot.account == mine and lot.acquired_day <= up_to],
            [a for a in (given.actions if actions is None else actions)
             if a.instrument == isin and a.account in (None, mine) and a.ex_day <= up_to],
            given.all_delivery,
            given.aliases,
        )  # fmt: skip
        return sum(lot.qty_u for lot in book.open_lots if lot.account == mine)

    def qty_turned(self, isin: str, up_to: int, action: dict, way: tuple[int, int] | None):
        """``qty`` with one applied action (``core.action_row``) read another way, or left out
        when ``way`` is None."""
        if self.given is None:
            return None
        key = (action["kind"], action["instrument"], action["ex_day"])
        mine = [a for a in self.given.actions
                if a.account == self.account and (a.kind, a.instrument, a.ex_day) == key]  # fmt: skip
        if len(mine) != 1:
            return None
        others = [a for a in self.given.actions if a is not mine[0]]
        if way is not None:
            others.append(replace(mine[0], num=way[0], den=way[1]))
        return self.qty(isin, up_to, others)


def _record(tx: Tx, day: int) -> dict[str, int]:
    held: dict[str, int] = {}
    for line in tx.rows("upstox_eod_holding", "instrument_key", day=day):
        isin = corpactions.isin_of_key(line["instrument_key"])
        if isin is not None:
            held[isin] = held.get(isin, 0) + line["qty"] + (line["t1_qty"] or 0)
    return held


def _actions(tx: Tx, up_to: int) -> tuple[dict, dict, set[str]]:
    """The Upstox account's bonuses and splits up to a day, one per event however Upstox worded
    it (``core.groups``): per share, the one the owner applied last (its action as the tax core
    gets it, the decision and the event decided: the likelier mistake when one was applied twice),
    the last one still waiting, and the shares with one in the ``core.EVE_REACH_DAYS`` up to the
    day."""
    decided = corpactions.decisions(tx)
    applied: dict[str, tuple[dict, dict, dict]] = {}
    pending: dict[str, dict] = {}
    recent: set[str] = set()
    for group in core.groups(tx.ca_events(to_day=up_to, gone=True)):
        if any(0 <= days_between(e["ex_day"], up_to) < core.EVE_REACH_DAYS for e in group):
            recent.add(group[0]["isin"])
        row = corpactions.standing(group, decided, corpactions.UPSTOX_SCOPE)
        live = [event for event in group if event["gone_at"] is None]
        if row is not None and row["state"] == "applied":
            event = next(e for e in group if e["id"] == row["event_id"])
            dated = live[-1] if live else event  # dated as accepted_actions dates it
            action = core.action_row(dated, row["ratio_num"], row["ratio_den"], True)
            if event["isin"] not in applied or applied[event["isin"]][1]["id"] < row["id"]:
                applied[event["isin"]] = (action, row, event)
        elif row is None and live:
            pending[live[-1]["isin"]] = live[-1]
    return applied, pending, recent


def _fix(lots: TaxLots, isin: str, day: int, held: int, applied: tuple) -> dict | None:
    """The change to an applied action that makes the lots agree with the holdings, if one does:
    its other reading, else leaving the lots as they were."""
    action, row, event = applied
    ways = core.readings(event["kind"], event["ratio_num"], event["ratio_den"])
    turned = [way for way in ways if way != (row["ratio_num"], row["ratio_den"])]
    for way in [*turned[:1], None]:
        if lots.qty_turned(isin, day, action, way) == held:
            state = "applied" if way is not None else "dismissed"
            num, den = way if way is not None else (None, None)
            return {"event_id": event["id"], "version": row["version"], "state": state,
                    "ratio_num": num, "ratio_den": den}  # fmt: skip
    return None


#: Lots below the holdings: the commoner cause first (v0.13.0 verification H1). A bonus or a split
#: since the purchase reaches the lots only once the owner confirms it, and a purchase entered for
#: its shares would carry a made-up cost and date. Before the Calendar is switched on, and after.
BONUS_LATER = (
    "If the company gave a bonus or split its shares since you bought, do nothing: that is "
    "confirmed in the Calendar in a later release."
)
BONUS_CALENDAR = (
    "If the company gave a bonus or split its shares since you bought, confirm it in the "
    "Calendar; do not add a purchase for it."
)


def _purchase(lots: TaxLots) -> str:
    """Where a purchase may really be missing: bought before the share trades SUDARSHAN has read,
    or moved in. Never a day the records do not reach: while Upstox keeps earlier years that are
    not read in full, the sentence says to read them first."""
    first = day_words(fy_first(lots.first_year))
    if not lots.unread:
        return (f"Add a purchase under Tax, Inputs only for shares bought before {first}, "
                "where the trades SUDARSHAN has read begin, or moved in from another "
                "account.")  # fmt: skip
    years = " and ".join(fy_label(year) for year in lots.unread)
    return (f"SUDARSHAN has read Upstox's trades from {first} only: read {years} on the Upstox "
            "tab's Trades view first. Then add a purchase under Tax, Inputs only for shares "
            "bought before the first day read, or moved in from another account.")  # fmt: skip


def _row(isin: str, name: str | None, held: int, lots: int, purchase: str, applied, pending,
         fix: dict | None, insights: bool) -> dict[str, Any]:  # fmt: skip
    label, have, said = name or isin, quantity(held), quantity(lots)
    if applied is not None:
        action = applied[0]
        what = f"the {action['kind'].lower()} of {day_words(action['ex_day'])}"
        words = f"{label}: after {what} the lots say {said} and Upstox holds {have}."
        if fix is not None and fix["state"] == "applied":
            words += (f" Read the other way round, the lots would say {have}: it was likely "
                      "confirmed the wrong way round.")  # fmt: skip
        elif fix is not None:
            words += (f" Without it the lots would say {have}: it may have been counted "
                      "twice.")  # fmt: skip
        else:
            words += (" Neither reading of it makes the two agree: a purchase or a sale may "
                      "be missing from the records.")  # fmt: skip
    elif pending is not None:
        words = (f"{label}: Upstox holds {have} and the lots say {said}. The {pending['kind']} "
                 f"of {day_words(pending['ex_day'])} waits for you under To confirm.")  # fmt: skip
    elif lots < held:
        cause = BONUS_CALENDAR if insights else BONUS_LATER
        words = f"{label}: Upstox holds {have} and the lots say {said}. {cause} {purchase}"
    else:
        words = (f"{label}: Upstox holds {have} and the lots say {said}. A sale, a transfer "
                 "out or a bonus or split may be missing from the records.")  # fmt: skip
    return {
        "isin": isin,
        "name": name,
        "held_qty_u": held,
        "lots_qty_u": lots,
        "ex_day": applied[0]["ex_day"] if applied is not None else None,
        "words": words,
        "fix": fix,
    }


def check(tx: Tx, today: int, now: int, lots: TaxLots | None = None, *, insights: bool,
          taxpayer: int | None = None) -> dict[str, Any]:  # fmt: skip
    """``LotCheck`` (types.ts): the shares whose tax lots and Upstox holdings differ.

    ``state`` is ``ok`` (compared; ``rows`` may be empty), ``waiting`` (it cannot compare yet,
    and ``words`` says for what) or ``none`` (no Upstox account that counts by its reports, or
    a ``taxpayer`` who does not own it: the check belongs on its owner's paper only, never on
    another family member's, which goes to that member's CA). ``day`` is the closing record
    compared with. ``words`` is the headline when rows differ: "Lots and holdings differ for 2
    shares: ABC 100 held, 90 in lots; ...". ``unrecorded_count`` is how many shares are held
    with no purchase in the records at all. ``insights`` is whether the Calendar, where a bonus
    or a split is confirmed, is switched on."""
    out: dict[str, Any] = {"state": "none", "account_id": None, "day": None, "words": None,
                           "rows": [], "unrecorded_count": 0}  # fmt: skip
    upstox = next((a for a in tx.rows("account") if a["kind"] == "upstox"), None)
    if taxpayer is not None and (upstox is None or upstox["member_id"] != taxpayer):
        return out
    lots = lots or TaxLots(tx, today, now)
    out["account_id"] = lots.account
    if lots.account is None:
        return out
    if not lots.ready or lots.read_at is None:
        return {**out, "state": "waiting", "words": WAITING_TRADES}
    last = add_days(day_of_instant(lots.read_at), -SETTLE_DAYS)
    days = [row["day"] for row in tx.rows("upstox_eod", "day") if row["day"] <= last]
    if not days:
        return {**out, "state": "waiting", "words": WAITING_RECORD}
    day = days[-1]
    held = _record(tx, day)
    applied, pending, recent = _actions(tx, day)
    names = corpactions.share_names(tx)
    purchase = _purchase(lots)
    skip = recent | lots.traded_on(day)
    rows, unrecorded = [], 0
    for isin in sorted(set(held) | lots.isins() | set(applied)):
        if not is_equity_share(isin) or isin in skip:
            continue
        have, in_lots = held.get(isin, 0), lots.qty(isin, day) or 0
        if have == in_lots:
            continue
        if in_lots == 0 and isin not in applied:
            unrecorded += 1  # bought before the records reach, or moved in: no lot to compare
            continue
        done = applied.get(isin)
        fix = _fix(lots, isin, day, have, done) if done is not None else None
        rows.append(_row(isin, names.get(isin), have, in_lots, purchase, done,
                         pending.get(isin), fix, insights))  # fmt: skip
    words = None
    if rows:
        listed = "; ".join(
            f"{r['name'] or r['isin']} {quantity(r['held_qty_u'])} held, "
            f"{quantity(r['lots_qty_u'])} in lots"
            for r in rows
        )
        words = (
            f"Lots and holdings differ for {len(rows)} {'share' if len(rows) == 1 else 'shares'}"
            f": {listed}. Compared at the close of {day_words(day)}."
        )
    return {**out, "state": "ok", "day": day, "words": words, "rows": rows,
            "unrecorded_count": unrecorded}  # fmt: skip


__all__ = ["SETTLE_DAYS", "TaxLots", "check"]
