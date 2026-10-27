# sudarshan-alerts (M6.8): order alerts on Telegram

A fill, a part fill, a rejection, a cancellation or a stop order that triggers reaches the owner's
Telegram as it happens, the way his broker apps tell him. Version 1 does order alerts only; price,
screener and other alerts come later in the same service.

```
Upstox Portfolio Stream Feed -> OpenAlgo's order adapter (in gunicorn) -> event bus -> ZMQ
  -> OpenAlgo's WebSocket proxy, ws://127.0.0.1:8765
  -> sudarshan-alerts: authenticate (the owner's API key), subscribe_orders
       after every subscription: the day's order book and trade book, once (resync.py)
  -> orders.py: is this change worth a message? claimed once in /var/lib/sudarshan-alerts/alerts.db
  -> outbox.py: merged and paced -> POST 127.0.0.1:5000/api/v1/telegram/notify
  -> OpenAlgo's Telegram bot -> the owner's linked chat
```

A standalone asyncio process with its own unit, never inside the OpenAlgo app: no thread,
scheduler or event loop is added to gunicorn, and nothing in OpenAlgo was changed for it. The
same service also carries the server's own notices to the owner's Telegram (below).

**On by default: the owner's decision.** His rule 7 benchmark (Kite, Dhan, Upstox, Fyers) says a
fill or a rejection must show at once, in the app and on Telegram. The switch below turns it off;
the default stays on.

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
| rejected after a part fill | `Rejected: BUY 65 ... (MIS, NFO); 40 were filled at ₹121.50. Broker says: ...` |
| cancelled | `Cancelled: BUY 65 NIFTY 21 OCT 25900 CE, none filled (MIS, NFO)` |
| cancelled after a part fill | `Part filled, rest cancelled: BUY 40 of 65 NIFTY 21 OCT 25900 CE at ₹121.50 (MIS, NFO)` |
| stop triggered, not filled | `Triggered: SELL 65 NIFTY 21 OCT 25900 CE at trigger ₹120.00, not filled yet (SL, MIS, NFO)` |
| found after a pause | `Filled: BUY 65 ... (MIS, NFO) (found after a pause in order alerts)` |
| the pause itself | `Order alerts paused 10:42:05 to 10:44:10 IST; the order book was checked.` |
| the pause, the book not readable | `Order alerts paused 10:42:05 to 10:44:10 IST; the order book could not be read; open Orders in the app to check.` |

- Quantities are units, as the broker reports them; money and quantities use Indian grouping
  (₹1,23,456.50); currency contracts show four decimals.
- The price is the average fill price. The order type is shown only for stop orders (SL, SL-M).
- Upstox's carry-forward product reaches the order stream as CNC; on a derivatives exchange it is
  shown as NRML.
- The broker's reason is quoted after "Broker says:", with the Markdown characters (asterisk,
  underscore, backtick, square brackets) replaced: OpenAlgo sends with Telegram Markdown and does
  not escape.
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
keeps only those. An order placed, changed or cancelled through OpenAlgo's own screens or API may
also get OpenAlgo's own Telegram line for that action (when its bot is on), next to this one.

**Broker square-off.** Upstox squares off by placing its own order, which arrives as a new order
and is told as `Filled`. The message does not say "square-off": nothing in the update says so,
and a guessed cause would mislead.

**Triggers.** An SL order waits as "trigger pending". When it triggers, the next update says
"open"; but a modify or a cancel of a stop order also passes through "open" for a moment ("modify
pending" and "cancel pending" arrive as open). So a stop order is reported as triggered only after
it stayed working and unfilled for 3 s; a fill, a cancel or a return to trigger pending inside that
time is reported as itself (or not at all). A stop that fills at once is one `Filled` line with
`(SL, ...)`. A modify that takes longer than 3 s is told as a trigger (nothing in the updates
tells the two apart), but each trigger-pending episode has its own record, so the real trigger
that follows is still told.

**After a pause.** OpenAlgo's proxy replays nothing when a client subscribes, so whatever happened
while nothing was listening (OpenAlgo, the proxy or this service restarting, a deploy, a dropped
connection) would never be told. So after every subscription, the day's first included, the
service reads OpenAlgo's order book and trade book once and runs each order through the same
rules, marked "(found after a pause in order alerts)" and timed when it was found. What was told
before stays silent. A pause longer than 10 s inside the window gets its own line when anything
was found, or when the book still could not be read after about three minutes of tries while the
stream stays up; that line says to open Orders in the app. A pause that ends before the market's
start (the 09:00 pre-open, or a special session's own start) gets a line only when something was
found: nothing trades before then, and a restart before the opening, such as a release being
installed, is routine. Otherwise only the journal has it. A drop during the tries keeps the
pause, so the next connection's line covers it from its start. When the window closes during
the tries they go on without the stream (they read only OpenAlgo's books), because nothing
after the close looks for that pause again: the day's last pause is told like any other, and a
stop during those tries sends it as not read. A message still on its way when the service
stops is waited for (up to 5 s). The moment the stream was last up is kept in `alerts.db`, so a
restart knows its pause; the first start of a day counts from the window's opening.
- OpenAlgo's order book for Upstox carries no fill price or rejection reason: fills and prices
  come from the trade book, and a rejection found this way points to the Upstox app for the reason.
- While OpenAlgo is in Analyze mode, its REST books answer from the sandbox, so the live book
  cannot be checked from here; the pause line then says so and points to the Upstox app.

## When it runs

Connected on trading days from 08:45 to 16:00 IST, with the calendar auto-login uses
(`sudarshan/autologin/schedule.py`): weekends and the dates in `/etc/sudarshan/nse_holidays.txt`
are closed, a date in `/etc/sudarshan/nse_extra_trading_days.txt` is open. Both files are read
again when they change. Outside the window the process stays up, disconnected, and connects again
at the next opening. The MCX evening session is not covered.

A special session gets its own window after the date: the exchange's session as its circulars
give it, from the start of the pre-open to the F&O close. For the Muhurat session on Sunday
8 Nov 2026 (NSE circulars 149/2026 and 142/2026), as `nse-calendar.conf` ships it:

```
2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)
```

The alerts keep the margins 08:45-16:00 keeps around an ordinary session (09:00-15:40): 15 minutes
before its start and 20 after its end, within the day. So that Sunday they are connected from
13:50 to 16:00, and an update after the close, such as the exchange cancelling an open order, is
told as on any day. On a weekday that trades anyway, the session's window is added to 08:45-16:00
(an evening session gives a second window; the order book read when it opens covers the hours in
between). The next trading day's catch-up counts its pause from that day's opening and reads the
order book Upstox gives that day, so it is the margins, not the catch-up, that cover the special
day's close.

Auto-login reads only the date of such a line, and the deploy tool only the date at its start, so
both work as before (a test holds each to it). A release ships these lines in
`sudarshan/ops/deploy/nse-calendar.conf` (see `sudarshan/README.md`). A listed day with no window
keeps 08:45-16:00, so a session outside those hours gets no alerts until its window is shipped.

**Upstox connections.** OpenAlgo's proxy keeps one pooled Upstox market-data connection per user
(the daily access token), opened when the first of the owner's clients signs in and kept while any
of them is connected. So from 08:45 to 16:00 this service keeps that connection open, even with no
screen open. It is the same one the owner's real-time screens use, within the proxy's budget of at
most 2. Before the day's Upstox login the proxy answers the sign-in with a broker error but still
subscribes this service to orders; the updates start with the login.

## Server notices (M18.x)

The owner is told, in one short message, what the server did with a release: **installed** (and
that a backup was taken), **could not be installed and was rolled back** (with the cause in plain
words, and whether the earlier version is running), a background service file the unit policy
**refused**, and a release that is **waiting** for the market to close. Examples:

```
सुदर्शन / SUDARSHAN
v0.3.3 installed at 18:52 IST. A backup was taken.
```

```
सुदर्शन / SUDARSHAN
Could not install v0.3.3 at 18:52 IST: the app did not come up healthy, so v0.3.2 is back. Nothing for you to do; the release will be looked at.
```

The deploy tool runs as root and holds no Telegram or OpenAlgo secret, so it only **records** each
fact as a line in `/var/lib/sudarshan/events.jsonl` (the format is in `sudarshan/ops/events.py`);
`notices.py` here looks at that file (and `/var/lib/sudarshan-backup/events.jsonl`, where the
nightly backup job records) every 20 seconds and sends through the same path as the order alerts.

- **Once.** Each notice is claimed in `alerts.db` by its event id before the message goes out, and
  marked sent after it; a restart, a second look, a second service on the same record or a
  rewritten file never repeats it. A message that could not go out gives its claim back and is
  tried again with growing pauses (30 s to 10 minutes); one Telegram did not confirm is given up
  after three tries; one OpenAlgo refuses for good (its bot stopped, the chat not linked) is
  dropped, not held back for later.
- **One message for a run.** Nothing goes out until 45 seconds have passed without a new notice,
  measured on this service's own clock, so an install and the service files it refused are one
  message. Everything waiting at a look goes out together: a service that was down for hours
  tells the owner once, in order, not once per notice (at most 20 notices a message).
- **Not after downtime.** A notice more than 12 hours old is history: it is marked and never sent.
- **A release that fails again** at each of the deploy timer's 10-minute runs is told once per
  cause, not once per try (the event id names the release, the cause and whether the earlier
  version is running).
- **Any time of day.** Notices do not depend on the alert window or on the market: a release is
  installed in the evening or at the weekend, so the owner's key is read when a notice is due.
- **Words for a trader.** Only a release tag (`vA.B.C`) and a service file name from the deploy
  tool's own pattern are ever repeated from a fact; nothing else in the file reaches the message,
  and the Markdown characters Telegram would act on never do.

`SUDARSHAN_ORDER_ALERTS=off` also turns these off (the service exits). `SUDARSHAN_NOTICE_SPOOLS`
(below) names the files to read. The first run of the deploy tool that records notices also tells
the install of the tool before it, which made no record, once, if it was within the last 12
hours; so v0.3.3's own install is told by the next timer run after it.

## Health page (M18.1)

```bash
curl -s 127.0.0.1:5582/health | python3 -m json.tool
```

Local clients only (bound to loopback; other peers and browser requests carrying an `Origin`
header get 403); use `127.0.0.1`, not `localhost`. It is read-only and carries no key, user name or
message text.

| field | meaning |
|---|---|
| `release` | the release the deploy tool installed, such as `v0.3.3`, read from `/var/lib/sudarshan/deployed_tag` at every request; `null` when that file is missing or holds anything but a tag |
| `version` | this service's own code version, not the release: it does not move with a release |
| `state`, `detail` | what it is doing now: `closed` outside the alert window, `connected` while order updates arrive, `connecting`, `backoff` after a drop, `waiting` for the owner's key, `starting` |
| `window` | the alert window (`08:45-16:00 IST`), or `always on` |
| `owner_ready`, `owner_problem` | whether the owner's OpenAlgo key was found; if not, why: `no_user`, `no_key`, `unreadable_key` or `database` |
| `stats` | connections, order updates and alerts of the day's window; counted again from zero when the window closes |
| `telegram` | messages and lines sent, dropped and failed, over the same period; `null` before the sender starts |

A port that is taken is logged ("The health page could not start ...") and the order alerts run
without the page. `SUDARSHAN_ALERTS_HEALTH_PORT=off` switches it off; a different number moves it
(the feed holds 5580 and 5581).

## Turning it off

To stop all messages:

```bash
echo 'SUDARSHAN_ORDER_ALERTS=off' | sudo tee -a /etc/sudarshan/alerts.env
sudo chown root:sudarshan /etc/sudarshan/alerts.env && sudo chmod 640 /etc/sudarshan/alerts.env
sudo systemctl restart sudarshan-alerts
```

The service then exits at once without reading any other setting and without contacting OpenAlgo,
and stays stopped. Remove the line and restart to turn it on again. Stopping OpenAlgo's Telegram
bot also stops these messages (OpenAlgo refuses to send while its bot is stopped); messages refused
that way are dropped, not held back for later. `systemctl stop` alone is not lasting: the deploy
tool starts ring services again after each release.

| variable | default | meaning |
|---|---|---|
| `SUDARSHAN_ORDER_ALERTS` | `on` | `off`: the service exits; nothing is sent and OpenAlgo is not contacted |
| `SUDARSHAN_ORDER_ALERTS_CANCELLED` | `all` | `part-filled`: only cancellations of an order that was part filled |
| `SUDARSHAN_ORDER_ALERTS_SANDBOX` | `off` | `on`: Analyze-mode (sandbox) orders too, each line starting with "Sandbox" |
| `SUDARSHAN_ALERTS_WINDOW` | `08:45-16:00` | IST, on trading days; a special day's own window gets the same margins around it. A value that is not HH:MM-HH:MM (the start first) logs a warning and the usual hours are used |
| `SUDARSHAN_ALERTS_ALWAYS_ON` | `0` | `1`: ignore the window and the calendar (tests, an evening sandbox check) |
| `SUDARSHAN_ALERTS_STATE_DIR` | systemd's `$STATE_DIRECTORY` | `/var/lib/sudarshan-alerts` |
| `SUDARSHAN_NOTICE_SPOOLS` | the deploy tool's and the backup job's files | colon-separated files the server's notices are read from (`/var/lib/sudarshan/events.jsonl:/var/lib/sudarshan-backup/events.jsonl`) |
| `SUDARSHAN_ALERTS_HEALTH_PORT` | `5582` | the health page's port on 127.0.0.1; `off` switches the page off, `0` lets the system pick (tests) |
| `SUDARSHAN_DEPLOYED_TAG_FILE` | `/var/lib/sudarshan/deployed_tag` | where the deploy tool records the installed release; the health page shows it as `release` |
| `SUDARSHAN_ALERTS_LOG_LEVEL` | `INFO` | |
| `SUDARSHAN_NSE_HOLIDAYS_FILE` | `/etc/sudarshan/nse_holidays.txt` | shared with auto-login and the feed |
| `SUDARSHAN_NSE_EXTRA_DAYS_FILE` | `/etc/sudarshan/nse_extra_trading_days.txt` | |
| `SUDARSHAN_OPENALGO_DIR` | the checkout | OpenAlgo's directory (`.env`, `db/`) |
| `SUDARSHAN_OPENALGO_ENV_FILE` | `<dir>/.env` | |
| `SUDARSHAN_OPENALGO_URL` | `http://127.0.0.1:5000` | loopback only (the key travels on it) |
| `SUDARSHAN_OPENALGO_WS_URL` | `ws://127.0.0.1:8765` | loopback only |

Settings go in `/etc/sudarshan/alerts.env` (root:sudarshan 640); a missing file means the
defaults; a change needs `sudo systemctl restart sudarshan-alerts`. A value the service cannot
use stops it with a plain line in the journal and exit status 2, which systemd does not retry:
fix the file, then restart. After a crash systemd restarts it every 10 s.

## The API key and the trust boundary

The service needs the owner's OpenAlgo API key to sign in to the proxy, to read the order book
after a pause, and to ask OpenAlgo to send a Telegram message. Nobody pastes it anywhere. The
service reads it from openalgo.db exactly as OpenAlgo's TradingView page does
(`user_db.find_user_by_username`, `auth_db.get_api_key`, `auth_db.get_api_key_for_tradingview`),
decrypting it with the Fernet key OpenAlgo derives from `API_KEY_PEPPER` and `FERNET_SALT`.

- **Read-only.** The unit may write only `/var/lib/sudarshan-alerts`. openalgo.db is in WAL mode,
  which SQLite cannot open read-only unless its `-wal` file exists or can be created. So each
  read picks its way: with a `-wal` file present it opens the database read-only (`mode=ro`) and
  sees the newest data; without one, the last connection to close has written everything back, and
  it reads the main file as it is (`immutable=1`), creating nothing. If the file or its log changed
  during such a lock-free read, it is read once more; a read that fails is tried the other way
  (owner.py). OpenAlgo's own error tracebacks for a read cut short are silenced; the service logs
  one plain line instead.
- **Fresh.** The key is read again before every connection attempt and after OpenAlgo refuses it,
  so a regenerated key is used from the next reconnect. A failed read keeps the key in use. No key
  yet: one line in the journal, no message, and it starts by itself once a key is generated (it
  looks again every minute).
- **Only to OpenAlgo.** Before the key goes to 127.0.0.1:8765 or :5000, the service checks in
  `/proc/net/tcp` and `tcp6` that the listening socket on that port belongs to its own user
  (OpenAlgo runs as the same `sudarshan` user); otherwise it refuses with a plain line in the
  journal. This narrows, not closes, the moment in which another local program could take the port
  while OpenAlgo is down. The WebSocket client follows no redirect, and the HTTP client neither.
- **What it holds.** The key is order-capable. The service uses it for those calls only and never
  calls an order endpoint. It is never logged, printed or written anywhere, and the unit allows
  network traffic to this server only (`IPAddressDeny=any`, `IPAddressAllow=localhost`).
- **What it could already reach.** It runs as `sudarshan`, the user OpenAlgo runs as, so it gains
  nothing that user could not already read: OpenAlgo's `.env` and databases. It takes three values
  from `.env` (the pepper, the salt and `DATABASE_URL`) and skips the rest: OpenAlgo's logging
  module would load the whole file (broker secrets included) into the environment when imported,
  and that load is turned off here (a test in a fresh interpreter holds it). Family members'
  services must never run as this user.
- **Telegram.** OpenAlgo looks up the chat linked to the owner's username and sends with its own
  bot: this service never sees the bot token or a chat id and cannot message anyone else.
  OpenAlgo's per-user notification switch on its Telegram page is not consulted by
  `/api/v1/telegram/notify`, and not by this service either.
- **Upstream, not changed here.** OpenAlgo's ZeroMQ bus on 127.0.0.1:5555 accepts a publisher from
  any local user, so a local program could put a made-up order update on it, and this service would
  tell it like a real one. The fix belongs in OpenAlgo (an authenticated bus or a Unix socket).

## Reliability

- **Once only.** Each alert is claimed in `alerts.db` by (order id, kind, filled quantity) before
  it is queued. A reconnect, a restart, a replay, the order book after a pause, or the same update
  from both the order stream and a webhook finds the claim and stays silent. Nothing is sent for an
  order after its final state, and a part fill only when more is filled than last time. Records
  older than 7 days are pruned at start-up and when the window closes.
- **If the record fails** (a full disk, a state directory that cannot be written): the service
  goes on with a bounded copy in memory, one ERROR line says why, and alerts are still sent;
  repeats are held back for the rest of the run, only a restart might repeat earlier ones.
- **Reconnects.** Backoff with jitter, 2 s doubling to 60 s, reset only after a connection stayed
  up 60 s; a refused key waits 60 s and is read again.
- **Pace.** At least 1.1 s between messages and at most 20 a minute (Telegram asks for about one a
  second per chat; OpenAlgo allows `/notify` 30 times a minute). A message waits 0.3 s after the
  first alert of a burst, so the burst goes out as one.
- **Delivery.** OpenAlgo is asked to wait for Telegram's answer. Its "queued for delivery" answer
  means Telegram did not take the message (nothing in OpenAlgo sends that queue later), so it is
  tried up to 3 times; any other success counts as delivered. If OpenAlgo's wait had timed out
  after Telegram had in fact delivered, the owner may see it twice (rare). When OpenAlgo cannot be
  reached, messages are retried for up to 30 minutes, then dropped with a line in the journal.
  Telegram bot stopped, or the owner's account not linked to a chat: dropped, with one line in the
  journal every 10 minutes.
- **Bounded.** At most 300 orders wait to be sent (beyond that the oldest are dropped and the next
  message says how many could not be listed), at most 200 stop orders wait out the trigger check,
  one proxy message is at most 1 MiB and at most 64 wait to be read. Memory limit 256 MB.
- **On stop** (SIGTERM, a deploy): the stream closes cleanly within a second, waiting alerts get
  5 s to go out. A crash loses only alerts that were waiting to be sent (claimed, so never sent
  twice); normally that is less than a second's worth. The next start reads the order book.

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

- **`The health page could not start`**: another program holds port 5582. The alerts are not
  affected; set `SUDARSHAN_ALERTS_HEALTH_PORT` to a free port, or `off`.
- **`No OpenAlgo API key yet`**: generate one in OpenAlgo; the service picks it up within a minute.
- **`OpenAlgo's WebSocket proxy refused the API key`**: the key was regenerated; it is read again.
- **`OpenAlgo's WebSocket proxy is not reachable`**: OpenAlgo is starting or stopped; it retries.
- **`The API key is not sent ...: port ... is held by another user's program`**: something other
  than OpenAlgo listens on OpenAlgo's port. Find it with `sudo ss -ltnp`.
- **`No Telegram message: OpenAlgo's Telegram bot is stopped`** or **`... not linked`**: start
  the bot or link the owner's account on OpenAlgo's Telegram Bot page.
- **`The order book could not be read after the pause`**: OpenAlgo had no working Upstox login, or
  was busy; a pause longer than 10 s is then told to the owner as not checked.
- **`The alert record ... cannot be used`**: the state directory or the disk; alerts still go out.
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
- The order book and trade book after a pause, against a real account (the fields are read from
  OpenAlgo's code and its tests, not seen live).
- A "trigger pending" that arrived again after the order had left it (a duplicate from a webhook,
  out of order) would start a new episode, and a second trigger line could follow.

## Tests

`uv run pytest -q sudarshan/alerts` (no network: a fake WebSocket proxy and a fake OpenAlgo on
127.0.0.1, an openalgo.db written with OpenAlgo's own models, and runs of the real entry point as a
child process). Contract tests read OpenAlgo's own sources for every behaviour the fakes assume
(the proxy's sign-in and subscription, the Telegram answers, the order stream's statuses, the
books' sandbox marker), so an upstream change fails a test. The unit is checked against the deploy
tool's own `unit_ok` policy and with `systemd-analyze verify` when it is installed.
