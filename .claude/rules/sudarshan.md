# सुदर्शन / SUDARSHAN — rules for this fork (branch `sudarshan`)

These rules add to the upstream OpenAlgo `CLAUDE.md` (read that too). Where the two differ, **these
rules win** for work on this fork.

SUDARSHAN is a personal + immediate-family trading platform built on OpenAlgo: broker **Upstox**,
one Oracle ARM64 server (2 OCPU / 12 GB) shared with the owner's other trading system **TRINETRA**
(never stop, restart, edit or read TRINETRA services or files). Android-only clients (Chrome PWA +
Telegram). Budget ₹0 beyond a TradingView plan. Only the owner trades; **family members are
view-only and never hold an order-capable credential.**

**This repository is public.** Never commit server IP addresses, account ids, names of family
members, tokens, keys, `.env` content, logs or the private specs. The specs — `feature-list.md`
(20 modules, ~310 features M1.1 … M20.x tagged Reuse / Configure / Extend / Build-new + phase) and
`delivery-plan.md` (scenarios A → B → C, gates G1–G5) — live outside the repo (server:
`~/sudarshan-specs/`). Ask for them if a task needs them and they are not in the workspace.

## Binding product rules (from the owner)
1. **Light theme only.** No `.dark` styles in new code; `color-scheme: only light`;
   `theme-color #F8F7F3`; the theme switcher is removed/hidden, not left as a dead control.
2. Name as **"सुदर्शन"** (Devanagari) **and "SUDARSHAN"** in every main place: `<title>`, PWA name,
   login, app-bar lockup, about, notifications, Telegram bot name, report headers.
3. Portfolio: the Upstox portfolio shown separately (read-only, Analytics Token) **plus** fully
   manual, fully customizable add-on portfolio tabs with a live "current status" view.
4. Multiple watchlists (built-in presets + user-created).
5. Screener: fully customizable; every run can auto-populate a chosen watchlist (membership table:
   first_seen / last_seen / exited / pinned) and raise alerts.
6. Colour-code everything useful; **never colour-only** (always an icon, label or sign too).
   Readable on a phone in outdoor sunlight.

## Design tokens (light canvas; text tokens ≥ 4.5:1 on #F8F7F3)
| token | hex | use |
|---|---|---|
| `--bg-canvas` | #F8F7F3 | page background (never #FFFFFF) |
| `--surface-card` | #FDFCF9 | cards (1 px border #DAD7D0) |
| `--text-primary` | #1E212B | body, numbers |
| `--text-secondary` | #515561 | labels |
| `--profit` / `--up` | #007E3F | profit, up, COMPLETE, LIVE |
| `--loss` / `--down` | #C9302D | loss, down, REJECTED |
| `--buy` | #1C65C8 | BUY, LONG |
| `--sell` | #D02F14 | SELL, SHORT |
| `--brand` | #284CAC | primary actions, links |
| `--family` | #007272 | FAMILY · VIEW ONLY badge (with lock icon) |
| `--sandbox` | #7F36BC | ANALYZE mode: light tint banner (#F2E8FF), not a purple page |
| `--warning-text` | #8A5600 | OPEN / PENDING text; fill #E8A127 takes ink text only |
| `--info` | #006A9D | informational |
| `--up-cvd` / `--down-cvd` | #1C65C8 / #AC5700 | `<html data-cvd="1">` colour-blind mode |

Fonts (self-hosted WOFF2, SIL OFL; CSP `font-src 'self'`): Inter (tabular numerals) + Noto Sans
Devanagari + Noto Sans Gujarati; wordmark Tiro Devanagari Sanskrit. Numbers via
`Intl.NumberFormat('en-IN')` + `tabular-nums`. Touch targets ≥ 48 dp. Minimal motion.

## Architecture rules
- OpenAlgo core stays upstream-shaped (small, well-marked edits only). **Everything new lives in
  `sudarshan/`** as separate systemd services (`sudarshan-feed`, `sudarshan-alerts`,
  `sudarshan-screener`, `sudarshan-portfolio`, `sudarshan-family`, `sudarshan-ops`) talking to
  OpenAlgo through the REST API / `openalgo` SDK at `http://127.0.0.1:5000` and the WebSocket proxy
  `ws://127.0.0.1:8765`.
- **No new asyncio loops, APScheduler jobs or background threads inside the OpenAlgo app**
  (gunicorn single worker + eventlet). Schedulers = systemd timers or the services above.
- New React page = **three registrations**: route in `frontend/src/App.tsx`, known path in
  `blueprints/react_app.py`, entry in `frontend/src/config/navigation.ts`. Missing one → 404.
- `frontend/dist` for deploys is built by CI (`sudarshan-build.yml`) and shipped as a Release asset.
  Never hand-edit `dist/` and never commit a local build.
- **Never run `install/update.sh`** (it stashes local changes and pulls upstream main) and never use
  GitHub's "Sync fork". Upgrades = rebase `sudarshan` on a tested upstream commit, then
  `upgrade/migrate_all.py`.
- One Upstox WebSocket only for SUDARSHAN services (Basic tier: 2 per user and TRINETRA holds one;
  Plus tier: 5). The feed service subscribes once and fans out; nothing else opens a socket.
- Feature flags default **OFF**; order-path changes ship behind a flag and pass Analyzer-mode tests.
- Family layer: every write namespace, admin route and bot command must return 401/403 for a viewer
  session (automated test). The family data plane runs on the read-only Analytics Token only.

## Upstox facts (verified 19 Oct 2026)
- REST limits per API per user: orders 10/s, 500/min, 2,000/30 min; standard 50/s, 500/min,
  2,000/30 min. Quote/LTP/OHLC V3: max 500 instrument keys per call.
- Feed V3 modes ltpc / full / option_greeks / full_d30; caps per connection ltpc 5,000, full 2,000,
  greeks 3,000; stale sockets count until timeout (fresh authorize URI on reconnect).
- Daily access token expires 03:30 IST; `POST /v2/totp-login/authorization/token` (x-api-key + totp
  + pin) can replace the browser login (TOTP sidecar; secrets only in `/etc/sudarshan`).
- Market Price Protection: `market_protection` -1 auto (default), 1–25 custom, 0 rejected.
- GTT: F&O options allowed (NRML, limit); OpenAlgo's "OCO" mapping opens a position — never use it
  to protect an existing one. Lot sizes/freeze come from the master (NIFTY 65, BANKNIFTY 30).
- SEBI/NSE since 1 Apr 2026: orders only from the registered static IP, daily 2FA login, ≤ 10
  orders/s without registration, market orders price-protected. Static IP changes once a week.

## Engineering conventions
- Python ≥ 3.12 with `uv`; frontend React 19 + Vite + Tailwind 4 + shadcn (new-york), Biome;
  tests: `pytest` (`sudarshan/<svc>/tests/`), `vitest`.
- One feature per PR, ≤ 300 changed lines where possible, title starts with the feature id
  (`M8.3: watchlist groups`). Order-path code (`sudarshan/feed`, anything touching
  `/api/v1/*order*`) needs unit tests + golden vectors + one Analyzer-mode session before merge.
- Secrets never in the repo: `.env`, `keys/`, `*.db`, `/etc/sudarshan/*` stay ignored/outside.
  Never print tokens, keys, PINs or TOTP secrets in logs, tests, commits or chat.
- Releases are tags `vA.B.C` on `sudarshan`. CI builds `frontend-dist.tar.gz` (+ `.sha256`); the
  server's `sudarshan-deploy.timer` installs the latest Release only outside 09:00–15:45 IST on
  trading days and rolls back on a failed health check.
- Before finishing a task: run the relevant tests, `npm run lint` for frontend changes, and update
  the feature's row in `sudarshan/STATUS.md` (M-id → done/partial + PR).
