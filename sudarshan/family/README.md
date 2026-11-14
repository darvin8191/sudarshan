# sudarshan-family (P6, M2.6, M2.7): the family view's service

Family members see the account owner's books, view only. They never hold an order-capable
credential, and nothing in this service can place, change or cancel an order. It is the one ring
service that takes input from people other than the owner (sign-ins, phones, Telegram chats), so it
runs as its own account and can read none of the owner's secrets.

Status: on this branch (`feat/family-a`): the contract (F0), the store (F1), the accounts (F3), the
service and its unit (F2), the viewer API for sign-in and the member's own account (F4), and the
owner's notices, the store's nightly copy and the audit op (F11). On `feat/family-t2`: the family
bot (F8), Web Push, quiet hours, the day's summary and the outbox (F10; "Channels" below). The
data plane and the WebSocket dialect are other lanes; their routes answer "not ready" until they land.
`sudarshan/STATUS.md` has one row per feature. The service ships switched off
(`SUDARSHAN_FAMILY=off`).

```
Family phone (Chrome, PWA)
  data   /family/api/*  -> nginx -> sudarshan-family 127.0.0.1:5585   (session cookie, CSRF header)
  ticks  /family/ws     -> nginx -> sudarshan-family                  (cookie at the handshake)
OpenAlgo's owner bridge -> POST 127.0.0.1:5585/v1/admin/<op>          (sealed, principal owner)
sudarshan-alerts        -> POST 127.0.0.1:5585/v1/nudge               (sealed: "orders changed")
the deploy, the owner   -> GET  127.0.0.1:5585/health                 (counts and states only)
```

## The service

| | |
|---|---|
| unit | `sudarshan-family.service` (`ops/systemd/`), started only once `/etc/sudarshan-family/provisioned` exists |
| account | `sudarshan-family`: no login shell, no home, not in the group `sudarshan` |
| port | 5585 on 127.0.0.1, one listener for everything above (no second port, never another port) |
| switch | `SUDARSHAN_FAMILY` in `/etc/sudarshan-family/family.env`, off by default |
| contract | `contract.py`; its twin on the phone `frontend/src/sudarshan/family/types.ts` (`python -m sudarshan.family.gentypes`) |
| privacy table | `privacy_fields.json`: what each of the four categories hides (one table for every reader) |

## Its files

| path | what | who writes it |
|---|---|---|
| `/etc/sudarshan-family/` | root:sudarshan-family 750, files 640: read only by this service | the deploy's provision step |
| `seal-admin.key`, `seal-nudge.key`, `seal-internal.key`, `seal-portfolio-family.key`, `seal-journal-family.key` | the derived seal keys (urlsafe base64, 32 bytes each): the service never derives a key itself | provision (from `python -m sudarshan.family.derive`, run as `sudarshan`) |
| `store.key` | 32 random bytes (standard base64): the key of the secrets at rest in the store | provision, once |
| `vapid_private.pem`, `vapid_public.txt` | the Web Push key pair | provision, once |
| `telegram_bot_token` | the family bot's token (one line, as BotFather shows it): read again when the file changes | the owner, once (Channels, below); the deploy never writes it |
| `feed-token` | this service's token for the price feed's write calls | provision |
| `nse_holidays.txt`, `nse_extra_trading_days.txt` | copies of the exchange's day files | provision, when they change |
| `family.env` | the settings, read as the unit's environment | the owner |
| `provisioned` | written only after `python -m sudarshan.family.check` passed as this account | provision |
| `/var/lib/sudarshan-family/family.db` | the store (folder 700, file 600) | this service only |
| `/var/lib/sudarshan-family-export/` | sudarshan-family:sudarshan 2750: what the ring may read | this service |
| `.../events.jsonl` | notices for the owner (0640), worded and sent by `sudarshan-alerts` | this service |
| `.../family-copy.db` | the store's copy for the nightly backup (0640), at 16:20 and 22:20 | this service |

The one-time set-up that makes the account, the folders and the firewall allow-list is
`ops/family/bootstrap.sh`; `sudarshan/README.md` says how the owner runs it.

## For the pages

- Read `GET /family/api/me` once when a page opens: it answers the member (or 401 in words) and sets
  the CSRF cookie `__Host-sudarshan_family_csrf` when the browser has none. Every request that is not
  a GET carries that cookie's value in `X-Family-CSRF` and a JSON body; sign-in and join included.
- A 401 is `signed_out` with the sentence to show (expired, signed out on this device, removed,
  paused); a wrong sign-in name, password or code is one answer, `signin_failed`; `locked` (429)
  names the time to try again.
- The WebSocket closes with 4401 and "Signed out on this device." when the session ends: go to the
  sign-in page, do not reconnect.

## Modules

| path | what | built in |
|---|---|---|
| `contract.py`, `gentypes.py` | the contract and its twin on the phone | F0 |
| `store.py`, `migrations.py`, `migrate.py`, `audit.py`, `errors.py`, `words.py` | family.db, its steps and `--status`, the audit, the refusals and their sentences | F1 |
| `accounts.py`, `sessions.py`, `invites.py`, `lockout.py`, `members.py` | passwords, codes, sign-ins, sessions, invites, lockout, the owner's members | F3 |
| `service.py`, `listener.py`, `ws.py`, `admin_api.py`, `health.py`, `config.py`, `__main__.py` | the process, its one listener and its doors | F2 |
| `viewer_api.py` | the viewer API: cookies, CSRF, sign-in, the member's own account | F4 |
| `notices.py`, `backup.py` | the owner's notices, the store's copy for the nightly backup | F11 |
| `derive.py`, `check.py` | the one-time set-up's pieces (the deploy runs them) | O2' |
| `bot.py`, `bot_words.py`, `telegram_client.py`, `quiet.py` (market hours) | the family bot: link codes, the read-only command table, two languages, its own pacer per chat | F8 |
| `push.py`, `quiet.py`, `digest.py`, `outbox.py`, `timers.py` | Web Push, quiet hours and Settings, the day's summary, the outbox, and the `channels` job that runs them with the bot | F10 |

## Channels

### The family bot (F8, M16.4)

A second Telegram bot, separate from the owner's: only this service holds its token, and it can
show what each member may see and nothing else. There is no command that places, changes or
cancels an order, and its HTTP client can reach only Telegram's API and two loopback services
(the portfolio on 5583, the price feed on 5580); a request anywhere else is refused before it
leaves.

| command | answers |
|---|---|
| `/status` | the market (open or closed, "Real-time" only while ticks arrive) and the indices |
| `/positions`, `/holdings` | the account owner's positions or holdings, under the member's privacy, with the time of the figures |
| `/orders` | today's orders, when orders are shared with the member |
| `/portfolio` | the portfolios shared with the member |
| `/quiet on`, `/quiet off` | quiet hours for the daily summary (sign-in notices are never quiet) |
| `/language en`, `/language gu` | English or Gujarati for the bot and the notifications |
| `/help`, `/stop` | the list; unlink this chat |

Anything else is answered "This bot only shows information. It cannot place, change or cancel an
order." A member links their chat from Settings, Link Telegram: the link works once, for 10
minutes. A chat that is not linked is only told how to link. Groups are never answered.

**The owner's steps, once.** The deploy does not make the token file; the owner puts it there.

1. In Telegram, open @BotFather and send `/newbot`. For the name, type
   `सुदर्शन / SUDARSHAN · Family`; for the user name, any free name ending in `bot`. BotFather
   answers with the token: a number, a colon and about 35 letters and digits. Copy it into your
   password manager; never paste it into a chat.
2. The family set-up (`sudarshan/ops/family/bootstrap.sh`) must have run, so that
   `/etc/sudarshan-family` exists on the server.
3. On the laptop, save the lines below as `sudarshan-family-bot.bat` (in Notepad: Save as, then
   Save as type "All files"), change `USER@SERVER` in its third line to the login you already use
   for the server, and double-click it:

   ```bat
   @echo off
   setlocal
   set "SERVER=USER@SERVER"
   ssh -t %SERVER% "sudo test -d /etc/sudarshan-family || { echo 'The family set-up has not been run on this server yet. Nothing was changed.'; exit 1; }; read -rs -p 'Family bot token (nothing shows as you paste it; press Enter after it): ' t; echo; [[ $t =~ ^[0-9]{5,16}:[A-Za-z0-9_-]{30,64}$ ]] || { echo 'Not saved: that is not a bot token as BotFather shows it. Nothing was changed.'; exit 1; }; printf '%%s\n' $t | sudo install -o root -g sudarshan-family -m 640 /dev/stdin /etc/sudarshan-family/telegram_bot_token && echo 'Saved. The family bot starts within a minute.'"
   if errorlevel 1 (
     echo The token was not saved: read the lines above, fix what they name and run this file again.
     pause
     exit /b 1
   )
   pause
   exit /b 0
   ```

   It asks for your password on the server once (`[sudo] password for ...:`), then for the token
   in a hidden prompt: paste it (a right-click pastes) and press Enter. Nothing shows as you
   paste, and the token is never on a screen, in a file on the laptop or in the shell history.
   The file it writes is `/etc/sudarshan-family/telegram_bot_token`, root:sudarshan-family 640.
4. Open Family on your phone: the Family bot card reads "Polling" within a minute. No restart is
   needed; the service reads the file again whenever it changes.

**When the card says otherwise.** "Token missing": the file is not there, or does not hold a token
as BotFather shows it (run step 3 again). "Telegram refused the token": the token was revoked or
mistyped; in @BotFather send `/token` (or `/revoke`, if it may have leaked), then run step 3 with
the new one. You also get one Telegram notice from the owner's bot when Telegram refuses a token.
If the service's journal says another program is using the token, a second copy of the bot is
running somewhere: stop it, or revoke the token and save a new one.

### Web Push, quiet hours and the day's summary (F10, M10.6, M10.9, M20.4)

A member turns notifications on per phone in Settings; the phone subscribes with this service's
own key (`vapid_public.txt`; the pair is made once by the provision step and held only here). Each
subscription is checked before it is kept: a known push service (Google, Mozilla, Apple, Microsoft),
https, no numeric address. A phone that signs out gets nothing more; three failed sends in a row
mark the phone ("Notifications may not arrive on this phone") and tell the owner once.

| message | when | push | Telegram | quiet hours |
|---|---|---|---|---|
| sign-in notice (a new phone, a locked sign-in) | at once | urgency high, topic `security-<kind>` | yes, when linked | never quiet |
| the day's summary | the last close + 25 min on trading days (16:05; 16:10 from 7 Dec 2026) | urgency low, TTL 6 h, topic `digest-<day>` | when chosen | held to the window's end inside its 6 h, else dropped with an audit line |
| a test (Settings, Send a test) | at once, this phone and the chat | urgency normal, TTL 60 s | when linked | never quiet |

Quiet hours are 21:00 to 08:00 unless the member changes them (Settings, or `/quiet on|off`). A
child is quiet outside the market session as well, so a child's 16:05 summary is dropped (with an
audit line) as the rules stand. The summary is six lines at most, through the member's privacy,
in their language: the day, the indices, positions (P&L when shown), the holdings' move today (when
P&L is shown), the journal's trades count (Y16: when orders are shown; won and lost with P&L too)
and the next trading day. Its text is made when it is sent, never stored; a summary the service
missed runs at its next start until 21:00, then the day is recorded as skipped.

`SUDARSHAN_FAMILY_JOURNAL_URL` (default `http://127.0.0.1:5586`) is where the summary asks the
journal for the day's counts; without the journal's family key (`seal-journal-family.key`) or an
answer, the summary goes without that line.
