"""The service against a fake WebSocket proxy and a fake OpenAlgo /notify on 127.0.0.1."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alerts_fakes import (
    API_KEY,
    NEW_API_KEY,
    OWNER,
    FakeOpenAlgo,
    FakeProxy,
    RunningClock,
    ist,
    this_years_option,
    update,
    wait_until,
)

from sudarshan.alerts.outbox import Outbox
from sudarshan.alerts.owner import Owner, OwnerError
from sudarshan.alerts.service import AlertService

REPO_ROOT = Path(__file__).resolve().parents[3]
HEADER = "सुदर्शन / SUDARSHAN"


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class Keys:
    """The owner reader: returns the current key, or raises the problem set."""

    def __init__(self, key: str = API_KEY):
        self.key, self.problem, self.reads = key, None, 0

    def __call__(self) -> Owner:
        self.reads += 1
        if self.problem:
            raise OwnerError(self.problem)
        return Owner(OWNER, self.key)


def _filled_prices(message: str) -> list[str]:
    """The price on each Filled line of a message: lines name no order id, prices do."""
    return [line.split(" at ")[1].split(" ")[0] for line in message.split("\n")
            if line.startswith("Filled: ")]  # fmt: skip


async def _start(settings, keys=None, clock=None) -> tuple[AlertService, asyncio.Task]:
    svc = AlertService(settings, owner_reader=keys or Keys(), clock=clock)
    return svc, asyncio.create_task(svc.run())


async def _stop(svc: AlertService, task: asyncio.Task) -> None:
    svc.stop("test")
    await asyncio.wait_for(task, 10)


def test_fills_reach_telegram_merged_and_once(settings_for, fast, caplog):
    caplog.set_level(logging.DEBUG, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            clock = RunningClock(ist(2026, 10, 21, 10, 0))
            svc, task = await _start(settings_for(proxy, notify), clock=clock)
            await wait_until(lambda: proxy.subscribed)
            assert proxy.keys_seen == [API_KEY] and svc.state == "connected"
            await proxy.push(update("1", "complete", average_price=123.45))
            await wait_until(lambda: len(notify.bodies) == 1)
            body = notify.bodies[0]
            assert (body["apikey"], body["username"], body["wait_for_delivery"]) == (
                API_KEY, OWNER, True,
            )  # fmt: skip
            lines = body["message"].split("\n")
            assert lines[:2] == [
                HEADER,
                "Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.45 (MIS, NFO)",
            ]
            assert lines[2].endswith(" IST")
            # A burst of four fills (a basket) is one message.
            for n in range(4):
                await proxy.push(update(f"b{n}", "complete", average_price=100 + n))
            await wait_until(lambda: len(notify.bodies) == 2)
            await asyncio.sleep(0.3)
            assert len(notify.bodies) == 2 and notify.messages[1].count("\nFilled: ") == 4
            # The same updates again (a webhook and the stream both on, or a replay): silence.
            await proxy.push(update("1", "complete", average_price=123.45))
            await asyncio.sleep(0.4)
            assert len(notify.bodies) == 2
            await _stop(svc, task)
            assert proxy.close_codes == [1000]  # a clean close frame
        assert API_KEY not in caplog.text

    asyncio.run(scenario())


def test_a_restart_and_a_replay_send_nothing_again(settings_for, fast):
    async def scenario():
        day = [update("r1", "complete"), update("r2", "rejected", rejection_reason="No margin")]
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            settings = settings_for(proxy, notify)
            svc, task = await _start(settings)
            await wait_until(lambda: proxy.subscribed)
            for payload in day:
                await proxy.push(payload)
            await wait_until(lambda: len(notify.bodies) == 1)
            await _stop(svc, task)
            # A new process on the same state directory; the proxy replays the day.
            svc, task = await _start(settings)
            await wait_until(lambda: proxy.subscribed)
            for payload in day:
                await proxy.push(payload)
            await proxy.push(update("r3", "complete"))
            await wait_until(lambda: len(notify.bodies) == 2)
            await asyncio.sleep(0.3)
            assert len(notify.bodies) == 2 and "Filled: BUY 65" in notify.messages[1]
            assert "No margin" not in notify.messages[1]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_reconnects_read_the_key_again_and_do_not_resend(settings_for, fast):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            keys = Keys()
            svc, task = await _start(settings_for(proxy, notify), keys)
            await wait_until(lambda: proxy.subscribed)
            await proxy.push(update("c1", "complete", average_price=101.0))
            await wait_until(lambda: len(notify.bodies) == 1)
            reads = keys.reads
            await proxy.drop_all()  # the proxy restarts (a deploy, a crash)
            await wait_until(lambda: proxy.connections == 2 and proxy.subscribed)
            assert keys.reads > reads  # a regenerated key would be picked up here
            await proxy.push(update("c1", "complete", average_price=101.0))  # an old state again
            await proxy.push(update("c2", "complete", average_price=102.0))
            await wait_until(lambda: len(notify.bodies) == 2)
            await asyncio.sleep(0.3)
            assert len(notify.bodies) == 2
            assert _filled_prices(notify.messages[1]) == ["₹102.00"]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_regenerated_key_is_used_after_the_proxy_refuses_the_old_one(settings_for, fast):
    async def scenario():
        async with FakeProxy(key=NEW_API_KEY) as proxy, FakeOpenAlgo(key=NEW_API_KEY) as notify:
            keys = Keys(API_KEY)
            svc, task = await _start(settings_for(proxy, notify), keys)
            await wait_until(lambda: proxy.keys_seen)
            assert proxy.keys_seen[0] == API_KEY and not proxy.subscribed
            keys.key = NEW_API_KEY  # the owner regenerated the key in OpenAlgo
            await wait_until(lambda: proxy.subscribed)
            assert proxy.keys_seen[-1] == NEW_API_KEY
            await proxy.push(update("k1", "complete"))
            await wait_until(lambda: notify.bodies)
            assert notify.bodies[0]["apikey"] == NEW_API_KEY
            await _stop(svc, task)

    asyncio.run(scenario())


def test_no_broker_login_yet_still_subscribes(settings_for, fast):
    """The proxy maps the client before it connects its broker adapter, and says so in an error."""

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            proxy.broker_error = "BROKER_INIT_ERROR"
            svc, task = await _start(settings_for(proxy, notify))
            await wait_until(lambda: proxy.subscribed)
            await proxy.push(update("n1", "complete"))
            await wait_until(lambda: notify.bodies)
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_missing_api_key_sends_nothing_and_is_logged_once(settings_for, fast, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            keys = Keys()
            keys.problem = "no_key"
            svc, task = await _start(settings_for(proxy, notify), keys)
            await wait_until(lambda: keys.reads >= 4)
            assert proxy.connections == 0 and notify.bodies == []
            assert caplog.text.count("No OpenAlgo API key yet") == 1
            keys.problem = None  # the owner generates one: it starts without a restart
            await wait_until(lambda: proxy.subscribed)
            await _stop(svc, task)

    asyncio.run(scenario())


def test_the_kill_switch(settings_for, fast, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            keys = Keys()
            svc, task = await _start(
                settings_for(proxy, notify, SUDARSHAN_ORDER_ALERTS="off"), keys
            )
            await asyncio.wait_for(task, 2)  # it does not wait for anything
            assert proxy.connections == 0 and keys.reads == 0 and svc.state == "off"
        assert "Order alerts are off (SUDARSHAN_ORDER_ALERTS=off)" in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("at", "extra_day"),
    [
        (ist(2026, 10, 21, 8, 44), None),  # Wednesday, before 08:45
        (ist(2026, 10, 21, 16, 0), None),  # the window ends at 16:00
        (ist(2026, 10, 24, 11, 0), None),  # Saturday
        (ist(2026, 11, 9, 11, 0), None),  # a holiday in the file
        (ist(2026, 11, 8, 18, 30), "2026-11-08"),  # Muhurat Sunday, but outside the window
    ],
)
def test_outside_trading_hours_it_does_not_connect(settings_for, fast, tmp_path, at, extra_day):
    async def scenario():
        (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
        if extra_day:
            (tmp_path / "nse_extra_trading_days.txt").write_text(f"{extra_day}  # Muhurat\n")
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            keys = Keys()
            settings = settings_for(proxy, notify, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            svc, task = await _start(settings, keys, Clock(at))
            await asyncio.sleep(0.3)
            assert proxy.connections == 0 and keys.reads == 0 and svc.state == "closed"
            await _stop(svc, task)

    asyncio.run(scenario())


def test_an_extra_trading_day_opens_and_the_window_end_closes(settings_for, fast, tmp_path):
    async def scenario():
        (tmp_path / "nse_extra_trading_days.txt").write_text("2026-11-08  # Muhurat\n")
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            clock = Clock(ist(2026, 11, 8, 15, 59, 58))  # a Sunday listed as a trading day
            settings = settings_for(proxy, notify, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            svc, task = await _start(settings, Keys(), clock)
            await wait_until(lambda: proxy.subscribed)
            clock.at += timedelta(seconds=3)  # 16:00:01
            await wait_until(lambda: proxy.close_codes == [1000])
            await wait_until(lambda: svc.state == "closed")
            await _stop(svc, task)

    asyncio.run(scenario())


def test_telegram_refusals_never_stop_the_stream(settings_for, fast):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            notify.answers = [FakeOpenAlgo.STOPPED]  # the owner stopped OpenAlgo's bot
            svc, task = await _start(settings_for(proxy, notify))
            await wait_until(lambda: proxy.subscribed)
            await proxy.push(update("t1", "complete", average_price=111.0))
            await wait_until(lambda: len(notify.bodies) == 1)
            await asyncio.sleep(0.3)
            await proxy.push(update("t2", "complete", average_price=112.0))
            await wait_until(lambda: len(notify.bodies) == 2)
            # The dropped message is not sent again with the next one.
            assert _filled_prices(notify.messages[1]) == ["₹112.00"]
            assert svc.state == "connected"
            await _stop(svc, task)

    asyncio.run(scenario())


def test_the_entry_point_refuses_bad_settings(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "SUDARSHAN_ALERTS_STATE_DIR": str(tmp_path),
        "SUDARSHAN_ORDER_ALERTS": "maybe",
    }
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.alerts"], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 2
    assert "SUDARSHAN_ORDER_ALERTS must be on or off" in done.stderr
    env |= {"SUDARSHAN_ORDER_ALERTS": "on", "SUDARSHAN_OPENALGO_URL": "http://192.0.2.1:5000"}
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.alerts"], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 2 and "must point at this server" in done.stderr


def test_the_whole_process_end_to_end(tmp_path):
    """``python -m sudarshan.alerts`` as the unit runs it: OpenAlgo's .env and openalgo.db (read
    read-only with OpenAlgo's own functions), the proxy, /notify, and a clean SIGTERM."""
    import os
    import signal

    from alerts_fakes import FakeOpenAlgoDb

    app = tmp_path / "openalgo"
    db = FakeOpenAlgoDb(app / "db")
    db.set_key(API_KEY)  # encrypted under this process's pepper and salt, as OpenAlgo would
    db.close()
    salt = os.environ.get("FERNET_SALT")
    (app / ".env").write_text(
        f"API_KEY_PEPPER = '{os.environ['API_KEY_PEPPER']}'\n"
        + (f"FERNET_SALT = '{salt}'\n" if salt else "")
        + "DATABASE_URL = 'sqlite:///db/openalgo.db'\nBROKER_API_SECRET = 'fake-not-read'\n"  # pragma: allowlist secret
    )

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "TMPDIR": str(tmp_path),
                "SUDARSHAN_OPENALGO_DIR": str(app),
                "SUDARSHAN_ALERTS_STATE_DIR": str(tmp_path / "state"),
                "SUDARSHAN_NSE_HOLIDAYS_FILE": str(tmp_path / "holidays.txt"),
                "SUDARSHAN_NSE_EXTRA_DAYS_FILE": str(tmp_path / "extra.txt"),
                "SUDARSHAN_OPENALGO_WS_URL": proxy.url,
                "SUDARSHAN_OPENALGO_URL": notify.url,
                "SUDARSHAN_ALERTS_ALWAYS_ON": "1",
            }
            log_path = tmp_path / "service.log"
            with open(log_path, "wb") as log_file:
                child = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "sudarshan.alerts", cwd=REPO_ROOT, env=env,
                    stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                )  # fmt: skip
            try:
                await wait_until(lambda: proxy.subscribed, 30)
                assert proxy.keys_seen == [API_KEY]
                # The child reads the real clock, so the option expires this year (no year shown).
                symbol = this_years_option()
                await proxy.push(update("e2e1", "complete", average_price=123.45, symbol=symbol))
                await wait_until(lambda: notify.bodies, 10)
                assert (
                    "\nFilled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.45 (MIS, NFO)\n"
                    in (notify.messages[0])
                )
                child.send_signal(signal.SIGTERM)
                assert await asyncio.wait_for(child.wait(), 20) == 0
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            assert proxy.close_codes == [1000]
        text = log_path.read_text()
        assert "OpenAlgo API key found" in text and "stopping: signal SIGTERM" in text
        assert API_KEY not in text
        assert (tmp_path / "state" / "alerts.db").exists()
        assert sorted(p.name for p in (app / "db").iterdir()) == ["openalgo.db"]  # untouched

    asyncio.run(scenario())


def test_updates_for_another_account_are_ignored(settings_for, fast):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            svc, task = await _start(settings_for(proxy, notify))
            await wait_until(lambda: proxy.subscribed)
            await proxy.push(update("u1", "complete", user_id="someone-else", average_price=1.0))
            await proxy.push(update("u2", "complete", average_price=2.0))
            await wait_until(lambda: notify.bodies)
            await asyncio.sleep(0.3)
            assert [_filled_prices(m) for m in notify.messages] == [["₹2.00"]]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_failed_key_read_on_the_telegram_path_keeps_the_key(settings_for, fast):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            keys = Keys()
            svc, task = await _start(settings_for(proxy, notify), keys)
            await wait_until(lambda: proxy.subscribed)
            keys.problem = "database"
            assert svc._reload_owner() is None and svc.owner.api_key == API_KEY
            keys.problem = "no_key"
            assert svc._reload_owner() is None and svc.owner.api_key == API_KEY
            keys.problem = None
            await proxy.push(update("r9", "complete"))
            await wait_until(lambda: notify.bodies)
            await _stop(svc, task)

    asyncio.run(scenario())


def test_an_unusable_alert_record_does_not_stop_the_alerts(settings_for, fast, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")
    (tmp_path / "state").write_text("a file where the state directory should be")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            svc, task = await _start(settings_for(proxy, notify))
            await wait_until(lambda: proxy.subscribed)
            assert svc.store.in_memory
            await proxy.push(update("m1", "complete", average_price=5.0))
            await proxy.push(update("m1", "complete", average_price=5.0))  # held back in memory
            await wait_until(lambda: notify.bodies)
            await asyncio.sleep(0.3)
            assert [_filled_prices(m) for m in notify.messages] == [["₹5.00"]]
            await _stop(svc, task)
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1 and "Alerts are still sent" in errors[0].getMessage()

    asyncio.run(scenario())


def test_a_muhurat_session_uses_its_own_window(settings_for, fast, tmp_path):
    async def scenario():
        line = "2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)\n"
        (tmp_path / "nse_extra_trading_days.txt").write_text(line)
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            clock = Clock(ist(2026, 11, 8, 10, 0))  # that Sunday's usual hours: closed
            settings = settings_for(proxy, notify, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            svc, task = await _start(settings, Keys(), clock)
            await asyncio.sleep(0.3)
            assert proxy.connections == 0 and svc.state == "closed"
            clock.at = ist(2026, 11, 8, 13, 50)  # 15 minutes before the pre-open
            await wait_until(lambda: proxy.subscribed)
            clock.at = ist(2026, 11, 8, 15, 59)  # the F&O close was at 15:40: still connected
            await asyncio.sleep(0.3)
            assert proxy.close_codes == []
            clock.at = ist(2026, 11, 8, 16, 0)
            await wait_until(lambda: proxy.close_codes == [1000])
            await _stop(svc, task)

    asyncio.run(scenario())


def test_the_key_is_not_sent_to_another_users_port(settings_for, fast, monkeypatch, caplog):
    from sudarshan.alerts import loopback

    caplog.set_level(logging.INFO, logger="sudarshan.alerts")
    real_uid = loopback.os.getuid()
    monkeypatch.setattr(loopback.os, "getuid", lambda: real_uid + 1)

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            keys = Keys()
            svc, task = await _start(settings_for(proxy, notify), keys)
            await wait_until(lambda: keys.reads >= 1)
            await asyncio.sleep(0.3)
            assert proxy.connections == 0 and proxy.keys_seen == []
            await _stop(svc, task)
        assert "held by another user's program" in caplog.text

    asyncio.run(scenario())


def test_a_redirect_is_not_followed(settings_for, fast):
    from websockets.asyncio.server import serve

    async def scenario():
        async with FakeProxy() as target, FakeOpenAlgo() as notify:

            def moved(connection, request):
                response = connection.respond(302, "moved")
                response.headers["Location"] = target.url
                return response

            async with serve(lambda ws: None, "127.0.0.1", 0, process_request=moved) as mover:
                port = mover.sockets[0].getsockname()[1]
                settings = settings_for(
                    notify=notify, SUDARSHAN_OPENALGO_WS_URL=f"ws://127.0.0.1:{port}"
                )
                svc, task = await _start(settings)
                await asyncio.sleep(0.5)
                assert target.connections == 0 and svc.state in ("backoff", "connecting")
                await _stop(svc, task)

    asyncio.run(scenario())


def test_switched_off_it_exits_at_once_whatever_else_is_set(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "SUDARSHAN_ORDER_ALERTS": "off",
        "SUDARSHAN_ORDER_ALERTS_CANCELLED": "bogus",  # would stop it if it were read
        "SUDARSHAN_OPENALGO_URL": "http://192.0.2.1:5000",
    }
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.alerts"], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 0
    assert "Order alerts are off" in done.stderr


@pytest.mark.parametrize(
    "owner, name", [(Outbox, "run"), (AlertService, "_tick")], ids=["Telegram", "clock"]
)
def test_a_stop_ends_even_when_a_task_swallows_its_cancel(
    settings_for, fast, monkeypatch, owner, name
):
    """The Telegram sender may be inside an httpx request when the stop cancels it, where a
    cancel can be lost (sudarshan/tasks.py): the stop used to wait for ever. Here each task
    swallows its first cancel, as anyio's connect_tcp can, and then parks."""
    job = getattr(owner, name)

    async def carries_on(self) -> None:
        try:
            await job(self)
        except asyncio.CancelledError:
            pass
        await asyncio.Event().wait()

    monkeypatch.setattr(owner, name, carries_on)

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            svc, task = await _start(settings_for(proxy, notify))
            await wait_until(lambda: proxy.subscribed)
            began = time.monotonic()
            await _stop(svc, task)
            assert time.monotonic() - began < 5 and svc.state == "stopped"

    asyncio.run(scenario())
