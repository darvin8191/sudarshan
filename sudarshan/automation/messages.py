"""The alert messages the Automation page gives the owner to copy (M12.1). Pure.

Three forms, none with a secret: the link's token is in the Webhook URL, never in the message.
Each is what payload.parse reads; a test parses every one of them as TradingView would fill it.
"""

from __future__ import annotations

import json

from . import model as m

#: A strategy's order-fill alert: TradingView fills every placeholder in.
STRATEGY = (
    '{"v": 1,\n'
    ' "id": "{{strategy.order.id}}",\n'
    ' "action": "{{strategy.order.action}}",\n'
    ' "contracts": "{{strategy.order.contracts}}",\n'
    ' "position": "{{strategy.market_position}}",\n'
    ' "size": "{{strategy.market_position_size}}",\n'
    ' "before": "{{strategy.prev_market_position}}",\n'
    ' "before_size": "{{strategy.prev_market_position_size}}",\n'
    ' "signed_size": "{{strategy.position_size}}",\n'
    ' "price": "{{strategy.order.price}}",\n'
    ' "ticker": "{{exchange}}:{{ticker}}",\n'
    ' "interval": "{{interval}}",\n'
    ' "bar": "{{time}}",\n'
    ' "fired": "{{timenow}}"}'
)

#: An indicator that calls alert(): placeholders are not filled there, so the script writes the
#: text. bar and fired are numbers (milliseconds).
INDICATOR = """//@version=6
indicator("My signal", overlay = true)
f_msg(string pos) =>
    '{"v":1,"position":"' + pos + '","ticker":"' + syminfo.prefix + ':' + syminfo.ticker +
      '","interval":"' + timeframe.period + '","bar":' + str.tostring(time) +
      ',"fired":' + str.tostring(timenow) + '}'
if goLong
    alert(f_msg("long"), alert.freq_once_per_bar_close)
if goShort
    alert(f_msg("short"), alert.freq_once_per_bar_close)
if goFlat
    alert(f_msg("flat"), alert.freq_once_per_bar_close)
"""


def condition(position: str) -> str:
    """An alertcondition(), a drawing or a price alert: one alert per target."""
    return json.dumps(
        {
            "v": 1,
            "position": position,
            "ticker": "{{exchange}}:{{ticker}}",
            "interval": "{{interval}}",
            "bar": "{{time}}",
            "fired": "{{timenow}}",
        }
    )


def for_strategy(strategy: m.Strategy) -> dict[str, object]:
    """The three forms for one strategy (contract.ALERT_MESSAGES). A strategy that trades one
    side only is not given the other side's fixed message."""
    sides = {m.BOTH: (m.LONG, m.SHORT), m.LONG: (m.LONG,), m.SHORT: (m.SHORT,)}[strategy.direction]
    return {
        "strategy": STRATEGY,
        "indicator": INDICATOR,
        "condition": [condition(side) for side in (*sides, m.FLAT)],
    }
