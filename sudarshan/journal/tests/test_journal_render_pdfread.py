"""The tests' own PDF reader: a stream is read by its /Length (lane X's finding)."""

from __future__ import annotations

import zlib

import pytest
from render_pdfread import _stream, read_pdf
from render_samples import monthly

from sudarshan.journal.render import pdf


def _ending_in(byte: int) -> tuple[bytes, bytes]:
    """Some text whose compressed form ends in ``byte`` (found by counting up, so always the
    same), and that compressed form."""
    for n in range(100_000):
        plain = b"BT (row %d) Tj ET" % n
        packed = zlib.compress(plain)
        if packed[-1] == byte:
            return plain, packed
    raise AssertionError("no such stream found")


def _obj(packed: bytes, eol: bytes = b"\n") -> bytes:
    return (
        b"<</Filter /FlateDecode /Length %d>>\nstream" % len(packed)
        + eol + packed + b"\nendstream"
    )  # fmt: skip


@pytest.mark.parametrize("byte", [0x0A, 0x0D])
def test_a_compressed_stream_that_ends_in_a_line_end_is_read_whole(byte):
    plain, packed = _ending_in(byte)
    # The defect itself: the bytes cut of their trailing line ends no longer inflate.
    with pytest.raises(zlib.error):
        zlib.decompress(packed.rstrip(b"\r\n"))
    assert _stream(_obj(packed)) == plain
    assert _stream(_obj(packed, b"\r\n")) == plain


def test_a_stream_that_is_not_compressed_keeps_its_own_line_ends():
    raw = b"q 1 0 0 1 0 0 cm Q\n\n"
    assert _stream(b"<</Length %d>>\nstream\n" % len(raw) + raw + b"\nendstream") == raw


def test_an_object_without_a_stream_has_none():
    assert _stream(b"<</Type /Catalog>>") is None


def test_a_length_that_does_not_reach_endstream_is_a_broken_file():
    _, packed = _ending_in(0x0A)
    short = _obj(packed).replace(b"/Length %d" % len(packed), b"/Length %d" % (len(packed) - 3))
    with pytest.raises(ValueError):
        _stream(short)
    with pytest.raises(ValueError):
        _stream(b"<</Length 9 0 R>>\nstream\nabc\nendstream")


def test_the_renderer_s_own_file_still_reads():
    assert read_pdf(pdf.render_pdf(monthly())).pages[0].lines
