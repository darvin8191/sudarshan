"""The family and journal purposes of the sealed API (P1, P2, PJ; cross-design Y4, Y6, Y24).

A service with a real store, a fake Upstox on loopback and three keys. Each test names the rule it
holds; the mutation targets 8, 9 and 10 of the family design (7.5) each turn one of them red.
"""

from __future__ import annotations

import asyncio
import inspect
import logging

import httpx
import pytest
from portfolio_fakes import FakeUpstox
from test_portfolio_import_ops import FERNET, NOW, OWNER, Rig
from test_portfolio_service import PEPPER
from test_portfolio_upstox import TOKEN

from sudarshan.autologin.handoff import seal
from sudarshan.portfolio import privacy, serialize
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.upstox import UpstoxReader
from sudarshan.vault import PORTFOLIO_FAMILY_API_INFO, PORTFOLIO_JOURNAL_API_INFO, fernet_for

FAM = fernet_for(PEPPER, PORTFOLIO_FAMILY_API_INFO)
JRN = fernet_for(PEPPER, PORTFOLIO_JOURNAL_API_INFO)
JOURNAL = "sudarshan-journal"
ALL = {"pnl": True, "qty": True, "funds": True, "orders": True}
NOT_SHARED = "Not shared with you."
HOUR = 3_600_000


@pytest.fixture
def fake():
    server = FakeUpstox()
    server.start()
    yield server
    server.stop()


class PRig(Rig):
    """The Rig of the import tests with the family and journal keys, a reader on a fake Upstox, a
    second tab and a priced entry on each."""

    def __init__(self, path, fake):
        super().__init__(path)
        token = path / "token"
        token.write_text(TOKEN)
        self.svc.api.purposes += [("family", FAM), ("journal", JRN)]
        self.svc.upstox = UpstoxReader(token, fake.url, clock=lambda: self.now)
        self.gold = self.book.asset_create(
            OWNER, "g-1", "manual", now=NOW, name="Gold coin", asset_class="gold",
            unit_label="g", first_value={"day": 20261001, "price_u": 60_000_000},
        )  # fmt: skip
        self.other = self.book.tab_create(OWNER, "t-2", "Private", "mixed", now=NOW)
        for tab in (self.tab, self.other):
            self.entry(tab)
        self.upstox_tab = next(t for t in self.book.tabs(OWNER) if t["system"])["id"]

    def entry(self, tab):
        version = next(t for t in self.book.tabs(OWNER) if t["id"] == tab["id"])["version"]
        entry = {
            "kind": "BUY", "trade_date": 20261001, "account_id": self.account["id"],
            "asset_id": self.gold["id"], "qty_u": 2_000_000, "amount_paise": 1_000_000,
        }  # fmt: skip
        self.book.activity_create(OWNER, tab["id"], version, entry, f"e-{tab['id']}", now=NOW)

    async def ask(self, fernet, principal, op, **args):
        async with httpx.AsyncClient(trust_env=False) as client:
            answer = await client.post(
                f"http://127.0.0.1:{self.svc.api.port}/v1/{op}",
                content=seal(fernet, op, principal=principal, args=args),
                timeout=20,
            )
        return answer.status_code, answer.json()

    async def owner(self, op, **args):
        status, body = await self.ask(FERNET, "owner", op, **args)
        assert status == 200, body
        return body["data"]

    async def family(self, member, op, **args):
        return await self.ask(FAM, f"viewer:{member}", op, **args)

    async def journal(self, op, **args):
        return await self.ask(JRN, JOURNAL, op, **args)

    async def grant(self, member, tabs, *, shown=ALL, level="view", role=None):
        for tab in tabs:
            await self.owner("share_set", tab_id=tab, viewer=member, level=level)
        await self.owner("member_privacy_set", member=member, role=role, shown=shown)


def scenario(test):
    wanted = [n for n in inspect.signature(test).parameters if n != "rig"]

    def wrapped(tmp_path, fake, caplog):
        extra = {"fake": fake, "caplog": caplog}

        async def go():
            rig = PRig(tmp_path, fake)
            await rig.svc.api.start(0)
            try:
                await test(rig, **{n: extra[n] for n in wanted})
            finally:
                await rig.svc.api.close()
                await rig.svc.upstox.aclose()
                rig.svc.store.close()

        asyncio.run(go())

    wrapped.__name__ = test.__name__
    return wrapped


def keys(data, found=None):
    found = set() if found is None else found
    if isinstance(data, dict):
        for key, value in data.items():
            found.add(key)
            keys(value, found)
    elif isinstance(data, list):
        for item in data:
            keys(item, found)
    return found


# ---- the purposes are told apart by their keys ---------------------------------------------------
@scenario
async def test_a_seal_under_a_key_of_no_purpose_is_refused(rig):
    stranger = fernet_for("z" * 48, PORTFOLIO_FAMILY_API_INFO)
    status, body = await rig.ask(stranger, "viewer:7", "state")
    assert status == 403 and body["code"] == "forbidden"


@scenario
async def test_each_purpose_takes_only_its_own_principal(rig):
    assert (await rig.ask(FAM, "owner", "state"))[0] == 403  # the family key never makes the owner
    assert (await rig.ask(FAM, JOURNAL, "state"))[0] == 403
    assert (await rig.ask(FAM, "viewer:", "state"))[0] == 403
    assert (await rig.ask(JRN, "viewer:7", "positions"))[0] == 403
    assert (await rig.ask(JRN, "owner", "positions"))[0] == 403
    assert (await rig.ask(FERNET, "viewer:7", "state"))[0] == 403
    assert (await rig.ask(FERNET, JOURNAL, "state"))[0] == 403


@scenario
async def test_the_owners_purpose_does_not_open_the_journals_or_the_familys_ops(rig):
    assert (await rig.ask(FERNET, "owner", "trades_day"))[0] == 403
    assert (await rig.ask(FERNET, "owner", "shares_for_viewer"))[0] == 403


# ---- the family purpose opens only read ops (mutation target 9) ----------------------------------
@scenario
async def test_the_family_purpose_opens_only_read_ops_and_a_write_is_403_and_logged(rig, caplog):
    assert set(kinds.FAMILY_OPS) == {
        "state", "summary", "tab_get", "performance", "upstox_book", "upstox_history",
        "shares_for_viewer",
    }  # fmt: skip
    await rig.grant("7", [rig.tab["id"]])
    refused = [n for n in kinds.SERVICE_OP_NAMES if n not in kinds.FAMILY_OPS]
    assert {"tab_create", "activity_create", "import_commit", "share_set", "trades_day"} <= set(
        refused
    )
    version = rig.version()
    with caplog.at_level(logging.WARNING, logger="sudarshan.portfolio"):
        for name in refused:
            status, body = await rig.family("7", name, id=rig.tab["id"], version=version)
            assert status == 403, name
            assert body["status"] == "error", name
    assert "refused the op tab_create under the family purpose" in caplog.text
    assert rig.version() == version  # nothing was written
    assert len(rig.book.tabs(OWNER)) == 3


@scenario
async def test_the_journal_purpose_opens_only_its_reads_and_a_write_is_403_and_logged(rig, caplog):
    assert set(kinds.JOURNAL_OPS) == {
        "trades_day", "order_history", "historical_trades", "pnl_report", "positions", "funds",
        "benchmark_closes",
    }  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="sudarshan.portfolio"):
        for name in (n for n in kinds.SERVICE_OP_NAMES if n not in kinds.JOURNAL_OPS):
            assert (await rig.journal(name, id=1))[0] == 403, name
    assert "refused the op state under the journal purpose" in caplog.text
    assert len(rig.book.tabs(OWNER)) == 3


# ---- P1: shares and privacy ----------------------------------------------------------------------
@scenario
async def test_a_share_is_made_changed_listed_and_removed_from_its_version(rig):
    made = await rig.owner("share_set", tab_id=rig.tab["id"], viewer="7", level="view")
    assert made == {"tab_id": rig.tab["id"], "viewer": "7", "level": "view", "expires_at": None,
                    "version": 1}  # fmt: skip
    status, body = await rig.ask(FERNET, "owner", "share_set", tab_id=rig.tab["id"], viewer="7",
                                 level="restricted")  # fmt: skip
    assert status == 409 and body["code"] == "version_conflict"  # a change names its version
    changed = await rig.owner("share_set", tab_id=rig.tab["id"], viewer="7", level="restricted",
                              expires_at=NOW + HOUR, version=1)  # fmt: skip
    assert changed["version"] == 2 and changed["level"] == "restricted"
    listed = await rig.owner("shares_list", viewer="7")
    assert [s["level"] for s in listed["shares"]] == ["restricted"]
    status, _ = await rig.ask(FERNET, "owner", "share_remove", tab_id=rig.tab["id"], viewer="7",
                              version=1)  # fmt: skip
    assert status == 409
    await rig.owner("share_remove", tab_id=rig.tab["id"], viewer="7", version=2)
    assert (await rig.owner("shares_list"))["shares"] == []


@scenario
async def test_a_share_asks_for_a_real_tab_a_level_a_viewer_and_a_future_expiry(rig):
    ask = lambda **a: rig.ask(FERNET, "owner", "share_set", **a)  # noqa: E731
    base = {"tab_id": rig.tab["id"], "viewer": "7", "level": "view"}
    assert (await ask(**{**base, "tab_id": 999}))[0] == 404
    assert (await ask(**{**base, "level": "admin"}))[0] == 400
    assert (await ask(**{**base, "viewer": "a b"}))[0] == 400
    assert (await ask(**{**base, "viewer": ""}))[0] == 400
    assert (await ask(**{**base, "expires_at": NOW - 1}))[0] == 400
    assert (await ask(**{**base, "tab_id": "1"}))[0] == 400
    assert (await rig.owner("shares_list"))["shares"] == []


@scenario
async def test_deleting_a_tab_takes_its_shares_with_it(rig):
    await rig.grant("7", [rig.tab["id"], rig.other["id"]])
    tab = next(t for t in rig.book.tabs(OWNER) if t["id"] == rig.other["id"])
    rig.book.tab_update(OWNER, tab["id"], tab["version"], {"archived": True}, now=NOW)
    tab = next(t for t in rig.book.tabs(OWNER) if t["id"] == rig.other["id"])
    rig.book.tab_delete(OWNER, tab["id"], tab["version"], tab["name"], now=NOW)
    left = (await rig.owner("shares_list"))["shares"]
    assert [s["tab_id"] for s in left] == [rig.tab["id"]]


@scenario
async def test_member_privacy_is_kept_by_member_and_refuses_pnl_without_quantities(rig):
    row = await rig.owner("member_privacy_set", member="7", role="child",
                          shown={"pnl": False, "qty": False, "funds": False, "orders": False})  # fmt: skip
    assert row == {"member": "7", "role": "child", "version": 1,
                   "shown": {"pnl": False, "qty": False, "funds": False, "orders": False}}  # fmt: skip
    status, body = await rig.ask(
        FERNET, "owner", "member_privacy_set", member="7", version=1,
        shown={"pnl": True, "qty": False, "funds": False, "orders": True},
    )  # fmt: skip
    assert status == 400 and body["message"] == (
        "Live P&L is worked out from quantities. Show quantities to show P&L."
    )
    again = await rig.owner("member_privacy_set", member="7", role=None, version=1, shown=ALL)
    assert again["version"] == 2 and again["shown"] == ALL and again["role"] is None
    for bad in ({"pnl": True}, {**ALL, "funds": "yes"}, None):
        assert (await rig.ask(FERNET, "owner", "member_privacy_set", member="7", version=2,
                              shown=bad))[0] == 400  # fmt: skip
    assert (await rig.ask(FERNET, "owner", "member_privacy_set", member="role:x",
                          shown=ALL))[0] == 400  # fmt: skip


# ---- P2: the scope is built from what the owner stored (mutation target 10) ----------------------
@scenario
async def test_the_family_sees_the_tabs_shared_with_it_and_a_request_can_only_narrow_them(rig):
    await rig.grant("7", [rig.tab["id"]])
    status, body = await rig.family("7", "state")
    assert status == 200
    assert [t["name"] for t in body["data"]["tabs"]] == [
        "Long term"
    ]  # not the Upstox tab, not Private
    both = [rig.tab["id"], rig.other["id"]]
    status, body = await rig.family("7", "state", tabs=both)  # the caller asks for more
    assert [t["id"] for t in body["data"]["tabs"]] == [rig.tab["id"]]
    status, body = await rig.family("7", "state", tabs=[rig.other["id"]])
    assert body["data"]["tabs"] == []
    status, body = await rig.family("7", "state", tabs=[])
    assert body["data"]["tabs"] == []
    status, body = await rig.family("7", "tab_get", id=rig.other["id"], tabs=[rig.other["id"]])
    assert status == 404  # the tab is there for the owner and gone for this member
    status, body = await rig.family("7", "tab_get", id=rig.other["id"])
    assert status == 404
    status, body = await rig.family("7", "tab_get", id=rig.tab["id"])
    assert status == 200 and body["data"]["tab"]["name"] == "Long term"


@scenario
async def test_a_member_with_no_share_sees_no_tab_and_a_bad_tabs_argument_is_refused(rig):
    status, body = await rig.family("8", "state")
    assert status == 200 and body["data"]["tabs"] == []
    for bad in ("1", [True], ["a"], {"a": 1}, list(range(101))):
        assert (await rig.family("8", "state", tabs=bad))[0] == 400


@scenario
async def test_an_expired_share_is_ignored_and_a_role_share_reaches_the_member_with_that_role(rig):
    await rig.owner("share_set", tab_id=rig.tab["id"], viewer="7", level="view", expires_at=NOW + 5)
    await rig.owner("member_privacy_set", member="7", role="child", shown=ALL)
    assert [t["id"] for t in (await rig.family("7", "state"))[1]["data"]["tabs"]] == [rig.tab["id"]]
    rig.now = NOW + 10  # the service's clock
    assert (await rig.family("7", "state"))[1]["data"]["tabs"] == []
    await rig.owner("share_set", tab_id=rig.other["id"], viewer="role:child", level="view")
    again = (await rig.family("7", "state"))[1]["data"]["tabs"]
    assert [t["id"] for t in again] == [rig.other["id"]]
    other = (await rig.family("8", "state"))[1]["data"]["tabs"]
    assert other == []  # no row, no role


@scenario
async def test_shares_for_viewer_names_the_tabs_the_levels_and_what_is_hidden(rig):
    await rig.grant("7", [rig.tab["id"]], shown={**ALL, "funds": False}, level="restricted")
    status, body = await rig.family("7", "shares_for_viewer")
    assert status == 200
    assert body["data"] == {
        "member": "7", "hides": ["funds"],
        "tabs": [{"tab_id": rig.tab["id"], "name": "Long term", "kind": "stocks",
                  "level": "restricted", "expires_at": None}],
    }  # fmt: skip


# ---- H1: a member is told only what their tabs reach ---------------------------------------------
def second_owner(rig):
    """A second member with an account of their own, used only on the tab that is not shared."""
    member = rig.book.member_create(OWNER, "m-2", "Second member", now=NOW)
    account = rig.book.account_create(OWNER, "a-2", member["id"], "Second bank", "bank", now=NOW)
    return member, account


@scenario
async def test_a_member_with_no_share_sees_no_member_no_account_and_no_job(rig):
    second_owner(rig)
    status, body = await rig.family("9", "state")
    assert status == 200
    data = body["data"]
    assert data["tabs"] == [] and data["members"] == [] and data["accounts"] == []
    assert "jobs" not in data and "amfi" not in data
    owner = await rig.owner("state")  # the owner's own state is whole
    assert {a["name"] for a in owner["accounts"]} == {"Upstox", "Zerodha", "Second bank"}
    assert {m["name"] for m in owner["members"]} == {"Me", "Second member"}
    assert "jobs" in owner and "amfi" in owner


@scenario
async def test_a_member_sees_only_the_accounts_of_their_tabs_and_those_accounts_members(rig):
    member, bank = second_owner(rig)
    version = next(t for t in rig.book.tabs(OWNER) if t["id"] == rig.other["id"])["version"]
    entry = {
        "kind": "BUY", "trade_date": 20261002, "account_id": bank["id"],
        "asset_id": rig.gold["id"], "qty_u": 1_000_000, "amount_paise": 600_000,
    }  # fmt: skip
    rig.book.activity_create(OWNER, rig.other["id"], version, entry, "e-bank", now=NOW)
    rig.svc.store.write(
        OWNER,
        lambda tx: tx.change("account", rig.account["id"], 1, {"note": "the owner's own"}, NOW),
    )
    await rig.grant("7", [rig.tab["id"]])
    data = (await rig.family("7", "state"))[1]["data"]
    assert [a["name"] for a in data["accounts"]] == ["Zerodha"]  # not Upstox, not Second bank
    assert [m["name"] for m in data["members"]] == ["Me"]
    assert "note" not in data["accounts"][0]  # quantities are shown to 7: the note is still his
    await rig.grant("8", [rig.other["id"]])
    data = (await rig.family("8", "state"))[1]["data"]
    assert {a["name"] for a in data["accounts"]} == {"Zerodha", "Second bank"}
    assert {m["name"] for m in data["members"]} == {"Me", "Second member"}
    assert member["id"] in {m["id"] for m in data["members"]}


@scenario
async def test_a_transfer_names_the_account_it_goes_to_for_the_member_too(rig):
    _, bank = second_owner(rig)
    move = {
        "kind": "TRANSFER", "trade_date": 20261003, "account_id": rig.account["id"],
        "to_account_id": bank["id"], "asset_id": rig.gold["id"], "qty_u": 1_000_000,
    }  # fmt: skip
    rig.book.activity_create(OWNER, rig.tab["id"], rig.version(), move, "e-move", now=NOW)
    await rig.grant("7", [rig.tab["id"]])
    status, body = await rig.family("7", "tab_get", id=rig.tab["id"])
    assert status == 200, body
    by_account = {bar["label"] for bar in body["data"]["allocation"]["account"]}
    assert by_account == {"Zerodha", "Second bank"}
    data = (await rig.family("7", "state"))[1]["data"]
    assert {a["name"] for a in data["accounts"]} == {"Zerodha", "Second bank"}


@scenario
async def test_a_loan_on_no_tab_is_never_shown_to_a_member(rig):
    def loose(tx):  # a loan v0.5.0 made, on no tab
        loan = tx.insert(
            "asset", {"kind": "liability", "name": "Car loan", "asset_class": "other"}, NOW
        )
        tx.set_price_point(loan["id"], 20261001, 30_000_000_000, NOW)

    rig.svc.store.write(OWNER, loose)
    await rig.grant("7", [rig.tab["id"]])
    owner = await rig.owner("tab_get", id=rig.tab["id"])
    assert [loan["name"] for loan in owner["loose_loans"]] == ["Car loan"]
    status, body = await rig.family("7", "tab_get", id=rig.tab["id"])
    assert status == 200 and body["data"]["loose_loans"] == []
    assert "Car loan" not in str(body["data"])


@scenario
async def test_a_members_scope_reads_no_table_their_tabs_do_not_reach(rig):
    """The store narrows every table for a scope with tabs and refuses the rest, so an op that
    forgets to narrow a read cannot leak it."""
    from sudarshan.portfolio.store import OutOfScope, Scope

    second_owner(rig)
    await rig.grant("7", [rig.tab["id"]], role="child")
    scope = Scope("owner", frozenset({rig.tab["id"]}))
    read = lambda fn: rig.svc.store.read(scope, fn)  # noqa: E731
    assert [a["name"] for a in read(lambda tx: tx.rows("account"))] == ["Zerodha"]
    assert [m["name"] for m in read(lambda tx: tx.rows("member"))] == ["Me"]
    assert [a["name"] for a in read(lambda tx: tx.rows("asset"))] == ["Gold coin"]
    assert read(lambda tx: tx.rows("upstox_eod", "day")) == []  # its tab is not shared
    assert read(lambda tx: tx.index_closes()) == {}  # market data a tab's comparison reads
    for table in ("tab_share", "member_privacy", "import_batch", "report_cache"):
        with pytest.raises(OutOfScope):
            read(lambda tx, table=table: tx.rows(table))
    for name in ("job_runs", "nav_day", "mf_count"):
        with pytest.raises(OutOfScope):
            read(lambda tx, name=name: getattr(tx, name)())
    nobody = Scope("owner", frozenset())
    assert rig.svc.store.read(nobody, lambda tx: tx.rows("member")) == []
    assert rig.svc.store.read(nobody, lambda tx: tx.rows("account")) == []


# ---- Y6: the stored floor, the caller can only add -----------------------------------------------
@scenario
async def test_the_stored_floor_hides_pnl_even_when_the_caller_sends_an_empty_mask(rig):
    await rig.grant("7", [rig.tab["id"]], shown={"pnl": False, "qty": True, "funds": False,
                                                   "orders": True})  # fmt: skip
    await rig.grant("9", [rig.tab["id"]])
    quiet = (await rig.family("7", "tab_get", id=rig.tab["id"], mask=[]))[1]["data"]
    loud = (await rig.family("9", "tab_get", id=rig.tab["id"], mask=[]))[1]["data"]
    pnl_names = {
        "unrealised_paise",
        "overall_gain_paise",
        "absolute_return_pct",
        "paise",  # the P&L of the day, inside a Today figure
        "base_paise",  # what the day's P&L is taken from: with the value it gives it away
        "realised_paise",
    }
    assert not pnl_names & keys(quiet)
    assert pnl_names <= keys(loud)
    assert "value_paise" in keys(quiet) and "qty_u" in keys(quiet)  # quantities are shown to 7


@scenario
async def test_hiding_pnl_keeps_the_trading_date(rig):
    """L4: ``today`` is also the trading date of state, summary and a tab; hiding P&L hides the
    figures inside a Today block, never the date."""
    await rig.grant("7", [rig.tab["id"]], shown={"pnl": False, "qty": True, "funds": False,
                                                   "orders": True})  # fmt: skip
    await rig.owner("share_set", tab_id=rig.tab["id"], viewer="8", level="view")  # no privacy row
    day = 20261026
    for member in ("7", "8"):
        for op in ("state", "summary"):
            assert (await rig.family(member, op))[1]["data"]["today"] == day, (member, op)
        tab = (await rig.family(member, "tab_get", id=rig.tab["id"]))[1]["data"]
        assert tab["today"] == day
    quiet = (await rig.family("7", "tab_get", id=rig.tab["id"]))[1]["data"]
    assert set(quiet["totals"]["today"]) == {"state", "counted", "unknown", "pending"}


@scenario
async def test_the_caller_may_add_to_the_mask_never_take_from_it(rig):
    await rig.grant("7", [rig.tab["id"]])
    plain = (await rig.family("7", "tab_get", id=rig.tab["id"]))[1]["data"]
    added = (await rig.family("7", "tab_get", id=rig.tab["id"], mask=["lines", "*_pct"]))[1]["data"]
    assert "lines" in keys(plain) and "lines" not in keys(added)
    await rig.grant("8", [rig.tab["id"]], shown={"pnl": False, "qty": True, "funds": False,
                                                   "orders": False})  # fmt: skip
    cut = (await rig.family("8", "tab_get", id=rig.tab["id"], mask=["not_a_field"]))[1]["data"]
    assert "overall_gain_paise" not in keys(cut)


@scenario
async def test_a_member_the_owner_has_not_set_sees_no_figures(rig):
    await rig.owner("share_set", tab_id=rig.tab["id"], viewer="7", level="view")  # no privacy row
    data = (await rig.family("7", "tab_get", id=rig.tab["id"]))[1]["data"]
    assert [r["asset"]["symbol"] for r in data["rows"]] == [None]  # the name stays (a gold coin)
    assert not {k for k in keys(data) if k.endswith(("_paise", "_u", "_pct"))} - {"tick_size_u"}


@scenario
async def test_with_quantities_hidden_pnl_is_hidden_whatever_the_row_says(rig):
    await rig.grant("7", [rig.tab["id"]], shown={"pnl": False, "qty": False, "funds": False,
                                                   "orders": False})  # fmt: skip

    def tamper(tx):  # a row the owner's op would refuse: P&L shown, quantities hidden
        row = tx.rows("member_privacy", member="7")[0]
        tx.change("member_privacy", row["id"], row["version"], {"show_pnl": 1}, NOW)

    rig.svc.store.write(OWNER, tamper)
    data = (await rig.family("7", "tab_get", id=rig.tab["id"]))[1]["data"]
    hidden = {"unrealised_paise", "overall_gain_paise", "paise", "base_paise", "qty_u", "lines"}
    assert not hidden & keys(data)


@scenario
async def test_a_restricted_share_shows_no_figures_and_a_view_share_shows_them(rig):
    await rig.grant("7", [rig.tab["id"]], level="restricted")
    await rig.grant("8", [rig.tab["id"]], level="view")
    cut = (await rig.family("7", "tab_get", id=rig.tab["id"]))[1]["data"]
    full = (await rig.family("8", "tab_get", id=rig.tab["id"]))[1]["data"]
    assert not {k for k in keys(cut) if hidden_by(k, serialize.VIEWER_MASK)}
    assert not {"qty_u", "value_paise", "lines", "totals"} & keys(cut)
    assert {"qty_u", "value_paise", "lines", "totals"} <= keys(full)
    assert [r["asset"]["name"] for r in cut["rows"]] == ["Gold coin"]


@scenario
async def test_the_restricted_mask_of_one_share_covers_every_tab_of_the_answer(rig):
    await rig.owner("share_set", tab_id=rig.tab["id"], viewer="7", level="view")
    await rig.owner("share_set", tab_id=rig.other["id"], viewer="7", level="restricted")
    await rig.owner("member_privacy_set", member="7", shown=ALL)
    data = (await rig.family("7", "tab_get", id=rig.tab["id"]))[1]["data"]
    assert "qty_u" not in keys(data)  # the union of the masks: nothing shows figures


def hidden_by(key, mask):
    return serialize.hidden(key, mask)


@scenario
async def test_the_answer_passes_the_mask_once_more_on_its_way_out(rig):
    """Mutation target 8, in the portfolio: an op that leaks a field is still masked."""
    await rig.grant("7", [rig.tab["id"]], shown={"pnl": False, "qty": False, "funds": False,
                                                   "orders": False})  # fmt: skip

    async def leaky(ctx, message):
        return {"symbol": "X", "value_paise": 5, "nested": [{"qty_u": 1, "name": "n"}]}

    rig.svc.api.registry = {"state": leaky}
    status, body = await rig.family("7", "state")
    assert status == 200 and body["data"] == {"symbol": "X", "nested": [{"name": "n"}]}
    status, body = await rig.ask(FERNET, "owner", "state")  # the owner's answer is untouched
    assert body["data"]["value_paise"] == 5


# ---- Y6: funds and the Upstox tab ----------------------------------------------------------------
@scenario
async def test_funds_are_not_served_without_a_funds_share(rig):
    await rig.grant("7", [rig.upstox_tab], shown={**ALL, "funds": False})
    status, body = await rig.family("7", "upstox_book", parts=["funds"], mask=[])
    assert status == 403 and body["message"] == NOT_SHARED
    status, body = await rig.family("7", "upstox_book", parts=["holdings", "funds"])
    assert status == 403 and body["message"] == NOT_SHARED
    status, body = await rig.family("7", "upstox_book")  # unasked: the parts it may see
    assert status == 200 and "funds" not in body["data"] and "holdings" in body["data"]
    await rig.owner("member_privacy_set", member="7", shown=ALL, version=1)
    status, body = await rig.family("7", "upstox_book", parts=["funds"])
    assert status == 200 and body["data"]["funds"]["state"] == "ok"
    assert body["data"]["funds"]["data"]["available_paise"] == 12_500_050


@scenario
async def test_the_upstox_book_is_refused_to_a_member_it_is_not_shared_with(rig):
    await rig.grant("7", [rig.tab["id"]])  # a tab, but not the Upstox one
    for op in ("upstox_book", "upstox_history"):
        status, body = await rig.family("7", op)
        assert status == 403 and body["message"] == NOT_SHARED, op
    assert rig.svc.upstox is not None and rig.svc.upstox._memory["holdings"].read_at is None


@scenario
async def test_orders_and_trades_follow_the_orders_category(rig, fake):
    await rig.grant("7", [rig.upstox_tab], shown={**ALL, "orders": False})
    status, body = await rig.family("7", "upstox_book", parts=["orders"])
    assert status == 403 and body["message"] == NOT_SHARED
    status, body = await rig.family("7", "upstox_book")
    assert not {"orders", "trades"} & set(body["data"])
    await rig.owner("member_privacy_set", member="7", shown=ALL, version=1)
    status, body = await rig.family("7", "upstox_book", parts=["orders", "trades"])
    assert status == 200 and set(body["data"]) == {"orders", "trades"}
    assert body["data"]["orders"]["read_at"] == NOW
    assert fake.count("day_orders") == 1 and fake.count("day_trades") == 1


@scenario
async def test_quantities_hidden_hides_what_the_book_says_of_them(rig):
    await rig.grant("7", [rig.upstox_tab], shown={"pnl": False, "qty": False, "funds": False,
                                                    "orders": True})  # fmt: skip
    status, body = await rig.family("7", "upstox_book", parts=["positions", "orders", "trades"])
    assert status == 200
    found = keys(body["data"])
    assert {"symbol", "transaction_type", "product", "last_price_u", "read_at"} <= found
    assert not {k for k in found if k.endswith(("_paise", "_pct"))}
    assert {k for k in found if k.endswith("_u")} <= set(privacy.json_never_hidden()), sorted(
        k for k in found if k.endswith("_u")
    )
    assert not {"qty_u", "avg_price_u", "price_u", "pnl_paise", "amount_paise"} & found


@scenario
async def test_the_family_and_the_owner_share_one_upstox_read(rig, fake):
    await rig.grant("7", [rig.upstox_tab])
    await rig.owner("upstox_book", parts=["positions"])
    await rig.family("7", "upstox_book", parts=["positions"])
    await rig.family("8", "upstox_book", parts=["positions"])  # not shared: refused, no read
    assert fake.count("positions") == 1


@scenario
async def test_three_family_reads_in_a_second_leave_the_owners_read_ok(rig, fake):
    """M4: a member cannot ask past the five seconds of memory (max_age 0 reads as 5), so family
    screens never spend the per-second reads of the owner's own tab."""
    await rig.grant("7", [rig.upstox_tab])
    for _ in range(3):
        status, body = await rig.family("7", "upstox_book", parts=["holdings"], max_age=0)
        assert status == 200 and body["data"]["holdings"]["state"] == "ok"
    owner = (await rig.owner("upstox_book", parts=["holdings"], max_age=0))["holdings"]
    assert owner["state"] == "ok" and owner["note"] is None
    assert fake.count("holdings") == 2  # one read for the family, then the owner's own


@scenario
async def test_the_family_summary_carries_only_the_shared_tabs_and_the_upstox_row_when_shared(rig):
    await rig.grant("7", [rig.tab["id"]])
    status, body = await rig.family("7", "summary")
    assert status == 200 and [e["tab"]["name"] for e in body["data"]["entries"]] == []
    await rig.owner("share_set", tab_id=rig.upstox_tab, viewer="7", level="view")
    status, body = await rig.family("7", "summary")
    (entry,) = body["data"]["entries"]
    assert entry["tab"]["system"] and entry["upstox"]["state"] == "ok"
    assert "qty_u" in keys(entry["upstox"]["holdings"])


@scenario
async def test_the_family_summary_of_a_member_with_no_privacy_row_still_reads_upstox(rig, caplog):
    """M1: quantities hidden (a member with no privacy row) take the part's note away; the row
    still says what was read, with no figure, and nothing is logged as a failure."""
    await rig.owner("share_set", tab_id=rig.upstox_tab, viewer="7", level="view")
    with caplog.at_level(logging.ERROR):
        status, body = await rig.family("7", "summary")
    assert status == 200
    (entry,) = body["data"]["entries"]
    row = entry["upstox"]
    assert row["state"] == "ok" and row["read_at"] == NOW
    assert row["holdings"] and all(h["name"] for h in row["holdings"])  # names stay
    found = keys(row)
    assert "note" not in found and not {k for k in found if k.endswith(("_paise", "_pct"))}
    assert {k for k in found if k.endswith("_u")} <= set(privacy.json_never_hidden())
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    owner = (await rig.owner("summary"))["entries"]
    assert next(e for e in owner if e["tab"]["system"])["upstox"]["state"] == "ok"


# ---- PJ: the journal's reads ---------------------------------------------------------------------
@scenario
async def test_trades_day_carries_read_at_and_shares_the_family_read(rig, fake):
    await rig.grant("7", [rig.upstox_tab])
    status, family = await rig.family("7", "upstox_book", parts=["trades"])
    status, journal = await rig.journal("trades_day")
    assert status == 200 and journal["status"] == "success"
    day = journal["data"]
    assert day["state"] == "ok" and day["read_at"] == NOW and len(day["data"]) == 2
    assert day["data"][0]["amount_paise"] == 1_399_998
    assert day["data"] == family["data"]["trades"]["data"]
    assert fake.count("day_trades") == 1  # one Upstox call for the two within 5 seconds


@scenario
async def test_positions_and_funds_are_the_readers_parts_with_read_at(rig):
    positions = (await rig.journal("positions"))[1]["data"]
    funds = (await rig.journal("funds"))[1]["data"]
    assert positions["read_at"] == NOW and positions["data"][0]["qty_u"] == -65_000_000
    assert funds["read_at"] == NOW and funds["data"]["available_paise"] == 12_500_050


@scenario
async def test_the_order_history_is_one_orders_rows_on_its_own_bucket(rig, fake):
    status, body = await rig.journal("order_history", order_id="261026000000002")
    data = body["data"]
    assert status == 200 and data["state"] == "ok" and data["read_at"] == NOW
    assert [r["status"] for r in data["rows"]] == ["put order req received", "trigger pending"]
    assert fake.queries[-1] == ("order_history", {"order_id": "261026000000002"})
    for bad in ("", "a b", "x" * 41, 7, None):
        assert (await rig.journal("order_history", order_id=bad))[0] == 400
    assert "order_history" in rig.svc.upstox.budget._buckets


@scenario
async def test_the_historical_trades_page_asks_upstox_for_that_range_and_page(rig, fake):
    status, body = await rig.journal(
        "historical_trades", segment="EQ", **{"from": 20260401, "to": 20260430, "page": 2}
    )
    data = body["data"]
    assert status == 200 and data["state"] == "ok" and data["read_at"] == NOW
    assert data["page"] == 2 and data["segment"] == "EQ" and len(data["rows"]) >= 1
    query = fake.queries[-1][1]
    assert (query["segment"], query["start_date"], query["end_date"], query["page_number"]) == (
        "EQ",
        "2026-04-01",
        "2026-04-30",
        "2",
    )
    bad = {"segment": "EQ", "from": 20260401, "to": 20260430}
    for change in ({"segment": "XX"}, {"from": 20260501}, {"to": 20269999}, {"page": 0},
                   {"page": "1"}, {"from": "2026-04-01"}):  # fmt: skip
        assert (await rig.journal("historical_trades", **{**bad, **change}))[0] == 400, change
    assert (
        "trades" in rig.svc.upstox.budget._buckets
        and "day_trades" not in rig.svc.upstox.budget._buckets
    )


@scenario
async def test_the_pnl_report_reads_one_part_of_one_year_and_segment(rig, fake):
    base = {"segment": "EQ", "fy": 2026}
    meta = (await rig.journal("pnl_report", part="metadata", **base))[1]["data"]
    assert meta["state"] == "ok" and meta["meta"]["trades_count"] >= 0 and meta["read_at"] == NOW
    data = (await rig.journal("pnl_report", part="data", page=1, **base))[1]["data"]
    assert data["state"] == "ok" and data["rows"][0]["pnl_paise"] == 125_000  # 26275.00 - 25025.00
    charges = (await rig.journal("pnl_report", part="charges", **base))[1]["data"]
    assert charges["charges"]["total_paise"] == 15_423
    assert fake.queries[-2][1]["financial_year"] == "2627"
    for change in ({"part": "all"}, {"segment": "MF"}, {"fy": 2020}, {"page_size": 0}):
        assert (await rig.journal("pnl_report", **{**base, "part": "data", **change}))[0] == 400
    for name in ("pnl_meta", "pnl", "charges"):
        assert name in rig.svc.upstox.budget._buckets


@scenario
async def test_the_benchmark_closes_come_from_the_stored_closes(rig):
    from sudarshan.portfolio.benchmark import BENCHMARK_OF, INDEX_KEYS

    key = next(k for k in INDEX_KEYS if BENCHMARK_OF[k] == "nifty50")
    rig.svc.store.write(OWNER, lambda tx: [tx.index_close_put(key, d, c, "eod")
                                           for d, c in ((20261001, 250_000_000), (20261002, 251_000_000),
                                                        (20261005, 252_000_000))])  # fmt: skip
    data = (
        await rig.journal("benchmark_closes", index="nifty50", **{"from": 20261002, "to": 20261005})
    )[1]["data"]
    assert data["read_at"] == NOW and data["closes"] == [
        {"day": 20261002, "close_u": 251_000_000},
        {"day": 20261005, "close_u": 252_000_000},
    ]
    none = (
        await rig.journal(
            "benchmark_closes", index="nifty500", **{"from": 20261001, "to": 20261005}
        )
    )[1]
    assert none["data"]["closes"] == []
    assert (await rig.journal("benchmark_closes", index="dow", **{"from": 1, "to": 2}))[0] == 400


@scenario
async def test_a_journal_read_leaves_the_owners_reserve(rig, fake):
    rig.svc.upstox.budget._of("day_trades")[2].tokens = 40.0
    held = (await rig.journal("trades_day"))[1]["data"]
    assert held["state"] == "unavailable" and held["data"] is None and fake.count("day_trades") == 0
    owner = (await rig.owner("upstox_book", parts=["positions"]))["positions"]
    assert owner["state"] == "ok"


@scenario
async def test_the_journal_never_sees_the_family_masks_and_the_family_never_the_journals_ops(rig):
    positions = (await rig.journal("positions"))[1]["data"]
    assert "qty_u" in keys(positions) and "pnl_paise" in keys(positions)
    await rig.grant("7", [rig.upstox_tab])
    assert (await rig.family("7", "positions"))[0] == 403
    assert (await rig.family("7", "trades_day"))[0] == 403


# ---- the one table of fields (Y24) ---------------------------------------------------------------
def test_the_viewer_mask_is_what_the_one_table_hides_for_pnl_and_quantities():
    table = privacy.load()
    assert set(table) == set(kinds.PRIVACY_CATEGORIES)
    assert serialize.VIEWER_MASK == table["pnl"] | table["qty"]
    assert privacy.floor(None) == privacy.mask_of(kinds.PRIVACY_CATEGORIES)
    assert privacy.floor(ALL) == frozenset()
    assert privacy.floor({**ALL, "qty": False}) == table["pnl"] | table["qty"]  # P&L needs qty
    assert privacy.floor({**ALL, "pnl": False}) == table["pnl"]
    assert privacy.floor({**ALL, "funds": False}) == table["funds"]


def test_value_and_invested_belong_to_quantities_and_the_live_price_is_never_hidden():
    table = privacy.load()
    for name in ("value_paise", "invested_paise", "cost_paise", "amount_paise"):
        assert serialize.hidden(name, table["qty"]), name
        assert not serialize.hidden(name, table["pnl"]) or name == "unused", name
    for name in ("pnl_paise", "realised_paise", "unrealised_paise", "overall_gain_paise", "xirr"):
        assert serialize.hidden(name, table["pnl"]), name
    for name in privacy.json_never_hidden():
        assert not serialize.hidden(name, serialize.VIEWER_MASK), name
    for name in ("symbol", "exchange", "transaction_type", "product", "kind", "read_at"):
        assert not serialize.hidden(name, serialize.VIEWER_MASK | table["funds"] | table["orders"])


def test_a_table_that_is_not_the_four_categories_is_refused(tmp_path):
    path = tmp_path / "f.json"
    path.write_text('{"categories": {"pnl": ["a"], "qty": ["b"], "funds": ["c"]}}')
    with pytest.raises(ValueError):
        privacy.load(path)
    path.write_text('{"categories": {"pnl": ["a"], "qty": [], "funds": ["c"], "orders": ["d"]}}')
    with pytest.raises(ValueError):
        privacy.load(path)
