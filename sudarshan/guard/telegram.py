"""Telegram's ``/killswitch``, in the owner's own chat only (M16.7, design section 8).

    /killswitch                  where it stands, and what turning it on would cancel
    /killswitch on <phrase>      turn it on, keeping positions
    /killswitch exit <phrase>    turn it on and Exit all
    /killswitch off <phrase>     resume

One command line each, so the bot keeps no conversation state. It answers only a private chat
with a linked Telegram account whose OpenAlgo user is this install's owner; family members never
hold an order-capable credential, and linking already needs the API key.

The bot's loop runs on a real OS thread. The kill switch takes the gate's lock, the sandbox's
locks and the database, so the whole command runs where the web app's own code runs:
``TelegramBotService._in_app_world`` hands it to the hub under eventlet and to the loop's executor
elsewhere (CLAUDE.md "Nothing may block or be blocked across the eventlet boundary"). The reply
is plain text, shown exactly as written.
"""

from __future__ import annotations

import functools

from utils.logging import get_logger

from . import killswitch, words

logger = get_logger(__name__)

#: How long the bot waits for each command before it says it is still working. The command
#: carries on regardless: an activation's steps finish and report on their own.
TIMEOUT_S = {"on": 300.0, "exit": 300.0, "off": 60.0}
STATUS_TIMEOUT_S = 60.0
VERBS = ("on", "exit", "off", "", "status")


def owner_of(chat, user) -> str | None:
    """The owner's OpenAlgo username when this is his own private chat with the bot, else None."""
    from database.telegram_db import get_telegram_user
    from database.user_db import find_user_by_username

    if getattr(chat, "type", None) != "private" or user is None:
        return None
    linked = get_telegram_user(user.id)
    if not linked:
        return None
    admin = find_user_by_username()
    if admin is None or linked.get("openalgo_username") != admin.username:
        return None
    return admin.username


def status_text(owner: killswitch.Owner) -> str:
    """Where the switch stands, and the exact commands; what it would cancel when off."""
    from . import gate

    state = gate.GATE.state()
    if state is None:
        return words.STATE_UNREADABLE
    if state.on:
        lines = [words.ks_already_on(killswitch._clock(state.since)), words.TG_RESUME_HOW]
        if state.report:
            lines += ["", words.ks_report_message(state.report)]
        return "\n".join(lines)
    try:
        counts = killswitch.preview(owner)
    except Exception:
        logger.exception("The kill switch preview for Telegram could not be counted")
        counts = None
    return "\n".join([words.TG_OFF, words.ks_preview(counts, telegram=True), "", words.TG_USAGE])


def answer(chat, user, verb: str, phrase: str) -> tuple[str, str]:
    """Run one command where the app's code runs; returns the reply and the name logged."""
    username = owner_of(chat, user)
    if username is None:
        return words.TG_ONLY_YOUR_CHAT, "killswitch_refused"
    if verb not in VERBS:
        return words.TG_USAGE, "killswitch_usage"
    owner = killswitch.owner_for(username, surface="telegram", actor=f"telegram:{user.id}")
    if verb in ("on", "exit"):
        done = killswitch.activate(owner, exit_all=verb == "exit", phrase=phrase)
        text = done.message
        if done.status == 200 and done.state is not None and done.state.report:
            text = words.ks_report_message(done.state.report)
        return text, f"killswitch_{verb}"
    if verb == "off":
        return killswitch.resume(owner, phrase=phrase).message, "killswitch_off"
    return status_text(owner), "killswitch_status"


async def handle(bot, update, context) -> None:
    """``/killswitch [on|exit|off] [phrase]``, from TelegramBotService.cmd_killswitch."""
    from database.telegram_db import log_command

    chat, user = update.effective_chat, update.effective_user
    args = [str(arg) for arg in (getattr(context, "args", None) or [])]
    verb = args[0].strip().lower() if args else ""
    phrase = " ".join(args[1:])
    logged = f"killswitch_{verb}" if verb in VERBS and verb else "killswitch_status"
    try:
        text, logged = await bot._in_app_world(
            functools.partial(answer, chat, user, verb, phrase),
            timeout=TIMEOUT_S.get(verb, STATUS_TIMEOUT_S),
        )
    except TimeoutError:
        text = words.TG_STILL_WORKING
    except Exception:
        logger.exception("Telegram /killswitch could not finish")
        text = words.TG_BROKE
    await update.effective_message.reply_text(text)
    log_command(getattr(user, "id", 0), logged, getattr(chat, "id", 0))
