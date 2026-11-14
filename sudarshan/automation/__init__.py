"""सुदर्शन / SUDARSHAN automation (M12): the intake in front of the smart order.

A TradingView alert reaches ``POST /sudarshan/tv/<token>``; this package decides whether it
becomes one smart order. It adds no sender of its own: sizing, claims, holds and the risk gate
stay in ``services/place_smart_order_service.py`` and below. Off by default
(``SUDARSHAN_AUTOMATION``, flag.py).
"""
