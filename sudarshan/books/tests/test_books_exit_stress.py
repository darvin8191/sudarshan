"""The guard's promises under random interleavings on real threads, the gthread shape (M6.9).

Adopted from the independent review's test_review_stress.py, for this round's guard. Twelve threads
send single exits and Exit alls at random, in both books, for four positions; at every step, from
inside the critical regions:

- no two exits for one position are sent at once, in one book;
- no exit is sent while an Exit all closes in that book;
- an Exit all sends nothing for a position that was busy (an exit for it being placed, or placed
  lately) when it took the book;
- every claim is let go in the end.
"""

from __future__ import annotations

import random
import threading
import time

from sudarshan.books import exit_guard
from sudarshan.books.exit_guard import ExitsInFlight

POSITIONS = [("NFO", f"NIFTY27OCT26{25000 + 50 * i}CE", "MIS") for i in range(4)]


def test_random_interleavings_keep_the_promises(monkeypatch):
    guard = ExitsInFlight()
    monkeypatch.setattr(exit_guard, "EXITS", guard)
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 0.02)  # notes come and go in the run
    state_lock = threading.Lock()
    inside = {book: {"all": 0, "exits": {}} for book in ("live", "sandbox")}
    violations: list[str] = []
    counts = {"exit": 0, "exit409": 0, "all": 0, "all409": 0, "skipped": 0}
    count_lock = threading.Lock()  # += on a shared dict is not atomic on real threads
    stop = time.monotonic() + 4.0

    def count(name, n=1):
        with count_lock:
            counts[name] += n

    def refused(name):
        # A refused caller backs off for a moment, as a real request does. Without it, eleven
        # threads retrying in a tight loop starve the one holding the claim of the interpreter
        # lock, and on a slow runner too few exits finish in the window to test anything
        # (CI on b5ae8c9: 78 exits against 1,346,328 refusals).
        count(name)
        time.sleep(0.0001 + random.random() * 0.0004)

    def app_exit(book, position):
        with guard.one(book, position) as busy:
            if busy:
                refused("exit409")
                return
            with state_lock:
                here = inside[book]
                if here["all"]:
                    violations.append(f"exit sent while Exit all closes ({book})")
                if here["exits"].get(position):
                    violations.append(f"two exits for one position ({book})")
                here["exits"][position] = here["exits"].get(position, 0) + 1
            time.sleep(random.random() * 0.003)  # the broker call
            with state_lock:
                here["exits"][position] -= 1
            if book == "live":
                guard.placed(book, position)  # before the claim is let go
            count("exit")

    def exit_all(book):
        with guard.every(book) as busy:
            if busy:
                refused("all409")
                return
            held = guard.busy(book)  # what the close-all skips
            with state_lock:
                here = inside[book]
                if here["all"]:
                    violations.append(f"two Exit alls closing ({book})")
                sending = [
                    position
                    for position, count in here["exits"].items()
                    if count and position not in held
                ]
                if sending:
                    violations.append(f"Exit all would send {sending} while it is placed ({book})")
                here["all"] += 1
            count("skipped", len(held))
            time.sleep(random.random() * 0.003)
            with state_lock:
                here["all"] -= 1
            count("all")

    def worker(seed):
        rnd = random.Random(seed)
        while time.monotonic() < stop:
            book = rnd.choice(["live", "live", "sandbox"])
            if rnd.random() < 0.3:
                exit_all(book)
            else:
                app_exit(book, rnd.choice(POSITIONS))

    threads = [threading.Thread(target=worker, args=(seed,)) for seed in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not any(thread.is_alive() for thread in threads)
    assert violations == [], violations[:10]
    assert len(guard) == 0
    assert counts["exit"] > 100 and counts["all"] > 20 and counts["exit409"] > 0, counts
