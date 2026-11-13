"""Report words and the family line (JA7): vectors W1-W14 and F1-F10, and the rules on every word."""

from __future__ import annotations

import itertools
import json
import re
import unicodedata
from pathlib import Path

import pytest

from sudarshan.journal.core import kinds, words

DATA = json.loads((Path(__file__).parent / "vectors" / "words.json").read_text(encoding="utf-8"))
VECTORS = DATA["vectors"]
REPORTS = [v for v in VECTORS if v["fn"] != "family_line"]
BY_ID = {v["id"]: v for v in VECTORS}


@pytest.mark.parametrize("vec", VECTORS, ids=[v["id"] for v in VECTORS])
def test_words_vector(vec):
    assert getattr(words, vec["fn"])(**vec["args"]) == vec["text"]


def test_the_two_samples_are_the_designs_own_text():
    assert BY_ID["W1"]["text"] == (
        "सुदर्शन · SUDARSHAN\n"
        "Mon 9 Nov · Day report\n"
        "Realised +₹4,312.50 · Unrealised −₹600.00 · Net +₹3,712.50 (before charges, est. ₹486.20)\n"
        "Trades 6 · won 3, lost 2, 1 flat\n"
        "By strategy: ORB +₹2,900.00 (2) · Manual +₹812.50 (4)\n"
        "Carried overnight: 1 position (NIFTY 25000 PE, 65)\n"
        "Kill switch off · Daily loss limit not reached\n"
        "Fills: 12 recorded live; the day's book is checked at 15:50."
    )
    assert BY_ID["W5"]["text"] == (
        "सुदर्शन · SUDARSHAN\n"
        "Mon 9 Nov · Morning brief\n"
        "Market open 09:15-15:30 (F&O to 15:40) · NIFTY weekly expiry today "
        "(Tue 10 Nov is a holiday)\n"
        "Funds available ₹1,82,400.00 · used ₹0.00\n"
        "Overnight: none\n"
        "Yesterday: net +₹3,712.50 · 6 trades\n"
        "Kill switch off · Auto login: see Telegram at 08:35-08:55"
    )


def test_the_required_cases_exist():
    notes = " | ".join(v["note"] for v in VECTORS)
    for needle in ("no trades", "holiday brief", "Muhurat", "no trading day", "design's sample"):
        assert needle in notes


@pytest.mark.parametrize("vec", REPORTS, ids=[v["id"] for v in REPORTS])
def test_every_report_has_the_header_first_and_no_banned_words(vec):
    text = vec["text"]
    assert text.splitlines()[0] == words.HEADER == "सुदर्शन · SUDARSHAN"
    lowered = text.lower()
    for banned in ("scratch", "breakeven", "break-even", "paper trad", "arm"):
        assert not re.search(rf"\b{banned}", lowered), banned
    assert len(text) <= kinds.LIMITS["report_text"]


@pytest.mark.parametrize("vec", VECTORS, ids=[v["id"] for v in VECTORS])
def test_no_icons_or_emojis(vec):
    for ch in vec["text"] or "":
        cat = unicodedata.category(ch)
        assert cat not in ("So", "Sk", "Cs", "Co"), (hex(ord(ch)), ch)
        assert ord(ch) < 0x1F000 and ch not in "️‍", hex(ord(ch))


@pytest.mark.parametrize("vec", REPORTS, ids=[v["id"] for v in REPORTS])
def test_the_minus_is_the_true_minus_and_a_profit_or_loss_has_a_sign(vec):
    text = vec["text"]
    assert not re.search(r"-₹", text), "a hyphen before a rupee amount"
    unsigned_ok = ("est. ", "used ", "available ", "after est. charges ")
    for m in re.finditer(r"(?<![+−])₹([\d,]+\.\d\d)", text):
        before = text[: m.start()]
        zero = m.group(1) == "0.00"
        allowed = any(before.endswith(p) for p in unsigned_ok)
        assert zero or allowed, (m.group(0), before[-30:])


def test_rupees_and_grouping():
    assert words.rupees(18240000) == "₹1,82,400.00"
    assert words.rupees(123456789, True) == "+₹12,34,567.89"
    assert words.rupees(-60000, True) == "−₹600.00"
    assert words.rupees(0, True) == "₹0.00"
    assert words.rupees(5) == "₹0.05"
    assert words.rupees(-100000000, True) == "−₹10,00,000.00"
    assert words.group_en_in("1000") == "1,000"
    assert words.group_en_in("12345678") == "1,23,45,678"
    assert words.group_en_in("999") == "999"


def test_labels_and_percentages():
    assert words.day_label(20261109) == "Mon 9 Nov"
    assert words.day_label(20261108) == "Sun 8 Nov"
    assert words.day_label_year(20261209) == "9 Dec 2026"
    assert words.signed_pct("1.20") == "+1.20%"
    assert words.signed_pct("-2.50") == "−2.50%"
    assert words.signed_pct("0.00") == "0.00%"
    assert words.plural(1, "trade") == "1 trade" and words.plural(6, "trade") == "6 trades"


def test_a_flat_trade_says_flat():
    assert ", 1 flat" in BY_ID["W1"]["text"]
    assert "scratch" not in BY_ID["W1"]["text"].lower()


def test_a_holiday_week_and_month_say_so():
    assert "No trading day this week." in BY_ID["W10"]["text"]
    assert "No trading day this month." in BY_ID["W14"]["text"]


def test_the_tax_pack_is_a_working_paper_without_section_numbers():
    text = BY_ID["W13"]["text"]
    assert "verify with your CA" in text and "filing source" in text
    assert not re.search(r"\bsection\s*\d|\bs\.\s*\d|\bsec\.\s*\d", text, re.I)


def test_a_bad_brief_kind_is_refused():
    with pytest.raises(ValueError):
        words.brief(20261109, kind="weekend")


# ---- the family line (Y16, Y24) --------------------------------------------------------------
CATS = ("pnl", "qty", "funds", "orders")


@pytest.mark.parametrize("combo", list(itertools.product([False, True], repeat=4)))
def test_family_line_truth_table(combo):
    cats = dict(zip(CATS, combo, strict=True))
    line = words.family_line(6, 3, 2, cats)
    if not cats["orders"]:
        assert line is None
        return
    assert line.startswith("Trades today: 6")
    shows_results = cats["pnl"] and cats["qty"]
    assert (" won 3" in line and " lost 2" in line) == shows_results
    assert "₹" not in line and "paise" not in line.lower()
    assert not re.search(r"[+−]\s*\d", line)


def test_family_line_never_has_money_whatever_the_numbers():
    for trades, won, lost in [(0, 0, 0), (6, 3, 2), (1000, 600, 400)]:
        line = words.family_line(trades, won, lost, dict.fromkeys(CATS, True))
        assert "₹" not in line and "." not in line and "," not in line


def test_family_day_answers_counts_only():
    got = words.family_day(20261109, 6, 3, 2, dict.fromkeys(CATS, True))
    assert got == {"day": 20261109, "line": "Trades today: 6 · won 3 · lost 2"}
    assert set(got) == {f.name for f in kinds.record("FamilyDay")}
    assert words.family_day(20261109, 6, 3, 2, {})["line"] is None
