"""The owner's desk (M10.1, M10.3, M10.4, M10.14): what the local API's ops do to market alerts.

The ops run in the alert service's loop beside the ticks, and every market.db call goes through
the runtime's one writer thread, so an owner's change and a fire are recorded in the order they
were decided (review A7.k). Changes are made one at a time and reach the Book only after the
store has accepted them (A7.m):

- the store first: an edit keeps a stop the engine made a moment before (store.update), and a
  pause, resume or restart is accepted only from the status it is for;
- then the Book (runtime.owner_put): a fresh state when the condition changed, the state resumed
  on a resume or restart, the kept one otherwise. A new, changed or resumed alert takes its side
  from a current frame (runtime._seed: never a snapshot from before its claim), so it fires on
  nothing it did not see happen;
- a key the feed does not hold yet (or not in the mode needed) is claimed before the alert is
  saved, while no renewal can drop it. The feed's refusal (its quota) or silence is said in
  words, and nothing is saved.

Limits (the owner's decision 5): at most ``max_instruments`` instruments and ``max_active``
Active alerts. A change that would add past either is refused in words; one that adds nothing is
never refused, even past a limit lowered since.

The page's status lines (review A7.h) are clock.status for the alert's own session (a stock that
closes by auction has its own) with the feed's last health in words.check_words, never a tick's
check: an untraded option fails every tick as outside hours all day. While market alerts are
paused (the feed down, unheard or silent), every line inside a session says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sudarshan.alerts.market import clock, groups, spec, words
from sudarshan.alerts.market.api import OWNER, SERVICE, ApiError
from sudarshan.alerts.market.book import BASE_CHANGED
from sudarshan.alerts.market.engine import NOT_CHECKED, NOT_JUDGED, Reason, stop_reason
from sudarshan.alerts.market.feedlink import FeedRefused, FeedUnreachable
from sudarshan.alerts.market.members import Unavailable
from sudarshan.alerts.market.sessions import AUCTION, next_session
from sudarshan.alerts.market.store import (
    GROUP_DELIVERY,
    MOVES,
    GroupLimit,
    GroupRefused,
    NotFound,
    Refused,
    StoreError,
    VersionConflict,
)
from sudarshan.alerts.messages import clean, quantity
from sudarshan.autologin.schedule import IST

log = logging.getLogger("sudarshan.alerts.market")

#: The test waits this long for Telegram's answer; one still pending then is in History later.
TEST_WAIT_S = 10.0
#: History pages: the page asks for up to this many at a time.
PAGE, PAGE_MAX = 50, 100
SINCE, SINCE_MAX = 200, 200
#: A change waits this long for its turn and its claim at the feed, so the page is told it was
#: not saved (503) before the bridge's 6 s end, and a change the page was told failed never
#: lands (review M1).
CHANGE_WAIT_S = 4.5
#: List alerts ask their lists again at most this often (the one-minute check inside a session).
GROUP_CHECK_MS = 60_000
ACTIONS = ("edit", "pause", "resume", "restart")
#: What the page sends of a list alert (the key, name and delivery are the group's own).
GROUP_DRAFT = ("ref", "name", "kind", "params", "frequency", "candle", "candle_min", "cooldown_s",
               "ends", "note", "delivery", "digest_min")  # fmt: skip
#: A group's condition is validated once on a stand-in instrument (each member is checked on its own).
STAND_IN = {
    "instrument_key": "NSE_EQ|LIST",
    "symbol": "LIST",
    "exchange": "NSE",
    "tick_size": "0.05",
}
GROUP_NAME_MAX = 40
#: What the service takes from a draft; the bridge adds the key, tick size and expiry.
DRAFT = (
    "symbol", "exchange", "instrument_key", "tick_size", "expiry", "kind", "params", "frequency",
    "candle", "candle_min", "cooldown_s", "ends", "note", "channels", "price",
)  # fmt: skip
#: A status line's codes, best first: the engine's line is the best of its sessions' lines.
ORDER = (
    clock.OK, clock.CLOSING_AUCTION, clock.FEED_DOWN, clock.NOT_OPEN_YET,
    clock.CLOSED_BY_EXCHANGE, clock.OUTSIDE_HOURS, clock.CLOSED_TODAY,
)  # fmt: skip
#: The sessions the engine's line follows while no alert is Active.
MAIN = ("NSE_EQ", "NSE_FO", "NSE_INDEX")
#: What a pushed card carries: enough to apply a change, or to know the page must be read again.
BRIEF = ("id", "version", "status", "status_reason", "check", "fired_today")
#: Notes worth a line on the card; one that waits for the feed comes from the Book while it lasts.
NOTED = frozenset({NOT_CHECKED, NOT_JUDGED, BASE_CHANGED})
WORDS = {
    "starting": "Market alerts are starting. Try again in a moment.",
    "stopping": "The alert service is stopping. Try again in a minute.",
    "instruments": "Alerts can follow at most {n} instruments at once. Delete alerts on an "
    "instrument you no longer need, or add this alert to an instrument that already has one.",
    "active": "You can keep at most {n} alerts Active at once. Pause or delete one you no longer "
    "need.",
    "feed_full": "The price feed cannot take another instrument right now. Delete an alert on an "
    "instrument you no longer need, then try again.",
    "feed_silent": "The price feed is not answering, so this was not saved. Try again in a minute.",
    "ended": "This alert's end date has passed. Pick a later one, or no end.",
    "draft": "The alert could not be read. Open the form again, then save.",
    "number": "This alert could not be found on the page. Open it again.",
    "action": "Pick Edit, Pause, Resume or Restart.",
    "prefs": "Pick where alerts go: Telegram on or off, and the app on or off.",
    "lists": "The watchlist could not be read just now, so nothing was made. Try again in a minute.",
    "lists_off": "The watchlist feature is switched off, so an alert cannot be set on a list.",
    "list_gone": "That list is no longer there. Open the lists again, then set the alert.",
    "no_symbols": "This list has no symbols that alerts can follow yet. Add some, then set the alert.",
    "name": "Give the alert a name.",
    "delivery": "Pick how the list alert goes to Telegram: the first at once and the rest in one "
    "message, or each at once.",
    "same_kind": "A list alert keeps its kind. Make a new alert to watch for something else.",
}


class OwnerDesk:
    """The API's desk while market alerts are switched on: ``api.Desk`` over the runtime."""

    def __init__(self, runtime):
        self.rt = runtime
        self._changes = asyncio.Lock()
        self._line: dict | None = None  # the page's line as last pushed to the open apps
        #: List alerts: the day of the last morning sync, when the next check may start, whether
        #: the lists said "off" last, and each group's earliest current-month future's end.
        self._group_day = None
        self._group_master = None  # the day whose master contract the lists were asked again after
        self._group_next = 0
        self._unread = False  # the lists could not be read at the last ask: cards say so
        self._off: bool | None = None
        self._ask: dict[int, int] = {}
        runtime.on_change, runtime.on_second = self._changed, self._second
        runtime.on_engine = self.engine_line

    def health(self) -> dict[str, Any]:
        rt = self.rt
        return {"service": SERVICE, "market_alerts": "on", "market_state": rt.state,
                "alerts_active": len(rt.book.alerts()), "instruments": len(rt.book.modes())}  # fmt: skip

    async def call(self, op: str, message: Mapping[str, Any]) -> Any:
        return await getattr(self, f"_{op}")(message)

    # ---- list alerts follow their list (M8.14) -----------------------------------------------
    async def _group_sync(self, message) -> dict[str, Any]:
        """The watchlist page's push named ``ref`` (or one group by ``id``): its groups follow it
        now. The answer is each group's brief."""
        rt = self._running()
        gid, ref = message.get("id"), message.get("ref")
        if not (isinstance(gid, int) and not isinstance(gid, bool)) and not isinstance(ref, str):
            raise ApiError(400, "invalid", WORDS["number"], "id")
        rows = await self._write(rt.store.groups, OWNER)
        found = [g["id"] for g in rows if (g["id"] == gid if gid is not None else g["ref"] == ref)]
        return {"groups": [b for i in found if (b := await self.sync(i)) is not None]}

    def _group_step(self, now: int) -> None:
        """Each second, on the clock job's own tick (no timer of its own): the morning sync on
        the first tick of a day (before the day's master contract is loaded, about 09:00), once
        more when the runtime has loaded it, then the lists' check at most once a minute inside a
        session, and sooner than that a current-month future's end."""
        rt = self.rt
        if rt.members is None or (rt.group_task is not None and not rt.group_task.done()):
            return
        day = _day(now)
        if day != self._group_day:
            reason = "morning"
            if rt.master_loaded(day):
                self._group_master = day
        elif self._group_master != day and rt.master_loaded(day):
            reason = "master"  # a current-month future resolves against today's master (09:00)
            self._group_master = day
        elif now < self._group_next:
            return
        elif self._ask and min(self._ask.values()) <= now:
            reason = "ask"
        elif rt.in_session(now):
            reason = "check"
        else:
            return
        self._group_day, self._group_next = day, now + GROUP_CHECK_MS
        rt.group_task = asyncio.create_task(self._follow(reason), name="market alerts: list alerts")

    async def _follow(self, reason: str) -> None:
        """One pass over the groups: the lists' version first on a check (list membership is not
        market data, so asking is allowed); the morning and a future's end sync them all."""
        rt = self.rt
        try:
            rows = [
                g for g in await self._write(rt.store.groups, OWNER) if g["status"] != "stopped"
            ]
            now = rt.now_ms()
            self._ask = {i: at for i, at in self._ask.items() if i in {g["id"] for g in rows}}
            if reason == "ask":  # only the groups whose current-month future has ended
                rows = [g for g in rows if self._ask.get(g["id"], now + 1) <= now]
            if rows and reason == "check":
                answer = await rt.members.version()
                if answer.state != groups.OK:  # off: freeze once; gone cannot be a version
                    rows = rows if answer.state == groups.OFF and not self._off else []
                elif not self._off:
                    rows = [g for g in rows if g["synced_version"] != answer.version]
            for group in rows:
                await self.sync(group["id"])
        except Unavailable as exc:  # the route is not there yet, or not answering: quiet
            self._unread = True
            rt._throttle(logging.INFO, "lists", "list alerts could not ask their lists: %s", exc)
        except ApiError as exc:
            log.warning("list alerts could not follow their lists just now: %s", exc)
        except Exception:
            log.exception("list alerts could not follow their lists")

    async def sync(self, group_id: int) -> dict[str, Any] | None:
        """One group follows its list: the members route's answer (outside the change lock), then
        add, remove and keep in one transaction (groups.plan decides; this only translates).
        A route that cannot be asked changes nothing. Returns the group's brief."""
        rt = self._running()
        group = await self._write(rt.store.group, OWNER, group_id)
        self._ask.pop(group_id, None)
        if group is None or group["status"] == "stopped" or rt.members is None:
            return None
        try:
            answer = await rt.members.members(group["ref"])
        except Unavailable as exc:
            log.info("the list of group %d could not be read just now: %s", group_id, exc)
            self._unread = True
            return None
        self._unread, self._off = False, answer.state == groups.OFF
        deadline = asyncio.get_running_loop().time() + CHANGE_WAIT_S
        async with self._turn(deadline), self._claims(deadline):
            try:
                brief = await self._follow_list(group_id, answer, deadline)
            finally:
                rt.wake_claims()  # a key no alert holds now is released at the feed
        return brief

    async def _follow_list(self, group_id: int, answer, deadline: float,
                           replan: bool = True) -> dict | None:  # fmt: skip
        rt, config = self.rt, self.rt.config
        now = rt.now_ms()

        def read():
            return rt.store.group(OWNER, group_id), rt.store.group_members(OWNER, group_id), \
                rt.store.alerts(OWNER)  # fmt: skip

        group, members, alerts = await self._write(read)
        if group is None or group["status"] == "stopped":
            return None
        held: dict[str, set[int]] = {}
        for row in alerts:
            if row["status"] == "active":
                held.setdefault(row["instrument_key"], set()).add(row["id"])
        plan = groups.plan(answer.state, answer.rows, [groups.Member(m["id"], m["instrument_key"])
                           for m in members], held, name=group["name"],
                           active=group["status"] == "active", max_instruments=config.max_instruments,
                           max_active=config.max_active, left_out=answer.left_out,
                           list_key=(group["list_key"], answer.list_key))  # fmt: skip
        if plan.action == "stop":
            await self._write(rt.store.stop_group, OWNER, group_id, plan.note, now)
            for row in await self._write(rt.store.group_members, OWNER, group_id):
                rt.owner_put(row)
            log.info("list alert group %d stopped: its list was deleted", group_id)
            brief = _group_brief(await self._write(rt.store.group, OWNER, group_id))
            self._push(now, [{"id": m["id"], "version": m["version"], "status": "stopped",
                              "status_reason": plan.note, "check": None, "fired_today": 0}
                             for m in members], groups=[brief])  # fmt: skip
            return brief
        if plan.action == "freeze" and group["status_reason"] == plan.note:
            return None  # already said
        built, skipped = [], 0
        for row in plan.add:
            try:
                built.append(_member(group, row, now))
            except spec.SpecError:
                skipped += 1  # a row the alerts cannot follow (an expired contract): left out
        if group["status"] == "active" and built:
            await self._claim_all([_probe(m, 0, "active") for m in built], deadline)
        left = list(plan.left_out) + ([groups.LEFT_OUT_OTHER.format(n=quantity(skipped))]
                                      if skipped else [])  # fmt: skip
        frozen = plan.action == "freeze"
        fit = groups.fit_check(config.max_instruments, config.max_active)

        def write():
            kept = group["synced_version"] if frozen else answer.version
            done = rt.store.sync_group(
                OWNER, group_id, now, add=built, remove=list(plan.remove), left_out=left if not frozen
                else group["left_out"], covered=plan.covered - skipped, synced_version=kept,
                fit=fit, note=plan.note)  # fmt: skip
            return done, rt.store.group_members(OWNER, group_id)

        try:
            done, rows = await self._write(write)
        except ApiError as exc:
            if exc.code != "limit":
                raise
            # Review L13: the plan was made on holders that have moved. What the owner removed
            # goes whatever the limits say; the adds are planned again once on fresh numbers.
            done, rows = await self._write(self._removals_only, group, members, plan, now)
            for alert_id in plan.remove:
                rt.owner_drop(alert_id)
            if replan:
                return await self._follow_list(group_id, answer, deadline, replan=False)
        for alert_id in plan.remove:
            rt.owner_drop(alert_id)
        for row in rows:
            if rt.alert(row["id"]) is None:
                rt.owner_put(row)
        if plan.ask_again_at is not None:
            self._ask[group_id] = plan.ask_again_at
        if plan.add or plan.remove:
            log.info("list alert group %d followed its list: %d added, %d removed, %d released",
                     group_id, len(built), len(plan.remove), len(plan.release))  # fmt: skip
        brief = _group_brief(done)
        self._push(now, removed=list(plan.remove), groups=[brief])
        return brief

    # ---- list alerts: the owner's ops (M8.14, A5) --------------------------------------------
    async def _group_add(self, message) -> dict[str, Any]:
        """An alert over a list: one alert per symbol, all or nothing. The list is read first
        (outside the change lock), then the keys are claimed at the feed and the group and its
        members are written in one transaction that checks both limits, so the answer names both
        numbers whether it fits or not."""
        rt, config = self._running(), self.rt.config
        draft = _group_draft(message.get("group"))
        ref = draft.get("ref")
        refusal = groups.ref_refusal(ref) if isinstance(ref, str) and ref else groups.NOT_A_LIST
        if refusal:
            raise ApiError(400, "invalid", refusal, "ref")
        now = rt.now_ms()
        fields = _group_fields(draft, now)
        answer = await self._list_of(ref)
        rows, _ = groups.diff([], answer.rows)
        built, skipped = [], 0
        for row in rows:
            try:
                built.append(_member(fields, row, now))
            except spec.SpecError:
                skipped += 1  # a row the alerts cannot follow (an expired contract): left out
        if not built:
            raise ApiError(400, "invalid", WORDS["no_symbols"], "ref")
        keys = [m["instrument_key"] for m in built]
        held = rt.book.alerts()  # the same numbers the transaction reads, said before any claim
        early = groups.check(keys, {a.key for a in held}, len(held), config.max_instruments,
                             config.max_active)  # fmt: skip
        if not early.ok:
            raise ApiError(409, "limit", early.words)
        left = list(groups.left_out_words(answer.left_out)) + (
            [groups.LEFT_OUT_OTHER.format(n=quantity(skipped))] if skipped else [])  # fmt: skip
        record = fields | {"ref": ref, "covered": len(built), "left_out": left,
                           "synced_version": answer.version, "synced_at": now,
                           "list_key": answer.list_key}  # fmt: skip
        said: list[str] = []
        deadline = asyncio.get_running_loop().time() + CHANGE_WAIT_S
        async with self._turn(deadline), self._claims(deadline):
            try:
                await self._claim_all([_probe(m, 0, "active") for m in built], deadline)
                group = await self._write(
                    rt.store.create_group, OWNER, record, built, now,
                    fit=groups.fit_check(config.max_instruments, config.max_active, "made", said))  # fmt: skip
            finally:
                rt.wake_claims()  # keys claimed for a refused group go at once
            members = await self._write(rt.store.group_members, OWNER, group["id"])
            for row in members:
                rt.owner_put(row)
        log.info("list alert group %d set by the owner over %s: %d alerts", group["id"], ref,
                 len(members))  # fmt: skip
        self._push(now, groups=[_group_brief(group)])
        return {
            "group": self._group_view(group, len(members), now),
            "fit": said[-1] if said else "",
        }

    async def _group_edit(self, message) -> dict[str, Any]:
        """The group's condition, wait, end, note, name or delivery, applied to every member in
        one transaction (each keeps its status and its own instrument). A changed condition starts
        every member's state again and sets Stopped ones going, which the limits must hold."""
        rt, config = self._running(), self.rt.config
        gid, version = _whole(message, "id"), _whole(message, "version")
        now = rt.now_ms()
        group = await self._write(rt.store.group, OWNER, gid)
        if group is None:
            raise ApiError(404, "gone", str(NotFound()))
        draft = _group_draft(message.get("group"))
        fields = _group_fields(draft, now, group)
        if (
            group["status"] == "active"
            and fields["ends_at"] is not None
            and fields["ends_at"] <= now
        ):
            raise ApiError(400, "invalid", WORDS["ended"], "ends")
        changed = spec.changes_condition(_shape(group), _shape(fields))
        said: list[str] = []
        deadline = asyncio.get_running_loop().time() + CHANGE_WAIT_S
        async with self._turn(deadline), self._claims(deadline):
            try:
                if changed:  # Stopped members go again: their keys at the feed first
                    stopped = await self._write(rt.store.group_members, OWNER, gid)
                    waking = [_probe(m | fields, m["id"], "active")
                              for m in stopped if m["status"] == "stopped"]  # fmt: skip
                    if waking:
                        await self._claim_all(waking, deadline)
                group = await self._write(
                    rt.store.update_group, OWNER, gid, version, fields, now, reset_state=changed,
                    fit=groups.fit_check(config.max_instruments, config.max_active, "changed", said))  # fmt: skip
            finally:
                rt.wake_claims()
            members = await self._write(rt.store.group_members, OWNER, gid)
            for row in members:
                rt.owner_put(row, reset=changed)
        log.info("list alert group %d edited by the owner", gid)
        group = await self._after_move(group, members, now)
        self._push(now, [self._member_brief(row) for row in members], groups=[_group_brief(group)])
        return {
            "group": self._group_view(group, len(members), now),
            "fit": said[-1] if said else "",
        }

    async def _group_pause(self, message) -> dict[str, Any]:
        return await self._group_move(message, "pause")

    async def _group_resume(self, message) -> dict[str, Any]:
        return await self._group_move(message, "resume")

    async def _group_move(self, message, move: str) -> dict[str, Any]:
        """Pause or resume: the group and every member that can move do, in one transaction. A
        resume adds Active alerts, so it is held to both limits as a new group is."""
        rt, config = self._running(), self.rt.config
        gid, version = _whole(message, "id"), _whole(message, "version")
        resumes, now, said = move == "resume", rt.now_ms(), []
        deadline = asyncio.get_running_loop().time() + CHANGE_WAIT_S
        async with self._turn(deadline), self._claims(deadline):
            group = await self._write(rt.store.group, OWNER, gid)
            if group is None:
                raise ApiError(404, "gone", str(NotFound()))
            try:
                if resumes and group["status"] == "paused":
                    if group["ends_at"] is not None and group["ends_at"] <= now:
                        raise ApiError(400, "invalid", WORDS["ended"], "ends")
                    waking = [spec.Alert.of(m | {"status": "active"}) for m in await self._write(
                        rt.store.group_members, OWNER, gid) if m["status"] == "paused"]  # fmt: skip
                    held = rt.book.alerts()
                    early = groups.check([a.key for a in waking], {a.key for a in held}, len(held),
                                         config.max_instruments, config.max_active, "changed")  # fmt: skip
                    if not early.ok:
                        raise ApiError(409, "limit", early.words)
                    await self._claim_all(waking, deadline)
                group = await self._write(
                    rt.store.move_group, OWNER, gid, version, move, now,
                    fit=groups.fit_check(config.max_instruments, config.max_active, "changed", said))  # fmt: skip
            finally:
                rt.wake_claims()
            members = await self._write(rt.store.group_members, OWNER, gid)
            for row in members:
                rt.owner_put(row, resume=resumes)
        log.info("list alert group %d: %s by the owner", gid, move)
        group = await self._after_move(group, members, now) if resumes else group
        self._push(now, [self._member_brief(row) for row in members], groups=[_group_brief(group)])
        return {
            "group": self._group_view(group, len(members), now),
            "fit": said[-1] if said else "",
        }

    async def _group_delete(self, message) -> dict[str, Any]:
        """The group and every member go in one transaction; their history stays in the log."""
        rt = self._running()
        gid = _whole(message, "id")
        async with self._changes:
            members = await self._write(rt.store.group_members, OWNER, gid)
            if not await self._write(rt.store.delete_group, OWNER, gid):
                raise ApiError(404, "gone", str(NotFound()))
            for row in members:
                rt.owner_drop(row["id"])
        log.info("list alert group %d deleted by the owner with %d alerts", gid, len(members))
        self._push(rt.now_ms(), removed=[row["id"] for row in members], removed_groups=[gid])
        return {"deleted": True, "alerts": len(members)}

    async def _after_move(self, group: dict, members: list, now: int) -> dict:
        """An edited or resumed group follows its list at once (the design's sync points). The
        change is saved either way: a list that cannot be read, or a feed that is slow, leaves the
        group as it was made and its card says so."""
        if group["status"] == "stopped":
            return group
        try:
            await self.sync(group["id"])
        except ApiError as exc:
            log.warning("list alert group %d could not follow its list just now: %s", group["id"],
                        exc)  # fmt: skip
        return await self._write(self.rt.store.group, OWNER, group["id"]) or group

    async def _list_of(self, ref: str):
        """The list's members from the sealed route, or the owner's words for why not: the
        feature is off, the list is gone, or the route cannot be asked (nothing is made)."""
        members = self.rt.members
        try:
            if members is None:
                raise Unavailable("no members route")
            answer = await members.members(ref)
        except Unavailable as exc:
            log.info("the list %s could not be read just now: %s", ref, exc)
            self._unread = True
            raise ApiError(503, "not_answering", WORDS["lists"]) from None
        self._unread = False
        if answer.state == groups.OFF:
            raise ApiError(409, "off", WORDS["lists_off"])
        if answer.state == groups.GONE:
            raise ApiError(404, "gone", WORDS["list_gone"])
        return answer

    def _member_brief(self, row: Mapping[str, Any]) -> dict[str, Any]:
        rt = self.rt
        return {"id": row["id"], "version": row["version"], "status": row["status"],
                "status_reason": row["status_reason"] or "", "check": None,
                "fired_today": _fired_today(rt.book.state(row["id"]), rt.market.today)}  # fmt: skip

    def _group_view(self, group: Mapping[str, Any], members: int, now: int) -> dict[str, Any]:
        """One group for the page's card: its fields, its words and how many alerts it holds."""
        shape = spec.Alert.of(dict(group, **STAND_IN, expiry=None, status=group["status"]))
        return {
            **{name: group[name] for name in (
                "id", "version", "ref", "name", "kind", "params", "frequency", "candle_min",
                "cooldown_s", "ends_at", "status", "status_reason", "delivery", "digest_min",
                "note", "covered", "left_out", "synced_at", "created_at", "updated_at")},
            "members": members,
            "words": {"title": words.group_title(group["name"], group["kind"], group["params"]),
                      "condition": words.condition(shape), "meta": words.meta(shape, _day(now))},
            "follow": groups.NOT_READ.format(n=quantity(members))
            if self._unread and group["status"] != "stopped" else "",
        }  # fmt: skip

    def _removals_only(self, group, members, plan, now):
        """Runs on the writer: the removals of a plan whose adds did not fit, nothing else, and the
        group's synced version kept so the next check asks again."""
        store = self.rt.store
        done = store.sync_group(
            OWNER, group["id"], now, add=[], remove=list(plan.remove), left_out=group["left_out"],
            covered=len(members) - len(plan.remove), synced_version=group["synced_version"])  # fmt: skip
        return done, store.group_members(OWNER, group["id"])

    # ---- reading -------------------------------------------------------------------------
    async def _state(self, message) -> dict[str, Any]:
        rt = self.rt
        now = rt.now_ms()
        state = {"enabled": True, "engine": self.engine_line(now), "segments": {},
                 "limits": self._limits(), "prefs": rt.prefs, "alerts": [], "last_event_id": 0,
                 "server_time": now}  # fmt: skip
        if not self._running(raising=False):
            return state
        store = rt.store

        def read():
            newest = store.events(OWNER, limit=1)
            return (store.alerts(OWNER), store.states(OWNER), newest[0]["id"] if newest else 0,
                    store.groups(OWNER))  # fmt: skip

        rows, saved, state["last_event_id"], found = await self._write(read)
        views = [view for row in rows if (view := self._view(row, now, saved.get(row["id"])))]
        state["alerts"] = views
        held = Counter(row["group_id"] for row in rows if row["group_id"] is not None)
        state["groups"] = [self._group_view(g, held[g["id"]], now) for g in found]
        state["groups_by_ref"] = {}  # which watchlist refs carry a group: the page syncs those
        for group in found:
            state["groups_by_ref"].setdefault(group["ref"], []).append(group["id"])
        for segment in sorted({view["instrument_key"].partition("|")[0] for view in views}):
            state["segments"][segment] = self.line(segment, now)
        return state

    async def _events(self, message) -> dict[str, Any]:
        rt = self._running()
        limit = _count(message, "limit", PAGE, PAGE_MAX)
        rows = await self._write(rt.store.events, OWNER, before=_whole(message, "before", None),
                                 limit=limit + 1, alert_id=_whole(message, "alert", None))  # fmt: skip
        return {"events": [_event(row) for row in rows[:limit]], "more": len(rows) > limit}

    async def _events_since(self, message) -> dict[str, Any]:
        rt = self._running()
        limit = _count(message, "limit", SINCE, SINCE_MAX)
        rows = await self._write(rt.store.events_since, OWNER, _whole(message, "after"), limit + 1)
        return {"events": [_event(row) for row in rows[:limit]], "more": len(rows) > limit}

    async def _prefs_get(self, message) -> dict[str, bool]:
        rt = self._running()
        return await self._write(rt.store.prefs, OWNER)

    # ---- changing ------------------------------------------------------------------------
    async def _create(self, message) -> dict[str, Any]:
        rt = self._running()
        fields = _draft(message.get("alert"))
        now = rt.now_ms()
        checked = _checked(fields, now)
        deadline = asyncio.get_running_loop().time() + CHANGE_WAIT_S
        async with self._turn(deadline):
            probe = _probe(checked, 0, "active")
            self._admit(probe, None)
            async with self._claims(deadline):
                try:
                    await self._claim(probe, deadline)
                    row = await self._write(rt.store.create, OWNER, checked, now)
                finally:
                    rt.wake_claims()  # a claim made for a refused change goes at once
                rt.owner_put(row)
        log.info("market alert %d set by the owner", row["id"])
        return self._pushed(self._view(row, now), now)

    async def _update(self, message) -> dict[str, Any]:
        rt = self._running()
        alert_id, version = _whole(message, "id"), _whole(message, "version")
        action = message.get("action")
        if action not in ACTIONS:
            raise ApiError(400, "invalid", WORDS["action"], "action")
        now = rt.now_ms()
        deadline = asyncio.get_running_loop().time() + CHANGE_WAIT_S
        async with self._turn(deadline):
            current = await self._write(rt.store.alert, OWNER, alert_id)
            if current is None:
                raise ApiError(404, "gone", str(NotFound()))
            if action == "edit":
                row = await self._edit(current, version, _draft(message.get("alert")), now,
                                       deadline)  # fmt: skip
            else:
                row = await self._move(current, version, action, now, deadline)
        log.info("market alert %d: %s by the owner", alert_id, action)
        return self._pushed(self._view(row, now), now)

    async def _edit(self, current: dict, version: int, fields: dict, now: int,
                    deadline: float) -> dict:  # fmt: skip
        rt = self.rt
        checked = _checked(fields, now)
        if "ends" not in fields:  # an edit that does not name the end keeps it
            checked["ends_at"] = current["ends_at"]
        changed = spec.changes_condition(current, checked)
        status = spec.status_after_edit(current["status"], changed)
        if status == "active" and checked["ends_at"] is not None and checked["ends_at"] <= now:
            raise ApiError(400, "invalid", WORDS["ended"], "ends")
        probe = _probe(checked, current["id"], status)
        self._admit(probe, current["id"])
        async with self._claims(deadline):
            try:
                if status == "active":
                    await self._claim(probe, deadline)
                row = await self._write(rt.store.update, OWNER, current["id"], version, checked,
                                        now, reset_state=changed)  # fmt: skip
            finally:
                rt.wake_claims()
            rt.owner_put(row, reset=changed)
        return row

    async def _move(self, current: dict, version: int, action: str, now: int,
                    deadline: float) -> dict:  # fmt: skip
        rt = self.rt
        resumes = action in ("resume", "restart")
        async with self._claims(deadline):
            try:
                if resumes and current["status"] == MOVES[action][0]:
                    probe = spec.Alert.of(current | {"status": "active"})
                    if stop_reason(probe, now) is None:  # else the store says why, in words
                        self._admit(probe, current["id"])
                        await self._claim(probe, deadline)
                row = await self._write(getattr(rt.store, action), OWNER, current["id"], version,
                                        now)  # fmt: skip
            finally:
                rt.wake_claims()
            rt.owner_put(row, resume=resumes)
        return row

    async def _delete(self, message) -> dict[str, bool]:
        rt = self._running()
        alert_id = _whole(message, "id")
        async with self._changes:
            if not await self._write(rt.store.delete, OWNER, alert_id):
                raise ApiError(404, "gone", str(NotFound()))
            rt.owner_drop(alert_id)
        log.info("market alert %d deleted by the owner", alert_id)
        self._push(rt.now_ms(), removed=[alert_id])
        return {"deleted": True}

    async def _prefs_set(self, message) -> dict[str, bool]:
        rt = self._running()
        telegram, app = message.get("telegram"), message.get("app")
        if not isinstance(telegram, bool) or not isinstance(app, bool):
            raise ApiError(400, "invalid", WORDS["prefs"], "channels")
        async with self._changes:
            prefs = await self._write(rt.store.set_prefs, OWNER, telegram=telegram, app=app,
                                      now=rt.now_ms())  # fmt: skip
            rt.use_prefs(prefs)
        return prefs

    async def _test(self, message) -> dict[str, Any]:
        """One test through each channel that is on in the settings (M10.14): a line on
        Telegram and a toast in the open apps, logged like a fire so History shows each delivery;
        their answers are waited for up to 10 s."""
        rt = self._running()
        now = rt.now_ms()
        prefs = rt.prefs
        channels = {"telegram": prefs["telegram"]}
        if rt.pusher is not None:  # the app's channel (M10.7)
            channels["app"] = prefs["app"]
        event_id = await self._write(rt.store.record_test, OWNER, words.TEST_TITLE,
                                     words.TEST_LINE, now, channels,
                                     detail=words.TEST_DETAIL)  # fmt: skip
        try:
            waiters = []
            if channels.get("app"):
                waiters.append(rt.push_test(event_id, now))
            if channels["telegram"]:
                waiters.append(rt.tell(event_id, words.TEST_LINE, now))
            if waiters:
                await asyncio.wait(waiters, timeout=TEST_WAIT_S)
        finally:
            rt.forget_waiter(event_id)
        found = await self._write(rt.store.events, OWNER, before=event_id + 1, limit=1)
        deliveries = found[0]["deliveries"] if found and found[0]["id"] == event_id else {}
        log.info("a market alert test was sent from the Alerts page")
        return {"event_id": event_id, "deliveries": deliveries}

    # ---- the screener's digests (M9.18, SV10) -----------------------------------------------
    async def _screener_digest(self, message) -> dict[str, Any]:
        """One run of a screen worth telling, sealed by sudarshan-screener under its own purpose
        (the API opened it). Recorded once under ``scr:<screen_id>:<run_key>``: a retried post
        answers "already told" and sends nothing twice. The Telegram line is composed here
        (words.screener_line) and goes through the market lane; the toast to the open apps."""
        rt = self._running()
        digest = message.get("args")
        if not isinstance(digest, dict):
            raise ApiError(400, "bad_request", "The screener's digest could not be read.")
        screen_id, run_key = digest.get("screen_id"), digest.get("run_key")
        if not isinstance(screen_id, int) or not isinstance(run_key, str) or not run_key:
            raise ApiError(400, "bad_request", "The screener's digest names no screen or run.")
        for name in ("entered", "exited"):
            if not isinstance(digest.get(name, []), list):
                raise ApiError(400, "bad_request", "The screener's digest could not be read.")
        now = rt.now_ms()
        at = digest.get("at") if isinstance(digest.get("at"), int) else now
        claim = f"scr:{screen_id}:{run_key}"
        have = await self._write(rt.store.event_by_claim, OWNER, claim)
        if have is not None:
            return {
                "claimed": False,
                "event_id": have["id"],
                "deliveries": have.get("deliveries", {}),
            }
        said = {
            "title": f"{words.SCREENER_TITLE}: {words.clean(digest.get('screen') or '', words.SCREENER_NAME_MAX)}",
            "detail": words.screener_detail(digest),
            "note": words.screener_note(digest),
        }
        line = words.screener_line(digest, at)
        prefs = rt.prefs
        channels = {"telegram": prefs["telegram"]}
        if rt.pusher is not None:
            channels["app"] = prefs["app"]
        event_id = await self._write(
            rt.store.record_screener, OWNER, claim, title=said["title"], line=line,
            detail=said["detail"], note=said["note"], now=now, channels=channels,
        )  # fmt: skip
        if event_id is None:  # another post of the same run got in first
            have = await self._write(rt.store.event_by_claim, OWNER, claim)
            return {"claimed": False, "event_id": have["id"] if have else None, "deliveries": {}}
        if channels["telegram"]:
            rt.tell_digest(event_id, line, at)
        if channels.get("app"):
            rt.push_digest(event_id, digest, said, at)
        log.info("screener digest recorded for screen %s (%s)", screen_id, run_key)
        return {"claimed": True, "event_id": event_id, "deliveries": channels}

    # ---- what the open apps are told at once (M10.7) ---------------------------------------
    def _changed(self, alert_id: int, reason: str) -> None:
        """An alert stopped or paused itself: every open app's card follows at once."""
        rt = self.rt
        alert = rt.alert(alert_id)
        if alert is None:
            return
        now = rt.now_ms()
        brief = {"id": alert.id, "version": alert.version, "status": alert.status,
                 "status_reason": reason, "check": None,
                 "fired_today": _fired_today(rt.book.state(alert.id), rt.market.today)}  # fmt: skip
        self._push(now, [brief])

    def _second(self, now: int) -> None:
        self._group_step(now)
        self._tick(now)

    def _tick(self, now: int) -> None:
        """Every second: a new line under "Alerts" (the market opened or closed, the feed went
        down or came back) reaches the open apps, which then read the page again."""
        if self.rt.pusher is None:
            return
        line = self.engine_line(now)
        if line != self._line:
            self._line = line
            self._push(now, engine=line)

    def _pushed(self, view: dict | None, now: int) -> dict | None:
        """An owner's change, for the owner's other screens: the card's status and version (a
        version newer than one a screen holds sends it to read the page again)."""
        if view is not None:
            self._push(now, [{name: view[name] for name in BRIEF}])
        return view

    def _push(self, now: int, alerts=(), *, removed=(), engine=None, groups=(),
              removed_groups=()) -> None:  # fmt: skip
        if self.rt.pusher is None or not (alerts or removed or engine or groups or removed_groups):
            return
        data = {"engine": engine or self.engine_line(now), "alerts": list(alerts),
                "removed": list(removed), "server_time": now}  # fmt: skip
        if groups:
            data["groups"] = list(groups)
        if removed_groups:
            data["removed_groups"] = list(removed_groups)
        self.rt.push_state(data)

    # ---- the page's lines ----------------------------------------------------------------
    def engine_line(self, now: int) -> dict[str, Any]:
        """The line under "Alerts": the best of the lines of the sessions with Active alerts."""
        rt = self.rt
        if rt.state == "starting":
            return _line("starting", words.STARTING)
        if rt.state != "running":
            return _line("off", rt.detail or words.STOPPED_UNEXPECTEDLY)
        market, classes = rt.market, {}
        for alert in rt.book.alerts():
            cas = alert.key in market.auction
            classes.setdefault(alert.segment + AUCTION if cas else alert.segment, []).append(alert)
        lines = [
            self.line(cls, now, printed=all(rt.book.printed(a.key, market.today) for a in held))
            for cls, held in classes.items()
        ] or [self.line(segment, now) for segment in MAIN]
        return min(lines, key=lambda line: ORDER.index(line["code"]))

    def line(self, cls: str, now: int, *, quotes: bool = False, printed: bool = False) -> dict:
        """One session's line: 'Watching · Real-time', 'Market closed · checks from Mon 09:15',
        'Not checking since 10:41:05 · the price feed is reconnecting'."""
        rt = self.rt
        market = rt.market
        code = clock.status(market, cls, now, printed=printed)
        if code in (clock.OK, clock.CLOSING_AUCTION) and rt.down_since is not None:
            code = clock.FEED_DOWN  # paused: silent, or its health overdue (review A7.e)
        opens = auction = None
        if code in (clock.CLOSED_TODAY, clock.OUTSIDE_HOURS):
            opens = self._opens(cls, now)
        if code == clock.CLOSING_AUCTION:  # "price at about 15:30" (X6j)
            session = market.sessions[cls]
            auction = (session.end, session.auction_close)
        text = words.check_words(code, now=now, opens_at=opens, since=rt.down_since,
                                 feed=market.feed, auction=auction, quotes=quotes)  # fmt: skip
        return _line(code, text, code == clock.OK)

    def check(self, alert: spec.Alert, now: int) -> dict[str, Any] | None:
        """An Active alert's own line when it is not simply watching: its session's line, what
        it waits for from the feed, or a note from today; None while it watches."""
        rt = self.rt
        cas = alert.key in rt.market.auction
        quotes = alert.cond.source != "ltp"
        printed = cas and rt.book.printed(alert.key, rt.market.today)
        line = self.line(alert.segment + AUCTION if cas else alert.segment, now, quotes=quotes,
                         printed=printed)  # fmt: skip
        if line["code"] != clock.OK:
            return line
        waiting = rt.book.waiting(alert.id)
        if waiting is not None:
            return _line("waiting", words.note_words(Reason(waiting, now)), True)
        if rt.no_prices(alert.key, now):  # design 2.3, review M7
            return _line("waiting", words.NO_PRICES, True)
        if quotes and (rt.last_frame(alert.key) or {}).get(alert.cond.source) is None:
            return _line("waiting", words.quote_waiting(alert.cond.source), True)  # A7.i
        noted = rt.note_of(alert.id)
        if noted is not None and noted[0].code in NOTED and _day(noted[1]) == _day(now):
            return _line("note", words.note_words(noted[0]), True)
        return None

    def _opens(self, cls: str, now: int) -> int | None:
        segment, _, cas = cls.partition("+")
        days = self.rt.days
        found = next_session(segment, now, is_trading_day=days.calendar.is_trading_day,
                             window_of=days.window, auction=bool(cas))  # fmt: skip
        return found.start if found is not None else None

    # ---- inside --------------------------------------------------------------------------
    def _view(self, row: dict, now: int, saved=None) -> dict[str, Any] | None:
        """One alert for the page (the contract's AlertRow); None for a row this release
        cannot read (it is skipped, as the runtime skips it)."""
        rt = self.rt
        try:
            alert = spec.Alert.of(row)
        except Exception:
            log.warning("market alert %s could not be read; it is left off the page", row.get("id"))
            return None
        state = rt.book.state(alert.id) or saved
        today = rt.market.today
        session = rt.market.sessions.get(alert.segment + AUCTION)
        auction = None
        if alert.key in rt.market.auction and session is not None:  # X6m
            auction = {"from": session.end, "close": session.auction_close,
                       "until": session.auction_until}  # fmt: skip
        return {
            **{name: row[name] for name in (
                "id", "version", "status", "kind", "symbol", "exchange", "instrument_key",
                "tick_size", "expiry", "params", "frequency", "candle_min", "cooldown_s",
                "ends_at", "channels", "created_at", "updated_at", "group_id")},
            "status_reason": row["status_reason"] or "",
            "note": row["note"] or "",
            "words": {
                "condition": words.condition(alert),
                "meta": words.meta(alert, _day(now)),
                "checks_at": words.checks_at(alert),
            },
            "check": self.check(alert, now) if alert.status == "active" else None,
            "fired_today": _fired_today(state, today),
            "auction": auction,
        }  # fmt: skip

    def _limits(self) -> dict[str, int]:
        rt = self.rt
        return {"instruments": rt.config.max_instruments, "instruments_used": len(rt.book.modes()),
                "active": rt.config.max_active, "active_used": len(rt.book.alerts())}  # fmt: skip

    def _admit(self, after: spec.Alert, before: int | None) -> None:
        """Refuse a change that would add past a limit; one that adds nothing passes."""
        rt, config = self.rt, self.rt.config
        if after.status != "active":
            return
        active = {alert.id: alert for alert in rt.book.alerts()}
        if before not in active and len(active) + 1 > config.max_active:
            raise ApiError(409, "limit", WORDS["active"].format(n=quantity(config.max_active)))
        if after.key not in rt.book.modes():
            keys = {alert.key for alert in active.values() if alert.id != before} | {after.key}
            if len(keys) > config.max_instruments:
                n = quantity(config.max_instruments)
                raise ApiError(409, "limit", WORDS["instruments"].format(n=n))

    @contextlib.asynccontextmanager
    async def _turn(self, deadline: float):
        """The one change at a time, waited for until ``deadline``: a change stuck behind a slow
        one is told it was not saved, and never lands afterwards (review M1)."""
        try:
            async with asyncio.timeout_at(deadline):
                await self._changes.acquire()
        except TimeoutError:
            raise ApiError(503, "feed", WORDS["feed_silent"]) from None
        try:
            yield
        finally:
            self._changes.release()

    @contextlib.asynccontextmanager
    async def _claims(self, deadline: float):
        """The claims job's lock, waited for until ``deadline`` (only the wait is timed)."""
        held = self.rt.claims_held(deadline)
        try:
            await held.__aenter__()
        except TimeoutError:
            raise ApiError(503, "feed", WORDS["feed_silent"]) from None
        try:
            yield
        finally:
            await held.__aexit__(None, None, None)

    async def _claim(self, alert: spec.Alert, deadline: float) -> None:
        """The alert's key at the feed, in the mode it needs, before the alert is saved: full for
        a stock that closes by auction, whose print is known by its volume (X6i)."""
        await self._claim_all([alert], deadline)

    async def _claim_all(self, alerts: list[spec.Alert], deadline: float) -> None:
        """The keys of several alerts at the feed in one request (a list's new members)."""
        rt = self.rt
        modes = rt.book.modes(full=rt.market.auction)
        added = False
        for alert in alerts:
            held = modes.get(alert.key)
            need = "full" if rt.closes_by_auction(alert) else alert.cond.feed
            if held in ("full", need):
                continue
            modes[alert.key] = "full" if "full" in (held, need) else need
            added = True
        if not added:
            return
        left = deadline - asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout_at(deadline):
                await rt.link.put_claims(modes, timeout=max(0.5, left))
        except TimeoutError:
            raise ApiError(503, "feed", WORDS["feed_silent"]) from None
        except FeedRefused as exc:
            log.warning("The price feed refused an instrument for a market alert: %s", exc)
            raise ApiError(409, "feed", WORDS["feed_full"]) from None
        except FeedUnreachable:
            raise ApiError(503, "feed", WORDS["feed_silent"]) from None

    async def _write(self, fn, *args, **kwargs):
        """One market.db call on the runtime's writer, its refusals in the owner's words."""
        try:
            return await self.rt.write(fn, *args, **kwargs)
        except NotFound as exc:
            raise ApiError(404, "gone", str(exc)) from None
        except VersionConflict as exc:
            raise ApiError(409, "conflict", str(exc)) from None
        except Refused as exc:
            raise ApiError(409, "refused", str(exc)) from None
        except GroupLimit as exc:  # the limits' words carry both numbers
            raise ApiError(409, "limit", str(exc)) from None
        except GroupRefused as exc:
            raise ApiError(409, "refused", str(exc)) from None
        except StoreError as exc:
            log.error("market.db refused an owner's request: %s", exc.cause)
            raise ApiError(503, "record", str(exc)) from None

    def _running(self, raising: bool = True):
        rt = self.rt
        if rt.state == "running" and None not in (rt.store, rt.link, rt.lane):
            return rt
        if not raising:
            return None
        if rt.state == "starting":
            raise ApiError(503, "starting", WORDS["starting"])
        if rt.state == "off":
            raise ApiError(503, "stopped", rt.detail or words.STOPPED_UNEXPECTEDLY)
        raise ApiError(503, "stopping", WORDS["stopping"])


def _fired_today(state, today: int) -> int:
    return state.fires_session if state is not None and state.session == today else 0


def _line(code: str, text: str, real_time: bool = False) -> dict[str, Any]:
    return {"code": code, "line": text, "real_time": real_time}


def _group_brief(group: Mapping[str, Any]) -> dict[str, Any]:
    """What the open apps need of a group after a sync: its card's status and words."""
    return {name: group[name] for name in (
        "id", "version", "status", "status_reason", "covered", "left_out", "synced_at")}  # fmt: skip


def _member(group: Mapping[str, Any], row, now: int) -> dict[str, Any]:
    """One list member as the alert the group makes of it: the group's condition on the row's
    instrument (spec.check, so a row the alerts cannot follow raises SpecError)."""
    made = spec.check({
        "instrument_key": row.instrument_key, "symbol": row.symbol, "exchange": row.exchange,
        "tick_size": row.tick_size, "expiry": row.expiry, "kind": group["kind"],
        "params": group["params"], "frequency": group["frequency"],
        "candle_min": group["candle_min"], "cooldown_s": group["cooldown_s"],
        "note": group["note"]}, now)  # fmt: skip
    return made | {"ends_at": group["ends_at"]}


def _shape(fields: Mapping[str, Any]) -> dict[str, Any]:
    """What a group's condition is made of, on one stand-in instrument, for spec.changes_condition."""
    return {"instrument_key": STAND_IN["instrument_key"], "kind": fields["kind"],
            "params": fields["params"], "frequency": fields["frequency"],
            "candle_min": fields["candle_min"]}  # fmt: skip


def _group_draft(value) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApiError(400, "draft", WORDS["draft"])
    return {name: value[name] for name in GROUP_DRAFT if name in value}


def _group_fields(
    draft: Mapping[str, Any], now: int, base: Mapping[str, Any] | None = None
) -> dict:
    """A list alert's shared fields as the store keeps them, the condition checked once on a
    stand-in instrument. An edit (``base`` is the group now) keeps its kind, and what the page did
    not name: the name, the delivery and the end."""
    kind = draft.get("kind", base["kind"] if base else None)
    if base is not None and kind != base["kind"]:
        raise ApiError(400, "invalid", WORDS["same_kind"], "kind")
    if kind not in groups.KINDS:
        raise ApiError(400, "invalid", groups.PRICE_LEVEL, "kind")
    name = clean(str(draft.get("name", base["name"] if base else "") or ""), GROUP_NAME_MAX)
    if not name:
        raise ApiError(400, "invalid", WORDS["name"], "name")
    delivery = draft.get("delivery", base["delivery"] if base else "first_then_digest")
    digest = draft.get("digest_min", base["digest_min"] if base else 5)
    if delivery not in GROUP_DELIVERY or isinstance(digest, bool) or not isinstance(digest, int) \
            or not 1 <= digest <= 60:  # fmt: skip
        raise ApiError(400, "invalid", WORDS["delivery"], "delivery")
    shared = {k: v for k, v in draft.items() if k not in ("ref", "name", "delivery", "digest_min")}
    checked = _checked({**shared, **STAND_IN, "kind": kind}, now)
    if base is not None and "ends" not in draft:
        checked["ends_at"] = base["ends_at"]
    return {**{k: checked[k] for k in ("kind", "params", "frequency", "candle_min", "cooldown_s",
                                       "ends_at", "note")},
            "name": name, "delivery": delivery, "digest_min": digest}  # fmt: skip


def _probe(fields: Mapping[str, Any], alert_id: int, status: str) -> spec.Alert:
    """The alert a change would make, for its limits and its claim."""
    return spec.Alert.of(dict(fields, id=alert_id, version=1, status=status))


def _draft(value) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApiError(400, "draft", WORDS["draft"])
    return {name: value[name] for name in DRAFT if name in value}


def _checked(fields: Mapping[str, Any], now: int) -> dict[str, Any]:
    try:
        return spec.check(fields, now)
    except spec.SpecError as exc:
        raise ApiError(400, "invalid", exc.message, exc.field) from None


_REQUIRED = object()


def _whole(message: Mapping[str, Any], name: str, default=_REQUIRED) -> int | None:
    value = message.get(name)
    if value is None and default is not _REQUIRED:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiError(400, "invalid", WORDS["number"], name)
    return value


def _count(message: Mapping[str, Any], name: str, default: int, most: int) -> int:
    value = _whole(message, name, default)
    return max(1, min(value, most))


def _event(row: Mapping[str, Any]) -> dict[str, Any]:
    """One History entry (the contract's AlertEvent)."""
    return {
        "id": row["id"], "alert_id": row["alert_id"], "at": row["fired_at"],
        "trade_time": row["trade_time"], "symbol": row["symbol"], "exchange": row["exchange"],
        "kind": row["kind"], "title": row["title"], "detail": row["detail"], "note": row["note"],
        "line": row["line"], "value": row["value"],
        "level": row["level"], "found_later": row["found_later"], "opening": row["opening"],
        "test": row["test"], "deliveries": row["deliveries"],
    }  # fmt: skip


def _day(ms: int):
    return datetime.fromtimestamp(ms / 1000, IST).date()
