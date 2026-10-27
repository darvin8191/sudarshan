"""SUDARSHAN's server operations: the deploy tool, nightly backups, notices (M18).

The deploy tool itself is bash (``deploy/sudarshan-deploy``, run as root by a timer). What lives
here as Python is what the ring services share with it: the notice spool (``events``), the
installed release (``release``) and the nightly backup job (``backup``).
"""
