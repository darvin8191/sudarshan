# सुदर्शन / SUDARSHAN changelog

Releases of the `sudarshan` branch, newest first, written for the owner deciding whether to
publish. The owner publishes each release on GitHub. The server's deploy timer then installs it
outside 08:30-16:00 IST on trading days (09:00-15:45 until v0.3.1), keeps backups, and rolls
back if the app does not come up healthy.

## v0.3.3 (28 Oct 2026)

An Exit all that never closes a position twice, and the phone and Telegram fixes from the
Analyze-mode check on 27 Oct. It includes everything in v0.3.2. **Publish it by Friday 6 Nov, and
publish nothing on Saturday 7 or Sunday 8 Nov.**

### What changes for you
- **Exit all closes what it can at once and says what it left.** It reads Upstox's positions and
  orders once, then sends every exit together, except for a position that is busy:
  - an exit for it is being placed, or was sent (or may have been) in the last 15 seconds, from any
    screen, a TradingView alert, a script, Flow or an earlier Exit all; or
  - an order on its closing side is still working at Upstox, such as a stop-loss or a target.
    Exit all leaves that position and names the order, for example "NIFTY 25900 CE has a SELL 65
    order (order ending 4821, placed 10:42:20) working to close it, so no exit was sent for it.
    Cancel it in Orders, then exit." An order that covers part of the position holds back the
    whole exit, and the sentence says so.
  - Each position left out gets its own sentence, on the phone and on Telegram (Telegram names at
    most 10, then a count). If Upstox's order book cannot be read, or does not answer in time
    (about 5 seconds), Exit all goes ahead and says it could not check for working orders.
  - Exit all no longer waits for another exit to finish.
- **On the phone, each position settles by its own outcome.** A position whose exit was sent stays
  held until Positions shows it closed, or until 15 seconds after its own order has ended, when
  Exit is offered again with what is known; one that was left out lets go at once and shows why
  for a minute.
- **One position's exit is refused while that position is busy,** with the same kind of sentence:
  the card's Exit, the wide table's Close, and an exit sent as a smart order to position size 0
  (TradingView, scripts). An order that opens or adds to a position is never refused.
- **Flow's "Close Positions" with a symbol closes only that position.** It closed every open
  position in live.
- **Telegram says what was sent.**
  - An exit reads "Exit sent" over "RELIANCE (NSE, CNC), sell 1 to close" and its order id. It used
    to say "Action: BUY, Quantity: 0".
  - "Exit not sent" or "Exit not confirmed" comes with the reason, and "No exit sent" when nothing
    was open.
  - A smart order from TradingView or a script that the exit protection refused now gets a "Smart
    order not sent" alert with the reason. It used to be silent.
  - A Cancel all that could not read the orders says why.
- **Nothing held, nothing waiting.** With no open position, the screens no longer show "Waiting
  for prices".
- **Exit all and Cancel all look unavailable when there is nothing to do,** and say why when
  tapped: "Nothing is open to exit.", "Nothing is open to cancel.", "Reading the order book." or
  "The order book could not be read just now."
- **Analyze mode's own errors read as the app's sentences,** for example when the sandbox's
  database is busy: "not confirmed" when the order may have been saved, a refusal when nothing was
  sent.

### What you must do
- **Publish by Friday 6 Nov** (the link is in your task list). The server installs it outside
  08:30-16:00 by itself.
- **After it installs, one Analyze-mode session on the phone** (about 10 minutes, during market
  hours). It also finishes v0.3.1's checks:
  1. Take two positions and Exit one from its card: the card says "Exit sent", and Exit all is
     unavailable (it says why when tapped) until Positions shows that one closed. Then Exit all
     closes the other. Analyze mode fills at once and keeps no notes, so the "already on its way"
     protection shows only in Live; it is proven on a real server worker in the tests.
  2. Add 1 to a position: the ticket opens with the position's own product.
  3. Place a LIMIT buy far below the price, change its price with Modify on the Orders card, then
     Cancel it. Place another far LIMIT and use Cancel all.
  4. On Telegram, the exit reads "sell ... to close".
  5. Buy more on a holding. In Analyze mode a holding appears the day after a CNC buy, so buy one
     share on the day before.

### What is refused now
- An exit for one position while that position is busy: the card's Exit, the table's Close, and a
  smart order to position size 0 from TradingView or a script. It answers 409 with the sentence
  and sends nothing.
- Exit all no longer exits a busy position or one with a working order on its closing side; it
  reports it instead.
- **What API callers get:**
  - Live `closeposition` answers carry a new `positions` list. Each position is either sent (with
    its order ids), not confirmed, or not sent (with the reason). Analyze-mode answers have none.
  - A refused smart order to position size 0 answers 409 with the sentence.

### Not in this release yet
- **A scale-out is not checked as an exit.** A smart order to a smaller non-zero size, for
  example from TradingView, is treated as an entry. It is neither checked nor noted as an exit,
  as in v0.3.2.
- **A single exit does not read Upstox's order book.** After a part-filled exit, a second phone
  or a TradingView alert more than 15 seconds later can send an exit for the rest while the first
  is still working.
- **A single exit leaves your own stop-loss or target working**, as Upstox's own exit does.
  Cancel that order in Orders after you exit.
- **A short entry sent right after an Exit all** can be sized from Upstox's lagging book
  (upstream behaviour).
- **Not covered by this protection:** the scalping terminal's close buttons and the strategy
  module's exits.
- **A GTT stop is not seen by Exit all.** Upstox keeps GTT orders out of its order book until they
  trigger, so Exit all neither sees nor skips them, and a GTT stop can later fill against a closed
  position. Cancel GTTs (Orders, GTT tab) before or after Exit all.
- **A slow Upstox or a full order limiter** (500 orders in a minute or 2,000 in 30 minutes) can
  make an Exit all outlast the phone's hold. The server still refuses an overlapping exit.
- **While Exit all reads the order book**, another read from Upstox at that moment (a quote, a
  screen's re-sync, a smart order's positions read) can fail and is refused safely; orders go
  through a separate connection and are not affected.
- **A smart order whose answer never came back** (a TradingView or script exit that may have gone
  through) is still not told on Telegram; check Orders.
- **"Already on its way (sent ...)"** can name an exit that, a moment later, found nothing to close;
  the sentence's "exit it again only if it is still open" is the cue.
- **Telegram lists at most 10 positions** an Exit all left out; when more were left out, open
  Positions and Orders to see which ones need a working order cancelled.

## v0.3.2 (27 Oct 2026)

Muhurat trading on Sunday 8 Nov, and SUDARSHAN's own times moved to the F&O close at 15:40. It
includes everything in v0.3.1. **Publish it by Friday 6 Nov, and publish nothing on Saturday 7
or Sunday 8 Nov.**

### What changes for you
- **Muhurat, Sunday 8 Nov 2026.** SUDARSHAN treats that afternoon as a trading session, as the
  exchanges announced it: pre-open 14:05, trading 14:20 to 15:30 for shares, to 15:40 for F&O
  (NSE circulars 149/2026 and 142/2026), MCX 14:20 to 15:30.
  - The server learns the date by itself. This release carries the line, and the first run after
    the install adds it to the server's calendar. Nothing to type on the server.
  - That day: auto-login logs in at 08:35 as on any trading day, the price feed connects at 14:03
    and stays until 15:50, order alerts on Telegram run from 13:50 to 16:00, and the screens show
    the market open (not "Market closed") during the session.
  - No release installs on that Sunday at any hour.
- **F&O closes at 15:40 every day (NSE, since 3 Aug 2026).** SUDARSHAN's own times follow it:
  - The price feed stays until 15:50 (it closed at 15:40), and reconnects by itself if prices
    stop between 09:15 and 15:40.
  - Test now on the Auto login page is refused until 15:40, so it never replaces your login while
    F&O trades. A scheduled auto-login never replaces a working login before 15:40 either.
- **Releases install outside 08:30-16:00 on trading days** (it was 09:00-15:45). A release you
  publish during the day installs after 16:00; one published before 08:30 installs before the
  morning's logins and alerts start.
- **Calmer order-alert notices.** "Order alerts paused ..." no longer comes before the market
  opens (the one on 27 Oct at 08:45 came from v0.3.1's install). A pause during the session is
  still told; when the order book cannot be read, SUDARSHAN keeps trying for about 3 minutes and
  then tells you to open Orders in the app. The day's last pause is caught up after 16:00 too.
- **A mistyped time window in a server setting** now costs a warning in the log and the usual
  hours, never a service that does not start.

### What you must do
- **Publish by Friday 6 Nov** (the link is in your task list). The server installs it outside
  trading hours by itself.
- **After it installs**, paste the v0.3.2 check from your task list into the "SUDARSHAN server"
  session. It changes nothing; it confirms the Muhurat line reached the server's calendar and
  that the services run.

### What is refused now
- An install between 08:30 and 09:00 or between 15:45 and 16:00 on a trading day, and all day on
  8 Nov.
- Test now on the Auto login page between 15:30 and 15:40.

### Not in this release yet
- **The Exit all and phone-screen fixes found on 27 Oct** move to the next release, v0.3.3: an
  Exit all that never closes a position twice when it follows another exit within seconds, the
  Exit message on Telegram ("Action: BUY, Quantity: 0" for a sell), "Waiting for prices" with
  nothing open, and Exit all and Cancel all that look tappable with nothing to exit or cancel.
  Until then, after an exit, wait until Positions shows it closed before you tap Exit all.
- **A message when a release installs.** For now the v0.3.2 check in the server session confirms
  it.
- **An evening special session** (none is planned) would still allow Test now during it:
  auto-login's guard reads the clock, not that day's own hours.
- **MCX's Muhurat hours** come from a published summary of MCX's circular; MCX's own page could
  not be read.
- **Holidays changed on OpenAlgo's admin Holidays page** go back to the built-in list at the next
  install, as they always have.
- **The feed may reconnect once at 09:15** if Upstox sends nothing in the minutes before the
  open. It did not happen on 27 Oct (no reconnects all day).

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
