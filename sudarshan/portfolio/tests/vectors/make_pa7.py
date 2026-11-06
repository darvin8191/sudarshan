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
ACTIONS = "BUY, SELL, DIVIDEND, INTEREST, FEE, SPLIT, BONUS, RIGHTS, TRANSFER_IN, TRANSFER_OUT or TRANSFER"
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
BIG = "This file is larger than 900 KB. Download it in shorter date ranges and import each part."


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
            return {
                "refused": f"The column '{n}' appears twice in the first row. Keep one of them."
            }
    out = []
    for number, record in numbered[first + 1 :]:
        cells = [unguard(c).strip() for c in record]
        if not any(cells):
            continue
        row = dict.fromkeys(wanted, "")
        for n, c in zip(names, cells, strict=False):
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


def write_text(header, rows):
    """CSV text as RFC 4180 writes it: cells guarded, quoted when they hold a comma, a quote or a
    line break, lines ended by CRLF."""

    def cell(text):
        text = guard(text)
        if any(ch in text for ch in ',"\r\n'):
            return '"' + text.replace('"', '""') + '"'
        return text

    return "".join(",".join(cell(c) for c in row) + "\r\n" for row in [header, *rows])


# ---------------------------------------------------------------- the key of a trade, worked out again
def money(qty_u, price_u):
    return rd(qty_u * price_u, 10**8)


def trade_key(prefix, entry, occurrence):
    """prefix:hash:occurrence; the hash is of the trade (kind, accounts, day, holding, quantity,
    amount, ratio) and nothing else."""
    amount = entry.get("amount_paise")
    if amount is None and entry.get("qty_u") is not None and entry.get("price_u") is not None:
        amount = money(entry["qty_u"], entry["price_u"])
    parts = [
        entry["kind"],
        entry.get("account_id"),
        entry.get("to_account_id"),
        entry["trade_date"],
        entry.get("asset_id"),
        entry.get("qty_u"),
        amount,
        entry.get("ratio_num"),
        entry.get("ratio_den"),
    ]
    basis = "|".join("" if p is None else str(p) for p in parts)
    return f"{prefix}:{hashlib.sha256(basis.encode('ascii')).hexdigest()[:32]}:{occurrence}"


# ================================================================ import_csv.json
TABLES = []


def tcase(cid, why, text, expect):
    got = table(text)
    if "refused" in expect:
        assert got == expect, (cid, got)
    else:
        want = {
            "header": expect["header"],
            "records": [[n, c, x] for n, c, x in expect["records"]],
        }
        assert got == want, (cid, got)
    TABLES.append({"id": cid, "why": why, "text": text, "expect": expect})


tcase(
    "T1",
    "Row 1 is the header, so the first record is row 2; names are lower-cased and spaces and "
    "hyphens become underscores; an empty cell is the empty text.",
    "Trade Date,Trade-Type,Note\n2026-01-05,buy,\n",
    {
        "header": ["trade_date", "trade_type", "note"],
        "records": [[2, {"trade_date": "2026-01-05", "trade_type": "buy", "note": ""}, False]],
    },
)
tcase(
    "T2",
    "A byte-order mark, CRLF line endings, spaces after a comma and a quoted cell with a comma, a "
    "doubled quote and a line break all pass; the record that holds the line break is one row and "
    "the next record is row 3.",
    BOM + 'a,b\r\n1, "x, ""y""\nz"\r\n2,3\r\n',
    {
        "header": ["a", "b"],
        "records": [[2, {"a": "1", "b": 'x, "y"\nz'}, False], [3, {"a": "2", "b": "3"}, False]],
    },
)
tcase(
    "T3",
    "Blank rows are skipped but keep their number: the record after two blank lines is row 4. A "
    "lone carriage return ends a line too.",
    "a\r1\r\r\r2\r",
    {"header": ["a"], "records": [[2, {"a": "1"}, False], [5, {"a": "2"}, False]]},
)
tcase(
    "T4",
    "A row with more cells than the header, the extra one not empty, is flagged; an extra cell that "
    "is empty (a trailing comma) is not.",
    "a,b\n1,2,3\n4,5,\n",
    {
        "header": ["a", "b"],
        "records": [[2, {"a": "1", "b": "2"}, True], [3, {"a": "4", "b": "5"}, False]],
    },
)
tcase(
    "T5",
    "A cell the platform guarded (one apostrophe in front of a formula) is read without it; a "
    "plain apostrophe in front of text is kept.",
    "a,b,c\n'=1+1,'text,''=x\n",
    {"header": ["a", "b", "c"], "records": [[2, {"a": "=1+1", "b": "'text", "c": "'=x"}, False]]},
)
tcase(
    "T6",
    "A column named twice refuses the whole file.",
    "a,A\n1,2\n",
    {"refused": "The column 'a' appears twice in the first row. Keep one of them."},
)
tcase("T7", "A quote that never closes refuses the whole file.", 'a\n"1\n', {"refused": UNREADABLE})
tcase("T8", "Text after a closing quote is not a table.", 'a,b\n1,"2"x\n', {"refused": UNREADABLE})
tcase("T9", "Nothing but blank lines is an empty file.", "\n\n  \n", {"refused": EMPTY})
tcase("T10", "A NUL character is no text file.", "a\n1\x00\n", {"refused": UNREADABLE})

NUMBERS = []


def ncase(text, places, expect):
    assert number(text, places) == expect, (text, places, number(text, places))
    NUMBERS.append({"text": text, "decimals": places, "expect": expect})


ncase("1428.25", 2, 142825)
ncase("10", 6, 10 * S)
ncase("0.5", 6, 500000)
ncase(".5", 2, 50)
ncase("5.", 2, 500)
ncase("100.50", 2, 10050)
ncase("100.500", 2, 10050)
ncase("100.501", 2, 2)
ncase("45.6789", 4, 456789)
ncase("45.67891", 4, 4)
ncase("1,428.25", 2, "number")
ncase(RUPEE + "100", 2, "number")
ncase("12abc", 2, "number")
ncase("1e3", 2, "number")
ncase("+5", 2, "number")
ncase("-5", 2, "negative")
ncase("-5.5", 2, "negative")
ncase("-", 2, "number")
ncase("", 2, "number")
ncase(ARABIC_THREE, 2, "number")
ncase("1" * 31, 2, "number")
ncase("0" * 29 + "7", 2, 700)

DAYS = []


def dcase(text, expect):
    assert read_day(text) == expect, (text, read_day(text))
    DAYS.append({"text": text, "expect": expect})


dcase("2026-03-10", 20260310)
dcase("10-03-2026", 20260310)
dcase("10/03/2026", 20260310)
dcase("5/1/2026", 20260105)
dcase("03/13/2026", None)
dcase("13/03/2026", 20260313)
dcase("2026-02-29", None)
dcase("29/02/2028", 20280229)
dcase("31/04/2026", None)
dcase("2026/03/10", None)
dcase("10-03/2026", None)
dcase("10 Mar 2026", None)
dcase("2026-3-1", 20260301)
dcase("", None)

RATIOS = []
for text, want in [
    ("5:1", [5, 1]),
    ("5/1", [5, 1]),
    ("1:10", [1, 10]),
    ("5", [5, 1]),
    (" 2 : 1 ", [2, 1]),
    ("a:b", None),
    ("5:", None),
    ("1:2:3", None),
    ("", None),
]:
    assert read_ratio(text) == want, text
    RATIOS.append({"text": text, "expect": want})

GUARDS = []
for cell, want in [
    ("=1+1", "'=1+1"),
    ("+5", "'+5"),
    ("-5", "'-5"),
    ("@SUM(A1)", "'@SUM(A1)"),
    (TAB + "x", "'" + TAB + "x"),
    (CR + "x", "'" + CR + "x"),
    ("'=x", "''=x"),
    ("text", "text"),
    ("'text", "'text"),
    ("", ""),
    ("5-3", "5-3"),
    ("a=b", "a=b"),
]:
    assert guard(cell) == want, cell
    assert unguard(want) == cell, cell  # the two undo each other
    GUARDS.append({"cell": cell, "written": want})

WRITES = []
for cid, header, rows, text in [
    ("W1", ["a", "b"], [["1", "x"]], "a,b\r\n1,x\r\n"),
    (
        "W2",
        ["a"],
        [["x, y"], ['say "hi"'], ["line\nbreak"]],
        'a\r\n"x, y"\r\n"say ""hi"""\r\n"line\nbreak"\r\n',
    ),
    ("W3", ["a"], [["=1+1"], ["-5"], ["@x"]], "a\r\n'=1+1\r\n'-5\r\n'@x\r\n"),
    ("W4", ["a"], [["=1,2"]], 'a\r\n"\'=1,2"\r\n'),
]:
    assert write_text(header, rows) == text, cid
    WRITES.append({"id": cid, "header": header, "rows": rows, "text": text})

IMPORT_CSV = {
    "about": (
        "Golden vectors for imports/csvutil.py (PA7): CSV text read as a table (record numbers, "
        "names, quotes, line endings, refusals), plain numbers as whole numbers of a unit, "
        "day-first dates, split ratios, the formula guard and CSV text written. The evaluator in "
        "make_pa7.py reads the same text a character at a time."
    ),
    "tables": TABLES,
    "numbers": NUMBERS,
    "days": DAYS,
    "ratios": RATIOS,
    "guards": GUARDS,
    "writes": WRITES,
}


# ================================================================ import_generic.json
TODAY = 20261030
WORLD = {
    "today": TODAY,
    "accounts": {"1": "Zerodha", "2": "Account B"},
    "default_account": 1,
    "assets": {
        "1": {"name": "RELIANCE", "symbol": "RELIANCE", "isin": "INE000A00001"},
        "2": {"name": "TCS", "symbol": "TCS", "isin": "INE000A00002"},
        "3": {"name": "INFY", "symbol": "INFY", "isin": "INE000A00003"},
    },
    "about": "Symbols and ISINs resolve without regard to case; every holding takes whole shares.",
}
HEAD = "date,action,symbol,exchange,isin,quantity,price,amount,charges,account,tds,ratio,note\n"


def ent(kind, day, account, **fields):
    """The entry a row means (contract names, the cells the row gave and nothing else)."""
    out = {"kind": kind, "trade_date": day, "account_id": account}
    out.update(fields)
    return out


def exist(i, kind, day, account, asset, qty, **fields):
    """An entry already in the tab."""
    return {
        "id": i,
        "seq": i,
        "kind": kind,
        "trade_date": day,
        "account_id": account,
        "asset_id": asset,
        "qty_u": qty * S,
        **fields,
    }


def row(n, status, entry=None, words=None, key=None):
    return {"row": n, "status": status, "words": words, "entry": entry, "key": key}


PREVIEWS = []


def pcase(cid, title, why, text, rows, summary, existing=(), keys=None, unused=(), keyed=True):
    """Add a preview case; the keys of rows without an id of their own are worked out here."""
    seen = {}
    for r in rows:
        if r["entry"] is not None and r["key"] is None and keyed:
            probe = trade_key("generic", r["entry"], 1)
            h = probe.rsplit(":", 1)[0]
            seen[h] = seen.get(h, 0) + 1
            r["key"] = f"{h}:{seen[h]}"
    PREVIEWS.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "text": text,
            "existing": list(existing),
            "keys": keys or {},
            "expect": {"summary": summary, "unused": list(unused), "rows": rows},
        }
    )


# I1: five rows, five new.
E1 = [
    ent(
        "BUY",
        20260105,
        1,
        asset_id=1,
        qty_u=10 * S,
        price_u=1400 * R,
        charges_paise=1250,
        note="first lot",
    ),
    ent("BUY", 20260210, 2, asset_id=2, qty_u=5 * S, price_u=3200 * R),
    ent("SELL", 20260302, 1, asset_id=1, qty_u=4 * S, price_u=1500 * R, charges_paise=1000),
    ent("DIVIDEND", 20260415, 1, asset_id=1, amount_paise=8000, tds_paise=800),
    ent("SPLIT", 20260520, None, asset_id=2, ratio_num=2, ratio_den=1),
]
pcase(
    "I1",
    "generic happy path",
    "Five rows of five kinds. Quantity 10 is 10,000,000 qty_u; a price of 1400.00 is 14,000,000 "
    "price_u; charges 12.50 are 1,250 paise. The first row names no account, so it is the "
    "account chosen for the import (1); row 3 names Account B. The split names none and is every "
    "account's (account null). Rows are numbered from 2 because the header is row 1.",
    HEAD
    + "2026-01-05,BUY,RELIANCE,NSE,,10,1400.00,,12.50,,,,first lot\n"
    + "2026-02-10,BUY,TCS,,INE000A00002,5,3200,,,Account B,,,\n"
    + "2026-03-02,SELL,reliance,,,4,1500,,10,,,,\n"
    + "2026-04-15,DIVIDEND,RELIANCE,,,,,80,,,8,,\n"
    + "2026-05-20,SPLIT,TCS,,,,,,,,,2:1,\n",
    [row(2 + k, "new", E1[k]) for k in range(5)],
    "5 new",
)

# I2: a row already in the tab.
KEY_A = trade_key("generic", ent("BUY", 20260105, 1, asset_id=1, qty_u=10 * S, price_u=1400 * R), 1)
pcase(
    "I2",
    "a row that is already in this tab",
    "Row 2 is the trade an earlier import added on 28 Oct 2026 (the tab keeps its key); the key is "
    "its content (BUY, account 1, 5 Jan 2026, holding 1, 10 shares, amount 14,000.00) and "
    "occurrence 1. Charges are not part of it. Row 3 is new. The date is this year's, so the "
    "sentence says '28 Oct' only.",
    HEAD + "2026-01-05,BUY,RELIANCE,,,10,1400,,5,,,,\n2026-01-06,BUY,RELIANCE,,,2,1410,,,,,,\n",
    [
        row(
            2,
            "dup",
            ent("BUY", 20260105, 1, asset_id=1, qty_u=10 * S, price_u=1400 * R, charges_paise=500),
            "Row 2 is already in this tab (imported 28 Oct).",
        ),
        row(3, "new", ent("BUY", 20260106, 1, asset_id=1, qty_u=2 * S, price_u=1410 * R)),
    ],
    "1 new, 1 already in this tab",
    existing=[exist(1, "BUY", 20260105, 1, 1, 10, price_u=1400 * R)],
    keys={KEY_A: 20261028},
)
PREVIEWS[-1]["expect"]["rows"][0]["key"] = KEY_A
pcase(
    "I2b",
    "an import from last year says the year",
    "The same tab with the earlier import dated 28 Oct 2025: the sentence keeps the year.",
    HEAD + "2026-01-05,BUY,RELIANCE,,,10,1400,,,,,,\n",
    [
        row(
            2,
            "dup",
            ent("BUY", 20260105, 1, asset_id=1, qty_u=10 * S, price_u=1400 * R),
            "Row 2 is already in this tab (imported 28 Oct 2025).",
            KEY_A,
        )
    ],
    "1 already in this tab",
    existing=[exist(1, "BUY", 20260105, 1, 1, 10, price_u=1400 * R)],
    keys={KEY_A: 20251028},
)

# I3: two identical rows in one file.
ONE = ent("BUY", 20260105, 1, asset_id=3, qty_u=1 * S, price_u=1500 * R)
K1, K2, K3 = (trade_key("generic", ONE, n) for n in (1, 2, 3))
THREE = HEAD + "".join("2026-01-05,BUY,INFY,,,1,1500,,,,,,\n" for _ in range(3))
pcase(
    "I3",
    "identical rows in one file all import",
    "Three identical purchases are three trades: they get the occurrence numbers 1, 2 and 3 on the "
    "same content, so none is a duplicate of another.",
    THREE,
    [row(2, "new", ONE, key=K1), row(3, "new", ONE, key=K2), row(4, "new", ONE, key=K3)],
    "3 new",
)
pcase(
    "I3b",
    "the same file read again adds nothing",
    "The tab now holds the three keys of I3: every row of the same file is already here, with the "
    "date the first import was made.",
    THREE,
    [
        row(2, "dup", ONE, "Row 2 is already in this tab (imported 29 Oct).", K1),
        row(3, "dup", ONE, "Row 3 is already in this tab (imported 29 Oct).", K2),
        row(4, "dup", ONE, "Row 4 is already in this tab (imported 29 Oct).", K3),
    ],
    "3 already in this tab",
    existing=[exist(1, "BUY", 20260105, 1, 3, 3, price_u=1500 * R)],
    keys={K1: 20261029, K2: 20261029, K3: 20261029},
)
pcase(
    "I3c",
    "a file with one more of the same trade adds only that one",
    "The tab holds occurrences 1 and 2 of the trade; the file has three: occurrence 3 alone is new.",
    THREE,
    [
        row(2, "dup", ONE, "Row 2 is already in this tab (imported 29 Oct).", K1),
        row(3, "dup", ONE, "Row 3 is already in this tab (imported 29 Oct).", K2),
        row(4, "new", ONE, key=K3),
    ],
    "1 new, 2 already in this tab",
    existing=[exist(1, "BUY", 20260105, 1, 3, 2, price_u=1500 * R)],
    keys={K1: 20261029, K2: 20261029},
)

# I4: unknown symbol.
pcase(
    "I4",
    "an unknown symbol",
    "TATAMTR is not in the instrument list. The bad row is told first in a listing; the good row "
    "is new. A cell echoed in a sentence is cut at 30 characters.",
    HEAD + "2026-01-05,BUY,TATAMTR,,,1,100,,,,,,\n2026-01-06,BUY,TCS,,,1,3000,,,,,,\n",
    [
        row(2, "bad", None, "Row 2: TATAMTR is not in today's instrument list. Check the symbol."),
        row(3, "new", ent("BUY", 20260106, 1, asset_id=2, qty_u=S, price_u=3000 * R)),
    ],
    "1 new, 1 with a problem",
)
PREVIEWS[-1]["expect"]["rows"][0]["entry"] = None

# I5: a sale beyond what is held.
pcase(
    "I5",
    "a sale beyond the holding",
    "The tab holds 10 RELIANCE in Zerodha on 5 Jan; the file sells 12 on 12 Mar. The ledger "
    "refuses the sale with its own sentence, and the purchase of 3 in the same file that comes "
    "before it does not rescue it (10 + 3 = 13 would, so the purchase is dated after the sale). "
    "The purchase is still new.",
    HEAD + "2026-03-12,SELL,RELIANCE,,,12,1500,,,,,,\n2026-03-13,BUY,RELIANCE,,,3,1500,,,,,,\n",
    [
        row(
            2,
            "bad",
            ent("SELL", 20260312, 1, asset_id=1, qty_u=12 * S, price_u=1500 * R),
            "Row 2: Zerodha holds 10 RELIANCE on 12 Mar 2026, so a sale of 12 cannot be saved. "
            "Add the missing purchase first.",
        ),
        row(3, "new", ent("BUY", 20260313, 1, asset_id=1, qty_u=3 * S, price_u=1500 * R)),
    ],
    "1 new, 1 with a problem",
    existing=[exist(1, "BUY", 20260105, 1, 1, 10, price_u=1400 * R)],
)

# I5b: rows that would break an entry already in the tab.
pcase(
    "I5b",
    "rows that would leave an entry of the tab without shares",
    "The tab has a purchase of 10 on 5 Jan and a sale of 10 on 12 Mar. The file sells 3 on 1 Feb, "
    "which is fine on its own day but leaves the March sale 3 short. The rows of that holding "
    "cannot join: none of them is imported and each says why.",
    HEAD + "2026-02-01,SELL,RELIANCE,,,3,1450,,,,,,\n",
    [
        row(
            2,
            "bad",
            ent("SELL", 20260201, 1, asset_id=1, qty_u=3 * S, price_u=1450 * R),
            "Row 2: The rows for RELIANCE do not fit the entries already in this tab: they would "
            "leave the sale on 12 Mar 2026 without shares. None of them is imported. Check whether "
            "the same trades are already in this tab, or fix that entry first.",
        )
    ],
    "1 with a problem",
    existing=[
        exist(1, "BUY", 20260105, 1, 1, 10, price_u=1400 * R),
        exist(2, "SELL", 20260312, 1, 1, 10, price_u=1500 * R),
    ],
)

# I6: dates, day first.
pcase(
    "I6",
    "dates are day first",
    "05/01/2026 is 5 January; 06-01-2026 is 6 January; 2026-01-07 is 7 January. 03/13/2026 would "
    "be month first and is not a day; a day after 30 Oct 2026 is refused whatever its form.",
    HEAD
    + "05/01/2026,BUY,TCS,,,1,3000,,,,,,\n06-01-2026,BUY,TCS,,,1,3000,,,,,,\n"
    + "2026-01-07,BUY,TCS,,,1,3000,,,,,,\n03/13/2026,BUY,TCS,,,1,3000,,,,,,\n"
    + "31/10/2026,BUY,TCS,,,1,3000,,,,,,\n",
    [
        row(2, "new", ent("BUY", 20260105, 1, asset_id=2, qty_u=S, price_u=3000 * R)),
        row(3, "new", ent("BUY", 20260106, 1, asset_id=2, qty_u=S, price_u=3000 * R)),
        row(4, "new", ent("BUY", 20260107, 1, asset_id=2, qty_u=S, price_u=3000 * R)),
        row(
            5,
            "bad",
            None,
            "Row 5: The date '03/13/2026' is not a real date. "
            "Write it as 2026-03-10, 10-03-2026 or 10/03/2026 (day first).",
        ),
        row(
            6,
            "bad",
            None,
            "Row 6: 31 Oct 2026 is after today. A date after today can be saved as a draft only.",
        ),
    ],
    "3 new, 2 with problems",
)

# I8: amount against quantity times price.
pcase(
    "I8",
    "amount wins over quantity times price when they differ by more than a rupee",
    "Row 2: 5 x 3000.00 = 15,000.00 and the amount is 15,000.50: within a rupee, no remark. Row 3: "
    "amount 15,500.00 against 15,000.00: used, and the row says so. Row 4: amount only, no "
    "remark. The amount is in the entry; the price is kept too.",
    HEAD
    + "2026-01-05,BUY,TCS,,,5,3000,15000.50,,,,,\n2026-01-06,BUY,TCS,,,5,3000,15500,,,,,\n"
    + "2026-01-07,BUY,TCS,,,5,,15000,,,,,\n",
    [
        row(
            2,
            "new",
            ent(
                "BUY", 20260105, 1, asset_id=2, qty_u=5 * S, price_u=3000 * R, amount_paise=1500050
            ),
        ),
        row(
            3,
            "new",
            ent(
                "BUY", 20260106, 1, asset_id=2, qty_u=5 * S, price_u=3000 * R, amount_paise=1550000
            ),
            "Row 3: The amount "
            + RUPEE
            + "15,500.00 is used, not quantity times price ("
            + RUPEE
            + "15,000.00).",
        ),
        row(4, "new", ent("BUY", 20260107, 1, asset_id=2, qty_u=5 * S, amount_paise=1500000)),
    ],
    "3 new",
)

# I9: problems in words.
pcase(
    "I9",
    "rows with problems are told in words, the first thing wrong in each",
    "Cells are read left to right: the date, then the action, then the holding, quantity, price, "
    "amount, charges, account, TDS, ratio, note, to_account.",
    HEAD
    + "2026-01-05,HOLD,TCS,,,1,1,,,,,,\n2026-01-05,BUY,,,,1,1,,,,,,\n"
    + "2026-01-05,BUY,TCS,,,-1,1,,,,,,\n2026-01-05,BUY,TCS,,,1,1,1,,Nobody,,,\n"
    + "2026-01-05,BUY,TCS,,,1.5,1,,,,,,\n2026-01-05,BUY,TCS,,,1,1,,x,,,,\n"
    + "2026-01-05,BUY,TCS,,,1,1,,,,,,a,b\n2026-01-05,SPLIT,TCS,,,,,,,,,two,\n",
    [
        row(
            2,
            "bad",
            None,
            "Row 2: The action 'HOLD' is not one this file can use. Use BUY, SELL, "
            "DIVIDEND, INTEREST, FEE, SPLIT, BONUS, RIGHTS, TRANSFER_IN, TRANSFER_OUT or TRANSFER.",
        ),
        row(3, "bad", None, "Row 3: No share or fund is named. Fill in the symbol or the ISIN."),
        row(
            4,
            "bad",
            None,
            "Row 4: The quantity '-1' cannot be negative. "
            "The action says whether it is a purchase or a sale.",
        ),
        row(
            5,
            "bad",
            None,
            "Row 5: There is no account called 'Nobody' in this portfolio. "
            "Check the name, or add the account first.",
        ),
        row(
            6,
            "bad",
            ent("BUY", 20260105, 1, asset_id=2, qty_u=1500000, price_u=R),
            "Row 6: Shares are whole numbers.",
        ),
        row(
            7,
            "bad",
            None,
            "Row 7: The charges 'x' is not a number. "
            "Write it with digits and a decimal point only, without commas or a currency sign.",
        ),
        row(
            8,
            "bad",
            None,
            "Row 8: This row has more columns than the first row. "
            "A note with a comma needs quotes around it.",
        ),
        row(
            9,
            "bad",
            None,
            "Row 9: The ratio 'two' must be two whole numbers such as 5:1 "
            "(each share becomes 5) or 1:10 (one new for every ten old).",
        ),
    ],
    "8 with problems",
)
# a quantity that cannot be a share count is refused before it has a key
PREVIEWS[-1]["expect"]["rows"][4]["key"] = None


# ---------------------------------------------------------------- I7: export, then import
HEADER16 = (
    "date,action,symbol,exchange,isin,quantity,price,amount,charges,account,tds,ratio,"
    "note,to_account,lot_date,value"
).split(",")
REF = {
    "1": {"symbol": "RELIANCE", "exchange": "NSE", "isin": "INE000A00001"},
    "2": {"symbol": "TCS", "exchange": "NSE", "isin": "INE000A00002"},
}


def act(i, kind, day, account, asset=None, **f):
    out = {
        "id": i,
        "seq": i,
        "status": "posted",
        "kind": kind,
        "trade_date": day,
        "account_id": account,
    }
    if asset is not None:
        out["asset_id"] = asset
    out.update(f)
    return out


EXPORT_ROWS = [
    act(
        1,
        "BUY",
        20260105,
        1,
        1,
        qty_u=10 * S,
        price_u=1400 * R,
        amount_paise=1400000,
        charges_paise=1250,
        note='first lot, "good"',
    ),
    act(
        2,
        "SELL",
        20260302,
        1,
        1,
        qty_u=4 * S,
        price_u=1500 * R,
        amount_paise=600000,
        charges_paise=1000,
        note="=HYPERLINK(1)",
    ),
    act(3, "TRANSFER", 20260310, 1, 1, to_account_id=2, qty_u=3 * S),
    act(
        4,
        "TRANSFER",
        20260320,
        None,
        2,
        to_account_id=2,
        qty_u=5 * S,
        amount_paise=1500000,
        lot_date=20250601,
        value_paise=1600000,
    ),
    act(5, "TRANSFER", 20260325, 2, 2, qty_u=1 * S, value_paise=330000),
    act(6, "BONUS", 20260401, 2, 2, qty_u=5 * S),
    act(7, "SPLIT", 20260402, None, 2, ratio_num=2, ratio_den=1),
    act(8, "DIVIDEND", 20260415, 1, 1, amount_paise=8000, tds_paise=800),
    act(9, "FEE", 20260416, 1, None, amount_paise=2500, note="yearly"),
    {**act(10, "BUY", 20260101, 1, 1, qty_u=1 * S, price_u=1 * R), "status": "draft"},
]
EXPORT_LINES = [
    [
        "2026-01-05",
        "BUY",
        "RELIANCE",
        "NSE",
        "INE000A00001",
        "10",
        "1400.00",
        "14000.00",
        "12.50",
        "Zerodha",
        "",
        "",
        'first lot, "good"',
        "",
        "",
        "",
    ],
    [
        "2026-03-02",
        "SELL",
        "RELIANCE",
        "NSE",
        "INE000A00001",
        "4",
        "1500.00",
        "6000.00",
        "10.00",
        "Zerodha",
        "",
        "",
        "=HYPERLINK(1)",
        "",
        "",
        "",
    ],
    [
        "2026-03-10",
        "TRANSFER",
        "RELIANCE",
        "NSE",
        "INE000A00001",
        "3",
        "",
        "",
        "",
        "Zerodha",
        "",
        "",
        "",
        "Account B",
        "",
        "",
    ],
    [
        "2026-03-20",
        "TRANSFER_IN",
        "TCS",
        "NSE",
        "INE000A00002",
        "5",
        "",
        "15000.00",
        "",
        "Account B",
        "",
        "",
        "",
        "",
        "2025-06-01",
        "16000.00",
    ],
    [
        "2026-03-25",
        "TRANSFER_OUT",
        "TCS",
        "NSE",
        "INE000A00002",
        "1",
        "",
        "",
        "",
        "Account B",
        "",
        "",
        "",
        "",
        "",
        "3300.00",
    ],
    [
        "2026-04-01",
        "BONUS",
        "TCS",
        "NSE",
        "INE000A00002",
        "5",
        "",
        "",
        "",
        "Account B",
        "",
        "",
        "",
        "",
        "",
        "",
    ],
    [
        "2026-04-02",
        "SPLIT",
        "TCS",
        "NSE",
        "INE000A00002",
        "",
        "",
        "",
        "",
        "",
        "",
        "2:1",
        "",
        "",
        "",
        "",
    ],
    [
        "2026-04-15",
        "DIVIDEND",
        "RELIANCE",
        "NSE",
        "INE000A00001",
        "",
        "",
        "80.00",
        "",
        "Zerodha",
        "8.00",
        "",
        "",
        "",
        "",
        "",
    ],
    ["2026-04-16", "FEE", "", "", "", "", "", "25.00", "", "Zerodha", "", "", "yearly", "", "", ""],
]
EXPORT_TEXT = write_text(HEADER16, EXPORT_LINES)
assert EXPORT_TEXT.splitlines()[2].split(",")[12] == "'=HYPERLINK(1)"
# 'first lot, "good"' needs quotes because of the comma and the quotes
assert EXPORT_TEXT.splitlines()[1].count('""good""') == 1
EXPORT = {
    "id": "I7",
    "title": "export, then import into an empty tab",
    "why": (
        "Nine posted entries (the draft is not written) in the order the ledger applies them, one "
        "line each in the import format: money as plain decimals (14000.00), quantity as it is, "
        "an outside transfer as TRANSFER_IN or TRANSFER_OUT with its original purchase date and "
        "value, the split as 2:1, a note that starts with = guarded by an apostrophe and a note "
        "with a comma and quotes quoted. The file name is ASCII: the tab's id and the day. Read "
        "back into an empty tab, the file gives the same entries (the test also replays both)."
    ),
    "tab_id": 7,
    "today": TODAY,
    "accounts": {"1": "Zerodha", "2": "Account B"},
    "assets": REF,
    "rows": EXPORT_ROWS,
    "expect": {"filename": "SUDARSHAN_Portfolio_7_2026-10-30.csv", "text": EXPORT_TEXT},
}

IMPORT_GENERIC = {
    "about": (
        "Golden vectors for imports/generic.py and imports/preview.py (PA7): what importing a "
        "generic CSV would do to a tab (new, already here, with a problem, in words), the "
        "duplicate keys with their occurrence numbers, and the export that reads back as the same "
        "ledger (I7). Keys are worked out again in make_pa7.py (a hash of the trade and its "
        "occurrence). Entries are the contract's ActivityInput."
    ),
    "world": WORLD,
    "previews": PREVIEWS,
    "export": EXPORT,
}
LIMITS = {
    "about": "The size and row caps of P19 (revised): checked before the table is read.",
    "max_file_bytes": 900_000,
    "max_rows": 5_000,
    "too_big": BIG,
    "too_many_rows": "This file has more than 5,000 rows. Download it in shorter date ranges and import each part.",
}
IMPORT_GENERIC["limits"] = LIMITS


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"import_csv.json": IMPORT_CSV, "import_generic.json": IMPORT_GENERIC}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
