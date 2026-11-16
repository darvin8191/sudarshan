"""T5: the sudarshan-signals service: the switch, the set-up, the letterbox cycle end to end, the
hand-on seam, /health, a taken port, the unit against the deploy's own policy, and descriptors."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import socket
import sqlite3
import threading
from pathlib import Path

import pytest
from signals_fakes import (
    AT,
    KEYS,
    PUBLISHER,
    Box,
    ask,
    document,
    free_port,
    health,
    reading,
    settings_for,
    until,
)

from sudarshan.signals import config, contract, diff, migrations, service
from sudarshan.signals.service import Service
from sudarshan.tests.fdwatch import FdWatch

REPO = Path(__file__).resolve().parents[3]
UNIT = REPO / "sudarshan/ops/systemd/sudarshan-signals.service"


def make(tmp_path, port, *, enabled=True, hooks=None, now=None, **more) -> tuple[Service, Box]:
    box = Box(tmp_path / "in")
    svc = Service(settings_for(tmp_path, port, enabled=enabled, **more), KEYS,
                  hooks={} if hooks is None else hooks, jobs={}, letterbox=box.reader,
                  scan_s=0.02, setup_every_s=0.05)  # fmt: skip
    if now is not None:
        svc.clock = now
    return svc, box


def run(scenario) -> None:
    asyncio.run(scenario())


async def started(svc: Service) -> asyncio.Task:
    task = asyncio.create_task(svc.run())
    await until(lambda: svc.state in ("running", "off"))
    return task


async def stopped(svc: Service, task: asyncio.Task) -> None:
    svc.stop()
    await asyncio.wait_for(task, 10)


def test_switched_off_it_reads_nothing_and_says_so(tmp_path):
    port = free_port()
    svc, box = make(tmp_path, port, enabled=False)
    box.drop(document())

    async def scenario():
        task = await started(svc)
        got = await health(port)
        assert got["signals"] == "off" and got["fresh"] is None and got["letterbox"] == "unknown"
        answer = (await ask(port, "state")).json()
        assert answer == {"status": "success",
                          "data": {"enabled": False, "message": contract.WORDS["off"]}}  # fmt: skip
        await stopped(svc, task)

    run(scenario)
    assert not (tmp_path / "state").exists() and len(box.names()) == 1


def test_not_set_up_until_the_set_up_file_appears(tmp_path):
    port = free_port()
    svc, box = make(tmp_path, port, unset=True)

    async def scenario():
        task = await started(svc)
        assert (await health(port))["signals"] == "not_set_up"
        data = (await ask(port, "state")).json()["data"]
        assert data == {"enabled": False, "message": contract.WORDS["not_set_up"]}
        (tmp_path / "signals.env").write_text(f"SUDARSHAN_SIGNALS_PUBLISHER_UID={PUBLISHER}\n")
        await until(lambda: svc.switch_state == "on")
        assert (await health(port))["letterbox"] == "ok"
        await stopped(svc, task)

    run(scenario)


def test_a_letterbox_not_as_the_set_up_leaves_it(tmp_path):
    port = free_port()
    svc, box = make(tmp_path, port)

    async def scenario():
        task = await started(svc)
        await until(lambda: svc.letterbox_state == "ok")
        box.folder.chmod(0o777)
        await until(lambda: svc.letterbox_state == "wrong")
        data = (await ask(port, "state")).json()["data"]
        assert data["message"] == contract.WORDS["letterbox_wrong"]
        assert (await health(port))["signals"] == "not_set_up"
        box.folder.chmod(0o1733)
        await until(lambda: svc.switch_state == "on")
        await stopped(svc, task)

    run(scenario)


def test_a_document_is_read_stored_removed_and_handed_on(tmp_path):
    port = free_port()
    batches = []

    async def hook(svc, batch):
        batches.append(batch)

    svc, box = make(tmp_path, port, hooks={"spy": hook}, now=lambda: AT + 1000)
    alert = {"id": "a-1", "module": "regime", "instrument": "NIFTY", "level": "act",
             "title": "Invented", "text": "Invented text.", "lang": "en"}  # fmt: skip
    box.drop(document(alerts=[alert]), written_ms=AT)

    async def scenario():
        task = await started(svc)
        await until(lambda: len(batches) == 1)
        assert box.names() == set()
        (batch,) = batches
        assert [(ch["kind"], ch["relay"]) for ch in batch.changes] == [("first", "pending")]
        assert [(a["id"], a["relay"]) for a in batch.alerts] == [("a-1", "pending")]
        assert batch.settings["lang"] == "hi"
        got = await health(port)
        assert got["signals"] == "on" and got["fresh"] is True
        assert got["heard_ms"] == AT + 1000 and got["next_by"] == AT + 300_000
        assert set(got) == {f.name for f in contract.record("Health")}
        await stopped(svc, task)

    run(scenario)


def test_repeats_older_numbers_and_gaps(tmp_path):
    port = free_port()
    batches = []

    async def hook(svc, batch):
        batches.append(batch)

    svc, box = make(tmp_path, port, hooks={"spy": hook}, now=lambda: AT + 1000)

    async def scenario():
        task = await started(svc)
        box.drop(document(seq=5))
        await until(lambda: len(batches) == 1)
        box.drop(document(seq=5, signals=[reading(state="RANGE")]), name=f"{AT}-5.json")
        box.drop(document(seq=4, signals=[reading(state="RANGE")]))
        await until(lambda: box.names() == set())
        assert len(batches) == 1, "a repeat or an older number makes no change"
        box.drop(document(seq=8, at=AT + 10, signals=[reading(state="RANGE")]))
        await until(lambda: len(batches) == 2)
        assert batches[1].changes[0]["kind"] == "state"
        await until(lambda: svc.today.get("gaps") == 2)
        assert (await health(port))["gaps_today"] == 2
        assert svc.today["repeat"] == 1 and svc.today["older"] == 1
        # a new run starts its own numbering
        box.drop(document(seq=1, at=AT + 20, run="r2", signals=[reading(state="UP", bias="bull")]))
        await until(lambda: len(batches) == 3)
        await stopped(svc, task)

    run(scenario)


def test_refused_newer_and_strangers_files(tmp_path):
    port = free_port()
    svc, box = make(tmp_path, port, now=lambda: AT + 1000)

    async def scenario():
        task = await started(svc)
        box.drop(document(seq=1, signals=[reading(label="L" * 41)]))
        box.drop(document(seq=2, v=2), name=f"{AT}-2.json")
        box.drop(document(seq=3), stranger=True)
        await until(lambda: svc.today.get("refused") == 1 and svc.today.get("strangers") == 1)
        await until(lambda: box.names() == {f"{AT}-2.json"})  # the newer format waits
        assert svc.current == {} and svc.today["newer_format"] == 1
        got = await health(port)
        assert got["refused_today"] == 1 and got["fresh"] is False
        await asyncio.sleep(0.2)
        assert svc.today["newer_format"] == 1, "a kept file is not read again"
        await stopped(svc, task)

    run(scenario)


def test_a_fault_on_one_document_does_not_hold_up_the_rest(tmp_path, monkeypatch):
    port = free_port()
    svc, box = make(tmp_path, port, now=lambda: AT + 1000)
    real = diff.apply

    def apply(prior, snap, now):
        if snap.seq == 1:
            raise RuntimeError("a fault of ours")
        return real(prior, snap, now)

    monkeypatch.setattr(service.diff, "apply", apply)
    box.drop(document(seq=1))
    box.drop(document(seq=2, at=AT + 5))

    async def scenario():
        task = await started(svc)
        await until(lambda: box.names() == set() and svc.last is not None)
        assert svc.last["seq"] == 2 and svc.today["refused"] == 1
        await stopped(svc, task)

    run(scenario)


def test_a_restart_between_stored_and_removed_makes_no_second_change(tmp_path, monkeypatch):
    """Design 10.6 "once only": the file is read again after the restart, as a repeat."""
    port = free_port()
    batches = []

    async def hook(svc, batch):
        batches.append(batch)

    first, box = make(tmp_path, port, hooks={"spy": hook}, now=lambda: AT + 1000)
    monkeypatch.setattr(service.inbox.Letterbox, "remove", lambda self, found: False)
    box.drop(document())

    async def once(svc):
        task = await started(svc)
        await until(lambda: svc.last is not None)
        await asyncio.sleep(0.1)
        await stopped(svc, task)

    run(lambda: once(first))
    assert len(batches) == 1 and box.names() == {f"{AT}-1.json"}
    repeats = first.today.get("repeat", 0)  # the first run read it again too: still no change
    monkeypatch.undo()
    second = Service(settings_for(tmp_path, port), KEYS, hooks={"spy": hook}, jobs={},
                     letterbox=box.reader, scan_s=0.02, setup_every_s=0.05)  # fmt: skip
    second.clock = lambda: AT + 2000

    async def again():
        task = await started(second)
        await until(lambda: box.names() == set())
        await stopped(second, task)

    run(again)
    assert len(batches) == 1 and second.today["repeat"] == repeats + 1


def test_a_failing_hook_is_logged_and_the_service_carries_on(tmp_path, caplog):
    port = free_port()

    async def broken(svc, batch):
        raise RuntimeError("hook fault")

    svc, box = make(tmp_path, port, hooks={"broken": broken}, now=lambda: AT + 1000)

    async def scenario():
        task = await started(svc)
        box.drop(document(seq=1))
        await until(lambda: svc.last is not None and svc.last["seq"] == 1)
        box.drop(document(seq=2, at=AT + 5))
        await until(lambda: svc.last["seq"] == 2)
        await stopped(svc, task)

    run(scenario)
    assert "hand-on hook broken failed" in caplog.text


def test_a_store_from_a_newer_release_keeps_the_view_off(tmp_path):
    port = free_port()
    (tmp_path / "state").mkdir()
    with sqlite3.connect(tmp_path / "state" / "signals.db") as conn:
        conn.execute(f"PRAGMA user_version = {migrations.LATEST + 1}")
    svc, _box = make(tmp_path, port)

    async def scenario():
        task = await started(svc)
        data = (await ask(port, "state")).json()["data"]
        assert data == {"enabled": False, "message": contract.WORDS["store_newer"]}
        assert (await health(port))["fresh"] is None
        await stopped(svc, task)

    run(scenario)


def test_a_taken_port_is_said_and_tried_again(tmp_path, caplog):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    port = held.getsockname()[1]
    svc, box = make(tmp_path, port, now=lambda: AT + 1000)
    svc._retry_s = 0.05

    async def scenario():
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.state == "port_taken")
        box.drop(document())
        await until(lambda: svc.last is not None)  # the letterbox is read all the same
        held.close()
        await until(lambda: svc.state == "running")
        assert (await health(port))["signals"] == "on"
        await stopped(svc, task)

    try:
        run(scenario)
    finally:
        held.close()
    assert f"port {port} on this server is taken" in caplog.text


def test_settings_from_the_environment():
    got = config.Settings.from_env(
        {"SUDARSHAN_SIGNALS": "on", "SUDARSHAN_SIGNALS_HTTP_PORT": "6000"}
    )
    assert got.enabled and got.http_port == 6000 and got.letterbox == Path(contract.LETTERBOX)
    assert not config.Settings.from_env({}).enabled
    for bad in ({"SUDARSHAN_SIGNALS": "maybe"}, {"SUDARSHAN_SIGNALS_HTTP_PORT": "x"},
                {"SUDARSHAN_SIGNALS_ALERTS_URL": "http://example.org:5582"}):  # fmt: skip
        with pytest.raises(config.ConfigError):
            config.Settings.from_env(bad)


def test_no_pepper_is_exit_status_2(monkeypatch, tmp_path):
    monkeypatch.setenv("SUDARSHAN_OPENALGO_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("API_KEY_PEPPER", "short")
    assert service.main() == 2


# ---- the unit ---------------------------------------------------------------------------------
def _ops_harness():
    path = REPO / "sudarshan/ops/tests/test_backup_unit.py"
    spec = importlib.util.spec_from_file_location("sudarshan_ops_unit_harness_signals", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def directives(key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.*?)\s*$")
    return [m.group(1) for line in UNIT.read_text().splitlines() if (m := pattern.match(line))]


def test_the_deploy_tools_policy_accepts_the_unit():
    harness = _ops_harness()
    if not harness.shutil.which("bash"):
        pytest.skip("needs bash")
    code, output = harness.policy_says(UNIT, UNIT.name)
    assert code == 0, output


@pytest.mark.parametrize(("old", "new"), [("User=sudarshan", "User=root"),
                                          ("ExecStart=/", "ExecStart=+/"),
                                          ("NoNewPrivileges=yes", "NoNewPrivileges=no")])  # fmt: skip
def test_the_policy_check_is_real_for_this_unit(tmp_path, old, new):
    harness = _ops_harness()
    if not harness.shutil.which("bash"):
        pytest.skip("needs bash")
    bad = tmp_path / UNIT.name
    bad.write_text(UNIT.read_text().replace(old, new))
    assert harness.policy_says(bad, bad.name)[0] != 0


def test_the_unit_is_the_designs():
    assert directives("User") == ["sudarshan"] and directives("Slice") == ["sudarshan.slice"]
    assert directives("ExecStart") == [
        "/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.signals"
    ]
    assert directives("MemoryHigh") == ["96M"] and directives("MemoryMax") == ["128M"]
    assert directives("CPUWeight") == ["40"] and directives("NoNewPrivileges") == ["yes"]
    assert directives("StateDirectory") == ["sudarshan-signals"]
    assert directives("StateDirectoryMode") == ["0750"] and directives("UMask") == ["0027"]
    assert directives("ReadWritePaths") == ["-/var/spool/sudarshan-signals/in"]
    assert directives("ReadOnlyPaths") == ["-/etc/sudarshan"]
    assert directives("Restart") == ["on-failure"] and directives("RestartPreventExitStatus") == [
        "2"
    ]
    assert directives("IPAddressDeny") == ["any"] and directives("IPAddressAllow") == ["localhost"]
    assert directives("EnvironmentFile") == []


# ---- descriptors and threads ------------------------------------------------------------------
def test_starting_and_stopping_leaks_nothing(tmp_path):
    def once(n: int) -> None:
        port = free_port()
        svc, box = make(tmp_path / str(n), port, now=lambda: AT + 1000)
        box.drop(document())

        async def scenario():
            task = await started(svc)
            await until(lambda: svc.last is not None)
            await ask(port, "state")
            await health(port)
            await stopped(svc, task)

        run(scenario)

    once(0)
    watch = FdWatch()
    for n in range(1, 5):
        once(n)
    watch.assert_clean()
    assert not [t for t in threading.enumerate() if t.name.startswith("signals-store")]
