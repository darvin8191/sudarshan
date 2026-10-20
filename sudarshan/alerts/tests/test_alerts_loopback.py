"""Who listens on a port: read from /proc/net/tcp and tcp6, before the API key goes there."""

from __future__ import annotations

import os
import socket

from sudarshan.alerts import loopback

HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"


def _tables(tmp_path, v4: list[str], v6: list[str] | None = None):
    tcp, tcp6 = tmp_path / "tcp", tmp_path / "tcp6"
    tcp.write_text(HEADER + "".join(v4))
    if v6 is not None:
        tcp6.write_text(HEADER + "".join(v6))
    return (tcp, tcp6)


def _row(address: str, port: int, state: str, uid: int) -> str:
    return f"   0: {address}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 " \
           f"00000000 {uid:>5}        0 12345 1 0000000000000000 100 0 0 10 0\n"  # fmt: skip


def test_listeners_reachable_from_loopback(tmp_path):
    me = os.getuid()
    tables = _tables(
        tmp_path,
        [
            _row("0100007F", 8765, "0A", me),  # 127.0.0.1, listening
            _row("0100007F", 5000, "0A", me + 7),  # another user's
            _row("050200C0", 6000, "0A", me + 7),  # 192.0.2.5: not reachable on loopback
            _row("0100007F", 7000, "01", me + 7),  # an established connection, not a listener
            _row("00000000", 7100, "0A", me + 7),  # 0.0.0.0 is reachable
        ],
        [_row("0000000000000000FFFF00000100007F", 7200, "0A", me + 7)],
    )
    assert loopback.listener_uids(8765, tables) == {me}
    assert loopback.holder("ws://127.0.0.1:8765", tables) == loopback.OURS
    assert loopback.holder("http://127.0.0.1:5000", tables) == loopback.OTHER
    assert loopback.holder("http://127.0.0.1:6000", tables) == loopback.NOBODY
    assert loopback.holder("http://127.0.0.1:7000", tables) == loopback.NOBODY
    assert loopback.holder("http://127.0.0.1:7100", tables) == loopback.OTHER
    assert loopback.holder("http://127.0.0.1:7200", tables) == loopback.OTHER  # v4 on a v6 socket
    assert loopback.refusal("http://127.0.0.1:5000", tables) == (
        "port 5000 of this server is held by another user's program"
    )
    assert loopback.refusal("ws://127.0.0.1:8765", tables) is None
    assert (
        loopback.refusal("http://127.0.0.1", tables) == "nothing listens on port 80 of this server"
    )


def test_a_port_shared_with_another_user_is_refused(tmp_path):
    me = os.getuid()
    tables = _tables(
        tmp_path, [_row("0100007F", 8765, "0A", me), _row("00000000", 8765, "0A", 0 if me else 1)]
    )
    assert loopback.holder("ws://127.0.0.1:8765", tables) == loopback.OTHER


def test_no_ipv6_table_is_fine_and_no_table_at_all_is_unknown(tmp_path):
    tables = _tables(tmp_path, [_row("0100007F", 8765, "0A", os.getuid())])
    assert not tables[1].exists()
    assert loopback.holder("ws://127.0.0.1:8765", tables) == loopback.OURS
    missing = (tmp_path / "none4", tmp_path / "none6")
    assert loopback.holder("ws://127.0.0.1:8765", missing) == loopback.UNKNOWN


def test_a_real_listener_of_this_process_is_ours():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert loopback.holder(f"http://127.0.0.1:{port}") == loopback.OURS
    assert loopback.holder(f"http://127.0.0.1:{port}") == loopback.NOBODY
