# सुदर्शन / SUDARSHAN screener (M9)

The owner's screens, judged on the live feed's candles: a screen is a set of conditions over a
universe (F&O stocks, futures, a NIFTY list, option strikes or a watchlist), checked at every
candle close of its timeframe or once after the close, ranked, and fed into a watchlist of the
owner's choosing. Every run can raise one alert through sudarshan-alerts.

The engine is pure Python under `sudarshan/screener/` (`spec`, `engine`, `library`, `price`,
`patterns`, `options`, `bars`, `candles`, `membership`, `alerting`, `words`, `presets`), pinned
by golden vectors in `tests/vectors/*.json`. The service `sudarshan-screener` (this directory's
`service.py`, run as `python -m sudarshan.screener`) owns `screener.db` under
`/var/lib/sudarshan-screener`, listens on `127.0.0.1:5584` only and talks to the feed, OpenAlgo
and the alerts service over loopback. OpenAlgo's bridge is `blueprints/sudarshan_screener.py`.

## Switch and settings

Off by default. `/etc/sudarshan/screener.env` (root:sudarshan 640) is made by `sudarshan-deploy`
on the first install that ships the unit, from `sudarshan/ops/deploy/screener-env.conf`, with
`SUDARSHAN_SCREENER='off'`; the deploy never changes a value the owner wrote there. Turning the
screener on is the owner's own edit (`SUDARSHAN_SCREENER='on'`, then `systemctl restart
sudarshan-screener`) after the live session of the owner's check. The deploy installs the unit
and the ring's `sudarshan.slice`, logs `migrate --status` before each start, checks
`127.0.0.1:5584/health` after it (a WARNING in its log when it does not answer, never a rollback),
copies `screener.db` into every backup and puts it back on a rollback.
`sudarshan-deploy --remove-ring sudarshan-screener.service` takes the service out (the unit goes,
its database is copied to a backup folder, the state folder and this file stay).

| setting | default |
|---|---|
| `SUDARSHAN_SCREENER` | `off` |
| `SUDARSHAN_SCREENER_HTTP_PORT` | `5584` |
| `SUDARSHAN_SCREENER_FEED_URL`, `SUDARSHAN_SCREENER_FEED_ZMQ` | `http://127.0.0.1:5580`, `tcp://127.0.0.1:5581` |
| `SUDARSHAN_SCREENER_OPENALGO_URL` | `http://127.0.0.1:5000` |
| `SUDARSHAN_SCREENER_ALERTS_URL` | `http://127.0.0.1:5582` |
| `SUDARSHAN_SCREENER_MAX_KEYS` | `320` (at most 700) |
| `SUDARSHAN_SCREENER_MAX_ACTIVE` | `12` intraday screens |
| `SUDARSHAN_SCREENER_OPTION_STRIKES` | `10` each side of the money |
| `SUDARSHAN_SCREENER_STOCK_IV` | `on` |
| `SUDARSHAN_SCREENER_KEEP_DAYS` | `100` (never under 90) |
| `SUDARSHAN_SCREENER_LOG_LEVEL` | `INFO` |

`python -m sudarshan.screener.migrate --status` reports the database's version without touching
it. `curl -s 127.0.0.1:5584/health | python3 -m json.tool` shows the release, the state, the
store's counts and each job.

## API: the contract between the service, the bridge and the page

The page calls OpenAlgo at `/sudarshan/screener/api/...` with its session; the bridge checks the
session (401 in the envelope), that it is the owner's (403 for any other session, reads
included), CSRF on writes and a limiter per class (reads 120 a minute, writes 60, previews 6),
then forwards one sealed request to the service and passes its answer back. The table is
`sudarshan/screener/contract.py` (`OPS`); the page's copy is
`frontend/src/sudarshan/screener/types.ts`.

| route | method | op | args |
|---|---|---|---|
| `/state` | GET | `state` | - |
| `/screens` | POST | `create` | `{spec, status?: "active" or "paused", request_id?}` |
| `/screens/<id>` | PATCH | `update` | `{version, action: "edit" (+ `spec`), "pause", "resume", "alerts_on", "alerts_off"}` |
| `/screens/<id>` | DELETE | `delete` | `?remove_added=1` |
| `/preview` | POST | `preview` | `{spec}` |
| `/screens/<id>/results` | GET | `results` | - |
| `/screens/<id>/runs` | GET | `runs` | `?before=&limit=` |
| `/runs/since` | GET | `runs_since` | `?after=&limit=` |
| `/runs/<id>` | GET | `run` | - |
| `/membership` | GET | `membership` | `?list=&screen=` (one at least) |
| `/membership/<id>` | PATCH | `membership_update` | `{version, action: "pin", "unpin", "dismiss", "restore"}` |
| `/presets`, `/universes`, `/today` | GET | `presets`, `universes`, `today` | - |

Every answer is one envelope:

```json
{"status": "success", "enabled": true, "data": {...}}
{"status": "success", "enabled": false, "data": null, "message": "The screener is switched off on the server."}
{"status": "error", "error": {"code": "refused", "message": "Type a number, like 55 or 2500.50.", "field": "c1.right"}}
```

The page acts on `error.code`, never on the HTTP status: `not_ready` (501), `bad_request` (400),
`not_found` (404), `version_conflict` (409), `refused` (422, `field` names the line of the
screen), `unavailable` (503), `not_confirmed` (504: a write was sent and no answer came back),
`unauthorized` (401), `forbidden` (403), `rate_limited` (429). The sentences shared word for word
are `contract.WORDS`.

What the data carries:

- `state`: `screens` (each screen row with `spec`, `status`, `version`, `where` (the conditions
  in words), `alerts`, `alerts_paused`, `list_id`, `last_run`), `engine` (`state`, `text`: the
  line under the header, and its facts), `budget` (`active`, `max_active`, `max_keys`).
- `create` and `update`: the screen row as in `state`.
- `results`: `screen`, `run` (the latest done run), `marks` (words), `members` (the membership
  rows of the screen's list), `values` (key to clause values of the latest run), `changes`.
- `runs`: `runs` (newest first, each with `mark_words`) and `more`; `run`: `run`, `changes`,
  `values`, `values_note` ("Values are kept for 7 days; the list for 100 days." when gone),
  `mark_words`; `runs_since`: `runs` (oldest first) and `more`.
- `membership`: `rows`; `membership_update`: the row.
- `presets`: `presets` (the Library cards: `key`, `name`, `about`, `usable`, `where`, `spec`,
  `reason`); `universes`: `kinds` and `built`; `today`: `session`, `trading`, `boundaries` per
  active screen (`k`, `end`, `at`) and `next_at`.

Units everywhere (cross-design X3): prices in paise as integers, percentages as decimal strings,
instants in epoch milliseconds, a trading day as `YYYYMMDD`.

Live updates reach an open app over Socket.IO: the app emits `sudarshan_screener_join` (the owner
only) and then receives `sudarshan_screener_run` (`{screen_id, run_id, run_key, matched, entered,
exited, missing, not_checked}`), `sudarshan_screener_lists` (`{list_id}`: a target list changed)
and `sudarshan_screener_state`. The service's two internal routes
(`/sudarshan/screener/internal/watchlist?op=watchlist_read|watchlist_apply` and
`/sudarshan/screener/internal/push`) are sealed under their own purpose, refused with 404 when
nginx forwarded them, and never called by the page.

## Alerts

A run worth telling (alerting.py decides) is one digest, sealed under the alerts' screener-digest
purpose and posted to sudarshan-alerts at `POST 127.0.0.1:5582/v1/screener_digest` by the outbox
(retried with backoff for 5 minutes). The alerts service records it once under
`scr:<screen_id>:<run_key>` (a retried post answers "already told"), composes the Telegram line
itself ("Screener: 15-min EMA cross, 10:30 to 10:45 candle: 3 entered: RELIANCE ₹1,428.25
(+1.20%), ...; 7 match now"), sends it through the market lane (12 a minute, fills first, 5-minute
age limit) and pushes a toast with a View link to the open apps; its History tab shows the row
with its delivery chips. Pause and not-checked notes stay in the app, never on Telegram.

## The replay check (the owner's check 7.4, after the close)

On the server, as the `sudarshan` user or root, after a trading day the feed recorded:

```sh
cd /opt/sudarshan/apps/openalgo
STATE_DIRECTORY=/var/lib/sudarshan-screener .venv/bin/python -m sudarshan.screener.check \
    /var/lib/sudarshan-feed/ticks/$(date +%F).jsonl.gz --from-db --compare
```

It opens screener.db read-only (nothing is written, the service may keep running), replays the
day's ticks through the same bars, runs, membership and alert policy with each tick's own clock,
prints one line per run (`10:00  15-min EMA cross  3 matched  1 entered (SBIN)  0 left  digest:
Alert raised: 1 entered; 3 match now`) and compares each with the run the service recorded.

Passing looks like this, and the command exits 0:

```
N runs replayed from 2026-10-28.jsonl.gz
Replay check: N runs compared, all match
```

A difference on a run the service marked `late` (a restart) or `repaired` (a pause) is printed as
`explained: ...` and still passes. Any other line starting `DIFFERENT:` fails the check (exit 1):
read the journal (`journalctl -u sudarshan-screener --since today`) for that run's time. Exit 2
means the check could not run (no recording, no screener.db, no universe yet); the message says
which. Without a database, `--spec screen.json --keys NSE_EQ|...,NSE_EQ|...` replays one screen
over the keys given (what CI does with synthetic recordings).

## Storage

`screener.db` (schema 1, `migrations.py`): screens and their state, runs with their changes and
values (kept 100 and 7 days), membership per screen, list and instrument (`first_seen`,
`last_seen`, `exited_at`, `pinned`, never pruned), the list as last seen, the alert marks per
session, the outbox (list syncs, digests, pushes), universes, today's bars, history coverage, the
daily OI and IV stores, pauses. One writer thread, `BEGIN IMMEDIATE` on every write, versions on
every owner edit and run.
