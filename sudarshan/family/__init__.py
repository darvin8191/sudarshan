"""SUDARSHAN family view (P6): the service ``sudarshan-family`` and its one-time set-up pieces.

Nothing is imported here: ``python -m sudarshan.family.check`` and ``.derive`` run as accounts that
must not load the service's code just to start.
"""

#: The version of this service's code (the release is the deploy tool's record; /health shows both).
__version__ = "0.1.0"
