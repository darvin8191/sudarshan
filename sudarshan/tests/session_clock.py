"""A login time that a test can stamp without racing the daily 03:00 IST rollover.

``utils.session.is_session_valid`` ends every login made before the most recent 03:00 IST rollover
(Indian broker tokens expire daily). A test that stamps its login from the wall clock a moment
before 03:00 and sends its next request a moment after it is refused as signed out: a failure that
only the one run in a night that crosses the rollover can show, and no rerun reproduces.

``login_moment()`` is the one place a test takes "now" for a login. Within ``MARGIN`` seconds before
the next rollover it waits until ``SETTLE`` seconds after it, then stamps; at any other time it
stamps now and does not wait. The wait happens once a night, and only for a test that starts in
that minute. A test that holds the session clock itself (a fixed ``when``) does not need this.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytz

from utils.session import _todays_rollover_boundary

#: How close to the next rollover a stamp is taken as one that could straddle it, in seconds.
MARGIN = 60
#: How long after the rollover the wait ends, in seconds.
SETTLE = 2

_IST = pytz.timezone("Asia/Kolkata")


def _now_ist() -> datetime:
    return datetime.now(_IST)


def login_moment(*, clock=_now_ist, sleep=time.sleep) -> datetime:
    """The IST instant to stamp as a login: now, unless the next rollover is within ``MARGIN``."""
    now = clock()
    upcoming = _todays_rollover_boundary(now)
    if upcoming <= now:
        upcoming += timedelta(days=1)
    away = (upcoming - now).total_seconds()
    if away <= MARGIN:
        sleep(away + SETTLE)
        now = clock()
    return now


def login_stamp(*, clock=_now_ist, sleep=time.sleep) -> str:
    """``login_moment()`` as the ISO string a session's ``login_time`` holds."""
    return login_moment(clock=clock, sleep=sleep).isoformat()
