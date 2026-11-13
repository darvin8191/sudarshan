"""The API's one error, apart from api.py so that the op modules can raise it (JB2)."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An answer other than success: HTTP status, a code the page acts on, the owner's words.

    The codes the bridge passes on are the contract's: ``bad_request`` (400), ``not_found``
    (404), ``version_conflict`` (409), ``refused`` (422), ``not_ready`` (501) and
    ``unavailable`` (503). The transport's own (``forbidden``, ``principal``, ``busy``, ``size``,
    ``timeout``, ``malformed``) are never shown to the owner as a code.
    """

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"status": "error", "code": self.code, "message": self.message}
        if self.field is not None:
            body["field"] = self.field
        return body
