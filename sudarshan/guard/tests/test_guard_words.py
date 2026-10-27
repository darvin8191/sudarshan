"""Every sentence the risk layer can show is written for a trader (M5.13, CLAUDE.md).

Each names the cause and the next action; none carries a status code, an exception's name, an
endpoint, a machine code, an internal identifier, "arm" or "paper". The scan renders every
sentence with representative figures and reads the result.
"""

from __future__ import annotations

import re

import pytest

from sudarshan.guard import band, rules, size, words

POSITION = ("NFO", "NIFTY27OCT2625900CE", "MIS")
SINCE = "10:42"


def _verdict(reason: str, room: int = 0) -> rules.Verdict:
    return rules.Verdict(
        reason in (rules.EXIT, rules.CNC_SELL, rules.LESS_RISK), reason, 65, 0, room
    )


def every_sentence() -> list[str]:
    out = list(words.FIXED)
    for reason in (
        rules.OPENS,
        rules.ADDS,
        rules.CROSSES,
        rules.ON_ITS_WAY,
        rules.OCO,
        rules.UNKNOWN,
    ):
        for side in (rules.BUY, rules.SELL, None):
            out.append(
                words.kill_switch_on(
                    SINCE,
                    _verdict(reason, 65 if reason == rules.CROSSES else 0),
                    position=POSITION,
                    side=side,
                    claim_sent_at=1_761_541_925.0 if reason == rules.ON_ITS_WAY else None,
                )
            )
    for pipe in ("live", "sandbox"):
        out += [
            words.book_unreadable(pipe, SINCE),
            words.book_unreadable(pipe, None),
            words.check_broke(pipe),
            words.order_not_found(pipe),
        ]
    out += kill_switch_sentences()
    out += daily_sentences()
    out += band_sentences()
    out += size_sentences()
    out += burst_sentences()
    return out


def burst_sentences() -> list[str]:
    out = []
    for count, window in ((10, 60.0), (1, 60.0), (5, 90.0), (3, 3600.0), (2, 1.0)):
        out.append(words.burst_refused(count, window, 1_761_541_925.0))
        for pipe in ("live", "sandbox"):
            out.append(words.burst_told(count, window, 1_761_541_925.0, pipe))
    for what in ("basket", "multi_leg", "split"):
        out.append(words.together_refused(what, None, out[0]))
    return out


def size_sentences() -> list[str]:
    out = []
    excesses = [("lots", 3, 2), ("lots", 2.31, 2), ("lots", 2, 1), ("shares", 1500, 1000),
                ("shares", 100001, 100000)]  # fmt: skip
    for unit, amount, cap in excesses:
        for reason, room in ((rules.OPENS, 0), (rules.ADDS, 0), (rules.CROSSES, 65),
                             (rules.ON_ITS_WAY, 0)):  # fmt: skip
            for side in (rules.BUY, rules.SELL):
                out.append(
                    words.size_refused(
                        size.Excess(unit, amount, cap),
                        _verdict(reason, room),
                        position=POSITION,
                        side=side,
                    )
                )
    leg = out[0]
    for what, part in (("basket", 2), ("multi_leg", 1), ("split", None)):
        out.append(words.together_refused(what, part, leg))
    return out


def band_sentences() -> list[str]:
    out = []
    for what in ("price", "limit", "trigger"):
        for above in (True, False):
            for side in (rules.BUY, rules.SELL):
                for pct, floor in ((20.0, 2.0), (1.0, 0.0), (2.5, 0.0)):
                    breach = band.Breach(side, what, 25500.0, 150.1, 23.9, pct, floor, above)
                    out.append(words.band_refused(breach))
    out += [words.checks_not_run(pipe) for pipe in ("live", "sandbox")]
    return out


def daily_sentences() -> list[str]:
    out = []
    for reason in (rules.OPENS, rules.ADDS, rules.CROSSES, rules.OCO, rules.UNKNOWN):
        for side in (rules.BUY, rules.SELL, None):
            verdict = _verdict(reason, 65 if reason == rules.CROSSES else 0)
            out.append(words.daily_locked(-4125.5, 4000.0, verdict, position=POSITION, side=side))
    for pipe in ("live", "sandbox"):
        out += [
            words.daily_unreadable(pipe),
            words.daily_lock_told(-4125.5, 4000.0, pipe),
            words.daily_not_checked(pipe),
        ]
    out += [words.daily_loosened_told(4000.0, after) for after in (6000.0, None)]
    return out


def kill_switch_sentences() -> list[str]:
    steps = ["saved", "told", "drain", "live_orders", "live_gtts", "live_exit_all",
             "live_orders_again", "sandbox_orders", "sandbox_gtts", "sandbox_exit_all", "mcp",
             "agent", "unknown"]  # fmt: skip
    counts = [
        None,
        {"orders": 0, "gtts": 0, "stops": 0, "protected": 0},
        {"orders": 1, "gtts": None, "stops": 1, "protected": 1},
        {"orders": 12, "gtts": 2, "stops": 3, "protected": 2},
    ]
    report = {"steps": [{"step": "live_orders", "ok": False, "words": words.KS_ORDERS_NOT_CONFIRMED},
                        {"step": "agent", "ok": True, "words": words.KS_AGENT_STOPPED}]}  # fmt: skip
    out = [words.ks_already_on(SINCE), words.ks_already_on("")]
    out += [words.ks_turned_on(SINCE, s) for s in ("app", "telegram", "api", None)]
    out += [words.ks_step_broke(step) for step in steps]
    out += [words.ks_drained(*n) for n in ((0, 0), (1, 0), (2, 1), (5, 0))]
    out += [words.ks_orders_cancelled(n, b) for n in (0, 1, 7) for b in counts[1:]]
    out += [words.ks_gtts_cancelled(*n) for n in ((0, 0), (1, 0), (2, 1))]
    out += [words.ks_exit_all("All Open Positions Squared Off"), words.ks_exit_all("")]
    out += [words.ks_swept_again(words.ks_orders_cancelled(1, {}))]
    out += [words.ks_sandbox_orders(n, ok) for n in (0, 1, 3) for ok in (True, False)]
    out += [words.ks_sandbox_gtts(*n) for n in ((0, 0), (1, 0), (2, 1))]
    out += [words.ks_sandbox_exit_all("Closed 1 positions"), words.ks_sandbox_exit_all("")]
    out += [words.ks_mcp_revoked(n) for n in (0, 1, 4)]
    out += [words.ks_resume_wait(n) for n in (1, 59)]
    out += [
        words.ks_report_lead(r)
        for r in (None, {"steps": []}, report, {"restarted": words.KS_RESTARTED})
    ]
    out += words.ks_report_message(report).split("\n")
    out += [words.ks_preview(c, telegram=t) for c in counts for t in (False, True)]
    out += [words.setting_invalid(name) for name in (*words._SETTING_RANGES, "band_on")]
    out += [words.settings_changed(["band_on", "max_lots_fo"], looser) for looser in (False, True)]
    out += [
        words.ks_resume_refused(words.PHRASE_WRONG),
        words.ks_resume_refused(words.ks_resume_wait(12)),
    ]
    return [line for line in out if line]


FORBIDDEN = [
    (r"(?<![₹,.])\b[1-5]\d\d\b(?!%|[,.]\d| seconds)", "a status code"),
    (r"\bHTTP\b|\bstatus code\b", "a protocol term"),
    (r"\b\w*(Error|Exception)\b|Traceback", "an exception's name"),
    (r"https?://|/api/|/v\d/|/sudarshan/|\.py\b", "an endpoint or a file"),
    (r"\b[A-Z]+_[A-Z_]+\b|UDAPI\w*", "a machine code"),
    (r"\barm(s|ed|ing)?\b", "an arm word"),
    (r"\bpaper\b|\bvirtual\b", "a word this platform does not use"),
    (r"\b(gate|claim|verdict|pipe|token|None|True|False|null)\b", "an internal word"),
    (r"[{}]", "a placeholder left unfilled"),
]


@pytest.mark.parametrize("sentence", every_sentence())
def test_a_sentence_is_written_for_a_trader(sentence):
    for pattern, what in FORBIDDEN:
        found = re.search(pattern, sentence, flags=re.IGNORECASE if "arm" in pattern else 0)
        assert found is None, f"{what} ({found.group(0)!r}) in: {sentence}"
    assert sentence.strip() == sentence and sentence.endswith((".", "app."))
    assert "  " not in sentence


def test_a_refusal_says_what_lets_the_order_go():
    crossing = words.kill_switch_on(
        SINCE, _verdict(rules.CROSSES, 65), position=POSITION, side=rules.SELL
    )
    assert "to exit, sell 65" in crossing and "NIFTY 25900 CE" in crossing
    covered = words.kill_switch_on(
        SINCE,
        _verdict(rules.ON_ITS_WAY),
        position=POSITION,
        side=rules.SELL,
        claim_sent_at=1_761_541_925.0,
    )
    assert covered.startswith("An exit for NIFTY 25900 CE is already on its way (sent 10:42:05)")
    plain = words.kill_switch_on(SINCE, _verdict(rules.OPENS), position=POSITION, side=rules.BUY)
    assert plain == (
        "New orders are refused: the kill switch is on since 10:42. Exits and cancels still go. "
        "To trade again, resume from Settings > Risk or send /killswitch in Telegram."
    )


def test_only_a_live_refusal_points_to_the_upstox_app():
    assert words.book_unreadable("live", SINCE).endswith("For an urgent exit, use the Upstox app.")
    assert "Upstox" not in words.book_unreadable("sandbox", SINCE)
    assert "Upstox" not in words.check_broke("sandbox")


def test_the_preview_says_in_numbers_what_turning_it_on_cancels():
    """Amendment R1, word for word where it gave the words."""
    counts = {"orders": 12, "gtts": 2, "stops": 3, "protected": 3}
    assert words.ks_preview(counts) == (
        "12 open orders and 2 GTTs will be cancelled, including 3 stop-loss orders that protect "
        "3 positions. Those positions stay open without a stop unless you also tick Exit all."
    )
    one = {"orders": 1, "gtts": 0, "stops": 1, "protected": 1}
    assert words.ks_preview(one, telegram=True) == (
        "1 open order and 0 GTTs will be cancelled, including 1 stop-loss order that protects 1 "
        "position. That position stays open without a stop unless you send /killswitch exit "
        "with your phrase."
    )
    assert words.ks_preview({"orders": 2, "gtts": 1, "stops": 0}) == (
        "2 open orders and 1 GTT will be cancelled."
    )


@pytest.mark.parametrize(
    ("amount", "shown"),
    [
        (4000, "₹4,000"),
        (4125.5, "₹4,125.50"),
        (123456.5, "₹1,23,456.50"),
        (10_000_000, "₹1,00,00,000"),
        (0.5, "₹0.50"),
        (999, "₹999"),
        (-4000, "-₹4,000"),
    ],
)
def test_rupees_are_grouped_the_indian_way(amount, shown):
    assert words.rupees(amount) == shown


@pytest.mark.parametrize(
    ("amount", "shown"),
    [(186, "₹186.00"), (7.2, "₹7.20"), (25500, "₹25,500.00"), (0.05, "₹0.05"), (150.1, "₹150.10")],
)
def test_a_price_always_shows_its_paise(amount, shown):
    assert words.price(amount) == shown


def test_a_status_code_in_a_sentence_is_still_caught():
    """The scan's code rule spares rupees, percentages and seconds, never a bare code."""
    (pattern, _what) = FORBIDDEN[0]
    assert re.search(pattern, "Upstox answered 403.")
    assert re.search(pattern, "error 500 from the broker")
    assert not re.search(pattern, "between 1 and 100%.")
    assert not re.search(pattern, "between ₹0 and ₹10,000.")
    assert not re.search(pattern, "between 1 and 3,600 seconds.")
