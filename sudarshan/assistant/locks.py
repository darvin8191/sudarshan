"""The assistant's locks: remote MCP never starts with settings that would allow orders (M17.9).

OpenAlgo ships remote MCP switched off, but with defaults that are wrong for this install: every
order tool is registered and the ``write:orders`` scope is offered, so one tap on a consent page
would hand a hosted AI client the right to trade. ``app.py`` asks ``boot_allows`` before it
registers the MCP blueprints. With remote MCP switched on, they are registered only when

- ``OPENALGO_MCP_READ_ONLY`` is on, so no tool that places, changes or cancels an order (or sends
  anything outward) exists in the server at all, and
- ``MCP_OAUTH_WRITE_SCOPE_ENABLED`` is off, so the write scope is never offered or granted.

Otherwise the app starts as usual without the assistant: an AI setting never stops trading. The
third lock is the tool allow-list (``policy.py``, a later release). Under all of them, every order
call still meets the risk gate and the kill switch.

Each setting is read exactly as upstream reads it (``mcp/mcpserver.py:_env_flag`` and
``blueprints/mcp_oauth.py:_supported_scopes``); a test holds the two readings together. This
module does no I/O of its own: the environment is handed in.
"""

from __future__ import annotations

from collections.abc import Mapping

ENABLED = "MCP_HTTP_ENABLED"
READ_ONLY = "OPENALGO_MCP_READ_ONLY"
WRITE_SCOPE = "MCP_OAUTH_WRITE_SCOPE_ENABLED"
REQUIRE_APPROVAL = "MCP_OAUTH_REQUIRE_APPROVAL"

#: What the log (and, from a later release, the System page) says while the locks refuse.
REFUSED = (
    "The assistant is switched off: its settings would allow orders. "
    "Set OPENALGO_MCP_READ_ONLY=1 and MCP_OAUTH_WRITE_SCOPE_ENABLED=False."
)
OFF = "The assistant is switched off."
ON = "The assistant is switched on, read-only. It cannot place, change or cancel orders."


def switched_on(environ: Mapping[str, str]) -> bool:
    """Whether the owner has switched remote MCP on (upstream's own reading, ``app.py``)."""
    return environ.get(ENABLED, "False").lower() == "true"


def read_only(environ: Mapping[str, str]) -> bool:
    """Whether the MCP server leaves out every tool that writes (upstream's ``_env_flag``)."""
    return environ.get(READ_ONLY, "0").strip().lower() in ("1", "true", "yes", "on")


def write_scope_offered(environ: Mapping[str, str]) -> bool:
    """Whether ``write:orders`` would be offered (upstream's reading: on unless set otherwise)."""
    return environ.get(WRITE_SCOPE, "True").lower() == "true"


def approval_required(environ: Mapping[str, str]) -> bool:
    """Whether a client that registers itself waits for the owner's approval."""
    return environ.get(REQUIRE_APPROVAL, "False").lower() == "true"


def locked(environ: Mapping[str, str]) -> bool:
    """Whether both settings that keep order tools away from the assistant hold."""
    return read_only(environ) and not write_scope_offered(environ)


def state(environ: Mapping[str, str]) -> dict:
    """The assistant's switch and locks, with the sentence for them. It holds no secret."""
    on = switched_on(environ)
    ok = locked(environ)
    return {
        "switched_on": on,
        "read_only": read_only(environ),
        "write_scope_offered": write_scope_offered(environ),
        "approval_required": approval_required(environ),
        "running": on and ok,
        "words": (ON if ok else REFUSED) if on else OFF,
    }


def boot_allows(environ: Mapping[str, str], logger=None) -> bool:
    """Whether ``app.py`` may register remote MCP. A refusal is logged once, at start-up.

    It never raises: the app starts either way, with or without the assistant.
    """
    if locked(environ):
        return True
    if logger is not None and switched_on(environ):
        logger.error(REFUSED)
    return False
