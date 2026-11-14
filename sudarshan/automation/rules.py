"""Whether one alert may become an order (M12.1). Pure: no I/O, no clock, no logging.

``decide`` is asked twice for an alert: once before the strategy's lock, to learn whether to wait
for a sibling, and once holding it, on rows read again under the lock. Only the second answer
counts. ``pipe`` says where a strategy's order goes, or why it goes nowhere; it never reads the
app's mode itself, the caller passes it.

The rules, in order:

1. repeat        the alert's claim is already there: nothing is sent.
2. out of order  it fired before the newest alert applied: nothing, an exit included (an old exit
                 must not close a newer entry).
3. stopped       a Stopped strategy sends nothing; one stopped for new entries lets only a flat
                 target through.
4. capped        the day's alerts are used up: the strategy takes no new entries today, and only a
                 flat target goes on.
5. late          older than the strategy's window, or dated more than 5 s ahead of this server: a
                 target that opens, adds or reverses is refused; a flat target goes on.
6. chain         the alert says where the strategy was before it, and that differs from what was
                 last applied: a fresh alert waits for its sibling; if none comes it is applied
                 and the gap is said.

Before any of them the direction filter reads a target the strategy does not trade as flat (a
long-only strategy reads short as flat), so an exit is never refused as an entry.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import flag
from . import model as m

FUTURE_SLACK_S = 5
CHAIN_WAIT_S = 1.5
CHAIN_STEP_S = 0.1


@dataclass(frozen=True)
class Decision:
    """``verdict`` None means go on to the order. ``target`` is +1, 0 or -1 after the direction
    filter."""

    verdict: str | None
    target: int
    wait: bool = False  # ask again in a moment: a sibling alert may be on its way
    late_s: int | None = None  # how late it was, when it was late (negative: dated ahead)
    gap: bool = False  # an earlier alert did not arrive; this one was applied
    capped_now: bool = False  # the cap is reached: the strategy takes no new entries today
    tell_stopped: bool = False  # the first ignored alert of the day for a stopped strategy


def target_of(strategy: m.Strategy, alert: m.Alert) -> int:
    """Where the alert points for this strategy: a side it does not trade is flat."""
    target = alert.target
    if (strategy.direction == m.LONG and target < 0) or (
        strategy.direction == m.SHORT and target > 0
    ):
        return 0
    return target


def decide(
    strategy: m.Strategy,
    state: m.State,
    alert: m.Alert,
    now_s: float,
    day: str,
    *,
    claimed: bool = False,
    waited: bool = False,
) -> Decision:
    """The verdict for one alert. ``day`` is today's date in India; ``claimed`` says the alert's
    claim is already written; ``waited`` that the wait for a sibling is over (or not allowed)."""
    target = target_of(strategy, alert)
    if claimed:
        return Decision("repeat", target)
    if state.last_fired_s is not None and alert.fired_s < state.last_fired_s:
        return Decision("out_of_order", target)
    if strategy.state == m.STOPPED or (strategy.state == m.EXITS_ONLY and target != 0):
        return Decision("stopped", target, tell_stopped=state.told_stopped_day != day)
    capped = state.alerts_on(day) >= strategy.max_alerts_day
    if capped and target != 0:
        return Decision("capped", target, capped_now=strategy.state == m.ACTIVE)
    age = int(now_s) - alert.fired_s
    late = age > strategy.max_age_s or -age > FUTURE_SLACK_S
    if late and target != 0:
        return Decision("late", target, late_s=age)
    before = alert.before_target
    gap = before is not None and before != state.last_target
    if gap and not late and not waited:
        return Decision(None, target, wait=True)
    # A sibling fired in the same second was applied just before this one: TradingView does not
    # say what "before" holds when two orders fill on one bar, so that is not called a gap.
    said_gap = gap and state.last_fired_s != alert.fired_s
    return Decision(
        None,
        target,
        late_s=age if late else None,
        gap=said_gap,
        capped_now=capped and strategy.state == m.ACTIVE,
    )


#: Why an order goes nowhere although the alert itself was in order.
SANDBOX_ONLY, GUARD_NOT_ON, APP_IN_ANALYZE, SEMI_AUTO = (
    "sandbox_only",
    "guard_not_on",
    "app_in_analyze",
    "semi_auto",
)


def pipe(
    stage: str, guard_stage: str, mode: str, app_in_analyze: bool, target: int
) -> tuple[bool | None, str | None]:
    """Where a strategy's order goes: (analyze, None), or (None, why) when it is sent nowhere.

    ``analyze`` True is the sandbox, False is Upstox. The pipe is the strategy's own: a Sandbox
    strategy goes to the sandbox whatever the app's mode, and a Live strategy's exit goes to
    Upstox even while the app is in Analyze mode, because a strategy closes its position where it
    opened it. Nothing here ever turns a Live strategy's order into a sandbox order: that would
    answer success while the real position stayed open.
    """
    if stage not in (flag.SANDBOX, flag.ON):
        return None, "off"
    if mode != m.LIVE:
        return True, None
    if stage != flag.ON:
        return (
            None,
            SANDBOX_ONLY,
        )  # no Live order leaves this server at this stage, an exit included
    if target != 0 and guard_stage != "on":
        return None, GUARD_NOT_ON
    if target != 0 and app_in_analyze:
        return None, APP_IN_ANALYZE
    return False, None


SENT, NO_ACTION, ORDER_REFUSED, NOT_CONFIRMED = (
    "sent",
    "no_action",
    "order_refused",
    "not_confirmed",
)


def read_answer(success: bool, response: object, status: int) -> tuple[str, bool, bool]:
    """What the smart order's answer means: (verdict, flat_found, cut).

    ``flat_found`` is True when the answer says there was no position to close. ``cut`` is True
    when an order went but the risk gate cut a reversal to its exit (the kill switch is on, or
    the day's loss limit is reached): the position is flat, not on the other side, and the
    answer's own sentence says so. Anything that is not a clear refusal (a 4xx before the send)
    and not a clear success reads as not confirmed: it may have reached the broker.
    """
    body = response if isinstance(response, dict) else {}
    message = str(body.get("message") or "").lower()
    if success:
        if body.get("orderid"):
            # The sandbox passes the gate's note on as ``risk_note``; the Live service puts it in
            # ``message``, which a placed order carries for no other reason.
            return SENT, False, bool(body.get("risk_note") or body.get("message"))
        if "no open position" in message or "no openposition" in message:
            return NO_ACTION, True, False
        if "no action" in message or "already matched" in message:
            return NO_ACTION, False, False
        return NOT_CONFIRMED, False, False
    if isinstance(status, int) and 400 <= status < 500 and not body.get("not_confirmed"):
        return ORDER_REFUSED, False, False
    return NOT_CONFIRMED, False, False


def state_after(
    verdict: str,
    flat_found: bool,
    target: int,
    alert: m.Alert,
    exchange: str,
    symbol: str,
    now_ms: int,
    state: m.State | None = None,
) -> dict[str, object]:
    """The state's new values once an order's answer is in. Empty when nothing was sent: the state
    moves on only when an order went or may have gone.

    The held contract is set when an entry went or may have gone, cleared when an exit answered
    success or found nothing to close, and kept when an exit is not confirmed.
    """
    if verdict not in m.MOVED:
        return {}
    values: dict[str, object] = {"last_target": target, "last_fired_s": alert.fired_s}
    if target == 0:
        if verdict != NOT_CONFIRMED:
            values.update(held_exchange=None, held_symbol=None, held_side=None, held_since_ms=None)
        return values
    side = m.LONG if target > 0 else m.SHORT
    same = state is not None and (state.held_symbol, state.held_side) == (symbol, side)
    values.update(
        held_exchange=exchange,
        held_symbol=symbol,
        held_side=side,
        held_since_ms=state.held_since_ms if same else now_ms,
    )
    return values
