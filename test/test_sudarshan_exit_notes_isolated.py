"""SUDARSHAN M6.9: an exit one test notes is not "placed just now" in the next.

sudarshan/books/exit_guard.py keeps each exit placed in the last 15 s, and an Exit all in that
time reads the position book until it shows them. Upstox's close-all notes its exits, so its
tests in test_gthread_review_brokers.py left notes in the suite's one interpreter, and
test_gthread_strategy_busy.py's Exit all a few seconds later read the book until it gave up
and answered 409 instead of 429. test/conftest.py gives each test fresh notes. The two tests
below run in this order, and the second fails without that.
"""

from sudarshan.books import exit_guard


def test_an_exit_all_notes_the_exit_it_placed():
    exit_guard.exit_placed("live", "NSE", "SBIN", "MIS")  # as Upstox's close-all does
    assert exit_guard.EXITS.placed_lately("live") == {("NSE", "SBIN", "MIS")}


def test_the_next_test_s_exit_all_does_not_read_the_book_for_it():
    reads = []

    def still_open(positions):
        reads.append(positions)
        return positions  # a book that never shows them closed

    answer = exit_guard.close_all_once(
        "live", lambda: ({"status": "success"}, 200), still_open=still_open
    )
    assert (answer, reads) == (({"status": "success"}, 200), [])
