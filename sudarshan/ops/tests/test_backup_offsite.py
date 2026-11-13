"""M18.11, part 2: the encrypted offsite copy of the nightly archive.

Real openssl, a throwaway key pair made here, and a local HTTP server standing in for the bucket's
pre-authenticated request (PAR) address. Nothing is mocked about the encryption or the upload.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import stat
import subprocess
import tarfile
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest
from backup_fakes import NIGHT, Archive, Server, count_rows, go

from sudarshan.alerts.notices import _backup_failed
from sudarshan.ops import backup, offsite
from sudarshan.ops.backup import OK

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")

TOKEN = "SECRETPARTOKEN0123456789abcdef"
TODAY = NIGHT.date()
ARCHIVE = "sudarshan-backup-20261027-163102.tar.gz"
SEALED = "sudarshan-backup-20261027-163102-v0.3.4.tar.gz.cms"


@pytest.fixture
def server(tmp_path):
    pretend = Server(tmp_path / "server")
    yield pretend
    pretend.close()


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(offsite, "PAUSE_S", 0.0)


class Bucket:
    """A local stand-in for the PAR address: keeps what is PUT, answers with ``status``."""

    def __init__(self, status: int = 200):
        self.status = status
        self.puts: list[tuple[str, bytes]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.puts.append((self.path, body))
                self.send_response(outer.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                pass

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    @property
    def par(self) -> str:
        return f"http://127.0.0.1:{self.http.server_port}/p/{TOKEN}/n/ns/b/bk/o/"

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def bucket():
    made = Bucket()
    yield made
    made.close()


def make_pair(folder: Path) -> tuple[Path, Path]:
    """A throwaway certificate and its private key (what the owner makes on the laptop)."""
    folder.mkdir(parents=True, exist_ok=True)
    key, cert = folder / "k.pem", folder / "c.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(cert), "-days", "30", "-subj", "/CN=test"],
        check=True, capture_output=True,
    )  # fmt: skip
    return key, cert


def decrypt(sealed: bytes, key: Path, cert: Path, tmp: Path) -> bytes | None:
    source, out = tmp / "in.cms", tmp / "out.bin"
    source.write_bytes(sealed)
    done = subprocess.run(
        ["openssl", "cms", "-decrypt", "-inform", "DER", "-in", str(source), "-inkey", str(key),
         "-recip", str(cert), "-out", str(out)],
        capture_output=True,
    )  # fmt: skip
    return out.read_bytes() if done.returncode == 0 and out.exists() else None


def write_file(server: Server, config: offsite.Config) -> Path:
    """The settings file as the set-up leaves it: made empty with mode 600, then filled in."""
    path = server.etc / "offsite.json"
    path.write_bytes(offsite.render(config))
    path.chmod(0o600)
    return path


def set_up(server: Server, par: str, cert: Path, expires: date = TODAY + timedelta(days=60)):
    write_file(server, offsite.Config(par, expires, cert.read_text()))


# ---- what is in the archive ---------------------------------------------------------------


def test_the_archive_holds_the_intended_files_and_nothing_of_trinetras(server, bucket, tmp_path):
    _key, cert = make_pair(tmp_path / "pair")
    server.populate()
    (server.var / "trinetra").mkdir()
    backup_db = server.var / "trinetra" / "t.db"
    backup_db.write_bytes(b"x")
    (server.var / "trinetra-engine").mkdir()
    (server.var / "trinetra-engine" / "e.db").write_bytes(b"x")
    set_up(server, bucket.par, cert)

    result = go(server)

    assert result.outcome == OK
    names = Archive(server.archive_dir / ARCHIVE).files
    assert "etc/sudarshan/offsite.json" not in names  # the upload link is not backed up
    assert not [n for n in names if "offsite" in n]
    assert not [n for n in names if "trinetra" in n]
    assert {n.split("/")[0] for n in names} == {"db", "ring", "etc", "app", "MANIFEST.json"}
    assert {n for n in names if n.startswith("ring/")} == {
        "ring/sudarshan-alerts/alerts.db", "ring/sudarshan-alerts/market.db",
        "ring/sudarshan-feed/feed.db",
    }  # fmt: skip
    assert "app/openalgo.env" in names  # the keys a restore needs ride inside the encrypted copy


# ---- encryption and the restore ---------------------------------------------------------------


def test_the_copy_opens_only_with_the_private_key_and_restores_end_to_end(server, bucket, tmp_path):
    key, cert = make_pair(tmp_path / "pair")
    other_key, other_cert = make_pair(tmp_path / "other")
    server.populate()
    set_up(server, bucket.par, cert)

    result = go(server)

    assert (result.outcome, result.offsite, result.failures) == (OK, "ok", [])
    ((path, sealed),) = bucket.puts
    assert path == f"/p/{TOKEN}/n/ns/b/bk/o/{SEALED}"  # date and release, no host name
    local = (server.archive_dir / ARCHIVE).read_bytes()
    assert b"APP_KEY" not in sealed and local[:200] not in sealed  # nothing readable leaves

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert decrypt(sealed, other_key, other_cert, scratch) is None  # a different key cannot
    assert decrypt(sealed, key, cert, scratch) == local  # the owner's can

    # the restore path: the decrypted file is the archive; read it back, then take a database out
    restored = scratch / "restored.tar.gz"
    restored.write_bytes(local)
    problems, manifest = backup.check_archive(restored)
    assert problems == [] and manifest["release"] == "v0.3.4"
    with tarfile.open(restored) as tar:
        data = tar.extractfile("db/sandbox.db").read()
    assert count_rows(data, scratch / "x.db") == 5
    assert not list(server.state.glob("work/*"))  # no encrypted copy is left on the server


# ---- the link, the log and the file ------------------------------------------------------------


def test_the_par_address_is_never_written_to_a_log_a_status_a_notice_or_the_archive(
    server, bucket, tmp_path, caplog
):
    _key, cert = make_pair(tmp_path / "pair")
    server.populate()
    set_up(server, bucket.par, cert)
    bucket.status = 403  # a failure logs and tells more than a success does
    caplog.set_level("DEBUG")

    go(server)

    everything = caplog.text + "".join(
        p.read_text(errors="replace")
        for p in server.state.iterdir()
        if p.is_file() and p.suffix in {".json", ".jsonl", ".log", ".noted"}
    )
    assert TOKEN not in everything and str(bucket.http.server_port) not in everything
    with tarfile.open(server.archive_dir / ARCHIVE) as tar:
        assert all(TOKEN.encode() not in tar.extractfile(m).read() for m in tar if m.isfile())
    assert stat.S_IMODE((server.etc / "offsite.json").stat().st_mode) == 0o600


def test_a_certificate_that_holds_a_private_key_or_a_link_that_is_not_https_is_refused(server):
    good = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
    cases = {
        "http://objectstorage.example.com/p/x/n/a/b/c/o/": good,
        "https://objectstorage.example.com/p/x/n/a/b/c/": good,
        "https://objectstorage.example.com/p/x/n/a/b/c/o/": good + "-----BEGIN PRIVATE KEY-----\n",
        "https://objectstorage.example.com/p/x/n/a/b/c/o/?x=1": good,
    }
    for par, cert in cases.items():
        write_file(server, offsite.Config("https://h/o/", TODAY, good))
        data = json.loads((server.etc / "offsite.json").read_text())
        data.update(par_url=par, certificate=cert)
        (server.etc / "offsite.json").write_text(json.dumps(data))
        with pytest.raises(offsite.OffsiteError):
            offsite.load(server.etc)


# ---- expiry -----------------------------------------------------------------------------------


def soon_events(server):
    return [e for e in server.events() if e.fact("why") == "offsite_soon"]


@pytest.mark.parametrize(("days", "told"), [(15, True), (1, True), (16, False), (60, False)])
def test_the_owner_is_warned_from_15_days_before_the_date_and_the_copy_still_goes(
    server, bucket, tmp_path, days, told
):
    """The date typed is the expiry date the console shows, the first day the link may not work
    (review L1): the copies go up to the day before it, and the warnings start 15 days ahead."""
    _key, cert = make_pair(tmp_path / "pair")
    server.populate()
    set_up(server, bucket.par, cert, TODAY + timedelta(days=days))

    result = go(server)

    assert (result.offsite, result.failures) == ("ok", []) and len(bucket.puts) == 1
    events = soon_events(server)
    assert len(events) == (1 if told else 0)
    if told:
        (line,) = _backup_failed(events[0])
        assert "Make a new one" in line and "steps 4, 5 and 9" in line
        assert ("expires tomorrow" if days == 1 else f"expires in {days} days") in line


def test_on_the_date_the_console_shows_nothing_is_sent_and_the_owner_is_told(
    server, bucket, tmp_path
):
    """Review L1: the console's expiry has a time of day (10:15 say), so on that date a run at
    16:30 found the link refused and told the owner it "may have been removed", beside a warning
    that it "expires today". The date is now the first day the link is not used."""
    _key, cert = make_pair(tmp_path / "pair")
    server.populate()
    set_up(server, bucket.par, cert, TODAY)

    result = go(server)

    assert bucket.puts == [] and (result.outcome, result.offsite) == (OK, "failed")
    assert [e.fact("reason") for e in server.events() if e.fact("why") == "offsite"] == ["expired"]
    assert soon_events(server) == []  # no "expires today" beside it
    shown = backup.check(server.settings(), NIGHT)
    assert "offsite: the link stopped working on 27 Oct 2026, so no copy is sent" in shown


@pytest.mark.parametrize(
    ("days", "words"),
    [
        (1, "stops working tomorrow, on 28 Oct 2026"),
        (30, "stops working in 30 days, on 26 Nov 2026"),
    ],
)
def test_the_check_says_when_the_link_stops_working(server, tmp_path, days, words):
    _key, cert = make_pair(tmp_path / "pair")
    set_up(server, "https://objectstorage.example.com/p/x/n/a/b/c/o/", cert,
           TODAY + timedelta(days=days))  # fmt: skip
    assert f"offsite: set (the link {words})" in backup.check(server.settings(), NIGHT)


def test_after_the_expiry_nothing_is_sent_and_the_local_backup_is_fine(server, bucket, tmp_path):
    _key, cert = make_pair(tmp_path / "pair")
    server.populate()
    set_up(server, bucket.par, cert, TODAY - timedelta(days=1))

    result = go(server)

    assert bucket.puts == [] and (server.archive_dir / ARCHIVE).exists()
    assert (result.outcome, result.offsite) == (OK, "failed")
    (event,) = [e for e in server.events() if e.fact("why") == "offsite"]
    (line,) = _backup_failed(event)
    assert line.startswith("Tonight's offsite backup did not upload: the upload link has reached")
    assert line.endswith("The local backup is fine.")


# ---- failures ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("status", "words"), [(403, "refused the file"), (503, "not accepting")])
def test_a_refused_upload_is_told_in_words_and_leaves_the_local_backup_alone(
    server, bucket, tmp_path, status, words
):
    _key, cert = make_pair(tmp_path / "pair")
    server.populate()
    set_up(server, bucket.par, cert)
    bucket.status = status

    result = go(server)

    assert (result.outcome, result.archive, result.offsite) == (OK, ARCHIVE, "failed")
    assert (server.archive_dir / ARCHIVE).exists() and not list(server.state.glob("work/*"))
    (event,) = [e for e in server.events() if e.fact("why") == "offsite"]
    (line,) = _backup_failed(event)
    assert words in line and line.endswith("The local backup is fine.")
    assert str(status) not in line and "http" not in line.lower()
    assert len(bucket.puts) == (3 if status == 503 else 1)  # only a busy bucket is tried again
    assert server.status()["offsite"] == "failed"


class EarlyAnswer:
    """A bucket that answers ``status`` as soon as it has the request's headers and closes the
    connection without reading the file, as storage does for a link that is gone (review L4)."""

    def __init__(self, status: int):
        self.status = status
        self.connections = 0
        self.listener = socket.create_server(("127.0.0.1", 0))
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.connections += 1
            with conn:
                seen = b""
                while b"\r\n\r\n" not in seen:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    seen += chunk
                conn.sendall(
                    f"HTTP/1.1 {self.status} Answer\r\nContent-Length: 0\r\n"
                    "Connection: close\r\n\r\n".encode()
                )

    @property
    def par(self) -> str:
        return f"http://127.0.0.1:{self.listener.getsockname()[1]}/p/{TOKEN}/n/ns/b/bk/o/"

    def close(self):
        self.listener.close()


@pytest.mark.parametrize(("status", "reason", "tries"), [(404, "refused", 1), (503, "busy", 3)])
def test_an_answer_given_before_the_file_was_read_is_told_by_what_it_said(
    tmp_path, status, reason, tries
):
    """Before: the connection closed under the upload, the answer already sent was never read,
    and a removed link read as a network fault, tried three times, 30 seconds apart."""
    big = tmp_path / "sealed.cms"
    big.write_bytes(os.urandom(20 << 20))  # larger than what the sockets buffer
    early = EarlyAnswer(status)
    try:
        with pytest.raises(offsite.OffsiteError) as raised:
            offsite.upload(early.par, big, "x.cms")
        assert raised.value.reason == reason
        archive = tmp_path / "a.tar.gz"
        archive.write_bytes(os.urandom(20 << 20))  # the job's own path, on a copy as large
        early.connections = 0
        outcome = offsite.send(
            etc_dir=_settings_dir(tmp_path, early.par), archive=archive, work=tmp_path / "w",
            log_file=tmp_path / "o.log", release="v0.8.0", today=TODAY, free_bytes=1 << 40,
            sleep=lambda _s: None,
        )  # fmt: skip
        assert (outcome.state, outcome.reason, early.connections) == ("failed", reason, tries)
    finally:
        early.close()


def _settings_dir(tmp_path: Path, par: str) -> Path:
    folder = tmp_path / "etc"
    folder.mkdir()
    _key, cert = make_pair(tmp_path / "pair")
    (folder / "offsite.json").write_bytes(
        offsite.render(offsite.Config(par, TODAY + timedelta(days=60), cert.read_text()))
    )
    (folder / "offsite.json").chmod(0o600)
    return folder


def test_a_bucket_that_cannot_be_reached_is_a_network_notice(server, tmp_path):
    _key, cert = make_pair(tmp_path / "pair")
    server.populate()
    gone = Bucket()
    par = gone.par
    gone.close()  # nothing listens there any more
    set_up(server, par, cert)

    result = go(server)

    assert (result.outcome, result.offsite) == (OK, "failed")
    (event,) = [e for e in server.events() if e.fact("why") == "offsite"]
    assert event.fact("reason") == "network"
    assert (server.archive_dir / ARCHIVE).exists()


# ---- off --------------------------------------------------------------------------------------


def test_without_the_file_the_copy_is_off_quietly_and_says_so_once_a_day(server, caplog):
    server.populate()
    caplog.set_level("INFO")

    first = go(server)
    second = go(server, force=True)

    assert (first.outcome, first.offsite, first.failures, first.exit_code) == (OK, "off", [], 0)
    assert second.offsite == "off" and server.events() == []
    said = [r for r in caplog.records if "offsite copy is off" in r.getMessage()]
    assert len(said) == 1  # the second run the same day stays silent
    assert "offsite: not set" in backup.check(server.settings(), NIGHT)
    next_day = go(server, NIGHT + timedelta(days=1), force=True)
    assert next_day.offsite == "off"
    assert len([r for r in caplog.records if "offsite copy is off" in r.getMessage()]) == 2


def test_a_file_that_cannot_be_used_is_a_notice_and_never_a_failed_local_backup(server):
    server.populate()
    (server.etc / "offsite.json").write_text("{not json")

    result = go(server)

    assert (server.archive_dir / ARCHIVE).exists() and result.archive == ARCHIVE
    (event,) = [e for e in server.events() if e.fact("why") == "offsite"]
    assert event.fact("reason") == "config"


# The owner's set-up command (--configure) has its own tests: test_offsite_configure.py.
