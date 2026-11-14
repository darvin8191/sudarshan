"""The restart page's nginx lines (M1.17, T8; review M2).

One static file, ``frontend/public/images/restarting.html``, is what a phone sees while the app
restarts. nginx shows it for its own "bad gateway" and "gateway timeout" answers once the owner
has installed ``sudarshan/ops/nginx/sudarshan-restart.conf``, which ``sudarshan/ops/restart_page.py``
writes from the page: the page is inside the lines, so there is no file whose absence could turn
a 502 into a 404. The lines are read as text, given to ``nginx -t``, and, where nginx is
installed, run for real on a socket file in the test's folder with the app down: the status is
kept (502 with nothing listening, 504 with an app that never answers), the body is the page, a
family phone gets the same page and not the "for the account owner" one, and the family API
keeps its own answer.
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

from sudarshan.ops import restart_page

REPO = Path(__file__).resolve().parents[3]
NGINX = REPO / "sudarshan" / "ops" / "nginx"
SNIPPET = (NGINX / "sudarshan-restart.conf").read_text(encoding="utf-8")
PAGE = REPO / "frontend" / "public" / "images" / "restarting.html"
README = " ".join((REPO / "sudarshan" / "README.md").read_text(encoding="utf-8").split())
VIEWER = {"Cookie": "__Host-sudarshan_family=abc"}
#: nginx reads no parameter longer than this.
NGINX_PARAMETER = 4096


def _directives() -> str:
    """The lines outside the page's own text: comments and every quoted piece taken out."""
    code = "\n".join(line for line in SNIPPET.splitlines() if not line.startswith("#"))
    return re.sub(r"'(?:[^'\\]|\\.)*'", "''", code, flags=re.S)


def test_the_lines_are_the_ones_written_from_the_page():
    assert restart_page.check(), "run: python -m sudarshan.ops.restart_page --write"
    assert restart_page.main(["--check"]) == 0


def test_each_answer_has_a_named_location_that_returns_its_own_status_with_the_page():
    code = _directives()
    assert re.findall(r"^error_page\s+(.*);", code, re.M) == [
        "502 @sudarshan_restarting_502", "504 @sudarshan_restarting_504",
    ]  # fmt: skip
    locations = dict(re.findall(r"^location\s+(\S+)\s*\{(.*?)^\}", code, re.M | re.S))
    assert sorted(locations) == ["@sudarshan_restarting_502", "@sudarshan_restarting_504"]
    for status in (502, 504):
        body = locations[f"@sudarshan_restarting_{status}"]
        # The status is written beside the page: a restart never reads as success or "not found".
        assert re.findall(r"^\s*return\s+(\d+)\s", body, re.M) == [str(status)]
        assert 'default_type "text/html; charset=utf-8";' in body
        assert 'add_header Cache-Control "no-store" always;' in body


def test_no_file_is_read_so_none_can_be_missing():
    """Review M2: with the page read from a file, a missing or unreadable file turned every 502
    and 504 into 404, an order's answer included."""
    code = _directives()
    for word in ("alias", "root ", "try_files", "/usr/share", "/opt/", "frontend", "internal;"):
        assert word not in code, word
    for phrase in ("/usr/share/sudarshan", "restarting.html /"):
        assert phrase not in README, phrase


def test_nothing_else_is_touched():
    code = _directives()
    # 503 is left out: nginx's own 503 is a limit, and the app's own answers are passed on as they are.
    assert "503" not in code and "500" not in code and "404" not in code
    for word in ("proxy_pass", "proxy_intercept_errors", "include", "server_name", "listen",
                 "ssl_", "upstream", "rewrite", "if ("):  # fmt: skip
        assert word not in code, word
    assert "trinetra" not in SNIPPET.lower()


def test_the_page_is_given_whole_in_pieces_nginx_can_read():
    page = PAGE.read_text(encoding="utf-8")
    cut = restart_page.pieces(page)
    assert "".join(cut) == page and len(cut) > 1
    for piece in cut:
        assert len(restart_page.escaped(piece).encode("utf-8")) + 2 < NGINX_PARAMETER
    # A quote or a backslash in the page comes back as itself; a $ would be read as a variable.
    assert "'" in page and restart_page.escaped("a'b\\c") == "a\\'b\\\\c"
    with pytest.raises(restart_page.PageRefused):
        restart_page.render("<p>costs $5</p>")
    assert "$" not in page


def test_a_changed_page_is_noticed(tmp_path):
    page, target = tmp_path / "page.html", tmp_path / "lines.conf"
    page.write_text("<p>Restarting</p>", encoding="utf-8")
    assert restart_page.check(page, target) is False  # no lines yet
    target.write_text(restart_page.render("<p>Restarting</p>"), encoding="utf-8")
    assert restart_page.check(page, target) is True
    page.write_text("<p>Restarting.</p>", encoding="utf-8")
    assert restart_page.check(page, target) is False


def test_the_readme_says_how_to_install_the_lines():
    assert PAGE.is_file()
    for phrase in (
        "## The restart page (M1.17)",
        "sudo install -m 644 -o root -g root sudarshan/ops/nginx/sudarshan-restart.conf "
        "/etc/nginx/snippets/sudarshan-restart.conf",
        "include snippets/sudarshan-restart.conf;",
        "sudo nginx -t",
        "sudo systemctl reload nginx",
        "python -m sudarshan.ops.restart_page --write",
    ):
        assert phrase in README, phrase


def _site(tmp_path: Path, *, family: bool) -> Path:
    """A site of the test's own with the lines installed as the README says, and nothing behind it."""
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    # The lines exactly as shipped, and no copy of the page anywhere nginx could read one.
    shutil.copy(NGINX / "sudarshan-restart.conf", snippets / "sudarshan-restart.conf")
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
        # An app that took the request and never answers: nginx gives up after a second.
        f"    location /slow {{ proxy_read_timeout 1s; proxy_pass http://unix:{tmp_path}/slow.sock; }}\n"
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
        connection.request(
            method, path, body=b"{}" if method == "POST" else None, headers=headers or {}
        )
        answer = connection.getresponse()
        if answer.getheader("Cache-Control") is not None:
            assert answer.getheader("Cache-Control") == "no-store"
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

        # The owner's page load, and an order sent at the wrong second: bad gateway, with the
        # page. Never 404 (review M2): the phone reads that as an order refused.
        for method, path in (("GET", "/dashboard"), ("GET", "/"), ("POST", "/api/v1/placeorder"),
                             ("POST", "/api/v1/closeposition")):  # fmt: skip
            status, kind, body = _ask(sock, method, path)
            assert (status, kind) == (502, "text/html; charset=utf-8"), (method, path, status)
            assert body == page, (method, path)

        # An app that took the request and did not answer in time: gateway timeout, with the page.
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            slow.bind(str(tmp_path / "slow.sock"))
            slow.listen(4)
            for method in ("GET", "POST"):
                status, kind, body = _ask(sock, method, "/slow")
                assert (status, kind) == (504, "text/html; charset=utf-8"), (method, status)
                assert body == page
        finally:
            slow.close()

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
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
