"""The alert message parser, the decision rules and the sentences (A2). All pure."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from sudarshan.automation import flag, payload, rules, words
from sudarshan.automation import model as m

HERE = Path(__file__).parent
PAYLOADS = json.loads((HERE / "payload_vectors.json").read_text())["cases"]
DECIDES = json.loads((HERE / "decide_vectors.json").read_text())["cases"]


# ---- payload ------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", PAYLOADS, ids=[c["name"] for c in PAYLOADS])
def test_payload_vector(case):
    body = bytes.fromhex(case["hex"]) if "hex" in case else case["text"]
    if case.get("as_bytes"):
        body = body.encode()
    if "error" in case:
        with pytest.raises(payload.BadMessage) as bad:
            payload.parse(body)
        assert bad.value.reason == case["error"], bad.value.reason
        if "what" in case:
            assert bad.value.what == case["what"]
        assert bad.value.reason in payload.REASONS
        return
    alert = payload.parse(body)
    for key, value in case["expect"].items():
        assert getattr(alert, key) == value, (key, getattr(alert, key))


def test_every_reason_has_a_vector_and_a_sentence():
    seen = {case["error"] for case in PAYLOADS if "error" in case}
    assert seen == set(payload.REASONS)
    for reason in payload.REASONS:
        assert words.bad_message("ORB", reason, "{{strategy.order.id}}").startswith("ORB: ")


def test_the_claim_tells_two_alerts_apart_and_two_deliveries_together():
    def one(**change):
        data = {"v": 1, "position": "long", "fired": "2026-11-16T04:45:02Z", **change}
        return payload.parse(json.dumps(data)).claim

    assert one() == one(comment="again", price="1")  # the same alert, delivered twice
    assert len({one(), one(position="flat"), one(id="L2"), one(fired="2026-11-16T04:45:03Z")}) == 4


def test_what_is_stored_keeps_no_secret_and_no_unknown_key():
    body = json.dumps(
        {
            "v": 1,
            "position": "long",
            "apikey": "0123456789abcdef0123456789abcdef",
            "comment": "my token sdt_" + "k" * 43,
            "id": "x" * 500,
            "fired": "2026-11-16T04:45:02Z",
        }
    )
    kept = payload.redact(body)
    assert "0123456789abcdef" not in kept and "sdt_" not in kept and "apikey" not in kept
    assert '"_other_keys":1' in kept and '"comment":"(removed)"' in kept
    assert len(kept) <= 600 and "x" * 65 not in kept
    assert payload.redact(b"\xff not json") == "(not readable)"
    assert payload.redact("[1, 2]") == "(not readable)"


# ---- decide -------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", DECIDES, ids=[c["name"] for c in DECIDES])
def test_decide_vector(case):
    strategy = m.Strategy(id=1, name="ORB", **case["strategy"])
    state = m.State(strategy_id=1, **case["state"])
    alert = m.Alert(**case["alert"])
    got = rules.decide(
        strategy, state, alert, case["now"], case["day"],
        claimed=case["claimed"], waited=case["waited"],
    )  # fmt: skip
    for key, value in case["expect"].items():
        assert getattr(got, key) == value, (key, getattr(got, key))
    if got.verdict is not None:
        assert got.verdict in m.VERDICTS and got.wait is False


def test_every_refusal_of_decide_has_a_vector():
    assert {c["expect"]["verdict"] for c in DECIDES} == {
        None, "repeat", "out_of_order", "stopped", "capped", "late",
    }  # fmt: skip


# ---- the pipe -----------------------------------------------------------------------------------

PIPE = [
    # stage, guard, mode, app in Analyze, target -> (analyze, why)
    ("off", "on", "sandbox", False, 1, (None, "off")),
    ("off", "on", "live", False, 0, (None, "off")),
    ("sandbox", "off", "sandbox", False, 1, (True, None)),
    ("sandbox", "on", "sandbox", True, 0, (True, None)),
    ("sandbox", "on", "live", False, 1, (None, "sandbox_only")),  # never a Live order at this stage
    (
        "sandbox",
        "on",
        "live",
        False,
        0,
        (None, "sandbox_only"),
    ),  # and never its exit in the sandbox
    ("on", "on", "sandbox", False, 1, (True, None)),
    ("on", "off", "sandbox", True, -1, (True, None)),
    ("on", "on", "live", False, 1, (False, None)),
    ("on", "on", "live", False, 0, (False, None)),
    ("on", "on", "live", True, 1, (None, "app_in_analyze")),
    ("on", "on", "live", True, -1, (None, "app_in_analyze")),
    ("on", "on", "live", True, 0, (False, None)),  # its exit still goes to Upstox
    ("on", "sandbox", "live", False, 1, (None, "guard_not_on")),
    ("on", "off", "live", False, -1, (None, "guard_not_on")),
    ("on", "sandbox", "live", False, 0, (False, None)),
    ("on", "off", "live", True, 0, (False, None)),
    ("unknown", "on", "live", False, 1, (None, "off")),
]


@pytest.mark.parametrize(("stage", "guard", "mode", "analyze", "target", "expect"), PIPE)
def test_where_an_order_goes(stage, guard, mode, analyze, target, expect):
    assert rules.pipe(stage, guard, mode, analyze, target) == expect


def test_a_sandbox_strategy_never_reaches_upstox_and_a_live_one_never_the_sandbox():
    for stage in flag.STAGES:
        for guard in ("off", "sandbox", "on"):
            for app in (False, True):
                for target in (-1, 0, 1):
                    assert rules.pipe(stage, guard, "sandbox", app, target)[0] is not False
                    assert rules.pipe(stage, guard, "live", app, target)[0] is not True


# ---- the smart order's answer -------------------------------------------------------------------

ANSWERS = [
    (True, {"status": "success", "orderid": "251116000001"}, 200, ("sent", False)),
    (True, {"status": "success", "message": "Positions Already Matched. No Action needed."}, 200, ("no_action", False)),
    (True, {"status": "success", "message": "No action needed. Position size matches current position."}, 200, ("no_action", False)),
    (True, {"status": "success", "message": "No open position found. Not placing exit order."}, 200, ("no_action", True)),
    (True, {"status": "success", "message": "No OpenPosition Found. Not placing Exit order."}, 200, ("no_action", True)),
    (True, {"status": "success"}, 200, ("not_confirmed", False)),
    (True, None, 200, ("not_confirmed", False)),
    (False, {"status": "error", "message": "busy"}, 409, ("order_refused", False)),
    (False, {"status": "error", "message": "unsized"}, 424, ("order_refused", False)),
    (False, {"status": "error", "message": "kill switch"}, 403, ("order_refused", False)),
    (False, {"status": "error", "message": "bad"}, 400, ("order_refused", False)),
    (False, {"status": "error", "message": "queue"}, 429, ("order_refused", False)),
    (False, {"status": "error", "message": "x"}, 500, ("not_confirmed", False)),
    (False, {"status": "error", "message": "x"}, 502, ("not_confirmed", False)),
    (False, {"status": "error", "message": "x", "not_confirmed": True}, 409, ("not_confirmed", False)),
    (False, {"status": "error"}, None, ("not_confirmed", False)),
    (False, "boom", 200, ("not_confirmed", False)),
]  # fmt: skip


@pytest.mark.parametrize(("success", "response", "status", "expect"), ANSWERS)
def test_reading_the_smart_orders_answer(success, response, status, expect):
    assert rules.read_answer(success, response, status) == expect


ALERT = m.Alert(position="long", fired_s=500)


@pytest.mark.parametrize("verdict", [v for v in m.VERDICTS if v not in m.MOVED])
def test_the_state_stays_when_nothing_was_sent(verdict):
    """Mutation 12."""
    assert rules.state_after(verdict, False, 1, ALERT, "NFO", "X", 9) == {}


def test_the_state_after_an_order():
    entry = rules.state_after("sent", False, 1, ALERT, "NFO", "NIFTY24NOV26FUT", 9)
    assert entry == {
        "last_target": 1, "last_fired_s": 500, "held_exchange": "NFO",
        "held_symbol": "NIFTY24NOV26FUT", "held_side": "long", "held_since_ms": 9,
    }  # fmt: skip
    maybe = rules.state_after("not_confirmed", False, -1, ALERT, "NFO", "X", 9)
    assert maybe["held_side"] == "short"  # an entry that may have gone is held
    held = m.State(1, held_symbol="X", held_side="long", held_since_ms=3)
    same = rules.state_after("no_action", False, 1, ALERT, "NFO", "X", 9, held)
    assert same["held_since_ms"] == 3  # already there: since when is kept
    closed = rules.state_after("sent", False, 0, ALERT, "NFO", "X", 9)
    assert closed["held_symbol"] is None and closed["last_target"] == 0
    assert rules.state_after("no_action", True, 0, ALERT, "NFO", "X", 9)["held_symbol"] is None
    # an exit that is not confirmed keeps the held contract
    assert rules.state_after("not_confirmed", False, 0, ALERT, "NFO", "X", 9) == {
        "last_target": 0, "last_fired_s": 500,
    }  # fmt: skip


# ---- sentences ----------------------------------------------------------------------------------

BANNED = re.compile(
    r"\b(arm|armed|arming|disarm|paper|virtual|webhook|endpoint|exception|traceback|http|json|"
    r"payload|null|none|true|false|status code|api key|token)\b|\b[1-5]\d\d\b|[{}<>_]",
    re.I,
)
SAMPLES = {
    "name": "ORB", "fired_s": 1794804302, "target": 1, "late_s": 130, "last_target": 0,
    "exits_only": True, "most": 20, "reason": "unknown", "what": "NSE:XYZ1!", "sent": "NSE:SBIN",
    "chart": "NSE:NIFTY1!", "symbol": "NIFTY24NOV26FUT", "stop_s": 1794804302, "figure": 1800,
    "quantity": 65, "flat_found": False, "said": "Another exit is on its way.", "held": "NIFTY24NOV26FUT",
    "wanted": "NIFTY29DEC26FUT", "confirmed": False, "state": "active", "surface": "the app",
    "mode": "live", "other": "Second", "side_": "long", "epoch_s": 1794804302, "seconds": 130,
    "product": "MIS", "strategy": m.Strategy(id=1, name="ORB", underlying="NIFTY"),
}  # fmt: skip
REASONS = {
    "bad_message": [*payload.REASONS],
    "not_mapped": ["unknown", "no_chart", "not_listed", "no_expiry", "master", "commodity", "index", "unchecked"],
    "not_sized": ["freeze", "lot"],
    "mode_refused": ["app_in_analyze", "guard_not_on", "semi_auto", "sandbox_only"],
}  # fmt: skip


def _sentences():
    for name, fn in inspect.getmembers(words, inspect.isfunction):
        if name.startswith("_") or fn.__module__ != words.__name__:
            continue
        params = inspect.signature(fn).parameters
        base = {key: SAMPLES[key] for key in params}
        variants = [base]
        if "reason" in params:
            variants = [{**base, "reason": reason} for reason in REASONS[name]]
        if "target" in params:
            variants = [{**v, "target": t} for v in variants for t in (1, -1, 0)]
        if "state" in params:
            variants = [{**v, "state": s} for v in variants for s in m.STATES]
        if "exits_only" in params:
            variants = [{**v, "exits_only": e} for v in variants for e in (True, False)]
        if "late_s" in params:
            variants = [{**v, "late_s": s} for v in variants for s in (130, -12, 4000)]
        if "mode" in params:
            variants = [{**v, "mode": s} for v in variants for s in m.MODES]
        for kwargs in variants:
            yield name, kwargs, fn(**kwargs)
    for key, value in vars(words).items():
        if key.isupper() and not key.startswith("_"):
            for text in value.values() if isinstance(value, dict) else [value]:
                if isinstance(text, str):
                    yield key, {}, text


def test_every_sentence_is_a_traders():
    seen = list(_sentences())
    assert len(seen) > 100
    for name, kwargs, text in seen:
        assert isinstance(text, str) and text.strip() == text and text, name
        # a contract name, a chart name and a clock are allowed their own characters
        plain = text.replace("NSE:XYZ1!", "").replace("NSE:NIFTY1!", "").replace("NSE:SBIN", "")
        plain = re.sub(r"\b\d{2}:\d{2}(:\d{2})?\b", "", plain).replace("130 s", "")
        plain = plain.replace("25000", "").replace("1800", "").replace("2 min 10 s", "")
        found = BANNED.search(plain)
        assert not found, (name, kwargs, found.group(0), text)


def test_the_words_file_itself_never_says_the_banned_words():
    source = inspect.getsource(words)
    assert not re.search(r"\b(arm|armed|arming|paper trading|virtual trading)\b", source, re.I)


def test_sentences_as_the_design_words_them():
    assert words.out_of_order("ORB", 1794804298) == (
        "ORB: an alert fired at 10:14:58 arrived after a newer one and was not applied."
    )
    assert words.late("ORB", 1, 130, 0).startswith(
        "ORB: a buy alert arrived 2 min 10 s late and was not sent. The chart says long;"
    )
    assert words.capped("ORB", 20) == (
        "ORB sent 20 alerts today, the most you allow. It will take no new entries today. "
        "Its exit alert still goes."
    )
    assert words.stopped("ORB", False, 1) == "ORB is stopped. Its alerts are being ignored."
    assert words.gap("ORB", 1) == (
        "ORB: an earlier alert did not arrive. This one was applied: now long."
    )
    assert words.bad_message("ORB", "placeholder", "{{strategy.order.id}}").startswith(
        "ORB: the alert message still has strategy.order.id in it"
    )
    assert words.contract_name("NIFTY24NOV2625000CE") == "NIFTY 24 Nov 25000 CE"
    assert words.contract_name("NIFTY24NOV26FUT") == "NIFTY 24 Nov FUT"
    assert words.contract_name("RELIANCE") == "RELIANCE" and words.contract_name("M&M") == "M&M"
    assert words.clock(1794804302) == "10:15:02"
    assert [words.span(s) for s in (45, 120, 130, 3900)] == [
        "45 s",
        "2 min",
        "2 min 10 s",
        "1 h 5 min",
    ]
    assert words.trades(m.Strategy(id=1, name="ORB", underlying="NIFTY")) == (
        "NIFTY future, this month · Intraday · 1 lot"
    )
    assert words.mode_refused("ORB", "app_in_analyze", 1) == (
        "The app is in Analyze mode, so ORB's buy was not sent. Its exit alerts still go to Upstox."
    )


def test_a_service_sentence_is_passed_on_without_braces_or_length():
    said = words.order_refused("ORB", "x {a} " + "y" * 900)
    assert "{" not in said and len(said) < 360
