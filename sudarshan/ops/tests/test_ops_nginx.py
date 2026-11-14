"""The AI assistant's nginx lines and their read-only check (M19.3, P8 4.6).

nginx is not installed where the tests run, so the snippet is read as text: every address an AI
app calls is open to Anthropic's range only, the consent page stays open, the health line is for
the machine itself, and nothing in the files reaches past SUDARSHAN's own site. The check is run
against a stand-in for nginx on loopback.
"""

from __future__ import annotations

import ast
import http.server
import re
import shutil
import ssl
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sudarshan.ops import nginx_check

REPO = Path(__file__).resolve().parents[3]
NGINX = REPO / "sudarshan" / "ops" / "nginx"
SNIPPET = (NGINX / "sudarshan-mcp.conf").read_text(encoding="utf-8")
ZONE = (NGINX / "sudarshan-mcp-zone.conf").read_text(encoding="utf-8")
README = " ".join((REPO / "sudarshan" / "README.md").read_text(encoding="utf-8").split())
RANGE = "160.79.104.0/21"


def _code(text: str) -> str:
    return "\n".join(line.split("#", 1)[0].rstrip() for line in text.splitlines())


def _locations() -> dict[str, str]:
    found = re.findall(r"^location\s+(\S+\s+\S+)\s*\{(.*?)^\}", _code(SNIPPET), re.M | re.S)
    return {" ".join(match.split()): body for match, body in found}


def _lands_in(path: str) -> str:
    """The location nginx picks for ``path`` among the snippet's: an exact one first, then the
    longest ``^~`` prefix (the snippet has no regex location), else the site's own ``/``."""
    best = ""
    for key in _locations():
        kind, where = key.split()
        if kind == "=" and where == path:
            return key
        if kind == "^~" and path.startswith(where) and len(where) > len(best.split()[-1:] or ""):
            best = key
    return best or "the site's own location /"


def test_the_snippet_is_five_plain_locations():
    assert set(_locations()) == {
        "^~ /mcp", "= /mcp/healthz", "^~ /oauth/", "= /oauth/authorize", "^~ /.well-known/oauth-",
    }  # fmt: skip
    code = _code(SNIPPET)
    assert code.count("{") == code.count("}") == 5
    assert "~*" not in code and "location ~ " not in code, "no regex: the order cannot surprise"


def test_every_address_an_ai_app_calls_is_open_to_anthropics_range_only():
    for key in ("^~ /mcp", "^~ /oauth/", "^~ /.well-known/oauth-"):
        body = _locations()[key]
        assert re.findall(r"^\s*(allow|deny)\s+(\S+);", body, re.M) == [
            ("allow", RANGE),
            ("deny", "all"),
        ], key
    assert "limit_req zone=sudarshan_mcp burst=20 nodelay;" in _locations()["^~ /mcp"]
    assert "limit_req_zone $binary_remote_addr zone=sudarshan_mcp:1m rate=2r/s;" in _code(ZONE)
    assert _code(ZONE).strip().count(";") == 1, "the zone file is that one line"


def test_the_consent_page_stays_open_and_the_health_line_is_for_the_machine():
    assert not re.search(r"\b(allow|deny)\b", _locations()["= /oauth/authorize"])
    assert re.findall(r"^\s*(allow|deny)\s+(\S+);", _locations()["= /mcp/healthz"], re.M) == [
        ("allow", "127.0.0.1"),
        ("deny", "all"),
    ]


def test_every_route_the_app_registers_for_the_assistant_lands_in_the_right_location():
    """From the blueprints' own route lines, so a route upstream adds is placed or fails here."""
    http_routes = re.findall(
        r'@mcp_http_bp\.route\(\s*"([^"]*)"', (REPO / "blueprints" / "mcp_http.py").read_text()
    )
    oauth = (REPO / "blueprints" / "mcp_oauth.py").read_text()
    paths = {"/mcp" + route for route in http_routes} | {"/mcp/"}
    paths |= {"/oauth" + r for r in re.findall(r'@mcp_oauth_bp\.route\(\s*"([^"]*)"', oauth)}
    paths |= {
        re.sub(r"<[^>]+>", "x/y", r)
        for r in re.findall(r'@mcp_wellknown_bp\.route\(\s*"([^"]*)"', oauth)
    }
    assert len(paths) >= 11, paths
    for path in sorted(paths):
        landed = _lands_in(path)
        if path == "/oauth/authorize":
            assert landed == "= /oauth/authorize"
        elif path == "/mcp/healthz":
            assert landed == "= /mcp/healthz"
        else:
            assert landed in ("^~ /mcp", "^~ /oauth/", "^~ /.well-known/oauth-"), (path, landed)
    for other in (
        "/",
        "/dashboard",
        "/api/v1/placeorder",
        "/auth/login",
        "/family",
        "/.well-known/x",
    ):
        assert _lands_in(other) == "the site's own location /", other


def test_the_files_reach_nothing_but_openalgo_on_this_machine():
    for text in (SNIPPET, ZONE):
        code = _code(text)
        for word in ("include", "root ", "alias", "server_name", "listen", "ssl_", "upstream",
                     "rewrite", "return", "add_header", "lua", "if ("):  # fmt: skip
            assert word not in code, word
        assert "trinetra" not in text.lower()
    assert set(re.findall(r"proxy_pass\s+(\S+);", _code(SNIPPET))) == {"http://127.0.0.1:5000"}
    assert _code(SNIPPET).count("proxy_pass") == 5
    assert set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d+)?", _code(SNIPPET))) == {
        RANGE,
        "127.0.0.1",
    }


@pytest.mark.skipif(shutil.which("nginx") is None, reason="needs nginx for its own syntax test")
def test_nginx_itself_accepts_the_two_files(tmp_path):
    """``nginx -t`` on a configuration of the test's own that includes both files as the README
    says: the zone line at http level, the snippet inside one server block. Nothing is started
    and no other configuration is read."""
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log stderr;\nevents {{}}\n"
        f"http {{\n  access_log off;\n  client_body_temp_path {tmp_path}/body;\n"
        f"  proxy_temp_path {tmp_path}/proxy;\n  fastcgi_temp_path {tmp_path}/fastcgi;\n"
        f"  uwsgi_temp_path {tmp_path}/uwsgi;\n  scgi_temp_path {tmp_path}/scgi;\n"
        f"  include {NGINX}/sudarshan-mcp-zone.conf;\n"
        "  server {\n    listen 127.0.0.1:1;\n"
        f"    include {NGINX}/sudarshan-mcp.conf;\n"
        "    location / { proxy_pass http://127.0.0.1:5000; }\n  }\n}\n"
    )
    done = subprocess.run(
        ["nginx", "-t", "-q", "-c", str(conf), "-p", str(tmp_path), "-e", "stderr"],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    without_zone = conf.read_text().replace(f"  include {NGINX}/sudarshan-mcp-zone.conf;\n", "")
    conf.write_text(without_zone)
    done = subprocess.run(
        ["nginx", "-t", "-q", "-c", str(conf), "-p", str(tmp_path), "-e", "stderr"],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert done.returncode != 0, "a forgotten zone file is caught by nginx -t, before any reload"


def test_the_check_reads_statuses_and_judges_them():
    good = {path: (403 if refuse else 200) for path, refuse, _what in nginx_check.EXPECTED}
    code, lines = nginx_check.judge(good)
    assert code == 0 and lines[-1] == "The assistant's nginx lines are in effect."
    assert all(line.startswith("ok ") for line in lines[:-1])
    not_installed = dict(good) | {"/mcp": 401, "/oauth/token": 405}
    code, lines = nginx_check.judge(not_installed)
    assert code == 1 and sum(line.startswith("NO ") for line in lines) == 2
    assert "NOT refused" in "\n".join(lines) and "README" in lines[-1]
    too_tight = dict(good) | {"/oauth/authorize": 403}
    assert nginx_check.judge(too_tight)[0] == 1
    assert nginx_check.judge(dict(good) | {"/mcp/healthz": 404})[0] == 0, "remote MCP is off"
    assert nginx_check.judge({"/": None}) == (
        2,
        ["nginx did not answer on this server for that address. Nothing was checked."],
    )


def test_the_check_needs_the_apps_address_and_asks_only_for_it(capsys):
    asked = []

    def ask(path, host):
        asked.append((path, host))
        return 403 if {p: r for p, r, _ in nginx_check.EXPECTED}[path] else 200

    assert nginx_check.main(["--host", "https://app.example.invalid/"], ask) == 0
    assert {host for _path, host in asked} == {"app.example.invalid"}
    assert [path for path, _host in asked] == [path for path, _r, _w in nginx_check.EXPECTED]
    assert "in effect" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        nginx_check.main([], ask)  # no default host: another site must never be asked
    assert nginx_check.main(["--host", "a b"], ask) == 2
    asked.clear()
    assert nginx_check.main(["--host", "app.example.invalid"], lambda path, host: None) == 2
    capsys.readouterr()


class _Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append((self.path, self.headers.get("Host")))
        self.send_response(403 if self.path == "/mcp" else 200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def test_status_of_asks_loopback_with_the_host_and_reads_only_the_status(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "nginx-check-test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256())
    )  # fmt: skip
    pem = tmp_path / "test.pem"
    pem.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption())
        + cert.public_bytes(serialization.Encoding.PEM)
    )  # fmt: skip
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    server.seen = []
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, name="nginx-check-test", daemon=True)
    thread.start()
    try:
        address = server.server_address
        assert nginx_check.status_of("/mcp", "app.example.invalid", address) == 403
        assert nginx_check.status_of("/", "app.example.invalid", address) == 200
        assert server.seen == [("/mcp", "app.example.invalid"), ("/", "app.example.invalid")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    assert nginx_check.status_of("/", "app.example.invalid", ("127.0.0.1", 1), 1.0) is None


def test_the_check_opens_no_file_and_starts_nothing():
    tree = ast.parse((REPO / "sudarshan" / "ops" / "nginx_check.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "argparse", "http", "re", "ssl", "sys", "collections"}
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "open" not in called
    assert nginx_check.ADDRESS == ("127.0.0.1", 443)


def test_the_readme_gives_the_owners_steps():
    for phrase in (
        "## nginx lines for the AI assistant (M19.3)",
        "sudo install -m 644 -o root -g root sudarshan/ops/nginx/sudarshan-mcp-zone.conf "
        "/etc/nginx/conf.d/sudarshan-mcp-zone.conf",
        "sudo install -m 644 -o root -g root sudarshan/ops/nginx/sudarshan-mcp.conf "
        "/etc/nginx/snippets/sudarshan-mcp.conf",
        "include snippets/sudarshan-mcp.conf;",
        "sudo nginx -t",
        "sudo systemctl reload nginx",
        "never `restart`",
        "sudo -u sudarshan .venv/bin/python -m sudarshan.ops.nginx_check --host",
        RANGE,
        "`SUDARSHAN_WATCH_CERT_HOST`",
    ):
        assert phrase in README, phrase
