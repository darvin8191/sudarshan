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

Off by default, and v0.6.0 ships it off. `/etc/sudarshan/screener.env` (root:sudarshan 640) is
made by `sudarshan-deploy` on the first install that ships the unit, from
`sudarshan/ops/deploy/screener-env.conf`, with `SUDARSHAN_SCREENER='off'`; the deploy never
changes a value the owner wrote there and never writes `on`. Switching it on is the owner's own
edit (`SUDARSHAN_SCREENER='on'`, then `systemctl restart sudarshan-screener`), and only after his
two checks: the replay check on a recorded day (below) and one live session watched on the phone
(the checks are listed in `sudarshan/CHANGELOG.md`, v0.6.0). Switched off, the service drops the
instruments an earlier run claimed at the feed.

The deploy installs the unit and the ring's `sudarshan.slice` (CPU weight 100, the ring's share
before the slice named one: at most half the CPU when the box is busy), logs `migrate --status` before each start, checks
`127.0.0.1:5584/health` after it (a WARNING in its log when it does not answer, never a rollback),
copies `screener.db` into every backup and puts it back on a rollback.
`sudarshan-deploy --remove-ring sudarshan-screener.service` takes the service out (the unit goes,
its claims at the feed are dropped, its database is copied to a backup folder, the state folder
and this file stay). A plain `systemctl stop` keeps the claims for the restart, as market alerts
do: to stop the screener for good, switch it off or remove it.

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

What the data carries (the shapes of `types.ts`):

- `state`: `enabled`, `engine` (`state`, `line`: the words under the header, `real_time`,
  `next_at`), `screens` (each a `ScreenRow`: `id`, `version`, `name`, `status`, `status_reason`,
  `where` (the conditions in words), `spec`, `matches`, `entered_today`, `added_count`,
  `target_name`, `list_id`, `next_check`, `last_run_id`, `alerts`, `alerts_paused`,
  `updated_at`), `limits` (`active`, `active_used`, `max_keys`), `last_run_id` (the newest run
  the service holds, for the catch-up), `server_time`.
- `create` and `update`: one `ScreenRow`; `delete`: `deleted` (the id) and `removed_from_list`
  (with `remove_added`: the screener's items in the list that no other screen wants, which the
  queued list sync then takes out; the screen's membership rows stay for the history).
- `results`: `screen_id`, `run` (a `RunRow`: `id`, `screen_id`, `at`, `candle` ("10:00 to
  10:15"), `matches`, `entered`, `exited`, `missing`, `not_checked`, `status`, `marks` (words),
  `alerts`), `rows` (each a `ResultRow`: `member_id`, `key`, `symbol`, `exchange`, `rank`,
  `values` ("Close 120.00 · RSI 61.2"), `forming`, `chip` (icon, text, tone, name, note),
  `ltp`, `change_pct`), `not_checked` (`count`, `reasons`). Rows are the run's matches best
  ranked first as the screen ranks them; matches the list could not take are counted in the
  run's marks ("2 more matched, waiting for the next check (at most 1 added a check)").
- `runs`: `runs` (`RunRow`s, newest first) and `more`; `run`: `screen_id`, `run`, `rows` (that
  run's whole match set: from its values while they are kept, else replayed from the day's first
  run and its changes), `not_checked`, `values_note` ("Values are kept for 7 days; the list for
  100 days." once the values are gone, else null); `runs_since`: `runs` (oldest first) and `more`.
- `preview`: `label` ("Preview on the 10:15 to 10:30 candle, not saved"), `count`, `first` (at
  most 25 `ResultRow`s), `not_checked`, `needs`.
- `membership`: `rows` (each a `MemberRow`: the membership fields with `chip`);
  `membership_update`: the row.
- `presets`: `presets` (the Library cards: `key`, `name`, `about`, `usable`, `where`, `spec`,
  `reason`; two are usable in this release, ORB breakout and VWAP reclaim; six say what they
  wait for, history before today or a daily store, and Delivery spike that it needs the daily
  delivery file); `universes`: `universes` (each `kind`, `label`, `note`, plus `id`,
  `underlying` or `list_id`; the owner's own watchlists are not listed, the page has them);
  `today`: `screens` (`id`, `name`, `entered`, `exited`, `matches`),
  `recent` (`symbol`, `exchange`, `kind`, `at`, `screen`: the day's real entries and exits, from
  the membership), `unseen_alerts`, `session`.

Units everywhere (cross-design X3): prices in paise as integers, percentages as decimal strings,
instants in epoch milliseconds, a trading day as `YYYYMMDD`.

Live updates reach an open app over Socket.IO: the app emits `sudarshan_screener_join` (the owner
only) and then receives `sudarshan_screener_run` (`{screen_id, run_id, run_key, at, candle, counts: {matches,
entered, exited, missing, not_checked}, entered, exited, more_entered, more_exited, marks}`: at most 40 names a
side, the counts carry the rest), `sudarshan_screener_lists` (`{list_id}`: a target list changed) and
`sudarshan_screener_state` (`{engine, screens: [], removed: []}`, sent whenever the engine line's state or
words change, so an open page never keeps "Real-time" after the feed stops). The service's two internal routes
(`/sudarshan/screener/internal/watchlist?op=watchlist_read|watchlist_apply` and
`/sudarshan/screener/internal/push`) are sealed under their own purpose, refused with 404 when
nginx forwarded them, and never called by the page.

**The dashboard (lane B, M9.16).** The Dashboard view's layout lives in `screener.db` (step 4: `dashboard`
and `dashboard_widget`), behind one version that every write names; the first change (version 0) turns
the suggestion (a table for each Active screen, newest first) into the stored layout and then applies
itself. A widget is addressed by its screen and its kind, never by an id: `GET /dashboard`
(`dashboard`: `version`, `suggested`, `widgets`, `limits`, `last_run_id`), `GET /dashboard/widget?screen_id=&kind=`
(`widget`: one card), `POST /dashboard/widgets` (`widget_add`: `{version, screen_id | preset, kind?, rows?,
sort_by?, value_clauses?, start?, when?}`; a preset makes its screen and the widget in one transaction, a
screen that would pass the Active limit is added Paused and the answer's `notice` says so),
`PATCH /dashboard/widget` (`widget_update`: `{version, screen_id, kind}` and any of `to_kind`, `rows`,
`sort_by`, `value_clauses`), `DELETE /dashboard/widget?screen_id=&kind=&version=` (`widget_remove`) and
`POST /dashboard/order` (`widget_order`: `{version, order: [[screen_id, kind], ...]}`, the whole layout).
A card (`dashboard.py`, pure) carries the screen's name and state, how it is checked, the latest run's
rows in the widget's order (ties by symbol), and a `line` that says why it is or is not live in the engine
line's own words; the `dashboard` read works cards out in position order against a 1.5 s budget and the
cards past it answer `rows_pending` with no rows, which the page reads with `widget`. Every write queues a
`sudarshan_screener_dashboard` push (`{version}`) in its own transaction, so another screen reads the
layout again; the bridge forwards every event in `contract.PUSH_EVENTS`, so a new one needs no bridge edit.
A run's push patches only a card's counts and run line: rows move once, from the card's own read.

## Alerts

A run worth telling (alerting.py decides) is one digest, sealed under the alerts' screener-digest
purpose and posted to sudarshan-alerts at `POST 127.0.0.1:5582/v1/screener_digest` by the outbox
(retried with backoff for 5 minutes). The alerts service records it once under
`scr:<screen_id>:<run_key>` (a retried post answers "already told"), composes the Telegram line
itself ("Screener: 15-min EMA cross, 10:30 to 10:45 candle: 3 entered: RELIANCE ₹1,428.25
(+1.20%), ...; 7 match now"), sends it through the market lane (12 a minute, fills first, 5-minute
age limit) and pushes a toast whose View opens the screen; its History tab shows the row with its
delivery chips. Pause and not-checked notes stay in the app, never on Telegram. A digest market
alerts refuse for good (switched off on the server, their shipped default: 409) is not tried
again, and a dropped digest turns the run's "Alert raised" into the reason ("No alert sent:
market alerts are switched off on the server").

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
which. Each screen starts the day with the members it carried from earlier days (one a check or
the owner touched that day is taken as matching at the start, or as dismissed if the owner
dismissed it), and only that day's runs are compared.

Before the screener was ever switched on there is no screener.db (switched off it never makes
one), so the first replay check runs one preset over the stocks the feed recorded that day, with
`--spec` (one screen as JSON) and `--keys` (what CI runs on synthetic recordings):

```sh
cd /opt/sudarshan/apps/openalgo
DAY=/var/lib/sudarshan-feed/ticks/$(date +%F).jsonl.gz
.venv/bin/python -c "import json; from sudarshan.screener.presets import preset; print(json.dumps(preset('vwap_reclaim').spec))" > /tmp/screen.json
KEYS=$(.venv/bin/python -c "import sys; from pathlib import Path; from sudarshan.feed.replay import read_ticks; print(','.join(sorted({k for k, _, _ in read_ticks(Path(sys.argv[1])) if k.startswith('NSE_EQ|')})))" "$DAY")
.venv/bin/python -m sudarshan.screener.check "$DAY" --spec /tmp/screen.json --keys "$KEYS"
```

It passes when it prints a line for each 5-minute close of the session and ends with "N runs
replayed from ...", exit 0. Nothing is compared yet, since the service has made no run, and each
stock is named by the end of its key (its ISIN): the comparison with TradingView is the live
session's, on the phone.

## Storage

`screener.db` (schema 3, `migrations.py`; step 2 adds each bar's running volume `cum` and last
trade time `last_trade`, step 3 a pause's `key`): screens and their state, runs with their changes and values (kept 100
and 7 days), membership per screen, list and instrument (`first_seen`, `last_seen`, `exited_at`,
`pinned`, never pruned, kept when a screen is deleted), the list as last seen (each add recorded
before it is sent), the alert marks per session and the pauses (kept 7 days), the outbox (list
syncs, digests, pushes), universes, today's bars, history coverage, the daily OI and IV stores, and
the seen mark (every 10 s). One writer thread, `BEGIN IMMEDIATE` on every write, versions on every
owner edit and run.

`bar_today` holds one trading day: a start or midnight clears every earlier day and restores only
today's bars, each with its running volume, so a restart's first bar carries its own volume. A
restart opens a pause from the last seen mark: a candle that holds minutes the screener did not see
is "Not checked", never judged whole. An instrument no screen followed for a while (a screen paused
and resumed, a watchlist that could not be read, the key limit) counts its volume again from when
it comes back, and the stretch is kept as that instrument's own pause (with its key, so a restart
keeps it): its candles that hold the stretch are "Not checked" too. A start that cannot follow
anything at once (OpenAlgo's master contract downloading) counts the wait the same way for every
instrument it had bars of. A stretch before the open, or of 10 s or less, changes nothing.

## What the screener follows, and when

- Universes and the claim at the feed are built when the day starts, again after 09:00 and
  whenever OpenAlgo finishes a master download (asked once a minute), and at the feed job's next
  tick (about a second) after a screen is made, edited, resumed, paused or deleted. Never while
  OpenAlgo says the download is running or its symbol table is empty: that is tried again every
  5 minutes. A screen whose universe is empty or not built yet is "Not checked: the instruments
  of this screen are not loaded yet": no member counts as a miss, nothing is taken out of a list.
- A watchlist universe is the owner's list, read through the bridge; an option window waits for
  its index's first price, follows that index, and is built at the next tick after it.
- Keys past `SUDARSHAN_SCREENER_MAX_KEYS` are not claimed and read "Not checked: past the 320
  instruments the screener can follow tick by tick".

## Not built yet

- History before today and the daily stores (open interest, implied volatility, India VIX): they
  wait for the feed's REST governor (SV3, SV4). Until then a plan that needs more candles than one
  session holds reads "Not checked: needs 151 candles of 15 minutes; a day gives 25, ...", the
  six presets that need them are not usable, and "Warming up · history loading" never shows.
- Re-anchoring an option window at 09:16 and near its edge; reading a watchlist universe again at
  each run; refusing a screen at save when it would pass the key limit.
- Compacting the not-checked rows: every not-checked instrument is still written to `run_change`
  and `run_value` at every run.
