# sudarshan/ — the ring around OpenAlgo

Everything सुदर्शन / SUDARSHAN adds lives here as **separate processes** (systemd units named
`sudarshan-<svc>.service`, all in `sudarshan.slice`), never inside the OpenAlgo app (eventlet,
single worker, no in-app schedulers). They talk to OpenAlgo over `http://127.0.0.1:5000/api/v1`
(the `openalgo` Python SDK) and `ws://127.0.0.1:8765`, and to Upstox directly only through the
read-only Analytics Token (market data, portfolio reads) — never with an order-capable token.

| dir | service | what it is |
|---|---|---|
| `feed/` | `sudarshan-feed` | The **single** Upstox Feed V3 WebSocket for all of SUDARSHAN (Basic tier = 2 per user, TRINETRA holds one). Subscribes once, keeps a tick snapshot, republishes over local ZMQ, records ticks to Parquet for replay, exposes `/snapshot` over HTTP. Also the REST rate governor (orders 10/s·500/min·2,000/30 min; standard 50/500/2,000) every other service must go through. |
| `alerts/` | `sudarshan-alerts` | Server-side alert engine (price, % move, volume, OI change/build-up, IV, indicator cross, Greeks, time, P&L, screener hit, feed health, rollover). Evaluates on the feed, not in a browser tab. Lifecycle: one-time / recurring, cooldown, snooze, expiry, history. Delivers to Telegram (owner bot via OpenAlgo `/api/v1/telegram/notify`, family bot separately) and Web Push later. |
| `screener/` | `sudarshan-screener` | Scheduled scans (1/5/15 min, EOD) over universes (NIFTY 50/100/200/500, F&O list, custom watchlists, option chains) with a rule builder (price action, indicators via TA-Lib, OI build-up classes, IV rank/percentile, PCR, basis, bhavcopy fields). Results feed **membership-table watchlists** (first_seen / last_seen / hits / exited / pinned / score) in OpenAlgo's watchlist DB and raise digest alerts; every run is stored for rewind and forward-return backtests. |
| `portfolio/` | `sudarshan-portfolio` | Upstox tab (holdings, positions, MTF, funds tree, trade history, trade-wise P&L + charges, GTT, MF, portfolio stream) on the Analytics Token + daily snapshots, dividends from corporate actions, sector tags; and the **custom manual portfolio engine**: activity model (BUY/SELL/DIVIDEND/FEE/INTEREST/TRANSFER/SPLIT/BONUS), any asset, tags, imports (CSV/tradebook), FIFO lots, STCG/LTCG (ITA 2025 sections, "verify with CA"), XIRR/TWR, allocation, goals, custom tab layouts. |
| `family/` | `sudarshan-family` | View-only layer for family members: own users/sessions/TOTP, privacy mode, invitations, per-user watchlists/alerts, audit log, viewer UI variants, family Telegram bot. Backed by the Analytics-Token data plane only; every OpenAlgo write/admin/bot route must answer 401/403 to a viewer. |
| `ops/` | `sudarshan-ops` (timers) | Daily-rollover guard (03:35 / 08:45 checks, TOTP token sidecar), backups to OCI Object Storage, health checks (healthchecks.io), EOD file jobs (bhavcopy, index constituents — once a day, outside the trading core), reports/digests (08:45 brief, 15:45 EOD, weekly, FY), kill switch. |

Conventions: one package per service (`sudarshan/<svc>/`), `pyproject`-managed with the repo's `uv`
environment, tests in `sudarshan/<svc>/tests/`, unit files in `sudarshan/ops/systemd/`, config in
`/etc/sudarshan/<svc>.env` (root:sudarshan 640; never in the repo). Feature ids from the
feature list (e.g. `M9.4`) go in PR titles and in `sudarshan/STATUS.md`.
