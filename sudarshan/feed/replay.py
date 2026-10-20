"""Replay a recorded tick file onto the local ZMQ bus, to test consumers offline.

    python -m sudarshan.feed.replay /var/lib/sudarshan-feed/ticks/2026-10-21.jsonl.gz --speed 10

Publishes the recorded payloads unchanged on the same topics (frame 0 = instrument key), plus a
``__health__`` message with state "replay" every few seconds. It binds the port itself, so while
the live service holds 5581 use another port (--port 5591) and point the consumer there.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import time
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

import zmq

from .bus import HEALTH_TOPIC, Publisher
from .config import setup_logging

log = logging.getLogger("sudarshan.feed.replay")


def read_ticks(path: Path) -> Iterator[tuple[str, int, bytes]]:
    """Yield (key, ts_recv, payload) per line; a file cut off mid-write ends quietly."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        try:
            for raw in f:
                line = raw.strip()
                try:
                    tick = json.loads(line)
                    yield tick["key"], int(tick.get("ts_recv") or 0), line
                except (ValueError, LookupError, TypeError):
                    continue  # blank or damaged line
        except (EOFError, gzip.BadGzipFile, zlib.error):
            log.warning("%s ends mid-write (crash or still recording): replayed up to there", path)


def replay(path: Path, port: int = 5581, speed: float = 1.0, max_gap: float = 10.0,
           wait: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> int:  # fmt: skip
    """Publish every tick in ``path``; ``speed`` 1 = recorded pace, 0 = as fast as possible."""
    pub = Publisher(port)
    count, previous, last_health = 0, None, 0.0
    try:
        sleep(wait)  # ZMQ drops messages until a subscriber has connected
        for key, ts, payload in read_ticks(path):
            if speed > 0 and previous is not None and ts > previous:
                sleep(min((ts - previous) / 1000 / speed, max_gap))
            previous = ts
            pub.send(key, payload)
            count += 1
            if time.monotonic() - last_health >= 5:
                last_health = time.monotonic()
                health = {
                    "service": "sudarshan-feed",
                    "state": "replay",
                    "file": path.name,
                    "published": count,
                }
                pub.send(HEALTH_TOPIC, json.dumps(health).encode())
    finally:
        pub.close(linger_ms=2000)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.feed.replay")
    parser.add_argument("file", type=Path, help="a ticks/YYYY-MM-DD.jsonl.gz recording")
    parser.add_argument("--speed", type=float, default=1.0, help="1 = recorded pace, 0 = no pauses")
    parser.add_argument("--port", type=int, default=5581, help="ZMQ PUB port on 127.0.0.1")
    parser.add_argument("--max-gap", type=float, default=10.0, help="longest pause, seconds")
    parser.add_argument("--wait", type=float, default=1.0, help="seconds for subscribers to join")
    args = parser.parse_args(argv)
    setup_logging()
    try:
        count = replay(args.file, args.port, args.speed, args.max_gap, args.wait)
    except zmq.ZMQError as exc:
        log.error("cannot bind 127.0.0.1:%d (%s): try another --port", args.port, exc)
        return 2
    except OSError as exc:
        log.error("cannot read %s: %s", args.file, exc)
        return 2
    except KeyboardInterrupt:
        return 130
    log.info("replayed %d ticks from %s", count, args.file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
