"""The review's small findings that change what the journal does (L8, L9, L17, L18, L3): numeric
trade ids in time order, an empty list of the day's trades trusted for nothing, a late day close
that reads the day first, the journal's memory on /health, and job rows kept 400 days. Against
the journal's rig and plain values; every value is synthetic."""

from __future__ import annotations

from journal_fakes import trade_row
from test_journal_capture import DAY, ist_ms, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)

from sudarshan.journal.core.pairing import pair_fills
from sudarshan.journal.jobs import catchup, check, day
from sudarshan.journal.service import resident_mb
from sudarshan.journal.store import Scope

OWNER = Scope.owner()


def test_numeric_trade_ids_in_the_same_millisecond_go_as_numbers():
    """L8, measured: "10" sorted before "9" as text, so a same-second sale then buy (9, 10) read
    as a buy then a sale: the trade's side flipped."""
    base = {"exchange": "NFO", "symbol": "X", "product": "MIS", "qty": 65, "exch_ts_ms": 1000}
    fills = [{**base, "trade_id": "10", "action": "BUY", "price_u": 1_000_000},
             {**base, "trade_id": "9", "action": "SELL", "price_u": 1_010_000}]  # fmt: skip
    (trip,) = pair_fills(fills)
    assert (trip["side"], trip["status"], trip["gross_paise"]) == ("short", "closed", 6_500)


def test_an_empty_list_of_the_days_trades_takes_nothing_out(rig):
    """L9: an "ok" with no rows at 16:05 marked every fill of the day withdrawn until 22:00."""
    rig.answer_trades([
        trade_row("t1", "o1", side="BUY", at=ist_ms(DAY, "10:01:00")),
        trade_row("t2", "o2", side="SELL", price_u=1_010_000, at=ist_ms(DAY, "10:20:00")),
    ])  # fmt: skip
    scenario(rig, rig.capture.read_live)
    rig.answer_trades([])
    rig.now = ist_ms(DAY, "16:05:00")
    scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    assert {f["source"] for f in rig.fills()} == {"stream_read"}
    (trip,) = rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", mode="live"))
    assert trip["status"] == "closed"
    # a list that names some of them still takes out the rest, as before
    rig.answer_trades([trade_row("t1", "o1", side="BUY", at=ist_ms(DAY, "10:01:00"))])
    scenario(rig, lambda: check.check_day(rig.svc, DAY, rig.now, False))
    sources = {f["trade_id"]: f["source"] for f in rig.fills()}
    assert sources == {"t1": "stream_read", "t2": "withdrawn"}


def test_a_late_day_close_reads_the_days_trades_first(rig):
    """L17: started after the close, the journal's 15:45 job counted no trades (it makes no read
    of its own) while the realised figure was right."""
    rig.answer_trades([
        trade_row("t1", "o1", side="BUY", at=ist_ms(DAY, "10:01:00")),
        trade_row("t2", "o2", side="SELL", price_u=1_125_000, at=ist_ms(DAY, "10:20:00")),
    ])  # fmt: skip
    rig.portfolio.answers["positions"] = {"state": "ok", "read_at": 1, "note": None, "data": []}
    rig.now = ist_ms(DAY, "17:00:00")
    line = scenario(rig, lambda: day.close_day(rig.svc, DAY, rig.now, True))
    row = rig.svc.store.read(OWNER, lambda tx: tx.day_close_get("live", DAY))
    assert row["trades_closed"] == 1
    assert line.startswith("the day's trades read first: 2 rows, 2 new")


def test_the_journals_memory_is_on_its_health_page(rig, tmp_path):
    state = rig.svc.health()["journal_state"]
    assert isinstance(state["rss_mb"], int) and state["rss_mb"] > 0
    assert resident_mb(tmp_path / "none") is None


def test_job_rows_older_than_400_days_are_let_go_at_the_catch_up(rig):
    """L3: the design's 400 days for job rows (report files are kept: about 6 MB a year)."""
    store = rig.svc.store
    store.write(OWNER, lambda tx: tx.job_put("eod", 20250901, "ok", None, 1))
    store.write(OWNER, lambda tx: tx.job_put("eod", 20251020, "ok", None, 1))
    rig.answer_trades([])
    rig.portfolio.answers["historical_trades"] = {"state": "ok", "rows": [], "total_pages": 1}
    rig.portfolio.answers["pnl_report"] = {"state": "ok", "rows": [], "charges": {}}
    rig.now = ist_ms(20261109, "22:00:00")
    scenario(rig, lambda: catchup.catch_up(rig.svc, 20261109, rig.now, False))
    days = {r["day"] for r in store.read(OWNER, lambda tx: tx.job_runs()) if r["name"] == "eod"}
    assert days == {20251020}  # 5 Oct 2025 is the first day kept on 9 Nov 2026
