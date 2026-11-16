"""T4: the letterbox reader (inbox.py) on a temporary folder, the owner check injected.

The tests make every file themselves, so "another account" is an inode the injected ``owner_of``
names as a stranger's; on a server it is the kernel's ``st_uid``. Every document is invented."""

from __future__ import annotations

import json
import os
import stat

import pytest

from sudarshan.signals import contract as c
from sudarshan.signals import inbox
from sudarshan.tests.fdwatch import FdWatch

AT = 1794800712250
ME = os.geteuid()
STRANGER = ME + 4242


def body(seq=1, at=AT) -> bytes:
    return json.dumps({"v": 1, "run": "r1", "seq": seq, "at": at, "next_by": at + 300_000,
                       "signals": [], "alerts": []}).encode()  # fmt: skip


class Box:
    """A letterbox folder, a reader over it, and the inodes said to be a stranger's."""

    def __init__(self, folder):
        self.folder = folder
        folder.mkdir()
        os.chmod(folder, 0o1733)
        self.strangers: set[int] = set()
        self.reader = inbox.Letterbox(
            folder, ME, owner_of=lambda st: STRANGER if st.st_ino in self.strangers else st.st_uid
        )

    def put(self, name: str, raw: bytes | None = None, mode: int = 0o644, stranger=False):
        path = self.folder / name
        path.write_bytes(body() if raw is None else raw)
        os.chmod(path, mode)
        if stranger:
            self.strangers.add(path.stat().st_ino)
        return path

    def names(self) -> set[str]:
        return {p.name for p in self.folder.iterdir()}


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path / "in")


def test_a_document_is_read_whole_and_removed_only_after_the_verdict(box):
    path = box.put(f"{AT}-1.json")
    os.utime(path, ns=(AT * 1_000_000, (AT - 7_000) * 1_000_000))
    got = box.reader.scan()
    assert got.state == "ok" and got.refused == [] and got.strangers == 0
    (found,) = got.found
    assert found.name == f"{AT}-1.json" and found.raw == body()
    assert found.written_ms == AT - 7_000 and found.size == len(body())
    assert box.names() == {found.name}  # still there until the service has stored it
    assert box.reader.remove(found) and box.names() == set()


def test_the_folder_must_be_the_letterbox(tmp_path, box):
    assert inbox.Letterbox(tmp_path / "nowhere", ME).scan().state == "missing"
    os.chmod(box.folder, 0o755)
    assert box.reader.scan().state == "wrong" and box.reader.state() == "wrong"
    os.chmod(box.folder, 0o1733)
    assert box.reader.state() == "ok"
    link = tmp_path / "link"
    link.symlink_to(box.folder)
    assert inbox.Letterbox(link, ME).state() == "wrong"  # never followed as a link
    assert inbox.Letterbox(box.folder, ME, own_uid=ME + 1).state() == "wrong"
    box.put(f"{AT}-1.json")
    os.chmod(box.folder, 0o777)
    assert box.reader.scan().found == [] and len(box.names()) == 1  # nothing read, nothing gone


def test_a_strangers_file_is_removed_whatever_its_name(box):
    box.put(f"{AT}-1.json", stranger=True)
    box.put(".writing", stranger=True)
    box.put("notes.txt", stranger=True)
    got = box.reader.scan()
    assert got.found == [] and got.strangers == 3 and got.refused == [] and box.names() == set()


def test_a_flood_of_strangers_cannot_push_the_publishers_documents_out(box):
    for n in range(600):
        box.put(f"{AT + 10**9 + n}-{n}.json", stranger=True)  # names newer than the real ones
    for n in range(10):
        box.put(f"{AT + n}-{n}.json")
    got = box.reader.scan()
    assert got.strangers == 600 and got.overflow == 0 and len(got.found) == 10


def test_the_publishers_dot_names_are_left_alone(box):
    box.put(f".{AT}-1.json.4242")
    assert box.reader.scan() == inbox.Scan() and box.names() == {f".{AT}-1.json.4242"}


def test_a_name_that_is_not_a_documents_is_removed_and_counted(box):
    box.put("1794800712250-1.json.bak")
    box.put("17948007122-1.json")
    got = box.reader.scan()
    assert sorted(got.refused) == [("17948007122-1.json", "name"),
                                   ("1794800712250-1.json.bak", "name")]  # fmt: skip
    assert box.names() == set()


@pytest.mark.parametrize(("mode", "bit"), [(0o666, "other write"), (0o664, "group write"),
                                           (0o744, "execute"), (0o4644, "set-id")])  # fmt: skip
def test_an_odd_mode_is_refused(box, mode, bit):
    path = box.put(f"{AT}-1.json", mode=mode)
    if stat.S_IMODE(path.stat().st_mode) != mode:
        pytest.skip(f"this file system will not set {bit}")
    got = box.reader.scan()
    assert got.found == [] and got.refused == [(f"{AT}-1.json", "mode")]


def test_a_link_is_never_followed(tmp_path, box):
    outside = tmp_path / "secret.json"
    outside.write_bytes(body())
    (box.folder / f"{AT}-1.json").symlink_to(outside)
    got = box.reader.scan()
    assert got.found == [] and got.refused == [(f"{AT}-1.json", "link")]
    assert outside.read_bytes() == body() and box.names() == set()


def test_a_second_link_is_refused(tmp_path, box):
    path = box.put(f"{AT}-1.json")
    os.link(path, tmp_path / "second")
    got = box.reader.scan()
    assert got.found == [] and got.refused == [(f"{AT}-1.json", "links")]
    assert (tmp_path / "second").exists()


def test_a_fifo_cannot_hold_the_scan(box):
    os.mkfifo(box.folder / f"{AT}-1.json", 0o644)
    got = box.reader.scan()
    assert got.found == [] and got.refused == [(f"{AT}-1.json", "kind")]


def test_size_is_capped_at_16_kib(box):
    box.put(f"{AT}-1.json", b"x" * c.DOC_LIMITS["bytes"])
    box.put(f"{AT}-2.json", b"x" * (c.DOC_LIMITS["bytes"] + 1))
    got = box.reader.scan()
    assert [f.name for f in got.found] == [f"{AT}-1.json"]
    assert got.refused == [(f"{AT}-2.json", "size")]


def test_a_file_changed_between_the_listing_and_the_open_is_judged_as_opened(tmp_path, box):
    path = box.put(f"{AT}-1.json")
    fd, _ = box.reader._open_folder()
    try:
        names = box.reader._list(fd, inbox.Scan())
        path.unlink()
        outside = tmp_path / "x"
        outside.write_bytes(body())
        path.symlink_to(outside)  # swapped for a link after the listing
        out = inbox.Scan()
        for name in names:
            box.reader._take(fd, name, out)
    finally:
        os.close(fd)
    assert out.found == [] and out.refused == [(f"{AT}-1.json", "link")]


def test_remove_takes_only_the_very_file_that_was_read(box):
    path = box.put(f"{AT}-1.json")
    (found,) = box.reader.scan().found
    path.unlink()
    box.put(f"{AT}-1.json", body(seq=12))  # the same name, another file
    assert not box.reader.remove(found) and box.names() == {f"{AT}-1.json"}


def test_a_newer_format_kept_is_not_read_again_until_it_goes(box):
    box.put(f"{AT}-1.json")
    (found,) = box.reader.scan().found
    box.reader.keep(found)
    again = box.reader.scan()
    assert again.found == [] and again.waiting == 0 and box.names() == {found.name}
    (box.folder / found.name).unlink()
    box.reader.scan()
    box.put(f"{AT}-1.json")
    assert [f.name for f in box.reader.scan().found] == [found.name]  # forgotten once gone


def test_a_thousand_files_go_64_a_scan_oldest_first(box):
    """The ceiling first takes the oldest 500; the newest 500 are read 64 a scan, in order."""
    for n in range(1000):
        box.put(f"{AT + n * 1000}-{n}.json", body(seq=n, at=AT + n * 1000))
    seen, overflow = [], 0
    while True:
        got = box.reader.scan()
        overflow += got.overflow
        if not got.found:
            break
        assert len(got.found) <= 64
        seen += [inbox.order_key(f.name) for f in got.found]
        assert got.waiting == 500 - len(seen)
        for found in got.found:
            box.reader.remove(found)
    assert overflow == 500 and seen == sorted(seen) and len(seen) == 500
    assert seen[0] == (AT + 500_000, 500)


def test_names_order_as_numbers_not_text():
    names = [f"{AT}-100.json", f"{AT}-99.json", f"{AT - 1}-5.json"]
    assert sorted(names, key=inbox.order_key) == [f"{AT - 1}-5.json", f"{AT}-99.json",
                                                  f"{AT}-100.json"]  # fmt: skip


def test_above_500_waiting_the_oldest_go(box):
    for n in range(520):
        box.put(f"{AT + n}-{n}.json")
    got = box.reader.scan()
    assert got.overflow == 20 and len(got.found) == 64
    assert inbox.order_key(got.found[0].name) == (AT + 20, 20)
    assert len(box.names()) == 500


def test_the_publisher_number_from_the_set_up_file(tmp_path):
    env = tmp_path / "signals.env"
    assert inbox.publisher_uid(env) is None
    env.write_text("# made by bootstrap.sh\nSUDARSHAN_SIGNALS_PUBLISHER_UID=1001\n")
    assert inbox.publisher_uid(env) == 1001
    env.write_text("SUDARSHAN_SIGNALS_PUBLISHER_UID=root\n")
    assert inbox.publisher_uid(env) is None
    env.write_text("SUDARSHAN_SIGNALS_PUBLISHER_UID=0\n")
    assert inbox.publisher_uid(env) is None


def test_ten_thousand_scans_leave_no_descriptor_open(box):
    box.put(f"{AT}-1.json")
    box.put("junk", stranger=True)
    watch = FdWatch()
    for n in range(10_000):
        got = box.reader.scan()
        if n == 0:
            box.reader.keep(got.found[0])
    box.reader.state()
    watch.assert_clean()


# ---- S1 (v0.15.0 review): a folder in the letterbox -------------------------------------------
def test_an_empty_folder_is_removed_whatever_its_name(box):
    """Before S1 a folder was unlinked as a file, which fails, so it was refused again on every
    scan for ever: a row, a count and a log line a second."""
    (box.folder / f"{AT}-1.json").mkdir()
    (box.folder / "anything").mkdir()
    (box.folder / ".hidden").mkdir()
    scan = box.reader.scan()
    assert sorted(scan.refused) == [
        (".hidden", "kind"),
        (f"{AT}-1.json", "kind"),
        ("anything", "kind"),
    ]
    assert box.names() == set()
    assert box.reader.scan() == inbox.Scan()


def test_a_strangers_empty_folder_is_removed_and_counted_as_a_strangers(box):
    folder = box.folder / "theirs"
    folder.mkdir()
    box.strangers.add(folder.stat().st_ino)
    assert box.reader.scan().strangers == 1 and box.names() == set()


def test_a_folder_with_something_in_it_is_counted_and_logged_once_then_passed_over(box, caplog):
    folder = box.folder / f"{AT}-1.json"
    folder.mkdir()
    (folder / "inside").write_text("kept")
    box.put(f"{AT}-2.json", body(2))
    with caplog.at_level("WARNING", logger="sudarshan.signals"):
        first = box.reader.scan()
        later = [box.reader.scan() for _ in range(50)]
    assert first.refused == [(folder.name, "kind")] and first.stuck == 0
    assert [f.name for f in first.found] == [f"{AT}-2.json"]  # the publisher's document is read
    for scan in later:  # nothing counted, nothing refused, nothing logged again
        assert (scan.refused, scan.strangers, scan.stuck) == ([], 0, 1)
    said = [r.getMessage() for r in caplog.records if "could not be removed" in r.getMessage()]
    assert len(said) == 1 and said[0].startswith("A folder in the TRINETRA letterbox")
    assert (folder / "inside").read_text() == "kept"  # nothing inside is touched or read


def test_a_stuck_folder_is_tried_again_when_it_changes_and_goes_once_empty(box):
    folder = box.folder / "stuck"
    folder.mkdir()
    (folder / "inside").write_text("x")
    assert box.reader.scan().refused == [("stuck", "kind")]
    assert box.reader.scan().stuck == 1
    (folder / "inside").unlink()  # emptied: the folder's own times change
    scan = box.reader.scan()
    assert scan.refused == [("stuck", "kind")] and scan.stuck == 0 and box.names() == set()
    assert box.reader._stuck == {}


def test_what_is_remembered_as_stuck_goes_when_the_entry_goes(box):
    import shutil

    folder = box.folder / "stuck"
    folder.mkdir()
    (folder / "inside").write_text("x")
    box.reader.scan()
    assert list(box.reader._stuck) == ["stuck"]
    shutil.rmtree(folder)
    assert box.reader.scan() == inbox.Scan() and box.reader._stuck == {}


def test_probing_a_folder_named_like_a_document_removes_it(box):
    (box.folder / f"{AT}-1.json").mkdir()
    assert box.reader.probe(f"{AT}-1.json") == "refused:kind" and box.names() == set()


# ---- S3 (v0.15.0 review): the publisher's dot-named files -------------------------------------
def _aged_box(tmp_path, now):
    aged = Box(tmp_path / "aged")
    aged.reader.clock = lambda: now[0]
    return aged


def test_a_dead_writers_dot_file_is_removed_after_an_hour_and_not_before(tmp_path):
    """A writer killed between its temporary file and the rename left it for ever (S3)."""
    import time

    now = [time.time()]
    box = _aged_box(tmp_path, now)
    name = f".{AT}-1.json.4242"
    box.put(name)
    for ahead in (0, 60, inbox.DOT_FILE_AGE_S - 5):
        now[0] = time.time() + ahead
        assert box.reader.scan() == inbox.Scan() and box.names() == {name}
    now[0] = time.time() + inbox.DOT_FILE_AGE_S + 5
    assert box.reader.scan() == inbox.Scan(swept=1) and box.names() == set()


def test_a_dot_file_still_being_written_is_never_old(tmp_path):
    """Age runs from the last write or change, whichever is later: an old modification time on
    a file just touched (its change time is now) does not make it a dead writer's."""
    import time

    now = [time.time() + inbox.DOT_FILE_AGE_S - 5]
    box = _aged_box(tmp_path, now)
    path = box.put(".being-written")
    os.utime(path, (0, 0))  # modified in 1970, changed just now
    assert box.reader.scan() == inbox.Scan() and box.names() == {".being-written"}
    now[0] = time.time() + 2 * inbox.DOT_FILE_AGE_S
    path.write_bytes(b"more")  # written again: touched now, by the real clock
    now[0] = time.time() + inbox.DOT_FILE_AGE_S - 5
    assert box.reader.scan() == inbox.Scan() and box.names() == {".being-written"}


def test_a_dot_file_from_the_future_is_left_alone(tmp_path):
    import time

    now = [time.time() - 86_400]  # this machine's clock stepped back a day
    box = _aged_box(tmp_path, now)
    box.put(".tmp")
    assert box.reader.scan() == inbox.Scan() and box.names() == {".tmp"}


def test_a_strangers_old_dot_file_is_a_strangers_as_before(tmp_path):
    import time

    now = [time.time() + 2 * inbox.DOT_FILE_AGE_S]
    box = _aged_box(tmp_path, now)
    box.put(".theirs", stranger=True)
    scan = box.reader.scan()
    assert (scan.strangers, scan.swept) == (1, 0) and box.names() == set()
