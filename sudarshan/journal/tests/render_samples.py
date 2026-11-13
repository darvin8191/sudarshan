"""Synthetic reports for the renderer's tests: invented figures, no real trade (JB8)."""

from __future__ import annotations

from sudarshan.journal.core import equity
from sudarshan.journal.render import doc as D

SYMBOLS = ("ALPHA 25000 CE", "BETA 52000 PE", "GAMMA FUT", "DELTA")


def trade_rows(n: int) -> tuple[tuple[D.Cell, ...], ...]:
    rows = []
    for i in range(n):
        net = (-1) ** i * (123456 + i * 10000)
        rows.append(
            (
                f"{1 + i % 28} Oct",
                SYMBOLS[i % len(SYMBOLS)],
                D.Chip("LONG" if i % 2 else "SHORT"),
                str(25 * (1 + i % 4)),
                D.Money(net),
                D.Chip("PROFIT" if net > 0 else "LOSS"),
            )
        )
    return tuple(rows)


COLUMNS = (
    D.Column("Day", 1.2),
    D.Column("Symbol", 2.4),
    D.Column("Side", 1, "c"),
    D.Column("Qty", 0.8, "r"),
    D.Column("Net", 1.8, "r"),
    D.Column("Result", 1, "c"),
)


def monthly(rows: int = 12) -> D.ReportDoc:
    table = D.Table(COLUMNS, trade_rows(rows), total=("Total", "", "", "", D.Money(-98765), ""))
    return D.ReportDoc(
        kind="monthly",
        period_key="202610",
        title="Monthly statement",
        period_label="October 2026",
        built_label="Built Sun 1 Nov 2026, 07:00",
        footer_note="Estimates before the final charges.",
        blocks=(
            D.Figures(
                (
                    D.Figure("Net after est. charges", D.Money(31234567)),
                    D.Figure("Trades", "42"),
                    D.Figure("Win rate", "61.90%"),
                    D.Figure("Deepest drawdown", D.Money(-456700)),
                )
            ),
            D.Heading("Summary"),
            D.Paragraph(("Net +₹3,12,345.67 (after est. charges ₹4,862.00)", "Trades 42")),
            D.Heading("Trades"),
            table,
            D.Note(
                "A working paper: verify with CA; the broker's own report is the filing source."
            ),
        ),
    )


def week_equity() -> dict:
    days = [20261102, 20261103, 20261104, 20261105, 20261106]
    nets = [123456, -345600, 222200, -120000, 50000]
    trips = [{"close_day": d, "net_paise": v} for d, v in zip(days, nets, strict=True)]
    return equity.equity_curve(trips, days, capital_base_paise=50000000)
