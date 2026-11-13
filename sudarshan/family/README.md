# sudarshan-family (P6, M2.6, M2.7): the family view's service

Family members see the account owner's books, view only. They never hold an order-capable
credential, and nothing in this service can place, change or cancel an order. It is the one ring
service that takes input from people other than the owner (sign-ins, phones, Telegram chats), so it
runs as its own account and can read none of the owner's secrets.

Status: on this branch (`feat/family-a`): the contract (F0), the store (F1), the accounts (F3), the
service and its unit (F2), the viewer API for sign-in and the member's own account (F4), and the
owner's notices, the store's nightly copy and the audit op (F11). The data plane, the WebSocket
dialect, the bot and Web Push are other lanes; their routes answer "not ready" until they land.
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
