"""A small reader for the parts of types.ts that the contract test compares with kinds.py.

It reads literals (numbers, strings, arrays, objects, true, false, null) after
``export const NAME ... =``, the names a module exports, and the ``PortfolioOps`` map. It is not
a TypeScript parser: anything it does not understand raises ``TsError`` instead of being skipped,
so a constant written in a way it cannot read fails the test loudly.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"-?\d[\d_]*(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


class TsError(ValueError):
    """The text is not a literal this reader understands."""


def _skip(text: str, i: int) -> int:
    """Past whitespace and comments."""
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise TsError("unterminated comment")
            i = j + 2
        else:
            break
    return i


def _string(text: str, i: int) -> tuple[str, int]:
    quote = text[i]
    i += 1
    out: list[str] = []
    while i < len(text):
        c = text[i]
        if c == "\\":
            nxt = text[i + 1]
            if nxt == "u":
                out.append(chr(int(text[i + 2 : i + 6], 16)))
                i += 6
            else:
                out.append(_ESCAPES.get(nxt, nxt))
                i += 2
        elif c == quote:
            return "".join(out), i + 1
        elif c == "\n":
            raise TsError("a string runs over a line")
        else:
            out.append(c)
            i += 1
    raise TsError("unterminated string")


def _value(text: str, i: int):
    i = _skip(text, i)
    if i >= len(text):
        raise TsError("a literal ends too soon")
    c = text[i]
    if c in "'\"":
        return _string(text, i)
    if c == "[":
        items: list = []
        i = _skip(text, i + 1)
        while text[i] != "]":
            item, i = _value(text, i)
            items.append(item)
            i = _skip(text, i)
            if text[i] == ",":
                i = _skip(text, i + 1)
        return items, i + 1
    if c == "{":
        obj: dict = {}
        i = _skip(text, i + 1)
        while text[i] != "}":
            if text[i] in "'\"":
                key, i = _string(text, i)
            else:
                match = _WORD.match(text, i)
                if not match:
                    raise TsError(f"a key is expected at: {text[i : i + 30]!r}")
                key, i = match.group(), match.end()
            i = _skip(text, i)
            if text[i] != ":":
                raise TsError(f"a colon is expected after the key {key!r}")
            obj[key], i = _value(text, i + 1)
            i = _skip(text, i)
            if text[i] == ",":
                i = _skip(text, i + 1)
        return obj, i + 1
    match = _NUMBER.match(text, i)
    if match:
        raw = match.group().replace("_", "")
        return (float(raw) if "." in raw else int(raw)), match.end()
    match = _WORD.match(text, i)
    if match and match.group() in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[match.group()], match.end()
    raise TsError(f"not a literal: {text[i : i + 30]!r}")


def read_const(text: str, name: str):
    """The literal ``export const NAME ... = <literal>`` holds (``as const`` after it is ignored)."""
    match = re.search(rf"^export const {re.escape(name)}\b[^=]*=", text, re.M)
    if not match:
        raise TsError(f"export const {name} is not in the module")
    value, end = _value(text, match.end())
    # Only `as const`, a semicolon or the end of the line may follow: `1 + 2` is not a literal.
    rest = text[end : text.find("\n", end) if text.find("\n", end) >= 0 else len(text)]
    rest = re.sub(r"/\*.*?\*/|//.*$", "", rest).strip()
    if rest not in ("", ";", "as const", "as const;"):
        raise TsError(f"{name}: more than a literal: {rest[:30]!r}")
    return value


def exported_consts(text: str) -> list[str]:
    return re.findall(r"^export const (\w+)\b", text, re.M)


def exported_types(text: str) -> set[str]:
    """Every name the module exports as an interface or a type."""
    return set(re.findall(r"^export (?:interface|type) (\w+)\b", text, re.M))


def _balanced(text: str, start: int) -> str:
    """The text between the brace at ``start`` and its closing brace."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    raise TsError("unbalanced braces")


def ops_map(text: str) -> dict[str, tuple[str, str]]:
    """Op name to (request type, answer type), from ``interface PortfolioOps``."""
    match = re.search(r"^export interface PortfolioOps\s*\{", text, re.M)
    if not match:
        raise TsError("interface PortfolioOps is not in the module")
    body = _balanced(text, match.end() - 1)
    found = re.findall(r"(\w+):\s*\{\s*args:\s*([^;]+?)\s*;\s*data:\s*([^;}]+?)\s*;?\s*\}", body)
    names = [name for name, _, _ in found]
    if len(names) != len(set(names)):
        raise TsError("an op is listed twice")
    return {name: (args, data) for name, args, data in found}


def parents(text: str) -> dict[str, list[str]]:
    """Interface or type name to the names it extends or intersects with (``A & B``)."""
    found: dict[str, list[str]] = {}
    for name, bases in re.findall(r"^export interface (\w+) extends ([\w, ]+?)\s*\{", text, re.M):
        found[name] = [base.strip() for base in bases.split(",")]
    for name, expr in re.findall(r"^export type (\w+) = ([\w ]+(?:& [\w ]+)+)$", text, re.M):
        found[name] = [part.strip() for part in expr.split("&")]
    return found


def includes(text: str, name: str, target: str) -> bool:
    """Whether the type ``name`` is ``target`` or extends it, directly or through others."""
    if name == target:
        return True
    return any(includes(text, base, target) for base in parents(text).get(name, []))
