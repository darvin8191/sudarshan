"""M12.2: the nginx lines that fence the alert links, and the deploy tool's reminder.

Three halves:

* **The text.** The two files in ``sudarshan/ops/nginx/`` are read and every rule is held: the four
  sender addresses and no other, each webhook location fenced, the forwarded-address headers set by
  nginx and the three the app would believe first cleared, the token out of both logs, the hang-up
  ignored, Chartink refused. Always runs.
* **A real nginx.** The same files in a real nginx in front of a fake app (only the app's port and,
  for the allowed case, the test's own address are changed, in a copy). Skipped when no nginx
  binary is found: ``SUDARSHAN_NGINX``, then ``nginx`` on PATH, then ``/usr/sbin/nginx``.
* **The deploy tool.** After an install it looks for the lines and records one notice when they are
  missing; it never fails or rolls back over them.
"""

from __future__ import annotations

import http.client
import os
import re
import select
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_deploy_calendar import Box, whole_tool
from test_deploy_notices import install_box, told

from sudarshan.alerts.notices import AT_ONCE, KIND_ORDER, compose
from sudarshan.autologin.schedule import IST
from sudarshan.ops.events import KINDS

REPO = Path(__file__).resolve().parents[3]
NGINX_DIR = REPO / "sudarshan" / "ops" / "nginx"
ZONES = NGINX_DIR / "sudarshan-webhooks-zones.conf"
SERVER = NGINX_DIR / "sudarshan-webhooks.conf"
README = (REPO / "sudarshan" / "README.md").read_text()
#: TradingView's published sender addresses ("About webhooks"). Public, and the only addresses here.
TRADINGVIEW = {"52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"}
FENCED = ("/sudarshan/tv/", "/strategy/webhook/", "/flow/webhook/")
TOKEN = "sdt_" + "k" * 43


def _code(text: str) -> str:
    """The file without its comments."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _locations(text: str) -> dict[str, str]:
    found = re.findall(r"location\s+\^~\s+(\S+)\s*\{(.*?)\n\}", _code(text), flags=re.S)
    return dict(found)


# ---- the text -----------------------------------------------------------------------------------


def test_the_sender_list_is_tradingviews_four_addresses_and_nothing_else():
    body = re.search(r"geo\s+\$sud_tv_sender\s*\{(.*?)\}", _code(ZONES.read_text()), flags=re.S)
    assert body, "the geo block is missing"
    rows = dict(re.findall(r"(\S+)\s+(\S+);", body.group(1)))
    assert rows.pop("default") == "0"  # everyone else is refused
    assert set(rows) == TRADINGVIEW and set(rows.values()) == {"1"}
    # and no other address anywhere in either file (the public repo holds no server address)
    both = ZONES.read_text() + SERVER.read_text()
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", both)) == TRADINGVIEW | {"127.0.0.1"}


def test_every_webhook_location_is_there_and_chosen_before_any_regex_location():
    assert set(_locations(SERVER.read_text())) == {*FENCED, "/chartink/webhook/"}


@pytest.mark.parametrize("path", FENCED)
def test_a_fenced_location_holds_every_line(path):
    body = _locations(SERVER.read_text())[path]
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    assert lines[0] == "if ($sud_tv_sender = 0) { return 404; }"  # refused before anything else
    for needed in (
        "limit_req zone=sud_hooks burst=10 nodelay;",
        "limit_req_status 429;",
        "access_log off;",
        "error_log /dev/null crit;",
        "proxy_ignore_client_abort on;",
        "proxy_read_timeout 180s;",
        "proxy_set_header Host $host;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $remote_addr;",
        'proxy_set_header CF-Connecting-IP "";',
        'proxy_set_header True-Client-IP "";',
        'proxy_set_header X-Client-IP "";',
        "proxy_pass http://127.0.0.1:5000;",
    ):
        assert needed in lines, (path, needed)
    # what the sender wrote is never handed on as an address
    assert "$proxy_add_x_forwarded_for" not in body and "$http_x_" not in body


def test_the_headers_cleared_are_the_ones_the_app_believes_before_x_real_ip():
    """utils/ip_helper.get_real_ip reads these, in this order, when TRUST_PROXY_HEADERS is on."""
    helper = (REPO / "utils" / "ip_helper.py").read_text()
    read = re.findall(r'request\.headers\.get\("([^"]+)"\)', helper)
    assert read[:5] == [
        "CF-Connecting-IP",
        "True-Client-IP",
        "X-Real-IP",
        "X-Forwarded-For",
        "X-Client-IP",
    ], read
    body = _locations(SERVER.read_text())["/sudarshan/tv/"]
    for name in read[:5]:
        assert re.search(rf"proxy_set_header\s+{re.escape(name)}\s", body), name


def test_the_automation_link_takes_8_kib_and_chartink_is_refused_for_everyone():
    locations = _locations(SERVER.read_text())
    assert "client_max_body_size 8k;" in locations["/sudarshan/tv/"]
    chartink = " ".join(locations["/chartink/webhook/"].split())
    assert "return 404;" in chartink and "proxy_pass" not in chartink
    assert "access_log off;" in chartink


def test_the_zone_is_two_a_second_per_address():
    zones = _code(ZONES.read_text())
    assert "limit_req_zone $binary_remote_addr zone=sud_hooks:1m rate=2r/s;" in zones


def test_the_readme_gives_the_owner_the_steps():
    section = README.split("## The fence around the alert links (M12.2)")[1].split("\n## ")[0]
    for needed in (
        "/etc/nginx/conf.d/sudarshan-webhooks-zones.conf",
        "/etc/nginx/snippets/sudarshan-webhooks.conf",
        "include snippets/sudarshan-webhooks.conf;",
        "sudo nginx -t && sudo systemctl reload nginx",
    ):
        assert needed in section, needed
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", section.replace("127.0.0.1", ""))


# ---- a real nginx -------------------------------------------------------------------------------


def _nginx() -> str | None:
    for candidate in (os.environ.get("SUDARSHAN_NGINX"), shutil.which("nginx"), "/usr/sbin/nginx"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _App(BaseHTTPRequestHandler):
    """The fake app: records each request, answers 200, and can be slow."""

    seen: list[dict] = []
    delay = 0.0
    protocol_version = "HTTP/1.0"

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        gone, until = False, time.monotonic() + type(self).delay
        while time.monotonic() < until and not gone:  # did nginx drop the request while we worked?
            ready, _, _ = select.select([self.connection], [], [], 0.05)
            gone = bool(ready) and self.connection.recv(1, socket.MSG_PEEK) == b""
        type(self).seen.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
                "dropped": gone,
            }
        )
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class Front:
    """nginx with the two shipped files, in front of the fake app."""

    def __init__(self, root: Path, binary: str, *, allow_me: bool, edit=lambda text: text):
        _App.seen, _App.delay = [], 0.0
        self.app = ThreadingHTTPServer(("127.0.0.1", 0), _App)
        threading.Thread(target=self.app.serve_forever, daemon=True, name="fake-app").start()
        self.port, self.root = _free_port(), root
        zones = ZONES.read_text()
        if allow_me:  # the test's own address joins the list, in the copy only
            zones = zones.replace("default 0;", "default 0;\n    127.0.0.1 1;")
        (root / "zones.conf").write_text(zones)
        upstream = f"127.0.0.1:{self.app.server_address[1]}"
        (root / "server.conf").write_text(
            edit(SERVER.read_text()).replace("127.0.0.1:5000", upstream)
        )
        (root / "nginx.conf").write_text(
            f"pid {root}/nginx.pid;\nerror_log {root}/error.log;\ndaemon off;\n"
            "events {}\nhttp {\n"
            f"  access_log {root}/access.log;\n  include {root}/zones.conf;\n"
            f"  client_body_temp_path {root}/body;\n  proxy_temp_path {root}/proxy;\n"
            f"  fastcgi_temp_path {root}/f;\n  uwsgi_temp_path {root}/u;\n  scgi_temp_path {root}/s;\n"
            f"  server {{\n    listen 127.0.0.1:{self.port};\n    include {root}/server.conf;\n"
            "    location / { return 200 site; }\n  }\n}\n"
        )
        self.proc = subprocess.Popen(
            [binary, "-c", str(root / "nginx.conf"), "-e", str(root / "error.log")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        self.close()
        raise AssertionError((root / "error.log").read_text())

    def post(self, path: str, body: bytes = b"{}", headers: dict | None = None) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", path, body=body, headers=headers or {})
            answer = conn.getresponse()
            answer.read()
            return answer.status
        finally:
            conn.close()

    def logs(self) -> str:
        return "".join(
            (self.root / name).read_text()
            for name in ("access.log", "error.log")
            if (self.root / name).exists()
        )

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.app.shutdown()
        self.app.server_close()


@pytest.fixture
def front(tmp_path, request):
    binary = _nginx()
    if binary is None:
        pytest.skip("no nginx binary on this machine")
    made = Front(tmp_path, binary, allow_me=getattr(request, "param", True))
    yield made
    made.close()


@pytest.mark.parametrize("front", [False], indirect=True)
@pytest.mark.parametrize("path", [*FENCED, "/chartink/webhook/"])
def test_a_sender_that_is_not_tradingview_never_reaches_the_app(front, path):
    assert front.post(path + TOKEN) == 404
    assert _App.seen == []
    assert front.post("/") == 200  # the rest of the site is as it was
    assert TOKEN not in front.logs()


@pytest.mark.parametrize("path", FENCED)
def test_an_allowed_sender_reaches_the_app_with_the_address_nginx_saw(front, path):
    spoof = {
        "CF-Connecting-IP": "52.89.214.238",
        "True-Client-IP": "34.212.75.30",
        "X-Client-IP": "54.218.53.128",
        "X-Real-IP": "52.32.178.7",
        "X-Forwarded-For": "52.32.178.7, 10.0.0.1",
    }
    assert front.post(path + TOKEN, b'{"v": 1}', spoof) == 200
    (seen,) = _App.seen
    assert seen["path"] == path + TOKEN and seen["body"] == b'{"v": 1}'
    heads = seen["headers"]
    assert heads["x-real-ip"] == "127.0.0.1" and heads["x-forwarded-for"] == "127.0.0.1"
    for cleared in ("cf-connecting-ip", "true-client-ip", "x-client-ip"):
        assert cleared not in heads, cleared
    assert TOKEN not in front.logs()


def test_chartink_is_refused_for_an_allowed_sender_too(front):
    assert front.post("/chartink/webhook/" + TOKEN) == 404 and _App.seen == []


def test_an_alert_too_large_is_refused_from_its_header_and_the_token_is_not_logged(front):
    assert front.post("/sudarshan/tv/" + TOKEN, b"x" * (20 * 1024)) == 413
    assert _App.seen == [] and TOKEN not in front.logs()


def test_a_flood_is_answered_429_and_the_token_is_not_logged(front):
    codes = [front.post("/sudarshan/tv/" + TOKEN) for _ in range(40)]
    assert codes[0] == 200 and 429 in codes
    assert codes.count(200) <= 14  # the burst of 10, the first, and what 2 a second refills
    assert TOKEN not in front.logs()


def _hang_up(front: Front) -> dict:
    _App.delay = 1.0
    with socket.create_connection(("127.0.0.1", front.port), timeout=5) as raw:
        body = b'{"v": 1}'
        raw.sendall(
            f"POST /sudarshan/tv/{TOKEN} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        time.sleep(0.2)  # nginx has handed it on; now the sender goes away
    deadline = time.monotonic() + 10
    while not _App.seen and time.monotonic() < deadline:
        time.sleep(0.05)
    (seen,) = _App.seen
    return seen


def test_an_alert_is_finished_after_the_sender_hangs_up(front):
    """TradingView stops waiting after 3 s. nginx must keep the request open to the app."""
    seen = _hang_up(front)
    assert seen["body"] == b'{"v": 1}' and seen["dropped"] is False
    assert TOKEN not in front.logs()


def test_without_that_line_nginx_drops_the_request_when_the_sender_goes(tmp_path):
    """The control: the same hang-up with the line taken out is seen by the app as a drop, so the
    test above cannot pass by accident."""
    binary = _nginx()
    if binary is None:
        pytest.skip("no nginx binary on this machine")
    line = "    proxy_ignore_client_abort on;\n"
    assert SERVER.read_text().count(line) == len(FENCED)
    made = Front(tmp_path, binary, allow_me=True, edit=lambda text: text.replace(line, ""))
    try:
        assert _hang_up(made)["dropped"] is True
    finally:
        made.close()


# ---- the deploy tool's reminder -----------------------------------------------------------------


def _nginx_folder(box: Box, *, zones: bool, include: str | None) -> None:
    etc = box.root / "nginx"
    (etc / "conf.d").mkdir(parents=True)
    (etc / "sites-enabled").mkdir()
    (etc / "snippets").mkdir()
    shutil.copy(SERVER, etc / "snippets" / "sudarshan-webhooks.conf")
    if zones:
        shutil.copy(ZONES, etc / "conf.d" / "sudarshan-webhooks-zones.conf")
    site = (
        "server {\n    listen 443 ssl;\n" + (include or "") + "    location / { return 200; }\n}\n"
    )
    (etc / "sites-enabled" / "site.conf").write_text(site)


INCLUDE = "    include snippets/sudarshan-webhooks.conf;\n"


@whole_tool
@pytest.mark.parametrize(
    ("zones", "include"),
    [(False, None), (True, None), (False, INCLUDE), (True, "    # " + INCLUDE.strip() + "\n")],
)
def test_missing_lines_are_a_warning_and_one_notice_never_a_rollback(tmp_path, zones, include):
    box = install_box(tmp_path)
    _nginx_folder(box, zones=zones, include=include)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.deployed() == "v1.0.1"  # the release is installed all the same
    assert "WARNING: the nginx lines that fence the alert links are not installed" in done.stdout
    found = told(box)
    assert [e.kind for e in found] == ["installed", "webhooks_open"]
    assert found[1].id == "deploy-webhooks-v1.0.1" and len({e.run for e in found}) == 1
    again = box.run("--redeploy")  # the same release again: the warning, not a second notice
    assert again.returncode == 0 and [e.kind for e in told(box)].count("webhooks_open") == 1


@whole_tool
def test_lines_in_place_are_silent(tmp_path):
    box = install_box(tmp_path)
    _nginx_folder(box, zones=True, include=INCLUDE)
    done = box.run()
    assert done.returncode == 0 and "fence the alert links" not in done.stdout
    assert [e.kind for e in told(box)] == ["installed"]


@whole_tool
def test_a_server_without_nginx_is_not_checked(tmp_path):
    box = install_box(tmp_path)
    done = box.run()
    assert done.returncode == 0 and "fence the alert links" not in done.stdout
    assert [e.kind for e in told(box)] == ["installed"]


def test_the_notice_is_one_sentence_for_the_owner_and_waits_for_the_close():
    assert "webhooks_open" in KINDS and "webhooks_open" in KIND_ORDER
    assert "webhooks_open" not in AT_ONCE  # a reminder, not a failure
    from sudarshan.ops.events import Event

    event = Event("deploy-webhooks-v1.0.1", "webhooks_open", 0.0, "r", "deploy", {"tag": "v1.0.1"})
    text = compose([event], datetime.now(IST))
    assert "The fence around the alert links" in text and "Nothing stopped working" in text
    assert "## The fence around the alert links (M12.2)" in README  # the section the notice names
    assert not re.search(r"\b(40[0-9]|nginx -t|HTTP)\b", text)
