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
| `family/` | `sudarshan-family` | View-only layer for family members: own users/sessions/TOTP, privacy mode, invitations, per-user watchlists/alerts, audit log, viewer UI variants, family Telegram bot. Backed by the Analytics-Token data plane only; every OpenAlgo write/admin/bot route must answer 401/403 to a viewer. |
| `autologin/` | `sudarshan-autologin` (oneshot + timer) | The daily Upstox login with TOTP + PIN (Upstox's TOTP token API) at 08:35/08:45/08:55 IST on trading days, and "Test now" from the Auto login page. Fetches the token, then hands it to OpenAlgo, which stores it exactly as a manual login does (its in-memory caches only refresh in its own process); never ends a login Upstox still accepts, and pauses after any refusal until the key or PIN is saved again. The one ring process that receives an order-capable token: it passes it to OpenAlgo and never uses it. Result on the page and in Telegram. |
| `vault/` | (library) | Owner vault `db/sudarshan-vault/sudarshan_vault.db` (directory 700, file 600; the auto-login unit may write only there): small secrets entered on the phone, Fernet under a key derived (HKDF) from OpenAlgo's pepper; write-only over HTTP. Also holds the auto-login status. |
| `ops/deploy/` | `sudarshan-deploy` (root, timer) | The server's deploy tool: installs the latest release outside market hours (backup, checksum, migrations, the `.env` settings below, health check, rollback), keeps the NSE day files in step with the release's calendar lines (below), records what it did for the owner's Telegram (below) and updates itself from each release. |
| `ops/` | `sudarshan-ops` (timers) | Daily-rollover guard (03:35 / 08:45 checks, TOTP token sidecar), backups to OCI Object Storage, health checks (healthchecks.io), EOD file jobs (bhavcopy, index constituents — once a day, outside the trading core), reports/digests (08:45 brief, 15:45 EOD, weekly, FY), kill switch. |

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
| 5586 | `sudarshan-journal` (P7, not built yet): its sealed local API and `GET /health` |
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
| any other client | - | - | 50 |
| the ring in all | 732 at worst | 1,212 | 1,400 |

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
later `portfolio.db` and `screener.db`), not in OpenAlgo's `db/` folder, so the deploy tool treats
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
archives cannot push the last good one out. Then a size cap (10 GB for all of them):

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
   Each has Lifecycle action Delete, Number of days 30 (or what you like, the same in both), and
   the State switch on:
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

## Server notices on Telegram (M18.x)

After an install, a rollback, a service file the unit policy refused, a release that waits for
the market to close, or a nightly backup that failed, the owner gets one short message on Telegram
in trader language (examples in `sudarshan/alerts/README.md`). The deploy tool runs as root, so it
holds no Telegram or OpenAlgo secret and cannot be made to send anything:

- it only **records** the fact, one JSON line, in `/var/lib/sudarshan/events.jsonl` (mode 644, the
  folder made traversable; `ops/events.py` has the format and `ops/files.py` the safe reader);
  `sudarshan-alerts` reads it, words it, and sends it once per event id (`alerts/notices.py`);
- the nightly backup runs as `sudarshan` and cannot write root's file, so it records its failures
  the same way in `/var/lib/sudarshan-backup/events.jsonl` (`ops/events.py` `record()`); both
  files are read;
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

If the answer to a logout, a password change or a reset says that your other devices could not be
signed out, the server could not record the time: log in and log out once more.

Tests: `sudarshan/alerts/tests/test_sessions_valid_after.py`, `test/test_gthread_sessions_valid_after.py`
and `frontend/src/sudarshan/signout.test.tsx`.
