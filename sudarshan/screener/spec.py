"""A screen's JSON (schema 1): checked in the owner's words, stored with its defaults, compiled.

``check(fields)`` takes what the phone sends and returns the form the store keeps, with every
default filled in and every number as text, or raises ``SpecError(field, words)``: the field it is
about and one sentence saying what to change. ``compile_plan(stored)`` turns a stored screen into the
``Plan`` the engine runs. Checking is idempotent: ``check(check(x)) == check(x)``.

Lines that are off stay in the stored form (and are checked, so the stored form is always sound)
but are not in the plan: ``Plan.clauses`` and ``Plan.root`` hold the lines that are on. A group
left with no line on is refused, so no run ever meets an empty one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from sudarshan.screener import catalog as cat
from sudarshan.screener.operand import Scope, SpecError, account, clause, is_obv, operand, whole
from sudarshan.screener.types import DAY, STATE, Clause, Group, Node, Not, Plan

__all__ = ["SpecError", "build", "check", "compile_plan"]

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,19}$")
_TOP = ("schema", "name", "universe", "run", "where", "rank", "target", "alerts")
_GROUPS = ("all", "any", "none", "not")
_MISSING = object()
NO_CONDITION = "Turn on at least one condition."
DEPTH_WORDS = "Groups can go three levels deep."
EMPTY_GROUP = "Turn on at least one condition in this group, or delete the group."


def check(fields: Mapping) -> dict:
    """The stored form of a screen, or ``SpecError``."""
    return build(fields)[0]


def compile_plan(stored: Mapping) -> Plan:
    """The plan of a stored (or any valid) screen."""
    return build(stored)[1]


def build(fields: Mapping) -> tuple[dict, Plan]:
    if fields.__class__ is not dict:
        raise SpecError("screen", "This screen could not be read. Open it again.")
    for key in fields:
        if key not in _TOP:
            raise SpecError(key, f"This screen has a field the screener does not know: '{key}'.")
    schema = fields.get("schema", cat.SCHEMA)
    if schema != cat.SCHEMA or schema.__class__ is not int:
        raise SpecError(
            "schema", "This screen needs a newer SUDARSHAN. Update, then open it again."
        )
    name = _name(fields.get("name"))
    universe = _universe(fields.get("universe"))
    run = _run(fields.get("run"), universe)
    scope = Scope(run["on"], run.get("tf", DAY), universe["kind"])
    walker = _Walker(scope, _explicit_ids(fields.get("where")))
    where, root = walker.walk(fields.get("where"), 0, "where")
    if root is None:
        raise SpecError("where", NO_CONDITION)
    rank, rank_plan = _rank(fields.get("rank", _MISSING), scope)
    if len(scope.instances) > cat.MAX_INSTANCES:
        raise SpecError(
            "where",
            f"A screen can use {cat.MAX_INSTANCES} different indicators at most. Remove one, "
            "or use the same settings in two places.",
        )
    stored = {
        "schema": cat.SCHEMA,
        "name": name,
        "universe": universe,
        "run": run,
        "where": where,
        "rank": rank,
        "target": _target(fields.get("target", _MISSING), name),
        "alerts": _alerts(fields.get("alerts")),
    }
    plan = Plan(
        on=run["on"],
        tf=scope.tf,
        root=root,
        clauses=tuple(walker.clauses),
        rank=rank_plan,
        rank_order=rank["order"],
        instances=tuple(sorted(scope.instances, key=repr)),
        need=dict(scope.need),
        uses=frozenset(scope.uses),
    )
    return stored, plan


def _only(raw: dict, allowed: tuple, path: str) -> None:
    for key in raw:
        if key not in allowed:
            raise SpecError(
                path, f"This part of the screen has a field the screener does not know: '{key}'."
            )


def _name(raw) -> str:
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(raw or "")).split())
    if not text:
        raise SpecError("name", "Give the screen a name.")
    if len(text) > cat.NAME_MAX:
        raise SpecError("name", f"Use a shorter name ({cat.NAME_MAX} characters at most).")
    return text


def _universe(raw) -> dict:
    words = "Pick where to look: F&O stocks, futures, a NIFTY list, option strikes or a watchlist."
    if raw.__class__ is not dict or raw.get("kind") not in cat.UNIVERSES:
        raise SpecError("universe", words)
    kind = raw["kind"]
    extra = {"options": "underlying", "index_list": "id", "watchlist": "list_id"}.get(kind)
    _only(raw, ("kind", extra) if extra else ("kind",), "universe")
    if kind == "options":
        if raw.get("underlying") not in cat.UNDERLYINGS:
            raise SpecError("universe", "Pick NIFTY or BANKNIFTY for the option strikes.")
        return {"kind": kind, "underlying": raw["underlying"]}
    if kind == "index_list":
        if raw.get("id").__class__ is not str or raw["id"] not in cat.INDEX_LISTS:
            raise SpecError("universe", "Pick one of the NIFTY lists.")
        return {"kind": kind, "id": raw["id"]}
    if kind == "watchlist":
        return {"kind": kind, "list_id": _list_id(raw.get("list_id"), "universe")}
    return {"kind": kind}


def _list_id(raw, path: str) -> int:
    if raw.__class__ is not int or not 0 < raw < 10**9:
        raise SpecError(path, "Pick a watchlist.")
    return raw


def _run(raw, universe: dict) -> dict:
    raw = {"on": "close", "tf": 15} if raw is None else raw
    if raw.__class__ is not dict or raw.get("on") not in cat.RUN_ON:
        raise SpecError("run", "Pick when to check: at each candle close, or after the close.")
    _only(raw, ("on", "tf"), "run")
    if raw["on"] == "after_close":
        run = {"on": "after_close"}
    else:
        tf = raw.get("tf", 15)
        if tf.__class__ is not int or tf not in cat.RUN_TFS:
            raise SpecError("run", "Check at the close of 1, 3, 5, 15, 30 or 60-minute candles.")
        run = {"on": "close", "tf": tf}
    if universe["kind"] == "index_list":
        name, followed = cat.INDEX_LISTS[universe["id"]]
        if not followed and run["on"] != "after_close":
            raise SpecError(
                "run",
                f"{name} is checked after the close (16:05). Checks at each candle close cover the "
                "F&O stocks, NIFTY 50, NIFTY BANK and the NIFTY and BANKNIFTY option strikes.",
            )
    return run


class _Walker:
    """Walks the condition tree once: checks it, keeps what is on, counts what is there."""

    def __init__(self, scope: Scope, reserved: set):
        self.scope = scope
        self.reserved = reserved
        self.clauses: list[Clause] = []
        self.count = 0
        self.used: set[str] = set()
        self.auto = 0

    def walk(self, raw, depth: int, path: str) -> tuple[dict, Node | None]:
        """(stored, compiled); compiled is None when nothing in it is on."""
        if raw.__class__ is not dict:
            raise SpecError(
                path, NO_CONDITION if depth == 0 else "A group holds conditions or other groups."
            )
        kinds = [key for key in _GROUPS if key in raw]
        if not kinds:
            return self._clause(raw)
        if len(kinds) > 1 or "op" in raw or "left" in raw:
            raise SpecError(
                path, "A group is either match all, match any, match none or not. Pick one."
            )
        kind = kinds[0]
        _only(raw, (kind,), path)
        if depth + 1 > cat.MAX_DEPTH:
            raise SpecError(path, DEPTH_WORDS)
        if kind == "not":
            stored, node = self.walk(raw["not"], depth + 1, path)
            if node is None:
                raise SpecError(path, EMPTY_GROUP)
            return {"not": stored}, Not(node)
        items = raw[kind]
        if items.__class__ is not list or not items:
            raise SpecError(path, "A group needs at least one condition.")
        stored_items, nodes = [], []
        for item in items:
            stored, node = self.walk(item, depth + 1, path)
            stored_items.append(stored)
            if node is not None:
                nodes.append(node)
        if not nodes:
            raise SpecError(path, NO_CONDITION if depth == 0 else EMPTY_GROUP)
        return {kind: stored_items}, Group(kind, tuple(nodes))

    def _clause(self, raw: dict) -> tuple[dict, Node | None]:
        self.count += 1
        if self.count > cat.MAX_CLAUSES:
            raise SpecError(
                "where", f"A screen can hold {cat.MAX_CLAUSES} conditions at most. Delete some."
            )
        cid = self._id(raw.get("id"))
        on = raw.get("on", True)
        if on.__class__ is not bool:
            raise SpecError(cid, "A condition is either on or off.")
        _only(raw, ("id", "on", "left", "op", "right", "args"), cid)
        # A line that is off is checked on its own, so it adds nothing to the screen's reach.
        scope = self.scope if on else Scope(self.scope.on, self.scope.tf, self.scope.universe)
        stored, node = clause(raw, scope, cid, cid)
        stored["on"] = on
        if not on:
            return stored, None
        self.clauses.append(node)
        return stored, node

    def _id(self, raw) -> str:
        if raw is None:
            while True:
                self.auto += 1
                cid = f"c{self.auto}"
                if cid not in self.reserved:
                    break
        elif raw.__class__ is str and _ID.match(raw):
            cid = raw
        else:
            raise SpecError(
                "where", "A condition's id must be letters and digits, 20 characters at most."
            )
        if cid in self.used:
            raise SpecError("where", "Two conditions share the same id. Open the screen again.")
        self.used.add(cid)
        return cid


def _explicit_ids(raw) -> set:
    """Every id the owner's tree names, so an automatic id never takes one of them."""
    found: set = set()
    if raw.__class__ is dict:
        if raw.get("id").__class__ is str:
            found.add(raw["id"])
        for key in _GROUPS:
            found |= _explicit_ids(raw.get(key))
    elif raw.__class__ is list:
        for item in raw:
            found |= _explicit_ids(item)
    return found


def _rank(raw, scope: Scope):
    if raw is _MISSING:
        raw = {"by": {"field": "change_pct", "tf": DAY}, "order": "desc"}
    elif raw is None:  # no ranking: the matches go by symbol
        raw = {"by": None}
    if raw.__class__ is not dict:
        raise SpecError("rank", "Pick what to rank the matches by, or nothing.")
    _only(raw, ("by", "order"), "rank")
    order = raw.get("order", "desc")
    if order not in ("asc", "desc"):
        raise SpecError("rank", "Rank from the highest or from the lowest.")
    if raw.get("by") is None:
        return {"by": None, "order": order}, None
    stored, item = operand(raw["by"], scope, "rank.by", "Pick what to rank the matches by.")
    if item.kind == "num" or item.unit == STATE:
        raise SpecError("rank.by", "Rank by a value such as a price, a volume or an indicator.")
    if is_obv(item):
        raise SpecError(
            "rank.by", "On-balance volume cannot rank matches: its level means nothing."
        )
    account(item, 0, scope, "rank.by")
    return {"by": stored, "order": order}, item


def _target(raw, name: str):
    if raw is None:
        return None
    default = f"Screener: {name}"[: cat.LIST_NAME_MAX]
    if raw is _MISSING:
        raw = {}
    if raw.__class__ is not dict:
        raise SpecError("target", "Pick a watchlist for the matches, or none.")
    _only(raw, ("list_id", "create_name", "grace", "max_adds_per_run"), "target")
    list_id = raw.get("list_id")
    if list_id is not None:
        list_id = _list_id(list_id, "target")
    create = raw.get("create_name", default if list_id is None else None)
    if list_id is not None:
        create = None
    elif create is None:
        raise SpecError("target", "Pick a watchlist, or give the new one a name.")
    else:
        create = " ".join(str(create).split())[: cat.LIST_NAME_MAX]
        if not create:
            raise SpecError("target", "Pick a watchlist, or give the new one a name.")
    return {
        "list_id": list_id,
        "create_name": create,
        "grace": _grace(raw.get("grace")),
        "max_adds_per_run": whole(
            raw.get("max_adds_per_run", cat.DEFAULT_MAX_ADDS), "target.max_adds_per_run", 1,
            cat.MAX_ADDS, "The most to add in one check",
        ),
    }  # fmt: skip


def _grace(raw) -> dict:
    raw = {"mode": "runs", "n": cat.DEFAULT_GRACE_RUNS} if raw is None else raw
    if raw.__class__ is not dict or raw.get("mode") not in cat.GRACE_MODES:
        raise SpecError(
            "target.grace", "Pick how long a miss is forgiven: some checks, the day, or never."
        )
    _only(raw, ("mode", "n"), "target.grace")
    if raw["mode"] != "runs":
        return {"mode": raw["mode"]}
    n = whole(raw.get("n", cat.DEFAULT_GRACE_RUNS), "target.grace", 1, 50, "The number of checks")
    return {"mode": "runs", "n": n}


def _alerts(raw) -> dict:
    raw = {} if raw is None else raw
    if raw.__class__ is not dict:
        raise SpecError("alerts", "Check the alert settings.")
    keys = ("on", "min_matches", "unique_per_day", "skip_zero", "tell_exits", "after_trigger",
            "cooldown_runs", "max_per_day_per_symbol", "channels", "columns")  # fmt: skip
    _only(raw, keys, "alerts")
    out = {}
    for key, default in (
        ("on", True),
        ("unique_per_day", True),
        ("skip_zero", True),
        ("tell_exits", False),
    ):
        value = raw.get(key, default)
        if value.__class__ is not bool:
            raise SpecError("alerts", "Each alert switch is either on or off.")
        out[key] = value
    out["min_matches"] = whole(
        raw.get("min_matches", 1), "alerts", 1, 1000, "The fewest matches to tell"
    )
    out["after_trigger"] = raw.get("after_trigger", "continue")
    if out["after_trigger"] not in cat.AFTER_TRIGGER:
        raise SpecError("alerts", "After the first alert, continue or pause.")
    out["cooldown_runs"] = whole(
        raw.get("cooldown_runs", 0), "alerts", 0, 1000, "The wait between alerts"
    )
    out["max_per_day_per_symbol"] = whole(
        raw.get("max_per_day_per_symbol", cat.DEFAULT_ALERT_CAP),
        "alerts",
        1,
        50,
        "The most alerts for one stock a day",
    )
    out["channels"] = _channels(raw.get("channels"))
    columns = raw.get("columns", ["ltp", "change_pct"])
    if (
        columns.__class__ is not list
        or not columns
        or any(item.__class__ is not str or item not in cat.ALERT_COLUMNS for item in columns)
        or len(set(columns)) != len(columns)
    ):
        raise SpecError(
            "alerts", "Pick which figures the alert shows: price, % change, or the rank value."
        )
    out["columns"] = list(columns)
    return {key: out[key] for key in keys}


def _channels(raw):
    if raw is None:
        return None
    if (
        raw.__class__ is dict
        and set(raw) == {"telegram", "app"}
        and all(value.__class__ is bool for value in raw.values())
    ):
        return {"telegram": raw["telegram"], "app": raw["app"]}
    raise SpecError("alerts", "Pick where to send it: Telegram, the app, or both.")
