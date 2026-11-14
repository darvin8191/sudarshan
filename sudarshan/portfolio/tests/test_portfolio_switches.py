"""The contract of phase P7 (M7.27, P0): its ops, job and three switches.

Every op of the phase is registered at once and answers "not ready" until its lane lands; once it
lands it still answers "not ready" while its switch is off (design p7 2.4), because the gate is put
on where an op is registered, not left to each module. The owner's ``state`` tells the page which
switches are on; a family member is told nothing of them.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from test_portfolio_import_ops import Rig
from test_portfolio_service import settings_for

from sudarshan.portfolio import config, timers
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.errors import ApiError
from sudarshan.portfolio.ops import REGISTRY, op, switched

PHASE_OPS = (
    "tax_get", "tax_inputs", "tax_input_set", "tax_input_delete", "tax_csv", "report_csv",
    "ca_list", "ca_decide", "dividends", "dividend_set", "calendar", "risk", "settings_get",
    "settings_set", "goals_list", "goal_create", "goal_update", "export_pdf",
)  # fmt: skip


class Ctx:
    def __init__(self, **on: bool):
        self.settings = dataclasses.replace(config.Settings(enabled=True), **on)


def run(coro):
    return asyncio.run(coro)


# ---- the contract ------------------------------------------------------------------------------
def test_every_op_of_the_phase_is_routed_and_waits_for_one_switch():
    assert set(PHASE_OPS) <= set(kinds.OP_NAMES)
    assert set(kinds.SWITCH_OF) == {*PHASE_OPS, "tax_pack"}
    assert set(kinds.SWITCHED_OPS) == set(kinds.SWITCH_NAMES) == set(kinds.SWITCH_ENV)
    names = [name for ops in kinds.SWITCHED_OPS.values() for name in ops]
    assert len(names) == len(set(names)), "an op waits for two switches"
    assert set(names) <= set(kinds.SERVICE_OP_NAMES)


def test_the_journal_asks_for_the_pack_and_for_nothing_it_could_write():
    assert "tax_pack" in kinds.JOURNAL_OPS and "tax_pack" not in kinds.OP_NAMES
    assert kinds.SWITCH_OF["tax_pack"] == "tax"
    assert not set(kinds.JOURNAL_OPS) & {spec.name for spec in kinds.OPS if spec.cls != "read"}


def test_the_switches_are_the_designs():
    assert kinds.SWITCH_ENV == {
        "tax": "SUDARSHAN_TAX",
        "insights": "SUDARSHAN_PORTFOLIO_INSIGHTS",
        "goals": "SUDARSHAN_PORTFOLIO_GOALS",
    }
    assert set(kinds.SWITCHED_OPS["tax"]) >= {"tax_get", "tax_inputs", "tax_csv", "tax_pack"}
    assert set(kinds.SWITCHED_OPS["insights"]) >= {"ca_list", "dividends", "calendar", "risk"}
    assert set(kinds.SWITCHED_OPS["goals"]) >= {"goals_list", "export_pdf"}
    assert kinds.SWITCHED_JOBS == {"insights": ("corporate_actions",)}
    assert set(kinds.SWITCHED_JOBS["insights"]) <= set(kinds.JOB_NAMES)


def test_a_tax_input_has_a_dry_run_and_downloads_are_reads():
    assert "tax_input_set" in kinds.DRY_RUN_OPS
    for name in ("tax_csv", "report_csv", "export_pdf", "export_csv"):
        spec = next(s for s in kinds.OPS if s.name == name)
        assert spec.download and spec.cls == "read" and spec.method == "GET", name


#: The ops of the phase whose lane has landed; every other still answers "not ready" whatever
#: its switch says. One line per pull request, so that lanes landing side by side merge cleanly.
BUILT = {
    "risk", "settings_get", "settings_set",  # R4 (M7.32)
}  # fmt: skip


def test_an_op_of_the_phase_not_built_yet_answers_not_ready():
    assert set(PHASE_OPS) & set(REGISTRY) == BUILT


# ---- the switches are off in code ---------------------------------------------------------------
def test_each_switch_is_off_unless_the_release_turns_it_on():
    settings = config.Settings.from_env({})
    assert (settings.tax, settings.insights, settings.goals) == (False, False, False)
    on = config.Settings.from_env(dict.fromkeys(kinds.SWITCH_ENV.values(), "on"))
    assert (on.tax, on.insights, on.goals) == (True, True, True)
    one = config.Settings.from_env({"SUDARSHAN_PORTFOLIO_INSIGHTS": "on"})
    assert (one.tax, one.insights, one.goals) == (False, True, False)


@pytest.mark.parametrize("key", list(kinds.SWITCH_ENV.values()))
def test_a_switch_that_is_neither_on_nor_off_is_refused_in_words(key):
    with pytest.raises(config.ConfigError, match=f"{key} must be on or off"):
        config.Settings.from_env({key: "maybe"})


def test_a_service_with_no_such_setting_counts_as_switched_off():
    assert config.switch_on(object(), "tax") is False
    assert config.switch_on(Ctx(tax=True), "tax") is True
    assert config.switch_on(Ctx(tax=True), "goals") is False


# ---- the gate on the ops ------------------------------------------------------------------------
def test_an_op_of_the_phase_answers_not_ready_while_its_switch_is_off():
    book: dict = {}
    calls: list = []

    @op("tax_get", book)
    async def tax_get(ctx, message):
        calls.append(message)
        return {"fy": 2026}

    with pytest.raises(ApiError) as refused:
        run(book["tax_get"](Ctx(), {"fy": 2026}))
    assert (refused.value.status, refused.value.code) == (501, "not_ready")
    assert str(refused.value) == kinds.WORDS["not_ready"] and calls == []
    with pytest.raises(ApiError):  # another switch on is not its switch
        run(book["tax_get"](Ctx(insights=True, goals=True), {}))
    assert run(book["tax_get"](Ctx(tax=True), {"fy": 2026})) == {"fy": 2026}
    assert calls == [{"fy": 2026}]


def test_an_op_of_no_switch_is_left_as_it_is():
    async def state(ctx, message):
        return 1

    assert switched("state", state) is state
    assert switched("tax_get", state) is not state


# ---- the owner's state says which switches are on -----------------------------------------------
def test_the_owners_state_names_the_switches_and_a_member_is_told_nothing(tmp_path):
    async def go():
        rig = Rig(tmp_path)
        rig.svc.settings = dataclasses.replace(settings_for(tmp_path), tax=True)
        await rig.svc.api.start(0)
        try:
            answer = await rig.call("state")
            assert answer["data"]["switches"] == {"tax": True, "insights": False, "goals": False}
            off = await rig.call("tax_get")  # on, but the op is not built yet
            assert off["code"] == "not_ready"
        finally:
            await rig.svc.api.close()
            rig.svc.store.close()

    run(go())


def test_the_family_state_has_no_switches(tmp_path):
    from test_portfolio_purposes import FAM

    async def go():
        rig = Rig(tmp_path)
        rig.svc.api.purposes.append(("family", FAM))
        await rig.svc.api.start(0)
        try:
            import httpx

            from sudarshan.autologin.handoff import seal

            async with httpx.AsyncClient(trust_env=False) as client:
                answer = await client.post(
                    f"http://127.0.0.1:{rig.svc.api.port}/v1/state",
                    content=seal(FAM, "state", principal="viewer:7", args={}),
                    timeout=20,
                )
            assert answer.status_code == 200
            assert "switches" not in answer.json()["data"]
        finally:
            await rig.svc.api.close()
            rig.svc.store.close()

    run(go())


# ---- the gate on the jobs -----------------------------------------------------------------------
def test_a_job_of_the_phase_does_nothing_while_its_switch_is_off(caplog):
    ran: list = []

    async def corporate_actions(service):
        ran.append(service)

    async def eod(service):
        ran.append("eod")

    jobs = {"corporate_actions": corporate_actions, "eod": eod}
    timers.gate_switched(jobs)
    assert jobs["eod"] is eod  # a job of no switch is left alone
    with caplog.at_level("INFO"):
        run(jobs["corporate_actions"](Ctx()))
    assert ran == [] and "SUDARSHAN_PORTFOLIO_INSIGHTS is off" in caplog.text
    on = Ctx(insights=True)
    run(jobs["corporate_actions"](on))
    assert ran == [on]
    gated = jobs["corporate_actions"]
    timers.gate_switched(jobs)  # gating twice wraps once
    assert jobs["corporate_actions"] is gated


def test_register_all_puts_the_gate_on(monkeypatch):
    from sudarshan.portfolio import corpactions, service

    monkeypatch.setattr(service, "JOBS", {})

    def register():
        @service.job("corporate_actions")
        async def corporate_actions(svc):
            return None

    monkeypatch.setattr(corpactions, "register_jobs", register)
    timers.register_all()
    assert getattr(service.JOBS["corporate_actions"], "switch", None) == "insights"
    assert set(service.JOBS) >= {"eod", "settle", "reports", "corporate_actions"}
