"""The TRINETRA view's contract (T0): its shapes hold together, its words are a trader's, and the
phone's types.ts is exactly what the contract makes."""

from __future__ import annotations

import re
import string
import subprocess
import sys
from pathlib import Path

from sudarshan.signals import contract as c
from sudarshan.signals import gentypes

REPO = Path(__file__).resolve().parents[3]


def test_every_field_names_a_known_type():
    for name in c.RECORDS:
        fields = c.record(name)
        assert fields, name
        assert len({f.name for f in fields}) == len(fields), f"{name} repeats a field"
        for field in fields:
            assert c.known_type(field.type), (name, field)
    for op in (*c.OPS, *c.SERVICE_OPS):
        assert c.known_type(op.res), op
        for field in c.parse_fields(op.req):
            assert c.known_type(field.type), (op.name, field)


def test_the_ops_the_purposes_and_the_bridge_agree():
    assert len(set(c.OP_NAMES)) == len(c.OP_NAMES)
    assert set(c.PURPOSES) == set(c.PRINCIPALS) == set(c.SEAL_LIMITS)
    assert set(c.SERVED_PURPOSES) == set(c.PURPOSE_OPS)
    for ops in c.PURPOSE_OPS.values():
        assert set(ops) <= set(c.OP_NAMES)
    assert c.PURPOSE_OPS["journal"] == ("state", "state_at")  # reads only (design 3.5)
    assert {op.cls for op in c.OPS} <= set(c.CLASSES) == set(c.LIMITS_PER_MIN) == set(c.TIMEOUTS_S)
    assert [op.name for op in c.OPS if op.cls == "write"] == ["settings_set"]
    for op in c.OPS:
        assert op.method in ("GET", "PUT") and op.route.startswith("/")
        if "<id>" in op.route:
            assert "id" in {f.name for f in c.parse_fields(op.req)}
    assert all(p.startswith("sudarshan/") and p.endswith("/v1") for p in c.PURPOSES.values())
    assert c.PURPOSES["relay"] == "sudarshan/alerts/signals/v1"  # the alerts' listener opens it
    assert c.SEAL_LIMITS == {"api": 16384, "journal": 4096, "push": 32768, "relay": 16384}
    assert c.PUSH_EVENTS == ("sudarshan_signals_update",) and c.PUSH_PATH.startswith(
        c.INTERNAL_PREFIX
    )
    assert c.RELAY_PATH == "/v1/signals_relay" and c.ALERTS_PORT == 5582 and c.PORT == 5588
    assert set(c.ERROR_STATUS) == set(c.ERROR_CODES)
    assert set(c.RELAYED_KINDS) < set(c.CHANGE_KINDS) and "lapsed" not in c.RELAYED_KINDS
    assert set(c.RELAYED_LEVELS) == {"watch", "act"}


def test_the_purposes_are_the_vaults():
    """K1: each purpose the contract names is a key of the vault, word for word, and each key
    opens only its own seals (the vault's own test holds every pair apart)."""
    from sudarshan.vault import keys

    assert c.PURPOSES["api"].encode() == keys.SIGNALS_API_INFO
    assert c.PURPOSES["journal"].encode() == keys.SIGNALS_JOURNAL_INFO
    assert c.PURPOSES["push"].encode() == keys.SIGNALS_PUSH_INFO
    assert c.PURPOSES["relay"].encode() == keys.ALERTS_SIGNALS_INFO
    assert {p.encode() for p in c.PURPOSES.values()} <= set(keys.PURPOSES)


def test_every_module_name_and_language():
    assert set(c.MODULE_NAMES) == set(c.KNOWN_MODULES)
    assert c.module_name("regime") == "DRISHTI" and c.module_name("breadth") == "Other"
    assert set(c.LANG_NAMES) == set(c.LANGS) == set(c.LANG_FALLBACK)
    assert c.DEFAULT_LANG == "hi"
    for name in c.KNOWN_MODULES:
        assert re.fullmatch(c.MODULE_PATTERN, name)
    assert set(c.BIAS_WORDS) == set(c.BIASES) and set(c.CHANGE_WORDS) == set(c.CHANGE_KINDS)
    assert set(c.LEVEL_WORDS) == set(c.ALERT_LEVELS) and set(c.HORIZON_WORDS) == set(c.HORIZONS)


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_word_is_for_a_trader():
    allowed = {"time", "age", "difference", "count", "wanted", "shown", "n"}
    for key, text in {**c.WORDS, **{f"age:{k}": v for k, v in c.AGE_WORDS.items()}}.items():
        assert text and text == text.strip(), key
        assert _fields(text) <= allowed, key
        low = text.lower()
        for word in ("traceback", "exception", "http", "errno", "json", "/health", "127.0.0.1",
                     "timeout", "paper", "virtual", "real-time", "status code"):  # fmt: skip
            assert word not in low, (key, word)
        assert not re.search(r"\b[1-5]\d\d\b", text), key
        assert not re.search(r"\b(live|arm|armed|arming)\b", low), key
        assert all(ord(ch) < 0x2000 for ch in text), f"{key}: no emoji or symbols"
    sentences = {k: v for k, v in c.WORDS.items() if k not in ("fresh", "lapsed", "too_few")}
    for key, text in sentences.items():
        assert text.endswith("."), key
    # section 3.6, word for word
    assert c.WORDS["stopped"] == "TRINETRA was stopped by its operators at {time}."
    assert c.WORDS["never"] == (
        "TRINETRA is not connected yet. Publishing has not been switched on in TRINETRA."
    )
    assert c.WORDS["off"] == "The TRINETRA view is switched off."
    assert c.WORDS["not_answering"] == "SUDARSHAN's TRINETRA reader is not answering."
    assert c.WORDS["not_today"] == "TRINETRA has not published today."


def test_types_ts_is_what_the_contract_makes(tmp_path):
    assert gentypes.TARGET.exists(), "run: python -m sudarshan.signals.gentypes"
    assert gentypes.check(), "types.ts is out of date: run python -m sudarshan.signals.gentypes"
    stale = tmp_path / "types.ts"
    stale.write_text(gentypes.render() + "// edited by hand\n", encoding="utf-8")
    assert not gentypes.check(stale)
    assert not gentypes.check(tmp_path / "missing.ts")
    text = gentypes.render()
    for name in (*c.RECORDS, *c.ENUMS, "SignalsOps", "Envelope", "WORDS", "OPS"):
        assert re.search(rf"\b(interface|type|const) {name}\b", text), name
    # the formatter's quotes: an apostrophe needs no escape
    assert "TRINETRA\\'s" not in text and "\"TRINETRA's clock" in text


def test_gentypes_check_runs_as_a_command():
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.signals.gentypes", "--check"],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
