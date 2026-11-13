"""Universes (SV2) built from an openalgo.db made with OpenAlgo's SymToken model: the F&O list with
CAS marks, nearest futures, indices, the shipped lists with missing names, the option window
anchored on the money, watchlist items, and the read-only master."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.symbol import Base, SymToken
from sudarshan.screener import catalog as cat
from sudarshan.screener import universe as u

TODAY = date(2026, 10, 28)


def _row(symbol, exchange, token, kind, name="", expiry=None, strike=-1.0, lot=1, tick=0.05):
    return SymToken(
        symbol=symbol, brsymbol=symbol, name=name or symbol, exchange=exchange, brexchange=exchange,
        token=token, expiry=expiry or "", strike=strike, lotsize=lot, instrumenttype=kind, tick_size=tick,
    )  # fmt: skip


@pytest.fixture
def master(tmp_path):
    path = tmp_path / "openalgo.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            _row("RELIANCE", "NSE", "NSE_EQ|INE002A01018", "EQ", "RELIANCE INDUSTRIES", tick=0.1),
            _row("SBIN", "NSE", "NSE_EQ|INE062A01020", "EQ"),
            _row("INFY", "NSE", "NSE_EQ|INE009A01021", "EQ"),
            _row("HDFCBANK", "NSE", "NSE_EQ|INE040A01034", "EQ"),
            _row("RELIANCE30OCT26FUT", "NFO", "NSE_FO|1", "FUT", "RELIANCE", "30-OCT-26", lot=250),
            _row("RELIANCE26NOV26FUT", "NFO", "NSE_FO|2", "FUT", "RELIANCE", "26-NOV-26", lot=250),
            _row("SBIN30OCT26FUT", "NFO", "NSE_FO|3", "FUT", "SBIN", "30-OCT-26", lot=750),
            _row("SBIN24SEP26FUT", "NFO", "NSE_FO|4", "FUT", "SBIN", "24-SEP-26", lot=750),  # past
            _row("NIFTY30OCT26FUT", "NFO", "NSE_FO|5", "FUT", "NIFTY", "30-OCT-26", lot=65),
            _row("BANKNIFTY30OCT26FUT", "NFO", "NSE_FO|6", "FUT", "BANKNIFTY", "30-OCT-26", lot=30),
            _row("NIFTY", "NSE_INDEX", "NSE_INDEX|Nifty 50", "INDEX"),
            _row("BANKNIFTY", "NSE_INDEX", "NSE_INDEX|Nifty Bank", "INDEX"),
            _row("INDIAVIX", "NSE_INDEX", "NSE_INDEX|India VIX", "INDEX"),
        ]
        + [
            _row(
                f"NIFTY30OCT26{strike}{kind}",
                "NFO",
                f"NSE_FO|{1000 + strike + (0 if kind == 'CE' else 1)}",
                kind,
                "NIFTY",
                "30-OCT-26",
                strike=float(strike),
                lot=65,
            )
            for strike in range(25000, 26550, 50)
            for kind in ("CE", "PE")
        ]  # fmt: skip
        + [
            _row(
                f"NIFTY06NOV26{strike}{kind}",
                "NFO",
                f"NSE_FO|{9000 + strike}",
                kind,
                "NIFTY",
                "06-NOV-26",
                strike=float(strike),
                lot=65,
            )
            for strike in (25500, 25600)
            for kind in ("CE",)
        ]  # fmt: skip
    )
    session.commit()
    session.close()
    engine.dispose()
    return u.Master(path)


def test_listing_reads_upstox_rows_and_finds_the_underlying():
    row = u.listing_of({"symbol": "reliance30oct26fut", "exchange": "NFO", "token": "NSE_FO|1", "name": "x",
                        "expiry": "30-OCT-26", "strike": -1, "lotsize": 250, "instrumenttype": "FUT",
                        "tick_size": 0.05})  # fmt: skip
    assert (
        row.underlying == "RELIANCE"
        and row.expiry == date(2026, 10, 30)
        and row.segment == "NSE_FO"
    )
    assert row.strike is None and row.tick_size == 5 and row.lot == 250
    option = u.listing_of({"symbol": "NIFTY25NOV2625900CE", "exchange": "NFO", "token": "NSE_FO|7",
                           "strike": 25900.0, "instrumenttype": "CE"})  # fmt: skip
    assert option.underlying == "NIFTY" and option.strike == 2_590_000
    assert (
        u.listing_of(
            {"symbol": "M&M", "exchange": "NSE", "token": "NSE_EQ|INE101A01026"}
        ).underlying
        is None
    )


def test_the_fno_list_is_every_stock_with_a_future_marked_cas(master):
    cash = master.cash("NSE")
    futures = master.rows("NFO", ["FUT"])
    members = u.fno_stocks(cash, futures, TODAY)
    assert [m.symbol for m in members] == ["RELIANCE", "SBIN"]
    assert all(m.cas and m.segment == "NSE_EQ" and m.exchange == "NSE" for m in members)
    assert members[0].key == "NSE_EQ|INE002A01018"


def test_futures_take_the_nearest_expiry_not_past_and_split_stocks_from_indices(master):
    futures = master.rows("NFO", ["FUT"])
    stocks = u.futures_of(futures, TODAY, index=False)
    assert [(m.symbol, m.key) for m in stocks] == [
        ("RELIANCE30OCT26FUT", "NSE_FO|1"),
        ("SBIN30OCT26FUT", "NSE_FO|3"),
    ]
    indices = u.futures_of(futures, TODAY, index=True)
    assert [m.symbol for m in indices] == ["BANKNIFTY30OCT26FUT", "NIFTY30OCT26FUT"]
    later = u.futures_of(futures, date(2026, 11, 2), index=False)
    assert [m.symbol for m in later] == ["RELIANCE26NOV26FUT"], "SBIN's October future is past"
    assert not any(m.cas for m in stocks)


def test_indices_and_the_shipped_lists(master):
    found = u.indices(master.rows("NSE_INDEX"))
    assert [m.key for m in found] == ["NSE_INDEX|Nifty Bank", "NSE_INDEX|Nifty 50"]
    names = u.read_list("NIFTYBANK")
    assert "HDFCBANK" in names and len(names) == 12 and all(n == n.upper() for n in names)
    assert len(u.read_list("NIFTY50")) == 50
    for list_id in cat.INDEX_LISTS:
        if list_id in ("NIFTY50", "NIFTYBANK"):
            assert (u.LISTS_DIR / f"{list_id}.txt").is_file()
    members, missing = u.index_list(
        ["HDFCBANK", "INFY", "NOTLISTED"], master.cash("NSE"), TODAY, fno={"HDFCBANK"}
    )
    assert [m.symbol for m in members] == ["HDFCBANK", "INFY"] and missing == ["NOTLISTED"]
    assert members[0].cas is True and members[1].cas is False, (
        "CAS only with derivatives (Oct 2026)"
    )
    with pytest.raises(FileNotFoundError):
        u.read_list("NIFTY9999")


def test_the_option_window_is_anchored_on_the_strike_nearest_the_price(master):
    options = master.rows("NFO", ["CE", "PE"])
    window = u.option_window(options, "NIFTY", 2_573_000, TODAY, strikes=2)
    strikes = sorted({int(m.symbol[12:17]) for m in window})
    assert strikes == [25650, 25700, 25750, 25800, 25850], "nearest 25750, two each side, both legs"
    assert len(window) == 10 and all(m.segment == "NSE_FO" for m in window)
    assert all("30OCT26" in m.symbol for m in window), "the nearest expiry only"
    edge = u.option_window(options, "NIFTY", 2_400_000, TODAY, strikes=2)
    assert sorted({int(m.symbol[12:17]) for m in edge}) == [25000, 25050, 25100]
    assert u.option_window(options, "BANKNIFTY", 5_000_000, TODAY) == []
    assert [
        m.symbol for m in u.option_window(options, "NIFTY", 2_555_000, date(2026, 11, 1), strikes=1)
    ] == ["NIFTY06NOV2625500CE", "NIFTY06NOV2625600CE"]


def test_watchlist_items_map_to_listings_and_name_the_unknown(master):
    masters = {"NSE": master.cash("NSE")}
    members, missing = u.watchlist_members(
        [{"symbol": "infy", "exchange": "nse"}, {"symbol": "RELIANCE", "exchange": "NSE"}, {"symbol": "XYZ", "exchange": "BSE"}],
        masters, TODAY, fno={"RELIANCE"},
    )  # fmt: skip
    assert [(m.symbol, m.cas) for m in members] == [("INFY", False), ("RELIANCE", True)]
    assert missing == ["BSE:XYZ"]
    rows = u.member_rows(members, {row.key: row for row in masters["NSE"].values()})
    assert rows[1] == {
        "key": "NSE_EQ|INE002A01018", "symbol": "RELIANCE", "exchange": "NSE", "segment": "NSE_EQ",
        "tick_size": 10, "lot": 1, "expiry": None, "strike": None, "opt_type": None, "cas": True,
    }  # fmt: skip


def test_the_master_is_read_only_and_a_missing_file_is_said_in_words(tmp_path, master):
    assert master.ready() is True
    with pytest.raises(u.MasterUnavailable) as caught:
        u.Master(tmp_path / "none.db").rows("NSE")
    assert "master contract" in str(caught.value) and not (tmp_path / "none.db").exists()
    assert u.Master(tmp_path / "none.db").ready() is False


#: The symbols of the two shipped files, pinned by hash so that no constituent list is copied here:
#: adding the one ``# as of`` header line to each file (M8.8, X1) must not change what the
#: screener reads from them.
PINNED = {
    "NIFTY50": (50, "f6744964a2feaad4c788f43e1e15139b8980e7536ca40f9869443dc0db694d48"),
    "NIFTYBANK": (12, "e9b859a1f32410429f12edcdb6ebeb9cae522a2d7d17511cf1d09172b4632da2"),
}


@pytest.mark.parametrize("list_id", sorted(PINNED))
def test_the_shipped_files_read_the_same_symbols_with_their_as_of_header(list_id):
    import hashlib

    names = u.read_list(list_id)
    count, digest = PINNED[list_id]
    assert len(names) == count
    assert hashlib.sha256("\n".join(names).encode()).hexdigest() == digest
    lines = (u.LISTS_DIR / f"{list_id}.txt").read_text(encoding="utf-8").splitlines()
    headers = [n for n, text in enumerate(lines) if text == "# as of 2026-09-30"]
    assert len(headers) == 1, "one header line, with the date of the index review"
    first = next(n for n, text in enumerate(lines) if text and not text.startswith("#"))
    assert headers[0] < first, "the header sits above the first symbol"
    assert all(text.startswith("#") for text in lines[: headers[0]]), "below the comment lines"
