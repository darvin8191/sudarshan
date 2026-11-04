"""Generator of the PA7 golden vectors: import_csv.json and import_generic.json.

How a file of entries is read and what importing it would do to a tab (design 4.11): the CSV
itself (quotes, line endings, formula guard), plain numbers, day-first dates, the generic format's
rows, duplicate keys, the ledger check and the export that reads back as the same ledger. Like the
other generators it imports nothing from sudarshan. Every case carries its hand derivation; the
evaluator below reads the same text again by a different road (a character-by-character CSV
reader, Decimal for numbers, a calendar worked out from civil days, a quantity-only ledger that
judges each incoming row against the prefix of the order), and the generator refuses to write a
file in which what was typed by hand and what was computed disagree.

Run: python3 make_pa7.py
"""

import hashlib
import re
from decimal import Decimal
from pathlib import Path

from portfolio_vector_tools import (
    RUPEE,
    civil_days,
    day_text,
    group_in,
    inr,
    plus_days,
    qty_text,
    rd,
    write_all,
)

BOM = chr(0xFEFF)
ARABIC_THREE = chr(0x663)
TAB, CR = chr(9), chr(13)
S = 10**6  # one unit
R = 10**4  # one rupee as price_u

# ---------------------------------------------------------------- the owner's words, written again
UNREADABLE = (
    "This file could not be read as a CSV table. "
    "Save it as CSV (UTF-8) from your spreadsheet and try again."
)
EMPTY = "The file is empty. Choose a CSV file whose first row names the columns."
ACTIONS = (
    "BUY, SELL, DIVIDEND, INTEREST, FEE, SPLIT, BONUS, RIGHTS, TRANSFER_IN, TRANSFER_OUT or TRANSFER"
)
NOUNS = {
    "BUY": "purchase",
    "SELL": "sale",
    "RIGHTS": "rights purchase",
    "DIVIDEND": "dividend",
    "INTEREST": "interest entry",
    "FEE": "fee",
    "TRANSFER": "transfer",
    "SPLIT": "split",
    "BONUS": "bonus",
}
BIG = "This file is larger than 1.2 MB. Download it in shorter date ranges and import each part."


def echo(text):
    """What a sentence shows of a cell: one line, cut to 30 characters (27 and three dots)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= 30 else flat[:27] + "..."


def quote_list(names):
    quoted = ["'" + n + "'" for n in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def say_row(n, sentence):
    return f"Row {n}: {sentence}"


def say_date_problem(text):
    return (
        f"The date '{echo(text)}' is not a real date. "
        "Write it as 2026-03-10, 10-03-2026 or 10/03/2026 (day first)."
    )


def say_number_problem(label, text, why):
    if why == "negative":
        hint = " The action says whether it is a purchase or a sale." if label == "quantity" else ""
        return f"The {label} '{echo(text)}' cannot be negative.{hint}"
    if why == "number":
        return (
            f"The {label} '{echo(text)}' is not a number. "
            "Write it with digits and a decimal point only, without commas or a currency sign."
        )
    return f"The {label} '{echo(text)}' has more than {why} decimals."


# ---------------------------------------------------------------- the CSV, read one character at a time
def read_records(text):
    """CSV text as a list of records (lists of cells), or None when it cannot be read.

    A record ends at a line break outside quotes (CRLF, LF or a lone CR); an empty line is a record
    of one empty cell. A quote opens a quoted cell only at the start of a cell (after any spaces
    that follow a comma); inside quotes a doubled quote is one quote; after the closing quote only
    a comma or the end of the line may follow.
    """
    if "\x00" in text:
        return None
    if text.startswith(BOM):
        text = text[1:]
    records, cells, cell = [], [], []
    quoted = closed = False
    fresh = True  # at the start of a cell: spaces are skipped
    pending = False  # something has been read since the last line break
    i = 0
    while i < len(text):
        ch = text[i]
        pending = True
        if quoted:
            if ch == '"':
                if text[i + 1 : i + 2] == '"':
                    cell.append('"')
                    i += 1
                else:
                    quoted, closed = False, True
            else:
                cell.append(ch)
        elif ch == ",":
            cells.append("".join(cell))
            cell, closed, fresh = [], False, True
        elif ch in "\r\n":
            if ch == "\r" and text[i + 1 : i + 2] == "\n":
                i += 1
            cells.append("".join(cell))
            records.append(cells)
            cells, cell, closed, fresh, pending = [], [], False, True, False
        elif closed:
            return None
        elif fresh and ch == " ":
            pass
        elif fresh and ch == '"':
            quoted, fresh = True, False
        else:
            cell.append(ch)
            fresh = False
        i += 1
    if quoted:
        return None
    if pending:
        cells.append("".join(cell))
        records.append(cells)
    return records


DANGER = "=+-@" + TAB + CR


def guard(cell):
    """One apostrophe in front when the cell, behind any apostrophes, starts like a formula."""
    body = cell.lstrip("'")
    return "'" + cell if body[:1] and body[0] in DANGER else cell


def unguard(cell):
    """One apostrophe off a cell that starts with an apostrophe and then looks like a formula."""
    body = cell.lstrip("'")
    return cell[1:] if cell.startswith("'") and body[:1] and body[0] in DANGER else cell


def header_name(cell):
    return re.sub(r"[\s\-]+", "_", cell.strip().lower())


def table(text):
    """The table in a text: ``{"header": [...], "records": [[number, {name: cell}, extra]]}`` or
    ``{"refused": sentence}``."""
    records = read_records(text)
    if records is None:
        return {"refused": UNREADABLE}
    numbered = list(enumerate(records, 1))
    first = next((k for k, (_, r) in enumerate(numbered) if any(c.strip() for c in r)), None)
    if first is None:
        return {"refused": EMPTY}
    names = [header_name(unguard(c)) for c in numbered[first][1]]
    wanted = [n for n in names if n]
    for n in wanted:
        if wanted.count(n) > 1:
            return {"refused": f"The column '{n}' appears twice in the first row. Keep one of them."}
    out = []
    for number, record in numbered[first + 1 :]:
        cells = [unguard(c).strip() for c in record]
        if not any(cells):
            continue
        row = {n: "" for n in wanted}
        for n, c in zip(names, cells):
            if n:
                row[n] = c
        out.append([number, row, any(cells[len(names) :])])
    return {"header": wanted, "records": out}


# ---------------------------------------------------------------- numbers, days, ratios
def number(text, places):
    """A plain number as a whole number of 10^-places, or 'number', 'negative', or the places."""
    t = text.strip()
    plain = r"[0-9]+(\.[0-9]*)?|\.[0-9]+"
    if len(t) > 30:
        return "number"
    if not re.fullmatch(plain, t):
        return "negative" if t.startswith("-") and re.fullmatch(plain, t[1:]) else "number"
    scaled = Decimal(t).scaleb(places)
    return int(scaled) if scaled == scaled.to_integral_value() else places


def read_day(text):
    """YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY as YYYYMMDD when it is a real day from 1990 to 2200."""
    t = text.strip()
    m = re.fullmatch(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})", t)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = re.fullmatch(r"([0-9]{1,2})([-/])([0-9]{1,2})\2([0-9]{4})", t)
        if not m:
            return None
        d, mo, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
    if not (1990 <= y <= 2200 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    day = y * 10000 + mo * 100 + d
    return day if plus_days(day, 0) == day else None


def read_ratio(text):
    m = re.fullmatch(r"([0-9]{1,6})(\s*[:/]\s*([0-9]{1,6}))?", text.strip())
    if not m:
        return None
    return [int(m.group(1)), int(m.group(3)) if m.group(3) is not None else 1]


def decimal_text(value, places, least):
    """A whole number of 10^-places as plain text with at least ``least`` decimals."""
    whole, part = divmod(value, 10**places)
    frac = (str(part).rjust(places, "0") if places else "").rstrip("0")
    frac = frac + "0" * (least - len(frac)) if len(frac) < least else frac
    return f"{whole}.{frac}" if frac else str(whole)


def iso(day):
    return f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"
