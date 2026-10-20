"""What the owner reads: golden messages from proxy payloads, and the formatting rules."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from alerts_fakes import ist, update

from sudarshan.alerts import messages
from sudarshan.alerts.orders import OrderTracker, parse
from sudarshan.alerts.store import Store

AT = ist(2026, 10, 21, 10, 42, 5)
HEADER = "सुदर्शन / SUDARSHAN"


def _message(tmp_path, *updates, settle_after=None, **tracker_options) -> str:
    """Run payloads through a fresh tracker and compose what the owner would receive."""
    store = Store(tmp_path / "alerts.db")
    tracker = OrderTracker(store, **tracker_options)
    alerts = []
    for payload in updates:
        alerts += tracker.on_update(payload, AT)
    if settle_after is not None:
        alerts += tracker.due(AT + timedelta(seconds=settle_after))
    store.close()
    assert alerts, "no alert"
    return messages.compose([a.line for a in alerts], AT, AT)


GOLDEN = [
    pytest.param(
        [update("1001", "complete", average_price=123.45)],
        "Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.45 (MIS, NFO)",
        id="complete",
    ),
    pytest.param(
        [update("1002", "open", quantity=130, filled_quantity=65, average_price=98.1)],
        "Part filled: BUY 65 of 130 NIFTY 21 OCT 25900 CE at ₹98.10 (MIS, NFO)",
        id="partial",
    ),
    pytest.param(
        [
            update(
                "1003",
                "rejected",
                action="SELL",
                product="CNC",  # Upstox's carry-forward "D" arrives as CNC; on NFO it is NRML
                rejection_reason="RMS:Rule: Check circuit limit including square off order "
                "exceeds : Circuit breach, Order Price :50.00, [Low_Price] *70.90*",
            )
        ],
        "Rejected: SELL 65 NIFTY 21 OCT 25900 CE (NRML, NFO). Broker says: RMS:Rule: Check "
        "circuit limit including square off order exceeds : Circuit breach, Order Price :50.00, "
        "(Low Price) 70.90",
        id="rejected-with-reason",
    ),
    pytest.param(
        [update("1004", "rejected", rejection_reason="")],
        "Rejected: BUY 65 NIFTY 21 OCT 25900 CE (MIS, NFO). The broker gave no reason.",
        id="rejected-without-reason",
    ),
    pytest.param(
        [update("1005", "cancelled", pricetype="LIMIT", price=120.0)],
        "Cancelled: BUY 65 NIFTY 21 OCT 25900 CE, none filled (MIS, NFO)",
        id="cancelled",
    ),
    pytest.param(
        [update("1006", "cancelled", filled_quantity=40, average_price=121.5)],
        "Part filled, rest cancelled: BUY 40 of 65 NIFTY 21 OCT 25900 CE at ₹121.50 (MIS, NFO)",
        id="cancelled-after-part-fill",
    ),
    pytest.param(
        [
            update("1007", "trigger pending", action="SELL", pricetype="SL", trigger_price=120),
            update("1007", "open", action="SELL", pricetype="SL", trigger_price=120),
        ],
        "Triggered: SELL 65 NIFTY 21 OCT 25900 CE at trigger ₹120.00, not filled yet "
        "(SL, MIS, NFO)",
        id="trigger",
    ),
    pytest.param(
        [
            update("1008", "trigger pending", action="SELL", pricetype="SL-M"),
            update("1008", "complete", action="SELL", pricetype="SL-M", average_price=119.8),
        ],
        "Filled: SELL 65 NIFTY 21 OCT 25900 CE at ₹119.80 (SL-M, MIS, NFO)",
        id="stop-filled",
    ),
]


@pytest.mark.parametrize(("payloads", "expected"), GOLDEN)
def test_golden_messages(tmp_path, payloads, expected):
    settle = 5.0 if expected.startswith("Triggered") else None
    text = _message(tmp_path, *payloads, settle_after=settle)
    assert text == f"{HEADER}\n{expected}\n10:42:05 IST"


def test_a_merged_burst_lists_every_order_once(tmp_path):
    fills = [update(f"20{n}", "complete", average_price=100 + n) for n in range(4)]
    text = _message(tmp_path, *fills)
    lines = text.split("\n")
    assert lines[0] == HEADER and lines[-1] == "10:42:05 IST"
    assert [line.split(" at ")[1] for line in lines[1:-1]] == [
        "₹100.00 (MIS, NFO)", "₹101.00 (MIS, NFO)", "₹102.00 (MIS, NFO)", "₹103.00 (MIS, NFO)",
    ]  # fmt: skip


def test_times_far_apart_show_a_range():
    text = messages.compose(["Filled: x"], AT, AT + timedelta(minutes=3, seconds=2))
    assert text.endswith("\n10:42:05 to 10:45:07 IST")
    assert messages.compose(["Filled: x"], AT, AT + timedelta(seconds=40)).endswith(
        "\n10:42:05 IST"
    )
    dropped = messages.compose(["Filled: x"], AT, AT, dropped=12)
    assert "12 more order updates could not be listed here. The order book has them." in dropped


def test_sandbox_lines_say_so(tmp_path):
    text = _message(tmp_path, update("3001", "complete", mode="analyze"), sandbox=True)
    assert "\nSandbox filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.45 (MIS, NFO)\n" in text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (123.45, "₹123.45"),
        (123456.5, "₹1,23,456.50"),
        (12345678.9, "₹1,23,45,678.90"),
        (999.999, "₹1,000.00"),
        (123.45333, "₹123.45"),  # an average of several fills
        (0.05, "₹0.05"),
    ],
)
def test_money_in_indian_format(value, text):
    assert messages.money(value) == text


def test_currency_prices_keep_four_decimals(tmp_path):
    payload = update("4001", "complete", symbol="USDINR28OCT26FUT", exchange="CDS",
                     product="NRML", quantity=1000, filled_quantity=1000, average_price=83.1225)  # fmt: skip
    text = _message(tmp_path, payload)
    assert "\nFilled: BUY 1,000 USDINR 28 OCT FUT at ₹83.1225 (NRML, CDS)\n" in text


def test_quantities_in_indian_format():
    assert messages.quantity(65) == "65"
    assert messages.quantity(1800) == "1,800"
    assert messages.quantity(130000) == "1,30,000"


@pytest.mark.parametrize(
    ("symbol", "name"),
    [
        ("NIFTY21OCT2625900CE", "NIFTY 21 OCT 25900 CE"),
        ("BANKNIFTY28OCT2657000PE", "BANKNIFTY 28 OCT 57000 PE"),
        ("NIFTYNXT5028OCT2668000CE", "NIFTYNXT50 28 OCT 68000 CE"),
        ("VEDL28OCT26292.5CE", "VEDL 28 OCT 292.5 CE"),
        ("NIFTY05NOV2625000PE", "NIFTY 5 NOV 25000 PE"),
        ("NIFTY28JAN2725900CE", "NIFTY 28 JAN 2027 25900 CE"),  # another year: shown
        ("BANKNIFTY28OCT26FUT", "BANKNIFTY 28 OCT FUT"),
        ("M&M", "M&M"),
        ("INFY", "INFY"),
        ("NIFTY 25900 CE 21 OCT 26", "NIFTY 25900 CE 21 OCT 26"),  # not mapped: as the broker says
    ],
)
def test_instrument_names(symbol, name):
    assert messages.instrument(symbol, date(2026, 10, 21)) == name


def test_broker_text_cannot_break_the_markdown_or_the_layout():
    text = messages.clean("a_b *bold* `code` [link](x)\n\tnext\x00line", 200)
    assert text == "a b bold 'code' (link)(x) next line"
    assert not re.search(r"[*_`\[\]]", text)
    assert messages.clean("x" * 300, 50) == "x" * 47 + "..."


def test_product_and_type_tags():
    def tags(**fields):
        return messages.tags(parse(update("1", "open", **fields)))

    assert tags() == "MIS, NFO"
    assert tags(product="CNC", exchange="NSE") == "CNC, NSE"
    assert tags(product="CNC", exchange="BFO") == "NRML, BFO"
    assert tags(pricetype="SL_M") == "SL-M, MIS, NFO"
    assert tags(pricetype="LIMIT") == "MIS, NFO"


def test_no_status_codes_or_protocol_words_in_our_own_text(tmp_path):
    payloads = [p.values[0] for p in GOLDEN]
    for number, group in enumerate(payloads):
        text = _message(tmp_path / str(number), *group, settle_after=5.0)
        ours = text.split("Broker says:")[0]
        for word in ("order_status", "trigger pending", "orderid", "HTTP", "None", "null"):
            assert word not in ours
