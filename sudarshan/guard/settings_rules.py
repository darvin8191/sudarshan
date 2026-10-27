"""The owner's risk settings as screens show them, and the rules for changing them (M20.7).

Every guard ships off (amendment R4). Tightening needs nothing; loosening needs the confirm
phrase: raising a limit, widening a band, raising a cap, turning a guard off, letting more
orders through the burst brake, keeping remote MCP access open, or changing the phrase itself.
The phrase is never sent to a screen.
"""

from __future__ import annotations

import re
from typing import Any

from utils.logging import get_logger

from . import store as risk_store
from . import words
from .store import RiskSettings

logger = get_logger(__name__)

#: field: (kind, smallest, largest)
NUMBERS = {
    "daily_loss_limit": (float, 1.0, 1e9),
    "band_opt_pct": (float, 1.0, 100.0),
    "band_opt_min_rs": (float, 0.0, 10_000.0),
    "band_fut_pct": (float, 0.1, 50.0),
    "band_cash_pct": (float, 0.1, 50.0),
    "max_lots_fo": (int, 1, 10_000),
    "max_qty_cash": (int, 1, 10_000_000),
    "burst_n": (int, 1, 10_000),
    "burst_window_s": (float, 1.0, 3600.0),
}
FLAGS = frozenset({"band_on", "band_both_sides", "size_on", "burst_on", "revoke_mcp"})
PHRASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,38}[A-Za-z0-9]$|^[A-Za-z0-9]$")
#: Fields where a larger value lets more through; for these a smaller one is looser.
LOOSER_WHEN_SMALLER = frozenset({"burst_window_s"})


def public(settings: RiskSettings) -> dict[str, Any]:
    """The settings for a screen: every field but the phrase, which is only said to be set."""
    shown = {name: getattr(settings, name) for name in risk_store.SETTING_FIELDS}
    shown.pop("confirm_phrase", None)
    shown["phrase_set"] = bool(settings.confirm_phrase)
    shown["updated_at"] = settings.updated_at.isoformat() if settings.updated_at else None
    shown["updated_by"] = settings.updated_by
    return shown


def parse(values: dict) -> tuple[dict[str, Any], str | None]:
    """The changes asked for, checked; or the sentence saying which one cannot be saved."""
    if not isinstance(values, dict):
        return {}, words.SETTINGS_NOT_UNDERSTOOD
    changes: dict[str, Any] = {}
    for name, raw in values.items():
        if name in FLAGS:
            if not isinstance(raw, bool):
                return {}, words.setting_invalid(name)
            changes[name] = raw
        elif name in NUMBERS:
            kind, low, high = NUMBERS[name]
            if raw is None and name == "daily_loss_limit":
                changes[name] = None  # off
                continue
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return {}, words.setting_invalid(name)
            if not low <= raw <= high or (kind is int and raw != int(raw)):
                return {}, words.setting_invalid(name)
            changes[name] = kind(raw)
        elif name == "confirm_phrase":
            phrase = str(raw or "").strip()
            if not PHRASE.match(phrase):
                return {}, words.PHRASE_INVALID
            changes[name] = phrase
        else:
            return {}, words.SETTINGS_NOT_UNDERSTOOD
    return changes, None


def loosens(old: RiskSettings, changes: dict[str, Any]) -> list[str]:
    """The changes that let more risk through than the settings do now."""
    looser = []
    for name, new in changes.items():
        before = getattr(old, name)
        if new == before:
            continue
        if name == "confirm_phrase":
            looser.append(name)
        elif name == "daily_loss_limit":
            if before is not None and (new is None or new > before):
                looser.append(name)
        elif name in FLAGS:
            if before and not new:
                looser.append(name)  # a guard, both-sides checking or MCP revoking turned off
        elif name in LOOSER_WHEN_SMALLER:
            if new < before:
                looser.append(name)
        elif new > before:
            looser.append(name)
    return looser


def change(values: dict, *, phrase: object, by: str) -> tuple[int, str, RiskSettings | None]:
    """Save the changes asked for; loosening needs the confirm phrase.

    The change is judged against the version read here and saved only while that version is
    still the saved one (store.save_settings), so a change made meanwhile from another screen
    cannot turn a tightening judged here into a loosening without the phrase.

    Returns:
        ``(status, sentence, settings or None)``: 200; 400, not saved and why; 409, changed
        meanwhile, not saved; 503, could not be read or saved.
    """
    from .killswitch import phrase_matches

    try:
        settings = risk_store.STORE.settings()
    except Exception:
        logger.exception("The risk settings could not be read for a change")
        return 503, words.KS_SETTINGS_UNREADABLE, None
    changes, problem = parse(values)
    if problem:
        return 400, problem, settings
    changes = {k: v for k, v in changes.items() if getattr(settings, k) != v}
    if not changes:
        return 200, words.SETTINGS_UNCHANGED, settings
    looser = loosens(settings, changes)
    if looser and not phrase_matches(phrase, settings.confirm_phrase):
        return 400, words.LOOSEN_NEEDS_PHRASE, settings
    try:
        saved = risk_store.STORE.save_settings(by, since=settings.updated_at, **changes)
    except risk_store.SettingsChanged:
        return 409, words.SETTINGS_CHANGED_MEANWHILE, None
    except Exception:
        logger.exception("The risk settings could not be saved")
        return 503, words.SETTINGS_NOT_SAVED, None
    shown = {k: ("(changed)" if k == "confirm_phrase" else v) for k, v in changes.items()}
    try:
        risk_store.STORE.audit(
            "settings_changed",
            words.settings_changed(sorted(changes), bool(looser)),
            surface="app",
            actor=by,
            detail={"changes": shown, "looser": looser},
        )
    except Exception:
        logger.exception("A risk settings change could not be audited")
    return 200, words.SETTINGS_SAVED, saved
