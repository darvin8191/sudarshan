"""T2: the words (words.py), hand-worked, and the golden vectors a page's twin can share.

``vectors/words.json`` (freshness over a clock table, notes, ages, times, the language picks) is
checked here against words.py; it is the file the phone's own freshness code is tested against,
as ``test/risk/vectors.json`` is for the trailing stop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.signals import contract as c
from sudarshan.signals import words

AT = 1794800712250  # Mon 16 Nov 2026 09:15:12.250 IST (invented)
MIDNIGHT = 1794853800000  # Tue 17 Nov 2026 00:00 IST
MIN = 60_000
FRESH = words.Last(published_ms=AT, fresh_until_ms=AT + 360_000)
VECTORS = json.loads((Path(__file__).parent / "vectors" / "words.json").read_text("utf-8"))


def test_never():
    got = words.freshness(None, AT)
    assert got == {"status": "never", "words": c.WORDS["never"], "published_ms": None,
                   "fresh_until_ms": None, "colour": False, "clock_ok": True,
                   "changes_at_ms": None}  # fmt: skip


def test_fresh_until_next_by_and_a_minute():
    got = words.freshness(FRESH, AT + 1_000)
    assert got["status"] == "fresh" and got["words"] == "TRINETRA · updated 09:15:12"
    assert got["colour"] and got["changes_at_ms"] == AT + 360_000
    assert words.freshness(FRESH, AT + 359_999)["status"] == "fresh"


def test_stale_says_since_when_and_how_old():
    got = words.freshness(FRESH, AT + 360_000)
    assert got["status"] == "stale"
    assert got["words"] == (
        "TRINETRA has not published since 09:15. This is its last reading, 6 minutes old."
    )
    assert got["colour"] and got["changes_at_ms"] == AT + 7 * MIN  # the age's next minute
    late = words.freshness(FRESH, AT + 30 * MIN - 1)
    assert late["colour"] and late["changes_at_ms"] == AT + 30 * MIN
    grey = words.freshness(FRESH, AT + 30 * MIN)
    assert not grey["colour"], "no colour of bias past 30 minutes (mutation target)"
    assert grey["words"].endswith("30 minutes old.") and grey["changes_at_ms"] == AT + 31 * MIN
    hours = words.freshness(FRESH, AT + 125 * MIN)
    assert hours["words"].endswith("2 hours old.") and hours["changes_at_ms"] == AT + 180 * MIN


def test_stale_from_another_day_names_the_day():
    got = words.freshness(FRESH, AT + 86_400_000 + 3_600_000)
    assert got["words"] == (
        "TRINETRA has not published since 16 Nov 09:15. This is its last reading, 25 hours old."
    )
    assert got["changes_at_ms"] == AT + 26 * 3_600_000
    assert words.freshness(FRESH, AT + 3 * 86_400_000)["words"].endswith("3 days old.")


def test_midnight_is_a_moment_of_change_for_todays_time():
    night = words.Last(published_ms=MIDNIGHT - 2 * MIN, fresh_until_ms=MIDNIGHT + 3_600_000)
    got = words.freshness(night, MIDNIGHT - MIN)
    assert got["status"] == "fresh" and got["changes_at_ms"] == MIDNIGHT
    assert words.freshness(night, MIDNIGHT)["words"] == "TRINETRA · updated 16 Nov 23:58:00"


def test_stopped_by_its_operators():
    stopped = words.Last(published_ms=AT, fresh_until_ms=AT + 360_000, stopping=True)
    got = words.freshness(stopped, AT + MIN)
    assert got["status"] == "stopped"
    assert got["words"] == "TRINETRA was stopped by its operators at 09:15."
    assert got["colour"] and got["changes_at_ms"] == AT + 30 * MIN
    later = words.freshness(stopped, AT + 40 * MIN)
    assert not later["colour"] and later["changes_at_ms"] == MIDNIGHT


def test_the_servers_clock_decides_never_the_documents():
    """Mutation target: freshness from ``at`` instead of the server's clock. A document whose
    clock is ten minutes ahead is fresh by the time it was written plus its promise."""
    snap_ahead = words.Last(published_ms=AT - 10 * MIN, fresh_until_ms=AT - 10 * MIN + 360_000,
                            clock_ok=False, clock_diff_ms=10 * MIN)  # fmt: skip
    assert words.freshness(snap_ahead, AT - 5 * MIN)["status"] == "fresh"
    assert words.freshness(snap_ahead, AT - 4 * MIN)["status"] == "stale"
    assert words.freshness(snap_ahead, AT)["clock_ok"] is False


def test_notes():
    assert words.notes(FRESH, refused_today=0, gaps_today=0, newer_format=False) == []
    got = words.notes(FRESH, refused_today=3, gaps_today=2, newer_format=True)
    assert got == [
        {"key": "refused", "count": 3, "words": "3 files from TRINETRA could not be read today. "
         "Its last good reading is shown."},
        {"key": "newer_format", "count": None, "words": c.WORDS["newer_format"]},
        {"key": "gaps", "count": 2, "words": "2 documents from TRINETRA did not arrive today."},
    ]  # fmt: skip
    one = words.notes(None, refused_today=1, gaps_today=1, newer_format=False)
    assert [n["words"] for n in one] == [
        "1 file from TRINETRA could not be read today. None of its readings can be shown yet.",
        "1 document from TRINETRA did not arrive today.",
    ]
    skewed = words.Last(AT, AT + 360_000, clock_ok=False, clock_diff_ms=-10 * MIN)
    assert words.notes(skewed, refused_today=0, gaps_today=0, newer_format=False) == [
        {"key": "clock", "count": None, "words": "TRINETRA's clock and the server's differ by "
         "10 minutes; its times may be off."}
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("delta", "said"),
    [(0, "under a minute"), (59_999, "under a minute"), (60_000, "1 minute"),
     (119_999, "1 minute"), (120_000, "2 minutes"), (120 * MIN - 1, "119 minutes"),
     (120 * MIN, "2 hours"), (48 * 3_600_000 - 1, "47 hours"), (48 * 3_600_000, "2 days"),
     (-5, "under a minute")],
)  # fmt: skip
def test_ages(delta, said):
    assert words.age_words(delta) == said


def test_times():
    assert words.clock_text(AT, AT) == "09:15"
    assert words.clock_text(AT, AT, seconds=True) == "09:15:12"
    assert words.clock_text(AT, AT + 86_400_000) == "16 Nov 09:15"
    assert words.clock_text(MIDNIGHT, MIDNIGHT) == "00:00"


def test_card_words():
    r = {"quality": "degraded", "valid_until_ms": AT + 3_600_000}
    assert words.card_words(r, False, AT) == [c.WORDS["degraded"]]
    assert words.card_words({**r, "quality": "no_data"}, True, AT) == [
        c.WORDS["no_data"], "Lapsed 10:15"]  # fmt: skip
    assert words.card_words({"quality": "ok", "valid_until_ms": None}, True, AT) == []


def test_the_owners_language_and_the_labelled_fallback():
    why = {"en": "E text.", "hi": "H text.", "gu": None}
    assert words.pick_why(why, "hi") == ("H text.", "hi", "")
    assert words.pick_why(why, "gu") == (
        "E text.", "en", "TRINETRA did not write this in Gujarati; shown in English.")  # fmt: skip
    assert words.pick_why({"gu": "G text."}, "en") == (
        "G text.", "gu", "TRINETRA did not write this in English; shown in Gujarati.")  # fmt: skip
    assert words.pick_why({}, "en") == (None, None, c.WORDS["why_none"])
    assert words.pick_why(None, "gu") == (None, None, c.WORDS["why_none"])


def test_relay_items_carry_fields_and_never_markup():
    change = {
        "id": 7,
        "at_ms": AT,
        "module": "regime",
        "instrument": "NIFTY",
        "kind": "state",
        "from_label": "FII accumulation",
        "to_label": "FII distribution",
        "bias": "bear",
        "confidence": 0.71,
        "why": {"en": "<b>x</b> " + "w" * 600, "hi": None, "gu": None},
    }
    got = words.relay_change(change, lang="gu", night=True)
    assert got["id"] == "c-7" and got["module_name"] == "DRISHTI" and got["night"] is True
    assert got["why_lang"] == "en" and len(got["why"]) == 300 and got["why"].endswith("...")
    assert got["why"].startswith("<b>x</b>")  # data, passed as plain text; the desk quotes it
    assert set(got) == {f.name for f in c.record("RelayChange")}
    alert = {"id": "a-1", "at_ms": AT, "module": "breadth", "instrument": None, "level": "act",
             "title": "Title", "text": "t" * 700, "lang": "hi"}  # fmt: skip
    got = words.relay_alert(alert, night=False)
    assert got["id"] == "a-a-1" and got["module_name"] == "Other" and len(got["text"]) == 300
    assert set(got) == {f.name for f in c.record("RelayAlert")}


def test_the_golden_vectors_hold():
    for case in VECTORS["freshness"]:
        last = words.Last(**case["last"]) if case["last"] else None
        assert words.freshness(last, case["now_ms"]) == case["expect"], case["name"]
    for case in VECTORS["notes"]:
        last = words.Last(**case["last"]) if case["last"] else None
        got = words.notes(
            last,
            refused_today=case["refused_today"],
            gaps_today=case["gaps_today"],
            newer_format=case["newer_format"],
        )
        assert got == case["expect"], case["name"]
    for case in VECTORS["ages"]:
        assert words.age_words(case["delta_ms"]) == case["expect"]
    for case in VECTORS["times"]:
        got = words.clock_text(case["ms"], case["now_ms"], seconds=case["seconds"])
        assert got == case["expect"]
    for case in VECTORS["why"]:
        assert list(words.pick_why(case["why"], case["lang"])) == case["expect"]
