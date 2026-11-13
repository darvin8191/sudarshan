"""Design 2.8 and 7.2: no log call in the family service passes a sign-in name, a name, a chat id,
an endpoint, a code, a token, a password or a TOTP code, by variable, attribute or key."""

from __future__ import annotations

import ast
from pathlib import Path

FAMILY = Path(__file__).resolve().parents[1]
FORBIDDEN = {"handle", "name", "chat_id", "endpoint", "code", "token", "password", "totp"}
LEVELS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
LOGGERS = {"log", "logger", "logging"}


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in LEVELS:
        return False
    owner = func.value
    if isinstance(owner, ast.Name):
        return owner.id in LOGGERS
    return isinstance(owner, ast.Attribute) and owner.attr in LOGGERS


def offences(source: str, where: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_log_call(node):
            continue
        for argument in [*node.args, *(k.value for k in node.keywords)]:
            for part in ast.walk(argument):
                named = None
                if isinstance(part, ast.Name):
                    named = part.id
                elif isinstance(part, ast.Attribute):
                    named = part.attr
                elif isinstance(part, ast.Subscript) and isinstance(part.slice, ast.Constant):
                    named = str(part.slice.value)
                if named in FORBIDDEN:
                    found.append(f"{where}:{node.lineno}: {named}")
    return found


def test_no_log_call_passes_a_forbidden_name():
    found = []
    for path in sorted(FAMILY.glob("*.py")):
        found += offences(path.read_text(encoding="utf-8"), path.name)
    assert found == []


def test_the_check_finds_each_way_a_name_can_reach_a_log():
    planted = (
        "log.info('x %s', handle)\n"
        "logger.warning('x %s', member.display_name or member.name)\n"
        "log.error('x %s', row['chat_id'])\n"
        "log.debug(f'x {token}')\n"
        "self.log.info('x', extra={'e': endpoint})\n"
        "log.info('fine %s', count)\n"
    )
    assert [line.split(": ")[1] for line in offences(planted, "planted")] == [
        "handle", "name", "chat_id", "token", "endpoint",
    ]  # fmt: skip
