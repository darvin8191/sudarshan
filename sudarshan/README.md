# sudarshan/ — the ring around OpenAlgo

Everything सुदर्शन / SUDARSHAN adds lives here as **separate processes** (systemd units named
`sudarshan-<svc>.service`, all in `sudarshan.slice`), never inside the OpenAlgo app (eventlet,
single worker, no in-app schedulers). They talk to OpenAlgo over `http://127.0.0.1:5000/api/v1`
(the `openalgo` Python SDK) and `ws://127.0.0.1:8765`, and to Upstox directly only through the
read-only Analytics Token (market data, portfolio reads) — never with an order-capable token.

| dir | service | what it is |
|---|---|---|
| `feed/` | `sudarshan-feed` | The **single** Upstox Feed V3 WebSocket for all of SUDARSHAN (the socket budget is below). Subscribes once, keeps a tick snapshot, republishes over local ZMQ, records ticks (gzip JSON lines) for replay, exposes `/snapshot` over HTTP, and (with M18.1's claims) takes each ring service's subscriptions. The REST governor for Upstox market-data reads is built here (below) and off by default. |
| `alerts/` | `sudarshan-alerts` | **Today (M6.8): order alerts.** Fills, part fills, rejections (with the broker's reason), cancellations and stop-order triggers from OpenAlgo's order stream (`ws://127.0.0.1:8765`, `subscribe_orders`) to the owner's Telegram through OpenAlgo `/api/v1/telegram/notify`; once only (claims in `/var/lib/sudarshan-alerts/alerts.db`), the order book read again after any pause, bursts merged, trading days 08:45-16:00 IST (16:05 from 7 Dec 2026); `SUDARSHAN_ORDER_ALERTS=off` turns it off (the service exits only when market alerts are off too); `127.0.0.1:5582/health` names the installed release and what the service is doing. **Also (M10, off until `SUDARSHAN_MARKET_ALERTS=on`):** the market alert engine, which judges price, % move, day high or low, VWAP, bid/ask and candle-close alerts on the feed's ticks, not in a browser tab, with the Alerts page, history and delivery on Telegram and in the app; it stays off unless switched on, and `alerts/README.md` says how. **Planned:** volume, OI change/build-up, IV, indicator cross, Greeks, time, P&L, screener hit, feed health and rollover alerts; the family bot separately, Web Push later. |
| `screener/` | `sudarshan-screener` | Scheduled scans (1/5/15 min, EOD) over universes (NIFTY 50/100/200/500, F&O list, custom watchlists, option chains) with a rule builder (price action, indicators via TA-Lib, OI build-up classes, IV rank/percentile, PCR, basis, bhavcopy fields). Results feed **membership-table watchlists** (first_seen / last_seen / hits / exited / pinned / score) in OpenAlgo's watchlist DB and raise digest alerts; every run is stored for rewind and forward-return backtests. |
| `portfolio/` | `sudarshan-portfolio` | Upstox tab (holdings, positions, MTF, funds tree, trade history, trade-wise P&L + charges, GTT, MF, portfolio stream) on the Analytics Token + daily snapshots, dividends from corporate actions, sector tags; and the **custom manual portfolio engine**: activity model (BUY/SELL/DIVIDEND/FEE/INTEREST/TRANSFER/SPLIT/BONUS), any asset, tags, imports (CSV/tradebook), FIFO lots, STCG/LTCG (ITA 2025 sections, "verify with CA"), XIRR/TWR, allocation, goals, custom tab layouts. |
| `journal/` | `sudarshan-journal` | **The trade journal and reports (M14, M15).** Every fill Upstox reports, paired into trades (long, short, flips split, carried across days), with the journal's figures (win rate, profit factor, expectancy, R, drawdown, calendar, heat map, expiry split, how far each trade went for and against) on the Journal page; and the reports (day report, morning brief, week in review, monthly statement with its CSV, tax pack) built on the journal's own schedule, kept in its archive on the Reports page and delivered on Telegram by `sudarshan-alerts`' report desk. It reads the account only through `sudarshan-portfolio`, prices from `sudarshan-feed` and OpenAlgo's own records read-only; it places no order and reaches nothing off this server. Off until `SUDARSHAN_JOURNAL` is on in OpenAlgo's `.env`, which the release sets (below); `127.0.0.1:5586/health` names the installed release and what it is doing. `journal/README.md` has the rest. |
| `family/` | `sudarshan-family` | View-only layer for family members: own users/sessions/TOTP, privacy mode, invitations, per-user watchlists/alerts, audit log, viewer UI variants, family Telegram bot. Backed by the Analytics-Token data plane only; every OpenAlgo write/admin/bot route must answer 401/403 to a viewer. |
| `autologin/` | `sudarshan-autologin` (oneshot + timer) | The daily Upstox login with TOTP + PIN (Upstox's TOTP token API) at 08:35/08:45/08:55 IST on trading days, and "Test now" from the Auto login page. Fetches the token, then hands it to OpenAlgo, which stores it exactly as a manual login does (its in-memory caches only refresh in its own process); never ends a login Upstox still accepts, and pauses after any refusal until the key or PIN is saved again. The one ring process that receives an order-capable token: it passes it to OpenAlgo and never uses it. Result on the page and in Telegram. |
| `vault/` | (library) | Owner vault `db/sudarshan-vault/sudarshan_vault.db` (directory 700, file 600; the auto-login unit may write only there): small secrets entered on the phone, Fernet under a key derived (HKDF) from OpenAlgo's pepper; write-only over HTTP. Also holds the auto-login status. |
| `ops/deploy/` | `sudarshan-deploy` (root, timer) | The server's deploy tool: installs the latest release outside market hours (backup, checksum, migrations, the `.env` settings below, health check, rollback), keeps the NSE day files in step with the release's calendar lines (below), records what it did for the owner's Telegram (below) and updates itself from each release. |
| `ops/` | `sudarshan-ops` (timers) | Daily-rollover guard (03:35 / 08:45 checks, TOTP token sidecar), backups to OCI Object Storage, health checks (healthchecks.io), EOD file jobs (bhavcopy, index constituents — once a day, outside the trading core), kill switch. The reports are not timers here: `sudarshan-journal` builds them on its own schedule and `sudarshan-alerts` delivers them. |

## The ring: sockets, ports, feed keys and REST (cross-design X2, X10, G1-G8)

**Upstox sockets.** The owner is on the Plus plan: 5 WebSocket connections per user, and a stale
socket counts until Upstox times it out.

| holder | sockets | token |
|---|---|---|
| TRINETRA (not part of SUDARSHAN; never touched from here) | 1 | Analytics Token |
| `sudarshan-feed`, for every ring service | 1 | Analytics Token |
| OpenAlgo's WebSocket proxy (the owner's screens, /trading, the tools; one pooled connection per user) | at most 2 | daily access token |
| kept free for reconnects | 1 | - |

**Ports.** Every one is bound to 127.0.0.1 and fixed: a taken port is an error in plain words,
never a move to another port.

| port | what |
|---|---|
| 5000 | OpenAlgo (REST `/api/v1`, the pages, the bridges to the ring) |
| 8765 | OpenAlgo's WebSocket proxy |
| 5555 | OpenAlgo's own ZMQ bus (its proxy's SUB binds; publishers connect) |
| 5580 | `sudarshan-feed` HTTP: `/health`, `/snapshot`, `/claims` (M18.1), and the REST governor under `/upstox/` (M18.9, off by default) |
| 5581 | `sudarshan-feed` ZMQ PUB: ticks and `__health__` |
| 5582 | `sudarshan-alerts`: the market alerts' sealed local API, and `GET /health` (the release installed and what the service is doing; the one listener, no other port) |
| 5583 | `sudarshan-portfolio`: its sealed local API, and `GET /health` (the release installed and what the service is doing; the one listener, no other port; a taken port is tried again every minute) |
| 5584 | `sudarshan-screener`: its sealed local API |
| 5585 | `sudarshan-family` (P6, not built yet): the family view's one listener: the viewer API and its WebSocket that nginx forwards (`/family/api/`, `/family/ws`), the sealed admin and nudge routes, and `GET /health`. It runs as its own account, `sudarshan-family` |
| 5586 | `sudarshan-journal`: its sealed local API (OpenAlgo's bridge for the Journal and Reports pages, the alerts' report desk and their nudges), and `GET /health` (the release installed and what the journal is doing, `journal_state`; the one listener, no other port; a taken port is tried again every minute while its jobs carry on) |
| 5587 | reserved for `sudarshan-protect` (later) |

**Sealed routes.** Each has its own key purpose in `sudarshan/vault/keys.py` (HKDF from OpenAlgo's
pepper; a seal of one purpose never opens another's), a 60 s seal, a nonce accepted once, and a
size limit of its own. A route's `NonceBook` (`sudarshan/autologin/handoff.py`) holds at least
2 x 60 s x its peak requests a second and answers busy (503) when full, rather than forgetting a
nonce that could still be replayed.

| route | purpose | limit |
|---|---|---|
| auto-login worker to OpenAlgo | `sudarshan/autologin/handoff/v1` | 16 KiB |
| OpenAlgo to the market alerts API | `sudarshan/alerts/api/v1` | 64 KiB |
| market alerts to OpenAlgo's owner room | `sudarshan/alerts/push/v1` | 128 KiB |
| screener digests to the market alerts | `sudarshan/alerts/screener-digest/v1` | 64 KiB |
| OpenAlgo to the portfolio API | `sudarshan/portfolio/api/v1` | 64 KiB (an import 2 MiB: a 1.2 MB file seals to 1.5 to 1.9 MiB, see `portfolio/core/kinds.py`) |
| OpenAlgo to the screener API | `sudarshan/screener/api/v1` | 64 KiB |
| the screener to OpenAlgo | `sudarshan/screener/internal/v1` | 16 KiB |
| OpenAlgo to the journal API | `sudarshan/journal/api/v1` | 64 KiB (a report's file comes back in the answer, at most 8 MiB) |
| the alerts' report desk to the journal (`report_get`, `report_file`, `delivery` only) | `sudarshan/journal/delivery/v1` | 16 KiB |
| the alerts' "orders changed" nudge to the journal | `sudarshan/journal/nudge/v1` | 4 KiB |
| the family service to the journal (P6, counts only) | `sudarshan/journal/family/v1` | 4 KiB |
| the journal to the portfolio API (account reads only) | `sudarshan/portfolio/api/journal/v1` | 64 KiB |

**Keys on the one feed socket (U9).** Upstox allows ltpc 5,000, full 2,000 and option_greeks
3,000 keys per connection; once more than one mode is in use the feed reads "combined" as 1,500
in all. Each ring service claims its keys from the feed (`PUT /claims/<client>`, M18.1) and keeps
under its own ceiling before it asks; the feed refuses a claim over the client's quota with 409,
naming the client. A key two clients hold counts once. Until the claims change is deployed, only
the base file subscribes.

| client | its own ceiling now | later | feed quota |
|---|---|---|---|
| base file (`feed/default_keys.txt`) | 12 | - | - |
| `sudarshan-alerts` | 200 | 300 | 300 |
| `sudarshan-portfolio` | 200 (ltpc) | - | 200 |
| `sudarshan-screener` | 320 (full, option_greeks) | 700 | 700 |
| `sudarshan-family` (P6) | - | 100 | 100 |
| `sudarshan-journal` | 50 (ltpc, its open trades) | - | 50 |
| `sudarshan-protect` (P7, later) | - | 50 | 50 |
| any other client (one slot) | - | - | 50 |
| the ring in all | 782 at worst | 1,412 | 1,450 |

U9 is the open Upstox report of drops near 600 s with 500 or more keys. It is judged on the
socket's total (`/health` `subscribed.total`), never on one client's count, and the first session
over 500 keys is its test for every client. The alerts' 300 and the screener's 700 wait for one
clean session at a total of at least 600 keys: no drop near 600 s in
`last_disconnect.socket_age_s` and `reconnects` 0. If drops come, the screener drops its live
claims that evening and the owner chooses whether cash stocks or stock futures are followed tick
by tick.

**REST to Upstox (the governor, M18.9).** Built and off by default (`SUDARSHAN_FEED_REST=off` answers every `/upstox/` request 503; the owner switches it on at G7). Upstox allows 50 requests a
second, 500 a minute and 2,000 per 30 minutes per API per user. The governor lives in
`sudarshan-feed` (`GET /upstox/<path>` on 5580), passes market-data reads only, and holds
SUDARSHAN to these shares; the other 60% stays for OpenAlgo's daily-token calls and TRINETRA.

| client | share | per second | per minute | per 30 minutes |
|---|---|---|---|---|
| SUDARSHAN in all | 40% | 20 | 200 | 800 |
| `sudarshan-screener` | 30% | 15 | 150 | 600 |
| `sudarshan-portfolio` | 5% | 2 | 25 | 100 |
| `sudarshan-alerts` | 5% | 2 | 25 | 100 |
| `sudarshan-journal` | 2.5% to 6.25% | 1 | 10 | 50 |

The clients' own ceilings may add up to more than SUDARSHAN's 40% (with the journal's they do,
at the minute and the 30-minute windows); the 40% then binds first come within a priority, and a
client's own ceiling still stops it taking the others' room (G3b, `feed/rest.py`).

Until it is switched on and `/health` shows it (`rest`), no ring service makes bulk or warm-start REST calls
to Upstox: prices come from the feed's snapshot or the last closing record, with their time.
Account reads (holdings, positions, funds) never pass through it; they stay in
`sudarshan-portfolio` at its own budget.

Conventions: one package per service (`sudarshan/<svc>/`), `pyproject`-managed with the repo's `uv`
environment, tests in `sudarshan/<svc>/tests/`, unit files in `sudarshan/ops/systemd/`, config in
`/etc/sudarshan/<svc>.env` (root:sudarshan 640; never in the repo). Feature ids from the
feature list (e.g. `M9.4`) go in PR titles and in `sudarshan/STATUS.md`.

## OpenAlgo settings that ship with a release (M18.2)

Nobody edits the server's OpenAlgo `.env` by hand. A release that needs a setting lists it in
`ops/deploy/openalgo-env.conf`, one line per key (`KEY  VALUE  OLD_VALUE...`; the file's header
gives the format). On every deploy, after the release is checked out and before OpenAlgo starts,
`sudarshan-deploy`:

- reads the list from GitHub at the release tag, never from the server's copy of the code, and
  only whole: a list cut off in transit stops the deploy before anything is touched, while an
  empty list, or none at all, sets nothing;
- copies `.env` into that deploy's backup folder as `openalgo.env` (mode 600, same owner);
- adds a missing key as `KEY='VALUE'`, and replaces a value only while it is one of the
  `OLD_VALUE`s (a default an earlier release shipped), so a value the owner chose stays;
- keeps every other line byte for byte, and the file's owner, group and mode;
- logs the names of the keys it changed and the values the release sets, never anything else
  from `.env`;
- puts the previous `.env` back, together with the databases, if the deploy rolls back.

`sudarshan-deploy --check` lists what the latest release would change, without changing it.
Tests: `sudarshan/ops/tests/test_deploy_env.py`.

`UPSTOX_ORDER_MAX_PER_SECOND  5` (M5.14) paces order calls to Upstox at 5 a second, half the 10
Upstox allows. Under the gthread worker only, an order call waits at most 10 s for its turn
(`utils/broker_backpressure.py`) and is refused after that, so at 5 a second the queue holds 50
order calls before refusing, where it held 80 at upstream's default of 8. A cancel or an Exit all sent behind a burst
waits its turn the same way. The eventlet worker (the default) has no such ceiling: its calls wait
however long the queue is.

`SUDARSHAN_JOURNAL  on` (M14, M15) turns the trade journal on: `sudarshan-journal` answers every
request as switched off until it is, and the alerts' report desk delivers reports only while the
journal says it is on (the desk reads it from the journal's `/health`). The line has no old value,
so an owner who sets it to `off` keeps it off through later deploys.

## NSE calendar lines that ship with a release (M0.1)

Nobody edits the server's day files by hand either. `/etc/sudarshan/nse_holidays.txt` (days NSE is
closed) and `/etc/sudarshan/nse_extra_trading_days.txt` (a session on a day that is otherwise
closed, with that session's own `HH:MM-HH:MM` window) drive the deploy tool's market gate,
auto-login's trading days, the order alerts' window and the feed's hours. A release
lists the lines it needs in `ops/deploy/nse-calendar.conf` (a `[FILE]` line, then `line TEXT`,
`was EARLIER_TEXT` for the same date, and `drop TEXT`; the file's header gives the format).
`sudarshan-deploy`:

- reads the list from GitHub at the release tag, never from the server's copy of the code, and
  takes it only when it arrived whole; the installed release's list is kept in
  `/var/lib/sudarshan/calendar-lists` once it arrived whole, and one GitHub did not find or that
  did not arrive whole keeps nothing and is asked for again on the next run;
- applies the list of the installed release on every run, also when it installs nothing and in
  market hours, because each reader picks up a changed file by itself; an install applies the
  list of the release it brings;
- adds a shipped line whose date the file does not list yet, and replaces a line only while it is
  a version an earlier release shipped for that date (a `was`); a date the owner wrote in his own
  words stays, and so does his removal of a line this tool added
  (`/var/lib/sudarshan/calendar_applied` records those lines); a `drop` takes out only a line
  this tool added;
- never changes today in a way that could open the market gate: a holiday is added only before
  its day; a run that installs nothing changes no line dated today or earlier (it only adds a
  special session for today); and the gate holds a release if the day files say trading as the
  run found them, after its calendar step, or as the release's own list would leave them (a
  release that announces today's session, or takes back today's holiday, waits);
- keeps every other byte and the file's owner, group and mode, creates a missing file
  root:sudarshan 640 (and writes only while `/etc/sudarshan` is root's and closed to others), and
  renames the new file into place in one step; a temporary file a killed run left is cleared;
- logs a change once it is written and each note (a line left alone, a failure) once while it
  holds; an install keeps the files it changes in its backup folder and puts them back if it rolls
  back; a list or a write that fails never stops an install.

The first release that carries the list is installed by the tool before it, which does not read
the list; the tool that install puts in place applies it on its first run, the next timer tick.
The deploy tool also asks the market gate again right before it stops OpenAlgo, in case the
window opened during the download. `sudarshan-deploy --check` shows what the latest release
would change. Tests: `sudarshan/ops/tests/test_deploy_calendar.py`.

## The ring's databases in a deploy's backup (M18.11)

Each ring service keeps its state in `/var/lib/sudarshan-<service>/` (`alerts.db`, `market.db`,
`portfolio.db`, `screener.db`, `journal.db`), not in OpenAlgo's `db/` folder, so the deploy tool treats
them apart from the databases it has always backed up:

- the ring is stopped before an install; then every `*.db` in every `/var/lib/sudarshan-*/` folder
  is copied with SQLite's online backup (rows still in the write-ahead log come with it; a plain
  file copy would lose them) into `<backup>/ring/<folder>/<name>`. The copy is made by the account
  that owns the folder (`stat -c %U`), `sudarshan`, or `sudarshan-family` for the family view's own
  folder, and streamed to the tool (next section); a folder any other account owns is left out with
  a warning, and nothing there is ever done as root. `sudarshan-family-export` holds only the family
  store's copy for the nightly backup, not a store, and is left out. Never at the top of the backup
  folder: a rollback puts every top-level `*.db` there into OpenAlgo's `db/` by name, and
  `market.db` exists in both places;
- a rollback puts each copy back in its own folder before OpenAlgo or any ring service starts
  again: the folder's own account writes it as a new file beside the database (mode 600), removes
  the old `-wal`, `-shm` and `-journal` files and renames the new file over the database. A folder
  that is gone is not made up. A database that cannot be put back is logged and left as it was
  found, and the rollback carries on;
- the sealed owner vault (`db/sudarshan-vault/`) is in neither direction: the deploy backup does
  not hold it, and a rollback never replaces what the owner saved after the backup was made;
- only plain names are used (`sudarshan-<name>` folders, file names of letters, digits, `.`, `_`
  and `-`) and no link is followed, so a file name cannot become a command or a path;
- before a ring service starts, in an install and in a rollback, the tool logs what
  `python -m sudarshan.<service>.migrate --status` says for each migrate module of that service
  (run as the account that owns the service's state folder, 60 s at most, the first 30 lines, each
  cut at 300 characters). No module, no line; no state folder yet (so no store), no line; a status
  that fails is one log line and never stops the install;
- a failure in any of this costs the copy or the line, never the install or the rollback.

The tool that does an install is the one already on the server (it updates itself from each
release, so a new tool acts from the next run). The first install that brings these steps is made
by the tool before them: it makes no ring copy, and a rollback of that install leaves the ring's
databases as they are. Tests: `sudarshan/ops/tests/test_deploy_ring_databases.py`.

## What an install does as root, and what it leaves to `sudarshan` (M18.11)

`sudarshan-deploy` runs as root in folders that belong to the service user: OpenAlgo's `db/` and
`frontend/`, and each ring service's `/var/lib/sudarshan-<service>/`. `sudarshan`, and anything
that runs as it (OpenAlgo included), can plant a link or swap a name there at any moment, and a
copy, a rename or a change of owner that root makes by such a path lands wherever the link leads: a
link planted at `alerts.db.restoring` was enough to overwrite, and take over, any file on the
server during a rollback. So:

- root opens, copies, renames and re-owns nothing by a path in those folders. OpenAlgo's
  databases, the ring's, the market history file (`historify.duckdb`) and the frontend are taken
  out and put back by the account that owns them (`sudo -u sudarshan`, or `sudo -u sudarshan-family`
  for the family view's store); the data crosses through a pipe
  from or to a file in the backup folder, which only root opens. A file put back is written whole
  as a new file beside its place and renamed over it, and a rename replaces a link and follows
  none;
- the install's backup folder, `/opt/sudarshan/backups/<time>-<tag>`, is root's alone, mode 700,
  and nothing in it is readable by others, not even for a moment (read it with `sudo`). What a
  rollback restores from cannot be changed by anyone who can write OpenAlgo's folders;
- of folders named like that (`YYYYMMDD-HHMMSS-vA.B.C`) the tool keeps the newest ten, and it
  removes no other folder in `/opt/sudarshan/backups`;
- a stop by the system, `^C` or a hangup between stopping OpenAlgo and its health check is a
  failure like any other: the install is rolled back, OpenAlgo and the ring start again and the
  owner is told. Only a failing command did that before; the services stayed stopped until the
  next run;
- every download has a time limit (a list or a checksum 60 s, the release's frontend 15 minutes,
  the fetch of the code 10 minutes), so a connection that stalls cannot hold an install up for
  ever;
- the lock, `/var/lib/sudarshan/deploy.lock`, is mode 644 and the state folder can be walked by
  others, whatever the service's umask, so the nightly backup (it runs as `sudarshan`) can open
  the lock and see that an install is running. The log writer does not hold the lock either: it
  outlived the tool now and then and kept the next run out;
- a notice goes on a line of its own: a line a crash cut off is closed first, and a cut-off line
  does not count as told.

Tests: `sudarshan/ops/tests/test_deploy_hardening.py`, and for the planted links
`test_deploy_ring_databases.py`.

## Nightly backups (M18.11)

`sudarshan-backup.timer` starts `sudarshan-backup.service` at 16:30 and 22:30 IST, every day. The
program (`python -m sudarshan.ops.backup`; code in `ops/backup.py`, the keeping rules in
`ops/retention.py`) decides for itself whether anything is to be done. It makes one archive a night
of what the server cannot rebuild:

| In the archive | What it is |
| --- | --- |
| `db/*.db` | OpenAlgo's databases (`openalgo.db`, `sandbox.db`, `logs.db`, ...) |
| `db/sudarshan-*/` | SUDARSHAN's own folders under `db/`, the sealed owner vault included, with their modes kept (folder 700, files 600) |
| `ring/sudarshan-*/*.db` | the ring's databases from `/var/lib` (alerts, market state, later portfolio and screener); the family view's store as the copy it leaves for this job, `sudarshan-family-export/family-copy.db` (the only file taken from that folder). The family view's own folder belongs to its own account and is closed to this job: it is passed over by name, never counted as a folder that could not be read |
| `etc/sudarshan/` | the machine keys, the settings and the NSE day files |
| `app/openalgo.env` | OpenAlgo's `.env`: without its keys a restored `openalgo.db` and the vault cannot be read |
| `MANIFEST.json` | written last: every file with its size and SHA-256, the release installed, anything that could not be saved |

Left out on purpose: `historify.duckdb` (market history, fetched again; DuckDB has no online
backup), nginx and the certificates (root's, issued again in minutes), and links, which are never
followed (a link could lead into another system's files). The archive holds the keys that protect
it (the `.env` is in it), so keep it as private as the `.env` itself and encrypt it before it
leaves the server (the offsite step below does, to the owner's public certificate).

**Never left out quietly.** Inside the places the job takes whole (`/etc/sudarshan`, the sealed
folders under `db/`, the `.env`), and for a database, what it does not take is named, not skipped:
a name with anything but letters, digits, `.`, `_` and `-` (or over 128 characters), a file over
32 MB (the `.env`: over 1 MB), more than 64 MB of files in `/etc/sudarshan` in all, folders nested
more than four deep (or any folder inside a sealed one), and a folder the job cannot list at all
(named with a closing slash, `etc/sudarshan/`: everything in it is missing). The archive is then
called partial and says so in its record, and the owner is told (cause `source`), so a key file
with a stray space in its name is found on the first night, not on the day it is needed.
`--check` lists them under "not taken". An editor's leftover (`feed.env~`) is such a file: remove
it, or the backup stays partial.

**Copied safely.** A database is copied with SQLite's own online backup, from a read-only
connection (the source is never written), into a private work folder, checked with SQLite's
quick check and only then packed. A plain copy of a live file can be caught half written and loses
the rows still in the write-ahead log (a test shows that). Any other file is copied to the work
folder first and packed from there, so a file that changes while it is read cannot damage the
archive. The archive is written under a temporary name, read back against its manifest and only
then renamed into place: a stop or a full disk leaves nothing that looks like an archive.

**When it runs, and when it does not:**

- Never from 08:30 to 16:00 on a trading day (to 16:05 from 7 Dec 2026, 20 minutes after the F&O
  close, which moves to 15:45 that day). A listed special day (a Muhurat evening) is kept
  for its session's own window widened by the margins the order alerts and the feed use, 30
  minutes before and 20 after: 13:35 to 16:00 for a session from 14:05 to 15:40. (The deploy tool
  is stricter and keeps a listed day for the market all day.) A run the timer or a boot starts
  earlier stops quietly; one still going when the market opens cleans up and leaves it for the
  next time.
- Not in the hour before those hours begin. A morning install (06:30 to 08:30) restarts the job,
  and a full backup would be cut off at 08:30: a run that the market hours would reach in less
  than 60 minutes stops quietly and leaves the night to the next timer. `--force` is no
  exception.
- Once a night: a complete archive less than 8 hours old ends a run at once, so the 22:30 timer
  is a second chance after a failure and nothing more, and the restart the deploy tool gives the
  service after an install costs nothing. A partial archive does not count as a night done.
- Not with an install. The deploy tool holds `/var/lib/sudarshan/deploy.lock` for the whole of an
  install; the job takes the same lock shared for as long as it copies and for the offsite upload
  that follows, waits up to 20 minutes for an install to end, and steps aside after that. A
  migration therefore never meets an open copy of the database it is changing, and an install
  that finds a backup running waits for the next timer tick (10 minutes) with the tool's usual
  "another deploy is running" line. A lock the job cannot open at all is not read as "no install":
  no archive is made and the owner is told (cause `lock`).
- Never twice at once (its own lock). A stop by the system (a deploy restarting the unit) cleans up
  and is not a failure: the unit is not left marked as failed.
- A night left undone is told. When a run stood aside (the market hours, another run, an install
  that would not end) and the newest complete archive is more than 30 hours old, the owner is told
  once a day how many hours that is and why the run stood aside (cause `overdue`). With no
  complete archive on the server at all nothing is said: there is nothing to be late against, and
  the first one comes from "Before the first night", below.

**Where the archives are, and who owns them.** In the unit's own state folder,
`/var/lib/sudarshan-backup/archive/` (folder 700, files 600, owner `sudarshan`), as
`sudarshan-backup-YYYYMMDD-HHMMSS.tar.gz` (`-partial` before the extension when something could
not be saved). Not under `/opt/sudarshan/backups`: the deploy tool keeps the newest 10 there and
removes the rest. **Not root-owned.** The deploy tool's unit policy refuses any unit that runs as
root, on purpose (nothing in the ring may be able to ask root for anything), so the job runs as
`sudarshan`. That keeps the archives from every other user on the server but not from a process
running as `sudarshan`: OpenAlgo and the ring do, and a compromise of the app could delete them.
The protection against that is the offsite copy; a root-side step in the deploy tool that moves
each night's archive into a root-owned folder is possible later and is the owner's decision.

**Keeping them.** 30 daily and 12 monthly archives: the newest of each of the 30 most recent
nights that have one, and the newest of each of the 12 most recent months, so a month is still
represented a year after its nights are gone. A night is represented by its newest *complete*
archive. The newest complete archive is never removed, whatever else is true: a run of partial
archives cannot push the last good one out.

**One archive per financial year, kept 8 years (M19.10).** The first complete archive made once
the market has closed on 31 March (from 16:00) is that financial year's archive, whatever the
counts above say; one made more than 92 days later is never taken for it. The newest 8 are kept
(`SUDARSHAN_BACKUP_KEEP_YEARLY`), for the years the tax records must be kept. Its offsite copy is
uploaded under a name of its own, `sudarshan-fy-2026-27-YYYYMMDD-HHMMSS-<release>.tar.gz.cms`,
so that the bucket's delete rules, which name the nightly prefix (step 3 below), never remove
it. The System page shows the newest year's archive and its size: eight of them must fit the
size cap and the bucket.

Then a size cap (10 GB for all of them), which never takes a monthly or a year's archive:

- before anything is written, what is outside the counts goes, and then the oldest nights, never a
  month's archive, never the three newest and never the newest complete one, until the new
  archive (estimated at 60% of the size of what is saved) fits. If it cannot fit, nothing more is
  removed, nothing is written and the owner is told (cause `cap`);
- then the disk is checked: room for that estimate, for the largest database staged beside the
  archive, and 2 GB left over. If not, nothing is written and the owner is told (cause `space`);
- after the write, the real size is held to the cap too; an archive bigger than its estimate that
  breaks the cap is kept and the owner is told.

**When something goes wrong,** the owner is told on Telegram once a day per cause (the notice
path above; a backup failure is told even while the market trades), in words like "The nightly
backup is incomplete: junk.db could not be saved. Everything else was saved. It tries again at
its next scheduled time; if it is incomplete again, have the server looked at." The message names
up to ten files, all or none (a longer list says "some files" and the archive's record has every
one). The causes: `source` (a file could not be copied, or was not taken for the reasons above: a
partial archive is made without it; a database that fails SQLite's check is kept, called out and
also makes the archive partial, cause `integrity`),
`space`, `cap`, `write` (the disk refused the archive or it did not read back whole), `offsite`
(the offsite copy failed, or its command did; the archive is safe on the server), `offsite_soon` (not a failure: the offsite upload link's expiry date is 15 days away or less), `lock` (the deploy tool's lock
cannot be opened, so an install cannot be told from none: nothing is saved until it can),
`overdue` (a run stood aside and the newest complete archive is more than 30 hours old) and
`settings` (a setting in `backup.env` cannot be used: the job exits with status 2 and changes
nothing). The job runs as
`sudarshan` and cannot write root's notice file, so it records in
`/var/lib/sudarshan-backup/events.jsonl`; `sudarshan-alerts` reads both files. `status.json`
beside it says how the last run that did something went.

**Settings** (optional, `/etc/sudarshan/backup.env`, owner `root`, group `sudarshan`, mode 640; a
value that cannot be used is refused by name and the owner is told):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUDARSHAN_BACKUP_KEEP_DAILY` | 30 | nights kept (1 to 366) |
| `SUDARSHAN_BACKUP_KEEP_MONTHLY` | 12 | months kept (0 to 120) |
| `SUDARSHAN_BACKUP_KEEP_YEARLY` | 8 | financial-year archives kept (0 to 50; 0 keeps none) |
| `SUDARSHAN_BACKUP_MAX_GB` | 10 | size cap for all archives (0.01 and up) |
| `SUDARSHAN_BACKUP_MIN_FREE_GB` | 2 | free space to leave beyond the archive (0 and up) |
| `SUDARSHAN_BACKUP_MIN_GAP_HOURS` | 8 | a complete archive newer than this ends a run (0 to 72) |
| `SUDARSHAN_BACKUP_DEPLOY_WAIT_MIN` | 20 | how long to wait for an install to end (0 to 600) |
| `SUDARSHAN_BACKUP_OFFSITE_CMD` | empty | an own offsite command (below); empty uses the built-in upload when `offsite.json` exists |
| `SUDARSHAN_BACKUP_OFFSITE_TIMEOUT_MIN` | 60 | the offsite command is stopped after this (1 to 720) |

**Offsite (from M18.11 part 2; does nothing until the owner has done the console steps below).**
After each good archive the job encrypts a copy on the server and uploads it to one OCI Object
Storage bucket of the owner's, which keeps every version (what that costs is in the table). Code:
`ops/offsite.py`.

| Choice | What and why |
| --- | --- |
| Encryption | `openssl cms -encrypt` to the owner's public X.509 certificate: AES-256-CBC, key wrapped with RSA-OAEP. openssl is on every Ubuntu, so nothing is installed and nothing can be "missing" but openssl itself (then the notice says so). It streams: a 300 MB archive takes about 13 MB of memory. |
| The key | The private key is made on the owner's laptop and never reaches the server (a second copy offline), so a server that is taken over cannot read the offsite copies. |
| A taken-over server | It holds the upload link, so it can still upload: new objects under any name, junk or a forged archive (the certificate is public, so a forged copy decrypts with the owner's key and passes `--verify`), and also under the name of a copy already there. With versioning on (step 2), such an upload becomes the newest version and the copy that was there stays, as a previous version, for 30 days after it was replaced (step 3). The link cannot read, list or delete anything, so it cannot remove or replace a copy already uploaded. A restore takes a version by the time Oracle shows it was uploaded, from before anything suspicious happened, and so ignores whatever was uploaded later ("Restoring from the offsite copy"). |
| What is inside | The same archive as the local one, `.env` included. A restore needs OpenAlgo's `APP_KEY` and `API_KEY_PEPPER`: without them `openalgo.db`'s encrypted columns and the vault cannot be read, and a second secret kept elsewhere is one more thing to lose. The copy that leaves the server can only be opened with the private key. The upload link itself (`offsite.json`) is not in any archive: it is a write credential, and a restore makes a new one. |
| Integrity | CBC does not authenticate by itself; after decrypting, `backup --verify` checks every file against the archive's SHA-256 manifest. That catches a damaged file, not a forged one: whoever holds the upload link can make an archive whose manifest matches. The upload time Oracle shows is what tells your copies from one uploaded later. |
| Upload | One `PUT` to a write-only pre-authenticated request (PAR) address for one bucket. The server holds no OCI credential. The address is in `/etc/sudarshan/offsite.json` only, never in a log, a notice, `status.json`, a process argument or an archive. |
| The file | `/etc/sudarshan/offsite.json`, mode 600, owner `sudarshan` (not root: the job runs as `sudarshan`, because the unit policy refuses root units, so a root-only file could not be read by it; no group or other can). It holds `par_url`, `expires` (`YYYY-MM-DD`) and `certificate` (public, PEM). A private key in it is refused. The set-up (step 9) has root make it empty with `install`, a standard system tool, and then `sudarshan` writes the settings into it in place, never replacing it or changing its owner or mode. Root never runs Python from the app folder for this: `sudarshan`, and anything that runs as it, can change what is there. A file that is there but cannot be used (another owner, a link, a folder, settings that cannot be read) is told as a failure, never taken for "not set up". |
| Names | `sudarshan-backup-YYYYMMDD-HHMMSS-<release>.tar.gz.cms`: the date and the release, no host name. |
| Retention | Two OCI lifecycle rules the owner sets (step 3): the newest version of a copy becomes a previous version after 30 days, and a previous version is deleted 30 days after it stopped being the newest, so a copy is kept about 60 days in all. The job never deletes an object, and the upload link could not. |
| What it costs | About 60 nights of copies are stored at a time (above). Oracle's Always Free allowance is 20 GB of Object Storage. On a Pay As You Go account that is an allowance, not a cap: storage past it is billed, and so is whatever someone holding the upload link uploads. Set a budget alert at a small amount (console: Billing, Budgets), look at the bucket's size after the first weeks, and shorten both rules of step 3 if about 60 nights of copies would pass 20 GB. |
| Expiry | The date in the file is the expiry date the console shows for the link. The console gives it a time of day, so the link may stop at any time on that date: the job takes that date as the first day the link is not used, and the last copy goes the day before. From 15 days before the date, the owner is told once a day on Telegram (cause `offsite_soon`) and the copies still go. From the date on, nothing is tried and the owner is told why (cause `offsite`, "the upload link has reached its expiry date"). |
| Failure | One notice: "Tonight's offsite backup did not upload: <reason>. The local backup is fine." The reasons are in words, with the next step where there is one: no encryption tool; could not reach Oracle's storage; storage refused the file (if the upload link is no longer listed in the bucket, make a new one with steps 4, 5 and 9); storage busy; the link has reached its expiry date (steps 4, 5 and 9); settings unusable or the backup could not be encrypted (run step 9 again). An answer the storage gives before it has read the whole file counts as what it says, so a link that is gone is told as a refusal and tried once. A busy or unreachable bucket is tried 3 times, 30 seconds apart; the local archive is never touched, and the next night uploads the next archive (the missed night's archive stays on the server for the local retention). Success is one journal line. |
| Not set up | Without the file, or with the empty file the set-up makes before the settings are typed in, the step is off: one journal line a day says so, and the local backup is unaffected. `--check` prints `offsite: not set`. |

`SUDARSHAN_BACKUP_OFFSITE_CMD` (the older hook below) still works for anyone who prefers their own
tool; when it is set it is used instead of the built-in upload, not as well as it. It is a command
line, run without a shell after each good archive with `{archive}` (the file's full path), `{name}`
and `{dir}` replaced in it, and it must name `{archive}` or `{dir}`. It runs as `sudarshan` with
nothing from the job's environment but `PATH`, `HOME` (the state folder) and `LANG`, in the state
folder, under the same shared deploy lock as the copy; its output goes to `offsite.log` there
(mode 600) and a failure is told to the owner.

### Setting up the offsite copy: the owner's steps

You need the OCI console in a browser, PowerShell on the laptop with Git for Windows (it brings
openssl) or OpenSSL for Windows, and the SSH login you already use for the server. Step 9 is one
`.bat` file that does the server's part: it sends only the public certificate, shows its
fingerprint on the laptop and again on the server, saves nothing until you type `yes`, and asks
for the upload link (the PAR address) in a hidden prompt on the server, so the link never appears
on a screen, in a file on the laptop or in the shell history. On the server, root only makes the
empty settings file, with `install`; the settings are written by `sudarshan`, the job's own user.

1. In the OCI console, open Storage, then Object Storage (Buckets), in the region the server uses.
2. Choose Create Bucket: any name, Standard tier, Private, and tick **Enable object versioning**.
   Versioning is what keeps the copies safe if the server is ever taken over: an upload under a
   name already in the bucket then becomes a new version and the copy that was there stays, so
   whoever holds the upload link cannot replace or remove a copy already uploaded. Open the
   bucket: under Features, Object versioning must read Enabled. (For a bucket made earlier,
   choose Enable there, then Enable versioning.)
3. In the bucket, open Policies, then Lifecycle policy rules, and make two rules with Create rule.
   Each has Lifecycle action Delete, Number of days 30 (or what you like, the same in both), the
   State switch on, and under Object name filters one filter of the kind **Include by prefix**
   with the text `sudarshan-backup-` (so the rule deletes the nightly copies only, and never a
   financial year's archive, whose name starts `sudarshan-fy-`; if you made the two rules before
   v0.13.1 without this filter, edit both and add it before the first 31 March):
   - Target **Latest version of objects**: after 30 days the newest version of a copy becomes a
     previous version;
   - Target **Previous versions of objects**: a previous version is deleted 30 days after it
     stopped being the newest.

   These two rules are the only thing that ever deletes. A copy is kept about 60 days in all, and
   a copy that a later upload under its name replaced is kept 30 days after that upload. Then set
   a budget alert in the console (Billing, Budgets) at a small amount: see "What it costs" above.
4. In the bucket, open Management, then Pre-authenticated requests, and choose Create
   pre-authenticated request: target Bucket, access type **Permit object writes** (not reads; leave
   Enable object listing off), and an expiration date. Pick the furthest date the console allows.
5. Copy the address the console shows (the pre-authenticated request URL) into your password
   manager, and write the expiration date beside it as year-month-day, for example 2027-11-12.
   The console shows the address only once.
6. On the laptop, open PowerShell and make a folder for the key: `mkdir $HOME\sudarshan-backup-key`
   then `cd $HOME\sudarshan-backup-key`.
7. Make the key pair. Git for Windows ships openssl at `C:\Program Files\Git\usr\bin\openssl.exe`;
   OpenSSL for Windows (`winget install ShiningLight.OpenSSL.Light`) puts it at
   `C:\Program Files\OpenSSL-Win64\bin\openssl.exe`, and then that path goes in the line below
   (and in the restore steps) instead:
   `& "C:\Program Files\Git\usr\bin\openssl.exe" req -x509 -newkey rsa:4096 -keyout sudarshan-backup.key -out sudarshan-backup.crt -days 36500 -subj "/CN=sudarshan-backup"`
   It asks for a passphrase twice. Choose one and keep it in your password manager, not with the
   key: a passphrase kept beside the key protects nothing if the USB stick is lost.
8. Copy `sudarshan-backup.key` to a second place that is not the server and not this laptop (a
   USB stick kept offline). Without the key no backup in the bucket can ever be opened. Never copy
   it to the server.
9. In the same folder, save the lines below as `sudarshan-offsite.bat` (in Notepad: Save as, then
   Save as type "All files"). Change `USER@SERVER` in its fourth line to the login you already use
   for the server, then double-click the file:

   ```bat
   @echo off
   setlocal
   cd /d "%~dp0"
   set "SERVER=USER@SERVER"
   set "OPENSSL=C:\Program Files\Git\usr\bin\openssl.exe"
   if not exist "%OPENSSL%" set "OPENSSL=C:\Program Files\OpenSSL-Win64\bin\openssl.exe"
   if not exist "%OPENSSL%" (
     echo openssl was not found. Install Git for Windows, or OpenSSL as step 7 says. Nothing was changed.
     goto stop
   )
   if not exist sudarshan-backup.crt (
     echo sudarshan-backup.crt is not in this folder. Save this file next to it. Nothing was changed.
     goto stop
   )
   echo The certificate on this laptop:
   "%OPENSSL%" x509 -noout -fingerprint -sha256 -in sudarshan-backup.crt
   if errorlevel 1 (
     echo sudarshan-backup.crt is not a certificate. Make the key pair again, step 7. Nothing was changed.
     goto stop
   )
   scp sudarshan-backup.crt %SERVER%:sudarshan-backup.crt
   if errorlevel 1 (
     echo The certificate was not copied to the server. Nothing was changed.
     goto stop
   )
   ssh -t %SERVER% "cd /opt/sudarshan/apps/openalgo && { sudo test -e /etc/sudarshan/offsite.json || sudo install -o sudarshan -g sudarshan -m 600 /dev/null /etc/sudarshan/offsite.json; } && sudo -u sudarshan .venv/bin/python -m sudarshan.ops.offsite --configure < ~/sudarshan-backup.crt; code=$?; rm -f ~/sudarshan-backup.crt; exit $code"
   if errorlevel 1 (
     echo The set-up did not finish: read the lines above, fix what they name and run this file again.
     goto stop
   )
   pause
   exit /b 0
   :stop
   pause
   exit /b 1
   ```

   It stops at the first step that fails and says so. The certificate goes to your home folder on
   the server, is read from there by your own login, and is removed at the end, whatever happened.
   What you should see, in this order:
   - `The certificate on this laptop:` and a line that starts `sha256 Fingerprint=`, then 32 pairs
     of letters and digits such as `4D:E1:53:...`.
   - scp and ssh may first ask for your SSH password or key passphrase, as when you log in; then
     `[sudo] password for ...:` from the server: your own password there, typed once.
   - `The certificate the server received has this SHA-256 fingerprint:` and 32 pairs, then
     `Type yes if the two are the same, or press Enter to stop:`. The pairs must be the same as
     the laptop's line. If they are, type `yes` and press Enter. If they are not, press Enter:
     nothing is saved. Then do not go on, and have the server looked at, because the file was
     changed on its way.
   - `Expiry date of the upload link, as the console shows it (YYYY-MM-DD):` type the date you
     wrote down in step 5, for example `2027-11-12`.
   - `Upload link (nothing shows as you paste or type it; press Enter after it):` paste the address
     from step 5 (a right-click pastes in this window) and press Enter. Nothing shows as you paste;
     that is how it should be.
   - `Saved.`, the command for step 10, then `Press any key to continue . . .`.

   A line that starts with `Not saved:` says why and what to do, and nothing was changed: fix what
   it names and double-click the file again.
10. On the server, check it as the job's own user: `cd /opt/sudarshan/apps/openalgo && sudo -u
    sudarshan .venv/bin/python -m sudarshan.ops.backup --check`. The last line should say
    `offsite: set (the link stops working in N days, on <the date you typed>)`. If it says
    `not set`, step 9 did not save: double-click the `.bat` again and read its last lines.
11. The next night's backup uploads one object. Look for it in the bucket the next morning
    (Objects: one `sudarshan-backup-...-v0.8.0.tar.gz.cms`), and check that Object versioning
    still reads Enabled.
12. From 15 days before the link's expiry date you are told on Telegram, every day until it is
    renewed: repeat steps 4, 5 and 9 (the certificate stays the same, and the `.bat` writes the
    new link over the old one). Then delete the old link in the console (Management,
    Pre-authenticated requests): until its date, anyone who copied it could still upload with it.

### Restoring from the offsite copy

1. In the console, open the bucket, then Objects. Each copy's name carries its date and release
   (`sudarshan-backup-YYYYMMDD-HHMMSS-v0.8.0.tar.gz.cms`), but a name proves nothing: whoever
   holds the upload link can upload under any name. Pick the copy by the time Oracle shows it was
   uploaded (Oracle's clock, not the name and not the server's):
   - If the server may have been taken over, or anything about it looks wrong, first delete the
     upload link (Management, Pre-authenticated requests, then Delete from its menu), so nothing
     more can be uploaded. Then, from the copy's Actions menu (the three dots), choose View
     versions: the newest version is at the top. Download a version uploaded before anything
     suspicious happened. A copy uploaded after that may have been made by whoever had the upload
     link, even if it decrypts and passes `--verify`. A copy that an upload replaced stays there
     30 days after that upload (step 3), so do this within that time.
   - Otherwise, download the copy for the day wanted.
2. On the laptop, decrypt it with the private key (it asks for the passphrase). Decrypting needs
   free memory of about three times the file's size:
   `& "C:\Program Files\Git\usr\bin\openssl.exe" cms -decrypt -inform DER -in <the .cms file> -inkey sudarshan-backup.key -recip sudarshan-backup.crt -out sudarshan-backup-restore.tar.gz`
3. Copy the `.tar.gz` to the server into a private folder (mode 700), and `--verify` it there
   (`python -m sudarshan.ops.backup --verify FILE`).
4. Restore from it with the manual steps under "Restoring" above. Then delete the decrypted file
   from the laptop and the server: it holds the `.env`.

**Looking at them.** `python -m sudarshan.ops.backup --check` says what a run would do now and
changes nothing: the hours (open, blocked, or not started this close to the market), the sources,
what would not be taken (`not taken:`), what the user it runs as cannot open (`cannot be read:`),
the archives and the offsite command. `--verify FILE` reads an archive back against the record it
carries (0 sound, 1 something wrong, 2 not an archive) and says whether it is complete. `--force`
makes an archive although a recent one exists.

**Before the first night.** On the server, from the app folder, run the check as the job's own
user:

```sh
sudo -u sudarshan .venv/bin/python -m sudarshan.ops.backup --check
```

It should print neither a `cannot be read:` nor a `not taken:` line. Run as root the check would
see everything; as `sudarshan` it sees what the nightly job sees, and a file in `/etc/sudarshan`
only root can open would otherwise make every archive partial, and the first sign would be the
notice after the first night. Fix what it names, then let the first night run (or start
`sudarshan-backup.service` by hand outside the market hours) and read the archive with
`--verify`: from then on a late night is told.

**Restoring** is a manual step, done with care and not by a rollback (a rollback uses the deploy
tool's own backup, and never puts the vault back):

1. `--verify` the archive, then unpack it into an empty private folder, not over live files.
2. Stop SUDARSHAN's services that use the files (never TRINETRA's).
3. Put each file back where its name says: `db/...` into OpenAlgo's `db/`, `ring/<folder>/<name>`
   into `/var/lib/<folder>/`, `etc/sudarshan/...` into `/etc/sudarshan/`, `app/openalgo.env` as
   OpenAlgo's `.env`; owner and modes as in the archive; remove the old `-wal` and `-shm` files
   beside each restored database first.
4. Restore the vault and `openalgo.env` of the same archive together (the vault's key comes from
   the `.env`), start the services and look at `/health`.

Tests: `sudarshan/ops/tests/test_backup_retention.py`, `test_backup_run.py`,
`test_backup_policy.py`, `test_backup_command.py` and `test_backup_unit.py`, with the notice
wording in `sudarshan/alerts/tests/test_alerts_notices.py`.

### The restore drill (M19.8)

A backup that has never been restored is not a backup. `sudarshan-drill` reads the newest
complete archive back, by itself, on the first Sunday of each month at 11:00
(`sudarshan-drill.timer`; never on a listed trading day, never in or just before market hours),
and tells you the result on Telegram. It restores nothing over live files, stops no service and
uses no network. In a private scratch folder inside the backup's own folder
(`/var/lib/sudarshan-backup/drill`, removed again on every outcome) it:

1. reads the archive back against the record it carries, and refuses an incomplete one;
2. unpacks it;
3. opens every database read-only, runs SQLite's own check on it, and refuses a store that is
   newer than this release understands;
4. checks that OpenAlgo's database holds exactly one owner account, an API key and an Upstox
   login;
5. checks that the archive's `.env` has the keys the app cannot start without (never printed);
6. opens the vault of sealed sign-in details with the key derived from that same `.env`: the
   step that proves the two restore together.

The result is on the System page, with the first thing that failed in plain words, and in
`/var/lib/sudarshan-backup/drill.json`. The watch tells you when the last drill is over 100 days
old. What it proves: the archive can be read back into a working set of files; it does not
prove that a new server boots ("If the server is lost" below).

To run it by hand, outside market hours, as the service user from the app folder:

```sh
cd /opt/sudarshan/apps/openalgo && sudo -u sudarshan .venv/bin/python -m sudarshan.ops.drill
```

It prints one line (`restore drill: passed ...`, or `FAILED:` and the cause) and sends the same
on Telegram. Without a file name it does nothing on a trading day; give it an archive to test
that one on any day after the close:
`sudo -u sudarshan .venv/bin/python -m sudarshan.ops.drill /var/lib/sudarshan-backup/archive/<name>`.
The deploy tool restarts the unit after an install like every other, so an install on a day
without trading runs one more drill.

**Your own drill, once a quarter.** The monthly drill never touches the bucket, the encryption
or your private key. Once a quarter: download a copy from the bucket, decrypt it on the laptop
and copy it to the server ("Restoring from the offsite copy" above, steps 1 to 3, into a folder
only `sudarshan` can read), then run the drill on that file with the line above and delete the
decrypted file from the server and the laptop. This is the only test of the bucket, the
encryption and the key together.

## Server notices on Telegram (M18.x)

After an install, a rollback, a service file the unit policy refused, a release that waits for
the market to close, a nightly backup that failed, or (once the family set-up below has been run) a
family check that failed, the owner gets one short message on Telegram
in trader language (examples in `sudarshan/alerts/README.md`). The deploy tool runs as root, so it
holds no Telegram or OpenAlgo secret and cannot be made to send anything:

- it only **records** the fact, one JSON line, in `/var/lib/sudarshan/events.jsonl` (mode 644, the
  folder made traversable; `ops/events.py` has the format and `ops/files.py` the safe reader);
  `sudarshan-alerts` reads it, words it, and sends it once per event id (`alerts/notices.py`);
- the nightly backup runs as `sudarshan` and cannot write root's file, so it records its failures
  the same way in `/var/lib/sudarshan-backup/events.jsonl` (`ops/events.py` `record()`); both
  files are read;
- the watch (`sudarshan-watch`, "Watching the server" below) records each change of a check in
  `/var/lib/sudarshan-watch/events.jsonl`, read the same way: a failure and the recovery from one
  are told at once, in market hours too; a warning waits for the close;
- recording is best effort, in its own subshell with its own error handling: a missing `jq`, a full
  disk or an unwritable folder costs the notice, never an install or a rollback; the file keeps its
  newest 200 lines; `--check` records nothing;
- a release that fails at every timer run is recorded once per release and cause; a release that
  waits is recorded once; the facts of one run share a run id and are told in one message;
- the owner's phone stays quiet while the market trades: from 09:15 to the F&O close (15:40;
  15:45 from 7 Dec 2026) only a failure
  (a rollback, a refused service file, a failed backup) is told, the rest goes out after the close,
  and no more than one message a minute ever goes out;
- the tool of the release before this one made no record, so the first run of a tool that records
  tells the install it finds (once, if the tag file is under 12 hours old, at the time it
  happened). The tool that does the install acts from the *next* run (self-update), so a release's
  own install is silent when the tool in place predates it.

Tests: `sudarshan/ops/tests/test_deploy_notices.py` (the whole tool on a fake server),
`test_ops_events.py`, and `sudarshan/alerts/tests/test_alerts_notices.py`.

Tests: `sudarshan/ops/tests/test_ops_docs.py`.

## Watching the server (M19.5)

`sudarshan-watch` looks at the server every 2 minutes, day and night, and tells you on Telegram
when something stops working and again when it works. It is a small program that a timer starts
and that ends a second or two later (`sudarshan-watch.timer`, `sudarshan-watch.service`; the
deploy tool installs both with the release). Nothing needs setting up for it.

| it looks at | it tells you when | when you are told |
|---|---|---|
| each SUDARSHAN service: price feed, alerts, portfolio, screener, family view, journal | one has not answered for two looks in a row (about 4 minutes) | at once |
| the Upstox login | from 08:58 on a trading day there is no login for today; later in the day, order updates have stopped arriving for 4 minutes | at once |
| the price feed's connection to Upstox | not connected for 4 minutes while the market is open | at once |
| the instrument list | it was not refreshed today by 09:00 on a trading day | at once |
| the server's disk | under 15 GB free (a warning); under 5 GB free (a failure) | the warning after the market closes, the failure at once |
| the server's memory | under 1 GB available for three looks in a row | after the market closes |
| the security certificate | under 48 hours left and not renewed | at once |
| OpenAlgo itself | it has not answered for two looks in a row | only through the outside heartbeat, because Telegram notices go through OpenAlgo |
| AI connections (OpenAlgo's own tables, read-only) | one more approved AI client, or one more live AI sign-in, than at the last look | at once: "press Disconnect the assistant on the System page" if it was not you |
| Telegram accounts linked to your bot | one was linked or unlinked | at once (a linked account can run the bot's commands) |
| the server's posture, once an hour: the assistant's safety settings, OpenAlgo's debug setting, the settings folder, the vault and the `.env` closed to other accounts, every SUDARSHAN port listening inside the server only | any of them is not as it should be | after the market closes, once per cause |
| the restore test of the backups ("The restore drill" below) | the last one is over 100 days old | after the market closes (the drill tells its own pass or failure) |
| Historify's downloaded history | it has reached 3 GB | after the market closes |
| shown only, on the System page and in the Sunday summary: the nightly backup's age and its offsite copy, operating-system updates waiting, a restart the system asks for | never a notice of their own: the backup job tells its own late night, and updates are applied by you, by hand, on a non-trading day | - |

- **Once per cause.** A failure is told once and its recovery once, however long it lasts. A
  service that was never set up on this server (the family view before its set-up) is not a fault.
- **While a release is being installed** the watch stands aside: the deploy tool stops and starts
  every service, and none of that is told.
- **It only reads and records.** It never restarts, stops or changes a service, holds no Telegram
  or OpenAlgo key, and sends nothing itself: it writes what changed to
  `/var/lib/sudarshan-watch/events.jsonl`, and `sudarshan-alerts` words and sends it ("Server
  notices on Telegram" above). It looks only at SUDARSHAN's own services and files and at
  OpenAlgo; nothing else that runs on the server is read, asked or named, and no outside site is
  called.
- **What it cannot tell you.** If the alerts service or OpenAlgo is down, a notice cannot go out
  until it is back; if the whole server is down, nothing here runs at all. The outside heartbeat
  below is for those two cases.

**The Sunday summary.** On Sunday from 18:00 one message sums the week up: what needs you (or
"Nothing needs you."), how many checks are fine, free disk space and the sizes of OpenAlgo's
databases and logs, Historify, SUDARSHAN's own stores and the backups, the newest backup's age
and the last restore test, system updates waiting and whether the system asks for a restart, and
how busy the server was: the level its processor and its memory stayed under for 95% of the
week. Those two are the whole server's figures, as the kernel gives them to every program (the
tenancy's idle rule is about them); the size of the system's own journal is not in it, because
that journal is not SUDARSHAN's to read.

**The server diary.** The notice files keep their newest 200 lines. The watch copies every
notice of every producer (installs, rollbacks, backups, drills, family sign-ins, reports made,
its own) once into `/var/lib/sudarshan-watch/log/<year>.jsonl`, one small text file a year,
keeps 8 years of them and never anything secret, and the nightly backup takes the folder. It is
the server's part of the "Records to keep each year".

To see what it sees, at any time, as the service user from the app folder (it writes nothing):

```
cd /opt/sudarshan/apps/openalgo && sudo -u sudarshan .venv/bin/python -m sudarshan.ops.watch --check
```

One line per check: `fine`, `warning`, `failing`, `unread` (could not be read just now) or `off`
(not expected now: the market is closed, or the service is not set up). The last look is also in
`/var/lib/sudarshan-watch/report.json` (`sudo cat` it), and on the System page.

**The System page (M20.8).** Menu, System (`/system`, the owner's account only) shows the last
look in one list, each line with an icon, a word and what to do: the services, the market day's
connections, the disk, memory, the certificate, updates, the outside heartbeat, the nightly
backup and the year's archive, the last restore test with the first thing that failed, the
safety settings, AI connections, Telegram links and your signed-in devices. It says so when the
watch has not looked for over 10 minutes. It is the page a support session asks you to open.
**Disconnect the assistant** on it ends every AI connection at once: every sign-in is revoked
and every token already handed out is refused at its next call. Use it whenever an AI
connection should end and the kill switch is not the tool: while the risk guard checks the
sandbox only, the kill switch leaves the assistant alone. Nothing comes back by itself; you
connect and approve again.

**After the release that brings the watch is installed, once:** run the `--check` line above and
read it; then, outside market hours, stop the screener for 5 minutes
(`sudo systemctl stop sudarshan-screener`, wait for the Telegram message, then
`sudo systemctl start sudarshan-screener` and wait for the second one).

### The outside heartbeat: the owner's steps

The watch runs on the server, and its notices go through OpenAlgo. If the whole server stops, or
OpenAlgo does, nothing on the server can tell you. An outside heartbeat can: a service on the
internet that expects a call from the server every 2 minutes and tells you, through its own
channel, when the calls stop. Without it the watch does everything else, and its report says "No
outside heartbeat is set up: nobody is told if the whole server stops."

SUDARSHAN creates no account anywhere. These are your steps, once, about ten minutes:

1. **Pick a heartbeat service** (they are also called "dead man's switch" or "cron monitoring"
   services) that gives each check a secret web address to call and alerts you when the calls
   stop. Healthchecks.io is one whose free plan is enough for one check; any service that works
   the same way will do. Create the account yourself, with your own email address.
2. **Create one check.** Name it something plain such as `server`; do not put your name, the
   broker or the server's address in it. Set **Period 2 minutes** and **Grace 8 minutes**, so you
   are told when nothing has arrived for 10 minutes.
3. **Choose how it tells you:** email at least, and the service's own phone or Telegram
   notification if it has one (that is the service's bot, not SUDARSHAN's).
4. **Copy the check's address** (it starts with `https://`). Treat it as a secret: whoever has it
   can keep the check green or raise a false alarm. Never paste it in a chat or a screenshot.
5. **Put it on the server,** in a file only root and the service user can read:

   ```
   sudo install -m 640 -o root -g sudarshan /dev/null /etc/sudarshan/watch.env
   sudo nano /etc/sudarshan/watch.env
   ```

   The file holds one line, with your address in place of the example:

   ```
   SUDARSHAN_WATCH_PING_URL=https://hc.example/your-check-address
   ```

6. **See it arrive.** Within 2 minutes the service's page shows the check as up. On the server,
   the `--check` line above ends with `heartbeat set`.
7. **Test it once,** outside market hours: `sudo systemctl stop sudarshan-watch.timer`, wait for
   the service's message (a little over 10 minutes), then
   `sudo systemctl start sudarshan-watch.timer` and see the check come back up.

How it behaves:

- While notices can go out, each look calls your address. While OpenAlgo or the alerts service has
  not answered for two looks in a row, or the watch cannot write its own files, it calls the
  address with `/fail` added, which Healthchecks.io and services like it read as "failing now",
  so you are told at once and not after 10 minutes. If your service uses a different address for
  failure, add a second line, `SUDARSHAN_WATCH_PING_FAIL_URL=https://...`.
- While a release is being installed the plain address is still called: the server is alive.
- Nothing is sent with the call: no figure, no name, no account. The service sees the server's
  address and the time, nothing else. The address is never written to a log, the report or
  Telegram, and it is in the nightly backup with the rest of `/etc/sudarshan`.
- An address that does not start with `https://`, or that carries a user name or a `?`, is not
  used; `--check` then says `bad_link`.

Tests: `sudarshan/ops/tests/test_watch_checks.py` (the vectors), `test_watch_run.py`,
`test_watch_reach.py`, `test_watch_unit.py`, `test_watch_heartbeat.py` and
`sudarshan/alerts/tests/test_alerts_notices_watch.py`.

## The family view's one-time set-up (P6)

The family view (family members see the books, view only, with no way to place an order) runs as
its own account on the server, `sudarshan-family`, which can read none of the owner's secrets:
not OpenAlgo's `.env`, `db/` or `log/`, not the sealed vault and not `/etc/sudarshan`. Its own
release comes later, switched off. This release (v0.10.0) only puts the pieces in place, and on a
server where the set-up below has not been run none of them does anything:

- `ops/family/bootstrap.sh`, the set-up the owner runs once (below);
- `python -m sudarshan.family.derive`, which the deploy tool runs as `sudarshan` to get the family
  account's seal keys (root never runs the app's Python: it checks each key it is handed and writes
  it itself);
- `python -m sudarshan.family.check`, which the deploy tool runs as `sudarshan-family`: it passes
  only when that account cannot open OpenAlgo's `.env`, any file in `db/`, `log/` or the vault,
  anything in `/etc/sudarshan`, the alerts' or the portfolio's state, or the feed's claims; can open
  every file in `/etc/sudarshan-family`; and is refused by the firewall when it tries OpenAlgo's
  market-data bus (port 5555) or its WebSocket proxy (port 8765), while the portfolio's port (5583)
  stays open to it. One line per check, PASS or FAIL, never a secret;
- in the deploy tool: the unit policy lets the family view's own service run as that account (and
  no other service, and only once its check has passed); the ring's databases are copied and put
  back by the account that owns their folder; and the provision step below;
- in the nightly backup: the family view's own folder is passed over, and the copy of its store it
  will leave for the backup is taken.

**The price feed's client tokens (every server, from this release).** The deploy tool makes one
token per ring client of the price feed, once, in `/etc/sudarshan/feed-clients/` (folder root,
group `sudarshan`, mode 750; each file mode 640): `sudarshan-alerts`, `sudarshan-screener`,
`sudarshan-portfolio`, `sudarshan-journal`, `sudarshan-family` and `sudarshan-protect`. Each holds
32 random bytes as 43 characters on one line. They are what a ring client shows the feed when it
changes the instruments the feed follows for it, so one client can no longer drop or replace
another's (the feed starts asking for them with its own change). A token that is there is never
written again; one that is deleted is made again, new, at the next run. The feed reads them all;
the family account reads only its own copy in `/etc/sudarshan-family/feed-token`. They are the
only new files a server without the family set-up gets, and making them is not logged.

### Running the set-up, once this release is installed

1. On the laptop, open the SSH session to the server, as for the offsite set-up.
2. Paste this line and press Enter (`sudo` asks for your password on the server). It fetches the
   set-up from GitHub at the release installed on the server, as the deploy tool fetches its own
   code, into a folder only root can read, runs it as root and removes it. It never runs a copy
   from the app's folder, which the service account can change.

   ```sh
   sudo bash -c 'd="$(mktemp -d)" && . /etc/sudarshan/deploy.env && curl -fsSL "https://raw.githubusercontent.com/${REPO#https://github.com/}/$(cat /var/lib/sudarshan/deployed_tag)/sudarshan/ops/family/bootstrap.sh" -o "$d/bootstrap.sh" && bash "$d/bootstrap.sh"; rc=$?; rm -rf "$d"; exit $rc'
   ```

   To see first what it would do, without changing anything, put ` --dry-run` after
   `"$d/bootstrap.sh"`.
3. What it does, saying each step: makes the account `sudarshan-family` (no home, no login shell,
   not in the group `sudarshan`); makes `/etc/sudarshan-family` (root, group `sudarshan-family`,
   750) and `/var/lib/sudarshan-family-export` (`sudarshan-family`, group `sudarshan`, 2750);
   installs the account's allow-list of outgoing connections, an nftables table of its own
   (`sudarshan_family`) loaded at every boot by `nft-sudarshan-family.service` (the account may
   reach this server only on the ports the family view uses and the local name resolver, and
   elsewhere only HTTPS); then runs the deploy tool's provision step (`sudarshan-deploy
   --family-provision`): the seal keys, a storage key and a Web Push key pair made once, copies of
   the NSE day files and of its feed token, and then the check, as the account itself. Nothing else
   on the server is touched: no other firewall table, not nginx, no other service.
4. What you should see: lines starting `changed:` (or `in place:` on a later run), the provision
   step's lines starting `family:`, and at the end `PASS.`. If it lists `chmod o-rwx ...` lines,
   other accounts can read some of OpenAlgo's files: run those lines yourself (nothing that runs
   needs those files open to others), then the set-up again. A line starting `FAIL` says what is
   wrong; fix it and run the set-up again. `an install was running` means wait ten minutes and run
   it again.
5. Running it again is safe at any time: a second run changes nothing and says so (`PASS. Nothing
   was changed`).

From then on the deploy tool keeps the keys and copies in place at every run (every ten minutes)
and runs the check again; a check that fails stops the family view until it passes again and is
told on Telegram once. The install backup keeps a copy of `/etc/sudarshan-family` (a rollback never
puts it back, as for the vault). The family view itself, its bot and its nginx lines come in a later
release, switched off; nothing more is needed until then.

To take it out again: `systemctl disable --now nft-sudarshan-family.service`, `nft delete table
inet sudarshan_family`, remove `/etc/systemd/system/nft-sudarshan-family.service`,
`/etc/nftables-sudarshan-family.nft` and the folder `/etc/sudarshan-family` (the deploy tool then
stops at once), and `userdel sudarshan-family`.

Tests: `sudarshan/ops/tests/test_family_bootstrap.py` (the set-up, and its allow-list loaded in a
network namespace of its own), `test_deploy_family.py` (the deploy tool's side, and that a server
without the set-up gets nothing but the tokens), `test_backup_family.py`, and
`sudarshan/family/tests/`.

## The risk layer's confirm phrase, if it is forgotten (M5.13)

The kill switch cannot be turned off, and no limit can be loosened, without the confirm phrase,
and the app never shows it once saved. If it is forgotten, it is set back to the default (STOP)
on the server, as the service user, from the app folder the deploy tool installs into:

```
cd /opt/sudarshan/apps/openalgo && sudo -u sudarshan .venv/bin/python -m sudarshan.guard.phrase --reset
```

- `--status` says whether the phrase is the default. It never prints the phrase.
- The reset writes a line to Settings > Risk > History ("The confirm phrase was set back to the
  default on the server.") and sends the owner a notice on Telegram, so a reset nobody asked for
  is seen. The command's output says whether the notice went; when the Telegram bot is off or no
  account is linked, it says so and the reset stands.
- It applies at the next try, within seconds, and nothing needs restarting: the app reads the
  phrase from the database each time it is asked for, never from what it kept a minute earlier.
- Change it again afterwards from Settings > Risk. A phrase that is already the default is left
  alone: the command says so and writes and sends nothing.
- Every wrong phrase is in History as well (turning the kill switch on or off, changing a limit),
  with what was being asked and how many tries, never with what was typed: tries in a row within
  a minute, from one place (the app or Telegram), share one line, counted up. A box left empty
  is asked for again and not recorded.

Tests: `sudarshan/guard/tests/test_guard_phrase.py`.

## Signing out every device, and a lost phone (M2.3)

A logout, a password change or a password reset on one device signs every device out at its next
page, read or reconnect, a phone that was asleep at the time included (v0.8.0). The server keeps
that moment (`session_cutoffs`) and refuses a session that logged in before it. A second device's
login never signs the first one out.

What they do not change:

- **The API key.** TradingView, Amibroker and the strategy host place orders with it, and those
  orders go out from the server's own registered address, so the static IP rule does not stop
  them. A logout or a password change leaves the key working, and every signed-in screen could
  read it. The app keeps a copy in the browser and removes it as soon as a screen learns that its
  session has ended (a logout, a read the server refuses, a live connection refused after a
  logout elsewhere).
- **The Upstox login**, on a password change or a reset: orders keep working. A logout ends it,
  as it always has.

If a phone is lost, or a session may have been copied:

1. On another device, change your password: Profile, Account, Change Password. Every device is
   signed out; log in again with the new password.
2. Open API Key in the profile menu, choose Regenerate and confirm, then Copy the new key.
3. Put the new key into every TradingView alert (and into Amibroker and your strategies, if you
   use them). From now on an alert that still carries the old key is refused.

A logout, a password change or a reset also ends the AI assistant's access at once (see "The AI
assistant" below): its consent was given by a session that has now ended.

If the answer to a logout, a password change or a reset says that your other devices could not be
signed out, the server could not record the time: log in and log out once more.

Tests: `sudarshan/alerts/tests/test_sessions_valid_after.py`, `test/test_gthread_sessions_valid_after.py`
and `frontend/src/sudarshan/signout.test.tsx`.

## nginx lines for the AI assistant (M19.3)

Do this only when you decide to use the AI assistant, before you turn Remote MCP on. Until then
nothing here is needed: with Remote MCP off, the assistant's addresses answer "not found".

nginx is the server's front door and is not SUDARSHAN's alone: other sites on this server may go
through it. So the deploy tool never touches nginx, SUDARSHAN never reads nginx's files, and these
lines are yours to install, once, into SUDARSHAN's own site and nowhere else. They let the
addresses an AI app calls be reached only from the range Anthropic publishes for its calls to
MCP servers (`160.79.104.0/21` on 14 Nov 2026; read Anthropic's page again before installing, and
whenever a connection that worked stops), leave the consent page open to your own browser, and
keep the health line for the server itself.

On the server, from the app folder (`cd /opt/sudarshan/apps/openalgo`):

1. Copy the two files into nginx's folders, owned by root. Never point nginx at the app folder
   itself: the service user can write there, and nginx runs with root's rights.

   ```
   sudo install -m 644 -o root -g root sudarshan/ops/nginx/sudarshan-mcp-zone.conf /etc/nginx/conf.d/sudarshan-mcp-zone.conf
   sudo install -m 644 -o root -g root sudarshan/ops/nginx/sudarshan-mcp.conf /etc/nginx/snippets/sudarshan-mcp.conf
   ```

2. Open SUDARSHAN's own site file in nginx (the one whose `server { }` block serves the app on
   port 443) and add one line inside that block, above its `location /` line. Add it to no other
   site:

   ```
   include snippets/sudarshan-mcp.conf;
   ```

3. Test, then reload. `nginx -t` reads every site's file and changes nothing; if it reports an
   error, undo step 2 and stop. Always `reload`, never `restart`: a reload keeps every open
   connection of every site.

   ```
   sudo nginx -t
   sudo systemctl reload nginx
   ```

4. Check that the lines are in effect, with the app's address as you type it in the browser. It
   asks nginx on the server itself, reads only whether each address is refused or let through,
   opens no nginx file and changes nothing:

   ```
   sudo -u sudarshan .venv/bin/python -m sudarshan.ops.nginx_check --host <the app's address>
   ```

   Each line starts with `ok`; the last says "The assistant's nginx lines are in effect." A line
   starting with `NO` names the address that is not as it should be.

After a release, compare the two files in the app folder with your copies
(`diff sudarshan/ops/nginx/sudarshan-mcp.conf /etc/nginx/snippets/sudarshan-mcp.conf`); the
changelog says when they changed. To take the lines out again, remove the `include` line, then
`sudo nginx -t` and `sudo systemctl reload nginx`.

If nginx serves more than one site on this server and SUDARSHAN's is not the first, add a line to
`/etc/sudarshan/watch.env` so the watch reads SUDARSHAN's own certificate and never another
site's: `SUDARSHAN_WATCH_CERT_HOST` followed by `=` and the app's name. (With a number for an address instead of a
name this cannot be chosen, and the watch reads the certificate nginx serves first.)

Tests: `sudarshan/ops/tests/test_ops_nginx.py`.

## The security audit (M19)

Three things look at the security of the install, each where it can see:

- **Before every release and after every rebase on upstream**, a Claude session runs the
  repository's `security-audit` skill on the release branch and reads its report, failures
  first. The report stays on that machine.
- **Once a month on GitHub** (`.github/workflows/sudarshan-security.yml`, the 3rd at 03:10 IST,
  and by hand from the Actions page) the same script runs on the default branch. This
  repository is public, so the run shows only whether the audit passed and the counts per sheet
  (`python -m sudarshan.ops.audit_counts`): the script's own output and its report are never
  published, and a failed run opens nothing. A red run means: ask a session to reproduce it
  privately with the skill.
- **On the server, every hour**, the watch's posture checks ("Watching the server") look at what
  GitHub cannot see: the real settings, the assistant's locks, the private folders, the ports.

## Secrets: where each one lives and what changing it breaks (M19.4)

Every secret the server holds, in one place. Nothing here is a value: this table says where a
value lives, who can read it and what you must redo when it changes. Change a secret because
something happened (a lost phone, a laptop given away, a session that may have been copied, a
log or a screenshot that showed it), not by the calendar.

| secret | where it lives | what changing it breaks | how to change it |
|---|---|---|---|
| OpenAlgo's `APP_KEY` and `API_KEY_PEPPER` | OpenAlgo's `.env` in the app folder | The pepper is the root of everything sealed: the owner vault (auto-login's TOTP key and PIN), every sealed route between the services, the stored Upstox token and the API key. A new pepper orphans all of them | **Do not change it.** It is restored with the archive it came from, never typed anew. If it ever must change, that is a planned evening with a support session |
| OpenAlgo API key | `db/openalgo.db`, sealed | Every TradingView alert, Amibroker and your own strategies carry it; the Telegram bot was linked with it. The SUDARSHAN services read the new one by themselves | API Key in the profile menu, Regenerate; then steps 2 and 3 of "Signing out every device, and a lost phone" |
| Your SUDARSHAN password and login code | `db/openalgo.db` (hashed; the code's key sealed) | Every device is signed out and the AI assistant is disconnected | Profile, Account, Change Password |
| Upstox app key and secret | OpenAlgo's `.env` (`BROKER_API_KEY`, `BROKER_API_SECRET`) | The daily Upstox login, until the new values are in place | Make new ones in Upstox's My Apps, put them where the old ones are, restart OpenAlgo, then "Test now" on the Auto login page |
| Auto-login's TOTP key and Upstox PIN | the owner vault, `db/sudarshan-vault/` (sealed with a key derived from the pepper) | Auto-login pauses until both are saved again | The Auto login page; they are never shown again once saved |
| Upstox Analytics Token | `/etc/sudarshan/upstox_analytics_token` (root-provisioned), and the family account's own copy in `/etc/sudarshan-family/feed-token` | The price feed, the Portfolio page and the family view. **It is shared with TRINETRA: making a new one in Upstox ends the old one for TRINETRA too.** Never make a new one casually | Only together with TRINETRA's operators: new token in Upstox, then the file here and TRINETRA's own copy by its own runbook |
| The owner's Telegram bot token | OpenAlgo's database, sealed (Telegram page) | All Telegram messages and commands until the new token is saved and the bot started | BotFather, `/revoke`; then the Telegram page in the app |
| The family bot's token and the family service's keys | `/etc/sudarshan-family/` (the family account only) | The family view's Telegram messages or sign-ins | "The family view's one-time set-up": run the set-up again from the laptop |
| The feed's client keys | `/etc/sudarshan/feed-clients/` (one file per service) | Nothing you see: a deleted key is made again, new, at the deploy tool's next run | Delete the file; wait for the next run |
| The backup certificate | public half in `/etc/sudarshan/offsite.json`; **the private key only on your laptop and on one offline copy** | Offsite copies made with the old certificate still open only with the old private key: keep it until those copies have expired | "Setting up the offsite copy", steps 6 to 9 |
| The offsite upload link | `/etc/sudarshan/offsite.json` (mode 600) | Offsite copies stop. It also expires by itself; you are told 15 days ahead | The same steps 4, 5 and 9 |
| The assistant's signing key | `keys/` in the app folder | Every AI connection ends; each must connect and be approved again | Stop OpenAlgo, move the key file away, start OpenAlgo (it makes a new one). Normally you never need to: "Ending its access, at once" does the same job |
| The outside heartbeat's link | `/etc/sudarshan/watch.env` (root:sudarshan, mode 640) | The heartbeat service stops hearing from the server and alerts you | Make a new check address in the heartbeat service and replace the line ("The outside heartbeat: the owner's steps") |
| The risk layer's confirm phrase | `db/sudarshan_risk.db` | Nothing; it is asked for at the next change | Settings > Risk; if forgotten, "The risk layer's confirm phrase, if it is forgotten" |

All of these except the private backup key are inside the nightly archive (`/etc/sudarshan`,
the `.env`, the databases, the vault), which is why an archive is as secret as the server.

## If the server is lost (M19.8)

For the day the server cannot be brought back: deleted, broken beyond repair, or taken over. Do
the steps in order. What you need before that day: the private backup key and its passphrase
(laptop and one offline copy), the login to the cloud console, and this README.

1. **A new server of the same shape, in the same cloud account, with the same reserved public
   address attached.** The address is what the phones' installed apps, every TradingView alert
   and Upstox's registered address all point at; with it kept, none of them changes. If the
   address cannot be kept, do "Changing the server's registered address" below as well.
2. **Install OpenAlgo and SUDARSHAN at the release the archive names** (`MANIFEST.json` inside
   the archive says which).
3. **Restore from the newest good offsite copy:** "Restoring from the offsite copy" above, then
   the manual steps under "Restoring". The vault and the `.env` of the same archive go back
   together.
4. **Issue the security certificate again** (a few minutes; the app's address is unchanged).
5. **Run the family set-up again** ("The family view's one-time set-up"): it makes the family
   account, its firewall rules and its files again; the keys come from the restored files.
6. **Start, then check:** `python -m sudarshan.ops.watch --check` (every line `fine` or `off`),
   "Test now" on the Auto login page, one test alert from TradingView in Analyze mode, and the
   heartbeat service showing the check up again.
7. **What is lost:** at most the day since the last archive of SUDARSHAN's own records. The
   journal reads the day's fills from Upstox again at its next reconcile; market alerts,
   watchlists and settings come back as of the archive. Your orders, positions and holdings are
   at Upstox and were never only here.
8. **Other systems on the server are not restored by any of this.** TRINETRA is restored by its
   own operators from its own runbook; nothing here touches it.

What has not been proven: an archive has not yet been restored onto a new server from end to end.
The restore drill of a later release proves an archive reads back; do one full restore onto a
spare server when you can.

## Changing the server's registered address (M19.9)

Since 1 April 2026 an order sent through the API is accepted only from an address registered with
the broker. Upstox lets you register a primary and a secondary address and change them **once in
a calendar week**, and says that a change ends the access tokens in use. The server's reserved
address is therefore something to keep (step 1 above). If it must change:

1. Pick a day the market is closed. Check that the registered address was not already changed
   this calendar week; if it was, API orders from the new address must wait for next week, and
   the Upstox app is your way to trade until then.
2. Ask Upstox support first whether the change also ends the Analytics Token. If it does,
   TRINETRA is affected and its operators must be ready the same evening.
3. Change the address in Upstox's My Apps.
4. Every Upstox login in use has ended: press "Test now" on the Auto login page and see it
   succeed. Then check that prices arrive (the feed uses the Analytics Token).
5. While the app's own address is this address, everything that names it changes too: the
   security certificate, every phone's installed app (remove and add again), every TradingView
   alert's webhook address, and `MCP_PUBLIC_URL` if the assistant is in use. A name for the server
   in place of the number makes this step disappear; that is worth settling before family phones
   install the app.
6. Note the date: the next change is possible from the next calendar week.

If a second reserved address costs nothing in your cloud account, registering it at Upstox ahead
of time as the secondary turns a forced move into no wait at all. Whether a secondary may sit
registered and unused for months is a question for Upstox support.

SUDARSHAN sends at most 5 orders a second (`UPSTOX_ORDER_MAX_PER_SECOND`), half of the 10 a
second above which an algo must be registered with the exchange.

## The Telegram bot's name and picture (M16.2)

Five minutes in Telegram, in your chat with BotFather, once. Nothing on the server changes.

1. Send `/mybots` and choose your SUDARSHAN bot.
2. Edit Bot, Edit Name: `सुदर्शन · SUDARSHAN`.
3. Edit Bot, Edit About: `Personal trading alerts. Private.` Edit Description: the same line.
   Do not put your name, the broker or the server's address in either.
4. Edit Bot, Edit Botpic: send the app's icon as a photo. It is `android-chrome-512x512.png` in
   the app's `frontend/public/images/` folder; the same picture is at
   `https://<your app's address>/images/android-chrome-512x512.png`.
5. Edit Bot, Edit Commands: send the list below as one message, so the bot's menu shows them.

```
status - Connection and mode
positions - Open positions
orderbook - Today's orders
tradebook - Today's trades
holdings - Holdings
funds - Funds
pnl - Today's P&L
quote - A quote: /quote SYMBOL EXCHANGE
chart - A chart: /chart SYMBOL EXCHANGE
killswitch - Stop new orders, or resume
menu - Buttons for the above
help - What the bot can do
```

`/closeall`, `/mode`, `/stoppython`, `/link` and `/unlink` work too and are left out of the menu
on purpose, so they are not one tap away. The bot answers only an account that was linked with
your API key, and `/killswitch` only in your private chat with it.

## Records to keep each year (M19.10)

SUDARSHAN's books are your working record. The statements Upstox issues are the record the
regulator and the tax office recognise: keep both. How many years the tax rules require is a
question for your CA; this list assumes 8 financial years.

Each April, for the year just ended:

| record | where it comes from | who keeps it |
|---|---|---|
| contract notes, ledger, tradebook, P&L statement and tax statement for the year | Upstox, by email and from its reports pages | you: download them and file them with the year |
| bank statements and every transfer to and from the broker | your bank, Upstox | you |
| the strategy behind each automated order: the TradingView alert's name and the Pine script as it was | TradingView; the order's strategy name in the journal | you: export the scripts with the year |
| every fill, paired into trades, with charges; the monthly statements and the tax pack | the journal (`journal.db`, the Reports page) | SUDARSHAN, for ever; download the tax pack and the year's statements |
| every order request and the broker's answer | OpenAlgo's `logs.db` | SUDARSHAN, for ever |
| what the risk layer refused, and the kill switch's history | `sudarshan_risk.db` (Settings > Risk > History) | SUDARSHAN, for ever |
| market alerts that fired | the alerts' history | SUDARSHAN |
| one full archive of the server from just after 31 March | the nightly backup: the first complete archive made once 31 March's market has closed, and its offsite copy under a name starting `sudarshan-fy-` ("Nightly backups") | SUDARSHAN, for 8 years on the server and in the bucket (once both delete rules carry the prefix filter of step 3); a copy with the year's papers costs nothing |
| installs, rollbacks, backup failures, restore drills, family sign-ins and every notice the watch told | the server diary, `/var/lib/sudarshan-watch/log/<year>.jsonl` ("Watching the server"), which the nightly backup takes | SUDARSHAN, for 8 years |

A later release adds one records pack a year (the journal's statements with the order, risk and
alert history as files).

## The AI assistant (M17)

SUDARSHAN's assistant is your own AI app connected to OpenAlgo's remote MCP, read-only. It ships
**switched off** and stays off until you turn Remote MCP on yourself (Admin, Remote MCP). The
tools SUDARSHAN adds for it come in a later release; what this release does is make sure the
assistant can never place, change or cancel an order, and that its access ends the moment you
want it to.

**It cannot be switched on with order tools (M17.9).** Every deploy writes three settings into
OpenAlgo's `.env` (the list in "OpenAlgo settings that ship with a release"):

| setting | value | what it does |
|---|---|---|
| `OPENALGO_MCP_READ_ONLY` | `1` | no tool that places, changes or cancels an order, switches the trading mode or sends a Telegram message exists in the MCP server at all |
| `MCP_OAUTH_WRITE_SCOPE_ENABLED` | `False` | the right to trade is never offered to an AI app and never granted |
| `MCP_OAUTH_REQUIRE_APPROVAL` | `True` | an AI app that registers itself waits for your approval on the Remote MCP page |

When SUDARSHAN starts with Remote MCP on, it checks the first two. If either would allow orders,
the app starts as usual **without** the assistant (its address answers "not found") and the log
says: "The assistant is switched off: its settings would allow orders. Set
OPENALGO_MCP_READ_ONLY=1 and MCP_OAUTH_WRITE_SCOPE_ENABLED=False." An AI setting never stops
trading. Under these locks nothing else changed: every order from any source still meets the risk
guards and the kill switch.

**Ending its access, at once.** Each of these ends every AI connection on its next request, not
after the 15 minutes its pass would otherwise last, and closes any connection it holds open:

- the kill switch, once the risk guard covers Live orders (while it checks Analyze mode only, it
  leaves the assistant alone, as its report says);
- the Remote MCP page's own kill switch ("Revoke all MCP tokens");
- a logout, a password change or a password reset.

Turning the kill switch off, or logging in again, brings nothing back: connect the assistant
again in your AI app and give consent again. To end one connection only, use Revoke beside it on
the Remote MCP page; that one can still work for up to 15 minutes.

**The Agent page** (`/agent`) is OpenAlgo's own chat, which needs a paid model key. SUDARSHAN
leaves it as it ships: not set up, with its trading switch off. The kill switch stops it too.

**From a laptop instead (M17.2).** OpenAlgo's MCP server can also run on a laptop
(`mcp/mcpserver.py`, started by Claude Desktop or Claude Code). Use it only if the connector
cannot be added, and then:

- set `OPENALGO_MCP_READ_ONLY=1` in that client's settings for the server. Without it the laptop
  is offered every tool, the order tools included;
- the API key you give it is an order-capable secret now sitting on that laptop: treat a lost or
  lent laptop as a lost phone ("Signing out every device, and a lost phone" above, steps 2 and 3);
- reach the server through SSH only, never by opening a port:
  `ssh -N -L 5000:127.0.0.1:5000 <you>@<server>`, with `http://127.0.0.1:5000` as the host.

Tests: `sudarshan/assistant/tests/`, `sudarshan/guard/tests/test_guard_migration.py`.
