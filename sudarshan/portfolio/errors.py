"""The API's one error, apart from api.py so that the op modules can raise it."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An answer other than success: HTTP status, a code the page acts on, the owner's words."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        field: str | None = None,
        entry_id: int | None = None,
    ):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field
        self.entry_id = entry_id

    def body(self) -> dict[str, Any]:
        body = {"status": "error", "code": self.code, "message": self.message}
        if self.field is not None:
            body["field"] = self.field
        if self.entry_id is not None:
            body["entry_id"] = self.entry_id
        return body
