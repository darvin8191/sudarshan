# sudarshan-family (P6, M2.6, M2.7): the family view's service

Family members see the account owner's books, view only. They never hold an order-capable
credential, and nothing in this service can place, change or cancel an order. It is the one ring
service that takes input from people other than the owner (sign-ins, phones, Telegram chats), so it
runs as its own account and can read none of the owner's secrets.

Status: the contract (F0) is in; the store, the accounts, the service, the viewer API and the owner
notices follow on this branch (`feat/family-a`); the data plane, the WebSocket dialect, the bot and
Web Push are other lanes. `sudarshan/STATUS.md` has one row per feature. The service ships switched
off (`SUDARSHAN_FAMILY=off`).

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
