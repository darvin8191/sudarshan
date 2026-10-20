# sudarshan-alerts (M6.8): order alerts on Telegram

A fill, a part fill, a rejection, a cancellation or a stop order that triggers reaches the owner's
Telegram as it happens, the way his broker apps tell him. Version 1 does order alerts only; price,
screener and other alerts come later in the same service.

```
Upstox Portfolio Stream Feed -> OpenAlgo's order adapter (in gunicorn) -> event bus -> ZMQ
  -> OpenAlgo's WebSocket proxy, ws://127.0.0.1:8765
  -> sudarshan-alerts: authenticate (the owner's API key), subscribe_orders
  -> orders.py: is this change worth a message? claimed once in /var/lib/sudarshan-alerts/alerts.db
  -> outbox.py: merged and paced -> POST 127.0.0.1:5000/api/v1/telegram/notify
  -> OpenAlgo's Telegram bot -> the owner's linked chat
```

A standalone asyncio process with its own unit, never inside the OpenAlgo app: no thread,
scheduler or event loop is added to gunicorn, and nothing in OpenAlgo was changed for it.

## What the owner receives

```
सुदर्शन / SUDARSHAN
Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.45 (MIS, NFO)
10:42:05 IST
```

| update | line |
|---|---|
| complete | `Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.45 (MIS, NFO)` |
| part fill | `Part filled: BUY 65 of 130 NIFTY 21 OCT 25900 CE at ₹98.10 (MIS, NFO)` |
| rejected | `Rejected: SELL 65 NIFTY 21 OCT 25900 CE (NRML, NFO). Broker says: ...` |
| cancelled | `Cancelled: BUY 65 NIFTY 21 OCT 25900 CE, none filled (MIS, NFO)` |
| cancelled after a part fill | `Part filled, rest cancelled: BUY 40 of 65 NIFTY 21 OCT 25900 CE at ₹121.50 (MIS, NFO)` |
| stop triggered, not filled | `Triggered: SELL 65 NIFTY 21 OCT 25900 CE at trigger ₹120.00, not filled yet (SL, MIS, NFO)` |

- Quantities are units, as the broker reports them; money and quantities use Indian grouping
  (₹1,23,456.50); currency contracts show four decimals.
- The price is the average fill price. The order type is shown only for stop orders (SL, SL-M).
- Upstox's carry-forward product reaches the order stream as CNC; on a derivatives exchange it is
  shown as NRML.
- The broker's reason is quoted after "Broker says:", with the Markdown characters (asterisk,
  underscore, backtick, square brackets) replaced (OpenAlgo sends with Telegram Markdown and does not escape).
- The time is when SUDARSHAN received the update. A merged message spanning more than a minute (a
  retry after OpenAlgo was down) shows the range.
- A burst (a basket, a multi-leg exit) becomes one message listing every order, one line per order
  with its newest state: up to 15 lines a message, the rest in the next one.
- Sandbox (Analyze mode) orders are skipped unless switched on; their lines start with "Sandbox".

**Cancellations.** OpenAlgo's order message has no field that says who cancelled an order (Upstox's
`status_message` is kept only for rejections), so an owner cancel cannot be told apart from a
broker or exchange cancel. Every cancellation is sent by default, because a cancel by the broker
(a stop order removed before the square-off) or by the exchange (an unfilled market order under
price protection, an IOC order) matters and looks the same. A cancellation after a part fill is
always sent: the position is not what was ordered. `SUDARSHAN_ORDER_ALERTS_CANCELLED=part-filled`
keeps only those.

**Broker square-off.** Upstox squares off by placing its own order, which arrives as a new order
and is told as `Filled`. The message does not say "square-off": nothing in the update says so,
and a guessed cause would mislead.

**Triggers.** An SL order waits as "trigger pending". When it triggers, the next update says
"open"; but a modify or a cancel of a stop order also passes through "open" for a moment ("modify
pending" and "cancel pending" arrive as open). So a stop order is reported as triggered only after
it stayed working and unfilled for 3 s; a fill, a cancel or a return to trigger pending inside that
time is reported as itself (or not at all). A stop that fills at once is one `Filled` line with
`(SL, ...)`.

## When it runs

Connected on trading days from 08:45 to 16:00 IST, with the calendar auto-login uses
(`sudarshan/autologin/schedule.py`): weekends and the dates in `/etc/sudarshan/nse_holidays.txt`
are closed, a date in `/etc/sudarshan/nse_extra_trading_days.txt` (a Muhurat session) is open. Both
files are read again when they change. Outside the window the process stays up, disconnected, and
connects again at the next opening. The MCX evening session is not covered.

**Upstox connections.** OpenAlgo's proxy opens an Upstox market-data connection (the daily access
token) for every authenticated client and keeps it while any client of the owner is connected. So
from 08:45 to 16:00 this service keeps that one connection open, even with no screen open. It is
the same pooled connection the owner's real-time screens use, within the proxy's budget of at most 2.
Before the day's Upstox login the proxy answers the sign-in with a broker error but still
subscribes this service to orders; the updates start with the login.

## Turning it off

On by default (the owner asked for it). To stop all messages:

```bash
echo 'SUDARSHAN_ORDER_ALERTS=off' | sudo tee -a /etc/sudarshan/alerts.env
sudo chown root:sudarshan /etc/sudarshan/alerts.env && sudo chmod 640 /etc/sudarshan/alerts.env
sudo systemctl restart sudarshan-alerts
```

The service then idles and does not contact OpenAlgo at all. Remove the line and restart to turn
it on again. Stopping OpenAlgo's Telegram bot also stops these messages (OpenAlgo refuses to send
while its bot is stopped); messages refused that way are dropped, not held back for later.
`systemctl stop` is not lasting: the deploy tool starts ring services again after each release.

| variable | default | meaning |
|---|---|---|
| `SUDARSHAN_ORDER_ALERTS` | `on` | `off`: nothing is sent and the proxy is not contacted |
| `SUDARSHAN_ORDER_ALERTS_CANCELLED` | `all` | `part-filled`: only cancellations of an order that was part filled |
| `SUDARSHAN_ORDER_ALERTS_SANDBOX` | `off` | `on`: Analyze-mode (sandbox) orders too, each line starting with "Sandbox" |
| `SUDARSHAN_ALERTS_WINDOW` | `08:45-16:00` | IST, on trading days |
| `SUDARSHAN_ALERTS_ALWAYS_ON` | `0` | `1`: ignore the window and the calendar (tests, an evening sandbox check) |
| `SUDARSHAN_ALERTS_STATE_DIR` | systemd's `$STATE_DIRECTORY` | `/var/lib/sudarshan-alerts` |
| `SUDARSHAN_ALERTS_LOG_LEVEL` | `INFO` | |
| `SUDARSHAN_NSE_HOLIDAYS_FILE` | `/etc/sudarshan/nse_holidays.txt` | shared with auto-login and the feed |
| `SUDARSHAN_NSE_EXTRA_DAYS_FILE` | `/etc/sudarshan/nse_extra_trading_days.txt` | |
| `SUDARSHAN_OPENALGO_DIR` | the checkout | OpenAlgo's directory (`.env`, `db/`) |
| `SUDARSHAN_OPENALGO_ENV_FILE` | `<dir>/.env` | |
| `SUDARSHAN_OPENALGO_URL` | `http://127.0.0.1:5000` | loopback only (the key travels on it) |
| `SUDARSHAN_OPENALGO_WS_URL` | `ws://127.0.0.1:8765` | loopback only |

Settings go in `/etc/sudarshan/alerts.env` (root:sudarshan 640); a missing file means the
defaults; a change needs `sudo systemctl restart sudarshan-alerts`. A value the service cannot
read stops it with a plain line in the journal (systemd tries again every 10 s).

## The API key and the trust boundary

The service needs the owner's OpenAlgo API key twice: to sign in to the proxy and to ask OpenAlgo
to send a Telegram message. Nobody pastes it anywhere. The service reads it from openalgo.db
exactly as OpenAlgo's TradingView page does (`user_db.find_user_by_username`,
`auth_db.get_api_key`, `auth_db.get_api_key_for_tradingview`), decrypting it with the Fernet key
OpenAlgo derives from `API_KEY_PEPPER` and `FERNET_SALT`.

- **Read-only.** The unit may write only `/var/lib/sudarshan-alerts`. openalgo.db is in WAL mode,
  which SQLite cannot open read-only unless its `-wal` file exists or can be created. So each
  read picks its way: with a `-wal` file present it opens the database read-only (`mode=ro`) and
  sees the newest data; without one, the last connection to close has written everything back, and
  it reads the main file as it is (`immutable=1`), creating nothing. A read that fails is tried the
  other way (owner.py).
- **Fresh.** The key is read again before every connection attempt and after OpenAlgo refuses it,
  so a regenerated key is used from the next reconnect. No key yet: one line in the journal, no
  message, and it starts by itself once a key is generated (it looks again every minute).
- **What it holds.** The key is order-capable. The service uses it for those two calls only and
  never calls an order endpoint. It is never logged, printed or written anywhere, and the unit
  allows network traffic to this server only (`IPAddressDeny=any`, `IPAddressAllow=localhost`).
- **What it could already reach.** It runs as `sudarshan`, the user OpenAlgo runs as, so it gains
  nothing that user could not already read: OpenAlgo's `.env` and databases. It takes three values
  from `.env` (the pepper, the salt and `DATABASE_URL`) and skips the rest: OpenAlgo's logging
  module would load the whole file (broker secrets included) into the environment when imported,
  and that load is turned off here. Family members' services must never run as this user.
- **Telegram.** OpenAlgo looks up the chat linked to the owner's username and sends with its own
  bot: this service never sees the bot token or a chat id and cannot message anyone else.
  OpenAlgo's per-user notification switch on its Telegram page is not consulted by
  `/api/v1/telegram/notify`, and not by this service either.

## Reliability

- **Once only.** Each alert is claimed in `alerts.db` by (order id, kind, filled quantity) before
  it is queued. A reconnect, a restart, a replay, or the same update from both the order stream and
  a webhook finds the claim and stays silent. Nothing is sent for an order after its final state,
  and a part fill only when more is filled than last time. Records older than 7 days are pruned
  at start-up and when the window closes.
- **Reconnects.** Backoff with jitter, 2 s doubling to 60 s, reset only after a connection stayed
  up 60 s; a refused key waits 60 s and is read again.
- **Pace.** At least 1.1 s between messages and at most 20 a minute (Telegram asks for about one a
  second per chat; OpenAlgo allows `/notify` 30 times a minute). A message waits 0.3 s after the
  first alert of a burst, so the burst goes out as one.
- **Delivery.** OpenAlgo is asked to wait for Telegram's answer. A message Telegram did not confirm
  is tried up to 3 times; if OpenAlgo's wait had timed out after Telegram had in fact delivered,
  the owner may see it twice (rare). When OpenAlgo cannot be reached, messages are retried for up
  to 30 minutes, then dropped with a line in the journal. Telegram bot stopped, or the owner's
  account not linked to a chat: dropped, with one line in the journal every 10 minutes.
- **Bounded.** At most 300 orders wait to be sent (beyond that the oldest are dropped and the next
  message says how many could not be listed), at most 200 stop orders wait out the trigger check,
  one proxy message is at most 1 MiB and at most 64 wait to be read. Memory limit 256 MB.
- **On stop** (SIGTERM, a deploy): the stream closes cleanly within a second, waiting alerts get
  5 s to go out. A crash loses only alerts that were waiting to be sent (claimed, so never sent
  twice); normally that is less than a second's worth.

## On the server

Nothing by hand: `sudarshan-deploy` installs `sudarshan/ops/systemd/sudarshan-alerts.service` from
the release that carries it, enables and starts it like `sudarshan-feed` (outside market hours),
and systemd creates `/var/lib/sudarshan-alerts` (mode 700). The settings file is optional.

Before the first trading day, check:

1. OpenAlgo has an API key (profile menu, API Key).
2. OpenAlgo's Telegram bot is started and linked to the owner (OpenAlgo, Telegram Bot).
3. OpenAlgo's order updates are not switched off (`ORDER_UPDATES_ENABLED` is not `FALSE`).

By hand, before a release ships it (outside market hours):

```bash
sudo install -m 644 sudarshan/ops/systemd/sudarshan-alerts.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sudarshan-alerts
journalctl -u sudarshan-alerts -f
```

An end-to-end check without real orders, outside market hours: put
`SUDARSHAN_ORDER_ALERTS_SANDBOX=on` and `SUDARSHAN_ALERTS_ALWAYS_ON=1` in the settings file,
restart, switch OpenAlgo to Analyze mode and place, then cancel, a sandbox limit order: Telegram
shows `Sandbox cancelled: ...`. Remove both lines and restart afterwards.

## Troubleshooting

Logs: `journalctl -u sudarshan-alerts`. One line per state change (`state connecting -> connected`)
and one per alert (`order <id>: filled`); repeated problems at most once per 5 or 10 minutes.

- **`No OpenAlgo API key yet`**: generate one in OpenAlgo; the service picks it up within a minute.
- **`OpenAlgo's WebSocket proxy refused the API key`**: the key was regenerated; it is read again.
- **`OpenAlgo's WebSocket proxy is not reachable`**: OpenAlgo is starting or stopped; it retries.
- **`No Telegram message: OpenAlgo's Telegram bot is stopped`** or **`... not linked`**: start
  the bot or link the owner's account on OpenAlgo's Telegram Bot page.
- **`The OpenAlgo API key cannot be decrypted ...`**: OpenAlgo's pepper or salt changed (for
  example `upgrade/rotate_pepper.py`); restart the service.
- **No alerts while connected**: `/websocket/order` in OpenAlgo shows the same stream; if it is
  silent too, OpenAlgo's order adapter is not receiving from Upstox.

## Not verified yet (needs a trading session with real orders)

- Whether Upstox's Portfolio Stream Feed sends an update for every part fill, or only some.
- The status sequence of an SL order that triggers ("trigger pending", then "open"), and of a
  modify or cancel of one; the 3 s rule is built on OpenAlgo's mapping of those statuses.
- How a broker square-off appears (expected: a new MIS order filled near the square-off time).
- Upstox's rejection wording, shown verbatim after "Broker says:".
- That Upstox replays nothing when OpenAlgo's order adapter reconnects (a replay would be silent
  for orders already told, but orders that ended while nothing was connected would be told late).

## Tests

`uv run pytest -q sudarshan/alerts` (no network: a fake WebSocket proxy and a fake `/notify` on
127.0.0.1, an openalgo.db written with OpenAlgo's own models, and one run of the real entry point
as a child process). The unit is checked against the deploy tool's own `unit_ok` policy and with
`systemd-analyze verify` when it is installed.
