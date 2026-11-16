"""What a rate-limited request on a session order route is told (v0.13.0 verify N4).

The orders blueprint has its own answer for the rate limit (``ratelimit_handler`` in
``blueprints/orders.py``): the app's JSON with 429, never the redirect to the rate-limit page
that ``app.py`` gives other pages. So the phone reads a limited exit, cancel or modify as a
refusal (``books/sendOutcome.ts``: the app's own answer under a 4xx) and shows its sentence;
nothing was sent, because the limit answers before the view runs. Only the sentence is ours.
"""

from __future__ import annotations

TOO_MANY = "Too many tries. Wait a moment, then try again."
