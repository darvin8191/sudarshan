"""Telegram for the automation (M16, M6.8): ``/auto`` and the automation's own notices.

    /auto                         every strategy: state, mode, position, last alert
    /auto stop <name>             stop new entries (its exit alert still goes)
    /auto stop all                the same for every Active strategy
    /auto start <name> [phrase]   start; the phrase is needed for a Live strategy

One command line each, so the bot keeps no conversation state. It answers only the owner's own
private chat (``sudarshan.guard.telegram.owner_of``). "Stop everything" and a change to Live are
on the page only: each deserves its sheet's words.

The bot's loop runs on a real OS thread, and a switch takes the strategy's lock and the database,
so the whole command runs where the web app's own code runs:
``TelegramBotService._in_app_world`` hands it to the hub under eventlet and to the loop's executor
elsewhere (CLAUDE.md "Nothing may block or be blocked across the eventlet boundary").

Notices are what the order alerts cannot say: late, out of order, a gap, not mapped, not sized,
the mode's refusal, the two-step's stopped entry, the cap, a stopped strategy's ignored alert
(once a day, counted by the intake), a switch moved. At most one a minute per strategy and reason;
the rest are counted and said with the next ("and 3 more like it"). A repeat is told to nobody.
Sending uses the queue the kill switch uses: it never waits for Telegram and never fails an alert.
The counts are in memory and lost on a restart.
"""

from __future__ import annotations

import functools
import time

from utils import real_threading
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

from . import model as m
from . import words

logger = get_logger(__name__)

TIMEOUT_S = 60.0
MERGE_S = 60.0
SURFACE = "Telegram"
#: Never told: the log has them, or the order alerts and the risk gate tell them themselves.
SILENT = frozenset({"repeat", "sent", "no_action", "order_refused", "not_confirmed"})
USAGE = (
    "/auto: every strategy\n"
    "/auto stop <name>: stop new entries (its exit alert still goes)\n"
    "/auto stop all: the same for every Active strategy\n"
    "/auto start <name> [phrase]: start (the phrase is needed for a Live strategy)"
)
ONLY_YOUR_CHAT = "This command works only in the account owner's own chat with the bot."
NO_STRATEGIES = "No strategies yet. Make one on the Automation page."
STILL_WORKING = "Still working on it. Check the Automation page in a moment."
BROKE = "That did not work. Nothing was changed. Try the Automation page."


def _line(strategy: m.Strategy, state: m.State, last: dict | None) -> str:
    said = {
        m.ACTIVE: "Active",
        m.EXITS_ONLY: "Stopped (exit alerts still go)",
        m.STOPPED: "Stopped",
    }
    mode = "Live" if strategy.mode == m.LIVE else "Sandbox"
    held = "holds nothing"
    if state.held_symbol:
        held = f"{state.held_side} {words.contract_name(state.held_symbol)}"
    alert = "no alert yet"
    if last:
        alert = f"last alert {words.clock((last['received_ms'] or 0) / 1000)}"
    return f"{strategy.name}: {said[strategy.state]} · {mode} · {held} · {alert}"


def status_text(store) -> str:
    strategies = store.strategies()
    if not strategies:
        return NO_STRATEGIES
    lines = [_line(s, store.state(s.id), store.last_alert(s.id)) for s in strategies]
    return "\n".join(lines)


def _find(store, name: str) -> m.Strategy | None:
    wanted = " ".join(name.split()).casefold()
    return next((s for s in store.strategies() if s.name.casefold() == wanted), None)


def answer(chat, user, args: list[str], desk=None, store=None) -> tuple[str, str]:
    """Run one ``/auto`` command where the app's code runs; the reply and the name logged."""
    from sudarshan.guard.telegram import owner_of

    from . import control
    from .store import shared

    if owner_of(chat, user) is None:
        return ONLY_YOUR_CHAT, "auto_refused"
    store = store or shared()
    verb = args[0].strip().lower() if args else ""
    if verb in ("", "status"):
        return status_text(store), "auto_status"
    if verb == "stop" and len(args) >= 2:
        name = " ".join(args[1:])
        if name.strip().lower() == "all":
            active = [s for s in store.strategies() if s.state == m.ACTIVE]
            if not active:
                return "No strategy is Active.", "auto_stop_all"
            said = []
            for strategy in active:
                said.append(_switch(control, strategy, m.EXITS_ONLY, None, desk, store))
            return "\n".join(said), "auto_stop_all"
        strategy = _find(store, name)
        if strategy is None:
            return f"No strategy is called {name[:40]}.\n{status_text(store)}", "auto_stop"
        return _switch(control, strategy, m.EXITS_ONLY, None, desk, store), "auto_stop"
    if verb == "start" and len(args) >= 2:
        # A name may hold spaces, and the phrase is the last word: try the whole text as the
        # name first, then all but the last word with the last as the phrase.
        whole = _find(store, " ".join(args[1:]))
        if whole is not None:
            return _switch(control, whole, m.ACTIVE, None, desk, store), "auto_start"
        strategy = _find(store, " ".join(args[1:-1])) if len(args) >= 3 else None
        if strategy is None:
            name = " ".join(args[1:])[:40]
            return f"No strategy is called {name}.\n{status_text(store)}", "auto_start"
        return _switch(control, strategy, m.ACTIVE, args[-1], desk, store), "auto_start"
    return USAGE, "auto_usage"


def _switch(control, strategy: m.Strategy, state: str, phrase, desk, store) -> str:
    try:
        changed = control.switch(
            strategy.id, None, state, SURFACE, phrase=phrase, desk=desk, store=store
        )
    except control.Refused as refused:
        return str(refused)
    return words.switched(changed.name, changed.state, SURFACE)


async def handle(bot, update, context) -> None:
    """``/auto [stop|start] [name] [phrase]``, from TelegramBotService.cmd_auto."""
    from database.telegram_db import log_command

    chat, user = update.effective_chat, update.effective_user
    args = [str(arg) for arg in (getattr(context, "args", None) or [])]
    logged = "auto"
    try:
        text, logged = await bot._in_app_world(
            functools.partial(answer, chat, user, args), timeout=TIMEOUT_S
        )
    except TimeoutError:
        text = STILL_WORKING
    except Exception:
        logger.exception("Telegram /auto could not finish")
        text = BROKE
    await update.effective_message.reply_text(text)
    log_command(getattr(user, "id", 0), logged, getattr(chat, "id", 0))


# ---- notices ------------------------------------------------------------------------------------


class Notices:
    """One notice a minute per strategy and reason; what came meanwhile is counted and said with
    the next. No timer: a count is said when the next notice of its kind is due."""

    def __init__(self, send=None, clock=time.monotonic):
        self._send = send or _queue
        self._clock = clock
        self._lock = real_threading.Lock()  # in-memory bookkeeping only
        #: (strategy id, reason) -> (when the last one was said, how many were held back since).
        self._seen = LockedTTLCache(maxsize=512, ttl=3600.0)

    def tell(self, strategy_id: int, reason: str, sentence: str) -> bool:
        """Say it now, or count it. True when it was handed to the queue."""
        if reason in SILENT or not sentence:
            return False
        key, now = (strategy_id, reason), self._clock()
        with self._lock:
            at, held = self._seen.get(key) or (None, 0)
            if at is not None and now - at < MERGE_S:
                self._seen[key] = (at, held + 1)
                return False
            self._seen[key] = (now, 0)
        if held:
            sentence = f"{sentence} (and {held} more like it)"
        try:
            self._send(sentence)
        except Exception:
            logger.exception("Automation: a notice could not be queued")
            return False
        return True


def _queue(text: str) -> None:
    """Queue one message to the owner's Telegram, as the kill switch does. Never waits."""
    from database.telegram_db import get_telegram_user_by_username
    from database.user_db import find_user_by_username
    from services.telegram_alert_service import alert_executor, telegram_alert_service
    from sudarshan.autologin.messages import BRAND
    from sudarshan.books.telegram_words import markdown_safe

    if not telegram_alert_service.is_bot_active():
        return
    admin = find_user_by_username()
    user = get_telegram_user_by_username(admin.username) if admin else None
    if not user:
        return
    alert_executor.submit(
        telegram_alert_service.send_alert_sync,
        user["telegram_id"],
        markdown_safe(f"{BRAND}\nAutomation: {text}"),
    )


#: The process's one desk of notices.
NOTICES = Notices()
