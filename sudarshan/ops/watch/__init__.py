"""sudarshan-watch: the server's own health, read every two minutes and told to the owner (M19.5).

A oneshot started by a timer (``python -m sudarshan.ops.watch``): no listener, no loop, no thread.
It reads (``readers.py``), judges with pure functions (``checks.py``), compares with its last
state and records what changed as events for the notice desk (``run.py``). It only reads and
reports: it never starts, stops or changes a service. It names SUDARSHAN's own services, ports
and folders and OpenAlgo's, nothing else on the server, and it never calls the exchange.
"""
