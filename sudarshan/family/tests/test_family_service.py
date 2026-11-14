"""F2: the sudarshan-family service: one listener for every door (POST, PUT and a WebSocket upgrade on
one port), the sealed doors (seal, replay, expiry, op mismatch, key, principal), forwarded headers
answered 404, the size caps, a taken port, switched off, a newer store, /health that names no one,
the settings and keys, and a stop that leaves nothing behind."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from family_net import ask, fast_hasher, keys, running, sealed, settings
from family_testkit import close, set_version
from websockets.asyncio.client import connect

from sudarshan.family import config, migrations, sessions
from sudarshan.family.listener import Response
from sudarshan.family.service import FamilyService
from sudarshan.family.store import FamilyStore
from sudarshan.tests.fdwatch import FdWatch

REPO = Path(__file__).resolve().parents[3]
FORWARDED = ("X-Forwarded-For", "X-Forwarded-Proto", "X-Real-IP")


class FakeViewer:
    """Stands in for the viewer API (F4): answers every route and lets one session in."""

    def __init__(self):
        self.seen: list[tuple[str, str, bytes]] = []

    async def http(self, request):
        self.seen.append((request.method, request.path, request.body))
        return Response.ok({"method": request.method, "path": request.path})

    async def authorize(self, request):
        return sessions.Session("ab" * 32, {"id": 1, "status": "active"}, {})


def service_for(root: Path, *, enabled: bool = True, **kw) -> FamilyService:
    made = FamilyService(settings(root, enabled), keys(enabled), hasher=fast_hasher(), **kw)
    return made


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


# ---- one listener, every door ---------------------------------------------------------------------
def test_post_put_and_an_upgrade_on_one_port(tmp_path):
    async def scenario():
        service = service_for(tmp_path)
        service.viewer = FakeViewer()
        async with running(service):
            port = service.listener.port
            admin = sealed(service.keys.admin, "member_list")
            status, head, answer = await ask(port, "POST", "/v1/admin/member_list", body=admin)
            assert (status, answer) == (200, {"status": "success", "data": []})
            assert head["cache-control"] == ["no-store"] and head["connection"] == ["close"]
            assert (await ask(port, "POST", "/family/api/login", body=b"{}"))[0] == 200
            status, _, answer = await ask(port, "PUT", "/family/api/settings", body=b'{"v":1}')
            assert (status, answer["data"]["method"]) == (200, "PUT")
            async with connect(f"ws://127.0.0.1:{port}/family/ws") as sock:
                await sock.send(json.dumps({"action": "authenticate", "family": True}))
                assert json.loads(await sock.recv()) == {"type": "auth", "status": "success"}
                assert len(service.registry) == 1
            for _ in range(100):
                if not len(service.registry):
                    break
                await asyncio.sleep(0.02)
            assert len(service.registry) == 0
            assert [m for m, _, _ in service.viewer.seen] == ["POST", "PUT"]

    run(scenario())


def test_a_websocket_is_pinged_and_closed_after_silence(tmp_path):
    """A phone that answers nothing gets pings, then the close (1001) once it has been silent."""
    from websockets.client import ClientProtocol
    from websockets.frames import Opcode
    from websockets.uri import parse_uri

    from sudarshan.family import ws

    async def scenario():
        service = service_for(tmp_path)
        service.viewer = FakeViewer()
        original = ws.ViewerSocket.__init__

        def quick(self, *args, **kwargs):
            original(self, *args, **(kwargs | {"ping_every": 0.1, "silence": 0.6}))

        ws.ViewerSocket.__init__ = quick
        try:
            async with running(service):
                port = service.listener.port
                client = ClientProtocol(parse_uri(f"ws://127.0.0.1:{port}/family/ws"))
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                client.send_request(client.connect())
                for chunk in client.data_to_send():
                    writer.write(chunk)
                began, frames = time.monotonic(), []
                async with asyncio.timeout(10):
                    while True:  # read only: the pings are never answered
                        data = await reader.read(65536)
                        if not data:
                            break
                        client.receive_data(data)
                        frames += [e for e in client.events_received() if hasattr(e, "opcode")]
                        if any(f.opcode is Opcode.CLOSE for f in frames):
                            break
                writer.close()
                opcodes = [f.opcode for f in frames]
                assert Opcode.PING in opcodes and opcodes[-1] is Opcode.CLOSE
                assert client.close_rcvd is not None and client.close_rcvd.code == 1001
                assert 0.5 <= time.monotonic() - began < 5
        finally:
            ws.ViewerSocket.__init__ = original

    run(scenario())


# ---- the sealed doors -------------------------------------------------------------------------------
def test_seals_replays_expiry_op_key_and_principal(tmp_path):
    async def scenario():
        service = service_for(tmp_path)
        async with running(service):
            port, admin, nudge = service.listener.port, service.keys.admin, service.keys.nudge

            async def post(path, body):
                status, _, answer = await ask(port, "POST", path, body=body)
                return status, answer

            good = sealed(admin, "member_list")
            assert (await post("/v1/admin/member_list", good))[0] == 200
            status, answer = await post("/v1/admin/member_list", good)  # the same nonce again
            assert status == 403 and answer["code"] == "forbidden"
            old = sealed(admin, "member_list", now=time.time() - 120)
            assert (await post("/v1/admin/member_list", old))[0] == 403
            other_op = sealed(admin, "member_create", display_name="M", role="viewer")
            assert (await post("/v1/admin/member_list", other_op))[0] == 403
            assert (await post("/v1/admin/member_list", sealed(nudge, "member_list")))[0] == 403
            not_owner = sealed(admin, "member_list", principal="someone")
            assert (await post("/v1/admin/member_list", not_owner))[0] == 403
            assert (await post("/v1/admin/no_such_op", sealed(admin, "no_such_op")))[0] == 404
            assert (await post("/v1/admin/share_list", sealed(admin, "share_list")))[0] == 501
            assert (await ask(port, "GET", "/v1/admin/member_list"))[0] == 404
            made = sealed(admin, "member_create", display_name="Member A", role="viewer")
            status, answer = await post("/v1/admin/member_create", made)
            assert status == 200 and answer["data"]["display_name"] == "Member A"
            assert "Member A" not in json.dumps((await ask(port, "GET", "/health"))[2])
            nudged = sealed(nudge, "nudge", principal="sudarshan-alerts", kind="orders",
                            sandbox=False, at=1)  # fmt: skip
            assert await post("/v1/nudge", nudged) == (
                200,
                {"status": "success", "data": {"taken": False}},
            )
            assert (
                await post(
                    "/v1/nudge",
                    sealed(
                        admin,
                        "nudge",
                        principal="sudarshan-alerts",
                        kind="orders",
                        sandbox=False,
                        at=1,
                    ),
                )
            )[0] == 403
            odd = sealed(nudge, "nudge", principal="sudarshan-alerts", kind="x", sandbox=0, at=1)
            assert (await post("/v1/nudge", odd))[0] == 400
            assert service.doors.counts["refused"] >= 6

    run(scenario())


@pytest.mark.parametrize("header", FORWARDED)
def test_a_forwarded_request_never_reaches_a_sealed_door_or_health(tmp_path, header):
    """Mutation target 16: what nginx forwards carries these headers; the sealed doors answer 404."""

    async def scenario():
        service = service_for(tmp_path)
        service.viewer = FakeViewer()
        async with running(service):
            port, admin = service.listener.port, service.keys.admin
            more = {header: "203.0.113.7"}
            for path, body in (("/v1/admin/member_list", sealed(admin, "member_list")),
                               ("/v1/nudge", sealed(service.keys.nudge, "nudge",
                                                    principal="sudarshan-alerts", kind="orders",
                                                    sandbox=False, at=1))):  # fmt: skip
                status, _, answer = await ask(port, "POST", path, body=body, headers=more)
                assert (status, answer["code"]) == (404, "not_found"), path
            assert (await ask(port, "GET", "/health", headers=more))[0] == 404
            assert (await ask(port, "POST", "/v1/anything", body=b"x"))[0] == 404
            assert (await ask(port, "POST", "/family/api/login", body=b"{}", headers=more))[
                0
            ] == 200
            status, _, _ = await ask(port, "POST", "/v1/admin/member_list",
                                     body=sealed(admin, "member_list"),
                                     headers={"Origin": "https://example.invalid"})  # fmt: skip
            assert status == 403

    run(scenario())


def test_the_size_caps_are_judged_before_the_body_is_read(tmp_path):
    async def scenario():
        service = service_for(tmp_path)
        service.viewer = FakeViewer()
        async with running(service):
            port = service.listener.port
            for path, cap in (("/v1/admin/member_list", 64 * 1024), ("/v1/nudge", 4 * 1024),
                              ("/family/api/login", 64 * 1024)):  # fmt: skip
                big = await ask(port, "POST", path, headers={"Content-Length": str(cap + 1)},
                                length=False)  # fmt: skip
                assert (big[0], big[2]["code"]) == (413, "too_large"), path
                at_cap = await ask(port, "POST", path, body=b"x" * cap)
                assert at_cap[0] != 413, path
            chunked = await ask(
                port,
                "POST",
                "/family/api/login",
                headers={"Transfer-Encoding": "chunked"},
                length=False,
            )
            assert chunked[0] == 400
            assert (await ask(port, "POST", "/family/api/login", length=False))[0] == 400
            twice = await ask(
                port, "POST", "/family/api/login", body=b"{}", headers={"Content-Length": "2"}
            )  # a second Content-Length
            assert twice[0] == 400

    run(scenario())


@pytest.mark.parametrize(
    "head",
    [
        "POST /family/api/login HTTP/1.1\r\nHost: x\r\nContent-Length: \xb2\r\n\r\n",
        "POST /family/api/login HTTP/1.1\r\nHost: x\r\nContent-Length: \xb9\xb3\r\n\r\n",
        "POST /v1/nudge HTTP/1.1\r\nHost: x\r\nContent-Length: \xb3\r\n\r\n",
        "GET //[x/ HTTP/1.1\r\nHost: x\r\n\r\n",
        "GET http://[::1/family/api/me HTTP/1.1\r\nHost: x\r\n\r\n",
    ],
    ids=["superscript-two", "superscripts", "nudge-superscript", "open-ipv6-target", "open-host"],
)
def test_a_head_it_cannot_read_is_400_in_words_never_a_500(tmp_path, caplog, head):
    """Review L2: "\\xb2".isdigit() is true and int() refuses it, and urlsplit raises on a target
    such as //[x/; both reached the catch-all and answered 500 with a traceback in the log."""

    async def scenario():
        service = service_for(tmp_path)
        service.viewer = FakeViewer()
        async with running(service):
            reader, writer = await asyncio.open_connection("127.0.0.1", service.listener.port)
            writer.write(head.encode("latin-1"))
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), 15)
            writer.close()
            first, _, body = raw.partition(b"\r\n\r\n")
            assert first.split(b"\r\n")[0] == b"HTTP/1.1 400 Bad Request"
            assert json.loads(body) == {
                "status": "error",
                "code": "invalid",
                "message": "That was not accepted. Check what you typed and try again.",
            }
            assert service.viewer.seen == []

    with caplog.at_level(logging.INFO, logger="sudarshan.family"):
        run(scenario())
    assert not [r for r in caplog.records if r.exc_info or r.pathname.endswith("listener.py")]


# ---- port, switch, store ------------------------------------------------------------------------------
def test_a_taken_port_is_said_in_words_and_tried_again(tmp_path, caplog):
    import socket

    async def scenario():
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        ticks = []

        async def job(service):
            while True:
                ticks.append(1)
                await asyncio.sleep(0.05)

        service = FamilyService(settings(tmp_path, True, ).__class__(
            **{**settings(tmp_path).__dict__, "http_port": port}), keys(), hasher=fast_hasher(),
            port_retry_s=0.2, jobs={"tick": job})  # fmt: skip
        async with running(service, wait_bound=False):
            for _ in range(100):
                if service.state == "port_taken":
                    break
                await asyncio.sleep(0.02)
            assert service.state == "port_taken"
            await asyncio.sleep(0.3)
            assert ticks, "the jobs keep running while the port is taken"
            holder.close()
            for _ in range(100):
                if service.listener.port == port:
                    break
                await asyncio.sleep(0.05)
            assert service.listener.port == port and service.state == "running"
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1 and f"{port} on this server is taken" in errors[0]
        assert "tries again every minute" in errors[0]

    with caplog.at_level(logging.INFO, logger="sudarshan.family"):
        run(scenario())


def test_switched_off_every_viewer_request_is_told_and_nothing_is_opened(tmp_path):
    async def scenario():
        service = service_for(tmp_path, enabled=False)
        async with running(service):
            port = service.listener.port
            status, _, answer = await ask(port, "GET", "/family/api/me")
            assert (status, answer["message"]) == (503, "The family view is switched off on "
                                                   "the server.")  # fmt: skip
            assert (await ask(port, "POST", "/family/api/login", body=b"{}"))[0] == 503
            refused = await ask(port, "GET", "/family/ws",
                                headers={"Upgrade": "websocket", "Connection": "Upgrade",
                                         "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                                         "Sec-WebSocket-Version": "13"})  # fmt: skip
            assert refused[0] == 503
            off = await ask(
                port,
                "POST",
                "/v1/admin/member_list",
                body=sealed(service.keys.admin, "member_list"),
            )
            assert off[2]["data"] == {"enabled": False, "message": "The family view is switched "
                                      "off on the server."}  # fmt: skip
            bad = await ask(port, "POST", "/v1/admin/member_list", body=b"not a seal")
            assert bad[0] == 403  # the seal is checked before the switch is said
            assert service.jobs == {} and service.store is None
            health = (await ask(port, "GET", "/health"))[2]
            assert health["family_state"]["enabled"] is False
        assert not (tmp_path / "state").exists()

    run(scenario())


VIEWER_OFF = "The family view is not available right now. Ask the account owner."
UPGRADE = {"Upgrade": "websocket", "Connection": "Upgrade",
           "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13"}  # fmt: skip


def test_a_store_a_newer_release_wrote_keeps_the_view_off_in_the_owners_words(tmp_path):
    """The owner reads why on his page and in /health; a family phone reads one sentence it can act
    on, never "install that release again" (review L4)."""
    made = FamilyStore(tmp_path / "state" / "family.db")
    made.close()
    close(None)
    set_version(tmp_path / "state" / "family.db", 99)

    async def scenario():
        service = service_for(tmp_path)
        async with running(service):
            port = service.listener.port
            status, _, answer = await ask(port, "GET", "/family/api/me")
            assert (status, answer["code"], answer["message"]) == (503, "off", VIEWER_OFF)
            status, _, answer = await ask(port, "GET", "/family/ws", headers=UPGRADE)
            assert (status, answer["message"]) == (503, VIEWER_OFF)
            off = await ask(
                port,
                "POST",
                "/v1/admin/member_list",
                body=sealed(service.keys.admin, "member_list"),
            )
            assert (
                off[2]["data"]["enabled"] is False and "newer release" in off[2]["data"]["message"]
            )
            health = (await ask(port, "GET", "/health"))[2]["family_state"]
            assert "newer release" in health["reason"] and health["store"] == "closed"

    run(scenario())


def test_a_store_that_will_not_open_tells_a_phone_to_ask_the_owner(tmp_path):
    """Review L4: "check that the server has free disk space" is the owner's sentence."""
    (tmp_path / "state").write_text("a file where the store's folder should be")

    async def scenario():
        service = service_for(tmp_path)
        async with running(service):
            port = service.listener.port
            for path in ("/family/api/me", "/family/api/login"):
                method = "GET" if path.endswith("me") else "POST"
                status, _, answer = await ask(port, method, path, body=b"{}" * (method == "POST"))
                assert (status, answer["code"], answer["message"]) == (503, "off", VIEWER_OFF)
            status, _, answer = await ask(port, "GET", "/family/ws", headers=UPGRADE)
            assert (status, answer["message"]) == (503, VIEWER_OFF)
            off = (await ask(port, "POST", "/v1/admin/state",
                             body=sealed(service.keys.admin, "state")))[2]["data"]  # fmt: skip
            assert off["enabled"] is False and "free disk space" in off["message"]

    run(scenario())


# ---- /health ---------------------------------------------------------------------------------------------
def test_health_carries_counts_and_states_and_no_one(tmp_path):
    async def scenario():
        service = service_for(tmp_path)
        async with running(service):
            port, admin = service.listener.port, service.keys.admin
            for n in range(2):
                await ask(port, "POST", "/v1/admin/member_create",
                          body=sealed(admin, "member_create", display_name=f"Member {n}",
                                      role="viewer"))  # fmt: skip
            status, _, doc = await ask(port, "GET", "/health")
            assert status == 200 and doc["service"] == "sudarshan-family"
            state = doc["family_state"]
            assert (state["enabled"], state["members"], state["sessions"]) == (True, 2, 0)
            assert state["store_version"] == migrations.LATEST
            assert state["jobs"] == {"prune": "running", "sockets": "running", "copy": "running"}
            assert set(state["notices"]) == {"written", "failed"} and set(state["copy"]) == {
                "at",
                "ok",
            }
            text = json.dumps(doc)
            assert not re.search(r"[0-9a-f]{16,}", text), "an id or a hash on /health"
            assert "Member" not in text and "endpoint" not in text and "handle" not in text

    run(scenario())


# ---- settings and keys --------------------------------------------------------------------------------
def test_settings_defaults_and_refusals():
    found = config.Settings.from_env({})
    assert (found.enabled, found.http_port, found.max_members, found.max_devices) == (
        False,
        5585,
        4,
        3,
    )
    assert found.etc_dir == Path("/etc/sudarshan-family") and not found.name_members
    assert found.holidays_file == Path("/etc/sudarshan-family/nse_holidays.txt")
    on = config.Settings.from_env({"SUDARSHAN_FAMILY": "on", "SUDARSHAN_FAMILY_MAX_DEVICES": "2",
                                   "STATE_DIRECTORY": "/x:/y"})  # fmt: skip
    assert on.enabled and on.max_devices == 2 and on.state_dir == Path("/x")
    for name, value in (("SUDARSHAN_FAMILY", "maybe"), ("SUDARSHAN_FAMILY_MAX_MEMBERS", "5"),
                        ("SUDARSHAN_FAMILY_FEED_URL", "http://example.invalid:5580"),
                        ("SUDARSHAN_FAMILY_PUSH_SUBJECT", "someone"),
                        ("SUDARSHAN_FAMILY_HTTP_PORT", "0")):  # fmt: skip
        with pytest.raises(config.ConfigError) as caught:
            config.Settings.from_env({name: value})
        assert name in str(caught.value)


def test_the_settings_file_is_read_under_the_environment(tmp_path, monkeypatch):
    (tmp_path / "family.env").write_text("SUDARSHAN_FAMILY=on\nSUDARSHAN_FAMILY_MAX_MEMBERS=2\n")
    monkeypatch.setenv("SUDARSHAN_FAMILY_ENV_FILE", str(tmp_path / "family.env"))
    monkeypatch.setenv("SUDARSHAN_FAMILY_MAX_MEMBERS", "3")
    found = config.Settings.from_env()
    assert found.enabled and found.max_members == 3


def test_keys_are_read_as_the_provision_step_writes_them(tmp_path):
    from cryptography.fernet import Fernet
    from family_testkit import store_key_text

    etc = tmp_path / "etc"
    etc.mkdir()
    one = config.Settings(enabled=True, etc_dir=etc)
    with pytest.raises(config.ConfigError, match="seal-admin.key is missing"):
        config.Keys.load(one)
    for purpose in ("admin", "nudge"):
        (etc / f"seal-{purpose}.key").write_text(Fernet.generate_key().decode() + "\n")
    with pytest.raises(config.ConfigError, match="store.key"):
        config.Keys.load(one)
    (etc / "store.key").write_text(store_key_text())
    assert config.Keys.load(one).store is not None
    assert config.Keys.load(config.Settings(enabled=False, etc_dir=etc)).store is None
    (etc / "seal-nudge.key").write_text("not a key\n")
    with pytest.raises(config.ConfigError, match="not a seal key"):
        config.Keys.load(one)


def test_a_key_it_cannot_use_stops_the_start_with_exit_status_2(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.family"], capture_output=True, text=True, timeout=60,
        cwd=REPO, env={**os.environ, "SUDARSHAN_FAMILY_ETC_DIR": str(tmp_path)},
    )  # fmt: skip
    assert done.returncode == 2
    assert (
        "sudarshan-family cannot run" in done.stderr and "seal-admin.key is missing" in done.stderr
    )


# ---- a stop leaves nothing ------------------------------------------------------------------------------
def test_a_stop_leaves_no_task_thread_or_descriptor(tmp_path):
    watch = FdWatch()

    async def scenario():
        service = service_for(tmp_path)
        service.viewer = FakeViewer()
        async with running(service):
            port = service.listener.port
            await ask(
                port,
                "POST",
                "/v1/admin/member_list",
                body=sealed(service.keys.admin, "member_list"),
            )
            await ask(port, "GET", "/health")
            sock = await connect(f"ws://127.0.0.1:{port}/family/ws")
            await sock.send(json.dumps({"action": "authenticate"}))
            await sock.recv()
        await sock.wait_closed()  # the stop closed it from the service's side
        assert sock.close_code == 1001
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert others == []

    run(scenario())
    names = [t.name for t in threading.enumerate()]
    assert not [n for n in names if n.startswith(("family-store", "family-argon2"))], names
    watch.assert_clean()


def test_descriptors_stay_flat_over_a_hundred_rounds(tmp_path):
    """Design 7.6: a hundred rounds of an admin op, a nudge and a socket opened and closed leave
    the process holding what it held after the first round."""

    def count() -> int:
        return len(os.listdir("/proc/self/fd"))

    async def one_round(service, port):
        await ask(
            port, "POST", "/v1/admin/member_list", body=sealed(service.keys.admin, "member_list")
        )
        await ask(port, "POST", "/v1/nudge",
                  body=sealed(service.keys.nudge, "nudge", principal="sudarshan-alerts",
                              kind="orders", sandbox=False, at=1))  # fmt: skip
        async with connect(f"ws://127.0.0.1:{port}/family/ws") as sock:
            await sock.send(json.dumps({"action": "authenticate"}))
            await sock.recv()

    async def scenario():
        service = service_for(tmp_path)
        service.viewer = FakeViewer()
        async with running(service):
            port = service.listener.port
            await one_round(service, port)
            await asyncio.sleep(0.2)
            before = count()
            for _ in range(100):
                await one_round(service, port)
            for _ in range(50):
                if count() <= before and not len(service.registry):
                    break
                await asyncio.sleep(0.05)
            assert count() <= before, (before, count())
            assert len(service.registry) == 0 and len(service.listener._connections) == 0

    run(scenario())
