"""SUDARSHAN M6.9: an exit one test notes is not "placed just now" in the next.

sudarshan/books/exit_guard.py keeps each exit placed in the last 15 s, and an Exit all in that
time sends nothing for those positions. Upstox's close-all notes its exits, so its tests in
test_gthread_review_brokers.py left notes in the suite's one interpreter, and a later test's Exit
all found its position busy. test/conftest.py gives each test fresh notes. The two tests below run
in this order, and the second fails without that.
"""

from sudarshan.books import exit_guard


def test_an_exit_all_notes_the_exit_it_placed():
    exit_guard.exit_placed("live", "NSE", "SBIN", "MIS")  # as Upstox's close-all does
    assert exit_guard.EXITS.placed_lately("live") == {("NSE", "SBIN", "MIS")}


def test_the_next_test_s_exit_all_finds_nothing_busy():
    def close_all():
        return {"busy": exit_guard.busy_positions("live")}, 200

    assert exit_guard.close_all_once("live", close_all) == ({"busy": {}}, 200)
