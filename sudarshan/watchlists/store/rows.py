"""Follow rows in the overlay (M8.10, M8.11, W15): the rows of ``wl_row`` and the rule that makes
one valid. The functions run inside the caller's ``with open_session() as s, s.begin():`` after
``store.sections.claim`` has moved the list's version, so the list's rows change under the same
compare-and-set as its sections. Pure data in and out: a rule is a plain dict, a refusal is
``Bad(field, words)``; nothing here reads the master or the clock (``now_ms`` is the store's)."""

from __future__ import annotations

from fractions import Fraction

from sqlalchemy import func, select

from sudarshan.watchlists import contract, schema
from sudarshan.watchlists.schema import OWNER
from sudarshan.watchlists.store import sections as _sections
from sudarshan.watchlists.words import rows as words

FIELDS = (
    "kind", "underlying", "underlying_exchange", "expiry_rule", "strike_rule", "offset",
    "strike", "opt_right",
)  # fmt: skip
_LATER = ("MCX", "NCDEX", "NCO", "CDS", "BCD", "GLOBAL_INDEX")


class Bad(Exception):
    """A rule that cannot be stored: the field it is about and the owner's sentence."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field, self.message = field, message


def as_rule(row) -> dict:
    """A stored row as the rule dict ``normalize`` takes (the strike in rupees)."""
    paise = row.strike_paise
    return {
        "kind": row.kind, "underlying": row.underlying,
        "underlying_exchange": row.underlying_exchange, "expiry_rule": row.expiry_rule,
        "strike_rule": row.strike_rule, "offset": row.offset,
        "strike": None if paise is None else paise / 100, "opt_right": row.opt_right,
    }  # fmt: skip


def _paise(strike) -> int:
    try:
        exact = Fraction(str(strike)) * 100
    except (ValueError, ZeroDivisionError):
        raise Bad("strike", words.STRIKE_BAD) from None
    if exact <= 0 or exact.denominator != 1 or exact > 10**12:
        raise Bad("strike", words.STRIKE_BAD)
    return int(exact)


def normalize(base: dict | None, patch: dict) -> dict:
    """The stored shape of ``patch`` laid over ``base`` (a stored rule, or nothing for a new row).
    A future has no strike, side or offset; a pair has no side; an ATM rule has no strike and a
    fixed one no offset. Raises ``Bad``."""
    rule = {**(base or {}), **{k: patch[k] for k in FIELDS if k in patch}}
    kind = rule.get("kind")
    if kind not in schema.FOLLOW_KINDS:
        raise Bad("kind", words.NOTHING)
    name = (rule.get("underlying") or "").strip()
    if not name or len(name) > 80:
        raise Bad("underlying", words.UNDERLYING_EMPTY)
    exchange = rule.get("underlying_exchange")
    if exchange not in schema.UNDERLYING_EXCHANGES:
        later = str(exchange).upper() in _LATER
        raise Bad("underlying_exchange", words.EXCHANGE_LATER if later else words.EXCHANGE_BAD)
    month = kind == "month_future"
    expiry = rule.get("expiry_rule") or ("month" if month else "nearest")
    if expiry not in schema.EXPIRY_RULES:
        raise Bad("expiry_rule", words.NOTHING)
    out = {"kind": kind, "underlying": name, "underlying_exchange": exchange,
           "expiry_rule": expiry, "strike_rule": "atm", "offset": 0, "strike": None,
           "opt_right": None}  # fmt: skip
    if month:
        return out
    fixed = rule.get("strike_rule") == "fixed"
    out["strike_rule"] = "fixed" if fixed else "atm"
    if fixed:
        out["strike"] = _paise(rule.get("strike")) / 100
    else:
        offset = rule.get("offset") or 0
        if not isinstance(offset, int) or isinstance(offset, bool) or not -10 <= offset <= 10:
            raise Bad("offset", words.OFFSET_BAD)
        out["offset"] = offset
    if kind == "atm_option":
        if rule.get("opt_right") not in ("CE", "PE"):
            raise Bad("opt_right", words.RIGHT_NEEDED)
        out["opt_right"] = rule["opt_right"]
    return out


def rows_of(s, list_id: int) -> list:
    fol = schema.FollowRow
    return list(s.scalars(select(fol).where(fol.principal == OWNER, fol.list_id == list_id)
                          .order_by(fol.position, fol.id)))  # fmt: skip


def count(s, list_id: int) -> int:
    fol = schema.FollowRow
    return s.scalar(select(func.count()).select_from(fol).where(
        fol.principal == OWNER, fol.list_id == list_id)) or 0  # fmt: skip


def find(s, row_id: int):
    fol = schema.FollowRow
    return s.scalars(select(fol).where(fol.principal == OWNER, fol.id == row_id)).first()


def find_many(s, ids: list[int]) -> dict:
    fol = schema.FollowRow
    got = s.scalars(select(fol).where(fol.principal == OWNER, fol.id.in_(ids))) if ids else ()
    return {r.id: r for r in got}


def section_exists(s, list_id: int, section_id: int) -> bool:
    sec = schema.SectionRow
    return s.scalar(select(sec.id).where(sec.principal == OWNER, sec.list_id == list_id,
                                         sec.id == section_id)) is not None  # fmt: skip


def _set(row, rule: dict) -> None:
    row.kind, row.underlying = rule["kind"], rule["underlying"]
    row.underlying_exchange, row.expiry_rule = rule["underlying_exchange"], rule["expiry_rule"]
    row.strike_rule, row.offset = rule["strike_rule"], rule["offset"]
    row.strike_paise = None if rule["strike"] is None else _paise(rule["strike"])
    row.opt_right = rule["opt_right"]


def add(s, list_id: int, rule: dict, section_id: int | None) -> int:
    """A new row at the end of the list's follow rows."""
    at = _sections.now_ms()
    last = s.scalar(select(func.max(schema.FollowRow.position)).where(
        schema.FollowRow.principal == OWNER, schema.FollowRow.list_id == list_id))  # fmt: skip
    row = schema.FollowRow(list_id=list_id, section_id=section_id, position=(last or 0) + 1,
                           created_at=at, updated_at=at)  # fmt: skip
    _set(row, rule)
    s.add(row)
    s.flush()
    return row.id


def change(s, row, rule: dict, section_id: int | None) -> None:
    _set(row, rule)
    row.section_id = section_id
    row.updated_at = _sections.now_ms()


def remove(s, row) -> None:
    s.delete(row)


def limit() -> int:
    return contract.LIMITS["rows_per_list"]
