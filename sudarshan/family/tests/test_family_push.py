"""F10: quiet hours (a window across midnight, a child quiet outside the market session, sign-in
notices and tests never quiet), the member's settings, and Web Push: the key, subscriptions (the
endpoint checked whole, ``replaces``, one per phone), and sends with the design's back-off (10
minutes, then an hour; three failures in a row mark the phone and tell the owner once)."""

from __future__ import annotations

import asyncio
import json

import pytest
from family_botkit import Weekdays, fake_service
from family_planekit import MONDAY, Clock, ist
from family_pushkit import ENDPOINT, FakePushService, subscription, write_vapid
from family_testkit import add_member, close, open_store

from sudarshan.family import push as push_module
from sudarshan.family import quiet, sessions
from sudarshan.family.bot_words import PAGE, Refused
from sudarshan.family.errors import FamilyError, VersionConflict
from sudarshan.family.quiet import MarketHours
from sudarshan.push import Outcome

MARKET = MarketHours(Weekdays())
TUESDAY, SATURDAY = MONDAY.replace(day=17), MONDAY.replace(day=21)


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


def signed(opened, now: int, **values):
    """An active member with a session (a phone), as the viewer API's checks give it."""
    member = add_member(opened, status="active", **values)

    def make(tx):
        device = sessions.Device("Android 15 · Chrome 130", "Android 15 · Chrome 130")
        session_id, _ = sessions.mint(tx, member["id"], device, now)
        return tx.row("viewer_session", id_hash=sessions.id_hash(session_id))

    row = opened.write_now(make)
    return member, sessions.Session(row["id_hash"], member, row)


# ---- quiet hours ----------------------------------------------------------------------------------
def test_a_window_across_midnight_and_its_edges():
    start, end = quiet.clock_minutes("21:00"), quiet.clock_minutes("08:00")
    assert quiet.in_window(21 * 60, start, end) and quiet.in_window(7 * 60 + 59, start, end)
    assert not quiet.in_window(8 * 60, start, end) and not quiet.in_window(20 * 60 + 59, start, end)
    assert quiet.in_window(16 * 60, 15 * 60, 17 * 60) and not quiet.in_window(
        17 * 60, 15 * 60, 17 * 60
    )
    assert not quiet.in_window(12 * 60, 9 * 60, 9 * 60)  # the same time twice is no window


@pytest.mark.parametrize(
    ("at", "held_until"),
    [
        (ist(MONDAY, 16, 5), None),
        (ist(MONDAY, 20, 59, 59), None),
        (ist(MONDAY, 21), ist(TUESDAY, 8)),
        (ist(MONDAY, 23, 30), ist(TUESDAY, 8)),
        (ist(TUESDAY, 7, 59), ist(TUESDAY, 8)),
        (ist(TUESDAY, 8), None),
    ],
)
def test_an_adults_summary_waits_out_the_default_window(at, held_until):
    member = {"role": "viewer", "quiet": {}}
    assert quiet.until(member, "digest", at, MARKET) == held_until


def test_sign_in_notices_and_tests_are_never_quiet():
    """Mutation target 21: a security notice silenced by quiet hours."""
    child = {"role": "child", "quiet": {}}
    adult = {"role": "viewer", "quiet": {"from": "00:00", "to": "23:59", "digest": True,
                                         "alerts": True}}  # fmt: skip
    for member in (child, adult):
        for at in (ist(MONDAY, 23), ist(SATURDAY, 3), ist(MONDAY, 12)):
            assert quiet.until(member, "security", at, MARKET) is None
            assert quiet.until(member, "test", at, MARKET) is None
    assert quiet.until(adult, "digest", ist(MONDAY, 12), MARKET) == ist(MONDAY, 23, 59)


def test_a_child_is_quiet_outside_the_market_session():
    child = {"role": "child", "quiet": {"digest": False}}
    assert quiet.until(child, "digest", ist(MONDAY, 11), MARKET) is None
    assert quiet.until(child, "digest", ist(MONDAY, 16, 5), MARKET) == ist(TUESDAY, 9, 15)
    assert quiet.until(child, "digest", ist(SATURDAY, 11), MARKET) == ist(
        MONDAY.replace(day=23), 9, 15
    )
    assert quiet.until(child, "digest", ist(MONDAY, 9, 14), MARKET) == ist(MONDAY, 9, 15)
    # a window ending inside a closed market waits for the session: Friday 22:00 -> Monday 09:15
    friday = MONDAY.replace(day=20)
    windowed = {"role": "child", "quiet": {}}
    assert quiet.until(windowed, "digest", ist(friday, 22), MARKET) == ist(
        MONDAY.replace(day=23), 9, 15
    )


def test_quiet_off_and_a_custom_window():
    off = {"role": "viewer", "quiet": {"digest": False, "alerts": False}}
    assert quiet.until(off, "digest", ist(MONDAY, 23), MARKET) is None
    custom = {"role": "viewer", "quiet": {"from": "15:00", "to": "17:00", "digest": True}}
    assert quiet.until(custom, "digest", ist(MONDAY, 16, 5), MARKET) == ist(MONDAY, 17)
    garbage = {"role": "viewer", "quiet": {"from": "25:00", "to": 7, "digest": "yes"}}
    assert quiet.quiet_of(garbage) == quiet.DEFAULT_QUIET  # a stored value that is not one


# ---- settings -------------------------------------------------------------------------------------
def test_settings_read_and_change_with_a_version(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 12))
            member, session = signed(opened, clock())
            routes = quiet.SettingsRoutes(fake_service(opened, clock, tmp_path))
            got = (await routes.route_settings(None, {}, session)).payload["data"]
            assert got == {"language": "en", "quiet": quiet.DEFAULT_QUIET,
                           "notify": {"digest_push": True, "digest_telegram": True},
                           "telegram_linked": False, "version": member["version"]}  # fmt: skip
            new = {"from": "22:30", "to": "07:00", "digest": True, "alerts": False}
            await routes.route_settings_set(None, {"version": got["version"], "quiet": new,
                                                   "language": "gu"}, session)  # fmt: skip
            got2 = (await routes.route_settings(None, {}, session)).payload["data"]
            assert got2["quiet"] == new and got2["language"] == "gu"
            assert got2["version"] == got["version"] + 1
            with pytest.raises(VersionConflict):
                await routes.route_settings_set(None, {"version": got["version"],
                                                       "notify": {"digest_push": False,
                                                                  "digest_telegram": True}},
                                                session)  # fmt: skip
            with pytest.raises(Refused) as refused:
                bad = {**new, "from": "25:00"}
                await routes.route_settings_set(None, {"version": got2["version"], "quiet": bad},
                                                session)  # fmt: skip
            assert str(refused.value) == PAGE["quiet_rule"] and refused.value.field == "quiet"
            for wrong in ({"notify": {"digest_push": True}}, {"language": "hi"}, {"quiet": "x"}):
                with pytest.raises(FamilyError) as refused:
                    await routes.route_settings_set(None, {"version": got2["version"], **wrong},
                                                    session)  # fmt: skip
                assert refused.value.code == "invalid"
            lines = await opened.read(lambda tx: tx.rows("audit", kind="member_updated"))
            assert [ln["detail"]["fields"] for ln in lines] == [["language", "quiet"]]
            assert lines[0]["actor"] == f"member:{member['id']}"
        finally:
            close(opened)

    run(go())


# ---- push: the routes -----------------------------------------------------------------------------
def test_the_key_and_a_server_without_one(tmp_path):
    async def go():
        vapid = write_vapid(tmp_path / "etc")
        service = fake_service(None, Clock(1), tmp_path / "etc")
        loaded = push_module.load_vapid(tmp_path / "etc", "")
        assert loaded is not None and loaded.public_key == vapid.public_key
        answer = await push_module.Push(service, loaded).route_push_key(None, {}, None)
        assert answer.payload["data"] == {"key": vapid.public_key}
        assert push_module.load_vapid(tmp_path / "missing", "") is None
        with pytest.raises(Refused) as refused:
            await push_module.Push(service, None).route_push_key(None, {}, None)
        assert refused.value.code == "not_ready" and str(refused.value) == PAGE["push_not_set_up"]

    run(go())


def test_a_subscription_is_checked_whole_before_anything_is_kept(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 12))
            member, session = signed(opened, clock())
            push = push_module.Push(
                fake_service(opened, clock, tmp_path), write_vapid(tmp_path / "e")
            )
            good = subscription(1)
            await push.route_push_subscribe(None, good, session)
            await push.route_push_subscribe(None, good, session)  # the re-sync on every open
            rows = await opened.read(lambda tx: tx.rows("push_subscription"))
            assert len(rows) == 1 and rows[0]["endpoint"] == good["endpoint"]
            assert (
                rows[0]["session_hash"] == session.id_hash and rows[0]["member_id"] == member["id"]
            )
            assert (
                rows[0]["p256dh"] == good["keys"]["p256dh"]
                and rows[0]["auth"] == good["keys"]["auth"]
            )
            lines = await opened.read(lambda tx: tx.rows("audit", kind="push_subscribed"))
            assert len(lines) == 1  # a re-post is not a new subscription
            for endpoint in ("http://fcm.googleapis.com/fcm/send/x", "https://127.0.0.1/x",
                             "https://evil.example/fcm/send/x", "https://fcm.googleapis.com:8443/x",
                             "https://user@fcm.googleapis.com/x", "https://[::1]/x",
                             "https://fcm.googleapis.com.evil.example/x", "", None):  # fmt: skip
                with pytest.raises(Refused) as refused:
                    await push.route_push_subscribe(None, {**good, "endpoint": endpoint}, session)
                assert refused.value.code == "invalid" and refused.value.field == "endpoint"
                assert "http" not in str(refused.value) or "secure (https)" in str(refused.value)
            with pytest.raises(Refused):
                await push.route_push_subscribe(None, {**good, "keys": {"p256dh": "x", "auth": "y"}},
                                                session)  # fmt: skip
            assert len(await opened.read(lambda tx: tx.rows("push_subscription"))) == 1
        finally:
            close(opened)

    run(go())


def test_replaces_one_per_phone_a_moved_phone_and_unsubscribe(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock = Clock(ist(MONDAY, 12))
            a, phone_a = signed(opened, clock())
            b, phone_b = signed(opened, clock(), display_name="Member B")
            push = push_module.Push(
                fake_service(opened, clock, tmp_path), write_vapid(tmp_path / "e")
            )
            first, second, others = subscription(1), subscription(2), subscription(3)
            await push.route_push_subscribe(None, first, phone_a)
            await push.route_push_subscribe(None, others, phone_b)
            # a replaces naming another member's endpoint removes nothing of theirs
            await push.route_push_subscribe(
                None, {**second, "replaces": others["endpoint"]}, phone_a
            )
            endpoints = lambda: opened.read(  # noqa: E731
                lambda tx: sorted(
                    (r["member_id"], r["endpoint"]) for r in tx.rows("push_subscription")
                )
            )
            assert await endpoints() == [
                (a["id"], second["endpoint"]),
                (b["id"], others["endpoint"]),
            ]
            # the same browser now signed in as B: the subscription moves with it
            await push.route_push_subscribe(None, second, phone_b)
            assert await endpoints() == [(b["id"], second["endpoint"])]
            # unsubscribe: one's own only
            await push.route_push_unsubscribe(None, {"endpoint": second["endpoint"]}, phone_a)
            assert await endpoints() == [(b["id"], second["endpoint"])]
            await push.route_push_unsubscribe(None, {"endpoint": second["endpoint"]}, phone_b)
            assert await endpoints() == []
            assert len(await opened.read(lambda tx: tx.rows("audit", kind="push_removed"))) == 1
        finally:
            close(opened)

    run(go())


# ---- push: sending --------------------------------------------------------------------------------
async def _pushing(opened, tmp_path, clock, fake, n: int = 1, **values):
    member, session = signed(opened, clock(), **values)
    service = fake_service(opened, clock, tmp_path)
    push = push_module.Push(service, write_vapid(tmp_path / "e"))
    http = fake.http()
    push.start(http)
    await push.route_push_subscribe(None, subscription(n), session)
    return service, push, http, member, session


def test_a_send_carries_ttl_urgency_and_topic_and_resets_failures(tmp_path):
    """Mutation target 20: Topic or Urgency dropped, or a TTL other than the time left."""

    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake = Clock(ist(MONDAY, 16, 5)), FakePushService()
            service, push, http, member, _ = await _pushing(opened, tmp_path, clock, fake)
            await opened.write(lambda tx: tx.update("push_subscription", {"member_id": member["id"]},
                                                    {"failures": 2}))  # fmt: skip
            done = await push.to_member(member["id"], b'{"v":1}', expiry=clock() + 21_600_000,
                                        urgency="low", topic="digest-20261116")  # fmt: skip
            assert (done.state, done.sent, done.phones) == ("sent", 1, 1)
            sent = fake.headers()[-1]
            assert sent["ttl"] == "21600" and sent["urgency"] == "low"
            assert sent["topic"] == "digest-20261116" and sent["content-encoding"] == "aes128gcm"
            assert sent["authorization"].startswith("vapid t=")
            row = (await opened.read(lambda tx: tx.rows("push_subscription")))[0]
            assert (row["failures"], row["backoff_until"], row["last_ok_at"]) == (0, None, clock())
            clock.at += 3_600_000  # an hour later the time left is five hours
            await push.to_member(member["id"], b"{}", expiry=ist(MONDAY, 16, 5) + 21_600_000,
                                 urgency="high", topic="security-lockout")  # fmt: skip
            assert fake.headers()[-1]["ttl"] == "18000" and fake.headers()[-1]["urgency"] == "high"
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_gone_forgets_and_failures_back_off_then_mark_the_phone_and_tell_the_owner(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake = Clock(ist(MONDAY, 16, 5)), FakePushService()
            service, push, http, member, session = await _pushing(opened, tmp_path, clock, fake)
            endpoint = ENDPOINT.format(n=1)
            expiry = clock() + 86_400_000
            send = lambda: push.to_member(member["id"], b"{}", expiry=expiry, urgency="low",  # noqa: E731
                                          topic=None)  # fmt: skip
            row = lambda: opened.read(lambda tx: tx.row("push_subscription", endpoint=endpoint))  # noqa: E731
            fake.queue(endpoint, 503, 503, 503)
            done = await send()
            assert done.state == "later" and done.retry_at == clock() + push_module.FIRST_BACKOFF_MS
            assert (await row())["failures"] == 1
            assert (await send()).state == "later" and len(fake.seen) == 1  # held back: not tried
            clock.at = done.retry_at
            done = await send()
            assert done.retry_at == clock() + push_module.NEXT_BACKOFF_MS  # then an hour
            devices = await opened.read(lambda tx: sessions.devices(tx, member["id"], clock()))
            assert devices[0]["notifications"] is None and service.notices.seen == []
            clock.at = done.retry_at
            await send()  # the third in a row marks the phone and tells the owner, once
            devices = await opened.read(lambda tx: sessions.devices(tx, member["id"], clock()))
            assert devices[0]["notifications"] == (
                "Notifications may not arrive on this phone. Turn them off and on again in Settings."
            )
            assert service.notices.seen == [("push_failing", {"device": "Android 15 · Chrome 130",
                                                               "attempts": "3"})]  # fmt: skip
            # a success starts the count again and lifts the mark
            clock.at = (await row())["backoff_until"]
            assert (await send()).state == "sent"
            devices = await opened.read(lambda tx: sessions.devices(tx, member["id"], clock()))
            assert devices[0]["notifications"] is None
            # 429 with Retry-After: the push service's own wait
            fake.retry_after = "120"
            fake.queue(endpoint, 429)
            done = await send()
            assert done.retry_at == clock() + 120_000
            # 410: the subscription is forgotten, with an audit line
            clock.at = done.retry_at
            fake.queue(endpoint, 410)
            assert (await send()).state == "refused" and await row() is None
            lines = await opened.read(lambda tx: tx.rows("audit", kind="push_removed"))
            assert lines[-1]["detail"] == {"why": "ended"} and lines[-1]["actor"] == "system"
            assert (await send()).state == "none"
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_a_signed_out_phone_gets_nothing_more_and_a_413_drops_only_the_message(tmp_path):
    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake = Clock(ist(MONDAY, 16, 5)), FakePushService()
            service, push, http, member, session = await _pushing(opened, tmp_path, clock, fake)
            endpoint = ENDPOINT.format(n=1)
            fake.queue(endpoint, 413)
            done = await push.to_member(member["id"], b"{}", expiry=clock() + 60_000, urgency="low",
                                        topic=None)  # fmt: skip
            row = await opened.read(lambda tx: tx.row("push_subscription", endpoint=endpoint))
            assert done.state == "refused" and row["failures"] == 0  # the phone is fine
            await opened.write(lambda tx: sessions.revoke(tx, session.id_hash, clock(), "logout"))
            done = await push.to_member(member["id"], b"{}", expiry=clock() + 60_000, urgency="low",
                                        topic=None)  # fmt: skip
            assert done.state == "none" and len(fake.seen) == 1
            assert await opened.read(lambda tx: tx.rows("push_subscription")) == []
            push.stop()  # the channels' job ended: nothing is sent, the message waits
            assert (await push.to_member(member["id"], b"{}", expiry=1, urgency="low",
                                         topic=None)).state == "none"  # fmt: skip
            await http.aclose()
        finally:
            close(opened)

    run(go())


def test_the_push_client_reaches_only_push_services():
    async def go():
        seen = []
        inner = push_module.httpx.MockTransport(lambda r: (seen.append(str(r.url)),
                                                           push_module.httpx.Response(201))[1])  # fmt: skip
        http = push_module.make_http(inner)
        assert (await http.post(ENDPOINT.format(n=1), content=b"x")).status_code == 201
        for url in ("https://api.telegram.org/bot1:x/getMe", "http://127.0.0.1:5583/v1/state",
                    "https://fcm.googleapis.com:444/x", "http://fcm.googleapis.com/x",
                    "https://evil.example/x"):  # fmt: skip
            with pytest.raises(push_module.HostRefused):
                await http.post(url, content=b"x")
        assert seen == [ENDPOINT.format(n=1)]
        await http.aclose()

    run(go())


def test_nothing_of_a_subscription_reaches_the_log(tmp_path, caplog):
    import logging

    async def go():
        opened = open_store(tmp_path)
        try:
            clock, fake = Clock(ist(MONDAY, 16, 5)), FakePushService()
            service, push, http, member, _ = await _pushing(opened, tmp_path, clock, fake)
            for status in (201, 503, 410):
                fake.queue(ENDPOINT.format(n=1), status)
                await push.to_member(member["id"], b"{}", expiry=clock() + 60_000, urgency="low",
                                     topic=None)  # fmt: skip
                clock.at += push_module.NEXT_BACKOFF_MS
            await http.aclose()
            return json.dumps(push.health())
        finally:
            close(opened)

    with caplog.at_level(logging.DEBUG):
        health = run(go())
    assert "SYNTH" not in caplog.text and "SYNTH" not in health
    assert Outcome.SENT.value in health  # counts by outcome, nothing else
