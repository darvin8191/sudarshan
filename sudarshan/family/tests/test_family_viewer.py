"""F4: the viewer API over the real listener: the two cookies exactly, CSRF on every request that is
not a GET, join, sign-in, me, sign-out, a password change, devices, the session checked on every
request and at the WebSocket's handshake, and a revoke or an expiry closing that device's sockets
within 5 s with 4401 and "Signed out on this device." (design 2.2, 6.7, 7.2; mutation target 1)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pyotp
import pytest
from family_net import ask, fast_hasher, keys, running, sealed, settings
from family_testkit import close
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from sudarshan.family import contract
from sudarshan.family.service import FamilyService

PASSWORD = "a long synthetic passphrase"
ANDROID = "Mozilla/5.0 (Linux; Android 15; K) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile"
DAY = 86_400_000


class Clock:
    def __init__(self):
        self.at = int(time.time() * 1000)

    def __call__(self) -> int:
        return self.at


class Phone:
    """A phone's browser, as far as the service can tell: a cookie jar and its headers."""

    def __init__(self, port: int, agent: str = ANDROID):
        self.port, self.agent = port, agent
        self.jar: dict[str, str] = {}
        self.last_cookies: list[str] = []

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.jar.items())

    async def call(self, method: str, path: str, body=None, headers=None, csrf=True):
        more = {"User-Agent": self.agent, "Cookie": self.cookie_header()} | (headers or {})
        if method != "GET" and csrf and contract.CSRF_COOKIE in self.jar:
            more.setdefault(contract.CSRF_HEADER, self.jar[contract.CSRF_COOKIE])
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode()
            more.setdefault("Content-Type", "application/json")
        status, head, answer = await ask(self.port, method, "/family/api" + path, body=raw,
                                         headers=more)  # fmt: skip
        self.last_cookies = head.get("set-cookie", [])
        for line in self.last_cookies:
            name, _, rest = line.partition("=")
            value = rest.split(";")[0]
            if "Max-Age=0" in line:
                self.jar.pop(name, None)
            else:
                self.jar[name] = value
        return status, answer


def attributes(line: str) -> tuple[str, str, set[str]]:
    name_value, *rest = [part.strip() for part in line.split(";")]
    name, _, value = name_value.partition("=")
    return name, value, set(rest)


class World:
    """A running service with a fake clock, the owner's sealed ops, and phones."""

    def __init__(self, root: Path):
        self.clock = Clock()
        self.service = FamilyService(settings(root), keys(), hasher=fast_hasher(),
                                     clock=self.clock)  # fmt: skip
        self.port = 0

    async def owner(self, op: str, **fields):
        status, _, answer = await ask(
            self.port, "POST", f"/v1/admin/{op}", body=sealed(self.service.keys.admin, op, **fields)
        )
        assert status == 200, answer
        return answer["data"]

    def code(self, secret: str) -> str:
        return pyotp.TOTP(secret).at(self.clock() // 1000)

    async def member(self, handle: str = "member_a", role: str = "viewer"):
        """A member who joined on a new phone: the card, their code-app secret and the phone."""
        card = await self.owner("member_create", display_name="Member A", role=role)
        invite = await self.owner("invite_create", member_id=card["id"])
        phone = Phone(self.port)
        assert (await phone.call("GET", "/me"))[0] == 401  # what a page does first: the CSRF cookie
        info = (await phone.call("GET", f"/join/{invite['code']}"))[1]["data"]
        status, answer = await phone.call(
            "POST",
            "/join",
            {
                "code": invite["code"],
                "handle": handle,
                "password": PASSWORD,
                "totp": self.code(info["totp_key"]),
            },
        )
        assert status == 200, answer
        return card, info["totp_key"], phone

    async def login(self, handle: str, secret: str, agent: str = ANDROID) -> Phone:
        self.clock.at += 30_000
        phone = Phone(self.port, agent)
        await phone.call("GET", "/me")
        status, answer = await phone.call(
            "POST", "/login", {"handle": handle, "password": PASSWORD, "totp": self.code(secret)}
        )
        assert status == 200, answer
        return phone


def scenario(root: Path, body):
    async def go():
        world = World(root)
        async with running(world.service):
            world.port = world.service.listener.port
            await body(world)

    try:
        asyncio.run(go())
    finally:
        close(None)


# ---- the cookies (mutation target 1) ---------------------------------------------------------------
def test_the_two_cookies_are_exactly_what_the_design_says(tmp_path):
    async def body(world: World):
        _, secret, phone = await world.member()
        phone = await world.login("member_a", secret)
        session = [c for c in phone.last_cookies if c.startswith(contract.COOKIE + "=")]
        csrf = [c for c in phone.last_cookies if c.startswith(contract.CSRF_COOKIE + "=")]
        assert len(session) == 1 and len(csrf) == 1
        name, value, attrs = attributes(session[0])
        assert name == "__Host-sudarshan_family" and len(value) >= 43
        assert attrs == {"Max-Age=2592000", "Path=/", "Secure", "SameSite=Strict", "HttpOnly"}
        name, value, attrs = attributes(csrf[0])
        assert name == "__Host-sudarshan_family_csrf" and len(value) >= 43
        assert attrs == {"Max-Age=2592000", "Path=/", "Secure", "SameSite=Strict"}  # page reads it
        kept = [c for c in phone.last_cookies if c.startswith(contract.PHONE_COOKIE + "=")]
        name, value, attrs = attributes(kept[0])  # review M2: the member's own phone, 90 days
        assert name == "__Host-sudarshan_family_phone" and len(value) >= 60 and len(kept) == 1
        assert attrs == {"Max-Age=7776000", "Path=/", "Secure", "SameSite=Strict", "HttpOnly"}
        assert not any("domain=" in c.lower() for c in phone.last_cookies)

    scenario(tmp_path, body)


def test_an_answer_sets_the_csrf_cookie_only_when_the_request_had_none(tmp_path):
    async def body(world: World):
        phone = Phone(world.port)
        await phone.call("GET", "/me")
        first = phone.jar[contract.CSRF_COOKIE]
        await phone.call("GET", "/me")
        assert phone.jar[contract.CSRF_COOKIE] == first and phone.last_cookies == []

    scenario(tmp_path, body)


# ---- CSRF on every request that is not a GET --------------------------------------------------------
def test_every_write_needs_the_csrf_header_that_matches_its_cookie(tmp_path):
    async def body(world: World):
        _, _, phone = await world.member()
        writes = [op for op in contract.VIEWER_OPS if op.method != "GET"]
        assert len(writes) == len(contract.VIEWER_WRITES)
        for op in writes:
            path = op.route.replace("<id>", "0123456789abcdef")
            for headers in ({contract.CSRF_HEADER: ""}, {contract.CSRF_HEADER: "x" * 43},
                            {"Origin": "https://elsewhere.example"}):  # fmt: skip
                status, answer = await phone.call(op.method, path, {}, headers=headers)
                assert (status, answer["code"]) == (403, "csrf"), (op.name, headers)
                assert answer["message"] == "This page is out of date. Reload it and try again."
            status, _ = await phone.call(op.method, path, {}, csrf=False)
            assert status == 403, op.name
        assert (await phone.call("GET", "/me"))[0] == 200  # a GET needs no header

    scenario(tmp_path, body)


def test_a_write_takes_json_only(tmp_path):
    async def body(world: World):
        _, _, phone = await world.member()
        for raw, kind in (
            (b"handle=x", "application/x-www-form-urlencoded"),
            (b"[1, 2]", "application/json"),
            (b"{not json", "application/json"),
        ):
            status, _, answer = await ask(
                world.port,
                "POST",
                "/family/api/logout",
                body=raw,
                headers={
                    "Content-Type": kind,
                    "Cookie": phone.cookie_header(),
                    contract.CSRF_HEADER: phone.jar[contract.CSRF_COOKIE],
                },
            )
            assert (status, answer["code"]) == (400, "invalid"), raw

    scenario(tmp_path, body)


# ---- sign-in, me, sign-out ----------------------------------------------------------------------------
def test_join_me_and_sign_out(tmp_path):
    async def body(world: World):
        card, _, phone = await world.member()
        status, answer = await phone.call("GET", "/me")
        me = answer["data"]
        assert status == 200 and me["member"] == {"id": card["id"], "handle": "member_a",
                                                  "display_name": "Member A", "role": "viewer",
                                                  "language": "en"}  # fmt: skip
        assert me["privacy"] == {"pnl": True, "qty": True, "funds": False, "orders": True}
        assert me["hidden"] == ["funds"] and me["limited"] is False
        assert me["device"]["current"] and me["device"]["label"] == "Android 15 · Chrome 142"
        assert me["expires_at"] == world.clock.at + 14 * DAY
        status, answer = await phone.call("POST", "/logout", {})
        assert status == 200 and any("Max-Age=0" in c for c in phone.last_cookies)
        status, answer = await phone.call("GET", "/me")
        assert status == 401 and answer["code"] == "signed_out"
        stale = Phone(world.port)
        stale.jar[contract.COOKIE] = "x" * 43
        status, answer = await stale.call("GET", "/me")
        assert (status, answer["message"]) == (401, "You were signed out on this device.")
        cleared = [c for c in stale.last_cookies if c.startswith(contract.COOKIE + "=")]
        assert cleared and "Max-Age=0" in cleared[0]  # overwritten, never Clear-Site-Data

    scenario(tmp_path, body)


def test_a_wrong_name_and_a_wrong_password_get_one_answer(tmp_path):
    async def body(world: World):
        await world.member()
        phone = Phone(world.port)
        await phone.call("GET", "/me")
        nobody = await phone.call(
            "POST", "/login", {"handle": "nobody_here", "password": PASSWORD, "totp": "123456"}
        )
        wrong = await phone.call("POST", "/login", {"handle": "member_a",
                                                    "password": "not the password at all",
                                                    "totp": "123456"})  # fmt: skip
        assert nobody == wrong == (401, {"status": "error", "code": "signin_failed",
                                         "message": "That sign-in name, password or code did "
                                                    "not match."})  # fmt: skip
        for _ in range(3):
            await phone.call("POST", "/login", {"handle": "member_a", "password": "wrong " * 3,
                                                "totp": "1"})  # fmt: skip
        status, answer = await phone.call(
            "POST", "/login", {"handle": "member_a", "password": "wrong " * 3, "totp": "1"}
        )
        assert status == 429 and answer["message"].startswith("Too many wrong attempts. Try again")

    scenario(tmp_path, body)


def test_a_stranger_cannot_lock_a_member_out_of_their_phone_nor_learn_the_name(tmp_path):
    """Review M2, the probe P3 the other way round: a member's name and a name nobody has get the
    same answers, 429 on the fifth alike; and the member's own phone, signed out, still signs in
    with the right password and code while the name is locked for every other phone."""

    async def body(world: World):
        _, secret, phone = await world.member()
        assert contract.PHONE_COOKIE in phone.jar
        stranger = Phone(world.port, agent="Mozilla/5.0 (Android 14) Firefox/133.0")
        await stranger.call("GET", "/me")
        seen, ghost = [], []
        for _ in range(6):
            wrong = {"password": "wrong wrong wrong", "totp": "000000"}
            seen.append(await stranger.call("POST", "/login", {"handle": "member_a", **wrong}))
            ghost.append(await stranger.call("POST", "/login", {"handle": "nobody_x", **wrong}))
        assert [status for status, _ in seen] == [401] * 4 + [429] * 2
        assert seen == ghost  # the same status and the same sentence, the time included
        await phone.call("POST", "/logout", {})
        assert contract.COOKIE not in phone.jar and contract.PHONE_COOKIE in phone.jar
        world.clock.at += 30_000
        right = {"handle": "member_a", "password": PASSWORD, "totp": world.code(secret)}
        new = Phone(world.port)
        await new.call("GET", "/me")
        assert (await new.call("POST", "/login", right))[0] == 429  # a phone never used: held
        status, answer = await phone.call("POST", "/login", right)
        assert status == 200 and answer["data"]["member"]["handle"] == "member_a"

    scenario(tmp_path, body)


def test_a_viewer_session_reaches_no_admin_op_and_the_owner_unlocks_a_lock(tmp_path):
    """Review L1: signin_unlock is the owner's alone, as every admin op is: a signed-in member's
    cookies open no admin door and no viewer route serves it; the owner's sealed op clears the
    lock, and the password and code app stay."""

    async def body(world: World):
        card, secret, phone = await world.member()
        headers = {"Cookie": phone.cookie_header(), "Content-Type": "application/json",
                   contract.CSRF_HEADER: phone.jar[contract.CSRF_COOKIE]}  # fmt: skip
        raw = json.dumps({"member_id": card["id"]}).encode()
        for more in ({}, {"X-Real-IP": "198.51.100.7"}):
            status, _, answer = await ask(world.port, "POST", "/v1/admin/signin_unlock", body=raw,
                                          headers=headers | more)  # fmt: skip
            assert status in (403, 404) and answer["status"] == "error", (more, status)
        assert (await phone.call("POST", "/signin_unlock", {"member_id": card["id"]}))[0] == 404
        stranger = Phone(world.port)
        await stranger.call("GET", "/me")
        wrong = {"handle": "member_a", "password": "wrong wrong wrong", "totp": "000000"}
        for _ in range(5):
            status, _ = await stranger.call("POST", "/login", wrong)
        assert status == 429
        locked = (await world.owner("member_list"))[0]["locked_until"]
        assert locked is not None
        unlocked = await world.owner("signin_unlock", member_id=card["id"])
        assert unlocked["locked_until"] is None and unlocked["status"] == "active"
        world.clock.at += 30_000
        right = {"handle": "member_a", "password": PASSWORD, "totp": world.code(secret)}
        assert (await stranger.call("POST", "/login", right))[0] == 200  # same password, same app

    scenario(tmp_path, body)


def test_a_paused_member_is_still_paused_after_a_reset_and_a_join(tmp_path):
    """Review M4, the probe P5 the other way round, over the listener and the owner's sealed ops."""

    async def body(world: World):
        card, _, _ = await world.member("member_a")
        paused = await world.owner("member_status", id=card["id"], status="suspended", version=2)
        invite = await world.owner("signin_reset", member_id=card["id"],
                                   version=paused["version"])  # fmt: skip
        phone = Phone(world.port)
        await phone.call("GET", "/me")
        info = (await phone.call("GET", f"/join/{invite['code']}"))[1]["data"]
        world.clock.at += 30_000
        status, answer = await phone.call("POST", "/join", {
            "code": invite["code"], "handle": "member_a", "password": PASSWORD,
            "totp": world.code(info["totp_key"])})  # fmt: skip
        assert (status, answer["code"]) == (401, "signed_out") and "paused" in answer["message"]
        assert contract.COOKIE not in phone.jar
        assert (await world.owner("member_list"))[0]["status"] == "suspended"

    scenario(tmp_path, body)


def test_the_member_row_is_read_on_every_request(tmp_path):
    async def body(world: World):
        card, _, phone = await world.member()
        hidden = {"pnl": False, "qty": False, "funds": False, "orders": False}
        await world.owner("member_update", id=card["id"], version=2, privacy=hidden)
        me = (await phone.call("GET", "/me"))[1]["data"]
        assert me["privacy"] == hidden and me["hidden"] == ["pnl", "qty", "funds", "orders"]
        await world.owner("member_status", id=card["id"], version=3, status="suspended")
        status, answer = await phone.call("GET", "/me")
        assert status == 401 and "paused" in answer["message"]

    scenario(tmp_path, body)


def test_a_password_change_signs_out_the_other_phone(tmp_path):
    async def body(world: World):
        _, secret, first = await world.member()
        second = await world.login("member_a", secret)
        world.clock.at += 30_000
        status, answer = await first.call(
            "POST",
            "/password",
            {"current": PASSWORD, "password": "a newer passphrase", "totp": world.code(secret)},
        )
        assert status == 200 and answer["data"]["words"] == (
            "Your password was changed. Your other devices are signed out."
        )
        assert (await first.call("GET", "/me"))[0] == 200
        assert (await second.call("GET", "/me"))[0] == 401

    scenario(tmp_path, body)


def test_devices_rename_and_sign_out_one(tmp_path):
    async def body(world: World):
        _, secret, first = await world.member()
        second = await world.login(
            "member_a", secret, agent="Mozilla/5.0 (Android 13) Firefox/133.0"
        )
        listed = (await second.call("GET", "/devices"))[1]["data"]
        assert [d["current"] for d in listed] == [True, False]
        other = listed[1]["id"]
        assert (await second.call("PATCH", f"/devices/{other}", {"label": "Tablet"}))[0] == 200
        assert (await first.call("GET", "/devices"))[1]["data"][0]["label"] == "Tablet"
        status, _ = await second.call("DELETE", f"/devices/{other}")
        assert status == 200 and (await first.call("GET", "/me"))[0] == 401
        mine = listed[0]["id"]
        await second.call("DELETE", f"/devices/{mine}")
        assert any("Max-Age=0" in c for c in second.last_cookies)

    scenario(tmp_path, body)


def test_routes_other_lanes_serve_are_not_ready_and_unknown_ones_are_not_found(tmp_path):
    async def body(world: World):
        _, _, phone = await world.member()
        assert (await phone.call("GET", "/books/positions"))[1]["code"] == "not_ready"
        stranger = Phone(world.port)
        assert (await stranger.call("GET", "/books/positions"))[0] == 401  # the session first
        assert (await phone.call("GET", "/nothing/here"))[0] == 404
        assert (await phone.call("PUT", "/me", {}))[0] == 404

    scenario(tmp_path, body)


# ---- the WebSocket: checked at the handshake, closed by a revoke --------------------------------------
async def open_socket(world: World, phone: Phone, origin: str | None = None):
    sock = await connect(f"ws://127.0.0.1:{world.port}/family/ws", origin=origin,
                         additional_headers={"Cookie": phone.cookie_header()})  # fmt: skip
    await sock.send(json.dumps({"action": "authenticate", "family": True}))
    assert json.loads(await sock.recv()) == {"type": "auth", "status": "success"}
    return sock


async def closed_within(sock, seconds: float) -> tuple[int, str, float]:
    began = time.monotonic()
    with pytest.raises(ConnectionClosed):
        async with asyncio.timeout(seconds):
            while True:
                await sock.recv()
    return sock.close_code, sock.close_reason, time.monotonic() - began


def test_the_handshake_needs_a_session_and_this_hosts_page(tmp_path):
    async def body(world: World):
        _, _, phone = await world.member()
        with pytest.raises(InvalidStatus) as caught:
            await connect(f"ws://127.0.0.1:{world.port}/family/ws")
        assert caught.value.response.status_code == 401
        with pytest.raises(InvalidStatus) as caught:
            await connect(f"ws://127.0.0.1:{world.port}/family/ws",
                          origin="https://elsewhere.example",
                          additional_headers={"Cookie": phone.cookie_header()})  # fmt: skip
        assert caught.value.response.status_code == 403
        sock = await open_socket(world, phone, origin=f"http://127.0.0.1:{world.port}")
        await sock.close()

    scenario(tmp_path, body)


@pytest.mark.parametrize("how", ["owner_revoke", "member_logout", "member_removed"])
def test_a_revoke_closes_that_devices_socket_within_5s_with_4401(tmp_path, how):
    async def body(world: World):
        card, secret, phone = await world.member()
        other = await world.login("member_a", secret)
        sock = await open_socket(world, phone)
        kept = await open_socket(world, other)
        mine = (await phone.call("GET", "/me"))[1]["data"]["device"]["id"]
        if how == "owner_revoke":
            await world.owner("device_revoke", member_id=card["id"], id=mine)
        elif how == "member_logout":
            await phone.call("POST", "/logout", {})
        else:
            await world.owner("member_remove", id=card["id"], version=2)
        code, reason, took = await closed_within(sock, 5)
        assert (code, reason) == (4401, "Signed out on this device.") and took < 5
        if how != "member_removed":
            await kept.send(json.dumps({"action": "authenticate"}))
            assert json.loads(await kept.recv())["type"] == "auth"  # the other device stays
        await kept.close()

    scenario(tmp_path, body)


def test_a_session_that_expires_closes_its_socket_within_5s(tmp_path):
    async def body(world: World):
        _, _, phone = await world.member()
        sock = await open_socket(world, phone)
        world.clock.at += 31 * DAY  # past both ends; the sweep sees it within two seconds
        code, reason, took = await closed_within(sock, 5)
        assert (code, reason) == (4401, "Signed out on this device.")
        status, answer = await phone.call("GET", "/me")
        assert (status, answer["message"]) == (401, "Your sign-in has expired. Sign in again.")

    scenario(tmp_path, body)


def test_descriptors_stay_flat_over_a_hundred_sign_ins_and_sockets(tmp_path):
    """Design 7.6: a hundred rounds of a sign-in, a socket opened and closed and a nudge leave the
    process holding what it held after the first round."""
    import os

    def count() -> int:
        return len(os.listdir("/proc/self/fd"))

    async def body(world: World):
        _, secret, _ = await world.member()

        async def one_round():
            phone = await world.login("member_a", secret)
            sock = await open_socket(world, phone)
            await sock.close()
            await ask(world.port, "POST", "/v1/nudge",
                      body=sealed(world.service.keys.nudge, "nudge",
                                  principal="sudarshan-alerts", kind="orders", sandbox=False,
                                  at=1))  # fmt: skip

        await one_round()
        await asyncio.sleep(0.2)
        before = count()
        for _ in range(100):
            await one_round()
        for _ in range(50):
            if count() <= before and not len(world.service.registry):
                break
            await asyncio.sleep(0.05)
        assert count() <= before, (before, count())
        assert len(world.service.registry) == 0
        assert len(world.service.accounts.codes) <= world.service.accounts.codes.size

    scenario(tmp_path, body)
