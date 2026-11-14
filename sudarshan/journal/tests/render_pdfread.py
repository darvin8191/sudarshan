"""A small PDF reader for the renderer's tests (JB8).

No PDF parser is installed in this project, so this reads exactly what fpdf2 writes: a classic
cross-reference table, Flate streams, TrueType fonts with a ToUnicode map and text drawn with Tj.
It is strict about structure (every cross-reference offset must land on its object) so a broken
file fails, and it returns what a page draws: its text, its filled rectangles and its path
operators, which is what the tests check.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field

_OBJ = re.compile(rb"(\d+) 0 obj\b")


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    colour: tuple[float, float, float]


@dataclass
class Text:
    string: str
    x: float
    y: float
    colour: tuple[float, float, float]
    font: str


@dataclass
class Page:
    texts: list[Text] = field(default_factory=list)
    rects: list[Rect] = field(default_factory=list)
    ops: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(t.string for t in self.texts)

    @property
    def lines(self) -> list[str]:
        """The text as lines: fragments on one baseline joined left to right (a rupee sign set
        from the fallback font is its own fragment)."""
        rows: dict[int, list[Text]] = {}
        for t in self.texts:
            rows.setdefault(round(t.y * 2), []).append(t)
        out = []
        for key in sorted(rows, reverse=True):
            parts = sorted(rows[key], key=lambda t: t.x)
            out.append("".join(t.string for t in parts))
        return out

    def count(self, op: str) -> int:
        return self.ops.count(op)


@dataclass
class Pdf:
    pages: list[Page]
    objects: dict[int, bytes]
    info: dict[str, str]
    base_fonts: set[str]

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)


def _stream(obj: bytes) -> bytes | None:
    """An object's stream, taken by its ``/Length`` as the format says. The bytes before
    ``endstream`` are never stripped of line ends: a compressed stream may itself end in a CR or
    an LF byte, and cutting it gave ``zlib.error`` by chance (about one page in a hundred)."""
    m = re.search(rb"stream\r?\n", obj)
    if not m:
        return None
    head = obj[: m.start()]
    length = re.search(rb"/Length (\d+)(?! \d+ R)", head)
    if length is None:
        raise ValueError("a stream without a direct /Length")
    raw = obj[m.end() : m.end() + int(length.group(1))]
    if len(raw) != int(length.group(1)) or not obj[m.end() + len(raw) :].lstrip(b"\r\n").startswith(
        b"endstream"
    ):
        raise ValueError("a stream whose /Length does not reach its endstream")
    if b"/FlateDecode" in head:
        return zlib.decompress(raw)
    return raw


def _cmap(data: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    text = data.decode("latin1")
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            out[int(src, 16)] = bytes.fromhex(dst).decode("utf-16-be")
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, dst in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block
        ):
            base = int(dst, 16)
            for i, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                out[code] = chr(base + i)
    return out


_ESC = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}


def _literal(data: bytes, i: int) -> tuple[bytes, int]:
    """A PDF literal string starting at ``data[i] == b'('``; returns (bytes, next index)."""
    out = bytearray()
    depth = 0
    i += 1
    while True:
        c = data[i]
        if c == 0x5C:  # backslash
            n = data[i + 1]
            if 0x30 <= n <= 0x37:
                j = i + 1
                digits = b""
                while j < len(data) and len(digits) < 3 and 0x30 <= data[j] <= 0x37:
                    digits += bytes([data[j]])
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
                continue
            out.append(_ESC.get(n, n))
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            if depth == 0:
                return bytes(out), i + 1
            depth -= 1
        out.append(c)
        i += 1


_TOKEN = re.compile(rb"\s*(\(|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[^\s/\[\]()<>]+)")


def _interpret(content: bytes, fonts: dict[str, dict[int, str]]) -> Page:
    page = Page()
    stack: list[tuple[float, float, float]] = []
    fill = (0.0, 0.0, 0.0)
    font = ""
    pos = (0.0, 0.0)
    rect: tuple[float, float, float, float] | None = None
    operands: list[object] = []
    i = 0

    def decode(raw: bytes) -> str:
        cm = fonts.get(font, {})
        return "".join(
            cm.get(int.from_bytes(raw[k : k + 2], "big"), "�") for k in range(0, len(raw), 2)
        )

    while i < len(content):
        m = _TOKEN.match(content, i)
        if not m:
            break
        tok = m.group(1)
        i = m.end()
        if tok == b"(":
            raw, i = _literal(content, m.end() - 1)
            operands.append(raw)
            continue
        if tok.startswith(b"<"):
            operands.append(bytes.fromhex(tok[1:-1].decode()))
            continue
        if tok.startswith(b"/"):
            operands.append(tok.decode())
            continue
        try:
            operands.append(float(tok))
            continue
        except ValueError:
            pass
        if tok == b"[":
            operands.append("[")
            continue
        if tok == b"]":
            operands.append("]")
            continue
        op = tok.decode()
        page.ops.append(op)
        nums = [o for o in operands if isinstance(o, float)]
        if op == "q":
            stack.append(fill)
        elif op == "Q":
            fill = stack.pop() if stack else fill
        elif op == "rg":
            fill = (nums[-3], nums[-2], nums[-1])
        elif op == "Tf":
            font = next(o for o in operands if isinstance(o, str) and o.startswith("/"))[1:]
        elif op == "Td":
            pos = (nums[-2], nums[-1])
        elif op == "re":
            rect = (nums[-4], nums[-3], nums[-2], nums[-1])
        elif op in ("f", "f*", "B", "B*") and rect is not None:
            x, y, w, h = rect  # fpdf2 writes a negative height: normalise to the lower-left corner
            page.rects.append(Rect(x, min(y, y + h), abs(w), abs(h), fill))
        elif op in ("Tj", "TJ"):
            raws = [o for o in operands if isinstance(o, bytes)]
            page.texts.append(Text("".join(decode(r) for r in raws), pos[0], pos[1], fill, font))
        operands = []
    return page


def read_pdf(data: bytes) -> Pdf:
    """Parse ``data`` strictly; raises ``AssertionError`` on a structural fault."""
    assert data.startswith(b"%PDF-1."), "no PDF header"
    assert data.rstrip().endswith(b"%%EOF"), "no end-of-file marker"
    start = int(re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data.rstrip() + b"\n").group(1))
    assert data[start : start + 4] == b"xref", "startxref does not point at the table"
    head = re.match(rb"xref\s+0 (\d+)\s", data[start:])
    count = int(head.group(1))
    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf])", data[start : start + 20 * (count + 3)])
    assert len(entries) >= count
    objects: dict[int, bytes] = {}
    for num, (off, _gen, kind) in enumerate(entries[:count]):
        if kind != b"n":
            continue
        at = int(off)
        found = _OBJ.match(data, at)
        assert found and int(found.group(1)) == num, f"object {num} is not at its offset"
        end = data.index(b"endobj", at)
        objects[num] = data[found.end() : end]

    def ref(text: bytes, key: bytes) -> int | None:
        m = re.search(key + rb"\s+(\d+) 0 R", text)
        return int(m.group(1)) if m else None

    cmaps: dict[int, dict[int, str]] = {}
    base_fonts: set[str] = set()
    for num, body in objects.items():
        if b"/Type /Font" in body and b"/Subtype /Type0" in body:
            m = re.search(rb"/BaseFont /([^\s/]+)", body)
            if m:
                base_fonts.add(m.group(1).decode())
            tu = ref(body, b"/ToUnicode")
            if tu is not None:
                cmaps[num] = _cmap(_stream(objects[tu]) or b"")

    pages = []
    for num in sorted(objects):
        body = objects[num]
        if not re.search(rb"/Type /Page\b(?!s)", body):
            continue
        res_ref = ref(body, b"/Resources")
        res = objects[res_ref] if res_ref is not None else body
        names = {n.decode(): int(r) for n, r in re.findall(rb"/(F\d+)\s+(\d+) 0 R", res)}
        fonts = {n: cmaps[r] for n, r in names.items() if r in cmaps}
        contents = ref(body, b"/Contents")
        assert contents is not None, "a page without content"
        pages.append(_interpret(_stream(objects[contents]) or b"", fonts))

    info: dict[str, str] = {}
    for body in objects.values():
        if b"/Producer" in body and b"/Title" in body:
            for key in (b"Title", b"Creator", b"Author", b"Producer"):
                m = re.search(rb"/" + key + rb" \(((?:\\.|[^\\)])*)\)", body)
                if m:
                    raw = m.group(1).replace(b"\\(", b"(").replace(b"\\)", b")")
                    info[key.decode()] = (
                        raw[2:].decode("utf-16-be")
                        if raw.startswith(b"\xfe\xff")
                        else raw.decode("latin1")
                    )
    return Pdf(pages, objects, info, base_fonts)
