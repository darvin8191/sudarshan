# sudarshan-feed (M18.1 / M18.8 / M13.1)

The **single** Upstox Market Data Feed V3 WebSocket for all of सुदर्शन / SUDARSHAN. It uses the
read-only Analytics Token, subscribes once, and fans the ticks out on 127.0.0.1 to the other
SUDARSHAN services (alerts, watchlists, screener, portfolio). Nothing else in SUDARSHAN opens an
Upstox socket: the Basic plan allows 2 sockets per user and TRINETRA holds one (Plus allows 5).

```
Upstox Feed V3 (wss, 1 socket) -> sudarshan-feed -> ZMQ PUB 127.0.0.1:5581  (ticks + __health__)
                                                 -> HTTP    127.0.0.1:5580  (/health, /snapshot, /subscribe,
                                                                /upstox/...: the REST governor)
                                                 -> $STATE_DIR: snapshot.json, subscriptions.json,
                                                                ticks/YYYY-MM-DD.jsonl.gz
```

A standalone asyncio process (not inside the OpenAlgo app, so the eventlet rules do not apply).
It only reads one file from the OpenAlgo tree: the generated protobuf module
`broker/upstox/streaming/MarketDataFeedV3_pb2.py`. No new dependencies: websockets, httpx,
protobuf and pyzmq are already in `requirements-nginx.txt`.

## Run

On the server (once):

```bash
sudo install -m 644 sudarshan/ops/systemd/sudarshan-feed.service /etc/systemd/system/
sudoedit /etc/sudarshan/upstox_analytics_token     # the token on one line; never echo it in a shell
sudo chown root:sudarshan /etc/sudarshan/upstox_analytics_token && sudo chmod 640 /etc/sudarshan/upstox_analytics_token
sudo systemctl daemon-reload && sudo systemctl enable --now sudarshan-feed
journalctl -u sudarshan-feed -f
```

`/etc/sudarshan/nse_holidays.txt`: one `YYYY-MM-DD` per line from the NSE holiday circular,
`#` comments allowed, also after the date. `sudarshan-deploy` adds the dates a release lists (see
`sudarshan/README.md`). A missing file means only weekends count as closed (logged, and shown in
/health). A special session listed in `nse_extra_trading_days.txt` (below) opens the feed that day,
also on a weekend or a holiday.

`/etc/sudarshan/nse_extra_trading_days.txt` (optional; auto-login, the alerts and the deploy tool
read it too): a day the exchange opens although it is a weekend or a holiday, such as a Muhurat
session. Same format, the date first on its line, then the session's own window from the
exchange's circulars: from the start of its pre-open to its F&O close. Give the window whenever
they state one. For the Muhurat session on Sunday 8 Nov 2026 (NSE circular 149/2026 for the cash
market: pre-open from 14:05, continuous trading from 14:20; circular 142/2026 for equity
derivatives: to 15:40):

```
2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)
```

That day the feed holds its socket from 14:03 to 15:50 (the usual margins, below) and the stall
watchdog runs from 14:20 to 15:40; the alerts keep their own margins, 13:50-16:00. A date with
no window (or one that cannot be read, which the journal reports) keeps the usual hours, and on a
weekend or a holiday the watchdog then waits for the day's first trade: a closed market is quiet,
and an afternoon session must not cost hours of reconnects before it starts.

Every reader agrees with the deploy tool byte for byte: a byte-order mark is fine, words are
separated by spaces or tabs, `#` starts a comment, a date is `YYYY-MM-DD`, and a stray byte spoils
only its own line. A missing file means no extra days (logged once). Neither file needs a restart:
each is read again when it changes, and every read is logged in one line.

By hand from the repo root (a dev box, outside market hours):

```bash
SUDARSHAN_FEED_STATE_DIR=/tmp/feed-state SUDARSHAN_FEED_ALWAYS_ON=1 \
SUDARSHAN_UPSTOX_TOKEN_FILE=$HOME/.upstox_token .venv/bin/python -m sudarshan.feed
```

On the server, `sudarshan-deploy` installs `sudarshan/ops/systemd/sudarshan-feed.service` from each
release (after its unit policy check), stops the feed together with OpenAlgo during an update and
restarts it afterwards - always outside market hours. By hand: `sudo systemctl restart sudarshan-feed`
(allowed for the dev user), also outside market hours.

## HTTP API: 127.0.0.1:5580

Local clients only (bound to loopback; other peers and browser requests carrying an `Origin`
header get 403). JSON in and out; use `127.0.0.1`, not `localhost` (which may resolve to ::1).

| request | answer |
|---|---|
| `GET /health` | state, detail, connected_since, socket_age_s, last_message_age_s, next_retry_in_s, token_file_readable, subscribed (counts by mode), keys_without_data, reconnects, last_error (+ hint), last_disconnect, market_window (below), market_status, cas_status (below), stats, recorder, rest (the governor, below) |
| `GET /snapshot?keys=K1,K2` | `{"ticks": {key: tick or null}}`; URL-encode the keys (`|` is `%7C`, space `%20`) |
| `GET /snapshot/all` | every key the service has a tick for (survives restarts and after-hours) |
| `GET /subscriptions` | the desired set by mode |
| `GET /upstox/<Upstox path>` | the REST governor (M18.9, below): Upstox's market-data reads for the other SUDARSHAN services |
| `POST /subscribe` `{"mode": "ltpc", "keys": [...]}` | 200 with counts; 400 bad mode or key; 409 over an Upstox cap |
| `POST /unsubscribe` `{"keys": [...]}` | 200 with counts |

`last_error` and `last_disconnect` describe the most recent such event and stay after recovery
(history, not current health): `state` says what is happening now, and comparing their `at` with
`connected_since` tells whether they came before the current socket.

`market_window` says whether the socket may be held now and why, the feed's hours today (`null`
on a closed day), what the two calendar files gave (`missing`, `not readable`, or a count) and
the next opening. On the Muhurat Sunday above, in the morning:

```json
{"open": false,
 "reason": "before the market window: extra trading day, own window 14:05-15:40, feed 14:03-15:50",
 "hours": "14:03-15:50", "holidays": "16 dates", "extra_days": "1 date, 1 own window",
 "next_open": "2026-11-08T14:03:00+05:30"}
```

A reopened day with no window of its own reads `market window: extra trading day, usual hours
08:58-15:50, watchdog after the first trade`. A day that trades anyway and has a window of its own
keeps its usual hours and adds the window, so `hours` may give two spans: `own window 18:00-19:00
added to the usual hours, feed 08:58-15:50, 17:58-19:10`. An ordinary day reads `market window`,
`weekend` or `NSE holiday` as before. `extra_days` counts a window that cannot be used as a window
problem; the journal says which line and what the day does instead.

`cas_status` is the closing-auction phase Upstox reports per segment (`casMarketStatus` in the
feed's market info; NSE CMTR75479, from 3 Aug 2026: F&O stocks stop continuous trading at 15:15
and close by auction). Upstox lists a segment only while its auction runs, so the map is `{}` the
rest of the day, and each market info replaces it whole. It is also `{}` while no socket is open:
a phase is known only from the open socket's own market info. A phase reads
`{"status": "CAS_LM_START", "updated_ms": 1793094600250}`, `updated_ms` being when the phase began
(Upstox's clock, epoch ms; `null` if it sent none; a number that is no epoch-ms instant is passed on
and logged as it came, and the frame's segment status is still recorded). A market info that
changes both maps goes out as one health copy carrying both. The phases Upstox documents are `CTS_CLOSE`
(continuous trading over, about 15:15), `CAS_LM_START` (auction orders, about 15:20),
`CAS_M_STOP` (limit orders only, about 15:25) and `CAS_STOP` (order entry closed, at a random time
from 15:28 to 15:30); any other word is passed on as Upstox sends it. Whether Upstox fills this
map at all is not yet known (on 27 Oct the segment status said nothing at 15:15 or 15:20), so the
journal tells every session: the first market info on each socket is one line even when the map
is empty (`closing auction status on connect: none`), and every later change is one line naming
the segments that changed, `closing auction status: NSE_EQ CAS_LM_START at
2026-10-27T15:20:00+05:30` (`NSE_EQ none` when it leaves the map). A change also goes out on the
health topic at once.

```bash
curl -s 127.0.0.1:5580/health | python3 -m json.tool
curl -s '127.0.0.1:5580/snapshot?keys=NSE_INDEX%7CNifty%2050,NSE_EQ%7CINE002A01018'
curl -s -X POST 127.0.0.1:5580/subscribe -H 'Content-Type: application/json' \
     -d '{"mode": "full", "keys": ["NSE_EQ|INE009A01021"]}'
```

## REST governor (M18.9): `GET /upstox/<path>`

Every SUDARSHAN service that needs Upstox's market data over REST (history for a warm start,
quotes for a first snapshot, the option chain) asks the feed, which holds the Analytics Token:
`GET http://127.0.0.1:5580/upstox` followed by Upstox's own path and query. The feed adds the
token, keeps SUDARSHAN inside one share of Upstox's limits, and hands back Upstox's status and
body as bytes, never parsed (cross-design rules G1-G8; the code is `rest.py`). Nobody else in
SUDARSHAN calls these APIs with the Analytics Token. It is a feature flag and ships **off**: until
the owner sets `SUDARSHAN_FEED_REST=on` (G7), every request below is answered 503 `off`.

```bash
curl -s -H 'X-Sudarshan-Client: sudarshan-portfolio' -H 'X-Sudarshan-Priority: interactive' \
  '127.0.0.1:5580/upstox/v3/market-quote/ltp?instrument_key=NSE_INDEX%7CNifty%2050'
```

Every request names its service in `X-Sudarshan-Client` (`sudarshan-screener`,
`sudarshan-portfolio` or `sudarshan-alerts`; anything else is 403) and its class in
`X-Sudarshan-Priority`: `repair` (a gap after a feed pause), `interactive` (the owner is waiting,
a morning snapshot) or `bulk` (warm starts, backfills, the 16:10 option chains).

**What passes**, each Upstox API on its own windows (anything else is 403, a request that does not
fit the API's shape 400; account reads such as holdings, positions or funds never pass and stay
in `sudarshan-portfolio`):

| API (in /health) | path | Upstox's page (upstox.com/developer/api-documentation/...) |
|---|---|---|
| `historical-candle` | `/v3/historical-candle/{key}/{unit}/{n}/{to}[/{from}]` | `v3/get-historical-candle-data` |
| `intraday-candle` | `/v3/historical-candle/intraday/{key}/{unit}/{n}` | `v3/get-intra-day-candle-data` |
| `full-quote` | `/v3/market-quote/quotes?instrument_key=K1,K2` (at most 500) | `get-full-market-quote-v3` |
| `ltp` | `/v3/market-quote/ltp?instrument_key=...` (at most 500) | `ltp-v3` |
| `ohlc` | `/v3/market-quote/ohlc?instrument_key=...&interval=1d\|I1\|I30` | `get-market-quote-ohlc-v3` |
| `option-chain` | `/v2/option/chain?instrument_key=K&expiry_date=YYYY-MM-DD` (or `current_week` ...) | `get-pc-option-chain` |

Keys are checked and every call goes to Upstox in one spelling (`|` as `%7C`); unit, interval,
dates and expiry keep the shapes those pages give; every parameter is required and no other is
taken.

**Shares**, per API: Upstox allows 50 a second, 500 a minute and 2,000 per 30 minutes per API and
user; SUDARSHAN takes at most 40% and each service at most its ceiling inside it, so the other
60% stays for OpenAlgo's daily token and TRINETRA. The windows slide: no second, minute or half
hour ever holds more.

| | per second | per minute | per 30 minutes |
|---|---|---|---|
| SUDARSHAN in all (40%) | 20 | 200 | 800 |
| `sudarshan-screener` (30%) | 15 | 150 | 600 |
| `sudarshan-portfolio` (5%) | 2 | 25 | 100 |
| `sudarshan-alerts` (5%) | 2 | 25 | 100 |

- **Order of service**: repair, then interactive, then bulk; first come first served within a
  class. At most 4 calls are in flight to Upstox and 64 wait; a request waits at most 30 s. When
  64 wait, the last of them in that order is told it is busy: the newcomer, unless it outranks
  that one (a full queue of warm-start calls never turns away a repair or a quote).
- **Bulk waits for the market to close**: from 08:30 to 16:00 IST on a trading day (around a
  special session's own window with the same margins, 13:35-16:00 for Muhurat 14:05-15:40) a bulk
  request is answered at once, never held.
- **Upstox says slow down** (a 429, or `UDAPI10005` in any answer): that API stops for every
  service for 60 s, then runs at half its rates for 10 minutes. **Upstox refuses the token** (a
  401): every API stops until the token file changes, as the socket does. A 401 for a call sent
  with the token the file held before (the owner has just replaced it) stops nothing: it says
  nothing about the new token. A failure to reach Upstox is one journal line per API at most
  every 5 minutes.
- Nothing is retried here: 4xx is final, 5xx and network errors are for the caller to retry.
  Callers should allow 70 s (30 s of waiting, 30 s for Upstox, the hand-over).
- **Memory**: a place in flight is held until the caller has the answer (or the 30 s to hand it
  over run out), so at most 4 answers wait in the feed at once: 32 MiB at most (4 x 8 MiB; an
  answer is copied once as it is assembled, so never over 64 MiB even at that instant), plus
  the kernel's socket buffers (4 MiB each at most with Ubuntu's defaults). A caller that does not
  read holds a place for those 30 s, then loses its answer.
- A caller that hangs up while it waits leaves the queue at once and never reaches Upstox; one
  that hangs up while its call is with Upstox frees its place at once. Closing the connection
  is what counts, so a caller must not half-close it after sending its request.
- Every answer carries `X-Sudarshan-Waited-Ms`. Upstox's own answers come with its status,
  `Content-Type` and body. The governor's own answers are JSON `{"error": sentence}`, written
  for the owner, with `X-Sudarshan-Reason`:

| status | `X-Sudarshan-Reason` | when | `Retry-After` |
|---|---|---|---|
| 503 | `bulk-hours` | bulk in market hours: "History downloads wait for the market to close (after 16:00)." | seconds to the end of market hours |
| 503 | `busy` | the last of 64 waiting, or no turn within 30 s: "SUDARSHAN's share of Upstox requests is in use; try again in a minute." | 60, or when a window opens |
| 503 | `rate-limited` | the API is stopped after a 429 for longer than the 30 s a request may wait | when it starts again |
| 503 | `token` | Upstox refused the token (until the file changes), or there is no usable token | 60 |
| 503 | `restarting`, `off` | the feed is stopping; the governor is switched off (the default, until `SUDARSHAN_FEED_REST=on`) | 60 |
| 502 | `too-large` | Upstox's answer is over 8 MiB | - |
| 502, 504 | `no-answer` | Upstox could not be reached, or did not answer within 30 s | - |
| 403 | `client`, `not-allowed` | an unknown `X-Sudarshan-Client`; a path or method outside the list | - |
| 400 | `bad-request` | a missing priority, a bad key, a missing or extra query parameter | - |

A caller's mistake (the 403 and 400 rows) is a fault in SUDARSHAN, not in the owner's settings,
so its sentence says so in plain words, for example "The price feed could not read this request
for Upstox. This is a fault in SUDARSHAN itself; nothing in your settings needs changing." What
was wrong (which header or parameter) is in the feed's journal at DEBUG
(`SUDARSHAN_FEED_LOG_LEVEL=DEBUG`), never in the answer.

`/health` shows the governor as `rest`: `enabled`, `counted_since_ms`, `in_flight`, `waiting`,
`bulk_refused_until_ms` (the end of today's market hours while bulk is refused),
`token_refused_since_ms`, `not_allowed`, and per API (`apis`) `calls`, `waits`, `refusals`,
`429s`, `frozen_until_ms` and `half_rate_until_ms`, with the same counts per service (`clients`,
services with any count). Instants are epoch ms (cross-design X3), `null` when not set. Never a
URL, a query or the token. The counts run on across restarts (`rest_counts.json`, below), from
`counted_since_ms`: after the first week, `apis.<api>.429s` says whether SUDARSHAN's 40% met
Upstox's limits on the account, which TRINETRA shares.

A restart never grants a share twice: the same file keeps every send of the last 30 minutes per
API and service, and any stop or half rate after a 429, so a deploy at 16:20 after a warm start
does not open a second 600 for the screener, and a feed restarted inside a stop stays stopped.
A change reaches the file within a second (a 429's stop at once), written on a thread of its
own so the disk never holds the event loop the ticks use. After a crash or a kill the last
second may be missing, so the next start counts that second as each service's whole
per-second share on every API (15, 2 and 2 calls, in every window), and the journal says so.

## ZMQ bus: 127.0.0.1:5581

The service **binds** a PUB socket; consumers connect SUB sockets. This is SUDARSHAN's own bus,
separate from OpenAlgo's 5555 market-data bus, where the opposite rule holds (the proxy's SUB
binds and publishers connect). A taken port is a startup error, never a silent port change.

Frames: `[topic = instrument key (utf-8), payload = compact JSON]`. Topic `__health__` carries
the /health document every 5 s and on every state change. ZMQ subscriptions match by prefix
(`NSE_INDEX|Nifty 50` also matches `NSE_INDEX|Nifty 500`), so compare frame 0 exactly:

```python
import json, zmq

sub = zmq.Context.instance().socket(zmq.SUB)
sub.connect("tcp://127.0.0.1:5581")
sub.subscribe(b"NSE_INDEX|Nifty 50")
while True:
    topic, payload = sub.recv_multipart()
    if topic == b"NSE_INDEX|Nifty 50":
        tick = json.loads(payload)
```

Tick fields (present once Upstox has sent them): `key`, `ltp`, `ltt` (epoch ms), `ltq`, `cp`
(previous close), `bid`, `bid_qty`, `ask`, `ask_qty` (best level), `ohlc` {open, high, low,
close, vol, ts} (the 1d candle), `ohlc_1m` {the same fields} (Upstox's one-minute `I1` candle in
full mode, stocks and indices alike, `ts` the minute's start; Upstox does not say whether it is
the minute just completed or the one forming, so compare `ts` with the minute you want),
`greeks` {delta, theta, gamma, vega, rho}, `iv`, `atp`, `vtt` (volume traded today), `oi`,
`tbq`, `tsq`, and `ts_recv` (epoch ms, when the service received it). Each publish is the key's
**full current state**: Upstox's initial_feed (after every (re)subscribe) starts a key afresh,
and live packets merge into it, so a group a packet leaves out keeps its last value (a packet
without the `I1` entry keeps the last `ohlc_1m`). Upstox's bare numeric fields cannot say "not
sent" apart from 0, so a 0 there keeps the previous value: a quantity that really drops to 0
(tsq at an upper circuit) shows its last non-zero value. Use REST quotes for circuit checks.

The one-minute candle costs the tick recorder (measured 28 Oct 2026 on synthetic full ticks
through the real decode, merge and gzip): a full tick's line grows from about 315 to 413 bytes,
but compressed from about 56 to 65-69 bytes (+16-22%). Today's base set (about 23 ticks a second)
records about 30 MB a day, 35 MB with the candle, against the 512 MB cap: no risk. At ring scale
the cap holds up to about 386 keys in full mode at one tick a second each, 316 with the candle;
the ring's 732 keys at worst would reach it after about 3.4 hours of trading (2.8 with the
candle). So the cap needs a decision (raise it, or record fewer keys) before the claims pass
about 300 full keys, with or without the candle.

## Behaviour

- **Market window**: the socket is held only on trading days from 08:58 to 15:50 IST
  (`SUDARSHAN_FEED_WINDOW`), closed with a close frame at the end of it. Those are margins around
  NSE's session: 2 minutes before the 09:00 pre-open, so the socket is authorised, connected and
  subscribed when the first data comes, and 10 minutes after the F&O close, so the updates that
  follow the close still arrive. Equity derivatives trade to 15:40 since 3 Aug 2026 (the cash
  market closes at 15:30), so the window ends at 15:50 and the stall watchdog runs 09:15-15:40.
  `SUDARSHAN_FEED_ALWAYS_ON=1` ignores the window and the calendar (tests).
- **Trading days** come from auto-login's calendar, the one the alerts use: weekends and the
  holidays file are closed, a date in the extra-days file is open, even when the holidays file
  lists it too (auto-login, the alerts and the deploy tool all read it that way).
  - A reopened weekend or holiday with its own window gets the usual day moved onto it, margins
    and all (those `SUDARSHAN_FEED_WINDOW` keeps around 09:00-15:40): `14:05-15:40` holds the
    socket from 14:03 to 15:50, with the watchdog from 14:20 (15 minutes in, as 09:15 is) to 15:40.
  - A day that trades anyway keeps its usual hours and adds its own window to them.
  - A reopened day with no usable window keeps the usual hours; its watchdog starts with the day's
    first trade (a tick whose last trade time is that day).
  - A window that cannot be read leaves that day at the usual hours, with a warning in the
    journal.
- **Connect**: every attempt re-reads the token file, calls
  `GET /v3/feed/market-data-feed/authorize` and dials the returned single-use URI.
- **Retry policy**:

  | outcome | next attempt |
  |---|---|
  | drop or failure | exponential with jitter: 1-2 s, 2-4 s, ... up to 150-300 s; back to the start once a socket stayed up 60 s |
  | WebSocket upgrade refused (403/429) | as above but never sooner than 60 s, so a stale socket can expire at Upstox |
  | authorize 401 ("token rejected") | 10 min, 30 min, then hourly; at once when the token file changes |
  | token file missing, empty or malformed | 60 s; at once when the file changes |
  | no data for 120 s during 09:15-15:40 (on a day with its own window: from 15 minutes after its start, the pre-open's length, to its end; on a reopened day without one: only after its first trade) | clean close and reconnect (stall watchdog) |

- **Subscriptions**: `default_keys.txt` (or `SUDARSHAN_FEED_KEYS`) is the base set, one
  `MODE KEY` per line, re-read at every start. API changes are stored as an overlay in
  `$STATE_DIR/subscriptions.json` and survive restarts. Every key is re-sent after a reconnect,
  in batches of 100. Caps per connection (Upstox): ltpc 5,000, full 2,000, option_greeks 3,000,
  full_d30 50; once two modes are in use, the total may not exceed the smallest "combined"
  figure among them (ltpc 2,000, option_greeks 2,000, full 1,500, full_d30 1,500). That reading
  of Upstox's "combined" column is the conservative one OpenAlgo's client uses.
- **Shutdown** (SIGTERM/SIGINT): close frame to Upstox (waits up to 5 s), final snapshot and
  recorder flush; `TimeoutStopSec=20` leaves room for both.

## Files and settings

`$STATE_DIR` (`SUDARSHAN_FEED_STATE_DIR`, else systemd's `STATE_DIRECTORY`, else
`/var/lib/sudarshan-feed`):

| file | what |
|---|---|
| `snapshot.json` | last tick per key; written atomically every 30 s and at shutdown, loaded at start |
| `subscriptions.json` | the API overlay on top of the keys file |
| `rest_counts.json` | the REST governor's counters, each API's sends of the last 30 minutes per service and any stop or half rate (epoch ms), saved within 1 s of a change and at every stop; deleting it (feed stopped) counts afresh and forgets the windows, so do it at least 30 minutes after SUDARSHAN's last Upstox call |
| `ticks/YYYY-MM-DD.jsonl.gz` | every published tick (IST date), one gzip member per 3 s flush; a day stops at the size cap; old days are deleted |

Replay a recording to test a consumer offline (binds its own port; use another one while the
live service holds 5581):

```bash
.venv/bin/python -m sudarshan.feed.replay /var/lib/sudarshan-feed/ticks/2026-10-21.jsonl.gz --speed 10 --port 5591
```

| variable | default |
|---|---|
| `SUDARSHAN_UPSTOX_TOKEN_FILE` | `/etc/sudarshan/upstox_analytics_token` |
| `SUDARSHAN_FEED_KEYS` | `sudarshan/feed/default_keys.txt` |
| `SUDARSHAN_FEED_STATE_DIR` | `$STATE_DIRECTORY` or `/var/lib/sudarshan-feed` |
| `SUDARSHAN_NSE_HOLIDAYS_FILE` | `/etc/sudarshan/nse_holidays.txt` |
| `SUDARSHAN_NSE_EXTRA_DAYS_FILE` | `/etc/sudarshan/nse_extra_trading_days.txt` |
| `SUDARSHAN_FEED_ALWAYS_ON` | `0` |
| `SUDARSHAN_FEED_WINDOW` | `08:58-15:50` (IST); a value that is not HH:MM-HH:MM (the start first) logs a warning and the usual hours are used |
| `SUDARSHAN_FEED_HTTP_PORT` / `SUDARSHAN_FEED_ZMQ_PORT` | `5580` / `5581` (always 127.0.0.1) |
| `SUDARSHAN_FEED_RECORD` | `1` |
| `SUDARSHAN_FEED_RECORD_MAX_MB` / `SUDARSHAN_FEED_RECORD_KEEP_DAYS` | `512` / `10` |
| `SUDARSHAN_FEED_LOG_LEVEL` | `INFO` |
| `SUDARSHAN_FEED_REST` | `off`: every `/upstox/` request is answered 503 `off`; the owner sets `on` in `/etc/sudarshan/feed.env` at G7, when the ring's REST callers start |
| `SUDARSHAN_UPSTOX_API_BASE` | `https://api.upstox.com` (tests point it at a local fake; https is required otherwise) |

Put overrides in `/etc/sudarshan/feed.env` (root:sudarshan 640); the unit reads it if present.

## Troubleshooting

Logs: `journalctl -u sudarshan-feed`. One line per state change (`state connecting -> connected`),
repeated errors at most once per 5 minutes with a "similar suppressed" count. The token and the
authorize URI's query string (a one-time code) are never logged or shown.

- **`last_error.kind = "upgrade_refused"`, HTTP 403** (log: "WebSocket upgrade refused with
  HTTP 403 ... per-user connection limit"): Upstox counted too many sockets for the account.
  Check who holds them: TRINETRA (one), and OpenAlgo's own WebSocket proxy, which opens a second
  Upstox socket with the trading token whenever a page streams (M18.2). A socket left behind by
  a crash counts until Upstox times it out; the service waits at least 60 s between such
  attempts and connects on its own once a slot frees. On the Plus plan the limit is 5.
- **`token_rejected`**: the Analytics Token was refused (expired, regenerated, or wrong file).
  Put the current token in the file; the service retries within 5 s of the change.
- **`keys_without_data.count > 0`** a minute after `connected`: those keys sent nothing. Upstox
  ignores unknown keys silently; check them against the instrument master (a split changes an
  ISIN). Illiquid instruments can also be quiet.
- **Drops**: `last_disconnect` has `socket_age_s`, `code` (1006 = no close frame) and `by`.
  Drops near 600 s with 500+ keys are the open Upstox report U9; every drop is logged with its
  age so a pattern shows.
- **`network` errors** such as `SSLCertVerificationError` or name resolution: the server's
  outbound HTTPS to Upstox; the service keeps retrying with backoff.
- **Start-up failures** (`sudarshan-feed cannot run: ...`): a port already in use, or a keys file
  line that is not `MODE KEY` or breaks a cap. systemd retries every 10 s; fix and wait.
- **REST governor**: `rest.token_refused_since` set means Upstox answered a REST call with 401
  (journal: "REST: Upstox refused the Analytics Token"); put the current token in the file and
  the governor resumes on the next request. A `429s` count that grows (journal: "REST: Upstox
  asked to slow down on ...") means the account's limits were met although SUDARSHAN keeps to its
  40%: the rest goes to OpenAlgo's daily-token calls and TRINETRA. Each call is one DEBUG line
  (service, API, status, size, wait), never its query.

`sudarshan/` deliberately has no `__init__.py`: the repo root carries one, and in CI the checkout
directory is itself named `sudarshan`, so pytest would import a `sudarshan/__init__.py` as
`sudarshan.sudarshan` and fail. As a namespace package it still runs with
`python -m sudarshan.feed`.

Tests: `uv run pytest -q sudarshan` (no network, no real token; a fake Upstox on 127.0.0.1).
