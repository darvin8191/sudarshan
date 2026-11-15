"""T2: what changed (diff.py), hand-checked case by case."""

from __future__ import annotations

from datetime import date

from sudarshan.signals import contract as c
from sudarshan.signals import diff

AT = 1794800712250  # Mon 16 Nov 2026 09:15:12 IST, an invented document's time
NIFTY = ("regime", "NIFTY")


def reading(module="regime", instrument="NIFTY", **over) -> c.ReadingIn:
    fields = {
        "module": module, "instrument": instrument, "state": "DISTRIBUTION",
        "label": "FII distribution", "bias": "bear", "as_of_ms": AT - 10_000, "horizon": "swing",
        "quality": "ok", "score": -0.4, "confidence": 0.7, "since_ms": AT - 86_400_000,
    }  # fmt: skip
    fields.update(over)
    return c.ReadingIn(**fields)


def snap(*readings: c.ReadingIn, at=AT, seq=1, written=None, stopping=False) -> c.Snapshot:
    written = at if written is None else written
    diff_ms = at - written
    clock = "ahead" if diff_ms > c.CLOCK_AHEAD_MS else "ok"
    return c.Snapshot(run="r1", seq=seq, at=at, next_by=at + 300_000, stopping=stopping,
                      readings=tuple(readings), alerts=(), clock=clock, clock_diff_ms=diff_ms,
                      written_ms=written, size=100)  # fmt: skip


def kinds(outcome: diff.Outcome) -> list[tuple[str, str, str | None, str]]:
    return [(ch.kind, ch.reading["module"], (ch.before or {}).get("state"), ch.reading["state"])
            for ch in outcome.changes]  # fmt: skip


def test_the_first_document_is_a_first_reading_of_each_pair():
    out = diff.apply({}, snap(reading(), reading("gap_bias", state="GAP_UP", bias="bull")), AT)
    assert kinds(out) == [("first", "regime", None, "DISTRIBUTION"),
                          ("first", "gap_bias", None, "GAP_UP")]  # fmt: skip
    assert out.altered and set(out.current) == {NIFTY, ("gap_bias", "NIFTY")}
    first = out.changes[0]
    assert first.at_ms == AT and first.as_row()["from_state"] is None
    assert out.current[NIFTY].published_ms == AT and out.current[NIFTY].doc_seq == 1


def test_another_state_another_bias_and_no_data_are_changes_and_a_score_alone_is_not():
    prior = diff.apply({}, snap(reading()), AT).current
    same = diff.apply(prior, snap(reading(score=-0.9, confidence=0.95), seq=2, at=AT + 1), AT + 1)
    assert kinds(same) == [] and same.altered  # the screens update; the timeline does not
    state = diff.apply(prior, snap(reading(state="ACCUMULATION", label="FII accumulation",
                                           bias="bull"), seq=2), AT)  # fmt: skip
    assert kinds(state) == [("state", "regime", "DISTRIBUTION", "ACCUMULATION")]
    row = state.changes[0].as_row()
    assert (row["from_label"], row["from_bias"], row["to_label"], row["bias"]) == (
        "FII distribution", "bear", "FII accumulation", "bull")  # fmt: skip
    bias = diff.apply(prior, snap(reading(bias="neutral"), seq=2), AT)
    assert kinds(bias) == [("bias", "regime", "DISTRIBUTION", "DISTRIBUTION")]
    nodata = diff.apply(prior, snap(reading(quality="no_data"), seq=2), AT)
    assert kinds(nodata) == [("no_data", "regime", "DISTRIBUTION", "DISTRIBUTION")]
    back = diff.apply(nodata.current, snap(reading(quality="ok"), seq=3), AT)
    assert kinds(back) == [] and back.altered  # coming back from no data is not a change
    still = diff.apply(nodata.current, snap(reading(quality="no_data"), seq=3), AT)
    assert kinds(still) == [] and not still.altered


def test_a_state_change_wins_over_a_bias_change_and_no_data():
    prior = diff.apply({}, snap(reading()), AT).current
    out = diff.apply(prior, snap(reading(state="RANGE", bias="neutral", quality="no_data")), AT)
    assert [ch.kind for ch in out.changes] == ["state"]


def test_a_reading_missing_from_a_document_leaves_and_comes_back_as_a_first_reading():
    prior = diff.apply({}, snap(reading(), reading("gap_bias")), AT).current
    gone = diff.apply(prior, snap(reading(), seq=2), AT)
    assert kinds(gone) == [] and gone.altered and set(gone.current) == {NIFTY}
    again = diff.apply(gone.current, snap(reading(), reading("gap_bias"), seq=3), AT)
    assert kinds(again) == [("first", "gap_bias", None, "DISTRIBUTION")]


def test_a_lapse_by_the_clock_is_one_change_and_the_next_mornings_reading_is_a_first():
    lapse = AT + 600_000
    out = diff.apply({}, snap(reading("gap_bias", valid_until_ms=lapse)), AT)
    assert diff.next_lapse_ms(out.current.values()) == lapse
    current, changes = diff.lapses(out.current, lapse - 1)
    assert changes == [] and current == out.current
    current, changes = diff.lapses(out.current, lapse)
    assert [(ch.kind, ch.at_ms) for ch in changes] == [("lapsed", lapse)]
    assert current[("gap_bias", "NIFTY")].lapsed and diff.next_lapse_ms(current.values()) is None
    assert diff.lapses(current, lapse + 5)[1] == []  # once
    # the same reading again, still lapsed: nothing new
    again = diff.apply(current, snap(reading("gap_bias", valid_until_ms=lapse), seq=2), lapse + 9)
    assert kinds(again) == [] and not again.altered
    # the next morning: a live reading with the same state is a first reading, not nothing
    tomorrow = AT + 86_400_000
    fresh = diff.apply(current, snap(reading("gap_bias", valid_until_ms=tomorrow + 600_000),
                                     at=tomorrow, seq=3), tomorrow)  # fmt: skip
    assert [ch.kind for ch in fresh.changes] == ["first"]
    assert fresh.changes[0].as_row()["from_state"] == "DISTRIBUTION"


def test_a_reading_that_arrives_lapsed_is_one_lapsed_change():
    out = diff.apply({}, snap(reading("gap_bias", valid_until_ms=AT - 5)), AT)
    assert [(ch.kind, ch.at_ms) for ch in out.changes] == [("lapsed", AT - 5)]
    assert out.current[("gap_bias", "NIFTY")].lapsed


def test_a_differing_clock_moves_the_lapse_onto_the_servers_clock():
    """TRINETRA's clock ten minutes ahead: its 09:15 is the server's 09:05."""
    ahead = 600_000
    s = snap(reading("gap_bias", valid_until_ms=AT + 900_000), written=AT - ahead)
    out = diff.apply({}, s, AT - ahead)
    entry = out.current[("gap_bias", "NIFTY")]
    assert entry.offset_ms == -ahead and entry.lapse_ms == AT + 300_000
    assert diff.lapses(out.current, AT + 300_000)[1][0].at_ms == AT + 300_000


def test_sessions_count_trading_days_from_since_to_today():
    trading = lambda day: day.weekday() < 5 and day != date(2026, 11, 18)  # noqa: E731
    wed = 1794335400000  # Wed 11 Nov 2026 00:00 IST
    fri = wed + 2 * 86_400_000 + 36_000_000
    assert diff.ist_day(wed) == date(2026, 11, 11) and diff.ist_day(wed - 1) == date(2026, 11, 10)
    assert diff.sessions(wed, fri, trading) == 3
    assert diff.sessions(wed, wed + 3_600_000, trading) == 1
    sunday = wed + 4 * 86_400_000
    assert diff.sessions(wed, sunday, trading) == 3  # the weekend adds none
    assert diff.sessions(wed, wed + 7 * 86_400_000, trading) == 5  # 18 Nov is a holiday here
    assert diff.sessions(None, fri, trading) is None
    assert diff.sessions(sunday, sunday + 3_600_000, trading) == 1  # begun, though not traded
