"""What the feed socket should carry: the keys file, a persisted overlay, and each client's claims.

The keys file (``MODE KEY`` per line) is the base set and is re-read at every start, so editing it
and restarting takes effect. Changes made through ``/subscribe`` and ``/unsubscribe`` are stored in
``$STATE_DIR/subscriptions.json`` as an overlay (keys added or re-moded, base keys removed), so
they survive restarts without rewriting the file.

Claims (M18.1) let several local services share the socket without disturbing each other: each
client (``sudarshan-alerts``) states its whole set of keys with the mode it needs, and replaces it
in one step. The socket carries the union of the overlay and every client's claims, each key in
the highest mode anyone holds it in (ltpc < option_greeks < full < full_d30), so a client never
downgrades or removes a key another one needs, and ``/unsubscribe`` cannot take a claimed key
away. Upstox's caps are checked on that effective set; a change that would break one changes
nothing. Each client also has a quota (cross-design R1: screener 700, alerts 300, portfolio 200,
any other 50) and every claim together may hold at most 1,400 keys; a refusal names the client.
A client that has not renewed its claims for 7 days loses them, so a removed service cannot hold
keys forever. ``subscriptions.json`` is version 2 (``claims``); version 1 still loads.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from .store import atomic_write_json

log = logging.getLogger("sudarshan.feed")

MODES = ("ltpc", "full", "option_greeks", "full_d30")
#: A key's mode is the highest any holder asks for.
RANK = {"ltpc": 0, "option_greeks": 1, "full": 2, "full_d30": 3}
#: A client id: lower case, digits and dashes, as a service's name (``sudarshan-alerts``).
CLIENT_RE = re.compile(r"[a-z][a-z0-9-]{0,39}")
#: Claims not renewed for this long are dropped.
CLAIM_TTL_S = 7 * 86400
#: Keys one client may claim (cross-design R1); a client not named here may claim OTHER_QUOTA.
QUOTAS = {"sudarshan-screener": 700, "sudarshan-alerts": 300, "sudarshan-portfolio": 200}
OTHER_QUOTA = 50
#: Keys every client's claims may hold together; a key two clients hold counts once.
RING_CEILING = 1_400
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


class Change(NamedTuple):
    """What the socket must be told after the effective set changed."""

    new: dict[str, str]  # key -> mode, for keys new to the socket
    moved: dict[str, str]  # key -> its new mode, for keys whose effective mode changed
    gone: tuple[str, ...]  # keys nobody holds any more

    def batches(self) -> list[tuple[str, list[str], str | None]]:
        """(method, keys, mode) in sending order: sub, change_mode, then unsub."""
        out = []
        for method, keys in (("sub", self.new), ("change_mode", self.moved)):
            for mode in MODES:
                group = [key for key, m in keys.items() if m == mode]
                if group:
                    out.append((method, group, mode))
        if self.gone:
            out.append(("unsub", list(self.gone), None))
        return out


def diff(before: dict[str, str], after: dict[str, str]) -> Change:
    new = {k: m for k, m in after.items() if k not in before}
    moved = {k: m for k, m in after.items() if k in before and before[k] != m}
    return Change(new, moved, tuple(k for k in before if k not in after))


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


def validate_claims(client, keys) -> tuple[str, dict[str, str]]:
    """A client id and its ``{key: mode}`` set, checked like /subscribe; an empty set is allowed."""
    if not isinstance(client, str) or not CLIENT_RE.fullmatch(client):
        raise ValueError("the client id is lower case letters, digits and dashes, at most 40")
    if not isinstance(keys, dict):
        raise ValueError('keys must be a JSON object like {"NSE_EQ|INE002A01018": "full"}')
    if len(keys) > max(CAPS.values()):
        raise ValueError(f"at most {max(CAPS.values())} keys per client")
    clean: dict[str, str] = {}
    for key, mode in keys.items():
        if not isinstance(key, str) or not KEY_RE.fullmatch(key.strip()):
            raise ValueError(f"not an Upstox instrument key: {str(key)[:80]!r}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of: {', '.join(MODES)} (got {str(mode)[:20]!r})")
        clean[key.strip()] = mode
    return client, clean


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
        #: client -> {"renewed_at": epoch seconds, "keys": {key: mode}}
        self.claims: dict[str, dict] = {}

    @classmethod
    def load(cls, keys_file: Path, state_file: Path, now: float | None = None) -> Subscriptions:
        subs = cls(parse_keys_file(keys_file), state_file)
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            subs.added = {k: m for k, m in data.get("added", {}).items() if m in MODES}
            subs.removed = set(data.get("removed", []))
            subs.claims = _read_claims(data.get("claims", {}))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as exc:
            log.error("ignoring unreadable %s (%s): using the keys file only", state_file, exc)
        check_caps(subs.overlay())  # a hand-edited keys file can still break a cap
        subs.expire(time.time() if now is None else now)
        try:
            check_caps(subs.desired())
            check_ring(subs.claims)
        except CapError as exc:  # the feed still starts with its base and overlay
            log.error(
                "dropping every client's claims at start: %s (%s)", exc, ", ".join(subs.claims)
            )
            subs.claims = {}
            subs.save()
        return subs

    def overlay(self) -> dict[str, str]:
        """The keys file with the API's changes: what /subscribe and /unsubscribe manage."""
        result = {k: m for k, m in self.base.items() if k not in self.removed}
        result.update(self.added)
        return result

    def desired(self) -> dict[str, str]:
        """The effective set the socket carries: the overlay and every claim, highest mode."""
        result = self.overlay()
        for claim in self.claims.values():
            for key, mode in claim["keys"].items():
                if key not in result or RANK[mode] > RANK[result[key]]:
                    result[key] = mode
        return result

    def by_mode(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for key, mode in self.desired().items():
            groups.setdefault(mode, []).append(key)
        return groups

    def counts(self) -> dict[str, int]:
        counts = Counter(self.desired().values())
        return {mode: counts.get(mode, 0) for mode in MODES} | {"total": sum(counts.values())}

    def claim_counts(self) -> dict[str, int]:
        return {client: len(claim["keys"]) for client, claim in sorted(self.claims.items())}

    def subscribe(self, mode: str, keys: list[str]) -> Change:
        """Add or re-mode keys in the overlay. Raises CapError, changing nothing."""
        added, removed = dict(self.added), set(self.removed)
        for key in keys:
            removed.discard(key)
            if self.base.get(key) == mode:
                added.pop(key, None)  # back to exactly what the keys file says
            else:
                added[key] = mode
        return self._commit(added=added, removed=removed)

    def unsubscribe(self, keys: list[str]) -> tuple[list[str], Change]:
        """Take keys out of the overlay: (the keys it held, what the socket must be told). A
        key a client claims stays on the socket, in the mode its claims need."""
        held = self.overlay()
        gone = [k for k in keys if k in held]
        added, removed = dict(self.added), set(self.removed)
        for key in gone:
            added.pop(key, None)
            if key in self.base:
                removed.add(key)
        return gone, self._commit(added=added, removed=removed) if gone else Change({}, {}, ())

    def put_claims(self, client: str, keys: dict[str, str], now: float) -> Change:
        """Replace ``client``'s whole set (and renew it). Raises CapError naming the client,
        changing nothing, over its quota, the ring's ceiling or an Upstox cap."""
        limit = quota(client)
        if len(keys) > limit:
            raise CapError(f"{client} may claim at most {limit} keys; it asked for {len(keys)}")
        claims = {c: v for c, v in self.claims.items() if c != client}
        if keys:
            claims[client] = {"renewed_at": int(now), "keys": dict(keys)}
        try:
            check_ring(claims)
            return self._commit(claims=claims)
        except CapError as exc:
            raise CapError(f"{client}: {exc}") from None

    def drop_claims(self, client: str) -> Change:
        if client not in self.claims:
            return Change({}, {}, ())
        return self._commit(claims={c: v for c, v in self.claims.items() if c != client})

    def expire(self, now: float) -> Change:
        """Drop the claims of every client that has not renewed them for 7 days."""
        stale = [c for c, v in self.claims.items() if now - v["renewed_at"] > CLAIM_TTL_S]
        if not stale:
            return Change({}, {}, ())
        log.warning("dropping the claims of %s: not renewed for 7 days", ", ".join(stale))
        return self._commit(claims={c: v for c, v in self.claims.items() if c not in stale})

    def _commit(self, **parts) -> Change:
        """Apply new parts if the effective set stays within the caps; returns the change."""
        before = self.desired()
        kept = {name: getattr(self, name) for name in parts}
        for name, value in parts.items():
            setattr(self, name, value)
        after = self.desired()
        try:
            check_caps(after)
        except CapError:
            for name, value in kept.items():
                setattr(self, name, value)
            raise
        self.save()
        return diff(before, after)

    def save(self) -> None:
        data = {"version": 2, "added": self.added, "removed": sorted(self.removed),
                "claims": self.claims}  # fmt: skip
        try:
            atomic_write_json(self.state_file, data)
        except OSError as exc:  # the change still applies to this run
            log.error("could not persist subscriptions to %s: %s", self.state_file, exc)


def check_ring(claims: dict[str, dict]) -> None:
    """Every client's claims together hold at most RING_CEILING keys (a shared key once)."""
    held = len(set().union(*(claim["keys"] for claim in claims.values())))
    if held > RING_CEILING:
        raise CapError(
            f"the claims of every client would hold {held} keys; at most {RING_CEILING} together"
        )


def quota(client: str) -> int:
    """How many keys ``client`` may claim (cross-design R1)."""
    return QUOTAS.get(client, OTHER_QUOTA)


def _read_claims(raw) -> dict[str, dict]:
    """Version 2's claims, each one checked; a damaged entry, or one over its client's quota,
    is dropped, not the file."""
    claims: dict[str, dict] = {}
    for client, claim in raw.items() if isinstance(raw, dict) else ():
        try:
            client, keys = validate_claims(client, claim["keys"])
            renewed = claim["renewed_at"]
            if isinstance(renewed, bool) or not isinstance(renewed, int | float):
                raise ValueError("renewed_at is not a time")
            if len(keys) > quota(client):
                raise ValueError(f"{len(keys)} keys, over its quota of {quota(client)}")
        except (ValueError, TypeError, KeyError) as exc:
            log.error("ignoring the stored claims of %r: %s", str(client)[:40], exc)
            continue
        if keys:
            claims[client] = {"renewed_at": int(renewed), "keys": keys}
    return claims
