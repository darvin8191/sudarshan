"""The screener's digests through sudarshan-alerts (SV10, screener design 4.4): the seal check
under the screener-digest purpose, the once-only claim, the Telegram line composed here and sent
through the market lane, the toast to the open apps, the delivery rows in History, and both ends
together with the payload the screener's digests.py builds."""

from __future__ import annotations

import asyncio

import pytest
from alerts_fakes import FakeOpenAlgo, ist
from market_fakes import FakeFeed, ms
from test_market_api import Desk, post, run, sealed, served
from test_market_desk import closed, opened
from test_market_runtime import Clock

from sudarshan.alerts.market import api as api_module
from sudarshan.alerts.market import words
from sudarshan.alerts.market.api import ApiError, MarketApi
from sudarshan.alerts.market.store import MarketStore
from sudarshan.screener import alerting as al
from sudarshan.screener import digests
from sudarshan.vault import ALERTS_API_INFO, ALERTS_SCREENER_DIGEST_INFO, fernet_for

PEPPER = "p" * 48  # fake, test only
API_FERNET = fernet_for(PEPPER, ALERTS_API_INFO)
DIGEST_FERNET = fernet_for(PEPPER, ALERTS_SCREENER_DIGEST_INFO)
AT = ms(ist(2026, 10, 21, 10, 45))
CANDLE = (ms(ist(2026, 10, 21, 10, 30)), AT)


def digest_payload(**over) -> dict:
    decision = al.Decision(
        True, al.TOLD,
        entered=(al.Named("NSE_EQ|INE002A01018", "RELIANCE", "NSE"), al.Named("NSE_EQ|INE062A01020", "SBIN", "NSE"),
                 al.Named("NSE_EQ|INE467B01029", "TCS", "NSE")),
        matches=7,
    )  # fmt: skip
    payload = digests.build(
        screen_id=4, screen_name="15-min EMA cross", run_key="4:v2:20261021:15:6", at=AT, session=20261021,
        decision=decision, candle=CANDLE,
        prices={"NSE_EQ|INE002A01018": (142825, 141131), "NSE_EQ|INE062A01020": (81255, 80610),
                "NSE_EQ|INE467B01029": (391200, 389642)},
    )  # fmt: skip
    payload.update(over)
    return payload


# ---- the words -------------------------------------------------------------------------------
def test_the_telegram_line_is_the_designs(quick):
    line = words.screener_line(digest_payload(), AT)
    assert line == (
        "Screener: 15-min EMA cross, 10:30 to 10:45 candle: 3 entered: RELIANCE ₹1,428.25 (+1.20%), "
        "SBIN ₹812.55 (+0.80%), TCS ₹3,912.00 (+0.40%); 7 match now"
    )
    many = digest_payload(entered=[{"key": f"NSE_EQ|{n}", "symbol": f"S{n}", "exchange": "NSE", "ltp": 10000, "change": None} for n in range(5)],
                          more_entered=3, exited=[{"key": "NSE_EQ|X", "symbol": "XYZ", "exchange": "NSE", "ltp": None, "change": None}])  # fmt: skip
    line = words.screener_line(many, AT)
    assert (
        "8 entered: S0 ₹100.00, S1 ₹100.00, S2 ₹100.00, S3 ₹100.00, S4 ₹100.00 and 3 more (the Screener page has them)"
        in line
    )
    assert "; 1 left: XYZ; 7 match now" in line
    index = digest_payload(screen="NIFTY long build-up" + "x" * 40, entered=[
        {"key": "NSE_INDEX|Nifty 50", "symbol": "NIFTY", "exchange": "NSE_INDEX", "ltp": 2591240, "change": "0.35"}])  # fmt: skip
    line = words.screener_line(index, AT)
    name = line.split(", 10:30")[0]
    assert name.endswith("...") and len(name) == len("Screener: ") + 40, "names are cut at 40"
    assert "NIFTY 25,912.40 (+0.35%)" in line, "an index in points, no rupee sign"
    assert words.screener_line(digest_payload(entered=[], candle=None), AT).endswith(
        "10:45 check: nothing new; 7 match now"
    )
    assert words.screener_detail(digest_payload()) == "3 entered, 7 match now"
    assert words.screener_note(digest_payload()) == ""
    assert words.screener_note(digest_payload(late_at=AT + 120_000, pause=True)) == (
        "checked late at 10:47 after a restart; alerts paused after this one; resume them on the Screener page"
    )
    for text in (line, words.screener_detail(digest_payload())):
        assert "*" not in text and "_" not in text.replace("build-up", "")


# ---- the store ---------------------------------------------------------------------------------
def test_a_digest_is_recorded_once_under_its_claim_with_a_delivery_row_per_channel(tmp_path, quick):
    store = MarketStore(tmp_path / "market.db")
    try:
        first = store.record_screener("owner", "scr:4:4:v2:20261021:15:6", title="Screener: EMA", line="l",
                                      detail="d", note="", now=AT, channels={"telegram": True, "app": False})  # fmt: skip
        assert first is not None
        again = store.record_screener("owner", "scr:4:4:v2:20261021:15:6", title="x", line="y", detail="",
                                      note="", now=AT + 1, channels={"telegram": True})  # fmt: skip
        assert again is None, "a retried post is already told"
        event = store.event_by_claim("owner", "scr:4:4:v2:20261021:15:6")
        assert event["id"] == first and event["kind"] == "screener" and event["alert_id"] == 0
        assert event["deliveries"]["telegram"]["status"] == "pending"
        assert event["deliveries"]["app"]["status"] == "off"
    finally:
        store.close()


# ---- the seal ----------------------------------------------------------------------------------
def test_the_digest_op_opens_only_under_the_screener_digest_purpose(quick):
    async def scenario():
        desk = Desk(screener_digest={"claimed": True, "event_id": 9})
        server, base = await served(desk, digest_fernet=DIGEST_FERNET)
        try:
            good = await post(
                base,
                "screener_digest",
                sealed("screener_digest", DIGEST_FERNET, args={"screen_id": 4}),
            )
            assert good.status_code == 200 and good.json()["data"] == {
                "claimed": True,
                "event_id": 9,
            }
            assert desk.calls[-1][0] == "screener_digest"
            wrong = await post(
                base, "screener_digest", sealed("screener_digest", API_FERNET, args={})
            )
            assert wrong.status_code == 403, "the bridge's seal never opens the digest op (X9)"
            other = await post(base, "state", sealed("state", DIGEST_FERNET))
            assert other.status_code == 403, "the digest's seal never opens the bridge's ops (X9)"
            assert len(desk.calls) == 1
        finally:
            await server.close()
        server, base = await served(Desk())  # a service without the digest key refuses the op
        try:
            none = await post(
                base, "screener_digest", sealed("screener_digest", DIGEST_FERNET, args={})
            )
            assert none.status_code == 403
        finally:
            await server.close()

    run(scenario)
    assert api_module.DIGEST_OP in api_module.OPS and isinstance(MarketApi, type)


# ---- both ends together -------------------------------------------------------------------------
def test_a_digest_from_the_screener_reaches_telegram_the_apps_and_history_once(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 45))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            office, rt, task = await opened(settings, feed, clock)
            try:
                server = MarketApi(office, API_FERNET, digest_fernet=DIGEST_FERNET)
                await server.start(0)
                base = f"http://127.0.0.1:{server.server.sockets[0].getsockname()[1]}"
                blob = digests.sealed(
                    DIGEST_FERNET, digest_payload()
                )  # exactly what the screener sends
                answer = await post(base, digests.OP, blob)
                assert answer.status_code == 200, answer.text
                data = answer.json()["data"]
                assert data["claimed"] is True and data["deliveries"]["telegram"] is True
                event_id = data["event_id"]
                for _ in range(100):
                    if notify.messages:
                        break
                    await asyncio.sleep(0.05)
                assert notify.messages, "the market lane sent the line"
                lines = notify.messages[0].split("\n")
                assert lines[1] == words.screener_line(digest_payload(), AT)
                assert lines[-1].endswith("IST")
                # the same run again: already told, nothing sent twice
                again = await post(
                    base, digests.OP, digests.sealed(DIGEST_FERNET, digest_payload())
                )
                assert again.json()["data"] == {
                    "claimed": False,
                    "event_id": event_id,
                    "deliveries": again.json()["data"]["deliveries"],
                }
                await asyncio.sleep(0.2)
                assert len(notify.messages) == 1
                history = await office.call("events", {})
                event = next(e for e in history["events"] if e["id"] == event_id)
                assert (
                    event["kind"] == "screener" and event["title"] == "Screener: 15-min EMA cross"
                )
                assert event["detail"] == "3 entered, 7 match now" and event["alert_id"] == 0
                for _ in range(100):
                    if event["deliveries"]["telegram"]["status"] == "sent":
                        break
                    await asyncio.sleep(0.05)
                    history = await office.call("events", {})
                    event = next(e for e in history["events"] if e["id"] == event_id)
                assert event["deliveries"]["telegram"]["status"] == "sent"
                # a damaged digest is refused in words, not recorded
                bad = await post(
                    base, digests.OP, digests.sealed(DIGEST_FERNET, {"screen_id": "x"})
                )
                assert bad.status_code == 400 and "screen or run" in bad.json()["message"]
                with pytest.raises(ApiError):
                    await office.call("screener_digest", {"args": "junk"})
                await server.close()
            finally:
                await closed(rt, task)

    run(scenario)
