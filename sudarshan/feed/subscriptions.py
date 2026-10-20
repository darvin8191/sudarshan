"""What the feed socket should carry: the keys file as a base, plus a persisted runtime overlay.

The keys file (``MODE KEY`` per line) is the base set and is re-read at every start, so editing it
and restarting takes effect. Changes made through the HTTP API are stored in
``$STATE_DIR/subscriptions.json`` as an overlay (keys added or re-moded, base keys removed), so
they survive restarts without rewriting the file.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

from .store import atomic_write_json

log = logging.getLogger("sudarshan.feed")

MODES = ("ltpc", "full", "option_greeks", "full_d30")
# Upstox Feed V3 per-connection caps ("Individual" column of the limits table).
CAPS = {"ltpc": 5000, "full": 2000, "option_greeks": 3000, "full_d30": 50}
# The "Combined" column applies once more than one mode is live. Upstox does not define it more
# precisely, so this takes the conservative reading OpenAlgo's own client uses: the total across
# all modes may not exceed the smallest combined figure among the modes in use.
COMBINED_CAPS = {"ltpc": 2000, "full": 1500, "option_greeks": 2000, "full_d30": 1500}
# SEGMENT|TOKEN, e.g. NSE_EQ|INE002A01018, NSE_FO|45450, NSE_INDEX|Nifty 50
KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{1,15}\|[^|\x00-\x1f\x7f]{1,60}")


class CapError(ValueError):
    """The change would break an Upstox per-connection subscription cap."""


def check_caps(desired: dict[str, str]) -> None:
    counts = Counter(desired.values())
    for mode, n in counts.items():
        if n > CAPS[mode]:
            raise CapError(
                f"{n} keys in {mode} exceeds the Upstox cap of {CAPS[mode]} per connection"
            )
    if len(counts) > 1:
        budget = min(COMBINED_CAPS[m] for m in counts)
        total = sum(counts.values())
        if total > budget:
            raise CapError(
                f"{total} keys across {', '.join(sorted(counts))} exceeds the Upstox combined cap "
                f"of {budget} per connection"
            )


def validate(mode, keys, need_mode: bool = True) -> list[str]:
    """Check an API request; returns the keys stripped and de-duplicated (order kept)."""
    if need_mode and mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    if not isinstance(keys, list) or not keys:
        raise ValueError("keys must be a non-empty JSON list of instrument keys")
    if len(keys) > max(CAPS.values()):
        raise ValueError(f"at most {max(CAPS.values())} keys per request")
    clean = []
    for key in keys:
        if not isinstance(key, str) or not KEY_RE.fullmatch(key.strip()):
            raise ValueError(f"not an Upstox instrument key: {str(key)[:80]!r}")
        clean.append(key.strip())
    return list(dict.fromkeys(clean))


def parse_keys_file(path: Path) -> dict[str, str]:
    """``MODE KEY`` per line; ``#`` starts a comment; a later line for the same key wins."""
    desired: dict[str, str] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or parts[0] not in MODES or not KEY_RE.fullmatch(parts[1].strip()):
            raise ValueError(f"{path}:{n}: expected 'MODE KEY' with MODE one of {', '.join(MODES)}")
        desired[parts[1].strip()] = parts[0]
    return desired


class Subscriptions:
    def __init__(self, base: dict[str, str], state_file: Path):
        self.base, self.state_file = base, state_file
        self.added: dict[str, str] = {}  # key -> mode, set through the API
        self.removed: set[str] = set()  # base keys unsubscribed through the API

    @classmethod
    def load(cls, keys_file: Path, state_file: Path) -> Subscriptions:
        subs = cls(parse_keys_file(keys_file), state_file)
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            subs.added = {k: m for k, m in data.get("added", {}).items() if m in MODES}
            subs.removed = set(data.get("removed", []))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as exc:
            log.error("ignoring unreadable %s (%s): using the keys file only", state_file, exc)
        check_caps(subs.desired())  # a hand-edited keys file can still break a cap
        return subs

    def desired(self) -> dict[str, str]:
        result = {k: m for k, m in self.base.items() if k not in self.removed}
        result.update(self.added)
        return result

    def by_mode(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for key, mode in self.desired().items():
            groups.setdefault(mode, []).append(key)
        return groups

    def counts(self) -> dict[str, int]:
        counts = Counter(self.desired().values())
        return {mode: counts.get(mode, 0) for mode in MODES} | {"total": sum(counts.values())}

    def subscribe(self, mode: str, keys: list[str]) -> tuple[list[str], list[str]]:
        """Returns (keys new to the socket, keys moving from another mode). Raises CapError."""
        current = self.desired()
        new = [k for k in keys if k not in current]
        moved = [k for k in keys if k in current and current[k] != mode]
        check_caps(current | dict.fromkeys(keys, mode))
        for key in keys:
            self.removed.discard(key)
            if self.base.get(key) == mode:
                self.added.pop(key, None)  # back to exactly what the keys file says
            else:
                self.added[key] = mode
        self.save()
        return new, moved

    def unsubscribe(self, keys: list[str]) -> list[str]:
        current = self.desired()
        gone = [k for k in keys if k in current]
        for key in gone:
            self.added.pop(key, None)
            if key in self.base:
                self.removed.add(key)
        if gone:
            self.save()
        return gone

    def save(self) -> None:
        try:
            atomic_write_json(
                self.state_file,
                {"version": 1, "added": self.added, "removed": sorted(self.removed)},
            )
        except OSError as exc:  # the change still applies to this run
            log.error("could not persist subscriptions to %s: %s", self.state_file, exc)
