"""M18.x: what the notice desk ships with, and two senders on one notice.

The pace tests in ``test_alerts_notices.py`` change ``NOTICES_PER_MINUTE`` and the gap (an autouse
fixture lets every other test run unpaced), so the numbers the service really starts with were
pinned by nothing: a mutant that made it two a minute passed every test. This module has no such
fixture. It also holds the look-then-claim race of ``NoticeDesk.sweep``: another sender may claim a
notice between this desk's look and its claim, and then the notice is not this desk's to send.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from alerts_fakes import ist
from test_alerts_notices import T0, Clock, Ready, Send, desk, note

from sudarshan.alerts import notices, outbox
from sudarshan.alerts.notices import NoticePace
from sudarshan.alerts.store import Store

# ---- the numbers the service ships with --------------------------------------------------------


def test_the_service_ships_one_notice_a_minute_and_the_order_alerts_own_gap():
    assert notices.NOTICES_PER_MINUTE == 1
    assert notices.MINUTE_S == 60.0
    assert outbox.MIN_GAP_S > 0


def test_the_pace_as_shipped_lets_a_second_notice_start_a_minute_after_the_first_and_not_before():
    pace = NoticePace()
    assert pace.wait(1000.0) == 0.0
    pace.took(1000.0)
    after_the_gap = 1000.0 + outbox.MIN_GAP_S + 0.5
    assert pace.wait(after_the_gap) == 1060.0 - after_the_gap  # the gap is over, the minute is not
    assert pace.wait(1059.9) > 0
    assert pace.wait(1060.0) == 0.0


# ---- two senders on one notice ----------------------------------------------------------------

LATER = ist(2026, 10, 24, 19, 5)


def two_notices(tmp_path):
    spool = tmp_path / "events.jsonl"
    note(spool, "installed", "i1", run="run-1", tag="v0.3.3", backup="yes")
    note(spool, "installed", "i2", LATER, run="run-2", tag="v0.3.4", backup="yes")
    return Store(tmp_path / "alerts.db")


async def look_and_claim(d, clock):
    await d.sweep()  # the first look: the run has to settle
    clock.advance(notices.SETTLE_S + 1)
    return await d.sweep()


def test_a_notice_another_sender_claimed_first_is_not_sent_again(tmp_path):
    clock = Clock(LATER + timedelta(seconds=5))
    store = two_notices(tmp_path)
    d, send = desk(tmp_path, clock, store=store, ready=Ready())
    real = store.claim_notices

    def the_other_sender_was_first(ids, now, stale_after):
        real(ids, now, stale_after)  # its claim lands between this desk's look and its claim
        return real(ids, now, stale_after)

    store.claim_notices = the_other_sender_was_first
    assert asyncio.run(look_and_claim(d, clock)) == notices.POLL_S
    assert send.texts == []  # nothing goes out twice
    assert (d.stats["messages"], d.stats["notices"]) == (0, 0)


def test_only_what_this_desk_claimed_is_in_its_message(tmp_path):
    clock = Clock(LATER + timedelta(seconds=5))
    store = two_notices(tmp_path)
    send = Send()
    d, _ = desk(tmp_path, clock, send=send, store=store)
    real = store.claim_notices

    def the_other_sender_took_the_first(ids, now, stale_after):
        real(ids[:1], now, stale_after)
        return real(ids, now, stale_after)

    store.claim_notices = the_other_sender_took_the_first
    asyncio.run(look_and_claim(d, clock))

    (text,) = send.texts
    assert "v0.3.4 installed" in text and "v0.3.3" not in text  # the other sender tells the first
    assert (d.stats["messages"], d.stats["notices"]) == (1, 1)
    clock.advance(30)
    asyncio.run(d.sweep())
    assert len(send.texts) == 1  # and the claimed one is not picked up again meanwhile
