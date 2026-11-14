"""Options on the phone (M11.1, screens design lane O): the option chain snapshot for `/options`.

One read of Upstox's put/call option chain per event, made inside OpenAlgo with the owner's daily
access token (the Smartlist pattern, ``sudarshan/watchlists/smartlist.py``), mapped to OpenAlgo's
symbols through the master so every row names what the proxy subscribes. Read-only and
request-driven: no thread, timer, event loop or scheduler here, and nothing that places, changes
or cancels an order.
"""
