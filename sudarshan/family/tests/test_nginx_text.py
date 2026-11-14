"""O3 (P6; M2.7, M2.11, M2.16; cross-design Y7, Y33; review H2, L8): the family view's nginx lines.

The three files in ``sudarshan/ops/nginx/`` are what the owner installs once on the server. Two halves:

* **The text.** The files are parsed (a small reader of nginx's syntax below) and every rule of the
  design is held: the guard's allow-list, word for word, and what it decides for the paths that
  matter; the guard as one line at server level, so it covers ``/socket.io/`` and ``/ws`` whatever
  locations the site has (Y7); the refusal page, a page and never a download (Y33); the family
  service's locations and nothing else on 5585, each naming its own path, so no ``/v1/`` door and no
  ``/health`` can be reached; the sign-in limits (H2); the invite paths out of the logs (L8); the
  forwarded headers; the size cap; the WebSocket's upgrade and time limits. Always runs.
* **A real nginx.** The same files run in a real nginx in front of two fake upstreams (the family
  service and OpenAlgo; only their ports are changed, in a copy), and what each request does is
  checked from outside. Skipped when no nginx binary is found: ``SUDARSHAN_NGINX``, then ``nginx`` on
  PATH, then ``/usr/sbin/nginx``.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sudarshan.family import contract, words, ws
from sudarshan.family.config import Settings

REPO = Path(__file__).resolve().parents[3]
NGINX_DIR = REPO / "sudarshan" / "ops" / "nginx"
ZONES = NGINX_DIR / "sudarshan-family-zones.conf"
SERVER = NGINX_DIR / "sudarshan-family.conf"
PROXY = NGINX_DIR / "sudarshan-family-proxy.conf"
#: Where each file goes on the server (the README's commands and the deploy's nginx check say the same).
INSTALLED = {
    ZONES: "/etc/nginx/conf.d/sudarshan-family-zones.conf",
    SERVER: "/etc/nginx/snippets/sudarshan-family.conf",
    PROXY: "/etc/nginx/snippets/sudarshan-family-proxy.conf",
}
FAMILY = f"127.0.0.1:{contract.PORT}"
OPENALGO = Settings.openalgo_url.removeprefix("http://")
BLOCKED_PAGE = "/sudarshan-family-blocked"
SENTENCE = "is for the account owner"


# ---- a small reader of nginx's syntax -----------------------------------------------------------
@dataclass
class Stmt:
    """One directive: its name, its arguments (quotes taken off) and, for a block, what is inside."""

    name: str
    args: list[str]
    block: list[Stmt] | None = None

    def all(self, name: str) -> list[Stmt]:
        return [s for s in self.block or [] if s.name == name]

    def one(self, name: str) -> Stmt:
        found = self.all(name)
        assert len(found) == 1, (self.name, self.args, name, len(found))
        return found[0]

    def values(self, name: str) -> list[list[str]]:
        return [s.args for s in self.all(name)]


#: nginx's escapes (ngx_conf_read_token): these lose the backslash; any other keeps it, so "\s" is \s.
ESCAPES = {'"': '"', "'": "'", "\\": "\\", "t": "\t", "r": "\r", "n": "\n"}


def tokens(text: str) -> list[str | tuple[str]]:
    """Words (a quoted word as a 1-tuple, so ``"{"`` is never a brace) and the marks ``{ } ;``."""
    out: list[str | tuple[str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            out.append(c)
            i += 1
        elif c in "\"'":
            j, buf = i + 1, []
            while text[j] != c:
                if text[j] == "\\" and text[j + 1] in ESCAPES:
                    buf.append(ESCAPES[text[j + 1]])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            out.append(("".join(buf),))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "{};":
                j += 1
            out.append(text[i:j])
            i = j
    return out


def parse(text: str) -> list[Stmt]:
    stack: list[list[Stmt]] = [[]]
    words_: list[str] = []
    for tok in tokens(text):
        if tok == ";":
            stack[-1].append(Stmt(words_[0], words_[1:]))
            words_ = []
        elif tok == "{":
            stmt = Stmt(words_[0], words_[1:], [])
            stack[-1].append(stmt)
            stack.append(stmt.block)  # type: ignore[arg-type]
            words_ = []
        elif tok == "}":
            assert not words_, f"a directive without its ; before }}: {words_}"
            stack.pop()
        else:
            words_.append(tok[0] if isinstance(tok, tuple) else tok)
    assert len(stack) == 1 and not words_, "the braces of the file do not close"
    return stack[0]


def top(path: Path) -> Stmt:
    return Stmt("file", [], parse(path.read_text(encoding="utf-8")))


def maps(path: Path = ZONES) -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """Each map by the variable it makes: (its source, its entries in order)."""
    return {s.args[1]: (s.args[0], [(e.name, e.args[0]) for e in s.block or []])
            for s in top(path).all("map")}  # fmt: skip


def map_value(entries: list[tuple[str, str]], source: str) -> str:
    """What nginx's map gives: a string entry that is the source, else the first pattern that
    matches (in order), else the default. PCRE's ``(?<name>`` is written ``(?P<name>`` here."""
    for key, value in entries:
        if key != "default" and not key.startswith("~") and key == source:
            return value
    for key, value in entries:
        if key.startswith("~") and re.search(key[1:].replace("(?<", "(?P<"), source):
            return value
    return dict(entries)["default"]


def locations(path: Path = SERVER) -> dict[tuple[str, str], Stmt]:
    """Every location of the server part by (modifier, path): "=", "^~", "" or "@"."""
    found = {}
    for s in top(path).all("location"):
        key = ("@", s.args[0]) if s.args[0].startswith("@") else (
            (s.args[0], s.args[1]) if len(s.args) == 2 else ("", s.args[0]))  # fmt: skip
        assert key not in found, key
        found[key] = s
    return found


def proxy() -> Stmt:
    return top(PROXY)


def viewer_cookie(cookie: str) -> str:
    mapping = maps()
    family = map_value(mapping["$sud_family"][1], cookie)
    owner = map_value(mapping["$sud_owner"][1], cookie)
    return map_value(mapping["$sud_viewer"][1], f"{family}:{owner}")


def blocked(path: str, viewer: str = "1") -> bool:
    return map_value(maps()["$sud_block"][1], f"{viewer}:{path}") == "1"


# ---- the text: the files and where they go ---------------------------------------------------------
def test_three_files_each_naming_where_it_is_installed():
    # The folder is shared: the automation's webhook fence ships its own two files beside these.
    family = sorted(p.name for p in NGINX_DIR.iterdir() if p.name.startswith("sudarshan-family"))
    assert family == sorted(p.name for p in INSTALLED)
    for path, target in INSTALLED.items():
        head = path.read_text(encoding="utf-8").split("\n\n", 1)[0]
        assert target in head, path.name
    assert "include snippets/sudarshan-family.conf;" in SERVER.read_text(encoding="utf-8")


def test_the_server_part_includes_the_proxy_part_by_its_installed_name():
    for key, loc in locations().items():
        for args in loc.values("include"):
            assert args == ["snippets/sudarshan-family-proxy.conf"], key


def test_only_names_of_its_own():
    """Every variable, zone and named location starts with sud_, so none meets the site's own."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in INSTALLED)
    for name in re.findall(r"zone=([A-Za-z0-9_]+)", text) + list(maps()):
        assert name.lstrip("$").startswith("sud_"), name
    for key in locations():
        if key[0] == "@":
            assert key[1].startswith("@sud_"), key


def test_no_icon_and_no_address_but_loopback():
    for path in INSTALLED:
        text = path.read_text(encoding="utf-8")
        for ch in text:
            if ord(ch) > 127:
                assert unicodedata.name(ch).startswith("DEVANAGARI") or ch == "·", (path, ch)
        for address in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            assert address == "127.0.0.1", (path.name, address)


# ---- the text: who is a family phone ----------------------------------------------------------------
def test_the_cookies_are_the_contracts_and_openalgos():
    family = maps()["$sud_family"]
    assert family == ("$http_cookie", [("default", "0"), (f"~(^|;\\s*){contract.COOKIE}=", "1")])
    owner = maps()["$sud_owner"]
    # OpenAlgo's own cookie under HTTPS: "__Secure-" + SESSION_COOKIE_NAME, whose default is session
    # (app.py; .sample.env). The owner checks his .env; the deploy's nginx check compares the two.
    assert owner == ("$http_cookie", [("default", "0"), ("~(^|;\\s*)__Secure-session=", "1")])
    app = (REPO / "app.py").read_text(encoding="utf-8")
    assert 'os.getenv("SESSION_COOKIE_NAME", "session")' in app
    assert 'f"__Secure-{session_cookie_name}"' in app
    assert maps()["$sud_viewer"] == ("$sud_family:$sud_owner", [("default", "0"), ("1:0", "1")])


@pytest.mark.parametrize(
    ("cookie", "viewer"),
    [
        ("__Host-sudarshan_family=abc", "1"),
        ("a=1; __Host-sudarshan_family=abc; b=2", "1"),
        ("a=1;__Host-sudarshan_family=abc", "1"),
        # the owner trying the family view on his own phone
        ("__Host-sudarshan_family=abc; __Secure-session=xyz", "0"),
        ("__Secure-session=xyz", "0"),
        ("", "0"),
        ("__Host-sudarshan_family_csrf=abc", "0"),  # signed out: a stranger
        ("x__Host-sudarshan_family=abc", "0"),
        ("__host-sudarshan_family=abc", "0"),  # cookie names are exact
        ("__Host-sudarshan_family_csrf=abc; __Host-sudarshan_family=abc", "1"),
    ],
)
def test_a_family_phone_is_the_family_cookie_without_openalgos(cookie, viewer):
    assert viewer_cookie(cookie) == viewer


# ---- the text: the guard ------------------------------------------------------------------------------
#: The allow-list word for word: widening any line (an /api/ entry, an anchor dropped) turns this red.
ALLOWED = [
    ("default", "0"),
    ("~^1:/family(/|$)", "0"),
    ("~^1:/assets/", "0"),
    ("~^1:/images/", "0"),
    ("~^1:/(favicon\\.ico|apple-touch-icon\\.png|sw\\.js|offline\\.html)$", "0"),
    ("~^1:", "1"),
]
OWNER_PATHS = [
    "/", "/dashboard", "/login", "/setup", "/about", "/orderbook", "/positions", "/apikey",
    "/api/v1/placeorder", "/api/v1/", "/api/websocket/apikey", "/auth/login", "/auth/logout",
    "/auth/csrf-token", "/auth/session-status", "/sudarshan/portfolio/api/state",
    "/sudarshan/family/api/member_list", "/sudarshan/family/internal/watchlists", "/socket.io/",
    "/ws", "/ws/", "/strategy", "/python", "/flow", "/mcp", "/agent", "/admin", "/sandbox",
    "/telegram", "/scalping", "/trading", "/family-members", "/familyx", "/assets", "/images",
    "/v1/admin/state", "/health", "/logo.png", "/sounds/alert.mp3", "/docs/x", BLOCKED_PAGE,
]  # fmt: skip
FAMILY_PATHS = [
    "/family", "/family/", "/family/positions", "/family/join/abc", "/family/api/me",
    "/family/api/login", "/family/ws", "/assets/index-abc.js", "/images/sudarshan-family.webmanifest",
    "/favicon.ico", "/apple-touch-icon.png", "/sw.js", "/offline.html",
]  # fmt: skip


def test_the_allow_list_word_for_word():
    source, entries = maps()["$sud_block"]
    assert source == "$sud_viewer:$uri"
    assert entries == ALLOWED


@pytest.mark.parametrize("path", OWNER_PATHS)
def test_a_family_phone_is_refused_everything_of_openalgo(path):
    assert blocked(path)
    assert not blocked(path, viewer="0")  # anyone else meets OpenAlgo as before


@pytest.mark.parametrize("path", FAMILY_PATHS)
def test_a_family_phone_reaches_the_family_view_and_its_files(path):
    assert not blocked(path)


def test_the_guard_is_one_line_at_server_level():
    """At server level the guard runs before any location is chosen, so it covers /socket.io/, /ws
    and every other location of the site without a line in each (Y7)."""
    server = top(SERVER)
    ifs = server.all("if")
    assert len(ifs) == 1 and ifs[0].args == ["($sud_block)"]
    assert [(s.name, s.args) for s in ifs[0].block or []] == [
        ("rewrite", ["^", BLOCKED_PAGE + "?", "last"])
    ]
    for key, loc in locations().items():
        assert not loc.all("if"), key  # an if inside a location is the unsafe kind
    assert SERVER.read_text(encoding="utf-8").count("$sud_block") == 1
    assert "sud_block" not in PROXY.read_text(encoding="utf-8")


def refusal_page() -> tuple[Stmt, str]:
    loc = locations()[("=", BLOCKED_PAGE)]
    (code, body) = loc.one("return").args
    assert code == "403"
    return loc, body


def test_the_refusal_page_is_a_page_and_never_kept():
    loc, body = refusal_page()
    assert loc.all("internal")
    assert loc.one("default_type").args == ["text/html; charset=utf-8"]
    headers = {args[0]: args[1:] for args in loc.values("add_header")}
    assert headers["Cache-Control"] == ["no-store", "always"]
    assert headers["X-Content-Type-Options"] == ["nosniff", "always"]
    assert headers["X-Frame-Options"] == ["DENY", "always"]
    assert headers["Referrer-Policy"] == ["no-referrer", "always"]
    csp = headers["Content-Security-Policy"]
    assert csp[1] == "always" and "default-src 'none'" in csp[0] and "script-src" not in csp[0]
    assert "frame-ancestors 'none'" in csp[0]


def test_the_refusal_page_says_where_the_family_view_is_in_the_light_look():
    _, body = refusal_page()
    assert body.startswith("<!doctype html>") and '<html lang="en">' in body
    assert "<title>सुदर्शन · SUDARSHAN</title>" in body
    assert f"This part of सुदर्शन / SUDARSHAN {SENTENCE}" in body
    assert '<a class="go" href="/family/">Open the family view</a>' in body
    # the owner who tried the family view on his own phone is told the way back to his login
    assert "Sign out" in body
    assert "only light" in body and "#F4F6F8" in body and "#0E5A73" in body
    assert "min-height:48px" in body
    for banned in ("<script", "http:", "https:", "@import", "url(", "$", "dark"):
        assert banned not in body, banned


# ---- the text: the family service --------------------------------------------------------------------
FAMILY_LOCATIONS = {
    ("=", "/family/api/login"),
    ("=", "/family/api/join"),
    ("^~", "/family/api/join/"),
    ("^~", "/family/api/"),
    ("=", contract.WS_PATH),
}


def upstream_of(loc: Stmt) -> str | None:
    found = loc.values("proxy_pass")
    return found[0][0] if found else None


def test_only_the_family_api_and_the_websocket_go_to_the_family_service():
    assert contract.VIEWER_PREFIX == "/family/api" and contract.WS_PATH == "/family/ws"
    to_family = {k for k, loc in locations().items() if FAMILY in (upstream_of(loc) or "")}
    assert to_family == FAMILY_LOCATIONS


def test_each_location_names_its_own_path_so_nginx_sends_the_cleaned_one():
    """With a path after the address nginx sends the path it matched, cleaned of ../ and %2e; without
    one it would send the path as the phone wrote it, and /v1/admin/../../family/api/login would reach
    the service as written."""
    for key, loc in locations().items():
        target = upstream_of(loc)
        if target is None:
            continue
        assert key[0] in ("=", "^~"), key
        assert target.startswith("http://127.0.0.1:"), key
        assert target.split("/", 3)[3:] == [key[1].lstrip("/")], (key, target)


def test_no_sealed_door_and_no_health_is_reachable():
    for path in INSTALLED:
        text = path.read_text(encoding="utf-8")
        assert "/v1" not in re.sub(r"#.*", "", text), path.name
    for key in locations():
        assert not key[1].startswith(("/v1", contract.HEALTH_PATH, "/health")), key


def test_signing_in_and_joining_are_held_per_address_and_in_all():
    zones = {s.args[1].removeprefix("zone=").split(":")[0]: (s.args[0], s.args[2])
             for s in top(ZONES).all("limit_req_zone")}  # fmt: skip
    assert zones == {
        "sud_family_signin": ("$sud_family_client", "rate=5r/m"),
        "sud_family_signin_all": ("sudarshan-family", "rate=12r/m"),
        "sud_family_api": ("$sud_family_client", "rate=20r/s"),
    }
    loc = locations()
    for path in ("/family/api/login", "/family/api/join"):
        assert loc[("=", path)].values("limit_req") == [
            ["zone=sud_family_signin", "burst=3", "nodelay"],
            ["zone=sud_family_signin_all", "burst=5", "nodelay"],
        ], path
    assert loc[("^~", "/family/api/join/")].values("limit_req") == [
        ["zone=sud_family_signin", "burst=3", "nodelay"]
    ]
    for key in (("^~", "/family/api/"), ("=", contract.WS_PATH)):
        assert loc[key].values("limit_req") == [["zone=sud_family_api", "burst=40", "nodelay"]]
    assert proxy().values("limit_req_status") == [["429"]]
    assert loc[("=", contract.WS_PATH)].values("limit_req_status") == [["429"]]


@pytest.mark.parametrize(
    ("address", "counted_as"),
    [
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2"),
        ("2001:db8:1:2::9", "2001:db8:1:2"),
        ("2001:db8::1", "2001:db8"),  # zeros early: counted more coarsely, never more finely
        ("fe80::1", "fe80"),
        ("203.0.113.7", "$binary_remote_addr"),
    ],
)
def test_ipv6_is_counted_by_its_first_64_bits(address, counted_as):
    entries = maps()["$sud_family_client"][1]
    got = map_value(entries, address)
    if got.startswith("$sud_net6"):
        pattern = next(k for k, v in entries if v == got)[1:].replace("(?<", "(?P<")
        got = re.search(pattern, address).group(got.removeprefix("$"))  # type: ignore[union-attr]
    assert got == counted_as


def test_forwarded_headers_are_nginxs_own():
    """X-Real-IP is the device's place in the family service; all three mark a request nginx sent,
    which the service refuses on its sealed doors and /health. Never what the phone wrote."""
    want = [
        ["Host", "$http_host"],
        ["X-Real-IP", "$remote_addr"],
        ["X-Forwarded-For", "$remote_addr"],
        ["X-Forwarded-Proto", "$scheme"],
    ]
    # L7 (feat/family-c): the doors refuse all five headers nginx can set; these are the three
    # this configuration sets, each of them refused.
    assert {h.lower() for h, *_ in want[1:]} <= {h.lower() for h in contract.FORWARDED_HEADERS}
    assert proxy().values("proxy_set_header") == want
    wsloc = locations()[("=", contract.WS_PATH)]
    assert wsloc.values("proxy_set_header") == [
        *want, ["Upgrade", "$http_upgrade"], ["Connection", "$sud_family_connection"],
    ]  # fmt: skip
    assert maps()["$sud_family_connection"] == (
        "$http_upgrade",
        [("default", "upgrade"), ("", "close")],
    )
    for key, loc in locations().items():
        if FAMILY in (upstream_of(loc) or "") and key != ("=", contract.WS_PATH):
            assert loc.values("include") == [["snippets/sudarshan-family-proxy.conf"]], key
            assert not loc.all("proxy_set_header"), key  # one set, in the shared file


def test_the_body_cap_and_the_time_limits():
    assert proxy().values("client_max_body_size") == [
        [f"{contract.LIMITS['viewer_bytes'] // 1024}k"]
    ]
    assert proxy().values("proxy_read_timeout") == [["30s"]]
    wsloc = locations()[("=", contract.WS_PATH)]
    for name in ("proxy_read_timeout", "proxy_send_timeout"):
        (value,) = wsloc.one(name).args
        # the service pings every PING_EVERY_S: a socket quiet for three of them is a dead one
        assert int(value.removesuffix("s")) >= 3 * ws.PING_EVERY_S, name
    assert wsloc.values("proxy_http_version") == [["1.1"]]
    assert wsloc.values("proxy_buffering") == [["off"]]


def test_nginxs_own_refusals_are_the_family_envelope_in_plain_words():
    pages = {
        "413": ("@sud_family_too_large", 413, "too_large"),
        "429": ("@sud_family_too_many", 429, "busy"),
    }
    error_pages = {tuple(a[:-1]): a[-1] for a in proxy().values("error_page")}
    assert error_pages == {
        ("413",): "@sud_family_too_large",
        ("429",): "@sud_family_too_many",
        ("502", "504", "=503"): "@sud_family_unavailable",
    }
    pages["503"] = ("@sud_family_unavailable", 503, "unavailable")
    banned = re.compile(r"\b(HTTP|[45]\d\d|nginx|gateway|upstream|error|limit_req)\b", re.I)
    for name, status, code in pages.values():
        loc = locations()[("@", name)]
        assert loc.values("default_type") == [["application/json; charset=utf-8"]], name
        assert loc.values("access_log") == [["off"]], name  # one may be for an invite's path (L8)
        (got_status, body) = loc.one("return").args
        envelope = json.loads(body)
        assert int(got_status) == status and envelope["status"] == "error", name
        assert envelope["code"] == code and code in contract.ERROR_CODES, name
        message = envelope["message"]
        assert message.endswith(".") and not banned.search(message), name
    too_large = json.loads(locations()[("@", "@sud_family_too_large")].one("return").args[1])
    assert too_large["message"] == words.WORDS["too_large"]


def test_the_invite_paths_stay_out_of_the_logs():
    """Review L8: the invite code is in /family/join/<code> and /family/api/join/<code>."""
    loc = locations()
    info = loc[("^~", "/family/api/join/")]
    assert info.values("access_log") == [["off"]]
    assert info.values("limit_req_log_level") == [["warn"]]  # under the error log's own level
    page = loc[("^~", "/family/join/")]
    assert page.values("access_log") == [["off"]]
    # OpenAlgo answers every page with strict-origin-when-cross-origin, which would put the code in
    # the Referer of every request the page makes afterwards, and the access log keeps Referers.
    assert page.values("proxy_hide_header") == [["Referrer-Policy"]]
    assert page.values("add_header") == [["Referrer-Policy", "no-referrer", "always"]]


def test_the_invite_page_goes_to_openalgo_as_every_page_does():
    page = locations()[("^~", "/family/join/")]
    assert upstream_of(page) == f"http://{OPENALGO}/family/join/"
    headers = {a[0]: a[1] for a in page.values("proxy_set_header")}
    assert headers["X-Real-IP"] == "$remote_addr" and headers["X-Forwarded-For"] == "$remote_addr"
    # read before X-Real-IP by OpenAlgo with TRUST_PROXY_HEADERS on (utils/ip_helper.py)
    helper = (REPO / "utils" / "ip_helper.py").read_text(encoding="utf-8")
    for name in ("CF-Connecting-IP", "True-Client-IP", "X-Client-IP"):
        assert f'"{name}"' in helper and headers[name] == "", name


# ---- a real nginx --------------------------------------------------------------------------------------
def nginx_binary() -> str | None:
    for found in (os.environ.get("SUDARSHAN_NGINX"), shutil.which("nginx"), "/usr/sbin/nginx"):
        if found and os.path.isfile(found) and os.access(found, os.X_OK):
            return found
    return None


NGINX = nginx_binary()
needs_nginx = pytest.mark.skipif(NGINX is None, reason="no nginx binary (set SUDARSHAN_NGINX)")
PHONE = {"Cookie": f"{contract.COOKIE}=nginx-test"}
CODE = "Invite-Code_0123456789abcdefghij"


@dataclass
class Seen:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes


class Upstream:
    """A fake service on a free port that keeps every request it is sent and answers it."""

    def __init__(self, headers: dict[str, str] | None = None):
        self.seen: list[Seen] = []
        seen, extra = self.seen, headers or {}

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                seen.append(
                    Seen(
                        self.command,
                        self.path,
                        {k.lower(): v for k, v in self.headers.items()},
                        body,
                    )
                )
                data = json.dumps({"status": "success", "data": {"target": self.path}}).encode()
                self.send_response(200)
                for name, value in {"Content-Type": "application/json", **extra}.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = answer

            def log_message(self, *args):  # quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()
        self.stopped = False

    def stop(self) -> None:
        if not self.stopped:
            self.stopped = True
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(5)

    def targets(self) -> list[str]:
        return [s.target for s in self.seen]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Edge:
    """A running nginx with the three files installed as on the server, in front of the fakes."""

    root: Path
    port: int
    family: Upstream
    openalgo: Upstream
    process: subprocess.Popen | None = None
    realip: bool = False
    stderr: list[bytes] = field(default_factory=list)

    def ask(self, method: str, target: str, headers: dict[str, str] | None = None,
            body: bytes | None = None, client: str | None = None):  # fmt: skip
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", "family.test")
        if client is not None:
            conn.putheader("X-Test-Client", client)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, response, data

    def log(self, name: str) -> str:
        path = self.root / name
        return path.read_text(errors="replace") if path.exists() else ""

    def logged(self, name: str, text: str, wait_s: float = 5.0) -> str:
        """The log once it holds ``text``: nginx writes a request's line after its answer has gone,
        so a test that has the answer can be ahead of the line."""
        deadline = time.monotonic() + wait_s
        while text not in (got := self.log(name)):
            assert time.monotonic() < deadline, f"{text!r} never reached {name}"
            time.sleep(0.02)
        return got


SITE = """
pid {root}/nginx.pid;
daemon off;
master_process off;
error_log {root}/error.log error;
events {{ worker_connections 256; }}
http {{
    default_type application/octet-stream;
    access_log {root}/access.log combined;
    client_body_temp_path {root}/tmp/body;
    proxy_temp_path {root}/tmp/proxy;
    fastcgi_temp_path {root}/tmp/fastcgi;
    uwsgi_temp_path {root}/tmp/uwsgi;
    scgi_temp_path {root}/tmp/scgi;
    {realip}
    include conf.d/*.conf;
    server {{
        listen 127.0.0.1:{port};
        server_name family.test;
        set $openalgo_loggable 1;
        include snippets/sudarshan-family.conf;
        location = /__sud_client {{ return 200 $sud_family_client; }}
        location /socket.io/ {{ proxy_pass http://127.0.0.1:{openalgo}; }}
        location = /ws {{ proxy_pass http://127.0.0.1:{openalgo}; }}
        location /ws/ {{ proxy_pass http://127.0.0.1:{openalgo}; }}
        location / {{ proxy_pass http://127.0.0.1:{openalgo}; {root_lines} }}
    }}
}}
"""


def install(root: Path, family: int, openalgo: int, port: int, realip: bool,
            root_lines: str = "") -> Path:  # fmt: skip
    """The three files where the server keeps them (conf.d/, snippets/), the two loopback ports
    pointed at the fakes, in a site like the server's: its own locations for /socket.io/ and /ws,
    and ``root_lines`` inside its location /."""
    for name in ("conf.d", "snippets", "tmp", "logs"):
        (root / name).mkdir()
    shutil.copy(ZONES, root / "conf.d" / ZONES.name)
    shutil.copy(PROXY, root / "snippets" / PROXY.name)
    server = SERVER.read_text(encoding="utf-8")
    server = server.replace(FAMILY, f"127.0.0.1:{family}").replace(
        OPENALGO, f"127.0.0.1:{openalgo}"
    )
    (root / "snippets" / SERVER.name).write_text(server, encoding="utf-8")
    conf = root / "nginx.conf"
    real = "set_real_ip_from 127.0.0.0/8; real_ip_header X-Test-Client;" if realip else ""
    conf.write_text(
        SITE.format(root=root, port=port, openalgo=openalgo, realip=real, root_lines=root_lines)
    )
    return conf


def has_realip() -> bool:
    done = subprocess.run([NGINX, "-V"], capture_output=True, text=True, timeout=10)  # type: ignore[list-item]
    return "http_realip_module" in done.stderr + done.stdout


@pytest.fixture
def edge(tmp_path, request):
    """A running nginx; a test may put lines inside the site's location / (``indirect``)."""
    family, openalgo = Upstream(), Upstream({"Referrer-Policy": "strict-origin-when-cross-origin"})
    port, realip = free_port(), has_realip()
    conf = install(
        tmp_path, family.port, openalgo.port, port, realip, getattr(request, "param", "")
    )
    process = subprocess.Popen([NGINX, "-p", str(tmp_path), "-c", str(conf)],  # type: ignore[list-item]
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # fmt: skip
    got = Edge(tmp_path, port, family, openalgo, process, realip)
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                pytest.fail(
                    f"nginx did not start: {process.stderr.read().decode(errors='replace')}"
                )  # type: ignore[union-attr]
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
                break
            assert time.monotonic() < deadline, "nginx did not listen in 10 s"
            time.sleep(0.05)
        yield got
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        if process.stderr is not None:
            process.stderr.close()
        family.stop()
        openalgo.stop()


def is_refusal(status: int, response, data: bytes) -> bool:
    return (status == 403 and response.getheader("Content-Type") == "text/html; charset=utf-8"
            and SENTENCE.encode() in data and response.getheader("Cache-Control") == "no-store")  # fmt: skip


@needs_nginx
def test_nginx_takes_the_files_as_the_server_would(tmp_path):
    conf = install(tmp_path, free_port(), free_port(), free_port(), realip=False)
    done = subprocess.run([NGINX, "-p", str(tmp_path), "-c", str(conf), "-t"],  # type: ignore[list-item]
                          capture_output=True, text=True, timeout=30)  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "test is successful" in done.stderr


@needs_nginx
@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("GET", "/"), ("GET", "/dashboard"), ("POST", "/api/v1/placeorder"), ("GET", "/auth/csrf-token"),
        ("POST", "/auth/login"), ("GET", "/sudarshan/portfolio/api/state"),
        ("GET", "/socket.io/?EIO=4&transport=polling"), ("GET", "/ws"), ("GET", "/ws/"),
        ("GET", "/family-members"), ("GET", "/family/../apikey"), ("GET", "/family/%2e%2e/apikey"),
        ("GET", "/assets/../api/v1/funds"), ("GET", BLOCKED_PAGE),
    ],
)  # fmt: skip
def test_a_family_phone_is_refused_with_the_page(edge, method, target):
    status, response, data = edge.ask(method, target, PHONE, b"{}" if method == "POST" else None)
    assert is_refusal(status, response, data), (target, status, data[:200])
    assert edge.openalgo.seen == [] and edge.family.seen == []


@needs_nginx
def test_the_family_phone_reaches_the_family_view_and_its_files(edge):
    for target in ("/family/", "/family/positions", "/assets/index-abc.js", "/images/a.png",
                   "/favicon.ico", "/apple-touch-icon.png", "/sw.js", "/offline.html"):  # fmt: skip
        status, _, _ = edge.ask("GET", target, PHONE)
        assert status == 200, target
    assert edge.openalgo.targets() == ["/family/", "/family/positions", "/assets/index-abc.js",
                                       "/images/a.png", "/favicon.ico", "/apple-touch-icon.png",
                                       "/sw.js", "/offline.html"]  # fmt: skip


@needs_nginx
@pytest.mark.parametrize(
    "cookie",
    [
        "",
        "__Secure-session=x",
        f"{contract.COOKIE}=x; __Secure-session=y",
        f"{contract.CSRF_COOKIE}=x",
    ],
)
def test_the_owner_and_strangers_meet_openalgo_as_before(edge, cookie):
    headers = {"Cookie": cookie} if cookie else {}
    for target in ("/api/v1/placeorder", "/socket.io/?EIO=4", "/ws", "/dashboard"):
        status, _, _ = edge.ask("POST" if "place" in target else "GET", target, headers,
                                b"{}" if "place" in target else None)  # fmt: skip
        assert status == 200, (cookie, target)
    assert len(edge.openalgo.seen) == 4 and edge.family.seen == []


@needs_nginx
@pytest.mark.parametrize(
    ("target", "arrives"),
    [
        ("/family/api/me", "/family/api/me"),
        ("/family/api/me?x=1", "/family/api/me?x=1"),
        ("//family//api//me", "/family/api/me"),
        ("/v1/admin/../../family/api/me", "/family/api/me"),
        ("/v1/admin/%2e%2e/%2e%2e/family/api/me", "/family/api/me"),
        ("/family/api/books/../me", "/family/api/me"),
        ("/family/api/books/positions", "/family/api/books/positions"),
    ],
)
def test_the_family_service_only_ever_sees_the_cleaned_family_path(edge, target, arrives):
    status, _, _ = edge.ask("GET", target, PHONE)
    assert status == 200
    assert edge.family.targets() == [arrives] and edge.openalgo.seen == []


@needs_nginx
@pytest.mark.parametrize(
    "target",
    ["/v1/admin/state", "/v1/nudge", "/health", "/family/api/../../v1/admin/state",
     "/family/api/%2e%2e/%2e%2e/v1/admin/state", "/family/../health"],
)  # fmt: skip
def test_no_sealed_door_and_no_health_reaches_the_family_service(edge, target):
    for headers in ({}, PHONE):
        edge.ask(
            "POST" if "v1" in target else "GET", target, headers, b"{}" if "v1" in target else None
        )
    assert edge.family.seen == []


@needs_nginx
def test_the_forwarded_headers_reaching_the_service_are_nginxs(edge):
    lies = {"X-Real-IP": "198.51.100.9", "X-Forwarded-For": "198.51.100.9",
            "X-Forwarded-Proto": "gopher", **PHONE}  # fmt: skip
    edge.ask("POST", "/family/api/logout", lies, b"{}")
    (seen,) = edge.family.seen
    assert seen.headers["x-real-ip"] == "127.0.0.1"
    assert seen.headers["x-forwarded-for"] == "127.0.0.1"
    assert seen.headers["x-forwarded-proto"] == "http"
    assert seen.headers["host"] == "family.test"
    assert seen.headers["cookie"] == PHONE["Cookie"] and seen.body == b"{}"
    assert seen.headers.get("connection") == "close" and "upgrade" not in seen.headers


@needs_nginx
def test_the_websocket_is_handed_on_with_its_upgrade(edge):
    edge.ask("GET", "/family/ws", {**PHONE, "Upgrade": "websocket", "Connection": "Upgrade",
                                   "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                                   "Sec-WebSocket-Version": "13"})  # fmt: skip
    edge.ask("GET", "/family/ws", PHONE)
    first, second = edge.family.seen
    assert (first.target, first.headers["upgrade"], first.headers["connection"]) == (
        "/family/ws", "websocket", "upgrade")  # fmt: skip
    assert first.headers["x-real-ip"] == "127.0.0.1"
    assert "upgrade" not in second.headers and second.headers["connection"] == "close"


def refused_in_words(status: int, response, data: bytes, want: int, code: str) -> bool:
    envelope = json.loads(data)
    return (status == want and response.getheader("Content-Type") == "application/json; charset=utf-8"
            and envelope["status"] == "error" and envelope["code"] == code)  # fmt: skip


@needs_nginx
@pytest.mark.parametrize("path", ["/family/api/login", "/family/api/join"])
def test_signing_in_is_held_to_a_few_tries_per_address(edge, path):
    answers = [edge.ask("POST", path, PHONE, b"{}") for _ in range(6)]
    assert [a[0] for a in answers[:4]] == [200] * 4  # the first and a burst of 3
    for status, response, data in answers[4:]:
        assert refused_in_words(status, response, data, 429, "busy")
        assert json.loads(data)["message"].startswith("Too many tries")
    assert len(edge.family.seen) == 4  # the service did no work for the rest
    assert f'request: "POST {path} HTTP/1.1"' in edge.logged("error.log", "limiting requests")
    time.sleep(2.5)  # one try comes back every 12 seconds, not sooner
    assert edge.ask("POST", path, PHONE, b"{}")[0] == 429
    assert len(edge.family.seen) == 4


@needs_nginx
@pytest.mark.parametrize("path", ["/family/api/login", "/family/api/join"])
def test_signing_in_is_held_for_everyone_together(edge, path):
    if not edge.realip:
        pytest.skip("this nginx has no realip module to speak for other addresses")
    answers = [edge.ask("POST", path, PHONE, b"{}", client=f"192.0.2.{i}") for i in range(1, 9)]
    assert [a[0] for a in answers] == [200] * 6 + [429] * 2
    assert len(edge.family.seen) == 6


@needs_nginx
def test_ipv6_addresses_of_one_64_share_one_count(edge):
    if not edge.realip:
        pytest.skip("this nginx has no realip module to speak for other addresses")
    assert edge.ask("GET", "/__sud_client", client="2001:db8:1:2::a")[2] == b"2001:db8:1:2"
    assert edge.ask("GET", "/__sud_client", client="2001:db8::5")[2] == b"2001:db8"
    statuses = [edge.ask("POST", "/family/api/login", PHONE, b"{}", client=f"2001:db8:1:2::{i}")[0]
                for i in range(1, 6)]  # fmt: skip
    assert statuses == [200] * 4 + [429]
    assert edge.ask("POST", "/family/api/login", PHONE, b"{}", client="2001:db8:1:3::1")[0] == 200


@needs_nginx
def test_a_body_past_the_cap_is_refused_in_words_before_it_is_read(edge):
    with socket.create_connection(("127.0.0.1", edge.port), 5) as conn:
        conn.sendall(b"POST /family/api/password HTTP/1.1\r\nHost: family.test\r\n"
                     b"Cookie: " + PHONE["Cookie"].encode() + b"\r\nContent-Type: application/json\r\n"
                     b"Content-Length: %d\r\n\r\n" % (contract.LIMITS["viewer_bytes"] + 1))  # fmt: skip
        got = b""
        while (
            b"\r\n\r\n" not in got
        ):  # the head, then the body by its length (never wait for a close)
            chunk = conn.recv(65536)
            assert chunk, got
            got += chunk
        head, _, body = got.partition(b"\r\n\r\n")
        length = int(re.search(rb"(?i)\r\ncontent-length: *(\d+)", head).group(1))  # type: ignore[union-attr]
        while len(body) < length:
            chunk = conn.recv(65536)
            assert chunk, body
            body += chunk
    assert head.startswith(b"HTTP/1.1 413 "), head[:100]
    assert b"application/json" in head
    assert json.loads(body)["code"] == "too_large"
    assert edge.family.seen == []


@needs_nginx
def test_a_family_service_that_does_not_answer_is_said_in_words(edge):
    edge.family.stop()
    status, response, data = edge.ask("GET", "/family/api/me", PHONE)
    assert refused_in_words(status, response, data, 503, "unavailable")
    assert (
        json.loads(data)["message"]
        == "The family view did not answer. Wait a minute, then try again."
    )


@needs_nginx
def test_the_invite_code_reaches_no_log_and_no_referer(edge):
    status, response, _ = edge.ask("GET", f"/family/join/{CODE}",
                                   {"CF-Connecting-IP": "198.51.100.1", "True-Client-IP": "198.51.100.2",
                                    "X-Client-IP": "198.51.100.3", "X-Real-IP": "198.51.100.4"})  # fmt: skip
    assert status == 200 and response.getheader("Referrer-Policy") == "no-referrer"
    (page,) = edge.openalgo.seen
    assert page.target == f"/family/join/{CODE}" and page.headers["x-real-ip"] == "127.0.0.1"
    for name in ("cf-connecting-ip", "true-client-ip", "x-client-ip"):
        assert name not in page.headers, name
    for _ in range(6):  # past the limit too: the refusal is quiet as well
        edge.ask("GET", f"/family/api/join/{CODE}", PHONE)
    assert edge.family.targets() == [f"/family/api/join/{CODE}"] * 4
    edge.ask("GET", "/family/positions", PHONE)  # the log itself works, and is written in order
    assert CODE not in edge.logged("access.log", "/family/positions")
    assert CODE not in edge.log("error.log")


def readme_lines() -> str:
    """The two lines the README's install section offers for the site's location / (step 5)."""
    readme = (REPO / "sudarshan" / "family" / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Installing the nginx lines (O3)\n", 1)[1]
    found = re.search(r'\n +(proxy_set_header CF-Connecting-IP "";)\n +(proxy_set_header True-Client-IP "";)\n',
                      section)  # fmt: skip
    assert found, "the README's two lines for location / are not where the install section has them"
    return f"{found.group(1)} {found.group(2)}"


@needs_nginx
@pytest.mark.parametrize(
    ("edge", "passed_on"), [("", True), (readme_lines(), False)], indirect=["edge"]
)
def test_the_readmes_two_lines_keep_a_callers_address_from_openalgo(edge, passed_on):
    """OpenAlgo reads CF-Connecting-IP and True-Client-IP before the address nginx sets, when its .env
    trusts forwarded addresses; a site's location / passes on whatever a caller writes in them unless it
    sets them empty, as the README's optional step does (and the deploy's check warns about)."""
    fake = {"CF-Connecting-IP": "198.51.100.7", "True-Client-IP": "198.51.100.8"}
    status, _, _ = edge.ask("GET", "/dashboard", fake)
    assert status == 200
    (seen,) = edge.openalgo.seen
    got = {name: seen.headers.get(name.lower()) for name in fake}
    assert got == (fake if passed_on else dict.fromkeys(fake))
