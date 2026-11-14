"""The family viewer API under /family/api (M2.6, M2.10, M2.16; design 2.2, 6.3, 6.4, 6.7).

What a family phone reaches through nginx, cookie-authenticated, and nothing else. The routes are
the contract's (``contract.VIEWER_OPS``); this module serves the sign-in and the member's own
account, and the lanes of the data plane, notifications and Telegram fill in theirs (until then
they answer "not ready" to a signed-in member).

* **The session cookie** ``__Host-sudarshan_family`` is ``Secure; HttpOnly; SameSite=Strict;
  Path=/; Max-Age`` 30 days, with no Domain (the ``__Host-`` prefix makes the browser hold all of
  that). OpenAlgo never reads it, so a viewer is an anonymous visitor to every OpenAlgo route.
  A request whose session has ended is answered 401 in the member's words and the cookie is
  overwritten with ``Max-Age=0`` (never ``Clear-Site-Data``, which would also clear the owner's
  cookie on a phone he uses too).
* **CSRF**: ``__Host-sudarshan_family_csrf`` (readable by the page; set on any answer to a request
  that had none, and renewed at every sign-in) and the header ``X-Family-CSRF`` carrying its value
  on every request that is not a GET, compared in constant time; an Origin header, when sent, must
  be this host; a body must be JSON. SameSite=Strict already stops a cross-site request in every
  current browser; this is the cheap second lock.
* **Words**: every answer is a sentence for a person (``words.py``), and a wrong sign-in name, a
  wrong password and a wrong code get the same one.
* **The phone cookie** ``__Host-sudarshan_family_phone`` (HttpOnly, 90 days, the session cookie's
  other attributes) is set after a right sign-in, a join and a password change, and kept through a
  sign-out: it is how the member's own phone still signs in while strangers have locked the name
  (``lockout.py``). Sign-ins are also counted by the network area of the address nginx gives.

The WebSocket's handshake is checked here too (``authorize``): the same cookie, the same session
check, the same rule for Origin.
"""

from __future__ import annotations

import hmac
import json
import secrets
from typing import Any
from urllib.parse import urlsplit

from . import lockout, sessions, words
from .accounts import Accounts, Outcome, device_from
from .contract import (
    COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    LIMITS,
    PHONE_COOKIE,
    PHONE_DAYS,
    VIEWER_PREFIX,
    viewer_op,
)
from .errors import FamilyError
from .listener import Request, Response
from .store import Secrets

MAX_AGE_S = LIMITS["session_days"] * 86_400
PHONE_AGE_S = PHONE_DAYS * 86_400
CSRF_BYTES = 32
_ATTRS = "Path=/; Secure; SameSite=Strict"


def session_cookie(session_id: str) -> tuple[str, str]:
    return ("Set-Cookie", f"{COOKIE}={session_id}; Max-Age={MAX_AGE_S}; {_ATTRS}; HttpOnly")


def cleared_cookie() -> tuple[str, str]:
    return ("Set-Cookie", f"{COOKIE}=; Max-Age=0; {_ATTRS}; HttpOnly")


def csrf_cookie(token: str) -> tuple[str, str]:
    return ("Set-Cookie", f"{CSRF_COOKIE}={token}; Max-Age={MAX_AGE_S}; {_ATTRS}")


def phone_cookie(value: str) -> tuple[str, str]:
    return ("Set-Cookie", f"{PHONE_COOKIE}={value}; Max-Age={PHONE_AGE_S}; {_ATTRS}; HttpOnly")


def cookies_of(request: Request) -> dict[str, str]:
    """The request's cookies by name, the first of each (a phone may hold the owner's too)."""
    found: dict[str, str] = {}
    for part in request.headers.get("cookie", "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and name not in found:
            found[name] = value.strip()
    return found


def token_ok(value: str | None) -> bool:
    return bool(value) and len(value) <= 100 and value.replace("-", "").replace("_", "").isalnum()


def hidden_of(privacy: dict[str, bool]) -> list[str]:
    """The categories a member does not see; quantities hidden hide P&L too (cross-design Y24)."""
    hidden = [c for c in ("pnl", "qty", "funds", "orders") if privacy.get(c) is not True]
    if "qty" in hidden and "pnl" not in hidden:
        hidden.insert(0, "pnl")
    return hidden


def me_of(session: sessions.Session) -> dict[str, Any]:
    """The member asking, as their phone reads them (the contract's ``Me``)."""
    member, row = session.member, session.row
    return {
        "member": {k: member[k] for k in ("id", "handle", "display_name", "role", "language")},
        "privacy": member["privacy"],
        "hidden": hidden_of(member["privacy"]),
        "limited": member["role"] == "child",
        "device": sessions.device(row, session.id_hash),
        "expires_at": min(row["absolute_expires_at"], row["idle_expires_at"]),
    }


def done(key: str | None = None) -> dict[str, Any]:
    return {"ok": True, "words": words.say(key) if key else None}


class ViewerApi:
    """The viewer API, for the service's router (``http``) and its WebSocket door (``authorize``)."""

    def __init__(self, accounts: Accounts, keys: Secrets | None):
        self.accounts, self.keys = accounts, keys

    # ---- what a request carries ----
    def _device(self, request: Request) -> sessions.Device:
        address = request.headers.get("x-real-ip") or (request.peer or ("",))[0]
        place = area = None
        if self.keys is not None and address:
            place = self.keys.place(address)
            area = self.keys.keyed("area", lockout.area_of(address))
        return device_from(request.headers.get("user-agent"), place, area=area,
                           phone=cookies_of(request).get(PHONE_COOKIE))  # fmt: skip

    @staticmethod
    def _same_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None:
            return True
        return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()

    @staticmethod
    def _args(request: Request, parts: dict[str, str]) -> dict[str, Any]:
        if request.method in ("GET", "DELETE"):
            return {**request.query, **parts}
        if request.body:
            kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if kind != "application/json":
                raise FamilyError("invalid")
            try:
                body = json.loads(request.body)
            except (ValueError, UnicodeDecodeError):
                raise FamilyError("invalid") from None
            if not isinstance(body, dict):
                raise FamilyError("invalid")
            return {**body, **parts}
        return dict(parts)

    # ---- the doors ----
    async def http(self, request: Request) -> Response:
        cookies = cookies_of(request)
        extra = [] if token_ok(cookies.get(CSRF_COOKIE)) else [csrf_cookie(new_csrf())]
        try:
            response = await self._route(request, cookies)
        except FamilyError as exc:
            response = Response.refused(exc)
            if exc.code == "signed_out" and COOKIE in cookies:
                response.headers.append(cleared_cookie())
        if not any(value.startswith(CSRF_COOKIE + "=") for _, value in response.headers):
            response.headers.extend(extra)
        return response

    async def _route(self, request: Request, cookies: dict[str, str]) -> Response:
        found = viewer_op(request.method, request.path[len(VIEWER_PREFIX) :])
        if found is None:
            raise FamilyError("not_found")
        op, parts = found
        if request.method != "GET":
            sent = request.headers.get(CSRF_HEADER.lower(), "")
            kept = cookies.get(CSRF_COOKIE, "")
            if not self._same_origin(request) or not token_ok(kept):
                raise FamilyError("csrf")
            if not hmac.compare_digest(sent.encode(), kept.encode()):
                raise FamilyError("csrf")
        args = self._args(request, parts)
        session = await self.accounts.check(cookies.get(COOKIE)) if op.auth == "session" else None
        handler = getattr(self, f"_{op.name}", None)
        if handler is None:
            raise FamilyError("not_ready")
        return await handler(request, args, session)

    async def authorize(self, request: Request) -> sessions.Session:
        """The WebSocket's handshake: this host's page, a session that signs its member in."""
        if not self._same_origin(request):
            raise FamilyError("csrf")
        return await self.accounts.check(cookies_of(request).get(COOKIE))

    # ---- sign-in ----
    @staticmethod
    def _signed_in(outcome: Outcome) -> Response:
        cookies = [session_cookie(outcome.session_id or ""), csrf_cookie(new_csrf())]
        if outcome.phone:
            cookies.append(phone_cookie(outcome.phone))
        return Response.ok(me_of(outcome.session), cookies)  # type: ignore[arg-type]

    async def _join_info(self, request: Request, args: dict, session: Any) -> Response:
        return Response.ok(await self.accounts.join_info(args.get("code"), self._device(request)))

    async def _join(self, request: Request, args: dict, session: Any) -> Response:
        outcome = await self.accounts.join(args.get("code"), args.get("handle"),
                                           args.get("password"), args.get("totp"),
                                           self._device(request))  # fmt: skip
        return self._signed_in(outcome)

    async def _login(self, request: Request, args: dict, session: Any) -> Response:
        outcome = await self.accounts.login(args.get("handle"), args.get("password"),
                                            args.get("totp"), self._device(request))  # fmt: skip
        return self._signed_in(outcome)

    # ---- the member's own account ----
    async def _me(self, request: Request, args: dict, session: sessions.Session) -> Response:
        return Response.ok(me_of(session))

    async def _logout(self, request: Request, args: dict, session: sessions.Session) -> Response:
        await self.accounts.logout(session)
        return Response.ok(done(), [cleared_cookie()])

    async def _logout_all(self, request: Request, args: dict,
                          session: sessions.Session) -> Response:  # fmt: skip
        await self.accounts.logout(session, everywhere=True)
        return Response.ok(done("signed_out_everywhere"), [cleared_cookie()])

    async def _password(self, request: Request, args: dict, session: sessions.Session) -> Response:
        outcome = await self.accounts.change_password(
            session, args.get("current"), args.get("password"), args.get("totp"),
            self._device(request),
        )  # fmt: skip
        return Response.ok(done("password_changed"),
                           [phone_cookie(outcome.phone)] if outcome.phone else [])  # fmt: skip

    async def _devices(self, request: Request, args: dict, session: sessions.Session) -> Response:
        return Response.ok(await self.accounts.devices(session))

    async def _device_rename(self, request: Request, args: dict,
                             session: sessions.Session) -> Response:  # fmt: skip
        await self.accounts.rename(session, args.get("id"), args.get("label"))
        return Response.ok(done())

    async def _device_signout(self, request: Request, args: dict,
                              session: sessions.Session) -> Response:  # fmt: skip
        await self.accounts.sign_out_device(session, args.get("id"))
        mine = args.get("id") == session.device_id
        return Response.ok(done(), [cleared_cookie()] if mine else [])


def new_csrf() -> str:
    return secrets.token_urlsafe(CSRF_BYTES)
