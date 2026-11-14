"""Expected dividends, the owner's confirmation of each, and the calendar (phase P7, lane C:
M7.13, M7.29, M7.36; design p7 9.5).

A dividend Upstox lists becomes one line per account that held the share at the close of the day
before the ex-date: the quantity held then, times the rupees a share. The quantity comes from the
Upstox account's closing record of that day and from each custom tab's ledger; the arithmetic and
the likely-TDS flag are the tax core's (``sudarshan.tax.dividends``), so the Dividends view and the
tax working paper can never disagree.

* **Expected is not credited.** A line stays ``expected`` until the owner says what reached his
  bank (and the tax deducted from it), or that nothing came. Nothing here reads a bank.
* **A tab's own entry is not doubled.** Where a custom tab has a DIVIDEND entry for the share
  within ``LINK_DAYS`` of the ex-date, the line is linked to it and takes its figures; it is then
  changed in the tab, not here.
* **Lines are made once the ex-date is here** (``sync``: after the nightly read, and when the
  owner opens the view). A dividend to come is shown by the calendar with what is held now, and
  has no line yet. A line still expected follows a late closing record; a confirmed one is never
  touched; an expected line of an event Upstox withdrew is removed.
* **Where no closing record says** what the Upstox account held (an ex-date from before
  SUDARSHAN kept records), no line is made: an amount is never guessed.
* A family member sees none of this: no op here is a family op.
"""

from __future__ import annotations

from typing import Any

from sudarshan.tax import dividends as rules

from . import corpactions
from .core.days import add_days, financial_year, is_day
from .core.kinds import DIVIDEND_STATES
from .core.money import paise_ok
from .store import Invalid, NotFound, Refused, Tx

#: A tab's DIVIDEND entry this many days after the ex-date is still that dividend.
LINK_DAYS = 60
LINE_KEYS = ("id", "member_id", "account_id", "isin", "event_id", "activity_id", "fy", "ex_day",
             "qty_u", "expected_paise", "state", "credited_paise", "tds_paise", "credited_day",
             "version")  # fmt: skip
SAY = {
    "state": "Choose whether the dividend was received.",
    "amount": "Enter the amount that reached your bank, in rupees.",
    "tds": "Enter the tax deducted as an amount, or leave it empty.",
    "day": "Enter the day it was credited, not later than today.",
}


def _held(holdings: corpactions.Holdings, upstox: dict | None, event: dict) -> dict[int, int]:
    """Quantity per account the day before the event's ex-date, accounts holding none left out."""
    out: dict[int, int] = {}
    if upstox is not None:
        qty = holdings.upstox(event["isin"], event["ex_day"])
        if qty:
            out[upstox["id"]] = qty
    for tab in holdings.tabs:
        for account, qty in holdings.tab(tab["id"], event["isin"], event["ex_day"]).items():
            out[account] = out.get(account, 0) + qty
    return out


def _tab_entry(tx: Tx, asset_id: int, line: dict, taken: set[int], exact: bool) -> dict | None:
    """The owner's own DIVIDEND entry for this line's share and account, if a tab has one not
    yet linked: the earliest within ``LINK_DAYS`` of the ex-date; with ``exact``, only one whose
    gross amount is the amount expected."""
    last = add_days(line["ex_day"], LINK_DAYS)
    for row in tx.rows("activity", "trade_date,id", kind="DIVIDEND", asset_id=asset_id):
        if (
            row["status"] == "posted"
            and row["account_id"] == line["account_id"]
            and line["ex_day"] <= row["trade_date"] <= last
            and row["id"] not in taken
            and (not exact or row["amount_paise"] == line["expected_paise"])
        ):
            return row
    return None


def _link(tx: Tx, holdings: corpactions.Holdings, pending: list[dict], now: int) -> int:
    """Link the lines still expected to the tabs' own DIVIDEND entries: first each entry to the
    line whose expected amount it matches, then what is left by date, an entry to one line only.
    The line takes the entry's figures (its gross less the tax deducted, and its day)."""
    taken = {r["activity_id"] for r in tx.rows("dividend_line") if r["activity_id"] is not None}
    linked = 0
    for exact in (True, False):
        for line in [row for row in pending if row["id"] not in taken_lines(tx)]:
            asset = holdings.assets.get(line["isin"])
            entry = _tab_entry(tx, asset["id"], line, taken, exact) if asset else None
            if entry is None:
                continue
            taken.add(entry["id"])
            gross, held_back = entry["amount_paise"] or 0, entry["tds_paise"] or 0
            tx.change("dividend_line", line["id"], line["version"], {
                "state": "confirmed", "activity_id": entry["id"], "tds_paise": held_back,
                "credited_paise": gross - held_back, "credited_day": entry["trade_date"],
                "fy": financial_year(entry["trade_date"]),
            }, now)  # fmt: skip
            linked += 1
    return linked


def taken_lines(tx: Tx) -> set[int]:
    return {r["id"] for r in tx.rows("dividend_line") if r["activity_id"] is not None}


def sync(tx: Tx, today: int, now: int) -> int:
    """Bring the lines up to what Upstox lists and what was held. How many lines changed."""
    holdings = corpactions.Holdings(tx, today)
    accounts = {a["id"]: a for a in tx.rows("account")}
    upstox = next((a for a in accounts.values() if a["kind"] == "upstox"), None)
    lines = {(r["account_id"], r["event_id"]): r for r in tx.rows("dividend_line")}
    changed, pending = 0, []
    for event in tx.ca_events(to_day=today, gone=True):
        if event["kind"] != "dividend" or event["amount_u"] is None:
            continue
        mine = {key: row for key, row in lines.items() if key[1] == event["id"]}
        if event["gone_at"] is not None:
            for row in mine.values():
                if row["state"] == "expected":
                    changed += tx.remove("dividend_line", row["id"])
            continue
        for account, qty in _held(holdings, upstox, event).items():
            values = {"qty_u": qty, "expected_paise": rules.expected_paise(qty, event["amount_u"])}
            have = mine.get((account, event["id"]))
            if have is None:
                have = tx.insert("dividend_line", {
                    **values, "member_id": accounts[account]["member_id"], "account_id": account,
                    "isin": event["isin"], "event_id": event["id"], "ex_day": event["ex_day"],
                    "fy": financial_year(event["ex_day"]), "state": "expected",
                }, now)  # fmt: skip
                changed += 1
            elif have["state"] == "expected" and any(have[k] != v for k, v in values.items()):
                have = tx.change("dividend_line", have["id"], have["version"], values, now)
                changed += 1
            if have["state"] == "expected" and account != (upstox or {}).get("id"):
                pending.append(have)
    return changed + _link(tx, holdings, pending, now)


def owner_member(tx: Tx) -> int:
    """The taxpayer when none is named: the member the Upstox account belongs to."""
    upstox = [a for a in tx.rows("account") if a["kind"] == "upstox"]
    return upstox[0]["member_id"] if upstox else tx.rows("member")[0]["id"]


def _year(tx: Tx, fy: int, member_id: int, accounts: set[int] | None = None) -> dict[str, Any]:
    names = {a["isin"]: a["name"] for a in tx.rows("asset", kind="listed") if a["isin"]}
    lines = [
        {**{key: row[key] for key in LINE_KEYS}, "name": names.get(row["isin"])}
        for row in tx.rows("dividend_line", "ex_day,id", member_id=member_id, fy=fy)
        if accounts is None or row["account_id"] in accounts
    ]
    return rules.year(lines, fy, names)


def report(tx: Tx, fy: int, member_id: int, now: int) -> dict[str, Any]:
    """``dividends``: a taxpayer's lines of a financial year by payer, the totals and the
    likely-TDS flags (types.ts ``DividendsReport``)."""
    if tx.get("member", member_id) is None:
        raise NotFound()
    return {"fy": fy, "member_id": member_id, "as_of": now, "dividends": _year(tx, fy, member_id)}


def for_pack(tx: Tx, fy: int, member_id: int, buyback_paise: int = 0) -> dict[str, Any]:
    """For the tax working paper (``tax_ops``): the pack's ``dividends`` block of the taxpayer's
    accounts that count for tax, with a buyback's receipt (the pack's own figure) beside the
    payers. A line still expected counts at its expected amount; ``unconfirmed_count`` says so."""
    counted = {a["id"] for a in tx.tax_accounts(member_id) if a["tax_mode"] != "none"}
    return rules.block(_year(tx, fy, member_id, counted), buyback_paise)


def set_line(tx: Tx, line_id: int, args: dict[str, Any], today: int, now: int) -> dict[str, Any]:
    """``dividend_set``: the owner's word on one line, from its version. ``confirmed`` takes
    what reached the bank, the tax deducted and the day; the year it counts in is the year of
    that day. ``not_received`` and ``expected`` clear them."""
    line = tx.get("dividend_line", line_id)
    if line is None:
        raise NotFound()
    state = args.get("state")
    if state not in DIVIDEND_STATES:
        raise Invalid(SAY["state"])
    if line["activity_id"] is not None:
        raise Refused("This dividend is an entry in one of your tabs. Change the entry there.",
                      entry_id=line["activity_id"], reason="linked")  # fmt: skip
    values: dict[str, Any] = {"state": state, "credited_paise": None, "tds_paise": None,
                              "credited_day": None, "fy": financial_year(line["ex_day"])}  # fmt: skip
    if state == "confirmed":
        credited, tds, day = (args.get(k) for k in ("credited_paise", "tds_paise", "credited_day"))
        if not paise_ok(credited) or credited < 0:
            raise Invalid(SAY["amount"])
        if tds is not None and (not paise_ok(tds) or tds < 0):
            raise Invalid(SAY["tds"])
        if day is not None and (not is_day(day) or day > today):
            raise Invalid(SAY["day"])
        values.update(credited_paise=credited, tds_paise=tds, credited_day=day)
        if day is not None:
            values["fy"] = financial_year(day)
    row = tx.change("dividend_line", line_id, args.get("version"), values, now)
    return {key: row[key] for key in LINE_KEYS}


def calendar(tx: Tx, today: int, from_day: int, to_day: int) -> dict[str, Any]:
    """``calendar``: the events between two days, each with its public facts and, apart from
    them, what is held (the day before the ex-date; for an event to come, now) and the amount a
    dividend is expected to bring (types.ts ``CalendarReport``). ``listed_future`` is whether
    Upstox lists any event to come at all."""
    holdings = corpactions.Holdings(tx, today)
    upstox = next((a for a in tx.rows("account") if a["kind"] == "upstox"), None)
    reads = {r["isin"]: r["read_at"] for r in tx.ca_reads() if r["state"] == "ok"}
    rows = []
    for event in tx.ca_events(from_day=from_day, to_day=to_day):
        held = sum(_held(holdings, upstox, event).values())
        pays = event["kind"] == "dividend" and event["amount_u"] is not None and held
        rows.append({
            "event_id": event["id"], "isin": event["isin"], "name": holdings.name(event["isin"]),
            "kind": event["kind"], "ex_day": event["ex_day"], "amount_u": event["amount_u"],
            "ratio_num": event["ratio_num"], "ratio_den": event["ratio_den"],
            "held_qty_u": held or None,
            "expected_paise": rules.expected_paise(held, event["amount_u"]) if pays else None,
            "checked_at": reads.get(event["isin"]),
        })  # fmt: skip
    ahead = tx.ca_events(from_day=add_days(today, 1))
    return {"from": from_day, "to": to_day, "listed_future": bool(ahead), "calendar": rows}


__all__ = ["calendar", "for_pack", "owner_member", "report", "set_line", "sync"]
