"""The follow-row rules (W14) against hand-typed vectors, OpenAlgo's own ATM and the words."""

from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path

import pytest

from sudarshan.watchlists import dynamic
from sudarshan.watchlists.dynamic import Price
from sudarshan.watchlists.words import dynamic as words

V = json.loads((Path(__file__).parent / "vectors" / "dynamic.json").read_text(encoding="utf-8"))
ROOT = Path(__file__).resolve().parents[3]


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def strikes(name):
    if name == "ladder":
        return V["ladder"]
    if name == "uneven":
        return V["uneven"]
    return name


@pytest.mark.parametrize("c", V["pick"])
def test_pick_expiries(c):
    got = dynamic.pick_expiries(V["expiries"], ms(c["now"]))
    assert got == {k: c[k] for k in ("nearest", "next", "month", "next_month")}


@pytest.mark.parametrize("c", V["closes_pick"], ids=lambda c: c["name"])
def test_closes_from_the_calendar(c):
    closes = {d: ms(t) for d, t in c["closes"].items()}
    got = dynamic.pick_expiries(c["expiries"], ms(c["now"]), closes)
    assert got["nearest"] == c["nearest"]


def test_unsorted_and_repeated_expiries_read_the_same():
    a = dynamic.pick_expiries(V["expiries"], ms("2026-11-24T10:00:00+05:30"))
    b = dynamic.pick_expiries(V["expiries"][::-1] * 2, ms("2026-11-24T10:00:00+05:30"))
    assert a == b


@pytest.mark.parametrize("c", V["atm"])
def test_atm(c):
    assert dynamic.atm_strike(c["price"], strikes(c["strikes"])) == c["atm"]


def _openalgo_atm():
    """OpenAlgo's own function, read from its source so no part of the app is imported."""
    tree = ast.parse((ROOT / "services" / "option_symbol_service.py").read_text(encoding="utf-8"))
    keep = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name in ("find_atm_strike_from_actual", "_is_finite_number")
    ]
    assert len(keep) == 2
    import logging
    import math

    ns = {"logger": logging.getLogger("oa-atm-probe"), "math": math}
    exec(compile(ast.Module(keep, []), "oa", "exec"), ns)  # noqa: S102
    return ns["find_atm_strike_from_actual"]


def test_atm_agrees_with_openalgos_rule_over_the_vectors():
    oa = _openalgo_atm()
    for c in V["atm"]:
        s = strikes(c["strikes"])
        if s:
            assert dynamic.atm_strike(c["price"], s) == oa(c["price"], sorted(s)), c


@pytest.mark.parametrize("c", V["band"])
def test_band(c):
    low, high = dynamic.band(strikes(c["strikes"]), c["strike"])
    assert (low, high) == (c["low"], c["high"])


@pytest.mark.parametrize("c", V["hold"])
def test_hold_or_recentre(c):
    assert dynamic.choose_atm(c["price"], V["ladder"], c["held"]) == c["strike"]


@pytest.mark.parametrize("c", V["offset"])
def test_offset(c):
    assert dynamic.offset_strike(V["ladder"], c["atm"], c["offset"]) == c["strike"]


def test_ten_below_the_lowest_is_refused():
    c = V["ten_below_lowest"]
    assert dynamic.offset_strike(V["ladder"], c["atm"], c["offset"]) is None


@pytest.mark.parametrize("c", V["price"], ids=lambda c: c["name"])
def test_price_choice(c):
    got = dynamic.choose_price(
        tick=tuple(c["tick"]) if "tick" in c else None,
        connected=c.get("connected", False),
        snapshot=tuple(c["snapshot"]) if "snapshot" in c else None,
        feed=tuple(c["feed"]) if "feed" in c else None,
        last=tuple(c["last"]) if "last" in c else None,
        closed=c.get("closed", False),
    )
    assert list(got) == c["expect"]


def run(c):
    expiries = V["expiries"]
    by = dict.fromkeys(expiries, V["ladder"])
    sides = None
    if "sides" in c:
        sides = {(s["expiry"], s["strike"]): tuple(s["rights"]) for s in c["sides"]}
    price = Price(*c["price"])
    return dynamic.resolve(c["row"], expiries, by, ms(c["now"]), price, c["held"], None, sides)


@pytest.mark.parametrize("c", V["resolve"], ids=lambda c: c["name"])
def test_resolve(c):
    r = run(c)
    for key, want in c["expect"].items():
        if key == "valid_until":
            want = ms(want)
        if key == "band":
            want = tuple(want)
        if key == "missing":
            want = tuple(want)
        assert getattr(r, key) == want, (key, r)


def test_valid_until_is_the_expiry_close_for_a_month_future():
    r = run(V["resolve"][-1])
    assert r.valid_until == ms("2026-11-24T15:30:00+05:30")


def test_quiet_page_price_is_not_stale_after_a_long_gap():
    p = dynamic.choose_price(tick=(25912.35, 0), connected=True)
    assert p.source == "tick" and p.state == "ok"


@pytest.mark.parametrize("c", V["words"], ids=lambda c: c["name"])
def test_words(c):
    row = {"kind": "atm_option", "opt_right": "CE"}
    base = dynamic.Resolution(
        "ok",
        "2026-11-24",
        25900,
        None,
        None,
        Price(25912.35, ms("2026-11-24T10:00:00+05:30"), "tick", "ok"),
        None,
    )
    kind = c["case"]
    if kind == "waiting":
        assert words.waiting("NIFTY") == c["text"]
    elif kind == "closed":
        assert words.closed("NIFTY", c["price"], ms(c["at"])) == c["text"]
    elif kind == "moved":
        assert (
            words.moved(25850, "CE", ms("2026-11-24T10:12:00+05:30"), 25931, "NIFTY") == c["text"]
        )
    elif kind == "rolled":
        assert words.rolled("2026-12-01", ms("2026-11-24T15:30:00+05:30")) == c["text"]
    elif kind == "past_end":
        r = dynamic.Resolution(
            "missing", "2026-11-24", None, None, None, base.price, "past_end", steps=10
        )
        assert words.missing(r, "NIFTY", row) == c["text"]
    elif kind == "now":
        assert words.describe(base, "NIFTY", row, ms("2026-11-24T10:00:05+05:30")) == c["text"]
    else:
        assert words.describe(base, "NIFTY", row, ms("2026-11-24T10:05:00+05:30")).startswith(
            c["text"].split(", price")[0]
        )
        assert "price from 10:00:00" in words.describe(
            base, "NIFTY", row, ms("2026-11-24T10:05:00+05:30")
        )


def test_chips_and_no_forbidden_words():
    assert words.chip("atm_option", 0) == "ATM"
    assert words.chip("atm_option", 2) == "ATM+2"
    assert words.chip("atm_option", -1) == "ATM-1"
    assert words.chip("month_future", None) == "Current-month FUT"
    src = (ROOT / "sudarshan/watchlists/words/dynamic.py").read_text(encoding="utf-8")
    assert "arm" not in src.lower().replace("warm", "")


def test_the_module_is_pure():
    tree = ast.parse(Path(dynamic.__file__).read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert names <= {"__future__", "datetime", "fractions", "typing"}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attrs & {"now", "utcnow", "time", "monotonic", "sleep"}
