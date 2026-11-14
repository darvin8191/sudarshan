"""A cancel that stops a request while it connects leaves no socket open (``safe_http``).

Under httpx 0.28 (httpcore 1.0.9, anyio 4.14) a request cancelled in the instant between anyio's
connect and its return, or during the TLS handshake, keeps its socket open after the client is
closed: the event loop's transport holds it, and the peer waits on it. The family bot's
descriptor test met it on a CI machine (fd 19 and 20: the bot's socket and the fake Telegram's,
left by the stop's cancel of the long poll). Each sweep runs in a child process, so a socket a
sweep leaves open goes with it. The first two tests show the defect with httpx's own transport,
so the others cannot pass by finding nothing; if one of them fails, a later httpx, httpcore or
anyio no longer loses the socket, and ``safe_http`` can go.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx

from sudarshan.family import digest, push, safe_http, telegram_client
from sudarshan.family.config import Settings

ROOT = Path(__file__).resolve().parents[3]

#: The child: a server that never answers and closes at the client's end of stream, then one
#: request per round, cancelled after ``steps`` turns of the loop (a sweep across the connect) or
#: after a pause (inside the TLS handshake, which the server never answers); prints the rounds
#: whose sockets are still open half a second after the client was closed.
CHILD = r"""
import asyncio, gc, json, os, sys
sys.path.insert(0, sys.argv[1])
import httpx
from sudarshan.family import safe_http

SAFE, SCHEME, ROUNDS = sys.argv[2] == "safe", sys.argv[3], int(sys.argv[4])


def sockets():
    out = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{name}")
        except OSError:
            continue
        if target.startswith("socket:"):
            out[name] = target
    return out


async def serve(reader, writer):
    try:
        while await reader.read(65536):
            pass
    except ConnectionError:
        pass
    finally:
        writer.close()


async def main():
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    options = {"retries": 0, "verify": False}
    left = []
    for round_ in range(ROUNDS):
        before = sockets()
        made = safe_http.transport(**options) if SAFE else httpx.AsyncHTTPTransport(**options)
        client = httpx.AsyncClient(transport=made, trust_env=False)
        task = asyncio.ensure_future(client.post(f"{SCHEME}://127.0.0.1:{port}/x", json={}))
        if SCHEME == "https":
            await asyncio.sleep(0.2)  # connected; the TLS handshake waits for the server
        else:
            for _ in range(round_):
                await asyncio.sleep(0)
        while not task.done():
            task.cancel()
            await asyncio.sleep(0)
        await client.aclose()
        for _ in range(25):
            await asyncio.sleep(0.02)
            gc.collect()
            new = {fd for fd, target in sockets().items() if before.get(fd) != target}
            if not new:
                break
        if new:
            left.append(round_)
    server.close()
    print(json.dumps(left))


asyncio.run(main())
"""


def sweep(mode: str, scheme: str, rounds: int) -> list[int]:
    """The rounds whose sockets stayed open (for http, a round's number is its loop turns)."""
    done = subprocess.run([sys.executable, "-I", "-c", CHILD, str(ROOT), mode, scheme,
                           str(rounds)], capture_output=True, text=True, timeout=240)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_httpx_own_transport_loses_a_socket_when_a_cancel_lands_in_the_connect():
    assert sweep("plain", "http", 40) != []


def test_httpx_own_transport_loses_a_socket_when_a_cancel_lands_in_the_tls_handshake():
    assert sweep("plain", "https", 3) == [0, 1, 2]


def test_the_safe_transport_closes_what_a_cancelled_connect_opened():
    assert sweep("safe", "http", 40) == []


def test_the_safe_transport_closes_the_socket_of_a_cancelled_tls_handshake():
    assert sweep("safe", "https", 3) == []


def test_the_safe_transport_is_httpxs_own_on_the_cancel_safe_backend():
    made = safe_http.transport(retries=0)
    assert isinstance(made, httpx.AsyncHTTPTransport)
    assert isinstance(made._pool._network_backend, safe_http.CancelSafeBackend)


def test_every_family_channel_client_sends_through_the_safe_transport():
    def backend(client: httpx.AsyncClient):
        return client._transport.inner._pool._network_backend

    journal = digest.make_http(Settings.journal_url)
    clients = [telegram_client.make_http(), push.make_http(), journal]
    assert all(isinstance(backend(c), safe_http.CancelSafeBackend) for c in clients)
