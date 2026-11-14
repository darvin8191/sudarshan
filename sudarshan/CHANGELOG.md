# सुदर्शन / SUDARSHAN changelog

Releases of the `sudarshan` branch, newest first, written for the owner deciding whether to
publish. The owner publishes each release on GitHub. The server's deploy timer then installs it
outside 08:30-16:00 IST on trading days (09:00-15:45 until v0.3.1), keeps backups, and rolls
back if the app does not come up healthy.

## v0.5.2 (14 Nov 2026)

A small security release on top of v0.5.1: NSE's freeze limits from 2 November, three security
fixes from OpenAlgo (the Telegram bot's /link, old logins on the market data helper pages, forged
order postbacks), and the kill switch's Telegram command checking the same link. It includes
everything in v0.5.1, and through it v0.5.0 and v0.4.1. **It is a security release, so by your
choice it may be published before v0.5.1 has run a trading day. v0.5.1's checks still apply: its
phone check after this install, and its first Live trading day's checks on Monday 16 Nov.**

### What changes for you
- **NSE's freeze limits from 2 November are set on install, and stay set.** NIFTY 1,800 (27 lots
  of 65), BANKNIFTY 900 (30 lots of 30), FINNIFTY 1,800, MIDCPNIFTY 2,800, NIFTYNXT50 600 and
  NIFTYFPI 8,500. v0.5.1 still had October's (NIFTY 3,510, BANKNIFTY 1,440), and every install put
  them back, a value typed in on the Freeze Quantities page included. Now:
  - the scalping terminal's Close, Close all and auto-exit split a NIFTY close above 27 lots
    (BANKNIFTY above 30) into orders the exchange accepts, where one order of 28 to 54 NIFTY lots
    (31 to 48 BANKNIFTY) was rejected and left the position open;
  - /trading refuses an order above the limit on screen, naming the limit, where the exchange
    rejected it after it was sent;
  - a limit you set yourself that NSE never published (1,755 for NIFTY, say) is kept, on this
    install and every later one.
- **The Telegram bot's /link checks the key on this server.** /link used to ask the server typed
  in the command whether the key was good, so anyone who found the bot could link a chat through a
  server of their own. That chat could then switch you between Live and Analyze (in Analyze your
  next orders go to the sandbox and report success) and stop your Python strategies. Now /link
  takes only this server's API key, checked before anything is sent anywhere. /mode, /stoppython,
  their buttons, "Close all + Stop strategies" and /killswitch check the chat's key again each
  time: a chat whose key is not this server's API key now is asked to link again, and nothing
  happens.
- **Old logins cannot read the market data helper pages.** The pages that give the app's screens
  your API key and the market data settings took any cookie with your user name in it, even one
  from before the 03:00 logout, and from midnight to 03:00 a login of any age passed every check.
  Now a login from before the most recent 03:00 is refused everywhere, at any hour; these pages
  answer it as signed out and end nothing. Your ordinary day is unchanged: a login after 03:00
  holds until the next 03:00, past midnight included.
- **Forged order postbacks are refused.** Anyone who could reach the server could post a made-up
  Upstox fill to its postback address. It went out as a live order update, which the strategy
  module applies to its orders and which can start a Flow workflow that places orders. Every
  Upstox postback is now refused before it is read. SUDARSHAN never used them: your order updates
  come through Upstox's order stream, which OpenAlgo opens itself at login, so order alerts, the
  books and the strategy module are unchanged.

### What you must do
- **Publish it** (the link is in your task list). On a Saturday the deploy timer installs it at
  once.
- **On the Telegram page (OpenAlgo, Telegram Bot), remove any linked chat that is not your own.**
  A chat a stranger linked before this release can no longer act on the server, but it stays in
  the list until you remove it, and while it is there your order alerts can go to it instead of
  you.
- **Send /link again only if you regenerated the API key since you linked.** Until you do, /mode,
  /stoppython and /killswitch ask you to link again. A chat linked with your current key needs
  nothing.
- **If your risk confirm phrase is still the shipped one, set your own on the Risk page.** The
  shipped phrase is in this public repository. Only you type the phrase, and once the guard checks
  Live, /killswitch exit with it cancels your orders and exits every position.
- **Check after the install (1 minute):** Freeze Quantities shows NIFTY 1800 and BANKNIFTY 900.
  If it does not, the install rolled back (the database goes back as it was), so keep single NIFTY
  orders within 27 lots and BANKNIFTY within 30 until it is installed. In Analyze mode, a 30-lot
  NIFTY scalping Close goes as 27 lots and 3.
- **Publish a release prepared before this one only once its notes say it includes v0.5.2.** One
  that does not puts the October limits back when it installs.

### What is refused now that used to be accepted
- **Telegram /link with a key that is not this server's API key**, before anything is sent.
- **/mode, /stoppython, their buttons, "Close all + Stop strategies" and /killswitch** from a chat
  whose stored key is not this server's API key now: asked to link again, nothing done.
- **A login from before the most recent 03:00**, between midnight and 03:00 as at any other hour:
  the market data helper pages answer it as signed out, the app's live connection refuses it, and
  any other page sends it to the login page.
- **Every Upstox order postback** (and Dhan's, Fyers', Angel One's and AliceBlue's): refused before
  it is read. Zerodha's, which carry a signature, are still checked and accepted.
- **An order above the freeze limit from /trading**: refused on screen, naming the limit. The
  exchange refused these anyway.
- Everything else goes as before. Your TradingView alerts, scripts and API calls use the API key
  and are not affected.

### Not in this release yet
- **The order ticket, Exit, Exit all, TradingView, scripts and the strategy module do not split at
  the freeze limit.** Each sends one order, so closing a NIFTY position over 27 lots (BANKNIFTY over
  30) that way is refused by Upstox, as it has been since 2 November. Close it in parts, or keep
  positions within those sizes.
- **A device signed out from another device, or a copied cookie, still counts as signed in until
  03:00**, as in v0.5.1, so it can still read the market data helper pages until then.
- **Between midnight and 03:00, a login refused for being old signs every device out.** A browser
  that still holds a login from before the previous 03:00 and opens a page in that window now ends
  the session on all your devices and the day's Upstox login in SUDARSHAN, up to three hours before
  the usual 03:00, where v0.5.1 let that old login in. Markets are closed then and Upstox's login
  ends at 03:30 anyway; the next morning's login is as usual.
- Everything under v0.5.1's "Not in this release yet" still stands.

## v0.5.1 (8 Nov 2026)

Live exits close what Upstox's positions do not show yet, reversals and the scalping and strategy
exits keep to the one-at-a-time rule, a close Upstox did not answer is said and held as not
confirmed, the risk guard gets the fixes it needs before it can check Live, and order updates
now reach only your own signed-in screens. It includes
everything in v0.5.0 and v0.4.1. **Publish it only after v0.4.1 has installed and run one trading
day on its own: Wednesday 11 Nov after the close, or later (Tuesday 10 Nov is a market holiday).
v0.5.0 is not published on its own: this release carries it to the server.**

### What changes for you
- **Order updates reach only your own signed-in screens.** The app's live connection used to send
  every order event (symbol, side, order id, product) to anyone who opened it, signed in or not.
  It now refuses a connection without your session, and sends order and alert events only to
  screens that passed that check. A refused or dropped connection is tried again after a short
  pause; while it is not connected the screen says "Order updates · Reconnecting" and never shows
  old updates as current. A screen left open past 03:00 goes to the login page by itself.
- **A logout on another device ends this one cleanly.** After you log out on the laptop, the phone
  says "You have been logged out from another device" and shows the login form within a few
  seconds, and stays on it after a reload of that page, where it used to bounce between the login
  page and the dashboard. It makes at most one automatic trip to the login page a minute, so it
  cannot bounce; a screen that learns within that minute that the session has ended shows "Order
  updates · Sign in again to get them" with a button.
- **Exit all closes a position Upstox's positions do not show yet.** A position bought seconds ago
  is in Upstox's order list before its positions list. Exit all used to name it and leave it to
  you; it now exits it from its fills (the contract, product and size from the order rows),
  through the same one-at-a-time rule and time limit as every other position. When one of those
  cannot be worked out, it is named as not closed, never counted as closed.
- **A product traded today that Upstox lists only in another product is still named, not sent.**
  A conversion in the Upstox app (Intraday to Delivery, say) whose new row is not shown yet cannot
  be told from a fresh position, and an exit sent in the old product would open the opposite one.
  Exit all says so and asks you to check Positions.
- **Exit all never says "squared off" over a position it could not see.** An order Upstox shows
  complete with no filled quantity is unknown, not 0: its position is named as not sized, where
  Exit all used to miss it. When Upstox's order list does not come in time and the positions show
  nothing open (closed positions at 0, or a sale from holdings), it says the order list was not
  checked. A reversal whose resting order still works is named, not passed over as flat.
- **A sale from holdings is left alone**, whether Upstox lists it or not and whether the order list
  came or not: never bought back, never named as a position to close. An F&O Delivery (NRML) short
  is still a position.
- **TradingView reversals and part exits** (a smart order to another size that reduces or reverses
  the position) now hold the position while they are placed, and the hold stays until Upstox's
  order list shows the order ended. A repeated reversal is refused while the first is being
  placed, is not yet seen ended, or a market order works on the side that closes the position, so
  it can no longer go beside a first one that part-filled and rests. Exit all leaves the position
  to it meanwhile. The refusal says this order was not sent and what to check before sending it
  again. One sent after the first is seen complete goes, sized from today's fills.
- **The scalping terminal and the strategy module keep to the one-at-a-time rule in Live**: the
  terminal's Close, Close all and trailing stop auto-exit, and the strategy module's exits (basket
  and signal). A close is refused while another exit of the position is being placed or an earlier
  one still works at Upstox, and the next exit from any surface waits for it. The order list is
  read only while an earlier exit is held, with the session the terminal's own order uses.
- **A close Upstox does not answer reads "not confirmed".** When Upstox times out on a scalping
  close, the terminal says "This exit is not confirmed: it may have gone through. Check Orders
  before sending it again." instead of the raw error. Close all leads with "No scalping close is
  confirmed." or "Closed N of M scalping positions." and names each position; "No scalping
  position was closed" only when every close was refused, and never "success" over a refusal.
  Such a close (the strategy module's too) holds the position for 150 s, then for as long as
  Upstox shows a market order working on its closing side or its order list cannot be read, up to
  16 h: both size the next close from Upstox's positions, which can show the first fill a minute
  late. The refusal says the
  earlier exit is not confirmed, and to close from the Upstox app only if that exit is not in
  Orders.
- **The strategy module records a held exit as a hold** ("held, to be sent again", at warn) on
  both the basket and the signal path, not as a rejection; the leg stays managed and the next
  tick, the pending stop or the next exit signal sends it.
- **The risk guard, still Analyze mode only.** These are the fixes Live waits for, and act only
  once the guard checks Live:
  - the gate judges an exit on the order list its caller has just read (one read for all of Exit
    all's exits), and only inside that call;
  - it reads a position Upstox's positions do not list yet from the day's fills, so the kill
    switch lets its exit go; but a product of an instrument listed in another product is read as
    the positions show it (it may be a conversion), so an order there is judged on the positions
    alone;
  - a market exit's claim lasts while Upstox shows the order working, never past 16 h; an
    entry's or a resting order's claim ends 150 s after its answer, as before;
  - with Upstox's order list unreadable and Exit all asked, the kill switch cancels nothing and
    sends nothing, so your stop-loss orders keep working, and looks for the order list again 2 s
    later; an Exit all that sent nothing because the order list did not come is tried once more;
    when the switch did cancel stops and Exit all then left a position with nothing working (not
    sized, out of time, or a position Upstox does not list that it could not size or that may be
    a conversion), it says that position is open with its stop-loss order cancelled and points to
    the Upstox app;
  - its words never deny a cancel that went before, and a cancel that may have reached Upstox
    reads "not confirmed";
  - when Upstox's positions cannot be read, the day's loss figure comes from a read at most 15 s
    old, not 150 s;
  - Upstox's answer that no order is open counts as nothing to cancel only when it is the whole
    answer;
  - a smart order to size 0 from flat is judged as sending nothing, as the order path sends;
  - an order Upstox shows with no filled quantity that has filled nothing (rejected, or waiting
    for validation or its trigger) no longer stops every exit.

### What you must do
- **Publish it** (the link is in your task list) on Wednesday 11 Nov after the close or later, once
  v0.4.1 has run a trading day. It brings v0.5.0 with it, so v0.5.0's checks apply as well.
- **Check on your phone after the install (2 minutes, after the close):** with the app open on the
  phone, log out on the laptop. The phone shows "You have been logged out from another device" and
  the login form within a few seconds, with no flicker, and is still on the form after a reload.
  Sign in again: the laptop's logout signs Upstox out too, so this includes the Upstox login; live
  order updates then come back without a reload. Then lock the phone for two minutes and unlock
  it: "Reconnecting" briefly, then live again.
- **After a scalping close says "not confirmed", check Orders before closing again.** SUDARSHAN
  holds that position for 150 s, and longer while a market order of yours works on its closing
  side or Upstox's order list cannot be read. To close it sooner, use the Upstox app, and only if
  the first exit is not in Orders.
- **On the first Live trading day, check and tell me:**
  1. how soon Upstox's order list shows an order just placed (it sets how often a repeated
     reversal, or Exit all over a fresh reversal, says "not confirmed");
  2. whether a market order with price protection part-fills and rests at Upstox, and for how long;
  3. one scalping close of a contract within 150 s of an app Exit of it: it should go once Orders
     shows the first one complete; and what the terminal says when Upstox is slow;
  4. Positions before and after converting one lot, and whether a closed position at 0 stays
     listed;
  5. whether Upstox's positions list a sale from holdings;
  6. which order statuses Upstox sends with no filled quantity, and that a part-filled order never
     shows as rejected;
  7. Upstox's answer to Cancel all when no order is open;
  8. how far Upstox's positions lag a fill (it sets the window in the last point under "Not in
     this release yet");
  9. MCX, for when the guard checks Live: its claim on a market exit sent in the evening is kept
     into the next morning, which costs one order list read then; the next morning's first MCX
     order should go as usual.

### What is refused now that used to be accepted
- **The app's live connection without your session**: refused, and a screen whose session has
  ended is sent to the login page. Your TradingView alerts, scripts and API calls are not
  affected: they use the API key, not this connection.
- **Live, Exit all** with the order list not in time and nothing open in the positions: it says
  the order list was not checked, where it used to answer "squared off". Nothing is sent either
  way.
- **Live, a TradingView reversal or part exit** while an exit or a reversal of the position is
  being placed, or is not yet seen ended in the order list: refused, with the reason. (One while a
  market order works on the closing side was refused already.)
- **Live, the scalping terminal's Close, Close all and auto-exit, and the strategy module's
  exits:** refused while another exit of the position is being placed or still works at Upstox,
  and for at least 150 s after one of theirs that Upstox did not answer. The trailing stop tries
  again every 3 s, the strategy module on its next tick.
- Everything else goes as before, and the sandbox is left alone on every surface. Newly sent: an
  Exit all of a position Upstox's positions do not list yet, sized from its fills.

### Not in this release yet
- **The guard still checks Analyze mode only.** Live waits for sessions S1 and S2 and the first
  Live day's checks above.
- **The scalping terminal and the strategy module still size their exits from their own books.**
  Once Upstox shows an earlier exit complete, the hold ends, and a close sent before Upstox's
  positions show that fill can close the position a second time. A close Upstox did not answer is
  held 150 s for this reason; one it answered is not.
- **The strategy module's log records an exit Upstox did not answer as rejected**, with the
  failure's own words; it is held and sent again as above.
- **Once the guard checks Live, a position Upstox does not list and that cannot be sized reads as
  flat**, so under the kill switch its exit is refused as opening; Exit all names it, and you
  close it from the Upstox app. The same holds for a product that may be a conversion.
- **The kill switch's stop warning does not count a position whose exit Upstox itself refused.**
- **A reversal back inside the positions' lag:** long 65, reversed to -65, reversed back to +65
  while Upstox's positions still show +65: the order reads as nothing to do and the account stays
  at -65. It was like this before; check 8 above sets how wide the window is.
- **Plain TradingView order alerts** are still not judged as exits, as in v0.4.1.
- **A logout on one device does not end a device that is not open at the time** (a phone locked
  for more than a minute counts), or one with a request still on its way: that device stays signed
  in until 03:00 unless it lands on the login page. Ending every session at once needs a separate
  change to how sessions end.
- **Two devices on the same home Wi-Fi:** the later sign-in replaces the session record of the
  device that signed in first, so that device keeps working but is asked to sign in again if it
  lands on the login page (for example from the home page's Login button).

## v0.5.0 (6 Nov 2026)

The Portfolio page: your Upstox account read-only, and portfolios you keep yourself. It includes
everything in v0.4.1. **It is not published on its own: v0.5.1 carries it to the server.**

### What changes for you
- **The Portfolio page, switched on by this release.** It never places or changes an order, and it
  reads Upstox only, with the read-only token. You can turn it off with `SUDARSHAN_PORTFOLIO` set to
  `off` in OpenAlgo's `.env`; later releases leave your value alone.
- **The Upstox tab:** holdings, positions, MTF positions and funds, read when you open the tab,
  after each order and on a slow re-sync while the market is open, and shown with how old they are. Funds cannot be read from midnight to 05:30; the tab says so
  instead of showing an error.
- **Your own tabs:** shares, ETFs, mutual funds, gold, deposits, anything with a value. Every figure
  comes from your entries and prices; nothing is typed in as a total. Buys, sells, dividends,
  interest, fees, transfers, splits, bonus shares and rights. Sales use FIFO for each account and
  asset. A tab shows value, invested, gain, today, XIRR, time-weighted return, allocation by class,
  sector, cap size, account, member and tag, filters, saved views and a layout of your own.
- **Import and export:** a generic CSV or a Zerodha tradebook, up to 900 KB and 5,000 rows. You see a
  preview first, then add the rows, and you can undo the whole import. The same file twice adds nothing.
  Export gives a CSV that reads back to the same entries.
- **Prices:** live from the feed for what you hold (the 200 most valuable holdings; the rest at their
  last closing record), mutual funds from AMFI's nightly list, and anything else at the value you
  entered, with a note when it is old.
- **History of the Upstox tab:** a closing record is taken 15 minutes after the close and settled the
  next morning. A part Upstox did not answer for is shown as not known, never as 0.

### What you must do
- **Publish v0.5.1, not this one:** v0.5.1 carries this release with it.
- **Check on your phone after the first trading day:**
  - the Upstox tab against the Upstox app: quantities, average prices, P&L, T1 quantity and the
    haircut;
  - one custom tab against your own FIFO arithmetic, with a buy, a part sale and a dividend;
  - an import of your own Zerodha tradebook: if its first row is not recognised, tell me the header
    row; then undo it, and import the same file again;
  - the next morning's history point for the Upstox tab;
  - the funds window: open the tab after midnight and read the sentence.
- **After the first night, check that mutual-fund prices arrived.** AMFI's file layout is read as
  documented and has not been fetched yet.

### What is refused now that used to be accepted
- Nothing existing changes. New: an import over 900 KB or 5,000 rows is refused with a sentence that
  says to split it; an entry that would sell more than is held is refused with the date it fails.

### Not in this release yet
- **The index history for the benchmark** and **fallback quotes** wait for the feed's request governor,
  which ships off. Until you turn it on, the benchmark fills day by day from the closing record and an
  unpriced holding is counted at its cost, with a note.
- **Open points from the review:**
  - the morning check of each holding's identity follows a symbol that another company reuses;
  - a candle never replaces a close already stored, and both indexes share one limit of 30 requests a
    run;
  - liabilities without a tab are not shown;
  - the PDF export, the dividend forecast and family sharing are not built.
- **The dashboard card** replaces the Holdings card one release later (your decision P5).

## v0.4.1 (4 Nov 2026)

Live exits that Upstox's order book decides, sized from today's fills; a smart order to size 0
that never opens a position; Telegram for Cancel all in Analyze mode; phone session fixes. It
includes everything in v0.4.0. **Publish it on Monday 9 Nov or later: never on Saturday 7 or
Sunday 8 Nov (Muhurat).**

### What changes for you
- **Live: one exit at a time for each position, until Upstox's order book shows it done.** After
  an exit is sent (a card, a sheet, Exit all or a TradingView exit), a second exit of the same
  position is refused while Upstox's order book shows the first one working. The message names
  that order and says to cancel or modify it in Orders. The hold ends when the book shows the
  order complete, cancelled or rejected, not after a fixed time. When the book cannot be read, it
  lasts up to 150 s and says the first exit is not confirmed.
- **Live exit sizes come from today's fills as well as the positions.** Upstox's positions can lag
  a fill by up to a minute. Every exit now reads the positions and then the order list, and
  corrects each size by the fills the positions have not shown yet. A position converted in the
  Upstox app (Intraday to Delivery, say) is exited in the product that now holds it.
- **A smart order to position size 0 never opens a position, in Live and in Analyze mode.** When
  the position is already flat, nothing is sent, the answer is "No open position found. Not
  placing exit order." and, in Live, Telegram tells you. If your own exit is still working at
  Upstox, the answer names that order instead, because it can still fill. This covers
  TradingView's own exit arriving after you exited by hand or a stop flattened the position, and
  the exit of an entry that never filled. TradingView's strategy alerts carry the size after the
  order, so their entries are never size 0. An exit sent for a product that holds nothing while
  another product does (TradingView set to Intraday, the position in Delivery) also sends nothing,
  and says where the position is.
- **A smart order that reduces or reverses a position is sized from the corrected figure**, so a
  lagging positions row can no longer make it sell more than you hold, and it waits while a market
  order on the closing side is still working, so a repeated reversal alert cannot double it. One
  that only adds is sized as before, without reading the order list.
- **A single exit waits while a market order on the closing side is working**, even one placed
  from the Upstox app or before a restart. A resting stop loss or target (SL, SL-M, LIMIT) does not
  block it. Exit all leaves such a position out and names the order.
- **Exit all** reads the positions and then the order list once, never waits, and lists every
  position it left out with the reason. A part-filled exit that was cancelled is sized from the
  order list, and the sending budget covers the wait at the 5-a-second pacer. A position traded
  seconds ago that Upstox's positions do not show yet is named as not closed, and the answer never
  says all positions were squared off while one is left.
- **Every exit message is a sentence.** Anything that may have reached Upstox reads "not
  confirmed", never a raw error, and Upstox's own refusal is shown without its error code.
- **Telegram tells you about Cancel all in Analyze mode**, like a single cancel. It used to stay
  silent (the v0.3.3 Analyze step).
- **Phone sessions:**
  - a session check asked before a logout no longer lands after it;
  - a check that finds no API key clears the saved one, so the screen says so;
  - a ready screen checks the session again when the phone wakes or the feed signs in again;
  - the broker TOTP login syncs the session before the first screen.
- **The Alerts page's disabled New button** reads in sunlight.
- **The risk guard, still Analyze mode only:** with Upstox's order book unreadable, the kill switch
  cancels without it and sends no market exit; a second exit waits while the first market exit is
  working; an exit nobody judged is judged by the last positions read; its history file upgrades
  cleanly from an older one.
- **A release build tries GitHub three times before it gives up.** v0.4.0's first build passed
  every test, but one GitHub error at the last step left it unmarked as the latest release, so
  the server did not install it until the build was run again.

### What you must do
- **Publish it** on Monday 9 Nov or later (the link is in your task list). The server installs it
  outside 08:30-16:00 by itself. If v0.4.0 is not installed yet, its steps (the backup check,
  sessions S1 and S2) apply as well.
- **When an exit says Upstox did not send your orders in time, close that position from the Upstox
  app** or try again in a minute. SUDARSHAN no longer guesses a size.
- **If any alert, script or Flow of yours sends position size 0 to open a position, change it to
  send the size after the order** (65 for a long, -65 for a short). TradingView strategy alerts
  made on the TradingView page already do. Flow's Smart Order node used to say "0=use quantity";
  it now says that 0 closes the position. A size-0 order is now only ever an exit.

### What is refused now that used to be accepted
- **Live, every exit SUDARSHAN sizes** (cards, sheets, Exit all, and TradingView smart orders that
  reduce or reverse a position):
  - when Upstox's order list does not come within 4 s (5 s for Exit all), nothing is sent, and
    Exit all sends nothing at all;
  - when the positions and the order list do not add up, nothing is sent for that position. Just
    after a conversion, with a fill the positions do not show yet, this lasts until they do;
  - a second exit of a position while the first is working at Upstox;
  - a single exit while a market order on the closing side is working.
- **Live and Analyze mode, smart orders to position size 0:**
  - from flat, whatever their side or quantity: nothing is sent. It used to be placed as a new
    order;
  - while an exit of that position is being sent or held: refused with the reason.
- **Live, a smart order that reduces or reverses** while a market order on the closing side is
  still working: refused with the reason.
- Smart orders that only add, and every order with a size other than 0 that finds nothing working,
  go as before.

### Not in this release yet
- **The guard still checks Analyze mode only.** Live waits for sessions S1 and S2 and four kill
  switch fixes found in this release's review.
- **Plain TradingView order alerts** (an ordinary order, not a smart order) are not judged as
  exits: a plain SELL after you exited by hand still opens a short. Keep the caution in your task
  list for those.
- **The scalping terminal's closes and the strategy module's exits** are not covered by the
  one-at-a-time rule.
- **Not measured yet, on the first Live trading day:** how long Upstox's order list takes to
  answer; how Upstox shows a market exit resting at its protected price (the market-order wait
  depends on it reading MARKET); the rows Upstox shows after a conversion; MCX quantities.
- **A late TradingView exit that sent nothing is logged as a failed order** in OpenAlgo's API log,
  as an exit with nothing open always was; nothing was sent.
- **Exit all does not close a position Upstox's positions do not show yet**; it names it, and you
  close it from the Upstox app or tap Exit all again a minute later.
- **Two identical reversal alerts in the same second can still both go.** The wait starts once
  Upstox's order list shows the first one working.
- **An offsite copy of the backups**, and the feed's REST governor, stay as in v0.4.0.

## v0.4.0 (4 Nov 2026)

The risk guard, tried in the sandbox first; market alerts, built and switched off; nightly
backups, server notices and a safer install. It includes everything in v0.3.4. **Publish it by
Friday 6 Nov or from Monday 9 Nov: never on Saturday 7 or Sunday 8 Nov (Muhurat).**

### What changes for you
- **Live orders: Upstox order calls are paced at 5 a second (OpenAlgo's own figure was 8).**
  Upstox allows 10, and 5 leaves room for a burst that arrives unevenly. An Exit all of 10
  positions takes about 2 s, of 20 about 4 s. Nothing else about Live orders changes in this
  release.
- **The risk guard, Settings > Risk, for Analyze mode only.** It brings:
  - the kill switch, with Exit all;
  - a daily loss limit (delivery is not counted);
  - a price band, a size limit and a burst brake;
  - a history of everything it refused.
  This release checks sandbox orders only: Live orders are not checked, and in this stage the
  switch leaves the agent and remote MCP access alone. While the switch is on, a
  chip shows in the app bar and a line on the order ticket. Telegram `/killswitch on`, `exit` or
  `off`, with your phrase, works in your own chat only. Saving a daily limit at or below today's
  loss asks for a second tap.
- **Telegram tells you about refused orders that came from outside the app** (TradingView,
  scripts, Flow, the strategy module), merged per symbol and reason per minute.
- **Market alerts are in this release, switched off.** Once turned on, they alert you on
  Telegram and in the app on a price, a % move, the day's high or low, VWAP, bid or ask, or a
  candle close. You set them from the new Alerts page (profile menu), the Watchlist or the phone
  chart. Until then nothing of them runs. Turning them on is a server step:
  `SUDARSHAN_MARKET_ALERTS=on` in `/etc/sudarshan/alerts.env`, then restart `sudarshan-alerts`.
- **A phone you have logged out stops getting your alerts.** Logging out, changing or resetting
  the password, and the 03:00 expiry now cut off every phone still connected to them; it gets
  them again only after a new login.
- **Nightly backups on the server**, at 16:30 and 22:30 IST, never in market hours. They cover
  OpenAlgo's, SUDARSHAN's and the ring's databases, the sealed vault, `/etc/sudarshan` and the
  `.env`. Each archive is read back before it counts, and 30 daily and 12 monthly are kept within
  10 GB. They stay on the server for now.
- **Server notices on Telegram:** a release installed, rolled back or waiting for its window, and
  a nightly backup that failed, was partial or is overdue.
- **A safer install.** v0.3.3 and v0.3.4 let root write, during an install or a rollback, through
  a link that a program running as the app's own user could have planted. That needed such a
  program on the server first. The install and the rollback now never write by path in a folder
  that user can change, and a rollback restores the code, every database, the `.env` and the
  ring's databases.
- **The feed** shows the closing-auction phase and Upstox's one-minute candle, ready for the
  alerts and the screener. Its health answer names the installed release, and tick recordings
  keep 7 days at up to 1,024 MB a day. Its REST governor is built and switched off.
- **Auto-login on a special trading day** follows that day's own hours. On Muhurat, Sunday
  8 Nov, 14:05-15:40, Test now works in the morning, and the scheduled run counts only the
  session as market time.
- **The Alerts page** says which of the four states it is in, like the Watchlist: checking, Upstox
  not logged in today, no API key, or ready.

### What you must do
- **Publish it** (the link is in your task list). The server installs it outside 08:30-16:00 by
  itself.
- **Once, before the first backup night:** on the server, from the app folder, run
  `sudo -u sudarshan .venv/bin/python -m sudarshan.ops.backup --check`. It should print no
  `cannot be read:` and no `not taken:` line; if it does, send the lines.
- **On a trading day, try the guard in Analyze mode.** Run session S1 (the kill switch) and
  session S2 (the size limit, the band, the daily limit and a basket). The steps are in your task
  list. The guard checks Live orders only after both sessions pass and two fixes found in this
  release's review are in (v0.4.1).

### What is refused now that used to be accepted
- **In Analyze mode only:**
  - new orders while the kill switch is on, or after the day's loss limit is reached;
  - orders over the size limit, priced far from the market, or past the burst brake;
  - a basket, multi-leg or split order that is not allowed whole (it goes whole or not at all);
  - the part of a reversal beyond its exit (only the exit is sent);
  - with the switch on or the day locked and the positions unreadable, an order that cannot be
    told from an exit (the answer says to use the Upstox app).
  TradingView, scripts and Flow get a refusal that gives the reason.
- **Live:** nothing is refused. Orders wait their turn at 5 a second.
- **In a market alert's level:** `1e3`, `1_000`, `+5`, and digits typed in Devanagari or Gujarati.

### Not in this release yet
- **The guard does not check Live orders yet.** That needs sessions S1 and S2, and two fixes
  found in this release's review:
  - a second exit sent while the first market exit is still working;
  - Exit all sized from an old positions book after a part-filled exit was cancelled.
- **What the guard does not see:**
  - GTTs that trigger at Upstox, and orders already resting at the exchange, are not checked when
    they fill.
  - The daily figure is gross, before charges, and up to 5 s old. Upstox's positions book itself
    can lag about a minute.
  - Upstox's own apps bypass the guard.
- **Exits the one-at-a-time rule does not cover yet:** the scalping terminal's closes and the
  strategy module's exits.
- **An offsite copy of the backups.** It needs your keys; the proposal is OCI Object Storage with
  rclone crypt.
- **The feed's REST governor** stays off until its live check.

## v0.3.4 (4 Nov 2026)

Prices straight after a login inside the app, and every price screen says why it has none. It
includes everything in v0.3.3. **Publish nothing on Saturday 7 or Sunday 8 Nov.**

### What changes for you
- **After a login inside the app, prices come at once.** A phone that was logged in before 03:00 is
  logged out on its first visit of the day (OpenAlgo's daily rule; the Upstox login that auto-login
  made stays). Logging in again inside the app used to leave the Watchlist, the phone chart, the
  Dashboard and the cards without the API key until the page was reloaded, and the Watchlist said
  "Prices and search need an API key". Now the login loads the API key, the Upstox state and the
  broker's features at once, for the password and the TOTP login alike, and so does Test now on the
  Auto login page.
- **Every price screen says why it has no prices.** The Watchlist, the phone chart, the Dashboard
  and the Holdings, Orders, Positions and Trades cards show one of these:
  - nothing alarming while the login is still being checked;
  - "Upstox is not logged in today, so there are no prices. Log in again with Test now on the Auto
    login page, or Reconnect on the Dashboard.", with both links, when today's Upstox login is
    missing;
  - "Prices and search need an API key." with its link, only when there really is no API key.
- **The phone chart says "The chart could not start." with Try again** when its stream could not be
  set up. It used to blame the API key.

### What you must do
- **Publish it** (the link is in your task list). The server installs it outside 08:30-16:00 by
  itself.
- **The next morning,** open the app on the phone, log in if it asks, and check that the Watchlist
  shows prices without a reload. Nothing else to check.

### What is refused now that used to be accepted
- Nothing.

### Not in this release yet
- A phone logged in before 03:00 is still logged out on its first visit of the day; that is
  OpenAlgo's daily rule, and Upstox stays logged in.
- The risk guards, market alerts and the ops work come in v0.4.0.

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
