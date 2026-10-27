"""Orders paced at 5 a second (M5.14): with the shipped UPSTOX_ORDER_MAX_PER_SECOND=5, twenty
senders at once never have more than 5 Upstox order calls booked in any rolling second, under
real threads (as the gthread worker runs them) and under eventlet's green threads, whose hub
keeps running while they wait.

Each case runs in a subprocess: the limiter reads the setting when it is imported, and eventlet's
monkey_patch cannot be undone. The limiter itself is upstream's (broker/upstox/api/rate_limiter);
what is proven here is the shipped figure, on both workers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SENDERS = 20

SCRIPT = textwrap.dedent(
    """
    import json, os, sys, time
    if sys.argv[1] == "eventlet":
        import eventlet
        eventlet.monkey_patch()
    os.environ["UPSTOX_ORDER_MAX_PER_SECOND"] = sys.argv[2]
    sys.path.insert(0, sys.argv[3])
    import threading
    from broker.upstox.api import rate_limiter

    limiter = rate_limiter.ORDER_LIMITER
    sent, ticks = [], [0]
    done = threading.Event()

    def ticker():  # the hub (or the interpreter) keeps running while the senders wait
        while not done.is_set():
            ticks[0] += 1
            time.sleep(0.05)

    def send():
        rate_limiter.apply_rate_limit("order")
        sent.append(time.time())

    beat = threading.Thread(target=ticker)
    beat.start()
    started = time.time()
    senders = [threading.Thread(target=send) for _ in range(int(sys.argv[4]))]
    for thread in senders:
        thread.start()
    for thread in senders:
        thread.join(60)
    done.set()
    beat.join(5)
    booked = list(limiter._reserved)  # the slots the limiter booked, exactly
    print(json.dumps({"per_second": limiter.max_per_second, "booked": booked, "sent": sent,
                      "started": started, "ticks": ticks[0]}))
    """
)


def _run(worker: str, per_second: str = "5") -> dict:
    if worker == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    env = {key: value for key, value in os.environ.items() if not key.startswith("UPSTOX_")}
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT, worker, per_second, str(REPO), str(SENDERS)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _most_in_a_second(times: list[float]) -> int:
    times = sorted(times)
    most = 0
    for i, start in enumerate(times):
        most = max(most, sum(1 for later in times[i:] if later - start < 1.0 - 1e-6))
    return most


@pytest.mark.parametrize("worker", ["threads", "eventlet"])
def test_twenty_senders_get_no_more_than_five_order_calls_in_any_second(worker):
    seen = _run(worker)
    assert seen["per_second"] == 5
    assert len(seen["booked"]) == len(seen["sent"]) == SENDERS
    assert _most_in_a_second(seen["booked"]) == 5  # the cap, reached and never passed
    # Sent no sooner than booked: the twentieth call waits three seconds past the first.
    assert max(seen["sent"]) - seen["started"] >= 3.0 - 0.05
    assert seen["ticks"] >= 40  # about 3 s of 50 ms beats: nothing blocked the waiting


def test_without_the_setting_upstream_s_eight_a_second_applies():
    """The figure comes from the setting the deploy writes; upstream's own default is 8."""
    env_free = _run("threads", per_second="")
    assert env_free["per_second"] == 8
