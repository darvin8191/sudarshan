"""Generator of the PA6 golden vectors: allocation.json and filters.json.

How a tab's value divides into bars (by class, sector, cap size, account, member and tag), what
class AMFI's section header gives a fund, how a filter is read, and which holding lines a filter
leaves, with the lots held over or under a year. Like the other generators it imports nothing from
sudarshan: every case carries its hand derivation, a small evaluator below works the same case
out again by a different road (groups built from scratch for each kind of bar and ordered by three
stable passes, the filters applied one kind at a time, the anniversary of a lot worked out again),
and the generator refuses to write a file in which what was typed by hand and what was computed
disagree. The inputs are the contract's JSON, so the phone can run the same file.

Run: python3 make_pa6.py
"""

import re
from pathlib import Path

from portfolio_vector_tools import pct4, rd, write_all

# ---------------------------------------------------------------- the owner's words, written again
SHAPE = "This filter cannot be read. Choose accounts, kinds of asset and tags from the lists."
ACCOUNTS = "Choose the accounts from the list."
CLASSES = "Choose the kinds of asset from the list."
TAGS = "A tag is 1 to 30 characters with no comma, and a filter takes at most 10 tags."
HELD = "Choose held over a year or held under a year."
DAY = "Enter a valid date."
CLASS_LABELS = {
    "equity": "Equity",
    "mf_equity": "MF equity",
    "mf_debt": "MF debt",
    "mf_hybrid": "MF hybrid",
    "mf_other": "MF other",
    "gold": "Gold and silver",
    "cash": "FD and cash",
    "bonds": "Bonds",
    "real_estate": "Real estate",
    "esop": "ESOP and unlisted",
    "crypto": "Crypto",
    "other": "Other",
}
CLASS_ORDER = list(CLASS_LABELS)
CAP_LABELS = {"large": "Large cap", "mid": "Mid cap", "small": "Small cap"}
KINDS_OF_BAR = ["class", "sector", "mcap", "account", "member", "tag"]


# ---------------------------------------------------------------- what AMFI's header says
def class_of_header(header):
    """The class of a fund: the words before the dash inside the outermost brackets."""
    found = re.search(r"\((.*)\)", header or "", re.S) if isinstance(header, str) else None
    if not found:
        return "mf_other"
    head = re.sub(r"\s+", " ", found.group(1).split("-")[0].strip().lower())
    if head == "equity scheme":
        return "mf_equity"
    if head == "debt scheme":
        return "mf_debt"
    if head == "hybrid scheme":
        return "mf_hybrid"
    return "mf_other"


MF_CLASSES = []


def case_mf(cid, title, why, header, want):
    got = class_of_header(header)
    assert got == want, (cid, got, want)
    MF_CLASSES.append({"id": cid, "title": title, "why": why, "header": header, "expect": want})


case_mf(
    "MF1",
    "An equity scheme is MF equity",
    "The words before the dash inside the brackets are the scheme's headline: Equity Scheme.",
    "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
    "mf_equity",
)
case_mf(
    "MF2",
    "A tax-saving fund is an equity scheme too",
    "Close ended or open ended makes no difference; the headline is Equity Scheme.",
    "Close Ended Schemes(Equity Scheme - ELSS)",
    "mf_equity",
)
case_mf(
    "MF3",
    "A debt scheme is MF debt",
    "The words before the dash inside the brackets are Debt Scheme, the headline of a debt fund.",
    "Open Ended Schemes(Debt Scheme - Liquid Fund)",
    "mf_debt",
)
case_mf(
    "MF4",
    "A hybrid scheme is MF hybrid",
    "The words before the dash inside the brackets are Hybrid Scheme, the headline of a hybrid fund.",
    "Open Ended Schemes(Hybrid Scheme - Aggressive Hybrid Fund)",
    "mf_hybrid",
)
case_mf(
    "MF5",
    "A solution oriented scheme is MF other",
    "The headline is Solution Oriented Scheme: not equity, debt or hybrid.",
    "Open Ended Schemes(Solution Oriented Scheme - Retirement Fund)",
    "mf_other",
)
case_mf(
    "MF6",
    "An index fund is MF other, whatever it follows",
    "The headline is Other Scheme. What the index holds is not in the header, so no guess is made; "
    "the owner can set the class of the fund.",
    "Open Ended Schemes(Other Scheme - Index Funds)",
    "mf_other",
)
case_mf(
    "MF7",
    "Case and spaces do not matter",
    "'equity   scheme' with extra spaces and capitals, and no space after the dash.",
    "open ended schemes( EQUITY   Scheme -Large Cap Fund )",
    "mf_equity",
)
case_mf(
    "MF8",
    "Brackets inside the brackets",
    "The headline is taken from the outermost brackets: Equity Scheme, with a bracket of its own "
    "further on.",
    "Open Ended Schemes(Equity Scheme - Sectoral/Thematic (Banking))",
    "mf_equity",
)
case_mf(
    "MF9",
    "A header without a dash is MF other",
    "Close Ended Schemes(Income): Income is not a headline we know.",
    "Close Ended Schemes(Income)",
    "mf_other",
)
case_mf(
    "MF10",
    "A header without brackets is MF other",
    "There are no brackets to read a headline from, so the fund is not put in a class it may not "
    "belong to.",
    "Mutual fund",
    "mf_other",
)
case_mf(
    "MF11",
    "An empty header is MF other",
    "A fund whose section header is not known (empty text) has no headline to read.",
    "",
    "mf_other",
)
case_mf(
    "MF12",
    "No header at all is MF other",
    "A fund with no header (null) has no headline to read either; this must not be an error.",
    None,
    "mf_other",
)
case_mf(
    "MF13",
    "A bracket that is never closed is MF other",
    "The headline is read from between the brackets; with no closing bracket there is nothing "
    "between them, and the words after the opening one are not taken for it.",
    "Open Ended Schemes(Equity Scheme - Large Cap Fund",
    "mf_other",
)
case_mf(
    "MF14",
    "Only the first dash ends the headline",
    "The headline is what comes before the first dash inside the brackets: Equity Scheme. A second "
    "dash further on (Large - Mid) is part of the fund's own description and changes nothing.",
    "Open Ended Schemes(Equity Scheme - Large - Mid Cap Fund)",
    "mf_equity",
)


# ---------------------------------------------------------------- holdings, worked out again
def asset(i, kind, sector=None, mcap=None, tags=()):
    return {"id": i, "asset_class": kind, "sector": sector, "mcap": mcap, "tags": list(tags)}


def line(account, qty, cost=0, opened=None, inn=0, out=0, over=0):
    return {
        "account_id": account,
        "qty_u": qty,
        "open_qty_u": qty if opened is None else opened,
        "cost_paise": cost,
        "in_today_paise": inn,
        "out_today_paise": out,
        "over_year_qty_u": over,
    }


def row(an_asset, price, *lines):
    """The parts of a HoldingRow that the bars read."""
    return {
        "asset": an_asset,
        "today_mode": "ok",
        "price": {"price_u": price, "prev_close_u": None},
        "lines": list(lines),
    }


def bar(key, label, value, pct, holdings):
    return {"key": key, "label": label, "value_paise": value, "pct": pct, "holdings": holdings}


def bars_of(kind, rows, accounts, members):
    """The bars of one kind: every held line's value added into the groups it belongs to, then
    ordered by label, by key and by value (largest first), each a stable pass."""
    groups, total = {}, 0
    for r in rows:
        a, price = r["asset"], r["price"]["price_u"]
        for ln in r["lines"]:
            if ln["qty_u"] == 0:
                continue
            value = ln["cost_paise"] if price is None else rd(ln["qty_u"] * price, 10**8)
            total += value
            if kind == "class":
                belongs = [(a["asset_class"], CLASS_LABELS[a["asset_class"]])]
            elif kind == "sector":
                belongs = [(a["sector"], a["sector"])] if a["sector"] else [("", "No sector set")]
            elif kind == "mcap":
                if a["mcap"]:
                    belongs = [(a["mcap"], CAP_LABELS[a["mcap"]])]
                else:
                    belongs = [("", "No cap size set")]
            elif kind == "account":
                belongs = [(str(ln["account_id"]), accounts[str(ln["account_id"])]["name"])]
            elif kind == "member":
                member = str(accounts[str(ln["account_id"])]["member_id"])
                belongs = [(member, members[member])]
            else:
                names = []
                for tag in a["tags"]:
                    if tag not in names:
                        names.append(tag)
                belongs = [(tag, tag) for tag in names] or [("", "No tag")]
            for key, label in belongs:
                group = groups.setdefault(key, {"label": label, "value": 0, "assets": []})
                group["value"] += value
                if a["id"] not in group["assets"]:
                    group["assets"].append(a["id"])
    out = [
        bar(
            key,
            g["label"],
            g["value"],
            pct4(g["value"], total) if total else "0.0000",
            len(g["assets"]),
        )
        for key, g in groups.items()
    ]
    out = sorted(out, key=lambda b: b["key"])
    out = sorted(out, key=lambda b: b["label"])
    return sorted(out, key=lambda b: -b["value_paise"])


def agrees(got, hand, where):
    """Every number typed by hand in a case is the evaluator's number too, or nothing is written."""
    for path, want in hand.items():
        node = got
        for key in path.split("."):
            node = node[int(key)] if isinstance(node, list) else node[key]
        assert node == want, (where, path, node, want)


ALLOCATIONS = []


def case_bars(cid, title, why, rows, accounts, members, **hand):
    got = {kind: bars_of(kind, rows, accounts, members) for kind in KINDS_OF_BAR}
    agrees(got, hand, cid)
    ALLOCATIONS.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "members": members,
            "accounts": accounts,
            "rows": rows,
            "expect": got,
        }
    )


R = 10**4  # one rupee as price_u
S = 10**6  # one unit
ME = {"1": "Me"}
DEMAT = {"1": {"name": "Demat A", "member_id": 1}, "2": {"name": "Fund folio", "member_id": 1}}

case_bars(
    "AL1",
    "Three equal parts: each share is its own rounding, so they need not add to 100",
    "Account 1 holds 10 shares at 100.00 (1,000.00 = 100,000 paise, sector IT, large cap, tag core) "
    "and 2 grams of gold at 500.00 (100,000 paise); account 2 holds 50 fund units at 20.0000 "
    "(100,000 paise). The tab is worth 300,000 paise and each part is a third: 33.3333, three "
    "times, which is 99.9999 and not 100 (each share is rounded alone). By class the three are "
    "level, so the order is by name: Equity, Gold and silver, MF equity. By sector the 200,000 "
    "paise with no sector comes first (66.6667, two holdings), then IT. Likewise cap size and tag. "
    "Account 1 holds two assets (200,000 paise, 66.6667), account 2 one; one member holds it all "
    "(100.0000, three holdings).",
    [
        row(asset(1, "equity", "IT", "large", ["core"]), 100 * R, line(1, 10 * S, 90_000)),
        row(asset(2, "mf_equity"), 20 * R, line(2, 50 * S, 90_000)),
        row(asset(3, "gold"), 500 * R, line(1, 2 * S, 90_000)),
    ],
    DEMAT,
    ME,
    **{
        "class.0.key": "equity",
        "class.0.label": "Equity",
        "class.0.value_paise": 100_000,
        "class.0.pct": "33.3333",
        "class.0.holdings": 1,
        "class.1.key": "gold",
        "class.1.label": "Gold and silver",
        "class.2.key": "mf_equity",
        "class.2.label": "MF equity",
        "sector.0.key": "",
        "sector.0.label": "No sector set",
        "sector.0.value_paise": 200_000,
        "sector.0.pct": "66.6667",
        "sector.0.holdings": 2,
        "sector.1.key": "IT",
        "sector.1.value_paise": 100_000,
        "sector.1.pct": "33.3333",
        "mcap.0.label": "No cap size set",
        "mcap.1.key": "large",
        "mcap.1.label": "Large cap",
        "account.0.key": "1",
        "account.0.label": "Demat A",
        "account.0.value_paise": 200_000,
        "account.0.holdings": 2,
        "account.1.key": "2",
        "account.1.value_paise": 100_000,
        "member.0.key": "1",
        "member.0.label": "Me",
        "member.0.value_paise": 300_000,
        "member.0.pct": "100.0000",
        "member.0.holdings": 3,
        "tag.0.key": "",
        "tag.0.label": "No tag",
        "tag.0.holdings": 2,
        "tag.1.key": "core",
        "tag.1.value_paise": 100_000,
    },
)
case_bars(
    "AL2",
    "A holding with several tags is in each tag's bar, with all its value",
    "6 shares at 100.00 tagged income and core (60,000 paise), 3 shares tagged core, written twice "
    "(30,000: a tag written twice is one), and 1 share with no tag (10,000). The tab is worth "
    "100,000. core is 60,000 + 30,000 = 90,000 (90.0000, two holdings), income 60,000 (60.0000), "
    "no tag 10,000 (10.0000): 160 per cent between them, because the first holding is in two bars. "
    "By class the same three are one bar of 100,000.",
    [
        row(asset(1, "equity", tags=["income", "core"]), 100 * R, line(1, 6 * S)),
        row(asset(2, "equity", tags=["core", "core"]), 100 * R, line(1, 3 * S)),
        row(asset(3, "equity"), 100 * R, line(1, 1 * S)),
    ],
    {"1": {"name": "Demat A", "member_id": 1}},
    ME,
    **{
        "tag.0.key": "core",
        "tag.0.value_paise": 90_000,
        "tag.0.pct": "90.0000",
        "tag.0.holdings": 2,
        "tag.1.key": "income",
        "tag.1.value_paise": 60_000,
        "tag.1.pct": "60.0000",
        "tag.1.holdings": 1,
        "tag.2.key": "",
        "tag.2.value_paise": 10_000,
        "tag.2.pct": "10.0000",
        "class.0.value_paise": 100_000,
        "class.0.pct": "100.0000",
        "class.0.holdings": 3,
    },
)
case_bars(
    "AL3",
    "The same share in three accounts of two members",
    "10 shares at 200.00 in one account of the second member (2,000.00 = 200,000 paise) and 5 and "
    "5 in two accounts of the first (100,000 each): 400,000 paise. One share, so one holding in "
    "the class bar and in each member's bar, and one in each account's. The second member's "
    "account is largest (50.0000); the other two are level, so Demat A comes before Demat B. The "
    "members are level at 200,000, so by name: Me before Member B.",
    [row(asset(1, "equity"), 200 * R, line(1, 5 * S), line(2, 5 * S), line(3, 10 * S))],
    {
        "1": {"name": "Demat A", "member_id": 1},
        "2": {"name": "Demat B", "member_id": 1},
        "3": {"name": "Demat C", "member_id": 2},
    },
    {"1": "Me", "2": "Member B"},
    **{
        "class.0.value_paise": 400_000,
        "class.0.holdings": 1,
        "account.0.key": "3",
        "account.0.value_paise": 200_000,
        "account.0.pct": "50.0000",
        "account.1.key": "1",
        "account.1.pct": "25.0000",
        "account.2.key": "2",
        "member.0.key": "1",
        "member.0.label": "Me",
        "member.0.value_paise": 200_000,
        "member.0.holdings": 1,
        "member.1.key": "2",
        "member.1.label": "Member B",
        "member.1.pct": "50.0000",
    },
)
case_bars(
    "AL4",
    "A holding with no price is counted at its cost",
    "10 shares at 100.00 are worth 100,000 paise; 5 units of something with no price yet cost "
    "50,000 and count at that, so it does not look as if it had been lost. The tab is 150,000: "
    "66.6667 and 33.3333.",
    [
        row(asset(1, "equity"), 100 * R, line(1, 10 * S, 80_000)),
        row(asset(2, "other"), None, line(1, 5 * S, 50_000)),
    ],
    {"1": {"name": "Demat A", "member_id": 1}},
    ME,
    **{
        "class.0.key": "equity",
        "class.0.value_paise": 100_000,
        "class.0.pct": "66.6667",
        "class.1.key": "other",
        "class.1.value_paise": 50_000,
        "class.1.pct": "33.3333",
        "class.1.holdings": 1,
    },
)
case_bars(
    "AL5",
    "A line sold out is not a holding",
    "10 shares at 100.00 held in account 1 and a line of the same share in account 2 that was sold "
    "out today (nothing held now); and a gold holding whose only line was sold out. Only the 100,000 "
    "paise in account 1 is there: no gold bar, no bar for account 2, one holding.",
    [
        row(
            asset(1, "equity"),
            100 * R,
            line(1, 10 * S, 80_000),
            line(2, 0, 0, opened=10 * S, out=105_000),
        ),
        row(asset(3, "gold"), 500 * R, line(1, 0, 0, opened=2 * S, out=100_000)),
    ],
    DEMAT,
    ME,
    **{
        "class.0.key": "equity",
        "class.0.holdings": 1,
        "account.0.key": "1",
        "account.0.pct": "100.0000",
    },
)
case_bars(
    "AL6",
    "Each line is rounded on its own, as the holdings list is",
    "Two accounts each hold 0.005 unit of a fund at 1.0000: half a paisa each, which rounds half "
    "away from zero to 1 paisa. The class bar is 1 + 1 = 2 paise (rounding the whole holding "
    "would say 1). Each account is 1 of 2: 50.0000.",
    [row(asset(1, "mf_other"), 1 * R, line(1, 5_000), line(2, 5_000))],
    DEMAT,
    ME,
    **{
        "class.0.value_paise": 2,
        "class.0.pct": "100.0000",
        "account.0.key": "1",
        "account.0.value_paise": 1,
        "account.0.pct": "50.0000",
        "account.1.key": "2",
        "account.1.value_paise": 1,
    },
)
case_bars(
    "AL7",
    "Nothing held: no bars of any kind",
    "Every line is sold out. No bar, whatever the kind.",
    [row(asset(1, "equity"), 100 * R, line(1, 0, 0, opened=10 * S, out=100_000))],
    DEMAT,
    ME,
    **{kind: [] for kind in KINDS_OF_BAR},
)
case_bars(
    "AL8",
    "A tab worth nothing: the bar is there and its share is 0.0000",
    "One millionth of a unit at 1.0000 is worth 0.0001 of a paisa, which rounds to nothing. The "
    "tab is worth 0 paise, so there is nothing to divide by: the share is 0.0000 and the holding "
    "is still counted.",
    [row(asset(1, "equity"), 1 * R, line(1, 1))],
    {"1": {"name": "Demat A", "member_id": 1}},
    ME,
    **{
        "class.0.value_paise": 0,
        "class.0.pct": "0.0000",
        "class.0.holdings": 1,
        "member.0.pct": "0.0000",
    },
)
case_bars(
    "AL9",
    "A share exactly between two numbers goes away from zero",
    "1 unit at 0.01 is 1 paisa and 127 units at 0.01 are 127 paise: the tab is 128 paise. 1 / 128 "
    "is 0.78125 per cent, exactly between 0.7812 and 0.7813, which rounds to 0.7813; 127 / 128 is "
    "99.21875 per cent, which rounds to 99.2188 (half to even would say 0.7812).",
    [row(asset(1, "equity"), 100, line(1, 1 * S)), row(asset(2, "gold"), 100, line(1, 127 * S))],
    {"1": {"name": "Demat A", "member_id": 1}},
    ME,
    **{
        "class.0.key": "gold",
        "class.0.value_paise": 127,
        "class.0.pct": "99.2188",
        "class.1.key": "equity",
        "class.1.value_paise": 1,
        "class.1.pct": "0.7813",
    },
)
case_bars(
    "AL10",
    "Bars of equal value are ordered by name and then key, not by arrival",
    "Banks (500.00) arrives before Auto (500.00): level, so Auto comes first. The two accounts are "
    "both called Demat and hold the same value, so the order falls to the key as text: '10' comes "
    "before '2'.",
    [
        row(asset(1, "equity", "Banks"), 100 * R, line(2, 5 * S)),
        row(asset(2, "equity", "Auto"), 100 * R, line(10, 5 * S)),
    ],
    {"2": {"name": "Demat", "member_id": 1}, "10": {"name": "Demat", "member_id": 1}},
    ME,
    **{
        "sector.0.key": "Auto",
        "sector.0.pct": "50.0000",
        "sector.1.key": "Banks",
        "account.0.key": "10",
        "account.1.key": "2",
    },
)
case_bars(
    "AL11",
    "The three cap sizes and the holdings with none",
    "4, 3, 2 and 1 shares at 100.00 (40,000, 30,000, 20,000 and 10,000 paise) of large, mid, small "
    "and no cap size: 40.0000, 30.0000, 20.0000 and 10.0000 per cent, in that order. The sectors "
    "come out level: A is the first and the fourth share (40,000 + 10,000), B the second and the "
    "third (30,000 + 20,000), 50,000 paise each, so by name A comes first.",
    [
        row(asset(1, "equity", "A", "large"), 100 * R, line(1, 4 * S)),
        row(asset(2, "equity", "B", "mid"), 100 * R, line(1, 3 * S)),
        row(asset(3, "equity", "B", "small"), 100 * R, line(1, 2 * S)),
        row(asset(4, "equity", "A"), 100 * R, line(1, 1 * S)),
    ],
    {"1": {"name": "Demat A", "member_id": 1}},
    ME,
    **{
        "mcap.0.key": "large",
        "mcap.0.label": "Large cap",
        "mcap.0.pct": "40.0000",
        "mcap.1.key": "mid",
        "mcap.1.label": "Mid cap",
        "mcap.1.pct": "30.0000",
        "mcap.2.key": "small",
        "mcap.2.label": "Small cap",
        "mcap.2.pct": "20.0000",
        "mcap.3.key": "",
        "mcap.3.label": "No cap size set",
        "mcap.3.pct": "10.0000",
        "sector.0.key": "A",
        "sector.0.value_paise": 50_000,
        "sector.0.holdings": 2,
        "sector.1.key": "B",
        "sector.1.value_paise": 50_000,
        "sector.1.holdings": 2,
    },
)
case_bars(
    "AL12",
    "Bars of equal value are ordered by what they are called, not by their key",
    "Two holdings of 500.00 (50,000 paise each). Cash is held in the account called Zeta (key 1, "
    "member key 1 called Member B) and crypto in the account called Alpha (key 2, member key 2 "
    "called Me). Every bar is level, so each kind goes by name: Crypto before FD and cash although "
    "the key cash comes before crypto; Alpha before Zeta although key 1 comes before key 2; Me "
    "before Member B although key 1 comes before key 2. Sector, cap size and tag are one bar "
    "of both holdings each.",
    [
        row(asset(1, "cash"), 100 * R, line(1, 5 * S)),
        row(asset(2, "crypto"), 100 * R, line(2, 5 * S)),
    ],
    {"1": {"name": "Zeta", "member_id": 1}, "2": {"name": "Alpha", "member_id": 2}},
    {"1": "Member B", "2": "Me"},
    **{
        "class.0.key": "crypto",
        "class.0.label": "Crypto",
        "class.1.key": "cash",
        "class.1.label": "FD and cash",
        "account.0.key": "2",
        "account.0.label": "Alpha",
        "account.1.key": "1",
        "account.1.label": "Zeta",
        "member.0.key": "2",
        "member.0.label": "Me",
        "member.1.key": "1",
        "member.1.label": "Member B",
        "sector.0.holdings": 2,
        "sector.0.pct": "100.0000",
    },
)


# ---------------------------------------------------------------- filters, read
PARSES = []


def case_parse(cid, title, why, raw, want):
    """``want`` is the filter read, or the sentence the reader refuses with."""
    PARSES.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "raw": raw,
            "expect": {"error": want} if isinstance(want, str) else want,
        }
    )


def filters(accounts=(), classes=(), tags=(), held=None, as_of=None):
    return {
        "accounts": list(accounts),
        "classes": list(classes),
        "tags": list(tags),
        "held": held,
        "as_of": as_of,
    }


case_parse(
    "FP1",
    "A filter is read into one order whoever wrote it",
    "Accounts 3, 1, 3 are the accounts 1 and 3, once each and lowest first. Classes are in the "
    "order of the kinds of asset (equity before gold), once each. Tags lose the spaces round them, "
    "are once each and sorted. held and as_of stay as they were written.",
    {
        "accounts": [3, 1, 3],
        "classes": ["gold", "equity", "gold"],
        "tags": [" income ", "core", "core"],
        "held": "over",
        "as_of": 20261027,
    },
    filters([1, 3], ["equity", "gold"], ["core", "income"], "over", 20261027),
)
case_parse(
    "FP2",
    "Nothing chosen is nothing",
    "A filter with no key at all chooses nothing, and so shows everything: no account, no kind of "
    "asset, no tag, no held, no day.",
    {},
    filters(),
)
case_parse(
    "FP3",
    "A key it does not know is dropped and a null is nothing",
    "A saved view carries a sort and a number of its own, and a null where nothing was chosen. "
    "Those are not part of the filter: it comes out as the empty filter.",
    {"accounts": None, "tags": None, "view": 7, "sort": "value", "held": None},
    filters(),
)
case_parse(
    "FP4",
    "Ten tags of up to thirty characters are the most",
    "Nine short tags and one of exactly 30 characters, which is the longest a tag may be: all ten "
    "are kept and sorted as text (t0 to t8, then the long one, because x comes after t).",
    {"tags": ["x" * 30, *[f"t{n}" for n in range(9)]]},
    filters(tags=[*[f"t{n}" for n in range(9)], "x" * 30]),
)
case_parse(
    "FP5",
    "A tag is exact: capitals count and spaces inside stay",
    "Core and core are two tags, and the sort puts the capital first (code point order). The "
    "space inside 'high growth' stays; the ones round it go. Written twice, it is one.",
    {"tags": ["core", " Core", "high growth ", "high growth"]},
    filters(tags=["Core", "core", "high growth"]),
)
case_parse(
    "FP6",
    "Accounts are sorted as numbers, whatever order a set would give them",
    "8, 2, 16 and 1 come out as 1, 2, 8, 16. A set of these numbers does not come back in this "
    "order (its order follows how the numbers hash), so the result must be sorted on purpose.",
    {"accounts": [8, 2, 16, 1]},
    filters([1, 2, 8, 16]),
)
case_parse(
    "FE1",
    "An account is a number above nothing",
    "0 is no account: the numbers are those the store gave, from 1.",
    {"accounts": [0]},
    ACCOUNTS,
)
case_parse(
    "FE2",
    "An account is not text",
    "'1' is text and not an account number: the phone sends numbers.",
    {"accounts": ["1"]},
    ACCOUNTS,
)
case_parse(
    "FE3",
    "An account is not true or false",
    "True is 1 to a program but it is not an account: a true or false is never taken for a number.",
    {"accounts": [True]},
    ACCOUNTS,
)
case_parse(
    "FE4",
    "A kind of asset the tab does not have",
    "'shares' is not on the list of kinds (equity, MF equity, gold and so on): the owner chooses "
    "kinds from a list, so this is a view saved by something else.",
    {"classes": ["shares"]},
    CLASSES,
)
case_parse(
    "FE5",
    "A list that is not a list",
    "'equity' where a list of kinds was meant is not read as one kind: it could be one letter at "
    "a time. The filter as a whole is the thing that cannot be read.",
    {"classes": "equity"},
    SHAPE,
)
case_parse(
    "FE6",
    "A tag with a comma",
    "A tab is asked for with its tags in one list separated by commas, so a tag with a comma in it "
    "could never be asked for again. It is refused when it is chosen.",
    {"tags": ["a,b"]},
    TAGS,
)
case_parse(
    "FE7",
    "A tag of 31 characters",
    "One more than the longest a tag may be (30).",
    {"tags": ["x" * 31]},
    TAGS,
)
case_parse(
    "FE8",
    "Eleven tags",
    "Eleven different tags: one more than a filter takes (10).",
    {"tags": [f"t{n}" for n in range(11)]},
    TAGS,
)
case_parse(
    "FE9",
    "A blank tag",
    "Nothing but spaces is no tag at all: after the spaces go, it is empty.",
    {"tags": ["  "]},
    TAGS,
)
case_parse(
    "FE10",
    "A tag that is a number",
    "5 is not text, so it is not a tag and the list cannot be read.",
    {"tags": [5]},
    SHAPE,
)
case_parse(
    "FE11",
    "Held for neither over nor under",
    "'sideways' is neither held over a year nor held under a year.",
    {"held": "sideways"},
    HELD,
)
case_parse(
    "FE12",
    "A day that is not a day",
    "20261331 has a thirty-first month: it is not a date.",
    {"as_of": 20261331},
    DAY,
)
case_parse(
    "FE13",
    "A day written as text",
    "'20261027' is text: a day is a whole number written year, month, day.",
    {"as_of": "20261027"},
    DAY,
)
case_parse(
    "FE14",
    "Something that is not a filter",
    "A list where an object was meant: it has no keys to read.",
    [1, 2],
    SHAPE,
)
case_parse(
    "FE15",
    "Nothing at all",
    "null where an object was meant is not an empty filter: a filter is always sent, so a missing "
    "one is a fault of the sender.",
    None,
    SHAPE,
)
case_parse(
    "FE16",
    "The first thing wrong is the one said",
    "The accounts are wrong (0) and the classes are not even a list. The accounts are read first, "
    "so it is the accounts that are named; the trader mends one thing and is told the next.",
    {"accounts": [0], "classes": "equity"},
    ACCOUNTS,
)


# ---------------------------------------------------------------- filters, applied
TODAY = 20261027
SELECTIONS = []

FACTS = {
    1: asset(1, "equity", tags=["core"]),
    2: asset(2, "equity", tags=["income"]),
    3: asset(3, "gold"),
    4: asset(4, "mf_equity", tags=["core", "income"]),
}
# (account, asset) -> lots [day, qty_u, cost_paise], oldest first.
LOTS = {
    (1, 1): [
        [20240229, 4 * S, 400_000],
        [20251026, 3 * S, 330_000],
        [20251027, 2 * S, 240_000],
        [20251101, 1 * S, 130_000],
    ],
    (2, 1): [[20250105, 5 * S, 500_000]],
    (1, 2): [[20261027, 20 * S, 2_000_000]],
    (2, 3): [[20260101, 3 * S, 90_000]],
    (1, 4): [[20250301, 60 * S, 600_000], [20260301, 40 * S, 400_000]],
    (2, 4): [[20240102, 50 * S, 500_000]],
}


def whole_line(account, an_asset):
    """A position's line as the screen has it before any filter: all its lots, some of the money
    of today in it so that a line left alone can be seen to be left alone."""
    lots = LOTS[(account, an_asset)]
    qty = sum(lot[1] for lot in lots)
    cost = sum(lot[2] for lot in lots)
    bought_today = sum(lot[1] for lot in lots if lot[0] == TODAY)
    inn = sum(lot[2] for lot in lots if lot[0] == TODAY)
    return line(account, qty, cost, opened=qty - bought_today, inn=inn)


def lines_by_asset():
    out = {}
    for account, an_asset in sorted(LOTS, key=lambda key: (key[1], key[0])):
        out.setdefault(an_asset, []).append(whole_line(account, an_asset))
    return out


def anniversary(day):
    """The same date a year on; 29 February becomes 28 February."""
    year, month, number = day // 10000 + 1, day // 100 % 100, day % 100
    if month == 2 and number == 29 and not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        number = 28
    return year * 10000 + month * 100 + number


def selected(raw, lines, lots, facts, today):
    """The lines that pass, one kind of choice at a time."""
    f = {
        "accounts": raw.get("accounts") or [],
        "classes": raw.get("classes") or [],
        "tags": raw.get("tags") or [],
        "held": raw.get("held"),
        "as_of": raw.get("as_of"),
    }
    on = f["as_of"] if f["as_of"] is not None else today
    out = {}
    for an_asset, found in lines.items():
        asset_facts = facts[an_asset]
        if f["classes"] and asset_facts["asset_class"] not in f["classes"]:
            continue
        if f["tags"] and not [t for t in f["tags"] if t in asset_facts["tags"]]:
            continue
        keep = [ln for ln in found if not f["accounts"] or ln["account_id"] in f["accounts"]]
        if f["held"]:
            narrowed = []
            for ln in keep:
                mine = [
                    lot
                    for lot in lots.get((ln["account_id"], an_asset), [])
                    if (on > anniversary(lot[0])) == (f["held"] == "over")
                ]
                qty = sum(lot[1] for lot in mine)
                if qty:
                    cost = sum(lot[2] for lot in mine)
                    over = qty if f["held"] == "over" else 0
                    narrowed.append(line(ln["account_id"], qty, cost, over=over))
            keep = narrowed
        if keep:
            out[an_asset] = keep
    return {"lines": out, "today_off": bool(f["held"]) or f["as_of"] is not None}


def case_select(cid, title, why, raw, expect_lines, today_off, lots=None, today=TODAY, lines=None):
    lots = LOTS if lots is None else lots
    lines = lines_by_asset() if lines is None else lines
    got = selected(raw, lines, lots, FACTS, today)
    want = {"lines": expect_lines, "today_off": today_off}
    assert got == want, (cid, got, want)
    SELECTIONS.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "today": today,
            "filters": raw,
            "facts": {str(k): v for k, v in FACTS.items()},
            "lines": {str(k): v for k, v in lines.items()},
            "positions": {f"{a}/{s}": v for (a, s), v in lots.items()},
            "expect": {
                "lines": {str(k): v for k, v in expect_lines.items()},
                "today_off": today_off,
            },
        }
    )


ALL = lines_by_asset()
WORLD = (
    "The world of these cases is Tuesday 27 Oct 2026 and six lines. Asset 1 (equity, core) is held "
    "in account 1 in four lots and in account 2 in one; asset 2 (equity, income) in account 1 in "
    "one lot bought today; asset 3 (gold) in account 2; asset 4 (MF equity, core and income) in "
    "both. "
)
case_select(
    "FS1",
    "No filter leaves every line as it is",
    WORLD + "Nothing chosen: all six lines, untouched, and Today still means something.",
    {},
    ALL,
    False,
)
case_select(
    "FS2",
    "An account keeps the lines of that account",
    WORLD + "Account 2: its lines of assets 1, 3 and 4. Asset 2 had no line there and is gone.",
    {"accounts": [2]},
    {1: [ALL[1][1]], 3: [ALL[3][0]], 4: [ALL[4][1]]},
    False,
)
case_select(
    "FS3",
    "Kinds of asset are alternatives",
    WORLD + "Gold or MF equity: assets 3 and 4, every account.",
    {"classes": ["gold", "mf_equity"]},
    {3: ALL[3], 4: ALL[4]},
    False,
)
case_select(
    "FS4",
    "Tags are alternatives too",
    WORLD + "income or core: assets 1 (core), 2 (income) and 4 (both). Gold has no tag.",
    {"tags": ["core", "income"]},
    {1: ALL[1], 2: ALL[2], 4: ALL[4]},
    False,
)
case_select(
    "FS5",
    "Different kinds of choice all have to hold",
    WORLD + "Equity and tagged core: asset 1 only. Asset 2 is equity but not core; asset 4 is core "
    "but not equity.",
    {"classes": ["equity"], "tags": ["core"]},
    {1: ALL[1]},
    False,
)
case_select(
    "FS6",
    "Account, kind and tag together",
    WORLD + "Account 1, equity, core: the one line of asset 1 in account 1.",
    {"accounts": [1], "classes": ["equity"], "tags": ["core"]},
    {1: [ALL[1][0]]},
    False,
)
case_select(
    "FS7",
    "Held over a year keeps the lots bought more than a year ago, with their cost",
    WORLD + "Over a year on 27 Oct 2026 means bought before 27 Oct 2025. Account 1's asset 1: the "
    "lot of 29 Feb 2024 (4 units, cost 4,000.00, anniversary 28 Feb 2025) and the lot of 26 Oct "
    "2025 (3 units, 3,300.00, anniversary 26 Oct 2026) are over; the lot of 27 Oct 2025 is "
    "exactly a year old today, which is not more than a year, and the lot of 1 Nov 2025 is under. "
    "So 7 units at a cost of 730,000 paise. Account 2's lot of 5 Jan 2025 is over. Asset 2 was "
    "bought today and asset 3 on 1 Jan 2026: nothing over. Asset 4: the lot of 1 Mar 2025 "
    "(60 units, 6,000.00) and account 2's lot of 2 Jan 2024 (50 units, 5,000.00). Today is off.",
    {"held": "over"},
    {
        1: [line(1, 7 * S, 730_000, over=7 * S), line(2, 5 * S, 500_000, over=5 * S)],
        4: [line(1, 60 * S, 600_000, over=60 * S), line(2, 50 * S, 500_000, over=50 * S)],
    },
    True,
)
case_select(
    "FS8",
    "Held under a year keeps the rest",
    WORLD + "The lots left by FS7: account 1's asset 1 has the lots of 27 Oct 2025 and 1 Nov 2025 "
    "(2 + 1 units, cost 2,400.00 + 1,300.00 = 3,700.00 = 370,000 paise); asset 2's lot bought "
    "today (20 units, 20,000.00); asset 3's lot (3 units, 900.00); asset 4's lot of 1 Mar 2026 "
    "(40 units, 4,000.00). Account 2 has nothing under in assets 1 and 4. No lot is counted in "
    "both views: the units of FS7 and FS8 together are the whole line.",
    {"held": "under"},
    {
        1: [line(1, 3 * S, 370_000)],
        2: [line(1, 20 * S, 2_000_000)],
        3: [line(2, 3 * S, 90_000)],
        4: [line(1, 40 * S, 400_000)],
    },
    True,
)
case_select(
    "FS9",
    "Held and the other choices together",
    WORLD + "Over a year, account 2, tagged core: asset 1 (account 2: 5 units, 5,000.00) and "
    "asset 4 (account 2: 50 units, 5,000.00). Gold is not tagged.",
    {"held": "over", "accounts": [2], "tags": ["core"]},
    {1: [line(2, 5 * S, 500_000, over=5 * S)], 4: [line(2, 50 * S, 500_000, over=50 * S)]},
    True,
)
LEAP = {(1, 1): [[20240229, 4 * S, 400_000], [20250102, 2 * S, 200_000]]}
LEAP_LINES = {1: [line(1, 6 * S, 600_000)]}
case_select(
    "FS10",
    "The leap day: the first anniversary is not yet more than a year",
    "A lot bought on 29 Feb 2024 has its anniversary on 28 Feb 2025. As at 28 Feb 2025 the lot "
    "has been held exactly a year, so nothing is over and all 6 units (cost 6,000.00) are under; "
    "the other lot is bought on 2 Jan 2025.",
    {"held": "under", "as_of": 20250228},
    {1: [line(1, 6 * S, 600_000)]},
    True,
    lots=LEAP,
    today=20250228,
    lines=LEAP_LINES,
)
case_select(
    "FS11",
    "The day after, the lot is over a year",
    "As FS10 on 1 Mar 2025: the lot of 29 Feb 2024 (4 units, 4,000.00) is over; the other (2 "
    "units, 2,000.00) is under. Asked over, the first.",
    {"held": "over", "as_of": 20250301},
    {1: [line(1, 4 * S, 400_000, over=4 * S)]},
    True,
    lots=LEAP,
    today=20250301,
    lines=LEAP_LINES,
)
case_select(
    "FS12",
    "The lots are judged on the day the filter gives, not on today",
    "As FS11, but the day passed in as today is 27 Oct 2026: the filter says 1 Mar 2025 and that "
    "is the day the lots are judged on. Asked under, the lot of 2 Jan 2025 only (2 units, "
    "2,000.00).",
    {"held": "under", "as_of": 20250301},
    {1: [line(1, 2 * S, 200_000)]},
    True,
    lots=LEAP,
    today=TODAY,
    lines=LEAP_LINES,
)
case_select(
    "FS13",
    "A day on its own changes no line, and switches Today off",
    WORLD + "As at a day gone by: the caller replays the entries up to it and prices the lines "
    "then, so the lines here are the ones given. Nothing is dropped; Today is off.",
    {"as_of": 20260630},
    ALL,
    True,
)
case_select(
    "FS14",
    "A line with no lots is not there when lots are asked for",
    "A position the replay does not have (or one whose lots are all gone) cannot be over or under "
    "a year: its line is dropped.",
    {"held": "over"},
    {},
    True,
    lots={},
    lines={1: [line(1, 5 * S, 500_000)]},
)
case_select(
    "FS15",
    "Lines keep their order",
    "Asset 4's lines are given account 2 first; they stay in that order.",
    {"classes": ["mf_equity"]},
    {4: [ALL[4][1], ALL[4][0]]},
    False,
    lines={4: [ALL[4][1], ALL[4][0]]},
)


ALLOCATION = {
    "about": (
        "Golden vectors for core/allocation.py (PA6): the class AMFI's section header gives a "
        "fund, and the bars of a tab's value by class, sector, cap size, account, member and tag. "
        "Inputs are the contract's JSON (a HoldingRow's asset, price and lines, the accounts and "
        "the members), so the phone can run the same file. Every number is worked out by hand in "
        "the case's why; the evaluator in make_pa6.py builds the groups again from scratch."
    ),
    "mf_classes": MF_CLASSES,
    "allocations": ALLOCATIONS,
}
FILTERS = {
    "about": (
        "Golden vectors for core/filters.py (PA6): how a filter is read (and refused), and which "
        "holding lines a filter leaves, with the lots held over or under a year. Positions are "
        "given as lots [day, qty_u, cost_paise]; a case's world is described in its why."
    ),
    "parse": PARSES,
    "select": SELECTIONS,
}


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"allocation.json": ALLOCATION, "filters.json": FILTERS}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
