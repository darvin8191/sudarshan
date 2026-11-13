"""The journal service (JB2): settings, the switch, the store's seam, /health, a taken port, the
unit file against the deploy's own policy, and descriptors (design 7.1, 7.4, 8.2)."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import logging
import re
import socket
from pathlib import Path

import httpx
import pytest
from journal_fakes import KEYS, FakeFeed, free_port, sealed

from sudarshan.journal import config, migrations, service, store
from sudarshan.journal.config import ConfigError, Settings
from sudarshan.journal.core import kinds
from sudarshan.journal.service import Service
from sudarshan.tests.fdwatch import FdWatch

REPO = Path(__file__).resolve().parents[3]
UNIT = REPO / "sudarshan/ops/systemd/sudarshan-journal.service"


def run(scenario) -> None:
    asyncio.run(scenario())


def settings_for(tmp_path: Path, port: int, enabled: bool = True, **more) -> Settings:
    return Settings(
        enabled=enabled,
        http_port=port,
        state_dir=tmp_path / "state",
        deployed_tag_file=tmp_path / "deployed_tag",
        feed_url="http://127.0.0.1:9",
        **more,
    )


async def until(check, timeout: float = 10.0) -> None:
    for _ in range(int(timeout / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the condition did not come true in time")


async def ask(port: int, op: str, purpose: str = "api", **args) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(
            f"http://127.0.0.1:{port}/v1/{op}", content=sealed(op, purpose, **args), timeout=10
        )


async def health(port: int) -> dict:
    async with httpx.AsyncClient(trust_env=False) as client:
        return (await client.get(f"http://127.0.0.1:{port}/health", timeout=10)).json()


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store.stop()


# ---- settings ---------------------------------------------------------------------------------
def test_the_defaults_are_off_and_loopback(tmp_path):
    s = Settings.from_env({"SUDARSHAN_OPENALGO_DIR": str(tmp_path)})
    assert s.enabled is False and s.http_port == 5586 and s.max_claims == 50
    assert s.feed_url == "http://127.0.0.1:5580" and s.feed_zmq == "tcp://127.0.0.1:5581"
    assert s.portfolio_url == "http://127.0.0.1:5583"
    assert (s.eod_after_min, s.check_after_min, s.brief_before_min) == (5, 5, 30)
    assert s.weekly_at == (6, 18 * 60) and s.monthly_at == (1, 7 * 60)
    assert s.openalgo_db == tmp_path / "db/openalgo.db"
    assert s.latency_db == tmp_path / "db/latency.db"
    assert s.sandbox_db == tmp_path / "db/sandbox.db"
    assert s.risk_db == tmp_path / "db/sudarshan_risk.db"
    assert s.state_dir == Path("/var/lib/sudarshan-journal")
    assert s.ticks_dir == Path("/var/lib/sudarshan-feed/ticks")


def test_the_switch_and_the_paths_come_from_openalgos_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "SUDARSHAN_JOURNAL = 'on'\nAPI_KEY_PEPPER = '" + "k" * 40 + "'\n"
        "DATABASE_URL = 'sqlite:///db/openalgo.db'\nSANDBOX_DATABASE_URL = 'sqlite:////x/sb.db'\n"
    )
    merged = config.merged_env(
        {"SUDARSHAN_OPENALGO_DIR": str(tmp_path), "STATE_DIRECTORY": f"{tmp_path}/s:/other"},
        config.read_openalgo_env(env),
    )
    s = Settings.from_env(merged)
    assert s.enabled is True and s.sandbox_db == Path("/x/sb.db")
    assert s.state_dir == tmp_path / "s"
    assert config.read_pepper(merged) == "k" * 40
    assert "k" * 40 not in repr(s)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SUDARSHAN_JOURNAL", "maybe"),
        ("SUDARSHAN_JOURNAL_HTTP_PORT", "70000"),
        ("SUDARSHAN_JOURNAL_FEED_URL", "http://example.com:5580"),
        ("SUDARSHAN_JOURNAL_FEED_ZMQ", "tcp://10.0.0.1:5581"),
        ("SUDARSHAN_JOURNAL_PORTFOLIO_URL", "https://127.0.0.1:5583"),
        ("SUDARSHAN_JOURNAL_MAX_CLAIMS", "51"),
        ("SUDARSHAN_JOURNAL_BRIEF_BEFORE_MIN", "-5"),
        ("SUDARSHAN_JOURNAL_WEEKLY_AT", "SUNDAY 18:00"),
        ("SUDARSHAN_JOURNAL_MONTHLY_AT", "31 07:00"),
        ("SUDARSHAN_JOURNAL_MONTHLY_AT", "1 07:60"),
    ],
)
def test_a_setting_it_cannot_use_is_named_in_words(name, value):
    with pytest.raises(ConfigError) as caught:
        Settings.from_env({name: value})
    assert name in str(caught.value)


def test_a_missing_pepper_stops_the_service_with_status_2(tmp_path, monkeypatch):
    monkeypatch.setenv("SUDARSHAN_OPENALGO_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.delenv("API_KEY_PEPPER", raising=False)
    assert service.main() == 2


def test_the_purposes_keys_come_from_the_pepper_one_each():
    keys = service.keys_for("p" * 48)
    assert set(keys) == {"api", "delivery", "nudge", "family"}
    assert all(keys[name]._signing_key == KEYS[name]._signing_key for name in keys)  # noqa: SLF001


# ---- the switch -------------------------------------------------------------------------------
def test_switched_off_nothing_is_opened_every_op_answers_off_and_the_claims_go(tmp_path):
    feed = FakeFeed()
    url = feed.start()
    port = free_port()
    opened = []
    svc = Service(
        dataclasses.replace(settings_for(tmp_path, port, enabled=False), feed_url=url),
        KEYS,
        jobs={"never": lambda s: opened.append("job")},
        opener=lambda s: opened.append("store"),
    )

    async def scenario():
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.state == "off")
        answer = await ask(port, "state")
        assert answer.json()["data"]["enabled"] is False
        await until(lambda: any(c[0] == "DELETE" for c in feed.calls))
        state = (await health(port))["journal_state"]
        assert state["enabled"] is False and state["store"] == "closed"
        svc.stop()
        await asyncio.wait_for(task, 10)

    try:
        run(scenario)
    finally:
        feed.stop()
    assert opened == []
    delete = next(c for c in feed.calls if c[0] == "DELETE")
    assert delete[1] == "/claims/sudarshan-journal"


def test_the_release_is_retried_while_the_feed_does_not_answer():
    waits: list = []

    async def wait(seconds):
        waits.append(seconds)

    assert (
        asyncio.run(
            service.release_claims("http://127.0.0.1:9", delays=(0.0, 1.0, 2.0), sleep=wait)
        )
        is False
    )
    assert waits == [1.0, 2.0]


def test_switched_on_the_store_opens_the_ops_answer_and_stop_closes_everything(tmp_path):
    port = free_port()
    ran = []

    async def a_job(svc):
        ran.append("started")
        await asyncio.sleep(3600)

    svc = Service(settings_for(tmp_path, port), KEYS, jobs={"a_job": a_job})
    svc.clock = lambda: 1_794_200_000_000

    async def scenario():
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.state == "running")
        answer = await ask(port, "state")
        data = answer.json()["data"]
        assert data == {
            "enabled": True, "fills_today": 0, "open_trips": 0, "last_reconcile_ms": None,
            "limits": dict(kinds.LIMITS),
        }  # fmt: skip
        doc = await health(port)
        assert doc["service"] == "sudarshan-journal" and doc["state"] == "running"
        assert doc["journal_state"]["store"] == "open"
        assert doc["journal_state"]["jobs"] == {"a_job": "running"}
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(scenario)
    assert ran == ["started"] and svc.store is None
    assert (tmp_path / "state" / store.DB_FILENAME).exists()
    assert store._pool is None  # noqa: SLF001 - the writer thread ended with the store


def test_a_file_from_a_newer_release_keeps_the_journal_off_with_the_owners_words(tmp_path, caplog):
    port = free_port()
    path = tmp_path / "state" / store.DB_FILENAME
    store.JournalStore(path).close()
    store.stop()
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {migrations.LATEST + 1}")
    svc = Service(settings_for(tmp_path, port), KEYS, jobs={})

    async def scenario():
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.state == "off")
        data = (await ask(port, "state")).json()["data"]
        assert data == {"enabled": False, "message": migrations.SchemaTooNew.WORDS}
        state = (await health(port))["journal_state"]
        assert state["reason"] == migrations.SchemaTooNew.WORDS and state["enabled"] is False
        svc.stop()
        await asyncio.wait_for(task, 10)

    with caplog.at_level(logging.ERROR, "sudarshan.journal"):
        run(scenario)
    assert "newer release" in caplog.text


def test_a_taken_port_is_said_in_words_tried_again_and_the_jobs_keep_running(tmp_path, caplog):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    beats: list = []

    async def heartbeat(svc):
        while True:
            beats.append(1)
            await asyncio.sleep(0.02)

    svc = Service(settings_for(tmp_path, port), KEYS, jobs={"beat": heartbeat}, port_retry_s=0.1)

    async def scenario():
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.state == "port_taken")
        count = len(beats)
        await asyncio.sleep(0.2)
        assert len(beats) > count  # the jobs carry on while the port is taken
        held.close()
        await until(lambda: svc.state == "running")
        assert (await health(port))["journal_state"]["state"] == "running"
        svc.stop()
        await asyncio.wait_for(task, 10)

    try:
        with caplog.at_level(logging.INFO, "sudarshan.journal"):
            run(scenario)
    finally:
        held.close()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "is taken" in errors[0].getMessage()
    assert "tries again every minute" in errors[0].getMessage()
    assert "is free now" in caplog.text


def test_the_health_document_carries_nothing_secret(tmp_path):
    svc = Service(settings_for(tmp_path, free_port()), KEYS, jobs={})
    text = str(svc.health())
    assert "p" * 48 not in text and "pepper" not in text.lower()
    assert set(svc.health()["journal_state"]) >= {
        "enabled", "state", "store", "jobs", "fills_today", "open_trips", "last_reconcile_ms",
        "claims",
    }  # fmt: skip


# ---- the unit ---------------------------------------------------------------------------------
def _ops_harness():
    """The existing ops test harness (sudarshan/ops/tests/test_backup_unit.py: policy_says, the
    deploy tool's unit_ok() cut out of the script exactly as it ships), loaded by its path."""
    path = REPO / "sudarshan/ops/tests/test_backup_unit.py"
    spec = importlib.util.spec_from_file_location("sudarshan_ops_unit_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def directives(key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.*?)\s*$")
    lines = UNIT.read_text(encoding="utf-8").splitlines()
    return [m.group(1) for line in lines if (m := pattern.match(line))]


def test_the_deploy_tools_policy_accepts_the_unit():
    harness = _ops_harness()
    if not harness.shutil.which("bash"):
        pytest.skip("needs bash")
    code, output = harness.policy_says(UNIT, UNIT.name)
    assert code == 0, output


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("User=sudarshan", "User=root"),
        ("Group=sudarshan", "Group=root"),
        ("ExecStart=/", "ExecStart=+/"),
        ("NoNewPrivileges=yes", "NoNewPrivileges=no"),
        ("UMask=0077", "UMask=0077\nAmbientCapabilities=CAP_NET_ADMIN"),
    ],
)
def test_the_policy_check_is_real_for_this_unit(tmp_path, old, new):
    harness = _ops_harness()
    if not harness.shutil.which("bash"):
        pytest.skip("needs bash")
    bad = tmp_path / UNIT.name
    assert old in UNIT.read_text(), "the test must change something"
    bad.write_text(UNIT.read_text().replace(old, new))
    assert harness.policy_says(bad, bad.name)[0] != 0


def test_the_unit_is_loopback_only_and_within_the_designs_limits():
    assert directives("User") == ["sudarshan"] and directives("Group") == ["sudarshan"]
    assert directives("NoNewPrivileges") == ["yes"]
    assert directives("ExecStart") == [
        "/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.journal"
    ]
    assert directives("StateDirectory") == ["sudarshan-journal"]
    assert directives("StateDirectoryMode") == ["0700"]
    assert directives("EnvironmentFile") == ["-/etc/sudarshan/journal.env"]
    assert directives("Slice") == ["sudarshan.slice"]
    assert directives("MemoryHigh") == ["192M"] and directives("MemoryMax") == ["256M"]
    assert directives("CPUWeight") == ["40"]
    assert directives("IPAddressDeny") == ["any"]  # Y4.3: no internet at all
    assert directives("IPAddressAllow") == ["localhost"]
    assert directives("ProtectSystem") == ["strict"] and directives("ReadWritePaths") == []
    assert directives("Restart") == ["on-failure"]
    assert directives("RestartPreventExitStatus") == ["2"]
    assert directives("TimeoutStopSec") == ["20"]
    assert directives("WantedBy") == ["multi-user.target"]
    assert re.fullmatch(r"sudarshan-[a-z0-9-]+\.service", UNIT.name)


# ---- descriptors ------------------------------------------------------------------------------
def test_starting_and_stopping_leaks_no_descriptors(tmp_path):
    def once(n: int) -> None:
        port = free_port()
        svc = Service(settings_for(tmp_path / str(n), port), KEYS, jobs={})

        async def scenario():
            task = asyncio.create_task(svc.run())
            await until(lambda: svc.state == "running")
            await ask(port, "state")
            await health(port)
            svc.stop()
            await asyncio.wait_for(task, 10)

        run(scenario)

    once(0)  # warm: imports, the store thread, the first database
    watch = FdWatch()
    for n in range(1, 6):
        once(n)
    watch.assert_clean()
