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
   `theme-color #FFFFFF` (the app bar is white; the manifest says the same); the theme switcher
   is removed/hidden, not left as a dead control.
2. Name as **"सुदर्शन"** (Devanagari) **and "SUDARSHAN"** in every main place: `<title>`, PWA name,
   login, app-bar lockup, about, notifications, Telegram bot name, report headers.
3. Portfolio: the Upstox portfolio shown separately (read-only, Analytics Token) **plus** fully
   manual, fully customizable add-on portfolio tabs with a live "current status" view.
4. Multiple watchlists (built-in presets + user-created).
5. Screener: fully customizable; every run can auto-populate a chosen watchlist (membership table:
   first_seen / last_seen / exited / pinned) and raise alerts.
6. Colour-code everything useful; **never colour-only** (always an icon, label or sign too).
   Readable on a phone in outdoor sunlight.
7. **Real-time everywhere, tick by tick** (owner, 20 Oct 2026: "without it SUDARSHAN is of no
   use").
   - What updates live: every price, **chart** (the forming candle updates on every tick, and a
     new candle opens on time), P&L, position, holding value, order and trade status, watchlist,
     screener result and alert, from the live feed as ticks or events arrive. Charts are a must
     (owner, 20 Oct 2026), including a phone-first live chart screen.
   - Timed polling of market data is not acceptable. REST is only for the first snapshot, a
     periodic re-sync and data that is not market data. During a feed outage, upstream's slow
     REST fallback may fill the gap. The screen then says "Reconnecting" or "Delayed" with the
     age of the figures, never "Real-time".
   - Words on screen:
     - "Real-time" means ticks are arriving.
     - "LIVE" stays the trading mode chip (orders go to Upstox), as opposed to ANALYZE (sandbox).
     - Never use "Live" for the feed.
   - When a screen cannot be live (market closed, feed down, no broker login), it says so with a
     stale or closed marker and a label. It never shows old numbers as current.
   - New SUDARSHAN features are built live from day one.
   - The benchmark is the owner's broker apps: Kite, Dhan, Upstox and Fyers (owner, 20 Oct 2026).
     - Prices, P&L and depth move with every tick the broker sends.
     - A fill, rejection or broker square-off shows at once, in the app and on Telegram.
     - Screens resume within a second or two after the phone wakes, with a visible
       "reconnecting" state, never silently old numbers.
     - Candles open on time.
     - SUDARSHAN adds no delay of its own to what Upstox delivers.

## Design tokens: the D look (light only; text tokens ≥ 4.5:1 on the #F4F6F8 page)
Source of truth: `frontend/src/sudarshan/theme.css`; `theme.test.ts` checks every value below.

| token | hex | use |
|---|---|---|
| `--bg-canvas` | #F4F6F8 | page background, under white cards |
| `--surface-card` | #FFFFFF | cards, app bar, bottom nav (1 px border #E3E7EC, shadow `0 1px 2px rgba(16,24,40,.05)`, radius 14-18) |
| `--divider` | #EDF0F3 | rules inside a card |
| `--border-strong` / `--input` | #808A99 | outlines that must be seen (≥ 3:1) |
| `--text-primary` | #0F1A24 | ink: body, numbers |
| `--text-secondary` | #475569 | ink-2: labels |
| `--text-tertiary` | #5B6676 | ink-3: meta lines |
| `--brand` | #0E5A73 | Mor (peacock): primary actions (white text), links; hover #0A4558, tint #E4F1F4 |
| `--gold` | #E3A21A | decoration only, never text on white; tint #FDF3DC, ink on tint #8A5A00 |
| `--profit` / `--up` | #067A45 | profit, up, COMPLETE; tint #E6F5ED |
| `--live-*` | ink #05683A on #E6F5ED, edge #A7D9BE | LIVE chip |
| `--loss` / `--down` | #C3282E | loss, down, REJECTED; tint #FCEBEC |
| `--buy` / `--buy-solid` | #1C65C8 | BUY, LONG (`--buy` is an HSL triplet as upstream writes it; use `--buy-solid`, since `hsl(var(...))` is banned) |
| `--sell` / `--sell-solid` | #D02F14 | SELL, SHORT (same pairing) |
| `--family` | #3949AB | FAMILY · VIEW ONLY badge (with lock icon) |
| `--sandbox` | #7F36BC | ANALYZE mode: light tint banner (#F2E8FF, ink #522D74), not a purple page |
| `--warning-text` | #8A5A00 | OPEN / PENDING text; fill #E3A21A takes ink text only |
| `--info` | #006A9D | informational |
| `--up-cvd` / `--down-cvd` | #1C65C8 / #AC5700 | `<html data-cvd="1">` colour-blind mode |
| `--down-cvd-tint` | #FDF1E5 | colour-blind down chips (`--down-cvd` on it 4.59:1) |

Fonts (self-hosted WOFF2 via @fontsource, SIL OFL; CSP `font-src 'self'`): **Barlow** 400-700
for all UI and body text, `tabular-nums` on the whole page (its tabular set swaps digits only);
**Khand** 500-700 for screen titles and single big figures (P&L, prices; Khand has no tabular
figures, so never in a column); Latin subsets only, the ₹ sign and Indic text fall back to
**Noto Sans Devanagari / Gujarati**. The wordmark सुदर्शन is **Tiro Devanagari Sanskrit** drawn
as outlines (`frontend/src/sudarshan/marks.ts`), SUDARSHAN beside it in Khand 600, tracked 0.4em,
in brand. Icon "Mor": tile #0E5A73, ring #E3A21A, glyph #FFFFFF. Numbers via
`Intl.NumberFormat('en-IN')`. Touch targets ≥ 48 dp. Minimal motion.

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
- Upstox WebSocket budget. The owner is on **Plus: 5 connections per user**. Stale sockets count
  until they time out.
  - TRINETRA: 1 (Analytics Token).
  - `sudarshan-feed`: 1 (Analytics Token). It subscribes once and fans out to every ring service;
    ring services never open their own socket.
  - OpenAlgo's own WebSocket proxy (daily access token): at most 2. The owner's live screens,
    /trading and the tools share one pooled connection per user.
  - Always keep one slot free for reconnects.
- Feature flags default **OFF**; order-path changes ship behind a flag and pass Analyzer-mode tests.
  - A new screen that only opens the existing order ticket (`PlaceOrderDialog`) or calls the
    page's existing order handlers (exit, exit all, cancel, cancel all, modify), with no new
    order code, is a new entry point, not an order-path change. It ships without a flag and
    needs one Analyzer-mode use of each action from that screen after deploy.
  - A destructive action (exit, exit all, cancel, cancel all) asks first, sends once however it
    is tapped, and stays "sent" (`books/sent.ts`, app-wide, kept across a reload) until the
    book shows the position flat, gone or flipped (an exit) or the order final (a cancel). A
    part close keeps the hold. No answer at all holds up to 150 s from the tap (one Upstox call
    can take 120 s); an answer the app could not confirm holds 15 s after the answer or the
    last change; the exit's own open order (pinned by the order id in the answer) holds until
    a book read settles it. After that it asks again, saying what is known about the first.
    The server keeps exits for one position to one at a time from every surface
    (`sudarshan/books/exit_guard.py`): a claim while an exit is being placed and a 15 s note
    after it, for the app's Exit, a smart order to position size 0 (TradingView, scripts;
    claimed before its positions read) and Flow's close; an overlapping one gets 409 with a
    sentence, and an entry is never refused. Every Exit all takes one claim for the whole book
    in `close_position_with_auth`, reads positions and the order book once, and leaves out,
    with a sentence each, any position that is busy or has a working order on its closing
    side; it never waits. Anything that may have reached the broker reads "not confirmed",
    never a refusal or raw error text. A stranded hold costs a retry; a released one can
    reverse a position.
  - A sheet, a confirmation or an order ticket ignores taps for 500 ms after it opens
    (`watchlist/openGuard.ts`), so a double tap never presses what appeared under the finger.
  - Order alerts on Telegram (M6.8) are on by default (owner's broker-app benchmark, 20 Oct
    2026).
- Fields added to what a broker's order or position book returns are checked against every
  reader of `/api/v1/orderbook`, `/orderstatus` and `/positionbook` before they ship (the
  strategy module folds `/orderstatus` into its legs), get golden vectors built from the
  broker's raw rows, and are compared with the live book on the first trading session after
  deploy. A field that changes what a reader decides is an order-path change. Example: Upstox's
  `filled_quantity` stays out of the order book until `/orderstatus` keeps a part fill's average
  price (it sets 0.0 for any order that is not complete, so the engine would book an unpriced
  fill).
- The order ticket opens with `productPref.ticketProduct`, one rule for every screen: the
  product last picked for the segment on this phone, else Intraday (MIS) for derivatives and
  Delivery (CNC) for cash. An order on something already held uses its product instead: Add
  and Exit on a position use the position's product; Buy more (from one share) and Sell (the
  whole holding) on a holding use CNC.
- Family layer: every write namespace, admin route and bot command must return 401/403 for a viewer
  session (automated test). The family data plane runs on the read-only Analytics Token only.

## Upstox facts (verified 19 Oct 2026)
- REST limits per API per user: orders 10/s, 500/min, 2,000/30 min; standard 50/s, 500/min,
  2,000/30 min. Quote/LTP/OHLC V3: max 500 instrument keys per call.
- Feed V3 modes ltpc / full / option_greeks / full_d30; caps per connection ltpc 5,000, full 2,000,
  greeks 3,000; stale sockets count until timeout (fresh authorize URI on reconnect).
- Daily access token expires 03:30 IST; `POST /v2/totp-login/authorization/token` (x-api-key + totp
  + pin) can replace the browser login. SUDARSHAN does this as auto-login (M2.20).
  - The owner saves the TOTP key and PIN himself on the Auto login page.
  - They are sealed in the owner vault (`sudarshan/vault`, stored in `db/sudarshan-vault/` with
    mode 700/600, key derived from OpenAlgo's pepper).
  - They are never shown, logged or sent anywhere but Upstox.
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
- Secrets live in one of two places. Owner decision, 20 Oct 2026: whichever is hassle-free for
  him.
  - `/etc/sudarshan`, root-provisioned, for machine keys such as the Analytics Token.
  - The sealed owner vault, for anything the owner types into a SUDARSHAN page.
  - Ring services may read OpenAlgo's own stored credentials (for example its API key)
    read-only, through OpenAlgo's functions, and never copy them anywhere else.
- Releases are published **by the owner** (Claude sessions cannot create releases or tags): send
  him the pre-filled link `https://github.com/<owner>/sudarshan/releases/new?tag=vA.B.C&target=sudarshan&title=SUDARSHAN%20vA.B.C`
  and he taps Publish. `sudarshan-build.yml` then builds `frontend-dist.tar.gz` (+ `.sha256`),
  attaches them and marks the release latest (a failed build is demoted to pre-release). The
  server's `sudarshan-deploy.timer` installs the latest release only outside 08:30–16:00 IST on
  trading days and rolls back on a failed health check.
- Before finishing a task: run the relevant tests, `npm run lint` for frontend changes, and update
  the feature's row in `sudarshan/STATUS.md` (M-id → done/partial + PR).
