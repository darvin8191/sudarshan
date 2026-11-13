"""The day report, the morning brief, the archive and its ops (JB7): built through core/words.py,
kept once per period, announced to the report desk with ids only (Y8), the text Markdown-safe for
Telegram under the delivery purpose, the settings with what the Reports page shows, the owner's
"Check now" and the family's counts (Y16). Against fakes on 127.0.0.1; all values synthetic."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from http.server import BaseHTTPRequestHandler

import httpx
import pytest
from journal_fakes import KEYS, _Server, free_port, sealed, trade_row
from test_journal_capture import DAY, OPT, STOCK, ist_ms, placed, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_jobs import answer_positions, live_rows, position, record

from sudarshan.journal import reports
from sudarshan.journal.errors import ApiError
from sudarshan.journal.jobs import check, day
from sudarshan.journal.ops import REGISTRY
from sudarshan.journal.ops import common as ops_common
from sudarshan.journal.store import Scope
from sudarshan.ops.events import read_events

OWNER = Scope.owner()
CLOSE = ist_ms(DAY, "15:45:00")


def call(rig, op, purpose="api", **args):
    async def go():
        token = ops_common.CALLER.set(ops_common.Caller(purpose, "x"))
        try:
            return await REGISTRY[op](rig.svc, args)
        finally:
            ops_common.CALLER.reset(token)

    return scenario(rig, go)


def kept(rig, kind=None):
    rows = rig.svc.store.read(OWNER, lambda tx: tx.rows("report", order="id"))
    return [r for r in rows if kind is None or r["kind"] == kind]


def spool(rig):
    return read_events(reports.spool_of(rig.svc))


def close_the_day(rig, rows=None, positions=None):
    record(rig, live_rows() if rows is None else rows)
    answer_positions(rig, positions if positions is not None else [
        position(OPT["token"], "I", 0, realised=81_250),
        position("NSE_FO|50002", "D", 65, unrealised=-12_000),
    ])  # fmt: skip
    rig.now = CLOSE
    return scenario(rig, lambda: day.close_day(rig.svc, DAY, CLOSE, False))


# ---- the day report ----------------------------------------------------------------------------
def test_the_day_report_is_kept_once_and_announced_with_ids_only(rig):
    line = close_the_day(rig)
    (report,) = kept(rig, "eod")
    assert report["period_key"] == "20261109" and report["status"] == "ready"
    text = report["text"]
    assert text.splitlines()[:2] == ["सुदर्शन · SUDARSHAN", "Mon 9 Nov · Day report"]
    assert "Realised +₹812.50 · Unrealised −₹120.00" in text
    assert "By strategy: ORB +₹" in text and "Trades 1 · won 1, lost 0" in text
    assert "Kill switch off · Daily loss limit reached at 13:42" in text
    assert text.endswith("Fills: 3 recorded live; the day's book is checked at 16:05.")
    assert line.endswith(f"day report {report['id']} kept")
    (event,) = spool(rig)
    assert (event.src, event.kind, event.fact("report"), event.fact("report_kind")) == (
        "journal", "report", str(report["id"]), "eod",
    )  # fmt: skip
    assert event.fact("sha256") == "" and "text" not in event.facts  # Y8: no text in the spool
    assert event.id == f"report-{report['id']}-{report['built_ms']}"


def test_the_close_check_rewrites_the_kept_day_report_and_sends_nothing_again(rig):
    close_the_day(rig, rows=live_rows()[:2])
    rig.answer_trades(live_rows())
    rig.now = ist_ms(DAY, "16:05:00")
    scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    (report,) = kept(rig, "eod")
    assert report["text"].endswith("Fills: 2 recorded live; the close check found 1 more fill; "
                                   "the journal is corrected.")  # fmt: skip
    assert report["reconciled_at"] == rig.now
    assert len(spool(rig)) == 1


def test_a_close_check_before_the_day_report_leaves_it_to_be_built_and_sent(rig):
    """S4: both late at 17:00; the check ran first, so the day report is still announced."""
    record(rig, live_rows())
    scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    assert kept(rig, "eod") == []
    close_the_day(rig)
    (report,) = kept(rig, "eod")
    assert "the close check agreed" in report["text"] and len(spool(rig)) == 1


def test_a_switched_off_day_report_is_not_built(rig):
    rig.svc.store.write(OWNER, lambda tx: tx.settings_save(
        {**{k: v for k, v in tx.settings().items() if k != "version"}, "eod_on": False}, 1, 1))  # fmt: skip
    line = close_the_day(rig)
    assert kept(rig) == [] and line.endswith("the day report is switched off")


def test_unread_positions_are_said_in_the_day_report(rig):
    record(rig, live_rows())
    answer_positions(rig, None, state="unavailable")
    scenario(rig, lambda: day.close_day(rig.svc, DAY, CLOSE, False))
    (report,) = kept(rig, "eod")
    assert report["text"].endswith(reports.NOT_READ_LINE)


# ---- the morning brief -------------------------------------------------------------------------
def test_the_morning_brief_says_the_session_funds_overnight_and_yesterday(rig):
    tue = 20261110
    close_the_day(rig)  # Monday's close: yesterday for Tuesday's brief
    rig.portfolio.answers["funds"] = {"state": "ok", "read_at": 1, "note": None,
                                      "data": {"available_paise": 18_240_000, "groups": []}}  # fmt: skip
    answer_positions(rig, [position("NSE_FO|50002", "D", 65, overnight_qty_u=65_000_000)])
    rig.now = ist_ms(tue, "08:45:00")
    report_id = scenario(rig, lambda: reports.build_brief(rig.svc, tue, rig.now))
    (brief,) = kept(rig, "brief")
    assert brief["id"] == report_id and brief["period_key"] == "20261110"
    lines = brief["text"].splitlines()
    assert lines[1] == "Tue 10 Nov · Morning brief"
    assert lines[2] == "Market open 09:15-15:30 (F&O to 15:40)"
    assert lines[3] == "Funds available ₹1,82,400.00"
    assert lines[4] == "Overnight: 1 position (NIFTY18NOV2625000CE, 65)"
    assert lines[5].startswith("Yesterday: net +₹692.50 · 1 trade")
    assert lines[6] == "Kill switch off"
    assert [e.fact("report_kind") for e in spool(rig)] == ["eod", "brief"]


def test_the_muhurat_brief_names_its_window_and_an_expiry_its_index(rig, tmp_path):
    extra = tmp_path / "extra.txt"
    extra.write_text("2026-11-08 14:05-15:40\n")
    rig.svc.settings = type(rig.svc.settings)(**{**rig.svc.settings.__dict__,
                                                 "extra_days_file": extra})  # fmt: skip
    rig.svc.parts.pop("schedule", None)
    text = scenario(rig, lambda: reports.brief_inputs(rig.svc, 20261108, rig.now))
    assert text["kind"] == "muhurat" and text["window"] == "14:20-15:40"
    expiry = scenario(rig, lambda: reports.brief_inputs(rig.svc, 20261118, rig.now))
    assert expiry["expiry_label"] == "NIFTY"  # the master's index option of 18 Nov


def test_markdown_characters_are_escaped_for_telegram_only():
    assert reports.markdown_safe("my_orb *x* [a] `b`") == "my\\_orb \\*x\\* \\[a] \\`b\\`"


# ---- the ops -------------------------------------------------------------------------------------
@pytest.fixture
def a_report(rig, files):
    import sqlite3

    with sqlite3.connect(files / "openalgo.db") as conn:  # a strategy name with an underscore
        conn.execute("UPDATE order_logs SET request_data = ? WHERE id = 1",
                     (json.dumps(placed("o1", strategy="my_orb")["request_data"]),))  # fmt: skip
    close_the_day(rig)
    return kept(rig, "eod")[0]


def test_report_get_is_plain_for_the_page_and_markdown_safe_for_the_desk(rig, a_report):
    page = call(rig, "report_get", id=a_report["id"])
    desk = call(rig, "report_get", "delivery", id=a_report["id"])
    assert "By strategy: my_orb" in page["text"]
    assert "By strategy: my\\_orb" in desk["text"]
    assert set(page) == set(reports_fields()) and page["reason"] is None
    with pytest.raises(ApiError) as caught:
        call(rig, "report_get", id=999)
    assert caught.value.status == 404 and caught.value.message.endswith("Reports page again.")


def reports_fields():
    from sudarshan.journal.views import fields_of

    return fields_of("Report")


def test_reports_list_is_newest_first_and_filters_by_kind(rig, a_report):
    scenario(rig, lambda: reports.build_brief(rig.svc, DAY, rig.now))
    assert [r["kind"] for r in call(rig, "reports_list")] == ["brief", "eod"]
    assert [r["kind"] for r in call(rig, "reports_list", kind="eod")] == ["eod"]
    assert len(call(rig, "reports_list", limit=1)) == 1


def test_report_file_is_empty_until_the_renderer_keeps_one(rig, a_report):
    assert call(rig, "report_file", "delivery", id=a_report["id"]) == {
        "file_name": None, "file_mime": None, "file_sha256": None, "file_b64": None,
    }  # fmt: skip
    data = b"%PDF-1.7 synthetic"
    rig.svc.store.write(OWNER, lambda tx: tx.touch("report", a_report["id"], {
        "file_name": "SUDARSHAN_Day_report_20261109.pdf", "file_mime": "application/pdf",
        "file_sha256": hashlib.sha256(data).hexdigest(), "file_bytes": data}, 1))  # fmt: skip
    got = call(rig, "report_file", id=a_report["id"])
    assert base64.b64decode(got["file_b64"]) == data
    assert got["file_sha256"] == hashlib.sha256(data).hexdigest()
    assert "file_bytes" not in call(rig, "report_get", id=a_report["id"])


def test_delivery_records_the_desks_outcome_in_words(rig, a_report):
    answer = call(rig, "delivery", "delivery", report_id=a_report["id"], channel="telegram",
                  status="not_sent", reason="OpenAlgo's Telegram bot is stopped")  # fmt: skip
    assert answer["id"] == a_report["id"]
    got = call(rig, "report_get", id=a_report["id"])
    assert (got["status"], got["reason"]) == ("not_sent", "OpenAlgo's Telegram bot is stopped")
    call(rig, "delivery", "delivery", report_id=a_report["id"], channel="telegram",
         status="sent", reason=None)  # fmt: skip
    got = call(rig, "report_get", id=a_report["id"])
    assert (got["status"], got["reason"]) == ("sent", None)
    with pytest.raises(ApiError) as caught:
        call(rig, "delivery", "delivery", report_id=999, channel="telegram", status="sent")
    assert caught.value.status == 404 and caught.value.message.endswith(".")
    with pytest.raises(ApiError):
        call(rig, "delivery", "delivery", report_id=1, channel="email", status="sent")


def test_report_build_makes_the_day_report_and_the_brief_on_request(rig, a_report):
    built = call(rig, "report_build", kind="eod")
    assert built == {"id": a_report["id"], "version": 1,
                     "words": "The day report is in the archive."}  # fmt: skip
    assert len(spool(rig)) == 1  # a rebuild is not sent again
    brief = call(rig, "report_build", kind="brief")
    assert brief["words"] == "The morning brief is in the archive."
    with pytest.raises(ApiError) as caught:
        call(rig, "report_build", kind="weekly")
    assert (caught.value.status, caught.value.code) == (501, "not_ready")
    with pytest.raises(ApiError) as caught:
        call(rig, "report_build", kind="eod", period="20261110")
    assert caught.value.status == 422
    with pytest.raises(ApiError) as caught:
        call(rig, "report_build", kind="eod", period="20261106")
    assert caught.value.message.startswith("There is no close for that day")


# ---- settings ----------------------------------------------------------------------------------
class FakeAlerts(_Server):
    """sudarshan-alerts' /health."""

    def __init__(self):
        super().__init__()
        self.up = True

    def handler(fake):  # noqa: N805 - a closure over the fake
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                if not fake.up:
                    self.send_response(503)
                    self.end_headers()
                    return
                body = json.dumps({"service": "sudarshan-alerts", "state": "running"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


@pytest.fixture
def alerts(rig):
    fake = FakeAlerts()
    url = fake.start()
    rig.svc.settings = type(rig.svc.settings)(**{**rig.svc.settings.__dict__, "alerts_url": url})
    yield fake
    fake.stop()


def settings_of(rig):
    async def go():
        token = ops_common.CALLER.set(ops_common.Caller())
        try:
            return await REGISTRY["settings_get"](rig.svc, {})
        finally:
            ops_common.CALLER.reset(token)
            probe = rig.svc.parts.pop("alerts", None)
            if probe is not None:
                await probe.aclose()

    return scenario(rig, go)


def test_settings_carry_the_alerts_the_rate_card_and_the_times(rig, alerts):
    got = settings_of(rig)
    assert got["alerts_on"] is True and got["report_language"] == "en" and got["version"] == 1
    assert got["rate_card"] == {"version": "2026-11-14", "from_day": 20260401}
    assert got["times"] == {"brief": "08:45", "eod": "15:45", "check": "16:05",
                            "weekly": "Sunday 18:00", "monthly": "the 1st at 07:00",
                            "fy": "1 April 07:00"}  # fmt: skip
    alerts.up = False
    assert settings_of(rig)["alerts_on"] is False


def test_a_report_nobody_told_about_means_the_desk_is_off(rig, alerts, a_report):
    rig.now = a_report["built_ms"] + 31 * 60_000
    assert settings_of(rig)["alerts_on"] is False
    call(rig, "delivery", "delivery", report_id=a_report["id"], channel="telegram", status="sent")
    assert settings_of(rig)["alerts_on"] is True


def test_settings_set_under_its_version_and_english_only(rig):
    done = call(rig, "settings_set", version=1, eod_on=False, capital_base_paise=50_000_000)
    assert done["version"] == 2
    current = rig.svc.store.read(OWNER, lambda tx: tx.settings())
    assert current["eod_on"] is False and current["capital_base_paise"] == 50_000_000
    with pytest.raises(ApiError) as stale:
        call(rig, "settings_set", version=1, brief_on=False)
    assert stale.value.status == 409
    with pytest.raises(ApiError) as hindi:
        call(rig, "settings_set", version=2, report_language="hi")
    assert hindi.value.status == 422 and hindi.value.message == (
        "Reports are in English only in this release."
    )  # fmt: skip
    with pytest.raises(ApiError):
        call(rig, "settings_set", version=2, capital_base_paise=0)
    assert call(rig, "settings_set", version=2, capital_base_paise=None)["version"] == 3


# ---- check now, and the family's counts --------------------------------------------------------
def test_check_now_reads_once_a_minute(rig):
    rig.answer_trades(live_rows())
    first = call(rig, "reconcile_now")
    assert first["words"] == "The day's trades were read: 3 more fills found."
    with pytest.raises(ApiError) as caught:
        call(rig, "reconcile_now")
    assert caught.value.status == 422 and caught.value.message.startswith("The journal read")
    rig.now += 61_000
    again = call(rig, "reconcile_now")
    assert again["words"] == "The day's trades were read: the journal already had every fill."


def test_the_family_gets_counts_only(rig):
    record(rig, live_rows() + [
        trade_row("t4", "o4", key=STOCK["token"], product="D", side="BUY", qty=10,
                  price_u=2_500_000, at=ist_ms(DAY, "11:00:00")),
        trade_row("t5", "o5", key=STOCK["token"], product="D", side="SELL", qty=10,
                  price_u=2_400_000, at=ist_ms(DAY, "12:00:00")),
    ])  # fmt: skip
    got = call(rig, "family_day", "family", day=DAY)
    assert got == {"day": DAY, "trades": 2, "won": 1, "lost": 1}
    assert not any("paise" in k or "price" in k or "qty" in k for k in got)


def test_the_desk_reads_the_text_through_the_sealed_api(rig, a_report):
    """End to end: the alerts' desk seals report_get under its own purpose and principal."""
    port = free_port()

    async def go():
        await rig.svc.api.start(port)
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                answer = await client.post(
                    f"http://127.0.0.1:{port}/v1/report_get",
                    content=sealed("report_get", "delivery", id=a_report["id"]),
                    headers={"Content-Type": "application/octet-stream"}, timeout=10,
                )  # fmt: skip
                refused = await client.post(
                    f"http://127.0.0.1:{port}/v1/report_build",
                    content=sealed("report_build", "delivery", kind="eod"),
                    timeout=10,
                )
        finally:
            await rig.svc.api.close()
        return answer, refused

    answer, refused = scenario(rig, go)
    body = answer.json()
    assert answer.status_code == 200 and body["status"] == "success"
    assert "my\\_orb" in body["data"]["text"]
    assert refused.status_code == 403  # the desk's purpose opens three ops only
    assert KEYS["delivery"] is not None and asyncio.iscoroutinefunction(REGISTRY["report_get"])
