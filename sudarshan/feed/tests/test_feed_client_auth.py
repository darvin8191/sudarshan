"""Per-client tokens on the feed's write API (FL1): the feed's refusals, the open mode while no
token exists, a token file changed while running, and what each ring client sends."""

from __future__ import annotations

import asyncio
import logging
import secrets
from types import SimpleNamespace

import httpx
import pytest

from sudarshan.alerts.market import runtime as alerts_runtime
from sudarshan.alerts.market.feedlink import FeedLink as AlertsLink
from sudarshan.feed import client_auth
from sudarshan.feed.client_auth import ClientTokens, auth_headers
from sudarshan.feed.http_api import REFUSED_KEY, start_http
from sudarshan.portfolio import feedlink as portfolio_link
from sudarshan.screener import runtime as screener_runtime

CLIENTS = (
    "sudarshan-alerts", "sudarshan-screener", "sudarshan-portfolio",
    "sudarshan-journal", "sudarshan-family", "sudarshan-protect",
)  # fmt: skip
REL = "NSE_EQ|INE002A01018"


def token() -> str:
    return secrets.token_urlsafe(32)  # 43 characters, a made-up one every time


def write_tokens(folder, names=CLIENTS) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    made = {name: token() for name in names}
    for name, value in made.items():
        (folder / name).write_text(value + "\n")
    return made


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


class Stub:
    """Just enough of a FeedService for the HTTP layer: every write answers ok."""

    def __init__(self, tokens: ClientTokens):
        self.client_tokens = tokens
        self.rest = SimpleNamespace(serve=self._serve)
        self.reached: list[str] = []

    async def _serve(self, method, url, headers, respond):
        self.reached.append(f"{method} {url.path}")
        await respond(200, {"governed": True}, {})

    async def subscribe(self, mode, keys):
        self.reached.append("subscribe")
        return {"ok": True}

    async def unsubscribe(self, keys):
        self.reached.append("unsubscribe")
        return {"ok": True}

    async def put_claims(self, client, keys):
        self.reached.append(f"put {client}")
        return {"ok": True}

    async def drop_claims(self, client):
        self.reached.append(f"drop {client}")
        return {"ok": True}

    def health(self):
        return {"state": "ok"}

    def snapshot(self, keys):
        return {}

    def claims_view(self):
        return {"clients": {}}


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def serving(stub: Stub):
    port = free_port()
    server = await start_http(stub, port)
    return server, httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False)


# (method, path, body, who it names in X-Sudarshan-Client: None when the path names it)
WRITES = [
    ("PUT", "/claims/sudarshan-alerts", {"keys": {REL: "ltpc"}}, None),
    ("DELETE", "/claims/sudarshan-alerts", None, None),
    ("POST", "/subscribe", {"mode": "ltpc", "keys": [REL]}, "sudarshan-alerts"),
    ("POST", "/unsubscribe", {"keys": [REL]}, "sudarshan-alerts"),
    ("GET", "/upstox/v3/market-quote/quotes?instrument_key=x", None, "sudarshan-alerts"),
]
IDS = ["put-claims", "delete-claims", "subscribe", "unsubscribe", "governor"]


def send(http, write, who: str, auth: dict[str, str]):
    method, path, body, names = write
    if names is None:  # the path names the client
        path = "/claims/" + who
    headers = dict(auth) | ({"X-Sudarshan-Client": who} if names else {})
    return http.request(method, path, json=body, headers=headers)


def clock_of(now: list[float]):
    return lambda: now[0]


@pytest.mark.parametrize("write", WRITES, ids=IDS)
def test_the_right_token_passes_and_a_wrong_missing_or_foreign_one_is_refused(
    write, tmp_path, caplog
):
    made = write_tokens(tmp_path / "keys")
    stub = Stub(ClientTokens(tmp_path / "keys"))
    mine, other = "sudarshan-alerts", "sudarshan-screener"

    async def scenario():
        server, http = await serving(stub)
        async with server, http:
            assert (await send(http, write, mine, bearer(made[mine]))).status_code == 200
            assert len(stub.reached) == 1
            wrong = bearer(token())
            for auth in (wrong, {}, bearer(made[other]), {"Authorization": made[mine]}):
                answer = await send(http, write, mine, auth)
                assert answer.status_code == 401
                assert answer.json() == {"error": REFUSED_KEY}
            # one client's token never speaks for another client
            assert (await send(http, write, other, bearer(made[mine]))).status_code == 401
            assert len(stub.reached) == 1  # nothing else got through
            # a client with no token file of its own is refused while others have one
            assert (
                await send(http, write, "sudarshan-nobody", bearer(made[mine]))
            ).status_code == 401

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())
    text = caplog.text
    assert "feed write refused for sudarshan-alerts" in text
    assert not any(value in text for value in made.values())  # the log never holds a token
    assert "Bearer" not in text


def test_the_open_routes_need_no_token_and_the_sentence_is_for_the_owner(tmp_path):
    write_tokens(tmp_path / "keys")
    stub = Stub(ClientTokens(tmp_path / "keys"))

    async def scenario():
        server, http = await serving(stub)
        async with server, http:
            for path in ("/health", "/snapshot?keys=a", "/snapshot/all", "/claims"):
                assert (await http.get(path)).status_code in (200, 400), path
            assert (await http.get("/health")).status_code == 200
            assert (await http.get("/claims")).status_code == 200
            assert (await http.get("/subscribe")).status_code == 405  # no token asked of a GET

    asyncio.run(scenario())
    for word in ("401", "Bearer", "token", "header", "HTTP"):
        assert word not in REFUSED_KEY


def test_without_any_token_writes_stay_open_and_the_feed_says_so_once(tmp_path, caplog):
    stub = Stub(ClientTokens(tmp_path / "no-such-folder"))

    async def scenario():
        server, http = await serving(stub)
        async with server, http:
            for write in WRITES:
                assert (await send(http, write, "sudarshan-alerts", {})).status_code == 200
            for write in WRITES:
                assert (
                    await send(http, write, "sudarshan-alerts", bearer(token()))
                ).status_code == 200

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "writes to the price feed are open" in warnings[0].getMessage()
    assert len(stub.reached) == 2 * len(WRITES)


def test_an_empty_folder_is_the_same_open_mode(tmp_path):
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "sudarshan-alerts").write_text("not a key\n")  # unusable: no token exists
    tokens = ClientTokens(tmp_path / "keys")
    assert tokens.allows("sudarshan-alerts", None) is True


def test_a_token_file_changed_while_running_is_picked_up(tmp_path, caplog):
    folder, now = tmp_path / "keys", [1000.0]
    tokens = ClientTokens(folder, clock=clock_of(now))
    with caplog.at_level(logging.INFO):
        tokens.start()
    assert tokens.allows("sudarshan-alerts", None)  # none yet: open
    made = write_tokens(folder, ("sudarshan-alerts",))
    assert tokens.allows("sudarshan-alerts", None)  # the folder is looked at once a second
    now[0] += 1.0
    assert not tokens.allows("sudarshan-alerts", None)  # a token exists: closed
    assert tokens.allows("sudarshan-alerts", "Bearer " + made["sudarshan-alerts"])
    assert not tokens.allows("sudarshan-screener", "Bearer " + made["sudarshan-alerts"])
    new = token()  # the same file rewritten, as a deploy may do
    (folder / "sudarshan-alerts").write_text(new + "\n")
    now[0] += 1.0
    assert not tokens.allows("sudarshan-alerts", "Bearer " + made["sudarshan-alerts"])
    assert tokens.allows("sudarshan-alerts", "Bearer " + new)
    (folder / "sudarshan-screener").write_text(token() + "\n")  # a new client's file
    now[0] += 1.0
    assert tokens.names() == ["sudarshan-alerts", "sudarshan-screener"]
    (folder / "sudarshan-alerts").unlink()  # and one removed: that client is refused
    now[0] += 1.0
    assert not tokens.allows("sudarshan-alerts", "Bearer " + new)
    assert made["sudarshan-alerts"] not in caplog.text and new not in caplog.text


def test_a_malformed_token_file_is_ignored_with_a_line_in_the_journal(tmp_path, caplog):
    folder = tmp_path / "keys"
    made = write_tokens(folder, ("sudarshan-alerts",))
    (folder / "sudarshan-screener").write_text("short\n")
    tokens = ClientTokens(folder)
    with caplog.at_level(logging.WARNING):
        assert tokens.names() == ["sudarshan-alerts"]
        assert not tokens.allows("sudarshan-screener", "Bearer short")
    assert "sudarshan-screener is not a usable key" in caplog.text
    assert tokens.allows("sudarshan-alerts", "Bearer " + made["sudarshan-alerts"])


def test_refusals_are_counted_by_client_and_logged_sparingly(tmp_path, caplog):
    tokens = ClientTokens(tmp_path)
    with caplog.at_level(logging.WARNING):
        for _ in range(25):
            tokens.refuse("sudarshan-alerts")
        tokens.refuse("x\ninjected line")
    assert tokens.refused == {"sudarshan-alerts": 25, "an unnamed client": 1}
    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(lines) == 3  # the 1st and 10th of one client, the 1st of the unnamed one
    assert "injected" not in caplog.text


# ---- the client's half ---------------------------------------------------------------------


def test_auth_headers_read_the_file_cached_and_again_when_it_changes(tmp_path):
    folder = tmp_path / "keys"
    assert auth_headers("sudarshan-alerts", folder) == {}  # no folder, no header
    made = write_tokens(folder, ("sudarshan-alerts",))
    assert auth_headers("sudarshan-alerts", folder) == bearer(made["sudarshan-alerts"])
    assert auth_headers("sudarshan-screener", folder) == {}  # only its own file
    new = token()
    (folder / "sudarshan-alerts").write_text(new + "\n")
    assert auth_headers("sudarshan-alerts", folder) == bearer(new)
    (folder / "sudarshan-alerts").write_text("garbage\n")
    assert auth_headers("sudarshan-alerts", folder) == {}
    for odd in ("", "../keys/sudarshan-alerts", "Sudarshan-Alerts", "a" * 41):
        assert auth_headers(odd, folder) == {}


def test_a_settled_file_is_not_read_again(tmp_path, monkeypatch):
    folder = tmp_path / "keys"
    made = write_tokens(folder, ("sudarshan-alerts",))
    path = folder / "sudarshan-alerts"
    import os

    old = path.stat().st_mtime - 60
    os.utime(path, (old, old))
    assert auth_headers("sudarshan-alerts", folder) == bearer(made["sudarshan-alerts"])
    reads = []
    real = client_auth.read_token
    monkeypatch.setattr(client_auth, "read_token", lambda p: reads.append(p) or real(p))
    for _ in range(3):
        auth_headers("sudarshan-alerts", folder)
    assert reads == []


class FakeFeed:
    """A local server that records each request's method, path and headers, and says ok."""

    def __init__(self):
        self.seen: list[tuple[str, str, dict[str, str]]] = []

    async def __aenter__(self):
        self.port = free_port()
        self.server = await asyncio.start_server(self._on, "127.0.0.1", self.port)
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _on(self, reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
        method, path, _ = head[0].split(" ", 2)
        headers = {}
        for line in head[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        n = int(headers.get("content-length") or 0)
        if n:
            await reader.readexactly(n)
        self.seen.append((method, path, headers))
        body = b'{"ok":true,"ticks":{}}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()


@pytest.fixture
def made(tmp_path, monkeypatch):
    monkeypatch.setenv(client_auth.ENV_DIR, str(tmp_path / "keys"))
    return write_tokens(tmp_path / "keys")


def test_alerts_send_their_own_token_on_claims(made):
    async def scenario():
        async with FakeFeed() as feed, httpx.AsyncClient(trust_env=False) as http:
            link = AlertsLink("tcp://127.0.0.1:1", feed.url, http)
            try:
                await link.put_claims({REL: "ltpc"})
                await link.drop_claims()
            finally:
                link.close()
            return feed.seen

    seen = asyncio.run(scenario())
    assert [(m, p) for m, p, _ in seen] == [
        ("PUT", "/claims/sudarshan-alerts"), ("DELETE", "/claims/sudarshan-alerts")
    ]  # fmt: skip
    assert all(h["authorization"] == "Bearer " + made["sudarshan-alerts"] for _, _, h in seen)


def test_market_alerts_off_drops_their_claims_with_their_token(made):
    async def scenario():
        async with FakeFeed() as feed:
            await alerts_runtime.ReleasedClaims(feed.url).run()
            return feed.seen

    ((method, path, headers),) = asyncio.run(scenario())
    assert (method, path) == ("DELETE", "/claims/sudarshan-alerts")
    assert headers["authorization"] == "Bearer " + made["sudarshan-alerts"]


def test_the_screener_sends_its_own_token_not_the_alerts(made):
    async def scenario():
        async with FakeFeed() as feed, httpx.AsyncClient(trust_env=False) as http:
            link = screener_runtime.FeedLink("tcp://127.0.0.1:1", feed.url, http)
            try:
                await link.put_claims({REL: "full"})
                await link.drop_claims()
            finally:
                link.close()
            service = SimpleNamespace(settings=SimpleNamespace(feed_url=feed.url))
            await screener_runtime.release_claims(service, waits=(0.0,))
            return feed.seen

    seen = asyncio.run(scenario())
    assert [(m, p) for m, p, _ in seen] == [
        ("PUT", "/claims/sudarshan-screener"), ("DELETE", "/claims/sudarshan-screener"),
        ("DELETE", "/claims/sudarshan-screener"),
    ]  # fmt: skip
    assert all(h["authorization"] == "Bearer " + made["sudarshan-screener"] for _, _, h in seen)


def test_the_portfolio_sends_its_own_token_on_claims_and_on_the_governor(made):
    async def scenario():
        async with FakeFeed() as feed:
            client = portfolio_link.FeedClient(feed.url)
            try:
                await client.call("PUT", "/claims/sudarshan-portfolio", json={"keys": {}})
                await client.call(
                    "GET",
                    "/upstox/v3/market-quote/quotes",
                    params={"instrument_key": REL},
                    headers={"X-Sudarshan-Client": "sudarshan-portfolio",
                             "X-Sudarshan-Priority": "bulk"},
                )  # fmt: skip
            finally:
                await client.aclose()
            await portfolio_link.release(feed.url, delays=(0.0,))
            return feed.seen

    seen = asyncio.run(scenario())
    assert [(m, p.split("?")[0]) for m, p, _ in seen] == [
        ("PUT", "/claims/sudarshan-portfolio"),
        ("GET", "/upstox/v3/market-quote/quotes"),
        ("DELETE", "/claims/sudarshan-portfolio"),
    ]
    assert all(h["authorization"] == "Bearer " + made["sudarshan-portfolio"] for _, _, h in seen)
    assert seen[1][2]["x-sudarshan-client"] == "sudarshan-portfolio"  # the caller's own kept


def test_a_client_without_a_token_file_sends_no_authorization_header(tmp_path, monkeypatch):
    monkeypatch.setenv(client_auth.ENV_DIR, str(tmp_path / "no-keys"))

    async def scenario():
        async with FakeFeed() as feed:
            client = portfolio_link.FeedClient(feed.url)
            try:
                await client.call("PUT", "/claims/sudarshan-portfolio", json={"keys": {}})
            finally:
                await client.aclose()
            return feed.seen

    ((_, _, headers),) = asyncio.run(scenario())
    assert "authorization" not in headers


# ---- the real service, and the unit ----------------------------------------------------------


def test_the_real_feed_refuses_an_unkeyed_write_once_a_key_exists(feed_env, tmp_path, fast):
    import feed_fakes as ff

    from sudarshan.feed.config import Settings
    from sudarshan.feed.service import FeedService

    made = write_tokens(tmp_path / "feed-clients", ("sudarshan-alerts",))

    async def scenario():
        async with ff.FakeUpstox() as fake:
            svc = FeedService(Settings.from_env(feed_env(fake)))
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.http is not None)
            base = f"http://127.0.0.1:{svc.http_port}"
            async with httpx.AsyncClient(base_url=base, trust_env=False) as http:
                body = {"keys": {REL: "ltpc"}}
                url = "/claims/sudarshan-alerts"
                assert (await http.put(url, json=body)).status_code == 401
                ok = await http.put(url, json=body, headers=bearer(made["sudarshan-alerts"]))
                assert ok.status_code == 200 and ok.json()["claimed"] == 1
                assert (await http.get("/health")).status_code == 200
                assert (await http.get("/claims")).json()["clients"]["sudarshan-alerts"][
                    "keys"
                ] == 1
                governor = await http.get("/upstox/v2/anything")
                assert governor.status_code == 401  # before the governor's own answers
            svc.stop("test")
            await asyncio.wait_for(run, 10)

    asyncio.run(scenario())


def test_the_feed_unit_closes_its_state_folder_and_the_deploy_policy_accepts_it():
    import shutil
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    unit = repo / "sudarshan" / "ops" / "systemd" / "sudarshan-feed.service"
    text = unit.read_text()
    assert "\nStateDirectoryMode=0750\n" in text and "\nUMask=0027\n" in text
    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    tool = (repo / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy").read_text()
    start = tool.index("unit_ok() {")
    function = tool[start : tool.index("\n}\n", start) + 3]
    script = 'log() { echo "$*"; }\n' + function + '\nunit_ok "$1" "$2"\n'
    done = subprocess.run(
        ["bash", "-c", script, "policy", str(unit), unit.name], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
