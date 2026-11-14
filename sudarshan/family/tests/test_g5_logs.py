"""G5 (P6 T2; design 2.8, 7.2): no log call anywhere in ``sudarshan/family`` or ``sudarshan/push``
(every module, every folder, tests included) passes a value named handle, name, chat_id, endpoint,
code, token, password or totp: a sign-in name, a member's name, a Telegram chat, a push endpoint, an
invite or link code, a bot token, a password or a one-time code never reaches a journal.

A log call is any call of ``debug``, ``info``, ``warning``, ``warn``, ``error``, ``exception``,
``critical`` or ``log`` on a receiver whose name says log (``log``, ``logger``, ``self.log``,
``_log``, ``LOG``, the ``logging`` module itself) or on ``logging.getLogger(...)``. A value counts
wherever it sits in the call: a positional or keyword argument, ``extra``, inside an f-string, a
``%`` or ``.format()`` expression, a container, a subscript key, an attribute. The existing
``test_family_logs.py`` holds the service's top-level modules to the same list; this one holds the
whole of both packages, so a new folder or a new spelling of a logger cannot slip past.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOTS = (
    Path(__file__).resolve().parents[1],  # sudarshan/family
    Path(__file__).resolve().parents[2] / "push",  # sudarshan/push
)
FORBIDDEN = {"handle", "name", "chat_id", "endpoint", "code", "token", "password", "totp"}
LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _says_log(node: ast.AST) -> bool:
    """Whether a receiver is a logger: a name or attribute that says log, or getLogger(...)."""
    if isinstance(node, ast.Name):
        return "log" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "log" in node.attr.lower()
    if isinstance(node, ast.Call):
        func = node.func
        return isinstance(func, ast.Attribute) and func.attr == "getLogger"
    return False


def log_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in LEVELS and _says_log(node.func.value)):  # fmt: skip
            yield node


def named(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return str(node.slice.value)
    if isinstance(node, ast.keyword):
        return node.arg
    return None


def offences(source: str, where: str) -> list[str]:
    found = []
    for call in log_calls(ast.parse(source)):
        for argument in [*call.args, *call.keywords]:
            for part in ast.walk(argument):
                if part is argument and isinstance(part, ast.keyword):
                    continue  # the keyword's own name (extra=, exc_info=) is not a value
                if named(part) in FORBIDDEN:
                    found.append(f"{where}:{call.lineno}: {named(part)}")
    return found


def sources() -> list[Path]:
    found = sorted(p for root in ROOTS for p in root.rglob("*.py"))
    assert len(found) > 40, "the scan must see both packages"
    return found


def test_no_log_call_in_either_package_passes_a_forbidden_value():
    found, calls = [], 0
    for path in sources():
        text = path.read_text(encoding="utf-8")
        calls += len(list(log_calls(ast.parse(text))))
        found += offences(text, str(path.relative_to(ROOTS[0].parents[1])))
    assert calls >= 40, "the scan must find the packages' log calls (49 when it was written)"
    assert found == []


def test_the_scan_sees_the_channels_modules():
    names = {p.name for p in sources()}
    assert {"bot.py", "telegram_client.py", "push.py", "outbox.py", "digest.py", "quiet.py",
            "timers.py", "webpush.py"} <= names  # fmt: skip


def test_the_scan_finds_each_way_a_value_can_reach_a_log():
    planted = (
        "log.info('x %s', handle)\n"  # a variable
        "logger.warning('x %s', member.name)\n"  # an attribute
        "self.log.error('x %s', row['chat_id'])\n"  # a subscript key, a logger on self
        "_log.debug(f'x {token}')\n"  # an f-string
        "LOG.info('x', extra={'e': endpoint})\n"  # extra, a capitalised logger
        "logging.getLogger('x').info('%s' % code)\n"  # getLogger(...), a % expression
        "logging.critical('{}'.format(password))\n"  # the logging module, .format()
        "log.exception('x', exc_info=[totp])\n"  # inside a container
        "log.log(20, 'x %s', who.handle)\n"  # log.log
        "log.info('fine %s %s', count, kind)\n"  # allowed
        "log.info('fine', exc_info=True)\n"  # a keyword's own name is not a value
        "print(handle)\n"  # not a log call
    )
    found = [line.split(": ")[1] for line in offences(planted, "planted")]
    assert found == ["handle", "name", "chat_id", "token", "endpoint", "code", "password", "totp",
                     "handle"]  # fmt: skip
