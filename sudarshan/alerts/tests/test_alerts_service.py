"""The service against a fake WebSocket proxy and a fake OpenAlgo /notify on 127.0.0.1."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alerts_fakes import (
    API_KEY,
    NEW_API_KEY,
    OWNER,
    FakeNotify,
    FakeProxy,
    ist,
    update,
    wait_until,
)

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


async def _start(settings, keys=None, clock=None) -> tuple[AlertService, asyncio.Task]:
    svc = AlertService(settings, owner_reader=keys or Keys(), clock=clock)
    return svc, asyncio.create_task(svc.run())


async def _stop(svc: AlertService, task: asyncio.Task) -> None:
    svc.stop("test")
    await asyncio.wait_for(task, 10)


def test_fills_reach_telegram_merged_and_once(settings_for, fast, caplog):
    caplog.set_level(logging.DEBUG, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeNotify() as notify:
            svc, task = await _start(settings_for(proxy, notify))
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
            keys = Keys()
            svc, task = await _start(settings_for(proxy, notify), keys)
            await wait_until(lambda: proxy.subscribed)
            await proxy.push(update("c1", "complete"))
            await wait_until(lambda: len(notify.bodies) == 1)
            reads = keys.reads
            await proxy.drop_all()  # the proxy restarts (a deploy, a crash)
            await wait_until(lambda: proxy.connections == 2 and proxy.subscribed)
            assert keys.reads > reads  # a regenerated key would be picked up here
            await proxy.push(update("c1", "complete"))  # an old state again
            await proxy.push(update("c2", "complete"))
            await wait_until(lambda: len(notify.bodies) == 2)
            await asyncio.sleep(0.3)
            assert len(notify.bodies) == 2 and "c2" not in notify.messages[0]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_regenerated_key_is_used_after_the_proxy_refuses_the_old_one(settings_for, fast):
    async def scenario():
        async with FakeProxy(key=NEW_API_KEY) as proxy, FakeNotify(key=NEW_API_KEY) as notify:
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
            keys = Keys()
            svc, task = await _start(
                settings_for(proxy, notify, SUDARSHAN_ORDER_ALERTS="off"), keys
            )
            await asyncio.sleep(0.4)
            assert proxy.connections == 0 and keys.reads == 0 and svc.state == "off"
            await _stop(svc, task)
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
            notify.answers = [FakeNotify.STOPPED]  # the owner stopped OpenAlgo's bot
            svc, task = await _start(settings_for(proxy, notify))
            await wait_until(lambda: proxy.subscribed)
            await proxy.push(update("t1", "complete"))
            await wait_until(lambda: len(notify.bodies) == 1)
            await asyncio.sleep(0.3)
            await proxy.push(update("t2", "complete"))
            await wait_until(lambda: len(notify.bodies) == 2)
            assert "t1" not in notify.messages[1] and svc.state == "connected"
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
        async with FakeProxy() as proxy, FakeNotify() as notify:
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
                await proxy.push(update("e2e1", "complete", average_price=123.45))
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
        assert API_KEY not in text and "fake-not-read" not in text
        assert (tmp_path / "state" / "alerts.db").exists()
        assert sorted(p.name for p in (app / "db").iterdir()) == ["openalgo.db"]  # untouched

    asyncio.run(scenario())
