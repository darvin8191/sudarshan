"""List alerts, the rules (M8.14): what a group of alerts does when its list changes. Pure.

No I/O of any kind: no store, no feed, no clock, no logging. Every input is an argument and every
decision a return value, so the sync (desk.py, runtime.py) only translates: it reads the list's
members and the alerts in use, asks ``plan`` what to do, and applies the answer in one store
transaction. golden vectors: tests/vectors/groups.json.

A group makes one alert per symbol, so it needs one instrument and one alert for each. Market
alerts cap both numbers (config.py: 200 instruments, 500 Active alerts) and the fit check holds a
group to both, naming both: a group that does not fit is refused, never partly made. A list that
grows later is covered in list order as far as both limits allow, and the card says how far.

A removed symbol's claim at the feed is released only when no other alert holds that instrument
(``release``). When the watchlist feature is off the group freezes with its last members; a list
that was deleted stops it; a ranked list cannot carry one.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from sudarshan.alerts.messages import quantity

#: The kinds that make sense across many symbols (a price level is per symbol).
KINDS = ("move", "day_break", "vwap")

RANKED = (
    "A ranked list changes with every read. Set the alert on a list or a built-in list instead."
)
NOT_A_LIST = "This kind of list cannot carry an alert. Set it on a list or a built-in list."
LEFT_OUT_FIXED = "ATM and strike rows change contract during the day and are left out: {n}."
LEFT_OUT_OTHER = "Rows without a contract to follow are left out: {n}."
SWITCHED_OFF = "The watchlist feature is switched off, so this alert keeps its last {n} symbols."
LIST_DELETED = "The list {name} was deleted, so this alert stopped."
OK, OFF, GONE = "ok", "off", "gone"


class Row(NamedTuple):
    """One list member as the sealed members route answers it."""

    instrument_key: str
    symbol: str = ""
    exchange: str = ""
    tick_size: str = ""
    expiry: str | None = None
    #: A current-month future's last moment (epoch ms): ask the list again when it passes.
    valid_until: int | None = None

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Row:
        return cls(row["instrument_key"], row.get("symbol", ""), row.get("exchange", ""),
                   row.get("tick_size", ""), row.get("expiry"), row.get("valid_until"))  # fmt: skip


class Member(NamedTuple):
    """An alert the group already has."""

    alert_id: int
    key: str


class Plan(NamedTuple):
    action: str  # "sync", "freeze" or "stop"
    add: tuple[Row, ...] = ()
    remove: tuple[int, ...] = ()
    #: Instrument keys no other alert holds once ``remove`` is done: their claims are released.
    release: tuple[str, ...] = ()
    covered: int = 0
    total: int = 0
    left_out: tuple[str, ...] = ()
    #: The words for the card (a frozen or stopped group, or "Covers 180 of 195: ...").
    note: str = ""
    #: When to ask the list again (the earliest current-month future's last moment), or None.
    ask_again_at: int | None = None


class Fit(NamedTuple):
    ok: bool
    words: str


def ref_refusal(ref: str) -> str | None:
    """Why a list reference cannot carry a group; None when it can."""
    kind = ref.partition(":")[0]
    if kind == "ranked":
        return RANKED
    return None if kind in ("list", "preset", "flag") else NOT_A_LIST


def left_out_words(items: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """The rows the route left out, in words: ATM and strike rows together, others apart."""
    fixed = other = 0
    for item in items:
        if item.get("why") in ("atm", "strike"):
            fixed += 1
        else:
            other += 1
    out = []
    if fixed:
        out.append(LEFT_OUT_FIXED.format(n=quantity(fixed)))
    if other:
        out.append(LEFT_OUT_OTHER.format(n=quantity(other)))
    return tuple(out)


def check(
    new_keys: Sequence[str], in_use_keys: Collection[str], in_use_alerts: int,
    max_instruments: int, max_active: int,
) -> Fit:  # fmt: skip
    """Does a group of one alert per key fit both limits? ``in_use_*`` is what Active alerts
    hold now. The words carry both numbers, fit or not."""
    keys = set(new_keys)
    new_instruments = len(keys - set(in_use_keys))
    used_keys, alerts = len(set(in_use_keys)), len(new_keys)
    free_keys, free_alerts = max_instruments - used_keys, max_active - in_use_alerts
    if new_instruments <= free_keys and alerts <= free_alerts:
        return Fit(True, (
            f"This alert needs {quantity(len(keys))} instruments and {quantity(alerts)} alerts. "
            f"In use now: {quantity(used_keys)} of {quantity(max_instruments)} instruments and "
            f"{quantity(in_use_alerts)} of {quantity(max_active)} Active alerts. It fits."))  # fmt: skip
    short = []
    if alerts > free_alerts:
        short.append(f"it needs {quantity(alerts)} alerts and {quantity(max(free_alerts, 0))} "
                     f"of the {quantity(max_active)} are free")  # fmt: skip
    if new_instruments > free_keys:
        short.append(f"it needs {quantity(new_instruments)} more instruments and "
                     f"{quantity(max(free_keys, 0))} of the {quantity(max_instruments)} are free")  # fmt: skip
    return Fit(False, f"It does not fit: {'; and '.join(short)}. Nothing was made.")


def fit_check(
    max_instruments: int, max_active: int
) -> Callable[[frozenset[str], int, list[str]], str | None]:
    """The store's ``fit`` callback: refusal words, or None to admit."""

    def fit(in_use_keys: frozenset[str], in_use_alerts: int, new_keys: list[str]) -> str | None:
        result = check(new_keys, in_use_keys, in_use_alerts, max_instruments, max_active)
        return None if result.ok else result.words

    return fit


def release(
    holders: Mapping[str, Collection[int]], removed: Collection[int]
) -> tuple[str, ...]:  # fmt: skip
    """The instrument keys whose claim can go once alerts ``removed`` are deleted: those no
    other alert holds. ``holders`` maps each key to the ids of every alert holding it."""
    gone = set(removed)
    return tuple(sorted(key for key, ids in holders.items()
                        if ids and set(ids) <= gone))  # fmt: skip


def covers_words(covered: int, total: int, max_instruments: int, max_active: int) -> str:
    if covered >= total:
        return ""
    return (f"Covers {quantity(covered)} of {quantity(total)}: alerts can follow at most "
            f"{quantity(max_instruments)} instruments and {quantity(max_active)} Active alerts.")  # fmt: skip


def diff(members: Sequence[Member], rows: Sequence[Row]) -> tuple[tuple[Row, ...], tuple[int, ...]]:
    """(rows to add, member ids to remove), by instrument key. A key listed twice counts once,
    and a second member on one key goes. Kept members are untouched."""
    wanted: dict[str, Row] = {}
    for row in rows:
        wanted.setdefault(row.instrument_key, row)
    have: set[str] = set()
    remove = []
    for member in sorted(members, key=lambda m: m.alert_id):
        if member.key in wanted and member.key not in have:
            have.add(member.key)
        else:
            remove.append(member.alert_id)
    return tuple(row for key, row in wanted.items() if key not in have), tuple(remove)


def plan(
    state: str, rows: Sequence[Row], members: Sequence[Member],
    holders: Mapping[str, Collection[int]], *, name: str, active: bool,
    max_instruments: int, max_active: int, left_out: Iterable[Mapping[str, Any]] = (),
) -> Plan:  # fmt: skip
    """What the sync does with the list's answer. ``state`` is ok, off (the feature is switched
    off: freeze, never an empty list or an error) or gone (the list was deleted: stop).
    ``holders`` is every Active alert's instrument key -> alert ids; ``active`` is whether the
    group is Active (a Paused group adds without being held to the limits)."""
    if state == OFF:
        return Plan("freeze", covered=len(members), total=len(members),
                    note=SWITCHED_OFF.format(n=quantity(len(members))))  # fmt: skip
    if state == GONE:
        return Plan("stop", note=LIST_DELETED.format(name=name))
    add, remove = diff(members, rows)
    keep = len(members) - len(remove)
    total = keep + len(add)
    freed = release(holders, remove)
    left = {k: {i for i in ids if i not in set(remove)} for k, ids in holders.items()}
    keys = {k for k, ids in left.items() if ids}
    alerts = sum(len(ids) for ids in left.values())
    admitted = []
    for row in add:
        if active:
            if alerts + 1 > max_active or (
                row.instrument_key not in keys and len(keys) + 1 > max_instruments
            ):
                continue
            alerts += 1
            keys.add(row.instrument_key)
        admitted.append(row)
    covered = keep + len(admitted)
    ends = [r.valid_until for r in rows if r.valid_until is not None]
    return Plan("sync", tuple(admitted), remove, freed, covered, total, left_out_words(left_out),
                covers_words(covered, total, max_instruments, max_active),
                min(ends) if ends else None)  # fmt: skip
