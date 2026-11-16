"""Invented TRINETRA items for the signals desk's tests (AL2, M13.10): a RelayChange and a
RelayAlert as sudarshan-signals hands them (``signals/words.py``: relay_change, relay_alert), and a
relay. Nothing of TRINETRA's is in this repository; every reading and sentence here is made up."""

from __future__ import annotations

from alerts_fakes import ist

AT = ist(2026, 11, 16, 10, 42, 5)
AT_MS = int(AT.timestamp() * 1000)
WHY = "FII sold index futures for a third session; the basis turned negative."
WORDS_EXAMPLE = (
    "TRINETRA · NIFTY regime: FII accumulation → FII distribution (bearish), confidence 0.71."
)


def change(n: int = 1, **fields) -> dict:
    """A RelayChange, invented."""
    item = {
        "id": f"c-{n}", "at_ms": AT_MS, "module": "regime", "module_name": "DRISHTI",
        "instrument": "NIFTY", "kind": "state", "from_label": "FII accumulation",
        "to_label": "FII distribution", "bias": "bear", "confidence": 0.71, "why": WHY,
        "why_lang": "en", "night": False,
    }  # fmt: skip
    return item | fields


def alert(aid: str = "a-20261116-0007", **fields) -> dict:
    item = {
        "id": aid, "at_ms": AT_MS, "module": "regime", "module_name": "DRISHTI",
        "instrument": "NIFTY", "level": "act", "title": "FII selling quickens",
        "text": "Three sessions of net selling in index futures.", "lang": "en", "night": False,
    }  # fmt: skip
    return item | fields


def relay_message(changes=(), alerts=()) -> dict:
    return {"op": "signals_relay", "principal": "sudarshan-signals",
            "args": {"changes": list(changes), "alerts": list(alerts)}}  # fmt: skip
