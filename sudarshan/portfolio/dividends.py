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
  SUDARSHAN kept records), an amount is never guessed, and the dividend is never dropped in
  silence either (v0.13.0 review H1): when the records do not show the share was not held, the
  account gets a line with no quantity and no amount, listed apart as not counted
  (``rules.uncounted``) with its sentence, in no total, until the owner confirms what reached his
  bank. The same for a dividend Upstox lists with no amount.
* A family member sees none of this: no op here is a family op.
"""

from __future__ import annotations

from typing import Any

from sudarshan.tax import dividends as rules

from . import corpactions
from .core import corpactions as core
from .core.days import add_days, day_of_instant, financial_year, is_day
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


def sync(tx: Tx, today: int, now: int, held_now: list[dict[str, Any]] | None = None) -> int:
    """Bring the lines up to what Upstox lists and what was held. How many lines changed.

    A dividend is never silently dropped (review H1). Where no closing record says what the
    Upstox account held the day before the ex-date, and the records do not show the share was
    not held (``Holdings.upstox_maybe``), the account gets a line with no quantity and no
    amount: listed as "Not counted" (``rules.uncounted``) until the owner confirms what reached
    his bank. The same for a dividend Upstox lists without an amount."""
    holdings = corpactions.Holdings(tx, today, held_now, now)
    accounts = {a["id"]: a for a in tx.rows("account")}
    upstox = next((a for a in accounts.values() if a["kind"] == "upstox"), None)
    mine = corpactions.upstox_isins(tx, held_now) if upstox else set()
    lines = {(r["account_id"], r["event_id"]): r for r in tx.rows("dividend_line")}
    changed, pending = 0, []
    for event in tx.ca_events(to_day=today, gone=True):
        if event["kind"] != "dividend":
            continue
        have_all = {key: row for key, row in lines.items() if key[1] == event["id"]}
        if event["gone_at"] is not None:
            for row in have_all.values():
                if row["state"] == "expected":
                    changed += tx.remove("dividend_line", row["id"])
            continue
        held = _held(holdings, upstox, event)
        if (
            upstox is not None
            and event["isin"] in mine
            and holdings.upstox(event["isin"], event["ex_day"]) is None
            and holdings.upstox_maybe(event["isin"], event["ex_day"])
        ):
            held.setdefault(upstox["id"], 0)  # held or not is not known: no quantity
        for key, row in have_all.items():  # an unknown line the records no longer call for
            if key[0] not in held and row["state"] == "expected" and not row["qty_u"]:
                changed += tx.remove("dividend_line", row["id"])
        for account, qty in held.items():
            amount = event["amount_u"]
            values = {"qty_u": qty,
                      "expected_paise": rules.expected_paise(qty, amount) if amount else 0}  # fmt: skip
            have = have_all.get((account, event["id"]))
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


def _year(tx: Tx, fy: int, member_id: int, accounts: set[int] | None = None,
          names: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    names = corpactions.share_names(tx) if names is None else names
    lines = [
        {**{key: row[key] for key in LINE_KEYS}, "name": names.get(row["isin"]), "record_day": None}
        for row in tx.rows("dividend_line", "ex_day,id", member_id=member_id, fy=fy)
        if accounts is None or row["account_id"] in accounts
    ]
    return rules.year(lines, fy, names)


def _mark_records(tx: Tx, of_year: dict[str, Any], today: int, previous_day) -> None:
    """Review L4. A line of the Upstox account still expected takes its quantity from the last
    closing record within ``core.EVE_REACH_DAYS`` before the ex-date. Where that record is not
    the one of the trading day just before the ex-date (``previous_day``: a night's record was
    missed), the line says which record it is from (``record_day``): a trade in between would
    not be in it."""
    upstox = next((a for a in tx.rows("account") if a["kind"] == "upstox"), None)
    if upstox is None or previous_day is None:
        return
    record_days = [row["day"] for row in tx.rows("upstox_eod", "day")]
    for line in [ln for payer in of_year["payers"] for ln in payer["lines"]]:
        if line["account_id"] != upstox["id"] or line["state"] != "expected":
            continue
        used = core.eve_record(record_days, line["ex_day"], today)
        eve = previous_day(line["ex_day"])
        if used is not None and eve is not None and used < eve:
            line["record_day"] = used


NO_YEAR = "Dividends are worked out from the financial year {first} on. Choose a later year."


def report(tx: Tx, fy: int, member_id: int, now: int, held_now: list[dict] | None = None,
           previous_day=None) -> dict[str, Any]:  # fmt: skip
    """``dividends``: a taxpayer's lines of a financial year by payer, the totals and the
    likely-TDS flags, the dividends that could not be counted, and whether Upstox's list was
    read at all (types.ts ``DividendsReport``). ``held_now`` names a share the Upstox account
    holds (review M2); ``previous_day(day)`` is the trading day before a day, or None."""
    if tx.get("member", member_id) is None:
        raise NotFound()
    if fy < rules.first_year():
        first = rules.first_year()
        raise Invalid(NO_YEAR.format(first=f"{first}-{(first + 1) % 100:02d}"))
    today = day_of_instant(now)
    of_year = _year(tx, fy, member_id, names=corpactions.share_names(tx, held_now))
    _mark_records(tx, of_year, today, previous_day)
    return {"fy": fy, "member_id": member_id, "as_of": now, "dividends": of_year,
            "read_status": corpactions.read_status(tx, today)}  # fmt: skip


#: What the working paper says of its dividends when it cannot count them all (v0.13.0 review
#: H1): the insights switch is off, so Upstox's list is never read; it was never read yet; or the
#: last night's read failed (that night's own sentence follows).
PAPER_OFF = (
    "This paper counts no dividends yet: SUDARSHAN reads them from Upstox's corporate actions "
    "only once the dividends view is switched on in a later release. Take the year's dividends "
    "from your bank statements or Form 26AS."
)
PAPER_NOT_READ = (
    "This paper counts no dividends yet: SUDARSHAN reads Upstox's corporate actions for the "
    "first time tonight after 23:45, and the dividends of what you held follow."
)
PAPER_PAUSED = (
    "The dividends view is switched off, so no dividend after it was switched off is read from "
    "Upstox. Take any later ones from your bank statements or Form 26AS."
)
PAPER_FAILED = "Dividends may be missing here."


def for_pack(tx: Tx, fy: int, member_id: int, buyback_paise: int = 0, today: int | None = None,
             insights: bool = True) -> dict[str, Any]:  # fmt: skip
    """For the tax working paper (``tax_ops``): the pack's ``dividends`` block of the taxpayer's
    accounts that count for tax, with a buyback's receipt (the pack's own figure) beside the
    payers. A line still expected counts at its expected amount; ``unconfirmed_count`` says so.
    The dividends it could not count are listed (``uncounted``), and ``note`` says why a total
    of nothing is not "none": the insights switch is off (``insights``), the list was never read
    yet, or the last night failed."""
    counted = {a["id"] for a in tx.tax_accounts(member_id) if a["tax_mode"] != "none"}
    if fy < rules.first_year():
        of_year = rules.no_year()
    else:
        of_year = _year(tx, fy, member_id, counted)
    block = rules.block(of_year, buyback_paise)
    if not insights:
        block["note"] = PAPER_PAUSED if block["by_payer"] or block["uncounted"] else PAPER_OFF
        return block
    status = corpactions.read_status(tx, today) if today is not None else None
    if status is not None and status["state"] == "never":
        block["note"] = PAPER_NOT_READ
    elif status is not None and status["state"] == "failed":
        block["note"] = f"{PAPER_FAILED} {status['words']}"
    return block


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


def calendar(tx: Tx, today: int, from_day: int, to_day: int,
             held_now: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """``calendar``: the events between two days, each with its public facts and, apart from
    them, what is held (the day before the ex-date; for an event to come, now) and the amount a
    dividend is expected to bring (types.ts ``CalendarReport``). ``listed_future`` is whether
    Upstox lists any event to come at all; ``read_status`` whether its list was read at all, so
    that an empty answer before the first night, or after a failed one, is never shown as
    "nothing listed"."""
    holdings = corpactions.Holdings(tx, today, held_now)
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
    return {"from": from_day, "to": to_day, "listed_future": bool(ahead), "calendar": rows,
            "read_status": corpactions.read_status(tx, today)}  # fmt: skip


__all__ = ["calendar", "for_pack", "owner_member", "report", "set_line", "sync"]
