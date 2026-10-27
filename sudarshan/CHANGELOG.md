# सुदर्शन / SUDARSHAN changelog

Releases of the `sudarshan` branch, newest first, written for the owner deciding whether to
publish. The owner publishes each release on GitHub. The server's deploy timer then installs it
outside 08:30-16:00 IST on trading days (09:00-15:45 until v0.3.1), keeps backups, and rolls
back if the app does not come up healthy.

## v0.3.1 (21 Oct 2026)

Phone cards for the books, and exits and cancels that cannot go twice. It includes everything in
v0.3.0, so it can be installed straight over v0.2.x.

### What changes for you
- **Phone cards for Positions, Orders, Holdings and Trades.** On a phone each row is a card, with
  the price and P&L always in view and no sideways scrolling. A wider screen keeps the table.
  - Positions: total P&L at the top, with booked and open when Upstox's figures add up (a
    position carried from an earlier day shows its total only), and Exit all. Tap a position for
    Exit, Add (with the position's own product), Chart and today's details. Closed positions sit
    below with what they booked.
  - Orders: filter by Open, Executed, Rejected or Cancelled. A fill or rejection that Upstox
    pushes shows on its card at once, with the filled quantity, the average price and Upstox's
    reason. Modify and Cancel from the order, and Cancel all. Cancelling a part-filled order
    names what is left.
  - Holdings: current value, invested, overall P&L and today's change. Buy more starts at one
    share and Sell at the whole holding, both as Delivery (CNC). Turning the phone keeps the
    ticket and what you typed.
  - Trades: each fill as a card; tap it for the chart.
  - The phone's Back button closes the open question or sheet first. Reopening the app never
    brings back a question you did not just ask for.
- **An exit or a cancel cannot go twice.**
  - Exit, Exit all, Cancel and Cancel all ask first, and send once however they are tapped.
  - After you confirm, the card says "Exit sent" and Exit waits until Upstox's book shows the
    position closed. A part close keeps it waiting and says what is left ("Exit sent · 65 of 130
    left"). While its own exit order is open, the card says "Exit order open · check Orders".
  - If no answer comes back, the card says "No answer yet: it may still be going through" and
    Exit waits up to 2½ minutes, or until the book shows what happened. If Upstox answered but
    the app could not confirm the order, it says "Not confirmed: check Orders before sending
    again" and waits 15 seconds.
  - When Exit is offered again after a wait, the question says what is known about the first
    exit: when it was sent, how much filled, or that it was rejected or cancelled.
  - The server also refuses a second exit for a position, from any screen or phone, while the
    first is still being placed.
  - Every Exit all takes its turn, whether it comes from the phone, TradingView, Telegram's
    /closeall, a script or a Flow: while one is being placed, another is refused with "Exit
    all is still being placed. Wait for it, then check Orders." and Telegram tells you. A
    single exit from the phone waits the same way while an Exit all runs.
  - The wide Positions table's Close and Close all wait the same way.
- **Exit all and Cancel all say what really happened.** "All Open Positions Squared Off" appears
  only when every position got an exit order. Otherwise you see how many were sent, refused
  (with the first reason) or not confirmed, in red or amber, and the cards stay held. If
  Upstox's positions or orders could not be read, it says nothing was sent.
- **Taps on something that just opened are ignored.** A sheet, a question or an order ticket
  ignores taps for half a second after it opens, and its buttons look dimmed meanwhile, so a
  double tap never presses a button that appeared under your finger. Escape still closes.
- **Upstox's own rejection reason** shows in the order book, on the cards, on the trading screen
  and in order alerts found after a pause. A rejection Upstox gives no reason for says so,
  instead of "rejected".
- **No raw errors for orders.** An order, exit or cancel that may have reached Upstox is never
  shown as failed or as error text: it says it is not confirmed and to check Orders.
- **Telegram says what really happened.** A failed or partial Exit all arrives as "Exit all did
  not close every position" with the reason, a Cancel all with nothing open as "No open orders
  to cancel", and a partial one as "Cancel all did not cancel every order". "Position Closed"
  and "All Orders Cancelled" now mean exactly that. /closeall replies show the app's sentence
  instead of "HTTP 424: ...".

### What you must do
- **Analyze-mode check of the new buttons.** With Analyze mode on, on the phone:
  - place one order each from a position's Add and a holding's Buy more, then cancel them from
    the Orders cards;
  - exit one sandbox position from its card, and use Exit all with two sandbox positions.

  If v0.3.0's checks (watchlist sheet and phone chart orders) were not done yet, do them in the
  same session.

### What is refused now
- A second Exit for a position while the first is being placed or has not shown in the book:
  up to 2½ minutes with no answer, 15 seconds after an unconfirmed answer, and while its own
  exit order is open. A second Cancel of one order, the same way.
- A tap in the first half second after a sheet, a question or an order ticket opens.
- A second Exit all, from any surface, while one is being placed. While any Exit all runs (a
  few seconds, or minutes when Upstox is slow) no other exit from the app, TradingView,
  Telegram or a script goes through; the Upstox app still works.
- **What API callers (TradingView, scripts, Flow) now get:**
  - `closeposition`: a failed positions read answers 424 "Nothing was sent" (it said 200 "No
    Open Positions Found"); a partial result answers 400, 429 or 502 with the counts (it said
    200 "squared off"); an Exit all while another exit is being placed answers 409 with the
    sentence. Success is unchanged.
  - `cancelallorder`: an unreadable order book answers 424; failures answer 400 or 502 with the
    counts.
  - `cancelorder`: a timeout or an Upstox server error answers 502 "not confirmed" (it was 400
    with the error text).

### Not in this release yet
- **Filled quantity after a reload.** Upstox's order book, as OpenAlgo reads it, does not carry
  the filled quantity or the average price yet; adding them first needs the strategy module's
  order status to keep a part fill's price. Until then a part fill shows from Upstox's live
  update, and after a reload a working order shows its quantity alone. A stop order shows
  TRIGGER PENDING once Upstox's live update has said so, and OPEN after a reload until the next
  update.
- **An Exit all that nobody is watching can be refused.** An Exit all from a TradingView alert,
  a script or a Flow that arrives in the second the phone is placing a single exit gets 409,
  and nothing sends it again: the other positions stay open. Telegram tells you ("Exit all did
  not close every position"). The next release makes it wait for that exit instead.
- **Telegram's alert for a Cancel all that could not read the order book** does not yet say
  why.
- **Other exits not yet held against an Exit all:** single-position orders from TradingView or
  scripts, the strategy module's own exits, the scalping terminal's close buttons, and Flow's
  order nodes.
- **The trading screen's own Close** and the wide order table's Cancel do not show the held state
  yet (the server still refuses an overlapping exit). Modify after a timeout still reads as
  refused.
- **Holds belong to one browser tab.** Another tab or phone is stopped only by the server, and
  only while the first exit is still being placed.
- **Not yet seen on the live Upstox account:** Upstox's rejection reasons and position details
  (booked, open, today's buys and sells, carried quantity) in its books.

## v0.3.0 (21 Oct 2026)

Everything moves with the live feed, the way Kite, Dhan, Upstox and Fyers do. This release
includes everything in v0.2.1, so you can skip v0.2.1.

### What changes for you
- **Watchlist, first on the phone.**
  - The bottom bar is now Watchlist, Dashboard, Orders, Positions. Tradebook is in the menu.
  - Your watchlists from the trading screen show as tabs.
  - Prices move with every tick. The last move shows as a green up or red down arrow, and the
    day's change is shown in rupees and percent.
  - You can make, rename and delete lists, add symbols by search, and move or remove them.
  - Tap a symbol to buy or sell it, open its chart or option chain, or watch live market depth.
    The phone's Back button closes what is open before it leaves the page.
- **Order ticket product.** Every order ticket (watchlist and chart) opens with the product you
  last picked for that segment on this phone. Until you pick one it opens with Intraday (MIS) for
  F&O, commodity and currency, and Delivery (CNC) for shares. You can still change it in the
  ticket for one order.
- **Live charts.** On the trading screen and on the new phone chart:
  - The candle moves with every tick, and a new candle opens on time by the exchange's clock,
    even if your phone's clock is wrong.
  - Day, week and month candles are live. Volume comes from the day's total.
  - A quiet market shows one flat candle at the end.
  - On an hourly chart the last candle says "Closes 15:30".
  - The phone chart opens from the menu (Chart), or by tapping a symbol in Watchlist,
    Positions, Orders, Holdings or Trades, or an index tile on the dashboard.
  - Hold a finger on a candle to read it. Buy and Sell are on the same screen. "Keep screen on"
    stops the phone sleeping while you watch.
- **Positions, Holdings, Orders, Trades, Scalping and PnL Tracker are live.**
  - They change the moment Upstox reports a fill, part fill, rejection or cancel, with a short
    message and a soft sound. You can mute the sound under Profile > Display.
  - Prices and P&L move with every tick. P&L starts from Upstox's own figure, so profit you
    have already booked stays in it.
  - A page you are not looking at asks Upstox nothing until you come back.
- **Order alerts on Telegram.**
  - You get a message for a fill, a part fill (how many of how many), a rejection with the
    broker's words, a cancellation and a stop order's trigger, once each.
  - After any pause (a restart or an update) the order book is checked again. Anything missed is
    sent, marked "(found after a pause in order alerts)".
  - They run on trading days from 08:45 to 16:00, and are on by default.
- **Status words on every live screen:**
  - "Real-time" while prices arrive.
  - "Stale · updated Ns ago" when they stop during market hours.
  - "Reconnecting" while the connection is down.
  - "Delayed" while prices are fetched without the live connection.
  - "Market closed" after the close.

  Old figures are greyed and never shown as current. The green LIVE chip at the top still
  means your orders go to Upstox, not the sandbox.
- **Complete live data from Upstox.** Volume, previous close, open interest, IV and greeks now
  arrive with each tick, so change and % change use the previous close. On Upstox they read
  about 0% before. Option chain OI and volume no longer wait 30 seconds.
- **Phone wake and weak networks.**
  - The Upstox price connection is kept for 5 minutes after your last screen closes, so prices
    come back at once when the phone wakes.
  - A slow phone gets the newest prices instead of a backlog, and never delays your other
    screens.
- **Fewer Upstox calls.** A burst of 20 fills costs about 72 calls instead of about 149.

### What you must do
- **Server settings: nothing by hand.** The update sets the 5-minute connection hold and
  limits live prices to one Upstox connection. It does not overwrite a value you set yourself.
  - If v0.2.1 was skipped, these two settings take effect from the update after this one.
  - Until then the connection still stays open during market hours, because Telegram alerts
    keep it in use.
- **Sound.** Tap the screen once after opening the app, so the order sound can play.
- **Analyze-mode check.** Place one order from the watchlist sheet and one from the phone chart,
  then cancel them, to check the two new ways into the order ticket.

### What is refused now
- A screen that has taken nothing for 10 seconds while the server was free is disconnected. It
  reconnects by itself with fresh prices.
- Prices are no longer re-read on a timer while the live feed works.

### Not in this release yet
- **Phone cards.** Positions, Orders, Holdings and Trades still use the wide table on a phone.
  Cards like the broker apps come next.
- **Live tools.** The option tools (OI tracker, GEX, IV smile, straddle) are not live yet.
- **Watchlist extras.** Built-in preset lists (NIFTY 50 and others), drag to reorder, and groups.
- **Product in the ticket.** A product changed inside the order ticket is not remembered; only
  the watchlist sheet's choice is.
- **Not yet seen on the live Upstox feed.** The first trading session after this update is the
  live check for:
  - the new tick fields;
  - the price-only switch when depth closes;
  - the 09:15 candle;
  - part fills on Telegram;
  - how far Upstox's order book lags its push.

## v0.2.1 (20 Oct 2026)

### What changes for you
- **Auto login takes the Upstox QR code.** Upstox shows TOTP setup only as a QR code. On the Auto
  login page you can now paste the whole text your phone camera copies from that QR code (it
  starts with otpauth://), or the key if Upstox shows one. Only the key inside is saved.
- **Upstox's own steps.** The help card on that page gives the steps from Upstox's help pages,
  in English and Gujarati.
- **Settings arrive with an update.** When a later update needs one of OpenAlgo's settings
  changed, the update makes the change itself, so you never edit the server's settings file. A
  value you chose yourself is never overwritten. The old file is kept with that update's backup
  and put back if the update rolls back. This takes effect from the update after this one; the
  next one (v0.3.0) uses it to keep the Upstox price connection through a phone screen-off.

### What is refused now
- QR code text for a non-standard code (not 6 digits every 30 seconds) is refused, because the
  server could not produce the right codes from it.

## v0.2.0 (20 Oct 2026)

### What changes for you
- **New look (design "D"), light theme only.**
  - Colours: peacock blue (#0E5A73) and gold (#E3A21A).
  - App icon: still the "सु" letter, now on peacock with a gold ring.
  - Name: सुदर्शन is drawn with the same letters as the icon.
  - Fonts: Barlow for text, Khand for headings and large figures.
  - Gains are green with an up arrow and a plus sign. Losses are red with a down arrow and a minus sign.
- **Dashboard, live.** It shows:
  - today's P&L (realised plus unrealised). The unrealised part moves with every price tick of
    your open positions. It is checked against Upstox's own figures whenever an order is placed,
    fills or is rejected, and every 45 seconds while the market is open;
  - funds available, and how much margin is in use, updated with those checks;
  - NIFTY 50, NIFTY BANK and INDIA VIX, moving tick by tick;
  - a holdings summary.

  "Real-time" beside the figures means prices are arriving. (The green LIVE chip at the top is
  different: it means orders go to Upstox, not to the sandbox.) If prices stop for 10 seconds
  while the market is open, it says "Stale" and how long ago the last update came, and the
  figures that should be moving turn grey (an index also gets a clock mark). After the close it
  says "Market closed". When the phone wakes up, or the price feed drops, the app reconnects by
  itself and says "Reconnecting" with the age of the figures until prices arrive again; it never
  says "Real-time" in that time. If reconnecting takes a while, prices are fetched every few
  seconds until the feed is back. All figures come from your account; nothing is sample data.
- **Login page.** New layout. If Chrome has saved your password, it can fill it in after your
  fingerprint: Chrome, then Settings, then Google Password Manager, then "Verify it's you to
  autofill passwords".
- **Auto login (optional, off until you switch it on).** Find it in the profile menu under Auto login.
  - Save your Upstox TOTP key and PIN once. They are stored encrypted on the server and never shown again.
  - On trading days the server then gets the Upstox token for you at about 08:35, and Telegram
    tells you whether it worked.
  - **Test now** shows the result immediately. It is refused from 09:00 to 15:30 while you are
    already connected, because it replaces the day's login.
- **Home-screen icon.** Your existing shortcut keeps its old icon. For the new one, remove the
  shortcut and add it again from Chrome (menu, then Add to Home screen). It still opens in Chrome.

### What is refused now
- After any rejected sign-in (wrong PIN or TOTP key), auto login pauses until you save the key or
  PIN again, so a wrong PIN is never retried by itself. Test now is refused while it is paused.
- Between 09:15 and 15:30, auto login signs in only when there is no working login (the stored
  one has expired, or you logged out). It never replaces a working session. When Upstox cannot
  confirm either way, it does not sign in and tells you.
- Updates are not installed at any time on a special trading day (a weekend or holiday session
  such as Muhurat trading) once that date is in the server's extra trading days list.

### Not in this release yet
- Watchlists, the screener and alerts. Their screens are designed and come next.
- The holdings summary on the dashboard is read when the page opens, not tick by tick yet.
- Positions, Tradebook and the other inner pages still use the older green and red shades.
- The login page does not show whether NSE is open, because that needs data that only exists
  after login.
- Passkey fingerprint login needs a domain name. On the IP address, Chrome's password manager
  with fingerprint is used instead.
- The auto-login TOTP endpoint and its error codes have not yet been seen live. The first
  **Test now** is the live check.

## v0.1.0 (20 Oct 2026)
- `sudarshan-feed`: one Upstox market-data connection (Analytics Token) that all SUDARSHAN
  services share. It opens at 08:58 and closes at 15:40 IST on trading days. There is no visible
  change in the app yet.

## v0.0.1 (20 Oct 2026)
- First release through the new pipeline. GitHub builds the app; the server installs it outside
  market hours with database backups and an automatic rollback.
