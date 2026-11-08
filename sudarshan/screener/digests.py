"""The screener's digest to sudarshan-alerts (M9.18, design 4.4, S16): the payload, and its seal.

The screener does not talk to Telegram. When a run is worth telling (alerting.decide), it builds
one digest and seals it under the purpose ``sudarshan/alerts/screener-digest/v1``, which only the
alerts service opens (cross-design X9: the alerts API refuses it under the bridge's seal, and the
bridge refuses the alerts' ops under this one). The alerts lane builds the receiving op (SV10):
once-only claim on ``run_key``, lane pacing, the delivery row and the Telegram line.

The payload follows X3: prices in paise as integers, the change as a Decimal string, instants in
epoch milliseconds, the trading day as the session key. Nothing in it is a sentence for Telegram;
the alerts' words.py writes that line from these facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import seal

from . import alerting as al

#: The op the alerts service registers for this seal (SV10).
OP = "screener_digest"
SCHEMA = 1


def change_pct(ltp: int | None, cp: int | None) -> str | None:
    """The day's change as a Decimal string with two places ("-1.25"), None when unknown."""
    if ltp is None or not cp:
        return None
    value = (Decimal(ltp) - Decimal(cp)) * 100 / Decimal(cp)
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def _named(items, prices: Mapping[str, tuple[int | None, int | None]]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        ltp, cp = prices.get(item.key, (None, None))
        out.append(
            {
                "key": item.key,
                "symbol": item.symbol,
                "exchange": item.exchange,
                "ltp": ltp,
                "change": change_pct(ltp, cp),
            }
        )
    return out


def build(
    *,
    screen_id: int,
    screen_name: str,
    run_key: str,
    at: int,
    session: int,
    decision: al.Decision,
    prices: Mapping[str, tuple[int | None, int | None]],
) -> dict[str, Any]:
    """The digest of one told run. ``prices`` maps a key to (ltp, previous close) in paise."""
    return {
        "schema": SCHEMA,
        "kind": "screener",
        "screen_id": screen_id,
        "screen": screen_name,
        "run_key": run_key,
        "at": at,
        "session": session,
        "matches": decision.matches,
        "entered": _named(decision.entered, prices),
        "more_entered": decision.more_entered,
        "exited": _named(decision.exited, prices),
        "more_exited": decision.more_exited,
        "held": decision.held,
        "late_at": decision.late_at,
        "pause": decision.pause,
    }


def sealed(fernet: Fernet, payload: Mapping[str, Any], *, now: float | None = None) -> bytes:
    """The digest as one Fernet token for the alerts' ``screener_digest`` op."""
    return seal(fernet, OP, now=now, principal="owner", args=dict(payload))


__all__ = ["OP", "SCHEMA", "build", "change_pct", "sealed"]
