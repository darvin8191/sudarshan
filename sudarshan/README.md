# sudarshan/ — the ring around OpenAlgo

Everything सुदर्शन / SUDARSHAN adds lives here as **separate processes** (systemd units named
`sudarshan-<svc>.service`, all in `sudarshan.slice`), never inside the OpenAlgo app (eventlet,
single worker, no in-app schedulers). They talk to OpenAlgo over `http://127.0.0.1:5000/api/v1`
(the `openalgo` Python SDK) and `ws://127.0.0.1:8765`, and to Upstox directly only through the
read-only Analytics Token (market data, portfolio reads) — never with an order-capable token.

| dir | service | what it is |
|---|---|---|
| `feed/` | `sudarshan-feed` | The **single** Upstox Feed V3 WebSocket for all of SUDARSHAN (Basic tier = 2 per user, TRINETRA holds one). Subscribes once, keeps a tick snapshot, republishes over local ZMQ, records ticks (gzip JSON lines) for replay, exposes `/snapshot` over HTTP. Also the REST rate governor (orders 10/s·500/min·2,000/30 min; standard 50/500/2,000) every other service must go through. |
| `alerts/` | `sudarshan-alerts` | **Today (M6.8): order alerts.** Fills, part fills, rejections (with the broker's reason), cancellations and stop-order triggers from OpenAlgo's order stream (`ws://127.0.0.1:8765`, `subscribe_orders`) to the owner's Telegram through OpenAlgo `/api/v1/telegram/notify`; once only (claims in `/var/lib/sudarshan-alerts/alerts.db`), the order book read again after any pause, bursts merged, trading days 08:45-16:00 IST; `SUDARSHAN_ORDER_ALERTS=off` turns it off. **Planned:** the server-side alert engine (price, % move, volume, OI change/build-up, IV, indicator cross, Greeks, time, P&L, screener hit, feed health, rollover), evaluated on the feed, not in a browser tab, with one-time / recurring, cooldown, snooze, expiry and history; the family bot separately, Web Push later. |
| `screener/` | `sudarshan-screener` | Scheduled scans (1/5/15 min, EOD) over universes (NIFTY 50/100/200/500, F&O list, custom watchlists, option chains) with a rule builder (price action, indicators via TA-Lib, OI build-up classes, IV rank/percentile, PCR, basis, bhavcopy fields). Results feed **membership-table watchlists** (first_seen / last_seen / hits / exited / pinned / score) in OpenAlgo's watchlist DB and raise digest alerts; every run is stored for rewind and forward-return backtests. |
| `portfolio/` | `sudarshan-portfolio` | Upstox tab (holdings, positions, MTF, funds tree, trade history, trade-wise P&L + charges, GTT, MF, portfolio stream) on the Analytics Token + daily snapshots, dividends from corporate actions, sector tags; and the **custom manual portfolio engine**: activity model (BUY/SELL/DIVIDEND/FEE/INTEREST/TRANSFER/SPLIT/BONUS), any asset, tags, imports (CSV/tradebook), FIFO lots, STCG/LTCG (ITA 2025 sections, "verify with CA"), XIRR/TWR, allocation, goals, custom tab layouts. |
| `family/` | `sudarshan-family` | View-only layer for family members: own users/sessions/TOTP, privacy mode, invitations, per-user watchlists/alerts, audit log, viewer UI variants, family Telegram bot. Backed by the Analytics-Token data plane only; every OpenAlgo write/admin/bot route must answer 401/403 to a viewer. |
| `autologin/` | `sudarshan-autologin` (oneshot + timer) | The daily Upstox login with TOTP + PIN (Upstox's TOTP token API) at 08:35/08:45/08:55 IST on trading days, and "Test now" from the Auto login page. Fetches the token, then hands it to OpenAlgo, which stores it exactly as a manual login does (its in-memory caches only refresh in its own process); never ends a login Upstox still accepts, and pauses after any refusal until the key or PIN is saved again. The one ring process that receives an order-capable token: it passes it to OpenAlgo and never uses it. Result on the page and in Telegram. |
| `vault/` | (library) | Owner vault `db/sudarshan-vault/sudarshan_vault.db` (directory 700, file 600; the auto-login unit may write only there): small secrets entered on the phone, Fernet under a key derived (HKDF) from OpenAlgo's pepper; write-only over HTTP. Also holds the auto-login status. |
| `ops/deploy/` | `sudarshan-deploy` (root, timer) | The server's deploy tool: installs the latest release outside market hours (backup, checksum, migrations, the `.env` settings below, health check, rollback), keeps the NSE day files in step with the release's calendar lines (below) and updates itself from each release. |
| `ops/` | `sudarshan-ops` (timers) | Daily-rollover guard (03:35 / 08:45 checks, TOTP token sidecar), backups to OCI Object Storage, health checks (healthchecks.io), EOD file jobs (bhavcopy, index constituents — once a day, outside the trading core), reports/digests (08:45 brief, 15:45 EOD, weekly, FY), kill switch. |

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
