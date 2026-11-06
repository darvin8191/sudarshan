"""The portfolio's claims at the feed (M18.1): a fake feed on loopback."""

from __future__ import annotations

import asyncio
import logging

import pytest
from portfolio_fakes import FakeFeed

from sudarshan.portfolio.feedlink import (
    CLAIM_CAP,
    CLIENT,
    INDEX_KEYS,
    RENEW_MS,
    Claims,
    FeedClient,
)

NOW = 1_793_000_000_000


@pytest.fixture
def feed():
    fake = FakeFeed()
    fake.url = fake.start()
    yield fake
    fake.stop()


def run(coro):
    return asyncio.run(coro)


def sync(feed, steps, cap=CLAIM_CAP):
    """Run ``steps`` as [(keys, now)] against one Claims; returns the results."""

    async def go():
        link = FeedClient(feed.url)
        claims = Claims(link, cap)
        try:
            return [await claims.sync(keys, now) for keys, now in steps], claims
        finally:
            await link.aclose()

    return run(go())


def puts(feed):
    return feed.paths("PUT", "/claims/")


def test_the_client_id_mode_and_cap_follow_the_design():
    assert CLIENT == "sudarshan-portfolio" and CLAIM_CAP == 200 and RENEW_MS == 600_000
    assert INDEX_KEYS == ("NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty 500")


def test_a_put_goes_after_a_change_and_every_ten_minutes_and_not_between(feed):
    keys = [*INDEX_KEYS, "NSE_EQ|A"]
    sync(feed, [(keys, NOW), (keys, NOW + 60_000), (keys, NOW + RENEW_MS - 1)])
    assert len(puts(feed)) == 1
    assert puts(feed)[0]["_body"] == {"keys": dict.fromkeys(sorted(keys), "ltpc")}
    feed.calls.clear()
    sync(feed, [(keys, NOW), (keys, NOW + RENEW_MS)])
    assert len(puts(feed)) == 2  # the renewal
    feed.calls.clear()
    sync(feed, [(keys, NOW), ([*keys, "NSE_EQ|B"], NOW + 1000)])
    assert len(puts(feed)) == 2  # the set changed


def test_a_restarted_feed_is_told_again_at_once(feed):
    keys = [*INDEX_KEYS]

    async def go():
        link = FeedClient(feed.url)
        claims = Claims(link)
        await claims.sync(keys, NOW)
        feed.started_at = "2026-10-26T13:30:00"
        await claims.sync(keys, NOW + 5000)
        await claims.sync(keys, NOW + 6000)
        await link.aclose()

    run(go())
    assert len(puts(feed)) == 2


def test_the_cap_keeps_the_indexes_and_the_first_keys_and_says_so_once(feed, caplog):
    keys = [*INDEX_KEYS, *(f"NSE_EQ|K{n:04d}" for n in range(300))]
    with caplog.at_level(logging.WARNING):
        sync(feed, [(keys, NOW), (keys, NOW + 1000)])
    sent = puts(feed)[0]["_body"]["keys"]
    assert len(sent) == 200 and set(INDEX_KEYS) <= set(sent) and "NSE_EQ|K0197" in sent
    assert "NSE_EQ|K0198" not in sent
    assert sum("More than 200" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize(("status", "words"), [(404, "does not take claims"), (409, "no room")])
def test_a_404_or_a_409_is_said_once_and_never_becomes_a_subscribe(feed, caplog, status, words):
    feed.claim_status = status
    with caplog.at_level(logging.WARNING):
        results, claims = sync(feed, [(INDEX_KEYS, NOW), (INDEX_KEYS, NOW + 1000)])
    assert results == [False, False] and claims.claimed == frozenset()
    assert sum(words in r.getMessage() for r in caplog.records) == 1
    assert feed.paths("POST", "/subscribe") == [] and feed.paths("POST", "/unsubscribe") == []


def test_a_feed_that_does_not_answer_is_not_a_claim(feed):
    feed.down = True
    results, claims = sync(feed, [(INDEX_KEYS, NOW)])
    assert results == [False] and claims.claimed == frozenset()


def test_dropping_releases_the_claim(feed):
    async def go():
        link = FeedClient(feed.url)
        claims = Claims(link)
        await claims.sync(INDEX_KEYS, NOW)
        await claims.drop()
        await link.aclose()

    run(go())
    assert [c[1] for c in feed.calls if c[0] == "DELETE"] == [f"/claims/{CLIENT}"]


def test_the_portfolio_sends_what_the_alerts_service_sends():
    """Same id, path and body shape as sudarshan/alerts/market/feedlink.py (put_claims)."""
    import inspect

    from sudarshan.alerts.market import feedlink as alerts

    source = inspect.getsource(alerts.FeedLink.put_claims)
    assert '"PUT", f"/claims/{CLIENT}", {"keys": keys}' in source
    assert alerts.CLIENT == "sudarshan-alerts" and CLIENT != alerts.CLIENT


def test_follow_claims_the_indexes_the_listed_assets_and_the_upstox_holdings(tmp_path, feed):
    from sudarshan.autologin.schedule import TradingCalendar
    from sudarshan.portfolio import store as store_module
    from sudarshan.portfolio.book import Book
    from sudarshan.portfolio.prices import PriceBook
    from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope

    class Reader:
        def held_keys(self):
            return ["NSE_EQ|UPSTOXONLY", "NSE_EQ|INE467B01029"]

    store = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    try:
        Book(store).asset_create(
            Scope.owner(), "a-1", "listed", now=NOW, exchange="NSE", symbol="TCS",
            isin="INE467B01029", name="TCS", instrument_key="NSE_EQ|INE467B01029",
        )  # fmt: skip
        book = PriceBook(feed.url, None, TradingCalendar(tmp_path / "none"))

        async def go():
            ok = await book.follow(store, NOW, Reader())
            await book.aclose()
            return ok

        assert run(go()) is True
        sent = list(puts(feed)[0]["_body"]["keys"])
        assert sorted(sent) == sorted([*INDEX_KEYS, "NSE_EQ|INE467B01029", "NSE_EQ|UPSTOXONLY"])
        assert run(PriceBook(feed.url, None, TradingCalendar(tmp_path / "none")).follow(
            None, NOW)) is False  # fmt: skip
    finally:
        store_module.stop()


def test_over_the_cap_the_most_valuable_holdings_are_claimed_first(tmp_path, feed):
    from sudarshan.autologin.schedule import TradingCalendar
    from sudarshan.portfolio import store as store_module
    from sudarshan.portfolio.book import Book
    from sudarshan.portfolio.prices import PriceBook
    from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope

    store = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    try:
        book = Book(store)
        owner = Scope.owner()
        me = book.members(owner)[0]
        account = book.account_create(owner, "ac", me["id"], "Z", "demat", now=NOW)
        tab = book.tab_create(owner, "t-1", "Long term", "stocks", now=NOW)
        # keys in alphabetical order are the opposite of their value
        worth = {"NSE_EQ|AAA": 1_000, "NSE_EQ|BBB": 500_000, "NSE_EQ|CCC": 20_000}
        for n, (key, paise) in enumerate(worth.items()):
            asset = book.asset_create(
                owner, f"a-{n}", "listed", now=NOW, exchange="NSE", symbol=key[-3:],
                isin=f"INE00000000{n}", name=key, instrument_key=key,
            )  # fmt: skip
            version = next(t for t in book.tabs(owner) if t["id"] == tab["id"])["version"]
            book.activity_create(
                owner, tab["id"], version,
                {"kind": "BUY", "trade_date": 20261001, "account_id": account["id"],
                 "asset_id": asset["id"], "qty_u": 1_000_000, "amount_paise": paise},
                f"e-{n}", now=NOW,
            )  # fmt: skip
        prices = PriceBook(feed.url, None, TradingCalendar(tmp_path / "none"), max_claims=4)

        async def go():
            ok = await prices.follow(store, NOW)
            await prices.aclose()
            return ok

        assert run(go()) is True
        sent = set(puts(feed)[0]["_body"]["keys"])
        assert sent == {*INDEX_KEYS, "NSE_EQ|BBB", "NSE_EQ|CCC"}  # AAA is the least valuable
    finally:
        store_module.stop()
