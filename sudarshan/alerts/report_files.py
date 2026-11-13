"""A report's file on its way to Telegram: which files may go, and how each one is sent (M15.7).

The journal makes a report's file (a PDF, a CSV or a PNG chart). The alerts' report desk
(``reports_desk.py``) fetches its bytes and posts them to OpenAlgo's
``POST /api/v1/telegram/notify-file`` (``restx_api/telegram_bot.py``), and OpenAlgo sends them with
its own bot, so the bot token never leaves OpenAlgo (``send_file_sync`` in
``services/telegram_alert_service.py``). Both ends read the rules here, so they cannot drift:

    name     ASCII letters, digits, '.', '_' and '-', 1 to 80 characters; the extension is the kind
    kinds    .pdf  sendDocument  application/pdf  the bytes start with %PDF-
             .csv  sendDocument  text/csv         UTF-8 text without a NUL byte
             .png  sendPhoto     image/png        the bytes start with the PNG signature
    size     1 byte to MAX_BYTES (10 MiB)
    caption  at most CAPTION_MAX characters (Telegram's own limit), sent as plain text

The name is checked with ``fullmatch``: a pattern anchored with ``$`` would let a name ending in a
newline through. Nothing here does I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAME = re.compile(r"[A-Za-z0-9._-]{1,80}")
MAX_BYTES = 10 * 1024 * 1024
#: The whole multipart request: the file plus the key, the username and the caption.
MAX_BODY_BYTES = MAX_BYTES + 64 * 1024
CAPTION_MAX = 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PDF_SIGNATURE = b"%PDF-"


@dataclass(frozen=True)
class FileKind:
    """One kind of file: its MIME type, the Bot API method that sends it and that method's field."""

    extension: str
    mime: str
    method: str
    field: str


KINDS = {
    "pdf": FileKind("pdf", "application/pdf", "sendDocument", "document"),
    "csv": FileKind("csv", "text/csv", "sendDocument", "document"),
    "png": FileKind("png", "image/png", "sendPhoto", "photo"),
}
MIMES = frozenset(kind.mime for kind in KINDS.values())

#: Why OpenAlgo refused a file, in the words its answer gives (``code`` beside each).
WORDS = {
    "key": "The API key is missing or not valid. Copy it again from the API Key page in OpenAlgo.",
    "bot_stopped": "Telegram bot is stopped. Start the bot to send notifications.",
    "username": "No OpenAlgo username was given, so the file was not sent.",
    "no_file": "No file was attached, so nothing was sent.",
    "name": (
        "The file name may use only the letters A to Z, digits, dots, dashes and underscores, "
        "up to 80 of them, so the file was not sent."
    ),
    "kind": "Only PDF, CSV and PNG files can be sent to Telegram.",
    "empty": "The file is empty, so nothing was sent.",
    "size": "The file is larger than 10 MB, so it was not sent.",
    "content": "The file's content does not match its name (PDF, CSV or PNG), so it was not sent.",
    "caption": "The caption may be at most 1,024 characters, so the file was not sent.",
    "not_linked": (
        "This OpenAlgo user is not linked to a Telegram chat. Link it on the Telegram page, "
        "then send the file again."
    ),
    "not_taken": "Telegram did not take the file. Try again in a minute.",
    "failed": "The file could not be sent just now. Try again in a minute.",
}


def kind_of(name: str | None) -> FileKind | None:
    """The kind a file name says, or None when the name or its extension cannot be sent."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return None
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        return None
    return KINDS.get(extension.lower())


def content_matches(kind: FileKind, data: bytes) -> bool:
    """Whether the bytes are what the name says: a PDF, a PNG, or UTF-8 text for a CSV."""
    if kind.extension == "pdf":
        return data.startswith(PDF_SIGNATURE)
    if kind.extension == "png":
        return data.startswith(PNG_SIGNATURE)
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def problem(name: str | None, data: bytes, caption: str = "") -> str | None:
    """None when the file may go; otherwise the key of ``WORDS`` that says why not."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return "name"
    kind = kind_of(name)
    if kind is None:
        return "kind"
    if not data:
        return "empty"
    if len(data) > MAX_BYTES:
        return "size"
    if not content_matches(kind, data):
        return "content"
    if len(caption) > CAPTION_MAX:
        return "caption"
    return None
