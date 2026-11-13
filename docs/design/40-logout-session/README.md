# 40 - Logout And Session Lifecycle

## Session Types

OpenAlgo maintains separate but related state:

| State | Meaning |
|---|---|
| Flask app session | Browser is authenticated to the local application |
| Active-session row | Device/IP/login/last-seen audit for the app session |
| Broker auth row | Installation's current broker and encrypted auth/feed tokens |
| OpenAlgo API key | External/internal service authentication that resolves the broker row |

Multiple app devices share one broker auth row and one server-side broker market-data feed.

## Login And Heartbeat

Successful app/broker login assigns `session_id` and registers an `active_sessions` row. The database caps the user at five rows, replaces an existing same-user/same-IP row, and removes the oldest at the cap.

The React app reads `/auth/session-status`. For logged-in sessions, that request touches `last_seen` at most once per 30 seconds using a timestamp in the signed Flask session. This makes the security dashboard liveness field meaningful without writing on every poll.

## Broker Token Rollover

The default daily expiry time is 03:00 IST. `DISABLE_SESSION_EXPIRY=true` supports 24/7 crypto deployments. Request guards apply expiry and revoke broker access according to this policy.

Broker expiry does not necessarily invalidate the local app session. When `/auth/session-status` finds an authenticated browser but no usable broker token, it preserves `logged_in` and returns `broker_session_expired: true`. The UI can then render broker reconnect and `/auth/broker` can admit the user.

## Multi-Device Resume

On resume, `upsert_auth()` compares decrypted broker/feed tokens plus broker and revoke state. If nothing material changed, it does not publish a teardown invalidation. This prevents a second browser login from disconnecting the feed used by the first device.

If tokens materially change or are revoked, cache/feed invalidation remains required.

## Explicit Logout

Logout removes the current active-session row, clears the Flask session, and follows the route's broker-token policy. The route is explicitly CSRF-exempt in app registration because both supported logout forms must work; other session-changing routes retain CSRF protection.

## Account Security Events

- Password change clears all active-session rows, emits `force_logout`, and clears the current cookie.
- Password reset and setup-sensitive flows clear or revoke session state according to their route logic.
- Active-session APIs under auth and security are read-only; there is no documented endpoint for remotely revoking one selected device.

## Ending Every Session (SUDARSHAN, review HIGH-2)

The Flask session is a signed cookie the server cannot reach, so removing rows ends nothing by itself. Logout, password change and password reset call `utils.session.end_every_session()`, which records the moment in `session_cutoffs` (one row per user) and then clears the rows. `is_session_valid()` refuses a cookie whose `login_time` is earlier, so the daily-expiry request guard, every `check_session_validity` route and the Socket.IO connect check refuse it on its next request, and `/auth/session-status` clears it for every caller. `revoke_user_tokens()` only signs such a cookie out, with no broker teardown; a cookie also past the daily expiry takes the expiry path. A later login is never affected, so a second device cannot sign the first out. The cutoff is read through a `LockedTTLCache` invalidated after each write. Each login and the Security screen's list also remove rows of sessions the daily expiry has ended.

A failed write of the cutoff is tried once more for the same moment; when that fails too, the rows are still cleared and the answer of the logout, password change or reset says (status `error`) that the other devices could not be signed out and that one more logout does it (release review v0.8.0, L6). A password change or reset keeps the rows with no address, which only auto-login records: it makes no cookie, so its row keeps no device signed in, and the daily expiry of a stale cookie reads it as the login after the rollover that keeps the Upstox login the change kept (`auth_db.clear_browser_sessions`, L7). A logout still clears every row.

## Frontend Behavior

`AuthSync` restores user, broker, API key, app mode, capabilities, and active-session count. A normal unauthenticated response clears the Zustand stores. Network errors preserve existing state for the current render rather than forcing a false logout.

## Key Files

| File | Purpose |
|---|---|
| `blueprints/auth.py` | Session status, heartbeat, login, logout, password change |
| `database/auth_db.py` | ActiveSession model, cap, last-seen, token upsert/revocation |
| `app.py` | Daily expiry request guard and CSRF exemptions |
| `utils/session.py` | Protected blueprint checks |
| `frontend/src/components/auth/AuthSync.tsx` | SPA session restoration |
| `frontend/src/stores/sessionStore.ts` | Active-session count |
