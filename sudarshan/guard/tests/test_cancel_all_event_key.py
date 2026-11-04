"""Analyze mode's Cancel all from the app names the caller's API key on its event (v0.4.1).

The app's Cancel all passes ``order_data={}`` and the key in ``original_data``. The sandbox branch
of ``cancel_all_orders_with_auth`` published ``AllOrdersCancelledEvent`` with ``order_data``'s key,
which is empty, so OpenAlgo's Telegram alert found no user and sent nothing, while a single Cancel
was told (owner's Analyze check, 28 Oct 2026).
"""

from __future__ import annotations

import services.cancel_all_order_service as cancel_all
import services.sandbox_service as sandbox

ANSWER = {
    "status": "success",
    "canceled_count": 2,
    "failed_count": 0,
    "canceled_orders": ["SB-1", "SB-2"],
    "failed_cancellations": [],
}


def test_the_sandbox_cancel_all_event_names_the_callers_key(monkeypatch):
    published: list = []
    monkeypatch.setattr(cancel_all, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(cancel_all.bus, "publish", published.append)
    monkeypatch.setattr(
        sandbox,
        "sandbox_cancel_all_orders",
        lambda order_data, api_key, original_data: (True, dict(ANSWER), 200),
    )
    ok, answer, status = cancel_all.cancel_all_orders_with_auth(
        {}, "broker-token", "upstox", {"apikey": "OWNER-KEY"}
    )
    assert (ok, status, answer["canceled_count"]) == (True, 200, 2)
    events = [e for e in published if isinstance(e, cancel_all.AllOrdersCancelledEvent)]
    assert len(events) == 1
    assert events[0].api_key == "OWNER-KEY"
    assert events[0].mode == "analyze" and events[0].canceled_count == 2
    assert "apikey" not in events[0].request_data  # the key never rides in the request record
