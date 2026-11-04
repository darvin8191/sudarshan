"""The resolver reads a temporary copy of the master contract; nothing here touches OpenAlgo."""

from __future__ import annotations

import logging
from datetime import date

import pytest
from portfolio_fakes import SHARES, make_symtoken

from sudarshan.portfolio import resolver as resolver_module
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.book import Book
from sudarshan.portfolio.resolver import (
    Resolver,
    ResolverUnavailable,
    isin_of,
    reresolve,
    resolve_due,
)
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope

NOW = 1_793_000_000_000  # Mon 26 Oct 2026, 13:03 IST
OWNER = Scope.owner()


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


def book_of(tmp_path):
    store = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    return store, Book(store)


def test_a_share_is_found_by_symbol_and_by_key_with_its_isin(tmp_path):
    found = Resolver(make_symtoken(tmp_path / "o.db"))
    tcs = found.find(" tcs ", "NSE")
    assert (tcs.symbol, tcs.exchange, tcs.key) == ("TCS", "NSE", "NSE_EQ|INE467B01029")
    assert tcs.isin == "INE467B01029" and tcs.name == "Tata Consultancy Services"
    assert tcs.tick_size_u == 500 and tcs.segment == "NSE_EQ"
    assert found.find("TCS", "BSE").key == "BSE_EQ|INE467B01029"
    assert found.by_key("NSE_EQ|INE009A01021").symbol == "INFY"
    assert found.find("NOSUCH", "NSE") is None and found.by_key("NSE_EQ|X") is None
    assert found.find("TCS", "NFO") is None  # only the two cash exchanges


def test_the_plain_equity_series_wins_over_another_series_of_the_same_symbol(tmp_path):
    rows = [
        ("ABC", "NSE", "NSE_EQ|INE111A01011", "Abc", "BE", 0.05),
        ("ABC", "NSE", "NSE_EQ|INE222A01011", "Abc", "EQ", 0.05),
    ]
    found = Resolver(make_symtoken(tmp_path / "o.db", rows))
    assert found.find("ABC", "NSE").isin == "INE222A01011"


def test_isin_of_only_reads_a_cash_key():
    assert isin_of("NSE_EQ|INE002A01018") == "INE002A01018"
    assert isin_of("BSE_EQ|INE002A01018") == "INE002A01018"
    assert isin_of("NSE_FO|12345") is None and isin_of(None) is None and isin_of("NSE_EQ|") is None


def test_derivatives_and_the_download_date_are_read(tmp_path):
    found = Resolver(make_symtoken(tmp_path / "o.db", futures=("RELIANCE",)))
    assert found.has_derivatives("reliance") and not found.has_derivatives("TCS")
    assert found.downloaded_today("2026-10-26") and not found.downloaded_today("2026-10-27")


def test_a_missing_file_is_unavailable_in_words_and_never_created(tmp_path, caplog):
    gone = tmp_path / "none.db"
    found = Resolver(gone)
    with caplog.at_level(logging.ERROR), pytest.raises(ResolverUnavailable) as raised:
        found.find("TCS", "NSE")
    assert "not available" in str(raised.value) and "Try again" in str(raised.value)
    assert not gone.exists()  # opened read-only: nothing was made
    assert found.downloaded_today("2026-10-26") is False  # a question that can wait says no


def test_the_file_is_opened_read_only(tmp_path):
    path = make_symtoken(tmp_path / "o.db")
    found = Resolver(path)
    with pytest.raises(ResolverUnavailable):
        found._query("DELETE FROM symtoken")
    assert found.find("TCS", "NSE") is not None


def listed(book, rid, symbol, isin, key):
    return book.asset_create(
        OWNER, rid, "listed", now=NOW, exchange="NSE", symbol=symbol, isin=isin,
        name=symbol, instrument_key=key,
    )  # fmt: skip


def test_a_changed_isin_keeps_the_asset_and_the_old_one_stays_an_alias(tmp_path, caplog):
    """Mutation target 26: the asset keeps its id; a new asset would orphan its lots."""
    store, book = book_of(tmp_path)
    old = listed(book, "a-1", "TCS", "INE467B01011", "NSE_EQ|INE467B01011")
    account = book.account_create(OWNER, "ac", book.members(OWNER)[0]["id"], "Z", "demat", now=NOW)
    assert account["id"]
    found = Resolver(make_symtoken(tmp_path / "o.db"))
    with caplog.at_level(logging.INFO):
        said = reresolve(store, found, now=NOW + 1000)
    assert len(said) == 1 and "new ISIN" in said[0]
    assets = [a for a in book.assets(OWNER) if a["kind"] == "listed"]
    assert [a["id"] for a in assets] == [old["id"]]
    assert assets[0]["isin"] == "INE467B01029"
    assert assets[0]["instrument_key"] == "NSE_EQ|INE467B01029"
    aliases = {(a["kind"], a["value"]) for a in assets[0]["aliases"]}
    assert ("isin", "INE467B01011") in aliases and ("isin", "INE467B01029") in aliases
    assert sum("new ISIN" in r.getMessage() for r in caplog.records) == 1
    assert reresolve(store, found, now=NOW + 2000) == []  # nothing changed, nothing said


def test_an_isin_that_belongs_to_another_holding_is_left_alone(tmp_path):
    store, book = book_of(tmp_path)
    one = listed(book, "a-1", "TCS", "INE467B01011", "NSE_EQ|INE467B01011")
    listed(book, "a-2", "INFY", "INE467B01029", "NSE_EQ|INE467B01029")
    said = reresolve(store, Resolver(make_symtoken(tmp_path / "o.db")), now=NOW + 1)
    assert any("another holding" in line for line in said)
    mine = next(a for a in book.assets(OWNER) if a["id"] == one["id"])
    assert mine["isin"] == "INE467B01011"


def test_a_symbol_the_contract_no_longer_has_is_left_as_it_is(tmp_path):
    store, book = book_of(tmp_path)
    kept = listed(book, "a-1", "OLDCO", "INE999A01010", "NSE_EQ|INE999A01010")
    assert reresolve(store, Resolver(make_symtoken(tmp_path / "o.db")), now=NOW + 1) == []
    assert next(a for a in book.assets(OWNER) if a["id"] == kept["id"])["isin"] == "INE999A01010"


def test_an_unreadable_contract_skips_the_morning_check(tmp_path, caplog):
    store, book = book_of(tmp_path)
    listed(book, "a-1", "TCS", "INE467B01011", "NSE_EQ|INE467B01011")
    with caplog.at_level(logging.ERROR):
        assert reresolve(store, Resolver(tmp_path / "none.db"), now=NOW) == []
    assert any("skipped" in r.getMessage() for r in caplog.records)


class Calendar:
    def __init__(self, open_days):
        self.open_days = open_days

    def is_trading_day(self, day: date) -> bool:
        return day in self.open_days


class Downloaded:
    def __init__(self, ok=True):
        self.ok = ok

    def downloaded_today(self, iso):
        return self.ok


def test_the_morning_check_runs_once_on_a_trading_day_after_nine_and_the_download():
    monday = date(2026, 10, 26)
    cal = Calendar({monday})
    at_nine = 1_792_985_400_000  # 26 Oct 2026, 09:00 IST
    key = 20261026
    assert resolve_due(cal, Downloaded(), at_nine, None)
    assert not resolve_due(cal, Downloaded(), at_nine - 60_000, None)  # 08:59
    assert not resolve_due(cal, Downloaded(), at_nine, key)  # done today
    assert not resolve_due(cal, Downloaded(False), at_nine, None)  # not downloaded yet
    assert not resolve_due(Calendar(set()), Downloaded(), at_nine, None)  # a holiday


def test_the_service_job_is_registered_by_the_entry_point_only():
    assert "resolve" not in resolver_module.__dict__.get("JOBS", {})
    assert [s[0] for s in SHARES][:2] == ["RELIANCE", "TCS"]
