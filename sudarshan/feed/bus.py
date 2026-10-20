"""Local fan-out bus: one ZMQ PUB that BINDS 127.0.0.1:<port>, many local SUBs connect.

This is SUDARSHAN's own bus (default 5581), separate from OpenAlgo's 5555 market-data bus, where
the opposite rule holds (the proxy's SUB binds, publishers connect). Here exactly one process
publishes, so the publisher binds; a taken port is a startup error, never a silent port change.

Frames: [topic = instrument key (utf-8), payload = compact JSON]. ZMQ subscriptions match by
prefix ("NSE_INDEX|Nifty 50" also matches "NSE_INDEX|Nifty 500"), so consumers compare frame 0
exactly. Topic ``__health__`` carries the /health document every few seconds.
"""

from __future__ import annotations

import zmq

HEALTH_TOPIC = "__health__"


class Publisher:
    def __init__(self, port: int):
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.PUB)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.setsockopt(zmq.SNDHWM, 100_000)  # per subscriber; a slow one loses ticks, not us
        try:
            self.sock.bind(f"tcp://127.0.0.1:{port or '*'}")  # 0 = any free port (tests)
        except zmq.ZMQError:
            self.close()
            raise
        self.endpoint = self.sock.getsockopt_string(zmq.LAST_ENDPOINT)
        self.dropped = 0

    def send(self, topic: str, payload: bytes) -> None:
        try:
            self.sock.send_multipart((topic.encode(), payload), zmq.NOBLOCK)
        except zmq.Again:
            self.dropped += 1

    def close(self, linger_ms: int = 0) -> None:
        """The live service drops what is unsent; the replayer lingers so its tail arrives."""
        self.sock.close(linger_ms)
        self.ctx.term()
