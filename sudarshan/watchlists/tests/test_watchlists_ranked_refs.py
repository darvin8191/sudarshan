# ruff: noqa: F811
"""The ranked ref reader (M8.9, W22): the view, the members route's reader, and the token kept out
of what they answer. Fixtures and the fake Upstox are those of ``test_watchlists_ranked_bridge``."""

from __future__ import annotations

import json
import logging

import pytest
from test_watchlists_ranked_bridge import (  # noqa: F401  (fixtures)
    API,
    TOKEN,
    Reply,
    answer,
    client,
    data,
    get,
    ok,
    smart_row,
    upstox,
)

from sudarshan.watchlists import contract, ops
from sudarshan.watchlists.refs import ranked as ref
from sudarshan.watchlists.words import ranked as words


# -- the refs ----------------------------------------------------------------------------------------
def test_the_view_of_a_ranked_ref_is_locked_and_shares_the_ops_cache(client, upstox):
    upstox.fake.allow(ok())
    data(get(client))
    r = client.get(f"{API}/view?ref=ranked:futures:INDEX:OI_GAINERS", headers=client.headers)
    v = data(r)
    assert v["kind"] == "ranked" and v["locked"] is True and v["version"] == "0"
    assert v["name"] == "Index futures · OI gainers" and v["total"] == 20 and v["sections"] == []
    assert set(v) == {f.name for f in contract.record("View")}
    assert len(upstox.fake.calls) == 1


def test_a_ranked_ref_that_is_not_offered_is_invalid(client, upstox):
    for bad in ("ranked:futures:COMMODITY:PREMIUM", "ranked:mtf:INDEX:TOP_TRADED", "ranked:x"):
        r = client.get(f"{API}/view?ref={bad}", headers=client.headers)
        assert r.status_code == 400 and r.get_json()["field"] == "ref", bad
    assert upstox.fake.calls == []


def test_members_are_the_contracts_with_the_keys_left_out_and_no_flag_or_note(upstox):
    upstox.fake.allow(Reply(200, answer(3, extra=[smart_row(0, key="NSE_FO|99999")])))
    got = ref.members("futures:INDEX:OI_GAINERS", "owner")
    assert [m["symbol"] for m in got["members"]] == ["FUT0" + "25NOVFUT", "FUT1" + "25NOVFUT",
                                                    "FUT2" + "25NOVFUT"]  # fmt: skip
    assert got["valid_until"] is None and got["ref"] == "ranked:futures:INDEX:OI_GAINERS"
    assert got["left_out"] == [{"token": "NSE_FO|99999", "reason": words.NOT_IN_MASTER}]
    assert set(got) == {f.name for f in contract.record("Members")}
    again = ref.members("futures:INDEX:OI_GAINERS", "owner")  # inside the op's cache
    assert again["members"] == got["members"] and len(upstox.fake.calls) == 1


def test_members_are_refused_in_words_with_no_sign_in_never_empty(upstox):
    upstox.token["now"] = None
    with pytest.raises(ops.OpError) as e:
        ref.members("futures:INDEX:OI_GAINERS", "owner")
    assert e.value.code == "signin" and e.value.message == words.SIGNIN_TODAY
    upstox.token["now"] = TOKEN
    upstox.fake.allow(Reply(500))
    with pytest.raises(ops.OpError) as e:
        ref.members("futures:INDEX:OI_GAINERS", "owner")
    assert e.value.code == "failed"


def test_the_ref_readers_put_no_token_in_an_answer_or_a_log_line(client, upstox, caplog):
    caplog.set_level(logging.DEBUG)
    upstox.fake.allow(ok(), Reply(401))
    seen = [json.dumps(ref.view("futures:INDEX:OI_GAINERS", "owner"))]
    upstox.clock.advance(61)
    seen.append(json.dumps(ref.view("futures:INDEX:OI_GAINERS", "owner")))
    logged = " ".join(r.getMessage() + repr(r.args) + str(r.exc_text) for r in caplog.records)
    assert all(TOKEN not in text for text in seen + [logged])
