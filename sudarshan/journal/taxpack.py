"""The tax working paper as the Tax pack prints it (P7 J5, M15.5; design p7 4.9).

The portfolio computes the working paper with ``sudarshan/tax`` and hands it over as one plain
answer, the pack (``PortfolioLink.tax_pack``). This module only lays it out: blocks for the PDF
(``pack_blocks``) and a few lines for the report's words (``pack_lines``). **It works out no
figure.** Every amount printed is a field of the pack, shown as it came; no total is added up
here, no gain is classified, no rate applied. A rule change is a change to ``sudarshan/tax``.

Nothing here names a section of any Act. Every list the pack keeps out of its totals (a sale
with no purchase record, one that needs the price of 31 January 2018, an exempt buyback, a
position still open) is printed under its own heading, never as zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .core import words
from .core.money import ist_clock, ist_day
from .render import doc as D
from .render.tables import day_text

QTY_SCALE = 1_000_000
PRICE_SCALE = 10_000
TERMS = {"short": "Short-term", "long": "Long-term"}
#: The same, short, for the narrow last column of the sales table.
HELD = {"short": "Short", "long": "Long"}
BUCKETS = {
    "stcl": "Short-term capital loss",
    "ltcl": "Long-term capital loss",
    "business": "F&O (business) loss",
    "speculative": "Intraday (speculative) loss",
}
SEGMENTS = {"EQ": "Shares", "FO": "Futures and options", "COM": "Commodity", "CD": "Currency",
            "MF": "Funds"}  # fmt: skip
CHARGE_COLUMNS = (
    ("total_paise", "Total"),
    ("brokerage_paise", "Brokerage"),
    ("stt_paise", "STT"),
    ("gst_paise", "GST"),
    ("stamp_duty_paise", "Stamp"),
)
STATUS_WORDS = {
    "needs_fmv": "Needs the price of 31 Jan 2018",
    "unknown_cost": "No purchase record",
    "exempt_buyback": "Buyback, exempt in your hands",
}
#: Why a pack is not in the paper, in the owner's words, by ``PortfolioUnavailable.kind``.
NO_PACK = {
    "switched_off": "The tax working paper is switched off on the server, so this pack holds "
    "only what the journal recorded of your trading.",
    "not_ready": "The tax working paper is switched off on the server, so this pack holds only "
    "what the journal recorded of your trading.",
    "unreadable": "The portfolio service answered with a working paper this release cannot "
    "read, so it is left out. Build the pack again after the next update.",
}
NO_PACK_DEFAULT = (
    "The tax working paper could not be read from the portfolio service just now, so this pack "
    "holds only what the journal recorded of your trading. Build it again from Reports."
)
PACK_KEYS = ("fy", "as_of_ms", "rules_version", "capital_gains", "business", "charges")


def usable(pack: Any) -> bool:
    """Whether an answer is a pack this module can print."""
    return (
        isinstance(pack, Mapping)
        and all(k in pack for k in PACK_KEYS)
        and isinstance(pack["capital_gains"], Mapping)
        and isinstance(pack["business"], Mapping)
    )


def no_pack_words(kind: str | None) -> str:
    return NO_PACK.get(kind or "", NO_PACK_DEFAULT)


def as_of_label(pack: Mapping[str, Any]) -> str:
    at = int(pack["as_of_ms"])
    h, m, _ = ist_clock(at)
    return f"{words.day_label_year(ist_day(at))}, {h:02d}:{m:02d}"


def built_from(pack: Mapping[str, Any]) -> str:
    """The archive's line for a pack built on the working paper."""
    return f"Built from the tax working paper as of {as_of_label(pack)}."


def fy_text(fy: int) -> str:
    return f"FY {fy}-{(fy + 1) % 100:02d}"


def rate_text(rate_bp: int) -> str:
    whole, part = divmod(int(rate_bp), 100)
    return f"{whole}.{part:02d}".rstrip("0").rstrip(".") + "%"


def qty_text(qty_u: int) -> str:
    whole, part = divmod(abs(int(qty_u)), QTY_SCALE)
    text = words.group_en_in(str(whole))
    return f"{text}.{part:06d}".rstrip("0") if part else text


def rs(paise: Any) -> str:
    return "not given" if paise is None else words.rupees(int(paise))


def _name(instrument: Any) -> str:
    """An instrument as the pack names it: an ISIN, or a contract's parts in a row."""
    if isinstance(instrument, list | tuple):
        return " ".join(str(part) for part in instrument)
    return str(instrument or "")


def _amounts(rows: Sequence[tuple[str, D.Cell]]) -> D.Table:
    return D.Table((D.Column("", 4.2), D.Column("Amount", 2.0, "r")), tuple(rows))


def _carried(rows: Sequence[Mapping[str, Any]]) -> tuple[D.Block, ...]:
    if not rows:
        return ()
    body = tuple(
        (BUCKETS.get(r["bucket"], str(r["bucket"])), fy_text(r["loss_fy"]), rs(r["paise"]),
         fy_text(r["last_fy"]) if r.get("last_fy") is not None else "-")
        for r in rows
    )  # fmt: skip
    return (
        D.Table(
            (D.Column("Carried forward", 3.2), D.Column("Year of loss", 1.6),
             D.Column("Amount", 1.8, "r"), D.Column("Usable to", 1.6)),
            body,
        ),
    )  # fmt: skip


def _lines(lines: Sequence[str] | None) -> tuple[D.Block, ...]:
    kept = tuple(str(line) for line in lines or () if line)
    return (D.Paragraph(kept),) if kept else ()


def _sale_row(g: Mapping[str, Any], last: D.Cell) -> tuple[D.Cell, ...]:
    bought = g.get("acquired_day")
    return (
        str(g.get("isin") or ""),
        qty_text(g.get("qty_u") or 0),
        day_text(bought) if bought else "-",
        day_text(g["sale_day"]),
        rs(g.get("cost_paise")),
        rs(g.get("sale_paise")),
        last,
    )


SALE_COLUMNS = (
    D.Column("ISIN", 1.8),
    D.Column("Qty", 1.2, "r"),
    D.Column("Bought", 1.7),
    D.Column("Sold", 1.7),
    D.Column("Cost", 1.7, "r"),
    D.Column("Sale value", 1.7, "r"),
)
FMV_MARK = "* The highest price of 31 January 2018 was used as the cost of this sale."


def capital_blocks(cg: Mapping[str, Any]) -> tuple[D.Block, ...]:
    tax, taxable, exempt = cg["indicative_tax"], cg["taxable"], cg["exemption"]
    rows: list[tuple[str, D.Cell]] = [
        ("Short-term gains", D.Money(cg["st_gain_paise"])),
        ("Short-term losses", D.Money(-int(cg["st_loss_paise"]))),
        ("Long-term gains", D.Money(cg["lt_gain_paise"])),
        ("Long-term losses", D.Money(-int(cg["lt_loss_paise"]))),
        ("Yearly exemption on long-term gains", rs(exempt["limit_paise"])),
        ("Exemption used", rs(exempt["used_paise"])),
        ("Taxable short-term gain", rs(taxable["st_paise"])),
        ("Taxable long-term gain", rs(taxable["lt_paise"])),
    ]
    for line in tax["lines"]:
        # No amount inside a label: the page's text font has no rupee sign of its own.
        rows.append((
            f"Tax on {TERMS[line['term']].lower()} gain at {rate_text(line['rate_bp'])}",
            rs(line["tax_paise"]),
        ))  # fmt: skip
    rows.append(("Tax at the special rates (not your slab tax)", D.Ink(rs(tax["tax_paise"]), True)))
    rows.append((f"Plus {tax['cess_label']}", rs(tax["cess_paise"])))
    sales = tuple(
        _sale_row(g, D.Money(g["gain_paise"])) + (
            HELD.get(g.get("term"), "-") + ("*" if g.get("fmv_used") else "")
            + (", buyback" if g.get("buyback") else ""),
        )
        for g in cg["schedule"]
    )  # fmt: skip
    out: list[D.Block] = [
        D.Heading("Capital gains: listed shares and equity funds"),
        _amounts(rows),
        *_lines(cg.get("set_off_words")),
        *_carried(cg.get("carried") or ()),
        D.Heading("Every sale of the year"),
        D.Table((*SALE_COLUMNS, D.Column("Gain or loss", 1.8, "r"), D.Column("Term", 2.2)), sales,
                empty="No sale of shares or equity fund units in this financial year."),
        *_lines([FMV_MARK] if any(g.get("fmv_used") for g in cg["schedule"]) else ()),
    ]  # fmt: skip
    aside = [
        _sale_row(g, STATUS_WORDS.get(g.get("status"), str(g.get("status"))))
        for key in ("needs_fmv", "unknown_cost", "exempt_buybacks")
        for g in cg.get(key) or ()
    ]
    if aside:
        out += [
            D.Heading("Sales listed and in no total"),
            D.Table((*SALE_COLUMNS, D.Column("Why", 4.0)), tuple(aside)),
        ]
    return tuple(out)


def business_blocks(b: Mapping[str, Any]) -> tuple[D.Block, ...]:
    fo, spec, taxable = b["non_speculative"], b["speculative"], b["taxable"]
    body = (
        ("Profit or loss before charges", D.Money(fo["gross_paise"]), D.Money(spec["gross_paise"])),
        ("Charges, STT included", rs(fo["charges_paise"]), rs(spec["charges_paise"])),
        ("After charges", D.Money(fo["net_paise"]), D.Money(spec["net_paise"])),
        ("Taxable after losses set off", rs(taxable["non_speculative_paise"]),
         rs(taxable["speculative_paise"])),
        ("Turnover", rs(fo["turnover_paise"]), rs(spec["turnover_paise"])),
    )  # fmt: skip
    out: list[D.Block] = [
        D.Heading("Business income: F&O and intraday shares"),
        D.Table(
            (D.Column("", 3.0), D.Column("F&O and other derivatives", 2.4, "r"),
             D.Column("Intraday shares (speculative)", 2.4, "r")),
            body,
        ),
        D.Paragraph((
            f"Turnover in all: {rs(b['turnover_paise'])}. Method: "
            f"{b.get('turnover_method_label') or b.get('turnover_method')}.",
        )),
        *_lines(b.get("flags")),
        *_lines(b.get("set_off_words")),
        *_carried(b.get("carried") or ()),
    ]  # fmt: skip
    differs = b.get("differs") or ()
    if differs:
        out += [
            D.Heading("Differs from Upstox's own report"),
            D.Table(
                (D.Column("Contract", 3.4), D.Column("This paper", 2.0, "r"),
                 D.Column("Upstox says", 2.0, "r")),
                tuple((str(r.get("scrip")),
                       "not worked out" if r.get("own_paise") is None else D.Money(r["own_paise"]),
                       "nothing" if r.get("upstox_paise") is None else D.Money(r["upstox_paise"]))
                      for r in differs),
            ),
        ]  # fmt: skip
    waiting, unsettled = len(b.get("open") or ()), len(b.get("unknown_settlement") or ())
    said = []
    if waiting:
        said.append(f"{words.plural(waiting, 'position')} still open: in no total until closed.")
    if unsettled:
        said.append(
            f"{words.plural(unsettled, 'future')} past expiry with no settlement price: in no "
            "total."
        )
    return (*out, *_lines(said))


def charges_blocks(charges: Mapping[str, Any]) -> tuple[D.Block, ...]:
    by_segment = charges.get("by_segment") or {}
    head = D.Heading("Charges as Upstox reports them")
    if not by_segment:
        return (head, D.Paragraph(("Upstox's charges for this year are not read yet.",)))
    shown = [(k, t) for k, t in CHARGE_COLUMNS if any(k in v for v in by_segment.values())]
    body = tuple(
        (SEGMENTS.get(seg, str(seg)), *(rs(by_segment[seg].get(k)) for k, _ in shown))
        for seg in sorted(by_segment)
    )
    return (head, D.Table((D.Column("Segment", 2.4), *(D.Column(t, 1.7, "r") for _, t in shown)),
                          body))  # fmt: skip


def pack_blocks(pack: Mapping[str, Any]) -> tuple[D.Block, ...]:
    """The working paper's blocks, in the order the phone's Tax view shows its cards."""
    running = (
        ("The year is still running: every figure is to date.",) if pack.get("running") else ()
    )
    missing = tuple(str(m) for m in (pack.get("inputs") or {}).get("missing") or ())
    outside = pack.get("not_computed_sales") or ()
    out: list[D.Block] = [
        D.Heading("Tax working paper"),
        D.Paragraph((
            f"Computed by SUDARSHAN's portfolio service from Upstox's reports and your own "
            f"entries, as of {as_of_label(pack)}. Rules edition {pack['rules_version']}.",
            *running,
        )),
        *((D.Heading("Still needed from you or from Upstox"), D.Paragraph(missing))
          if missing else ()),
        *capital_blocks(pack["capital_gains"]),
        *business_blocks(pack["business"]),
        *charges_blocks(pack["charges"]),
        D.Heading("Not computed here"),
        *_lines(pack.get("not_computed")),
    ]  # fmt: skip
    if outside:
        out.append(D.Table(
            (D.Column("Sold, outside this paper", 3.4), D.Column("Day", 1.4),
             D.Column("Amount", 1.8, "r")),
            tuple((_name(r.get("instrument")),
                   day_text(r["day"]) if r.get("day") else "-", rs(r.get("amount_paise")))
                  for r in outside if isinstance(r, Mapping)),
        ))  # fmt: skip
    notes = _lines(pack.get("notes"))
    if notes:
        out += [D.Heading("Notes"), *notes]
    return tuple(out)


def pack_lines(pack: Mapping[str, Any]) -> list[str]:
    """The pack in the report's words: the taxable amounts per head, each as the pack has it."""
    cg, b = pack["capital_gains"], pack["business"]
    lines = [
        built_from(pack),
        f"Capital gains taxable: short-term {rs(cg['taxable']['st_paise'])}, long-term "
        f"{rs(cg['taxable']['lt_paise'])}; tax at the special rates "
        f"{rs(cg['indicative_tax']['tax_paise'])}",
        f"F&O after charges: {words.rupees(b['non_speculative']['net_paise'], signed=True)}"
        f"{words.SEP}intraday shares: "
        f"{words.rupees(b['speculative']['net_paise'], signed=True)}",
    ]
    return lines
