"""M18.x: the notice file the deploy tool and the backup job write, and the small readers around it.

``events.py`` is the format both writers and the one reader (``alerts/notices.py``) share; a line
that cannot be used is skipped and nothing here ever raises. ``files.py`` is how every reader opens a
file another program writes (never a FIFO, never a symlink, never more than it needs), and
``release.py`` reads the installed release tag from ``deployed_tag``.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from sudarshan.ops import events
from sudarshan.ops.events import Event, parse_line, read_events, record
from sudarshan.ops.files import read_regular
from sudarshan.ops.release import installed_release

NOW = 1_792_000_000.0  # 2026-10-14, a number the tests only compare


def line(**fields) -> str:
    base = {"v": "1", "id": "deploy-1", "src": "deploy", "kind": "installed", "at": "1792000000"}
    return json.dumps(base | fields)


# ---- parse_line -------------------------------------------------------------------------------


def test_a_line_the_deploy_tool_writes_is_read():
    event = parse_line(line(run="20261014-1852-v0.3.3", tag="v0.3.3", backup="yes"))
    assert event == Event(
        "deploy-1", "installed", NOW, "20261014-1852-v0.3.3", "deploy",
        {"tag": "v0.3.3", "backup": "yes"},
    )  # fmt: skip
    assert event.fact("tag") == "v0.3.3" and event.fact("missing", "x") == "x"


def test_a_line_without_a_run_is_its_own_run():
    assert parse_line(line()).run == "deploy-1"
    assert parse_line(line(run="not valid!")).run == "deploy-1"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"v": "1", "id": "a"',  # cut off by a crash
        line(v="2"),  # a format this reader does not know
        line(id=""),
        line(id="has space"),
        line(id="a" * 121),
        line(id=5),
        line(kind="exploded"),  # a kind with no words
        line(src="somebody"),
        line(at="soon"),
        line(at="0"),
        line(at="-5"),
        line(at="nan"),
        line(at="inf"),
        line(at=None),
    ],
)
def test_a_line_that_cannot_be_used_is_skipped(text):
    assert parse_line(text) is None


def test_bytes_are_read_like_text():
    assert parse_line(line().encode()) is not None
    assert parse_line(b"\xff\xfe not utf-8") is None


def test_facts_are_short_single_line_plain_text():
    event = parse_line(line(tag="v1\nHACK\x00\x1b[31m", other="x" * 500, number=7, nested={"a": 1}))
    assert "\n" not in event.fact("tag") and "\x00" not in event.fact("tag")
    assert "\x1b" not in event.fact("tag")
    assert len(event.fact("other")) == events.MAX_VALUE
    assert event.fact("number") == "7"
    assert "nested" not in event.facts  # only strings and numbers are facts


def test_the_reserved_names_are_not_facts():
    event = parse_line(line(run="r1"))
    assert not {"v", "id", "src", "kind", "at", "run"} & set(event.facts)


# ---- read_events ------------------------------------------------------------------------------


def test_a_missing_or_unusable_file_has_no_events(tmp_path):
    assert read_events(tmp_path / "none.jsonl") == []
    assert read_events(tmp_path) == []  # a directory
    (tmp_path / "empty.jsonl").write_text("")
    assert read_events(tmp_path / "empty.jsonl") == []


def test_good_lines_are_read_around_bad_ones(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([line(id="a"), "garbage", "", line(id="b", kind="waiting"), '{"v":"1"'])
    )
    assert [e.id for e in read_events(path)] == ["a", "b"]


def test_a_symlink_in_place_of_the_file_is_not_followed(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_text(line() + "\n")
    link = tmp_path / "events.jsonl"
    link.symlink_to(real)
    assert read_events(link) == []
    assert len(read_events(real)) == 1


def test_a_fifo_in_place_of_the_file_never_hangs_the_reader(tmp_path):
    fifo = tmp_path / "events.jsonl"
    os.mkfifo(fifo)
    started = time.monotonic()
    assert read_events(fifo) == []
    assert time.monotonic() - started < 2


def test_a_huge_file_is_read_from_its_tail(tmp_path):
    path = tmp_path / "events.jsonl"
    filler = "x" * 1000
    rows = [line(id=f"old-{n}", pad=filler) for n in range(1500)] + [line(id="newest")]
    path.write_text("\n".join(rows) + "\n")
    assert path.stat().st_size > events.MAX_BYTES
    found = read_events(path)
    assert found and found[-1].id == "newest"
    assert all(e.id.startswith(("old-", "newest")) for e in found)  # whole lines only


# ---- record (the writers that run as the sudarshan user) --------------------------------------


def test_record_writes_one_private_line_the_reader_reads(tmp_path):
    path = tmp_path / "spool" / "events.jsonl"
    assert record(
        path, kind="waiting", event_id="backup-1", at=NOW, run="r1", src="backup", tag="v0.3.3"
    )  # fmt: skip
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    (event,) = read_events(path)
    assert (event.id, event.kind, event.at, event.run, event.src) == (
        "backup-1", "waiting", NOW, "r1", "backup",
    )  # fmt: skip
    assert event.fact("tag") == "v0.3.3"


def test_record_does_not_write_an_id_twice(tmp_path):
    path = tmp_path / "events.jsonl"
    for _ in range(3):
        assert record(path, kind="installed", event_id="same", at=NOW, run="r", src="deploy")
    assert len(path.read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "exploded"},
        {"src": "somebody"},
        {"event_id": ""},
        {"event_id": "a b"},
        {"event_id": "x" * 121},
    ],
)
def test_record_refuses_what_a_reader_would_skip(tmp_path, bad):
    fields = {"kind": "installed", "event_id": "ok", "at": NOW, "run": "r", "src": "deploy"}
    path = tmp_path / "events.jsonl"
    assert record(path, **(fields | bad)) is False
    assert not path.exists()


def test_record_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    fields = {"kind": "installed", "event_id": "ok", "at": NOW, "run": "r", "src": "deploy"}
    assert record(blocker / "events.jsonl", **fields) is False  # the parent is a file
    (tmp_path / "events.jsonl").mkdir()
    assert record(tmp_path / "events.jsonl", **fields) is False  # the file is a directory


def test_record_will_not_write_through_a_symlink(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("keep")
    link = tmp_path / "events.jsonl"
    link.symlink_to(target)
    fields = {"kind": "installed", "event_id": "ok", "at": NOW, "run": "r", "src": "deploy"}
    assert record(link, **fields) is False
    assert target.read_text() == "keep"


def test_record_keeps_the_newest_lines_when_the_file_grows(tmp_path):
    path = tmp_path / "events.jsonl"
    for n in range(events.COMPACT_ABOVE + 1):
        assert record(path, kind="waiting", event_id=f"e{n}", at=NOW + n, run="r", src="backup")
    ids = [e.id for e in read_events(path)]
    assert len(ids) == events.KEEP_LINES
    assert ids[-1] == f"e{events.COMPACT_ABOVE}"
    assert ids[0] == f"e{events.COMPACT_ABOVE + 1 - events.KEEP_LINES}"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".*.new"))


def test_a_failed_compaction_does_not_undo_a_written_event(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"

    def broken(_path):
        raise OSError("disk full")

    monkeypatch.setattr(events, "_compact", broken)
    assert record(path, kind="waiting", event_id="e", at=NOW, run="r", src="backup") is True
    assert [e.id for e in read_events(path)] == ["e"]


def test_two_writers_appending_at_once_lose_nothing(tmp_path):
    path = tmp_path / "events.jsonl"

    def write(prefix: str) -> None:
        for n in range(40):
            record(path, kind="waiting", event_id=f"{prefix}{n}", at=NOW + n, run="r", src="backup")

    threads = [threading.Thread(target=write, args=(p,)) for p in "abc"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({e.id for e in read_events(path)}) == 120


# ---- files.read_regular -----------------------------------------------------------------------


def test_read_regular_reads_a_small_file_whole(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"one\ntwo\n")
    assert read_regular(path, 100) == b"one\ntwo\n"
    assert read_regular(path, 8) == b"one\ntwo\n"  # exactly the limit is not cut


def test_read_regular_reads_the_tail_from_a_whole_line(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"first\nsecond\nthird\n")
    assert read_regular(path, 14) == b"second\nthird\n"  # cut just after a line's end
    assert read_regular(path, 13) == b"third\n"  # the first line may be cut, so it is dropped
    assert read_regular(path, 5) == b""  # not one whole line fits


def test_read_regular_refuses_what_is_not_a_plain_file(tmp_path):
    assert read_regular(tmp_path / "missing", 10) is None
    assert read_regular(tmp_path, 10) is None
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    started = time.monotonic()
    assert read_regular(fifo, 10) is None
    assert time.monotonic() - started < 2
    real = tmp_path / "real"
    real.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(real)
    assert read_regular(link, 10) is None


def test_read_regular_does_not_read_a_device_that_never_ends():
    assert read_regular(Path("/dev/zero"), 10) is None


# ---- release.installed_release ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "tag"),
    [
        ("v0.3.3\n", "v0.3.3"),
        ("v0.3.3", "v0.3.3"),
        ("  v10.20.30  \n", "v10.20.30"),
        ("", None),
        ("none\n", None),
        ("v0.3\n", None),
        ("v0.3.3-rc1\n", None),
        ("v0.3.3\nv0.3.4\n", None),
        ("0.3.3\n", None),
        ("v0.3.3" + " " * 100 + "x", None),  # longer than a tag can be
        ("v1.2.3\x00", None),
    ],
)
def test_the_installed_release_is_the_tag_in_the_file(tmp_path, text, tag):
    path = tmp_path / "deployed_tag"
    path.write_bytes(text.encode())
    assert installed_release(path) == tag


def test_the_installed_release_is_none_when_it_cannot_be_read(tmp_path):
    assert installed_release(tmp_path / "missing") is None
    assert installed_release(tmp_path) is None
    fifo = tmp_path / "deployed_tag"
    os.mkfifo(fifo)
    assert installed_release(fifo) is None
    real = tmp_path / "real"
    real.write_text("v0.3.3\n")
    link = tmp_path / "link"
    link.symlink_to(real)
    assert installed_release(link) is None
