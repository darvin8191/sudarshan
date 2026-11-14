"""SUDARSHAN's side of the AI assistant (M17): what keeps it read-only and how its access ends.

The assistant is the owner's own AI app connected to OpenAlgo's remote MCP. Nothing here gives
it a tool; this package only refuses. ``locks.py`` keeps remote MCP from starting with settings
that would allow orders; ``cutoff.py`` ends every access token at once when the kill switch goes
on, the owner disconnects the assistant or his password changes.
"""
