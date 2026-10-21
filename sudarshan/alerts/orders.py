"""From OpenAlgo's order-update messages to alerts: which change is worth a message, and once only.

Input: the ``order_update`` message OpenAlgo's WebSocket proxy relays
(subscribers/wsproxy_subscriber.py). Fields used: ``orderid``, ``mode`` (live or analyze),
``symbol``, ``exchange``, ``action``, ``quantity``, ``filled_quantity``, ``average_price``,
``trigger_price``, ``pricetype``, ``product``, ``order_status`` and ``rejection_reason``.

``order_status`` for Upstox comes from broker/upstox/streaming/upstox_order_adapter.py: complete,
rejected, cancelled, open, "trigger pending", or Upstox's own words passed through in lower case
(the adapter maps "modify pending" and "cancel pending" to open). The sandbox writes
trigger_pending. Everything that is not final and not waiting for a trigger is "working".

Alerts:
  filled          complete, or working with everything filled
  part            working with some filled; again only when more is filled
  part_cancelled  cancelled after a part fill (always sent: the position is not what was ordered)
  cancelled       cancelled with nothing filled (setting SUDARSHAN_ORDER_ALERTS_CANCELLED)
  rejected        with the broker's reason
  triggered       a stop order that left "trigger pending" and is still working, unfilled,
                  TRIGGER_SETTLE_S later; an SL modify or cancel passes through the same states
                  for a moment (pending maps to open) and comes back to trigger pending or ends
                  as cancelled within that time, so it is not reported as a trigger

Nothing is sent for an order after its final state (filled, rejected, cancelled), and a part fill
is sent only when more is filled than last time, so a late or repeated message never goes out of
order. Each alert is claimed once in the store by (order id, kind, filled quantity) before it is
queued: a replay after a reconnect or a restart finds the claim and stays silent. A trigger is
claimed per trigger-pending episode, so a modify slower than the settle time (told as a trigger,
which nothing here can tell apart) does not use up the record a later real trigger needs.

Rows from the order book after a pause (resync.py) go through the same rules, marked found
later: they never start a trigger check and never overwrite a finer state with the book's coarse
one (the book reports a stop order waiting for its trigger as open).

Who cancelled an order cannot be read from this message: OpenAlgo's normalized payload has no
field for it (Upstox's status_message is kept only for rejections), so a cancellation is told
without a cause. An exchange's "expired" counts as cancelled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import messages
from .store import Store, StoreFailed

FINAL_STATES = ("complete", "rejected", "cancelled")
_CANCELLED = {"cancelled", "canceled", "cancelled after market order", "expired"}
#: How long a stop order that left "trigger pending" must stay working before it is reported as
#: triggered (see the module docstring); a fill or a cancel within it is reported as such.
TRIGGER_SETTLE_S = 3.0
#: Stop orders waiting out TRIGGER_SETTLE_S, at most (beyond it the oldest is not reported).
MAX_WAITING_TRIGGERS = 200
_STOP_TYPES = {"SL", "SL-M", "SLM", ""}
#: Status words, which some feeds put where a rejection's reason goes: no reason at all.
_STATUS_WORDS = {
    "open",
    "pending",
    "trigger pending",
    "complete",
    "completed",
    "rejected",
    "failed",
    "cancelled",
    "canceled",
}


@dataclass(frozen=True)
class OrderUpdate:
    order_id: str
    sandbox: bool
    state: str  # complete | rejected | cancelled | trigger_pending | working
    symbol: str
    exchange: str
    action: str
    quantity: int
    filled: int
    average_price: float
    trigger_price: float
    pricetype: str
    product: str
    reason: str

    @property
    def key(self) -> str:
        """The order's id in the store; sandbox ids get their own namespace."""
        return f"sandbox:{self.order_id}" if self.sandbox else self.order_id


@dataclass(frozen=True)
class Alert:
    key: str  # OrderUpdate.key: one line per order in a message, the newest state
    kind: str
    filled: int
    line: str
    at: datetime


def _int(value) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if math.isfinite(number) and number > 0 else 0


def _float(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def state_of(status) -> str:
    text = " ".join(str(status or "").lower().replace("_", " ").split())
    if text in ("complete", "completed"):
        return "complete"
    if text == "rejected":
        return "rejected"
    if text in _CANCELLED:
        return "cancelled"
    if text == "trigger pending":
        return "trigger_pending"
    return "working"


def parse(message: dict) -> OrderUpdate | None:
    """The fields this service reads, with safe types; None without an order id."""
    order_id = str(message.get("orderid") or "").strip()
    if not order_id or len(order_id) > 64:
        return None
    return OrderUpdate(
        order_id=order_id,
        sandbox=str(message.get("mode") or "").lower() == "analyze",
        state=state_of(message.get("order_status")),
        symbol=str(message.get("symbol") or ""),
        exchange=str(message.get("exchange") or ""),
        action=str(message.get("action") or ""),
        quantity=_int(message.get("quantity")),
        filled=_int(message.get("filled_quantity")),
        average_price=_float(message.get("average_price")),
        trigger_price=_float(message.get("trigger_price")),
        pricetype=str(message.get("pricetype") or ""),
        product=str(message.get("product") or ""),
        reason=_reason(message.get("rejection_reason")),
    )


def _reason(value) -> str:
    """A rejection's reason as given, or none when it is empty or only a status word."""
    reason = str(value or "").strip()
    return "" if " ".join(reason.lower().split()) in _STATUS_WORDS else reason


def classify(update: OrderUpdate, cancelled: str) -> tuple[str, int] | None:
    """(kind, filled quantity) for an update worth a message, before any history is applied."""
    if update.state == "rejected":
        return "rejected", update.filled
    if update.state == "cancelled":
        if update.filled:
            return "part_cancelled", update.filled
        return ("cancelled", 0) if cancelled == "all" else None
    if update.state == "complete":
        return "filled", update.filled or update.quantity
    if update.state == "working" and update.filled:
        if update.quantity and update.filled >= update.quantity:
            return "filled", update.filled
        return "part", update.filled
    return None


@dataclass
class _Waiting:
    update: OrderUpdate
    since: datetime
    due: datetime


class OrderTracker:
    """Turns order updates into alerts, using the store for history and the once-only claims."""

    def __init__(
        self,
        store: Store,
        *,
        cancelled: str = "all",
        sandbox: bool = False,
        settle_s: float = TRIGGER_SETTLE_S,
    ):
        self.store = store
        self.cancelled = cancelled
        self.sandbox = sandbox
        self.settle = timedelta(seconds=settle_s)
        self._waiting: dict[str, _Waiting] = {}

    def on_update(self, message: dict, now: datetime, *, found_later: bool = False) -> list[Alert]:
        """Alerts for one update. ``found_later``: a row of the order book after a pause."""
        update = parse(message)
        if update is None or (update.sandbox and not self.sandbox):
            return []
        if found_later and classify(update, self.cancelled) is None:
            return []  # the book's coarse state tells nothing new and must not replace a finer one
        waiting = self._waiting.pop(update.key, None)
        try:
            return self._apply(update, now, waiting, found_later)
        except StoreFailed:  # the store now answers from memory: the same work once more
            return self._apply(update, now, waiting, found_later)

    def _apply(self, update: OrderUpdate, now, waiting, found_later: bool) -> list[Alert]:
        alerts = []
        with self.store.transaction() as tx:
            before = tx.order(update.key)
            if before is not None and before.final:
                return []  # nothing after the final message, whatever arrives late
            decision = classify(update, self.cancelled)
            kind, filled = decision or (None, 0)
            # "Filled" is final too, even before the broker says complete: a late part fill
            # must not follow it.
            final = update.state in FINAL_STATES or kind == "filled"
            episodes = before.pending_seen if before is not None else 0
            if update.state == "trigger_pending" and (
                before is None or before.state != "trigger_pending"
            ):
                episodes += 1
            tx.save_order(update.key, update.state, update.filled, final, episodes, now)
            if kind is not None:
                newer = kind != "part" or filled > tx.most_filled(update.key, "part")
                if newer and tx.claim(update.key, kind, filled, now):
                    alerts.append(self._alert(kind, filled, update, now, found_later))
            elif self._left_trigger_pending(update, before):
                self._wait_for_trigger(update, now)
            elif waiting is not None and update.state == "working" and not update.filled:
                self._waiting[update.key] = waiting  # still unfilled: keep the original clock
        return alerts

    def due(self, now: datetime) -> list[Alert]:
        """Triggers that stayed unfilled for the settle time; told once per episode."""
        alerts = []
        for key, waiting in list(self._waiting.items()):
            if waiting.due > now:
                continue
            del self._waiting[key]
            try:
                alerts += self._settle(key, waiting, now)
            except StoreFailed:
                alerts += self._settle(key, waiting, now)
        return alerts

    def _settle(self, key: str, waiting: _Waiting, now: datetime) -> list[Alert]:
        with self.store.transaction() as tx:
            state = tx.order(key)
            still = state is not None and state.state == "working" and not state.final
            if still and not state.filled and tx.claim(key, "triggered", state.pending_seen, now):
                return [self._alert("triggered", 0, waiting.update, waiting.since)]
        return []

    def _left_trigger_pending(self, update: OrderUpdate, before) -> bool:
        return (
            before is not None
            and before.state == "trigger_pending"
            and update.state == "working"
            and not update.filled
            and update.pricetype.upper().replace("_", "-") in _STOP_TYPES
        )

    def _wait_for_trigger(self, update: OrderUpdate, now: datetime) -> None:
        if len(self._waiting) >= MAX_WAITING_TRIGGERS:  # bounded: the oldest is let go
            del self._waiting[min(self._waiting, key=lambda k: self._waiting[k].due)]
        self._waiting[update.key] = _Waiting(update, now, now + self.settle)

    def _alert(
        self, kind: str, filled: int, update: OrderUpdate, at: datetime, found_later=False
    ) -> Alert:
        today = at.astimezone(messages.IST).date()
        text = messages.line(kind, update, today, found_later=found_later)
        return Alert(update.key, kind, filled, text, at)
