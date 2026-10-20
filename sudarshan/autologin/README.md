# sudarshan-autologin (M2.20)

The daily Upstox login, done for the owner. SEBI requires a fresh 2FA broker login every trading
day; until now that meant OpenAlgo login, Connect Account, then the Upstox page with OTP and PIN,
every morning before 09:15. This logs in with Upstox's official endpoint for individual API apps
(`POST /v2/totp-login/authorization/token`, header `x-api-key`, body TOTP + PIN), using a TOTP key
and PIN the owner saves once from his phone.

```
sudarshan-autologin.timer   08:35, 08:45, 08:55 IST, Mon-Fri
  -> sudarshan-autologin.service (oneshot, user sudarshan)   python -m sudarshan.autologin
       1. trading day? inside 08:30-15:30? switched on?          (otherwise: nothing)
       2. ask OpenAlgo (sealed, 127.0.0.1): owner, broker, already logged in today?
       3. TOTP code (RFC 6238) from the vault key -> Upstox token endpoint
       4. hand the new token to OpenAlgo (sealed) -> OpenAlgo stores it like a manual login
       5. record the result in the vault; OpenAlgo sends one Telegram message

"Test now" button -> OpenAlgo runs the same program once as a child process and shows the answer
```

## On the phone: turning it on

1. **Turn on TOTP in Upstox** (once). In the Upstox app open Profile (Account settings) >
   Security (2FA) > TOTP and follow the steps. Keep the screen that shows the QR code: the text
   key under it is what SUDARSHAN needs. If TOTP is already on with an authenticator app, the key
   may have to be re-generated there; add the new key to the authenticator app as well.
2. Open SUDARSHAN, profile menu > **Auto login**.
3. Paste the **TOTP key** (spaces and lower case are fine), type the 6-digit **PIN**, press **Save**.
   Both are stored encrypted on the server and are never shown again; they can only be replaced
   or removed.
4. Press **Test now**. "Logged in" means everything works, including the Telegram message.
   (Not available 09:15-15:30 while Upstox is already connected for that day: a new login would
   cut off the live session.)
5. Switch **Log in automatically on trading days** on.
6. Make sure the Telegram bot is running and linked to the owner (OpenAlgo > Telegram Bot).
   Without it everything still works, but no message arrives.

Each trading morning after that: a Telegram message "Upstox auto-login OK (08:35 IST)". Opening
SUDARSHAN later asks only for the OpenAlgo password; the Upstox session is picked up as it is.

## Turning it off

- Profile menu > Auto login > switch **off**. Nothing runs; nothing else changes. Logging in by
  hand works exactly as before (it always does, on or off).
- To also forget the secrets: **Remove** next to the TOTP key and the PIN.
- On the server, to stop the schedule entirely: `sudo systemctl disable --now sudarshan-autologin.timer`.
  (The deploy tool enables it again with the next release; the switch on the page is the lasting way.)

## What the owner sees when it fails

Every failure is recorded on the Auto login page (last run, result, plain reason, recent attempts)
and sent once to Telegram, ending with what to do: "Will try again at 08:45. If that fails too,
please log in by hand before 09:15." or "Please log in by hand before 09:15."

| cause | message (shortened) | what happens next |
|---|---|---|
| wrong TOTP key or PIN (Upstox UDAPI100097) | Upstox did not accept the saved TOTP key or PIN | **paused**: no more scheduled tries until the key/PIN is saved again (or the switch is turned off and on). Repeated wrong PINs could lock the Upstox account. |
| TOTP not on for the account (UDAPI100098) | TOTP is not turned on for this Upstox account | paused, as above |
| the API key is not an individual app (UDAPI100101) | Upstox allows this login only for an individual (personal) API app | paused, as above |
| too many logins (UDAPI100099 / HTTP 429) | Upstox is refusing logins for a few minutes | 5 minute pause, next slot tries again |
| no internet / Upstox down / timeout | Could not reach Upstox / login service not working | next slot tries again |
| key or PIN not saved | The TOTP key or the PIN is not saved yet | told once per day |
| pepper rotated (`upgrade/rotate_pepper.py`) | The saved TOTP key and PIN can no longer be read ... Save them again | told once per day |
| OpenAlgo broker is not Upstox / no user / no API key | plain sentence for each | told once per day |
| OpenAlgo not running | (no Telegram: messages go through OpenAlgo) | Upstox is **not** asked, so no working token is revoked; next slot tries again. Shown on the page and in the journal. |
| Upstox gave a token but OpenAlgo did not answer | ... it was not saved | 3 tries over 7 s, then recorded as failed; next slot tries again |
| OpenAlgo answered but could not store it | ... OpenAlgo could not save it | no login is left on record, so the next slot tries again |

Already logged in by hand before 08:35? Nothing happens that day (no new login, no message).

## Rules it keeps

- **Trading days only**: weekends and the dates in `/etc/sudarshan/nse_holidays.txt` (one
  `YYYY-MM-DD` per line, `#` comments) are skipped. A missing file means only weekends are skipped.
- **One success ends the day**: each wake-up first asks OpenAlgo whether a login from this trading
  session (after 03:00 IST) exists, the same test OpenAlgo's own restart scan uses.
- **Its own pace**: at most one Upstox call per 60 s and 10 per 30 min (Upstox allows 1/s, 10/min,
  60/30 min). Every successful call revokes the previous token, so it never logs in "just in case".
- **Window**: a wake-up outside 08:30-15:30 IST does nothing. The deploy tool restarts ring units
  after every deploy, and `Persistent=true` replays a missed wake-up after a boot; both are safe.
- **One attempt at a time**: a lease row in the vault; a second start exits with status 75.

## How the token reaches OpenAlgo (and why not straight into openalgo.db)

The running gunicorn worker keeps the broker token in memory: `auth_cache` serves it per API key
until the next session expiry (and also keeps a "no session" answer), the symbol cache is loaded
into memory after the master contract download, and the order-update adapter runs there.
`database.auth_db.upsert_auth` clears those caches only in the process that calls it, and the
ZeroMQ invalidation reaches only the WebSocket proxy. A worker writing `openalgo.db` itself would
leave gunicorn serving yesterday's dead token (`test_autologin_cache.py` reproduces it).

So the worker hands the token over and OpenAlgo stores it in its own process, with the same calls
in the same order as a manual Upstox login (`utils.auth_utils.handle_auth_success`):
`register_session`, the `active_sessions_update` event, `log_login_attempt`,
`upsert_auth(user, token, "upstox", feed_token=None, user_id=None)` (Upstox's OAuth path stores no
feed token and no user id either), then the master contract download or cached load. Only the
browser cookie is left out. `test_autologin_parity.py` runs both against the same recorders and
pins the list of calls in `handle_auth_success`, so an upstream change there fails a test.

The active-session row ("SUDARSHAN auto-login (Upstox TOTP)" in Security > Active sessions) is
what keeps a phone's stale cookie from revoking the new token at its first request of the day
(`utils.session.revoke_user_tokens` spares the token when a login after 03:00 is on record).
Login activity shows these logins with type `auto_totp`.

The two loopback calls (`/sudarshan/autologin/internal/preflight` and `/internal/report`) carry a
Fernet token sealed with a key both sides derive from OpenAlgo's `API_KEY_PEPPER`: only a process
that can read OpenAlgo's `.env` can make one, it expires after 60 s, its nonce is used once, and
the access token never appears in clear on the wire or in a request log. They need no session and
are exempt from CSRF for that reason; everything else on `/sudarshan/autologin/` needs the
owner's session and the CSRF token.

## Why "Test now" is a child process

It runs the very program the timer runs, so a passing test means tomorrow's run works, and no
login code runs inside the eventlet worker. The request waits for the child: under eventlet
`subprocess` and `os.waitpid` are green, so the wait yields and the hub keeps serving, including
the child's own calls back into the same worker (`test_autologin_eventlet.py` measures hub
liveness; `test_autologin_gunicorn.py` runs the round trip under real gunicorn eventlet and
gthread workers). The child writes to `log/sudarshan_autologin.log` (mode 600, rotated at 1 MB),
is always reaped, and is stopped after 50 s.

## The vault (`sudarshan/vault/`)

`db/sudarshan_vault.db` (mode 600, created on first use): `secrets(name, ciphertext, key_id,
updated_at)`, `autologin_status` (one row: switch, pause, last run, lease) and
`autologin_attempts` (one row per Upstox call, 30 days). Encryption: Fernet with a key from
HKDF-SHA256 over `API_KEY_PEPPER` (fixed salt, info `sudarshan/vault/secrets/v1`); the hand-off
key uses info `sudarshan/autologin/handoff/v1`. Neither equals OpenAlgo's own broker-token key
(PBKDF2 over the same pepper). After a pepper rotation the stored secrets are reported as
unreadable (by `key_id`, without trying to decrypt) and must be saved again.

OpenAlgo and the worker must run as the same user (`sudarshan`), because the file is 600.

## Files and settings

| what | where |
|---|---|
| units | `sudarshan/ops/systemd/sudarshan-autologin.{service,timer}` (installed by the deploy tool) |
| scheduled runs log | `journalctl -u sudarshan-autologin` |
| "Test now" runs log | `log/sudarshan_autologin.log` |
| vault | `db/sudarshan_vault.db` |
| holidays | `/etc/sudarshan/nse_holidays.txt` |
| optional settings | `/etc/sudarshan/autologin.env` (root:sudarshan 640), table below |

| variable | default | meaning |
|---|---|---|
| `SUDARSHAN_OPENALGO_URL` | `http://127.0.0.1:5000` | OpenAlgo on this server (loopback only) |
| `SUDARSHAN_AUTOLOGIN_WINDOW` | `08:30-15:30` | IST minutes in which a scheduled wake-up may log in |
| `SUDARSHAN_NSE_HOLIDAYS_FILE` | `/etc/sudarshan/nse_holidays.txt` | trading holidays |
| `SUDARSHAN_AUTOLOGIN_LOG_LEVEL` | `INFO` | worker log level |

The Upstox API key and the pepper are read from OpenAlgo's own `.env` (`BROKER_API_KEY`,
`API_KEY_PEPPER`); nothing secret goes into the unit or its env file. Never logged or printed:
the TOTP key, the PIN, the TOTP code, the access token, the API key, or an Upstox error body
(it can echo the submitted values).

By hand on the server: `sudo -u sudarshan /opt/sudarshan/apps/openalgo/.venv/bin/python -m
sudarshan.autologin --test` (from `/opt/sudarshan/apps/openalgo`) runs one attempt like the
button.

## Not verified yet

- The Upstox TOTP token endpoint was implemented from the description in the task (method, path,
  `x-api-key`, `totp`/`pin`, error codes UDAPI100097/98/99/101); the public Get Token page does not
  document it. The code accepts `access_token` at the top level or under `data`, and `errorCode`
  or `error_code`. First live check: Test now outside market hours.
- `feed_token` and `user_id` are stored as None, exactly as OpenAlgo's Upstox OAuth callback does.

## Tests

`uv run pytest -q sudarshan` (no network: fake Upstox server, throwaway databases). The eventlet and
gunicorn cases run where the production stack is installed (`uv pip install "gunicorn>=25.0,<26"
"eventlet==0.41.2"`) and skip elsewhere.
