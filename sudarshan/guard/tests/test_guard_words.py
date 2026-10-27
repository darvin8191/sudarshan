"""Every sentence the risk layer can show is written for a trader (M5.13, CLAUDE.md).

Each names the cause and the next action; none carries a status code, an exception's name, an
endpoint, a machine code, an internal identifier, "arm" or "paper". The scan renders every
sentence with representative figures and reads the result.
"""

from __future__ import annotations

import re

import pytest

from sudarshan.guard import rules, words

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
    return out


FORBIDDEN = [
    (r"\b[1-5]\d\d\b", "a status code"),
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
