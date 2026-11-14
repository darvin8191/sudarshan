"""The watch's readers and its run (M19.5, P8 10.8): what is read and how a failure to read
looks, the state and the events over a sequence of runs, the report's shape, and no descriptor
left open."""

from __future__ import annotations

import http.server
import json
import os
import sqlite3
import ssl
import stat
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sudarshan.ops import events
from sudarshan.ops.watch import checks, readers, run
from sudarshan.ops.watch.__main__ import main
from sudarshan.tests.fdwatch import FdWatch

NOW = datetime(2026, 11, 16, 4, 30, tzinfo=UTC).timestamp()  # Monday 10:00 in India
FINE = {
    "now": NOW,
    "ist_minute": 600,
    "ist_date": "2026-11-16",
    "window_open": True,
    "health": {
        "feed": {"state": "connected", "market_window": {"open": True}},
        "alerts": {"state": "connected"},
        **{name: {"state": "running"} for name in ("portfolio", "screener", "family", "journal")},
    },
    "enabled": dict.fromkeys(checks.SERVICES, True),
    "openalgo": True,
    "master_date": "2026-11-16",
    "disk": {"free_gb": 40.0, "inodes_free_pct": 80.0},
    "mem_available_mb": 4000,
    "cert_hours_left": 120.0,
}


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        status, body = self.server.pages.get(self.path, (404, b"no"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local(tmp_path):
    """A loopback server of the test's own: (port, pages) with pages a dict the test fills."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    server.pages = {}
    thread = threading.Thread(target=server.serve_forever, name="watch-test-http", daemon=True)
    thread.start()
    yield server.server_address[1], server.pages
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def test_health_is_a_document_or_nothing(local):
    port, pages = local
    pages["/health"] = (200, b'{"state": "connected"}')
    assert readers.health(port) == {"state": "connected"}
    for answer in ((200, b"not json"), (200, b"[1]"), (503, b'{"state": "x"}')):
        pages["/health"] = answer
        assert readers.health(port) is None
    del pages["/health"]
    assert readers.health(port) is None  # 404
    assert readers.health(1) is None  # nothing listens: refused, and no error leaves the reader


def test_openalgo_answers_with_its_page_or_its_login(local, monkeypatch):
    port, pages = local
    monkeypatch.setattr(readers, "OPENALGO_URL", f"http://127.0.0.1:{port}/")
    pages["/"] = (200, b"ok")
    assert readers.openalgo() is True
    pages["/"] = (500, b"")
    assert readers.openalgo() is False
    monkeypatch.setattr(readers, "OPENALGO_URL", "http://127.0.0.1:1/")
    assert readers.openalgo() is False


def test_enabled_reads_the_units_link(tmp_path):
    assert readers.enabled("feed", tmp_path / "missing") is None
    assert readers.enabled("feed", tmp_path) is False
    (tmp_path / "sudarshan-feed.service").symlink_to("/nowhere")  # a link, dangling in a test
    assert readers.enabled("feed", tmp_path) is True


def test_disk_and_memory(tmp_path):
    seen = readers.disk(tmp_path)
    assert seen["free_gb"] > 0 and 0 <= seen["inodes_free_pct"] <= 100
    assert readers.disk(tmp_path / "missing") is None
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 7000000 kB\nMemAvailable:    2048000 kB\n")
    assert readers.mem_available_mb(meminfo) == 2000
    meminfo.write_text("MemTotal: 7000000 kB\n")
    assert readers.mem_available_mb(meminfo) is None
    assert readers.mem_available_mb(tmp_path / "missing") is None


OWN, OTHER = "sudarshan.example.invalid", "other-site.example.invalid"
OWN_ADDRESS = "203.0.113.7"  # a documentation address: the app reached by a number, not a name


def _site_context(tmp_path, common: str, hours: float, names=(), addresses=()):
    """A server-side TLS context whose certificate runs out ``hours`` after NOW."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common)])
    start = datetime.fromtimestamp(NOW, UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start - timedelta(days=1))
        .not_valid_after(start + timedelta(hours=hours))
    )
    covered = [x509.DNSName(n) for n in names]
    covered += [x509.IPAddress(ipaddress.ip_address(a)) for a in addresses]
    if covered:
        builder = builder.add_extension(x509.SubjectAlternativeName(covered), critical=False)
    pem = tmp_path / f"{common}.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        + builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem)
    return context


def _listener(first: ssl.SSLContext, by_name: dict[str, ssl.SSLContext]):
    """A TLS listener on loopback that, as nginx does, gives the site asked for by name its own
    certificate and anyone else the first site's."""

    def choose(sock, asked, _context):
        if asked in by_name:
            sock.context = by_name[asked]

    first.sni_callback = choose
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    server.pages = {}
    server.socket = first.wrap_socket(server.socket, server_side=True)
    server.handle_error = lambda *args: None  # the reader hangs up after the handshake
    thread = threading.Thread(target=server.serve_forever, name="watch-test-tls", daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def tls(tmp_path):
    """A server with two sites, as the owner's has. The first, another system's, has a
    certificate with 5 hours left; SUDARSHAN's own, asked for by name, has 31."""
    other = _site_context(tmp_path, OTHER, 5, names=[OTHER])
    own = _site_context(tmp_path, OWN, 31, names=[OWN, "*.apps.example.invalid"])
    server, thread = _listener(other, {OWN: own})
    yield server.server_address
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


@pytest.fixture
def no_site_env(monkeypatch):
    monkeypatch.delenv(readers.CERT_HOST_ENV, raising=False)


def test_the_certificates_hours_are_read_and_an_expired_one_too(tls):
    assert readers.cert_hours_left(NOW, tls, name=OWN) == pytest.approx(31.0, abs=0.01)
    assert readers.cert_hours_left(NOW + 40 * 3600, tls, name=OWN) == pytest.approx(-9, abs=0.01)
    assert readers.cert_hours_left(NOW, ("127.0.0.1", 1), name=OWN) is None


def test_the_certificate_read_is_sudarshans_own_and_never_the_first_one_offered(
    tls, tmp_path, monkeypatch, no_site_env
):
    """L5. nginx serves another system's site first on this server. With no name known nothing is
    asked and the reading says so; it is never the first site's 5 hours."""
    env = tmp_path / ".env"
    env.write_text("HOST_SERVER = 'http://127.0.0.1:5000'\n")  # OpenAlgo's own default
    assert readers.site_name(env) is None
    assert readers.cert_hours_left(NOW, tls, env_file=env) == readers.CERT_NO_NAME
    assert readers.cert_hours_left(NOW, tls) == readers.CERT_NO_NAME
    assert readers.cert_hours_left(NOW, tls, env_file=tmp_path / "missing") == readers.CERT_NO_NAME

    env.write_text(f"HOST_SERVER = 'https://{OWN}'\n")  # the address the owner opens the app at
    assert readers.site_name(env) == OWN
    assert readers.cert_hours_left(NOW, tls, env_file=env) == pytest.approx(31.0, abs=0.01)

    monkeypatch.setenv(readers.CERT_HOST_ENV, f" {OWN.upper()} ")  # the watch's own setting wins
    env.write_text(f"HOST_SERVER = 'https://{OTHER}'\n")
    assert readers.site_name(env) == OWN
    assert readers.cert_hours_left(NOW, tls, env_file=env) == pytest.approx(31.0, abs=0.01)


def test_another_sites_certificate_is_said_and_never_judged(tls, tmp_path, no_site_env):
    """nginx has no site of the name asked for on this address, so it gives its first site's
    certificate: the reading says it is another site's."""
    gone = "moved.example.invalid"
    assert readers.cert_hours_left(NOW, tls, name=gone) == readers.CERT_OTHER_SITE
    reading = {"cert_hours_left": readers.cert_hours_left(NOW, tls, name=gone)}
    (result,) = [r for r in checks.run_checks(reading, {}) if r.name == "certificate"]
    assert (result.level, dict(result.facts)) == (checks.UNREAD, {"cause": "other_site"})
    assert set(checks.CERT_CAUSES) == {readers.CERT_NO_NAME, readers.CERT_OTHER_SITE}


def test_an_app_reached_by_a_number_is_judged_only_on_a_certificate_for_that_number(tmp_path):
    """An address cannot be sent as a name, so nginx gives its first certificate: it is judged
    when it is issued for the app's address, and said to be another site's when it is not."""
    own = _site_context(tmp_path, "by-number", 31, addresses=[OWN_ADDRESS])
    other = _site_context(tmp_path, OTHER, 5, names=[OTHER])
    for first, expected in ((own, pytest.approx(31.0, abs=0.01)), (other, readers.CERT_OTHER_SITE)):
        server, thread = _listener(first, {})
        try:
            assert readers.cert_hours_left(NOW, server.server_address, name=OWN_ADDRESS) == expected
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


@pytest.mark.parametrize(
    ("written", "name"),
    [
        (f"https://{OWN}", OWN),
        (f"https://{OWN}:8443/", OWN),
        (f"HTTPS://{OWN.upper()}.", OWN),
        (f"https://{OWN_ADDRESS}", OWN_ADDRESS),
        ("http://127.0.0.1:5000", None),
        ("http://localhost:5000", None),
        ("https://[::1]", None),
        ("http://0.0.0.0:5000", None),
        ("", None),
        ("not an address", None),
    ],
)
def test_the_apps_own_address_is_taken_from_openalgos_settings(
    tmp_path, no_site_env, written, name
):
    env = tmp_path / ".env"
    env.write_text(f"HOST_SERVER = '{written}'\n")
    assert readers.site_name(env) == name


def test_a_wildcard_covers_one_label_only(tmp_path):
    from cryptography import x509

    context = _site_context(tmp_path, "w", 1, names=["*.apps.example.invalid"])
    del context
    cert = x509.load_pem_x509_certificate(
        (tmp_path / "w.pem").read_bytes().split(b"-----END PRIVATE KEY-----\n")[1]
    )
    assert readers._covers(cert, "trade.apps.example.invalid")
    assert not readers._covers(cert, "apps.example.invalid")
    assert not readers._covers(cert, "a.b.apps.example.invalid")


def _openalgo(tmp_path: Path, rows: list | None) -> tuple[Path, Path]:
    app = tmp_path / "app"
    (app / "db").mkdir(parents=True)
    env = app / ".env"
    env.write_text("DATABASE_URL = 'sqlite:///db/openalgo.db'\nBROKER_API_SECRET = 'never-read'\n")
    if rows is not None:
        with closing(sqlite3.connect(app / "db" / "openalgo.db")) as db, db:
            db.execute(
                "CREATE TABLE master_contract_status (broker TEXT PRIMARY KEY,"
                " last_download_time DATETIME)"
            )
            db.executemany("INSERT INTO master_contract_status VALUES (?, ?)", rows)
    return app, env


def test_the_instrument_lists_date_is_read_from_openalgos_table(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    import time as clock

    clock.tzset()
    try:
        # 20:00 UTC on the 15th is 01:30 on the 16th in India: the date told is India's.
        rows = [("upstox", "2026-11-15 20:00:00.000000"), ("old", "2026-11-01 03:00:00")]
        app, env = _openalgo(tmp_path / "a", rows)
        assert readers.master_date(app, env) == "2026-11-16"
        assert not list((app / "db").glob("*-wal")), "read-only: nothing is made beside it"
        app, env = _openalgo(tmp_path / "b", [("upstox", None)])
        assert readers.master_date(app, env) == ""
        app, env = _openalgo(tmp_path / "c", None)
        assert readers.master_date(app, env) is None, "no file: unread, and none is made"
        assert not (app / "db" / "openalgo.db").exists()
        (app / "db" / "openalgo.db").write_bytes(b"")
        assert readers.master_date(app, env) is None, "no table: unread, never 'not refreshed'"
    finally:
        monkeypatch.undo()
        clock.tzset()


def test_gather_gives_every_reading_and_never_raises(tmp_path, monkeypatch):
    def broke(*args, **kwargs):
        raise RuntimeError("broke")

    for name in ("health", "openalgo", "enabled", "disk", "mem_available_mb",
                 "cert_hours_left", "master_date"):  # fmt: skip
        monkeypatch.setattr(readers, name, broke)
    slower = ("read", "counts", "backup", "drill", "historify_gb")
    for name in slower:
        monkeypatch.setattr(readers.posture, name, broke)
    seen = readers.gather(NOW, tmp_path, tmp_path / ".env")
    monkeypatch.setattr(readers, "cpu", broke)
    monkeypatch.setattr(readers, "mem_total_mb", broke)
    assert set(seen) == set(FINE) | {
        "posture", "counts", "backup", "drill", "historify_gb", "cpu", "mem_total_mb",
    }  # fmt: skip
    assert all(seen[key] is None for key in ("posture", "counts", "backup", "drill"))
    assert (seen["ist_minute"], seen["ist_date"]) == (600, "2026-11-16")
    assert seen["health"] == dict.fromkeys(checks.SERVICES) and seen["window_open"] is None
    assert seen["openalgo"] is None and seen["disk"] is None and seen["master_date"] is None
    assert all(r.level != checks.FINE for r in checks.run_checks(seen, {}))


def test_the_instrument_list_is_asked_only_when_it_matters(tmp_path, monkeypatch):
    asked = []
    feed = {"state": "connected", "market_window": {"open": True}}
    monkeypatch.setattr(readers, "health", lambda port: feed if port == 5580 else None)
    monkeypatch.setattr(readers, "openalgo", lambda: True)
    monkeypatch.setattr(readers, "cert_hours_left", lambda now, **named: 100.0)
    monkeypatch.setattr(readers, "master_date", lambda *a: asked.append(1) or "2026-11-16")
    assert readers.gather(NOW, tmp_path, tmp_path)["master_date"] == "2026-11-16"
    assert readers.gather(NOW, tmp_path, tmp_path, "2026-11-16")["master_date"] == "2026-11-16"
    early = NOW - 2 * 3600  # 08:00: yesterday's list is still right
    assert readers.gather(early, tmp_path, tmp_path, "2026-11-13")["master_date"] == "2026-11-13"
    feed["market_window"]["open"] = False
    assert readers.gather(NOW, tmp_path, tmp_path)["master_date"] is None
    assert len(asked) == 1


def _settings(tmp_path: Path) -> run.Settings:
    return run.Settings(tmp_path / "state", tmp_path / "app", tmp_path / "app" / ".env")


def _runs(settings, readings_by_run):
    codes, at = [], NOW
    for readings in readings_by_run:
        at += 120
        seen = {**readings, "now": at}
        codes.append(
            run.run_once(
                settings,
                clock=lambda at=at: at,
                gather=lambda *a, s=seen: s,
                installing=bool,
                env={},
            )  # fmt: skip
        )
    return codes


def test_a_failure_is_recorded_once_and_its_recovery_once(tmp_path):
    settings = _settings(tmp_path)
    down = {**FINE, "health": {**FINE["health"], "screener": None}}
    assert _runs(settings, [FINE, down, down, down, FINE, FINE]) == [0] * 6
    told = events.read_events(settings.spool)
    assert [(e.kind, e.fact("check"), e.fact("was")) for e in told] == [
        ("watch_failing", "service_screener", "fine"),
        ("watch_fine", "service_screener", "failing"),
    ]
    assert told[0].src == "watch" and told[0].fact("service") == "screener"
    assert told[0].fact("minutes") == "4" and told[0].run != told[1].run
    state = json.loads(settings.state_file.read_text())
    assert state["told"]["service_screener"] == "fine" and state["master_date"] == "2026-11-16"
    assert stat.S_IMODE(settings.state_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.spool.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.state_dir.stat().st_mode) == 0o700


def test_openalgos_own_silence_is_never_an_event(tmp_path):
    settings = _settings(tmp_path)
    silent = {**FINE, "openalgo": False}
    assert _runs(settings, [FINE, silent, silent, silent]) == [0] * 4
    assert events.read_events(settings.spool) == []
    report = json.loads(settings.report_file.read_text())
    assert {c["name"]: c["level"] for c in report["checks"]}["openalgo"] == "failing"


def test_the_reports_shape_is_pinned_for_the_system_page(tmp_path):
    settings = _settings(tmp_path)
    low = {**FINE, "disk": {"free_gb": 12.0, "inodes_free_pct": 80.0}}
    assert _runs(settings, [low]) == [0]
    report = json.loads(settings.report_file.read_text())
    assert set(report) == {"v", "at", "release", "heartbeat", "checks"} and report["v"] == 1
    assert report["heartbeat"] == {
        "state": "not_set",
        "words": "No outside heartbeat is set up: nobody is told if the whole server stops.",
    }
    assert report["at"] == int(NOW + 120) and report["release"] is None
    assert [c["name"] for c in report["checks"]] == [
        *(f"service_{name}" for name in checks.SERVICES),
        "openalgo", "login", "feed", "instrument_list", "disk", "memory", "certificate",
        "posture_locks", "posture_debug", "posture_modes", "posture_ports", "posture_reboot",
        "posture_updates", "assistant", "telegram_links", "backup", "drill", "historify",
    ]  # fmt: skip
    disk = report["checks"][-14]
    assert disk == {
        "name": "disk",
        "level": "warning",
        "told": "warning",
        "since": NOW + 120,
        "facts": {"free_gb": "12.0", "cause": "space"},
    }
    assert stat.S_IMODE(settings.report_file.stat().st_mode) == 0o640
    assert "never-read" not in settings.report_file.read_text()


def test_a_state_file_that_cannot_be_used_starts_afresh(tmp_path):
    settings = _settings(tmp_path)
    settings.state_dir.mkdir()
    for junk in ("{not json", "[]", '{"v": 99, "told": {"disk": "failing"}}', ""):
        settings.state_file.write_text(junk)
        assert run.load_state(settings.state_file)["told"] == {}
    settings.state_file.write_text('{"v": 1, "told": 7, "memory": {"disk": {}}}')
    assert run.load_state(settings.state_file)["told"] == {}
    assert _runs(settings, [FINE]) == [0]


def test_a_change_that_could_not_be_recorded_is_told_by_the_next_run(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    assert _runs(settings, [FINE]) == [0]
    low = {**FINE, "disk": {"free_gb": 3.0, "inodes_free_pct": 80.0}}
    real = events.record
    monkeypatch.setattr(events, "record", lambda *a, **k: False)
    assert _runs(settings, [low]) == [1]
    assert json.loads(settings.state_file.read_text())["told"].get("disk", "fine") == "fine"
    monkeypatch.setattr(events, "record", real)
    assert _runs(settings, [low, low]) == [0, 0]
    assert [e.kind for e in events.read_events(settings.spool)] == ["watch_failing"]


def test_a_second_run_at_the_same_time_does_nothing(tmp_path):
    import fcntl

    settings = _settings(tmp_path)
    settings.state_dir.mkdir()
    held = os.open(settings.state_dir / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert _runs(settings, [FINE]) == [0]
        assert not settings.state_file.exists()
    finally:
        os.close(held)


def test_the_check_option_prints_and_writes_nothing(tmp_path, monkeypatch, capsys, no_site_env):
    monkeypatch.setenv("SUDARSHAN_WATCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SUDARSHAN_OPENALGO_DIR", str(tmp_path))
    monkeypatch.setattr(readers, "gather", lambda *a, **k: dict(FINE))
    assert main(["--check"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert len(printed) == len(checks.SERVICES) + 20 and printed[0].split()[:2] == [
        "service_feed",
        "fine",
    ]
    assert printed[-1].split()[:2] == ["heartbeat", "not_set"]
    # L5: which name the certificate is asked for by, or that none is known and none is checked
    assert printed[-2].split()[:3] == ["certificate", "site", "unknown"]
    assert "no certificate is checked" in printed[-2] and readers.CERT_HOST_ENV in printed[-2]
    (tmp_path / ".env").write_text(f"HOST_SERVER = 'https://{OWN}'\n")
    assert main(["--check"]) == 0
    named = capsys.readouterr().out.splitlines()[-2]
    assert named.split()[:3] == ["certificate", "site", "set"] and named.endswith(OWN)
    monkeypatch.setenv("SUDARSHAN_WATCH_PING_URL", "https://beat.example.invalid/ping/abc123")
    assert main(["--check"]) == 0
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.split()[:2] == ["heartbeat", "set"] and "abc123" not in last
    assert not (tmp_path / "state").exists()


def test_settings_come_from_the_units_environment():
    seen = run.settings_from({"STATE_DIRECTORY": "/var/lib/sudarshan-watch:/x"})
    assert seen.state_dir == Path("/var/lib/sudarshan-watch")
    assert seen.env_file == Path("/opt/sudarshan/apps/openalgo/.env")
    assert seen.spool == events.WATCH_SPOOL


def test_runs_and_readers_leave_no_descriptor_open(tmp_path, local, tls):
    port, pages = local
    pages["/health"] = (200, b'{"state": "connected"}')
    app, env = _openalgo(tmp_path, [("upstox", "2026-11-16 03:00:00")])
    settings = _settings(tmp_path)
    down = {**FINE, "health": {**FINE["health"], "journal": None}}
    _runs(settings, [FINE])  # the folder and its files exist before the window
    readers.health(port), readers.cert_hours_left(NOW, tls, name=OWN), readers.master_date(app, env)
    watch = FdWatch()
    for _ in range(40):
        assert readers.health(port) and readers.health(1) is None
        assert readers.cert_hours_left(NOW, tls, name=OWN) > 30 and readers.master_date(app, env)
        assert readers.cert_hours_left(NOW, tls, name="gone.invalid") == readers.CERT_OTHER_SITE
        readers.disk(tmp_path), readers.mem_available_mb(), readers.enabled("feed")
    assert _runs(settings, [FINE, down, down, FINE] * 10) == [0] * 40
    watch.assert_clean()


def test_nothing_is_judged_while_a_release_is_being_installed(tmp_path):
    """The deploy tool stops and starts every service: the watch stands aside until its lock is
    free, keeps its state as it was and tells nothing."""
    import fcntl

    lock = tmp_path / "deploy.lock"
    assert readers.installing(lock) is False, "no lock file: no install"
    lock.write_text("")
    assert readers.installing(lock) is False
    settings = _settings(tmp_path)
    down = {**FINE, "health": {**FINE["health"], "feed": None, "alerts": None}, "openalgo": False}
    assert _runs(settings, [FINE]) == [0]
    before = settings.state_file.read_bytes()
    held = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert readers.installing(lock) is True
        for _ in range(3):
            code = run.run_once(
                settings, gather=lambda *a: down, installing=lambda: readers.installing(lock),
                env={},
            )  # fmt: skip
            assert code == 0
    finally:
        os.close(held)
    assert readers.installing(lock) is False
    assert settings.state_file.read_bytes() == before
    assert events.read_events(settings.spool) == []
