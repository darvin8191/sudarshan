"""SUDARSHAN's risk layer: the kill switch and the pre-trade guards (M5.13, M5.14, M20.7).

Every Upstox order call that sends or changes an order (place, modify, GTT place, GTT modify)
and its four sandbox counterparts passes one gate (gate.py). The gate refuses a risk-increasing
order while the kill switch is on, a daily loss limit is reached or an order guard says no;
a risk-reducing order (an exit) always goes. Whether an order adds risk is decided by pure rules
(rules.py) from the broker's own position book. Nothing here runs unless
``SUDARSHAN_RISK_GUARD`` (flag.py) covers the order's pipe; it ships off.
"""
