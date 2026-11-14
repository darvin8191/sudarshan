"""Base event with common fields shared by all order events."""

from dataclasses import dataclass, field

from sudarshan.refused_keys import refused_api_key  # SUDARSHAN: G5
from utils.event_bus import Event


@dataclass
class OrderEvent(Event):
    """
    Base for all order-related events.

    Every order event carries:
    - mode: "live" or "analyze" — subscribers branch on this
    - api_type: the operation name used for logging and telegram templates
    - request_data / response_data: dicts for log subscribers
    - api_key: for telegram username resolution
    """

    mode: str = "live"  # "live" or "analyze"
    api_type: str = ""
    request_data: dict = field(default_factory=dict)
    response_data: dict = field(default_factory=dict)
    api_key: str = ""
    # SUDARSHAN: G5. Raised while refusing an /api/v1 request for its API key (missing or wrong),
    # worked out as the event is made, in the request's own thread (sudarshan/refused_keys.py).
    # Such an event is not an order, and the bus hands it to no subscriber (utils/event_bus.py).
    # Keyword only, so no field moves for a caller that passes them by position.
    key_refused: bool = field(
        default_factory=refused_api_key, kw_only=True, repr=False, compare=False
    )
