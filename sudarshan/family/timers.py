"""The family's channels, seated in the service, and the one job that runs them (F8, F10; design
2.6, 2.8).

``install`` builds the family bot, Web Push, the outbox and the day's summary, seats their viewer
routes (Telegram link and unlink, the push key and subscriptions, Settings, the test send), hears
the accounts' notices (a removed member's chat, a member's sign-in notices) and adds the job
``channels``. That job owns the three HTTP clients, each reaching only its own places:

    the bot      api.telegram.org, 127.0.0.1:5583 and :5580   (``telegram_client.ALLOWED``)
    Web Push     the push services' hosts on 443               (``push.PushOnly``)
    the journal  127.0.0.1:5586 only, for the summary's trades line (``digest.make_http``)

and runs, each started again 5 s after a fault:

    bot       long-polls Telegram and answers, through its pacer per chat
    outbox    sends what is due every 5 s (at once when a message is put)
    digest    looks every 30 s whether today's summary is due (Y42: a missed one runs until 21:00)

When the job ends (the service's stop cancels it), every part is stopped with
``sudarshan.tasks.cancel_and_wait`` and every client closed, even if the stop cancels it again
while it closes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sudarshan.common.schedule import Schedule
from sudarshan.tasks import cancel_and_wait

from . import bot as bot_module
from . import digest as digest_module
from . import push as push_module
from .bot import finish, hook_notices, install_check
from .config import ConfigError, seal_key
from .outbox import NOTICE_KINDS, Outbox
from .quiet import SettingsRoutes
from .telegram_client import TelegramClient

log = logging.getLogger("sudarshan.family")

RESTART_S = 5.0


async def _keep(piece: str, part: Callable[[], Awaitable[None]]) -> None:
    """Run a part for ever: a fault is logged and the part starts again after 5 s."""
    while True:
        try:
            await part()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("the family channels' %s stopped on a fault; it starts again in 5 s",
                          piece)  # fmt: skip
            await asyncio.sleep(RESTART_S)


class Channels:
    """The bot, Web Push, the outbox and the summary of one service."""

    def __init__(self, service: Any, *, bot: Any, push: Any, outbox: Outbox, digest: Any,
                 journal_key: Any):  # fmt: skip
        self.service, self.bot, self.push = service, bot, push
        self.outbox, self.digest, self.journal_key = outbox, digest, journal_key

    def admin_blocks(self) -> dict[str, Any]:
        """The admin ``state`` answer's ``bot`` and ``check`` blocks."""
        return {"bot": self.bot.report(), "check": install_check(self.service.settings.etc_dir)}

    def health(self) -> dict[str, Any]:
        """Counts and states only: no chat, endpoint, member or token."""
        return {"bot": self.bot.health(), "push": self.push.health(),
                "outbox": self.outbox.health(), "digest": self.digest.health()}  # fmt: skip

    async def run(self) -> None:
        """The job ``channels``: make the clients, run the parts, close everything."""
        settings = self.service.settings
        telegram = bot_module.make_http()
        self.bot.client = TelegramClient(telegram)
        pushing = push_module.make_http()
        self.push.start(pushing)
        journal = None
        if self.journal_key is not None:
            journal = digest_module.make_http(settings.journal_url)
            self.digest.journal = digest_module.Journal(settings.journal_url, self.journal_key,
                                                        journal)  # fmt: skip
        parts = {"bot": self.bot.run, "outbox": self.outbox.run, "digest": self.digest.run}
        tasks = [asyncio.create_task(_keep(piece, part), name=f"family-channels-{piece}")
                 for piece, part in parts.items()]  # fmt: skip
        try:
            await asyncio.gather(*tasks)
        finally:
            self.push.stop()
            self.digest.journal = None
            steps: list[Callable[[], Awaitable[Any]]] = [lambda: cancel_and_wait(*tasks),
                                                         telegram.aclose, pushing.aclose]  # fmt: skip
            if journal is not None:
                steps.append(journal.aclose)
            await finish(steps)


def install(service: Any) -> Channels:
    """Seat the channels in the service (see the module's words)."""
    settings, viewer = service.settings, service.viewer
    bot = bot_module.build(service)
    vapid = push_module.load_vapid(settings.etc_dir, settings.push_subject)
    push = push_module.Push(service, vapid)
    outbox = Outbox(service, push=push, bot=bot, market=bot.market)
    try:
        journal_key = seal_key(settings.etc_dir, "journal-family")
    except ConfigError:
        journal_key = None
        log.info("the family summary goes without the journal's trades line: the journal's family "
                 "key is not provisioned")  # fmt: skip
    schedule = Schedule(bot.market.calendar, eod_after_min=settings.digest_after_min)
    digest = digest_module.Digest(service, outbox=outbox, bot=bot, market=bot.market,
                                  schedule=schedule)  # fmt: skip
    outbox.render = digest.render  # Y44: a summary's text is made when it is sent
    routes = SettingsRoutes(service)
    seats = {
        "_telegram_link": bot.route_telegram_link,
        "_telegram_unlink": bot.route_telegram_unlink,
        "_push_key": push.route_push_key,
        "_push_subscribe": push.route_push_subscribe,
        "_push_unsubscribe": push.route_push_unsubscribe,
        "_settings": routes.route_settings,
        "_settings_set": routes.route_settings_set,
        "_test_send": outbox.route_test_send,
    }
    for name, route in seats.items():
        setattr(viewer, name, route)

    def heard(kind: str, member: Any = None, **facts: Any) -> None:
        if kind == "member_removed" and member:
            bot.member_removed(member)
        elif kind in NOTICE_KINDS:
            outbox.on_notice(kind, member, **facts)

    hook_notices(service, heard)
    channels = Channels(service, bot=bot, push=push, outbox=outbox, digest=digest,
                        journal_key=journal_key)  # fmt: skip
    service._job_makers["channels"] = lambda _service: channels.run()
    return channels
