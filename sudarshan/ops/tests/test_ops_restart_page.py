"""The restart page's nginx lines (M1.17, T8).

One static file, ``frontend/public/images/restarting.html``, is what a phone sees while the app
restarts. nginx shows it for its own "bad gateway" and "gateway timeout" answers once the owner
has installed ``sudarshan/ops/nginx/sudarshan-restart.conf``. The lines are read as text, given to
``nginx -t``, and, where nginx is installed, run for real on a socket file in the test's folder
with nothing behind them: the status is kept, the body is the file, a family phone gets the same
page and not the "for the account owner" one, and the family API keeps its own answer.
"""

from __future__ import annotations

import http.client
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
NGINX = REPO / "sudarshan" / "ops" / "nginx"
SNIPPET = (NGINX / "sudarshan-restart.conf").read_text(encoding="utf-8")
ZONES = (NGINX / "sudarshan-family-zones.conf").read_text(encoding="utf-8")
PAGE = REPO / "frontend" / "public" / "images" / "restarting.html"
README = " ".join((REPO / "sudarshan" / "README.md").read_text(encoding="utf-8").split())
INSTALLED = "/usr/share/sudarshan/restarting.html"
INTERNAL = "/images/sudarshan-restarting"
VIEWER = {"Cookie": "__Host-sudarshan_family=abc"}


def _code(text: str) -> str:
    return "\n".join(line.split("#", 1)[0].rstrip() for line in text.splitlines())


def test_the_snippet_is_one_error_page_line_and_one_internal_location():
    code = _code(SNIPPET)
    assert re.findall(r"^error_page\s+(.*);", code, re.M) == [f"502 504 {INTERNAL}"]
    locations = re.findall(r"^location\s+(\S+\s+\S+)\s*\{(.*?)^\}", code, re.M | re.S)
    assert [" ".join(key.split()) for key, _body in locations] == [f"= {INTERNAL}"]
    body = locations[0][1]
    assert re.search(r"^\s*internal;", body, re.M), "never answered to a request from outside"
    assert re.search(rf"^\s*alias {re.escape(INSTALLED)};", body, re.M)
    assert 'default_type "text/html; charset=utf-8";' in body


def test_the_status_is_kept_and_nothing_else_is_touched():
    code = _code(SNIPPET)
    # No "=200" or "=503": a restart must never read as success, nor as another failure.
    assert "=" not in re.search(r"^error_page\s+(.*);", code, re.M).group(1).replace(INTERNAL, "")
    # 503 is left out: nginx's own 503 is a limit, and the app's own answers are passed on as they are.
    assert "503" not in code and "500" not in code
    for word in ("proxy_pass", "proxy_intercept_errors", "include", "root ", "server_name",
                 "listen", "ssl_", "upstream", "rewrite", "return", "add_header", "if ("):  # fmt: skip
        assert word not in code, word
    assert "trinetra" not in SNIPPET.lower()
    # The file nginx reads is root's copy, never the app folder (the service user can write there).
    assert "/opt/" not in code and "frontend" not in code


def test_the_page_is_read_under_a_name_a_family_phone_may_reach():
    """The viewer guard runs again on the way to the page: under a name outside the family
    allow-list a family phone would be shown the refusal page while the app restarts."""
    allowed = [
        pattern
        for pattern, value in re.findall(r'^\s*"~(\^1:[^"]*)"\s+(\d);', ZONES, re.M)
        if value == "0"
    ]
    assert any(re.search(pattern, f"1:{INTERNAL}") for pattern in allowed), allowed
    # And it is not the address of the app's own copy, which the family app fetches and keeps.
    assert INTERNAL != "/images/restarting.html"
    assert "'/images/restarting.html'" in (REPO / "frontend" / "public" / "sw.js").read_text()


def test_the_page_exists_and_the_readme_says_how_to_install_it():
    assert PAGE.is_file()
    for phrase in (
        "## The restart page (M1.17)",
        f"sudo install -D -m 644 -o root -g root frontend/public/images/restarting.html {INSTALLED}",
        "sudo install -m 644 -o root -g root sudarshan/ops/nginx/sudarshan-restart.conf "
        "/etc/nginx/snippets/sudarshan-restart.conf",
        "include snippets/sudarshan-restart.conf;",
        "sudo nginx -t",
        "sudo systemctl reload nginx",
    ):
        assert phrase in README, phrase


def _site(tmp_path: Path, *, family: bool) -> Path:
    """A site of the test's own with the lines installed as the README says, and nothing behind it."""
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    share = tmp_path / "share"
    share.mkdir()
    shutil.copy(PAGE, share / "restarting.html")
    (snippets / "sudarshan-restart.conf").write_text(
        SNIPPET.replace(INSTALLED, str(share / "restarting.html")), encoding="utf-8"
    )
    for name in ("sudarshan-family.conf", "sudarshan-family-proxy.conf", "sudarshan-mcp.conf"):
        shutil.copy(NGINX / name, snippets / name)
    # As root nginx's workers would drop to an account that cannot read the test's folder.
    user = "user root;\n" if os.geteuid() == 0 else ""
    includes = (
        "    include snippets/sudarshan-family.conf;\n    include snippets/sudarshan-mcp.conf;\n"
        if family
        else ""
    )
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        f"{user}pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\ndaemon off;\n"
        f"events {{}}\nhttp {{\n  access_log off;\n  client_body_temp_path {tmp_path}/body;\n"
        f"  proxy_temp_path {tmp_path}/proxy;\n  fastcgi_temp_path {tmp_path}/fastcgi;\n"
        f"  uwsgi_temp_path {tmp_path}/uwsgi;\n  scgi_temp_path {tmp_path}/scgi;\n"
        f"  include {NGINX}/sudarshan-family-zones.conf;\n"
        f"  include {NGINX}/sudarshan-mcp-zone.conf;\n"
        f"  server {{\n    listen unix:{tmp_path}/site.sock;\n{includes}"
        "    include snippets/sudarshan-restart.conf;\n"
        # The app is down: nothing listens on this socket file.
        f"    location / {{ proxy_pass http://unix:{tmp_path}/app.sock; }}\n  }}\n}}\n",
        encoding="utf-8",
    )
    return conf


class _Unix(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("sudarshan.test", timeout=10)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(self._path)


def _ask(sock: Path, method: str, path: str, headers: dict | None = None):
    connection = _Unix(str(sock))
    try:
        connection.request(method, path, body=b"{}" if method == "POST" else None, headers=headers or {})
        answer = connection.getresponse()
        return answer.status, answer.getheader("Content-Type") or "", answer.read()
    finally:
        connection.close()


needs_nginx = pytest.mark.skipif(shutil.which("nginx") is None, reason="needs nginx itself")


@needs_nginx
@pytest.mark.parametrize("family", [False, True])
def test_nginx_itself_accepts_the_lines(tmp_path, family):
    conf = _site(tmp_path, family=family)
    done = subprocess.run(
        ["nginx", "-t", "-q", "-c", str(conf), "-p", str(tmp_path), "-e", "stderr"],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr


@needs_nginx
def test_a_real_nginx_with_the_app_down_shows_the_page_under_the_status_it_had(tmp_path):
    conf = _site(tmp_path, family=True)
    sock = tmp_path / "site.sock"
    server = subprocess.Popen(
        ["nginx", "-c", str(conf), "-p", str(tmp_path), "-e", str(tmp_path / "error.log")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 15
        while not sock.exists():
            assert server.poll() is None, (tmp_path / "error.log").read_text()
            assert time.monotonic() < deadline, "nginx did not open its socket"
            time.sleep(0.05)
        page = PAGE.read_bytes()

        # The owner's page load, and an order sent at the wrong second: bad gateway, with the page.
        for method, path in (("GET", "/dashboard"), ("GET", "/"), ("POST", "/api/v1/placeorder")):
            status, kind, body = _ask(sock, method, path)
            assert (status, kind) == (502, "text/html; charset=utf-8"), (method, path, status)
            assert body == page, (method, path)

        # A family phone opening the family view gets the same page, not the refusal page.
        status, _kind, body = _ask(sock, "GET", "/family/", VIEWER)
        assert status == 502 and body == page
        # What it may not reach is refused exactly as before.
        status, _kind, body = _ask(sock, "GET", "/dashboard", VIEWER)
        assert status == 403 and b"for the account owner" in body
        # The family API keeps its own answer, in its own envelope.
        status, kind, body = _ask(sock, "GET", "/family/api/me", VIEWER)
        assert status == 503 and kind.startswith("application/json")
        assert b'"code":"unavailable"' in body
        # The name is nginx's alone: from outside it is not there.
        status, _kind, body = _ask(sock, "GET", INTERNAL)
        assert status == 404 and body != page
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
