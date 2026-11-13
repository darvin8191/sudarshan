"""The family service's refusals, each a contract error code with its sentence (``words.py``).

A refusal reaches its reader as the envelope ``{"status": "error", "code", "message", "field"}``
with the sentence in their words; ``cause`` carries the system's own words for the journal only
("database is locked" read by a family member is a fault they think they caused).
"""

from __future__ import annotations

from typing import Any

from . import words
from .contract import ERROR_STATUS


class FamilyError(Exception):
    """One refusal. ``code`` is a contract error code; ``str()`` is the sentence (by default the
    code's own, or the one ``key`` names); ``field`` names the request field at fault."""

    def __init__(
        self,
        code: str,
        key: str | None = None,
        *,
        field: str | None = None,
        cause: str = "",
        **facts: str,
    ):
        if code not in ERROR_STATUS:
            raise ValueError(f"not an error code of the contract: {code}")
        super().__init__(words.say(key or code, **facts))
        self.code, self.field, self.cause = code, field, cause
        self.status = ERROR_STATUS[code]

    @property
    def message(self) -> str:
        return str(self)

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": "error", "code": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        return out


class StoreError(FamilyError):
    """The store could not do it; nothing was changed."""

    def __init__(self, cause: str = "", key: str = "failed"):
        super().__init__("failed", key, cause=cause)


class SchemaTooNew(FamilyError):
    """family.db was written by a newer release: this one must not write to it, and the family
    view stays off with the owner's sentence."""

    def __init__(self, found: int, known: int):
        super().__init__("store_newer", cause=f"file version {found}, code knows up to {known}")
        self.found, self.known = found, known


class NotFound(FamilyError):
    def __init__(self, key: str = "not_found"):
        super().__init__("not_found", key)


class VersionConflict(FamilyError):
    """Nothing was written: the row is not at the version the change was decided on."""

    def __init__(self) -> None:
        super().__init__("conflict")


class Invalid(FamilyError):
    """The request is not acceptable as sent; the sentence says what to change."""

    def __init__(self, key: str = "invalid", field: str | None = None):
        super().__init__("invalid", key, field=field)


class LimitReached(FamilyError):
    def __init__(self, key: str = "limit"):
        super().__init__("limit", key)
