# सुदर्शन / SUDARSHAN changelog

Releases of the `sudarshan` branch, newest first, written for the owner deciding whether to
publish. The owner publishes each release on GitHub. The server's deploy timer then installs it
outside 08:30-16:00 IST on trading days (09:00-15:45 until v0.3.1), keeps backups, and rolls
back if the app does not come up healthy.

## v0.12.0 (15 Nov 2026)

The family layer: a view-only family app for the phone, a family Telegram bot, notifications on the
family's phones, and a Family page where you add members and decide what each one sees. **All of it
ships switched off** (`SUDARSHAN_FAMILY` is off): installing this release shows no family member
anything, and nothing you trade with changes. It stays off until you do the switch-on steps below
yourself, on the server. It includes everything in v0.11.0. **Publish it only after v0.11.0 has
installed and run one trading day.**

### What changes for you from the day you install it
- **Nothing a family member can see.** There are no members, no invites and no shares, and the
  family address (`/family`) only says that the view is not set up or is switched off.
- **A new menu entry, Family, after Risk.** It opens and says the family view is not set up (or is
  switched off). You can look at it; nothing on it works until the steps below are done.
- **A new service, `sudarshan-family`, is installed.** It does not start until the one-time family
  set-up has passed its check. If you ran that set-up after v0.10.0, it starts with this install,
  switched off: it opens no data of its own, runs no job and answers every family request "switched
  off". It runs as its own account, which can read none of your secrets, and nothing in it can
  place, change or cancel an order.
- **Risk page:** the confirm phrase shows as dots, with a Show button; a wrong phrase appears in
  History at once; the daily limit's lock warning goes when its save is answered; and the page is
  right the moment you come back to its tab or the phone wakes.
- **A refused order's message is the server's own sentence,** with the symbol on a second line when
  the sentence does not name it (so a refused TradingView alert says which order it was). A refused
  basket says its sentence too, and a basket that may have gone says "not confirmed" and to check
  Orders first.
- **An order request with a missing or wrong API key** (an alert still carrying an old key, say) is
  refused as before, and now leaves no row in your order log and shows nothing in Analyze mode.
  Requests with your key, and every order made inside the app, are logged as before.
- **The deploy tool can check the family view's nginx lines.** It says nothing until those files are
  installed. After that it asks nginx to test its configuration only when one of SUDARSHAN's own
  nginx files has changed, not at every ten-minute run; `sudo sudarshan-deploy --nginx-check` tests
  whenever you run it.

### What the family view does, once you switch it on
- **A member's app:** its own address (`/family/`), installable from Chrome, with a "FAMILY · VIEW
  ONLY" badge and lock on every screen. Dashboard, Positions, Holdings and Watchlist, and behind
  More: Orders, Trades, Portfolio and Settings. There is no order button anywhere. Prices and P&L
  move tick by tick, and each screen says "Real-time", "Reconnecting" with the age of the figures,
  or "Market closed". A figure you hide reads "Hidden", never a blank or 0. A book you did not share
  reads "Not shared with you", and one that could not be read says so, on Orders and Trades too:
  neither is ever shown as an empty account.
- **Signing in:** a sign-in name, a password of 12 characters or more and a 6-digit code from a code
  app. A phone stays signed in for 30 days (14 without use); a member can use 3 phones. If you pause
  or remove a member, or their sign-in runs out, their phone says why on its sign-in screen.
- **Your Family page:** up to 4 members, as a viewer or a child (a child starts with everything
  hidden). An invite link you send yourself, which works once, for two hours, and shows when it was
  first opened. Pause, resume, unlock a locked sign-in, reset a sign-in, remove, sign a phone out.
  Shared watchlists and portfolio tabs, the record of everything done, and the state of the service
  and the bot.
- **What decides what a member sees** (four switches per member, and two kinds of share):
  - positions and holdings: sharing the Upstox tab with them, on the Family page, Shares, "Portfolio
    tabs" (view or restricted, with an end date if you want one). The Portfolio page itself has no
    share control;
  - orders and trades, and funds: that share, and the member's Orders and Funds switches;
  - the figures inside them: the member's P&L and Quantities switches (P&L can be shown only with
    quantities);
  - watchlists: each list's share, on the Family page, Shares. You choose who first; the switches
    appear after that. Sharing with every member is a deliberate step: choose "Every member", then
    tap the switch itself;
  - your other portfolio tabs: each tab's share, in the same "Portfolio tabs" section.
- **Book shares are gone.** An earlier build had a separate share for each book (positions,
  holdings and so on). It decided nothing, so the Family page no longer offers it and the server
  refuses one. The Shares tab says what does decide it, as above, and leads to both places.
- **P&L hidden, quantities shown:** the overall P&L, every rupee figure and every cost price are
  hidden. A member can still work out a day's change and a holding's value from the quantity and the
  price, by one multiplication. Hide quantities to hide those too; the member's sheet says so under
  the switch.
- **A share you remove, let end or change to restricted stops showing within about a minute,** at
  any hour, on the member's phone, in the family bot and in the daily summary. A member's own
  switches act at once.
- **While the server cannot confirm what a member is shared** (the portfolio service is restarting,
  say), their screen answers within a few seconds and says "What is shared with you could not be
  checked just now" over the last figures, with their time; they are never shown as current. After
  ten minutes without an answer the figures are put away, the screen says so, and they come back
  when the server answers again. A member sees less in that time, never more.
- **How old a price is, is never hidden.** In a shared portfolio tab each holding says so ("Value
  from 1 Oct · 45 days old"), and an old price is marked, also for a member who is shown no
  figures. This settles the point v0.10.0 left open: the age line no longer travels with quantities.
- **The family bot** (optional, a second Telegram bot of its own): the market, positions, holdings,
  orders and shared portfolios, each under the member's privacy, in English or Gujarati. Any other
  message is answered "This bot only shows information. It cannot place, change or cancel an order."
  A member links their chat from Settings; groups are never answered.
- **Notifications on a member's phone,** turned on by the member, phone by phone (on a phone two
  members use, each has their own switch): a notice of a sign-in from a new phone or a locked
  sign-in, and a short summary of the day 25 minutes after the last close (16:05; 16:10 from 7 Dec).
  Quiet hours are 21:00 to 08:00 unless the member changes them.
- **Signing in and joining are limited to 12 tries a minute for everyone together,** and 5 a minute
  from one connection, so that nobody outside can make the server do password work without end. The
  price: heavy outside traffic can make sign-in and join answer "Too many tries" for as long as it
  lasts. A member already signed in is not affected.

### What you must do
- **Nothing when you publish.** The install changes no setting of the app.
- **To switch the family view on, when you want it.** Do the steps in order, outside market hours,
  and stop at the first one that does not show what it should. The lines to paste and the detail are
  in `sudarshan/family/README.md`, under "Switching the family view on".
  1. **The one-time family set-up, as root** (one pasted line; skip it if you ran it after v0.10.0).
     You should see `PASS.` It makes the family's settings file with the switch off, whether you run
     it before or after this install; if it could not, the deploy's next run does, within ten
     minutes. The Family page then says the view is switched off.
  2. **The nginx lines:** copy the three files (one pasted line), add the one include line to the
     SUDARSHAN site, record which site file that is, test and reload nginx, then run
     `sudo sudarshan-deploy --nginx-check`. You should see, last, `nginx: PASS. The family view's
     nginx lines are installed, loaded and this release's.`
  3. **The family bot's token** (optional; the view works without it): make the bot with BotFather
     and save its token with the small file in the README. The Family bot card reads "Polling"
     within a minute.
  4. **The switch:** paste the one switch line (it starts `v=on`). You should see `The family view
     is switched on.`; the last word is read back from what it wrote. It restarts the family and
     alerts services and nothing else, and a later release never moves the switch back.
  5. **On the Family page,** which now shows the service on and its check passed: add a member and
     choose what their privacy hides. Then on its Shares tab, under "Portfolio tabs", share the
     Upstox tab with them, and under Watchlists choose who and share any lists.
  6. **Send the invite link yourself.** It works once, for two hours.
  7. **Check on a test member's phone:** the member joins and sees only what you shared; the
     server's address without `/family` shows a page saying that part is for the account owner; a
     fill appears on the family positions within a few seconds; orders and trades match your own
     Orders page; a list you stop sharing leaves the screen; the Upstox tab's share, once removed,
     reads "Not shared with you" within about a minute.
- **Run it for a few days with test members before you invite the family.** Make one viewer and one
  child, use them on a spare phone or in a second browser on your own, and try each switch and each
  share both ways. The checks under "Still to check" below are best done in those days.
- **If you try it in the same browser you trade in:** both work while you are signed in to both.
  Your own sign-in ends at 03:30 and the family one does not, so next morning your own pages show
  the "for the account owner" page: in the family view open Settings, sign this phone out, then
  sign in as usual. A second browser avoids this.
- **To switch it off again:** the same switch line with `v=off` in place of `v=on`. You should see
  `The family view is switched off.` Every family screen then says so, and members, shares and
  privacy are kept.

### What is refused now that used to be accepted
- **A book share,** as said above, with a sentence saying where the real controls are.
- **A request, without your sign-in, to the app's own addresses that hand its screens the API key
  and the WebSocket settings.** Your screens are signed in, so they see no change.
- **A call to the Telegram webhook that cannot be checked.** Your bot does not use the webhook.
- **The old `/test_chart` address** is removed; it led to no page.

### Still to check, or not modelled
- **Nothing of the family view has been seen on a real phone.** The installed app's look (whether an
  address bar shows on the Dashboard), notifications arriving on a locked phone, the app working
  under a real Chrome, the screen reader's reading order and the "Open in code app" button on the
  join screen were checked in tests or by reading only. A small phone's top bar was measured at 320,
  360 and 412 px in a test browser. The Risk page's changes were not run in a browser either.
- **A removed share takes up to a minute to stop showing,** not seconds. If the portfolio service is
  not answering at that moment, the member's screen keeps its last figures, marked as not checked,
  for up to ten minutes, as said above.
- **A shared portfolio tab's own page can wait about 24 seconds** if the portfolio service accepts
  the request and then does not answer. The book screens answer within a few seconds.
- **With large text on a small phone** the badge is shown whole on a second row and the top bar
  grows tall. That was measured in a test browser, not on a phone.
- **When the server restarts,** an installed family app can show the web server's bare error page
  instead of its own "try again" page.
- **Removing a member leaves their tab shares and privacy choice in the portfolio's records.** The
  member has no sign-in and a member's number is never given again, so only a break-in into the
  family service could ask with them.
- **A family phone that once opened your own sign-in page** sees that sign-in page, not the "for
  the account owner" page, when it strays outside `/family`. It gets no further; clearing the site's
  data in Chrome on that phone puts it right.
- **If the family service is down at the moment an invite link is opened,** the web server's error
  log can name the link. A link works once, for two hours.
- **If nginx's own test failed when SUDARSHAN's nginx files last changed,** the deploy log says so
  once and keeps that note until one of those files changes, even after you repair the cause
  elsewhere. `sudo sudarshan-deploy --nginx-check` says what holds now.
- **If the family service itself were broken into,** it could see what members may see and no more,
  and could reach the app's sign-in page as any stranger on the internet can; never an order, your
  API key, your Upstox token or your saved secrets. The README's "What this does not cover" says the
  rest.
- **Not in this release:** members' own watchlists, a chart for members, notifications on your own
  phone, and the tools.

## v0.11.0 (14 Nov 2026)

The trade journal and its reports: a Journal page with every trade paired from your fills, its
analytics and your playbooks, and a Reports page with the Morning brief, Day report, Week in review,
Monthly statement and Tax pack, each also sent to your Telegram. **This release switches the journal
on** (`SUDARSHAN_JOURNAL` is set to `on`). It ships on because it only reads: it cannot place, change
or cancel an order, and nothing you trade with changes. It includes everything in v0.10.0. **Publish
it only after v0.10.0 has installed and run one trading day.**

### What changes for you from the day you install it
- **A new service, `sudarshan-journal`, starts with the install and is on.** It reads your fills
  through the portfolio service, prices from the price feed and OpenAlgo's own records, and reaches
  nothing off the server. It keeps its own file, `journal.db`, in `/var/lib/sudarshan-journal/`;
  from the next install on, the deploy backs it up and puts it back on a rollback.
- **Two new menu entries, Journal and Reports.** The menu now reads Watchlist, Screener, Portfolio,
  Journal, Reports, then Holdings.
- **A fill becomes a trade in the Journal a few seconds after its Telegram alert.** A check after
  the close (16:05) adds any fill that was missed. Every night at 22:00 the journal reads the day
  once more, and rebuilds from Upstox's trade history any of the last 3 trading days it did not
  check itself. On the first night that is the 3 trading days before the install: their trades
  appear under the strategy "Trade history", without times.
- **New Telegram messages:** a Morning brief 30 minutes before the open (08:45), a Day report 5
  minutes after the F&O close (15:45; 15:50 from 7 Dec), a Week in review on Sunday at 18:00 with
  its chart as a picture, a Monthly statement on the 1st at 07:00 with a PDF, and a Tax pack on 1
  April and whenever you ask for one. They take turns with order and market alerts: a report never
  goes while one of those waits. If the install runs after a report's time on a trading day, that
  day's report can arrive late, the same evening.
- **Positions the journal did not see from start to end are listed apart, never guessed.** Shares
  or F&O positions you held before it started read "Opened before the journal"; a contract you let
  expire reads "Ended at expiry"; a share trade you no longer hold reads "Closing not seen". They
  sit under "Not in the figures" on the Trades list (one line with its count; tap to open), carry no
  P&L, and are left out of every figure. Each report says how many it left out. Upstox's own report
  has them. A sale of shares you hold is never shown as a short.
- **Charges and net figures are estimates,** marked "est.": Rs 30 an order plus the statutory
  charges. An order shared by two trades (a reversal) pays its brokerage once, split between them.
- **A chart, an order ticket or the scalping page left open overnight picks up the new day by
  itself** when the date changes in India, or when the phone wakes after midnight: an expiring
  contract's chart stops at its own time instead of drawing flat candles, and the scalping page
  reads the day's expiry list again.
- **Screener:** while a screen's 15:30 check waits for closing prices (from 7 Dec), its "next
  check" shows when it will really run. A screen no longer waits for the closing price of an auction
  stock the screener cannot follow (past its 320-instrument limit).

### What the journal and the reports do
- **Trades:** open trades first, their P&L moving with every tick ("Real-time" only while ticks
  arrive), then closed trades by whole days. The Sandbox switch shows Analyze-mode trades, never
  mixed with real ones.
- **A trade's sheet:** its fills, averages, gross, charges, net, how long it was held, its worst
  and best point, and how fast its first entry order was answered. A reversal's fill reads "65 of
  130" and says where the rest went. Below that, your own part: strategy, playbook and its
  checklist, risk in rupees, mistake tags, mood and notes. The sheet asks before it closes with
  changes you have not saved, and keeps them in that browser tab for up to a week.
- **Analytics:** net, win rate, profit factor, expectancy, average R, max drawdown, the equity
  curve, a calendar, best hours, hold times, results by strategy and by tag, expiry days against
  other days, and what charges took.
- **Playbooks:** your setups, each with its checks and how it has done.
- **Reports, Archive:** every report, whether it reached Telegram (and why not, if it did not),
  Open to read it, Download for its file, Download CSV on a Monthly statement, "Build the FY pack
  now" and "Build this month's statement now". A statement you build early is sent again, whole,
  on its day. A Week in review, Monthly statement or Tax pack missed because the journal was down
  is built at its next start, within 3 days.
- **Reports, Settings:** a switch for each report, the times as the server says them, and your
  trading capital.
- **The Tax pack is a working paper for your CA,** not a filing. Shares bought and sold the same
  day are listed as that; the 12-month line goes by calendar dates; a position sold in parts is
  booked sale by sale, each in its own year; and it has a block, "What this paper leaves out".

### What you must do
- **Nothing when you publish.** The deploy installs the service and adds `SUDARSHAN_JOURNAL='on'`
  to the app's settings. No other setting changes.
- **Under Reports, Settings, type your trading capital and save.** Until then the percentage
  figures ask you to set it.
- **To switch the journal off:** set `SUDARSHAN_JOURNAL` to `off` in the app's settings file and
  restart `sudarshan-journal`. It then stays off through later releases, and no report is sent.
  Deleting the line instead brings `on` back at the next install.
- **On the first trading day after it installs.** Do not place a real order for these checks; look
  at them when you next trade. The two that need an order are done in Analyze mode.
  - The Journal page opens. After your first real fill, the trade appears within a few seconds with
    Upstox's time and price, and the line beside "Open" says "Real-time".
  - In Analyze mode, one small round trip: it appears as one trade under the Sandbox switch, and
    not among your real trades.
  - In Analyze mode, one reversal (for example SELL 130 against a long of 65): the first trade's
    sheet reads "65 of 130" on the shared fill.
  - When you next sell part of a share you already hold: it reads "Opened before the journal",
    never SHORT under Open. Note the holding's quantity in the Upstox app just before and just after
    the sale, and tell the next work session (Upstox's holdings on the day of a sale are not yet
    checked).
  - Type a note on a trade and tap outside the sheet: it asks before closing.
  - Analytics, "By strategy": the Net column shows each rupee figure whole.
  - At 15:45 the Day report arrives; its realised figure matches the Upstox app (delivery sales are
    not in it). Its row under Reports says Sent, and Open shows its text.
  - After 16:05, an option that expired that day reads "Ended at expiry".
- **On the first night, after 22:00, and the next morning:**
  - each trade of the 3 trading days before the install appears once under "Trade history", never
    twice;
  - a share you shorted and covered on one of those days is one closed SHORT, not an open LONG;
  - no share you still hold has moved to "Closing not seen". If one has, and the shares are pledged
    or held under MTF, tell the next work session;
  - "Not in the figures" is one line with a count, and a tap opens it.
- **Later:** on Sunday at 18:00 the Week in review arrives with its picture; on 1 Dec, Download and
  Download CSV on the Monthly statement each give a file that opens.

### What is refused now that used to be accepted
- Nothing that was accepted before. Of the new parts: a report file goes to Telegram only as a PDF,
  CSV or PNG of at most 10 MB, and a download that cannot be the file shows a sentence and saves
  nothing.

### Still to check, or not modelled
- **Results of positions opened before the journal, and of contracts that ended at expiry, are not
  worked out.** They stay out of the figures; settlement values come in a later release.
- **A rebuilt day that mixes an intraday short and a delivery purchase of the same share, in unequal
  quantities,** is taken as delivery whole and can show a long larger than you hold. Check that
  share against the Upstox app; the nightly count ends it if you hold fewer.
- **"Closing not seen" covers shares only and runs at 22:00.** After the journal has been off for
  more than 3 trading days, a share trade whose sale it missed reads as open until the first 22:00
  after it is back; a share you trade every day is never counted; a reverse split or a transfer out
  ends the trade the same way. An F&O trade whose closing was missed ends at its expiry, and one
  closed in part meanwhile can pair a later order wrongly. How Upstox's holdings count pledged or
  MTF shares is not verified.
- **Upstox's trade history may name a trade differently from its trades of the day.** If it does, a
  day partly recorded live is doubled by the nightly rebuild; that is the first-night check above.
- **If the portfolio service does not answer when an F&O position is first seen,** a sale against
  an overnight long can read as a short until the journal asks again.
- **Charges:** every rate is still to be verified against Upstox's page. Upstox's lower brokerage
  on small share orders and the DP charge on delivery sales are not modelled; BSE, currency and
  commodity trades say "not estimated". The comparison with Upstox's own charges is the next release.
- **Tax pack:** it matches purchases to sales inside one journal trade, so shares sold out and
  bought back the same day are listed as a delivery sale and a new holding, where a broker's
  statement nets the day first; totals agree over time, the heading and year of a part can differ.
  Dividends and the list of trades are not in it (the Monthly statements list the trades).
- **For your CA:** shares bought on 29 February and sold on 1 March of the next year are listed as
  held over 12 months. Whether that one day counts is for the CA (the next such date is in 2028).
- **If the journal is stopped in the instant between marking a report as announced and writing its
  line for Telegram,** that report's message is not sent; the report is still on the Reports page. In
  three other narrow moments a report can arrive twice.
- **The Day report's realised figure leaves delivery sales out,** as the daily loss limit does.
- **Notes are saved only by Save.** Unsaved changes are kept in that browser tab only; an app the
  phone closed and started afresh has lost them. Back from a sheet opened by a link leaves the page
  without asking (the changes come back when the trade is opened again).
- **An open trade's sheet shows no moving P&L** (its card does), and past 100 open trades the rest
  read "Not priced".
- **Reports are in English;** a name typed in Gujarati prints as question marks in a PDF. A report
  you build yourself goes to Telegram at once, also in market hours. Report files are kept with no
  limit. With an empty holidays file, a holiday still gets a Morning brief and a Day report.
- **A list of more than a few hundred closed trades is slow to scroll back through.** The
  best-hours table in Analytics scrolls sideways when trades fall outside 9:00 to 15:00.
- **The new pages have no test under the opt-in worker** (the default worker's tests cover them),
  and the family's view of the journal is not in this release.

## v0.10.0 (14 Nov 2026)

Groundwork for the family view and the trade journal, which come in the next releases. Nothing you
trade with or look at changes. It includes everything in v0.9.0. **Publish it only after v0.9.0 has
installed and run one trading day.**

### What changes for you from the day you install it
- **Nothing on your screens.** Orders, the risk rules, charts, alerts, the screener, the watchlists
  and the portfolio work as in v0.9.0.
- **The price feed knows its clients.** At the deploy's next run, about ten minutes after the
  install, six key files appear together in `/etc/sudarshan/feed-clients/`, one for each SUDARSHAN
  service that asks the feed for prices. From then on the feed changes what it follows for a service
  only when that service shows its own key, so one service can no longer drop another's prices.
  Everything keeps running while this happens. A service that cannot read its key keeps the prices
  it already follows and says the feed did not take its new ones; no screen says "Real-time"
  without ticks.
- **The portfolio checks the Upstox Analytics Token once a day,** at its first start that day (three
  reads), so a lapsed token is said early.
- **Server notices on Telegram take turns with order and market alerts.** During a burst of market
  alerts a server notice waits a minute at most.
- **The portfolio's database gains two empty tables** (for tabs you may later share with family),
  made when the service starts; a rollback puts the old file back.
- **The price feed's state folder can be read only by the SUDARSHAN account and its group,** as the
  other services' folders already are.

### What you must do
- **Nothing when you publish.**
- **For the family view, when you want it** (it comes in a later release, switched off): run the
  one-time set-up from your SSH session, as written in `sudarshan/README.md` under "The family view's
  one-time set-up". It makes the family account, its own folder of keys and its own firewall rules,
  then checks that the account cannot read any of your secrets, and ends with `PASS.` Nothing else on
  the server changes, nothing runs for the family until that release arrives and you turn it on, and
  running it again is safe. Put ` --dry-run` after it to see first what it would do.

### What is refused now that used to be accepted
- **A request that changes what the price feed follows, without a service's key,** once the key
  files exist. Only SUDARSHAN's own services make such requests; the `curl` examples in
  `sudarshan/feed/README.md` now need the key's header.

### Still to check, or not modelled
- **The family and journal parts in this release do nothing yet:** the portfolio's reads for family
  members (a member sees only what the tabs shared with them reach, never another member's name or
  account), the family check and the family notices stay switched off until their own releases.
- **Upstox's orders and trades answers,** which the family view and the journal will read, were
  built from Upstox's documented fields, not from a captured answer. They are compared with the live
  book on the first session that uses them.
- **In the family view, a member not shown quantities would also miss the line saying how old the
  figures are** (that line travels with the quantities). This is settled before the family view is
  turned on.
- **If the alerts service cannot show the feed its key,** its log says "The price feed is not
  answering", which is not the cause. The prices it already follows keep coming.
- **If the server runs out of open files while the feed lists its key folder,** the feed reads the
  folder as empty and accepts requests without a key until its next listing.

## v0.9.0 (14 Nov 2026)

The new watchlists: sections inside a list, rows that follow an index's ATM option or its
current-month future by themselves, built-in lists, rankings from Upstox, favourite tabs, flags and
notes, a line with what you hold, copy and move between lists, TradingView import and export, and
alerts on a whole list. **All of it ships switched off** (`SUDARSHAN_WATCHLISTS_V2` is off): until a
later release turns it on, the watchlist page on the phone and the lists on /trading work exactly
as in v0.8.1, and nothing you trade with changes. It includes everything in v0.8.1. **Publish it
only after v0.8.1 has installed and run one trading day.**

### What changes for you from the day you install it
- **Nothing you can see.** The watchlist page, /trading's lists, the Holdings page and market alerts
  read as they do in v0.8.1.
- **Upstox holdings carry each row's ISIN inside the app** (the new watchlists match a holding to
  its NSE or BSE row by it). Nothing shows it yet, and every reader of the holdings was checked:
  none changes what it shows or decides.
- **A new file, `db/sudarshan_watchlists.db`.** The deploy creates it, backs it up with the app's
  database before each install and puts it back on a rollback. While the switch is off, the only
  write to it comes when a list is deleted or cleared (on /trading or the phone): that list's
  sections and layout there are cleared with it.
- **Market alerts' own database gains a table for alerts on a list** when the service starts, and the
  service reads it once a minute during market hours (there are none while the switch is off). It
  makes no call to the app or to Upstox for this. A rollback puts the old file back.
- **The release's build takes longer**, because the tests have grown: allow about 40 minutes after
  you tap Publish before its files are attached. The server installs it after that, outside market
  hours as before.

### What the new watchlists do, once they are turned on
- **Sections** inside a list (up to 20), each with one of five marks (a colour with its shape and
  name, never colour alone); a folded section stays folded on that phone. Edit mode saves a new order
  in one go; a symbol can be moved to a section, or copied or moved to another list.
- **Rows that follow:** an index's ATM option, ATM+2 and so on or a fixed strike, as one contract or
  as a strike row (CE, strike and PE in one row), or its current-month future. They roll by themselves
  at the close of expiry day and say "Rolled to ..." for a minute. Up to 20 in a list. Buy and Sell
  in their sheets open the usual order ticket on the contract the row shows.
- **Built-in lists:** Indices, NIFTY 50, NIFTY BANK, F&O stocks and Index futures, locked, with Make a
  copy. NIFTY 100, 200 and 500 and ten sector lists appear once their files are put on the server
  (`/etc/sudarshan/universes/`).
- **Rankings from Upstox** (gainers, losers, most active and Upstox's other categories, for index,
  stock and commodity futures and options): 20 rows, 50 on request, marked "Ranked by Upstox" with
  the ranking's age, never "Real-time". Pull down on a ranking to read it again.
- **Seven favourite tabs** (your first seven lists until you pin your own), **five flags** (Watch,
  Breakout, Results, Long term, Avoid; you can rename them) and **notes** of up to 500 characters on a
  symbol. Flags and notes are yours alone: the family view will never show them.
- **Sort** per list, high first or A to Z, with the change measured from the previous close or from
  today's open, saved on the server so the phone and a tablet agree.
- **"Show what I hold"** per list, off by default: each row adds the quantity you hold and your net
  position. While it is on, the page reads Holdings and Positions when it opens, after an order, on a
  reconnect and every 45 s while a market is open, as the Holdings page does.
- **Symbol search** that understands expiry words, strikes, ATM, ISIN and futures, with a suggestion
  for a typo.
- **TradingView:** export a list to the text TradingView imports (sections kept), and import a
  TradingView export into your lists (a file longer than 250 symbols is split).
- **Alerts on a list** (market alerts must be on too): one alert watches every symbol of a list or a
  section, follows the list's changes at once, and sends several firings close together as one
  Telegram message.
- **A change on any device reaches the phone within a second,** /trading's panel included.
- **Limits:** 50 lists, 250 symbols in a list, 20 sections. A list longer than 250 rows follows the 120
  rows around where you are scrolled; a row the page is not following shows its last price in grey
  with its time, never as current.

### What you must do
- **Nothing when you publish.** The deploy creates the watchlists file and adds
  `SUDARSHAN_WATCHLISTS_V2='off'` to the app's settings. No other setting changes.
- **On the first trading day after it installs,** open Holdings on the phone and on the web: the same
  rows, quantities and P&L as before. That is the check your rules ask for when a field is added to
  a broker's book; the ISIN itself is checked against Upstox's raw answer in the tests.
- **Turning it on is a later release's job,** after you say so. On the first day it is on, check:
  - a ranking opens with rows and an age that moves, and says how many contracts it left out, if any
    (Upstox's real answer has not been seen yet);
  - a symbol added on /trading appears on the phone within a second;
  - an ATM row's strike matches the option chain page's ATM, and on the next NIFTY expiry day the ATM
    and future rows roll at the close;
  - a quote read after 09:15 gives each row its open (change from today's open shows a dash until
    then);
  - two devices each on a 250-row list stay connected past 10 minutes;
  - pull down on a ranking: it reads again, and Chrome does not reload the page;
  - in Analyze mode, one Buy from a follow row's sheet and one from a ranking's sheet open the
    ticket on the right contract (your rules ask for one use of each new way into the ticket);
  - export one watchlist from TradingView and hand the file to the next work session, so the forms
    of index futures and BSE contracts can be checked (until then they are left out of export and
    import, with a sentence saying so).

### What is refused now that used to be accepted
- Nothing, while the switch is off.

### Still to check, or not modelled
- **Upstox's rankings are built from its documentation.** If its real answer has another shape, a
  ranking says it could not be read; it never shows an empty or wrong list as current.
- **TradingView's forms for index futures other than NIFTY and BANKNIFTY, and for BSE futures and
  options, are not checked yet,** so those rows are left out of an export and an import with a
  sentence naming them, until one real TradingView export confirms the forms.
- **A strike row added from a dated expiry tab follows the nearest expiry, not that date.** The form
  says so and says when it moves; a row that stays on one dated expiry needs a database step in a
  later release.
- **A note you are typing is lost if the symbol's sheet closes before Save** (Back, a tap outside,
  Buy). Your choice for a later release: save it when the sheet closes, or ask first.
- **Moves not saved in edit mode:** every way off the list asks first ("Save the new order?"), except
  leaving the page with the bottom bar, which drops them.
- **A section's menu has no Export yet;** export works from the list's menu.
- **Copying or importing 250 symbols into an existing list takes about 2 s** (measured on a test
  machine; the aim was under 1 s). The page stays usable meanwhile; it is to be measured on the
  server before turning it on, with the app's memory after a trading day with the switch on.
- **On a special-session expiry day** (none is announced), a follow row on an index contract rolls at
  the session's end rather than at the index's 15:30 stop, and the built-in Index futures list rolls
  its BSE futures at NSE's close for that day.

## v0.8.1 (13 Nov 2026)

The market's timings change on **Monday 7 Dec 2026** (SEBI's circular of 15 Oct 2026, NSE's of
6 Nov 2026): F&O trades to 15:45, an expiring index contract stops at 15:30 on its expiry day, and
a stock with derivatives closes by auction from 15:30 to 15:40. SUDARSHAN switches to them that
morning by itself. Before 7 Dec the hours stay as they are. Two things change from the day you
install it: what you see once an expiring contract stops at 15:40 on its expiry day (the second
item below), and a screen's 15:30 check that waits for an auction price that has not come is not
marked late and keeps the screen's later checks behind it (the fourth). It includes everything in
v0.8.0. **Publish it only after v0.8.0 has installed and run one trading day, and no later than
Friday 4 Dec**, so it is installed before the Monday it is for.

### What changes for you on 7 Dec 2026
- **F&O runs to 15:45 everywhere:** the charts on the phone and on /trading (the 15:40 five-minute
  candle opens on time, the last hourly candle closes at 15:45), the market status (the one Python
  strategies read too), Flow, and the daily loss limit's F&O session. A Python strategy keeps its
  own stop time: one saved with the 15:40 it was filled in with still stops at 15:40.
  To run it to the close, set Stop Time (IST) to 15:45 on its Edit Schedule page. A Flow Time
  Window with its own End Time keeps it the same way. Saved strategies are not changed.
- **An expiring index contract stops at 15:30 on its expiry day** (NIFTY, BANKNIFTY, SENSEX,
  NIFTYFPI and the others; a stock contract trades to 15:45). Its chart opens no candle after
  15:30 and its countdown ends at 15:30; the chart's marker and the order ticket say "Stopped
  trading at 15:30", never "Stale". The scalping terminal moves to the next expiry by itself at
  15:30 and says so ("The 08DEC26 expiry stopped trading at 15:30. Now showing 15DEC26."). Until
  6 Dec every expiring contract stops at 15:40, and from the day you install this the same happens
  then: the chart's marker and the order ticket say "Stopped trading at 15:40" where they said
  "Market closed", the scalping terminal moves on and says so, and Analyze mode refuses the
  contract's orders (see below).
- **Analyze mode settles an expiring index contract** at 15:30 on its expiry day, and a stock
  contract at 15:45. Until 6 Dec every expiring contract settles at 15:40. Your MIS square-off time
  does not change. An index contract is told from the master contract, so NIFTYFPI's and BSE's
  Focused IT's contracts settle at 15:30 too.
- **A stock with derivatives** trades to 15:30, then closes by auction. It shows "Closing auction ·
  price at about 15:40" until its price comes, and alerts wait for it until 15:45 at the latest (a
  price stamped a moment after 15:40 still counts). A screen holding such stocks (F&O stocks, a
  NIFTY list, a watchlist with one) makes its 15:30 check when their prices are all in, at about
  15:40, and at 15:45:05 at the latest. The check is on time, not marked late, and a watchlist
  screen's later checks (15:35 and 15:40 on a short timeframe) wait for it and come after it, in
  order. Before 7 Dec the same wait ends at 15:35:05 when a price has not come by 15:30:05, with
  the same rules. On the charts its auction price becomes the close of the day's last candle (the
  15:29 minute, the 15:25 five-minute candle, the 15:15 hourly candle), on every timeframe, and
  never opens a candle after 15:30.
- **An index's closing value**, if NSE sends it after 15:30 with the auction's prices, is taken
  until 15:45 as the day's last value: price and % move alerts judge it, the screener's index
  candles and the charts close at it. Whether or not it comes, a price or % move alert on an index
  at candle close judges the day's last candle (the one ending at 15:30) at 15:45:05, and a screen
  of indices makes its 15:30 check at 15:45:05 (on time, not marked late). **That is after F&O
  closes at 15:45, so that day such an alert or check cannot be acted on in F&O**; until 6 Dec it
  came at 15:30:05, with 10 minutes of F&O left. A screen with no index among its instruments, a
  watchlist screen included, does not wait for it. A watchlist screen that holds an index and F&O
  contracts makes its later checks after its 15:30 check, so at 15:45:05, after F&O closes (on a
  1-minute screen that is every check from 15:31 to 15:44); keep the index in a screen of its own
  to keep those checks on time.
- **A stock without derivatives** closes at 15:30 with no auction and keeps its usual close.
- **Auto-login** may log in until 15:45. Test now is refused from 09:00 to 15:45 while Upstox is
  connected, and the page names the day's hours.
- **Longer windows around the close:** the price feed stays connected until 15:55 and reconnects a
  stalled feed until 15:45; order alerts stay connected until 16:05; the nightly backup and
  history downloads wait until 16:05, and the portfolio's index history for the benchmarks now
  waits for them instead of failing the day.
- **The Upstox tab's closing record** is taken at 16:00 and marked "Taken late" after 16:30. The
  history names each record's time.
- **A day high or low alert on F&O** may start watching up to 15:44.
- **OpenAlgo's timings page** shows NFO and BFO at 15:40 with "15:45 from 7 Dec 2026" under it. A
  close you set yourself is never changed.

### What you must do
- **Nothing when you publish:** no setting changes and no database change.
- **Place your exits on an expiring index contract before 15:30 on its expiry day.** From Tue
  8 Dec an expiring NIFTY, BANKNIFTY or SENSEX contract cannot be traded after 15:30 on its expiry
  day. Check any exit you set for after 15:30: a strategy's exit time, a Python strategy's stop
  time, a Flow time window, an NRML position you meant to close near the old 15:40. From 15:30 the
  scalping terminal shows the next expiry by itself and says so, and its Close cannot close a
  position in the expiring contract either.
- **If you set your own NFO or BFO close** on the timings page, it stays as you set it on every
  date. Set it back to 15:40 to let it follow by itself, or to 15:45.
- **If you set your own window** in `SUDARSHAN_FEED_WINDOW`, `SUDARSHAN_ALERTS_WINDOW` or
  `SUDARSHAN_AUTOLOGIN_WINDOW`, it keeps its end, unless it ends at the usual time (15:50, 16:00
  and 15:40): that end moves 5 minutes later from 7 Dec, as the usual windows do.
- **On the morning of Mon 7 Dec, check:** the Auto login page says "from 09:00 to 15:45"; the
  timings page shows NFO today as 09:15-15:45; the feed's health shows hours 08:58-15:55.
- **That afternoon, check:** after 15:40 a NIFTY future's phone chart still forms candles (the 15:40
  candle opens); after about 15:40 RELIANCE's 15:25 five-minute candle closes at its auction price;
  from 15:30 to 15:45, NIFTY 50's and SENSEX's prices on their phone charts: whether they stay at
  their 15:29 value, change once at about 15:40 (the closing value), or keep changing (see "Still
  to check" below). Note which, for the next release.
- **Left to the server session that evening:** whether the portfolio's index history for the
  benchmarks ran after 16:05. Nothing on the phone shows it; the session reads
  `sudo journalctl -u sudarshan-portfolio -n 200 --no-pager`, where the day's line after 16:05 says
  "index history ok" (or "skipped" with nothing to fetch), never "failed".
- **On Tue 8 Dec (NIFTY expiry), check,** on charts and a scalping page opened that day: after 15:30
  the expiring NIFTY contract's chart opens no candle and says "Stopped trading at 15:30", the
  scalping terminal shows the next expiry and a notice saying so, and an Analyze order on the
  contract is refused; its Analyze position settles at 15:30.

### What is refused now that used to be accepted
- **In Analyze mode, an order on an expiring contract after its stop on its expiry day** (15:40
  until 6 Dec; from 7 Dec 15:30 for an index contract, 15:45 for a stock contract) is refused with
  "This contract stopped trading at 15:30 on its expiry day; the exchange would refuse it." (the
  contract's own stop in place of 15:30). It used to fill at the last price.

### Still to check, or not modelled
- **On 7 Dec after 15:30**, a stock without derivatives shows its close at 15:30 and no auction
  line. Which stocks close by auction comes from reports of NSE's circulars of 6 Nov 2026 (stocks
  with derivatives on any exchange); NSE's own text was not read.
- **An index's closing value after 15:30 is not confirmed.** Whether NSE sends NIFTY 50's and
  SENSEX's closing value with the auction's prices is to be seen on 7 Dec (the afternoon check
  above). If nothing comes, nothing changes, apart from the index alerts at candle close and the
  screens of indices being judged at 15:45:05.
- **Every index value from 15:30 to 15:45 counts as the closing value.** If NSE sends values during
  the auction (as it does in the pre-open), each one is judged as it comes: a price alert can fire
  on one that is not the close, and the day's last candle on the charts and in the screener takes
  its high or low (one at 26,100 at 15:31 and the close at 26,020 at 15:40 leave the 15:25
  five-minute candle with a high of 26,100 and a close of 26,020). If the 7 Dec check shows values
  during the auction, only the last one should count; that needs a fix.
- **Upstox's intraday history after 7 Dec is not seen yet.** If it gives the auction's price a
  candle of its own after 15:30, that candle shows on a chart loaded later; the live chart puts the
  price in the day's last candle. An hourly candle from 15:15 (and a 30-minute one) spans the close
  and takes a later trade, as it always has.
- **BSE's Focused IT index contracts** are told from the master in Analyze mode, the charts and the
  scalping terminal. Market alerts and the screener know index contracts by name (NIFTYFPI added),
  so a market alert on a Focused IT contract runs to 15:45 on its expiry day.
- **A stock is told from an index by the master contract**: an F&O name the master does not list
  as a cash stock reads as an index's. A stock whose F&O and cash names differ for a while (during
  a rename, say) would stop at 15:30 on its expiry day in Analyze mode, on the charts and in the
  scalping terminal. The app's log names every such name once a day (FOCIT, BSE's Focused IT, is
  expected there), so the server session can spot one.
- **The charts and the order ticket ask the server for an instrument's own hours when they open**;
  if the server cannot answer, they keep the exchange's hours, as before.
- **A chart, order ticket or scalping page left open from the previous day keeps that day's hours
  until you reload it.** On an expiry day such a chart does not know its contract stops at 15:30:
  it opens flat candles after 15:30 and says "Stale", and such a scalping page stays on the
  expiring contract. Reload them, or open them again, each morning.
- **While a 15:30 check waits** (for an auction price or an index's closing value), the Screener
  page's "next check" line can name the next trading day, or a 15:35 or 15:40 check that will only
  come after the waiting one. The checks themselves run in order; only the line is wrong.
- **A stock with derivatives that the screener does not follow** (past its limit of 320
  instruments) never gets an auction price there, so a screen holding one waits to 15:45:05 every
  day, and its later checks with it. Keep such screens smaller than the limit.
- **Deploy gate:** installs are still kept out only until 16:00, as your rules say. From 7 Dec an
  install between 16:01 and 16:05 can restart the order alerts while they are connected; they read
  the order book again afterwards.
- **Option greeks, IV and the straddle and gamma charts** assume NFO and BFO options expire at
  15:30. That is right for index options from 7 Dec and 15 minutes early for stock options on their
  expiry day.

## v0.8.0 (13 Nov 2026)

A logout on one device now signs out every device, the nightly backup can keep an encrypted copy
in your own Oracle storage bucket, and five fixes to the Risk page from the sandbox test of the
daily loss lock. It includes everything in v0.7.0. **Publish it only after v0.7.0 has installed and
run one trading day.** Nothing you trade with changes: orders, the risk rules themselves, the price
feed and API-key calls work as before.

### What changes for you
- **A logout ends every device's session.** A logout, a password change or a password reset on one
  device signs every other device out at its next page, read or reconnect, a phone that was asleep
  at the time included. Before, the other devices stayed signed in until 03:00.
- **A second device's login never signs the first one out** and never touches the price feed, as
  before. TradingView, Amibroker and the strategy host place orders with the API key, and none of
  this changes it: a logout or a password change does not change the API key (after a lost phone,
  see What you must do).
- **The app forgets the API key at every sign-out.** The copy the app keeps in the browser is
  removed as soon as a screen learns that its session has ended (a logout, a read the server
  refuses, a live connection refused after a logout elsewhere), before it goes to the login page.
  Before, it stayed until the next page load.
- **A logout that could not sign the other devices out says so.** If the server could not record
  the time, a logout from the menu at the top right, a password change or a reset tells you,
  instead of "Logged out successfully"; log in and log out once more from that menu. The Sign out
  buttons on a few other pages (Playground, Historify, the Flow editor, the broker sign-in page and
  the error page) do not say it yet. A password change or reset also keeps the
  auto-login's entry in Active Sessions, so an old session's next page no longer ends the Upstox
  login the change kept.
- **Old rows leave Active Sessions.** Rows of sessions the 03:00 expiry had already ended (the two
  October rows) go at the next login, or when the Security screen opens.
- **An offsite copy of the nightly backup, off until you set it up.** After each good backup the
  server encrypts a copy to your own certificate and uploads it to one Oracle Object Storage bucket
  of yours, which keeps every version. The private key stays on your laptop and on one offline
  copy, so a server that is taken over cannot read the copies. It can still upload new ones, junk
  or forged, even under the name of a copy already there: the bucket then keeps the earlier copy
  as a previous version, and a restore takes a copy by the time Oracle shows it was uploaded, from
  before anything suspicious happened, so it ignores them. Your bucket's two lifecycle rules (30
  days, then 30 more for a copy that is no longer the newest) are the only thing that deletes.
- **What the bucket costs.** It holds about 60 nights of copies. Oracle's Always Free allowance is
  20 GB; on a Pay As You Go account storage past it is billed, so set a budget alert and look at
  the bucket's size after the first weeks (README, "What it costs").
- **Told on Telegram when it matters:** "Tonight's offsite backup did not upload: (the reason). The
  local backup is fine." once a day per cause, after three tries for a busy or unreachable bucket;
  and a reminder every day from 15 days before the upload link's expiry date. That date is the
  one the console shows, and the last copy goes the day before it, because the console's expiry
  has a time of day. A refusal names the next step (a new upload link, or the set-up again). A
  good night is one journal line, no message.
- **The Risk page, from the sandbox test of the daily loss lock:**
  - every box that asks for the confirm phrase now says "Type your confirm phrase. It is STOP unless
    you changed it.", with its own reason beside it;
  - a forgotten phrase can be set back to STOP on the server (the command is in `sudarshan/README.md`,
    "The risk layer's confirm phrase, if it is forgotten", and under What you must do). It works at
    the next try, with no restart, and the phrase card says so. Every reset writes the History line
    "The confirm phrase was set back to the default on the server." and tells you on Telegram: "The
    confirm phrase was set back to the default on the server at (the time). If this was not you,
    change it in Settings > Risk and check who can reach the server.";
  - a wrong phrase is now written in History, from the app or Telegram, for a settings change and
    for turning the kill switch on or off: "A wrong confirm phrase was refused when asked to ..."
    with what was asked and how many tries in a row (one line, counted up), never what was typed;
    a box left empty is asked for again, not recorded;
  - after saving a daily limit at or below the day's loss, the warning "saving locks new orders until
    tomorrow" no longer stays on the card; the day card's chip says the rest;
  - in Analyze mode a refused order's message reads "NIFTY17NOV2625200CE: Daily loss limit reached:
    ...", without the word "Error".

### What you must do
- Nothing when you publish. The install adds one small table for the logout time.
- **If a phone is lost, or you change your password because a session may have been copied:**
  also open API Key in the profile menu, choose Regenerate, and put the new key into your
  TradingView alerts (and Amibroker and your strategies, if you use them). A logout or a password
  change does not change the API key, every signed-in screen could read it, and orders sent with
  it go out from the server's own address. Alerts that still carry the old key are refused from
  then on. The steps are in `sudarshan/README.md`, "Signing out every device, and a lost phone".
- **If you forget the confirm phrase:** on the server, run
  `cd /opt/sudarshan/apps/openalgo && sudo -u sudarshan .venv/bin/python -m sudarshan.guard.phrase --reset`,
  then set a new phrase in Settings > Risk. History and Telegram show every reset. A reset you did
  not make means someone can run commands on the server: change the phrase and have the server
  looked at.
- Once after the install, after the close: put the phone to sleep, log out on the laptop, then wake
  the phone. It should show the login page. Log in again on both (a logout also ends the Upstox
  login, as it always has). The two October rows should be gone from Active Sessions.
- When you want the offsite copy: the 12 steps in `sudarshan/README.md`, "Setting up the offsite
  copy": the Oracle console (a bucket with versioning on, two lifecycle rules, a budget alert), a
  key pair made on the laptop, and one `.bat`. The `.bat` sends only the public certificate, shows
  its fingerprint on the laptop and again on the server, and saves nothing until you type yes; then
  it asks for the link's expiry date and, in a hidden prompt, the upload link. On the server, root
  only makes the empty settings file; the settings are written by the job's own user, and no
  program from the app folder runs as root. The next morning, look for one file in the bucket.

### What is refused now that used to be accepted
- A device signed in before a logout, a password change or a password reset elsewhere is sent to the
  login page at its next page, read or reconnect.
- The offsite set-up refuses a private key (only the public certificate goes to the server), a
  certificate the nightly encryption cannot use, a run as root, and a settings file it did not
  make; each says what to do instead.

### Not in this release yet
- **The offsite copy has not met a real bucket yet**: the first night after your console steps is
  its first run, and one trial restore into a scratch folder is still to be done. The versioning
  and lifecycle steps follow Oracle's documentation; when you look for the first copy, check that
  the bucket's Object versioning reads Enabled.
- **The logout check on the phone** above is the first check of that change on the real server.
- The open points listed under v0.7.0 remain.

## v0.7.0 (12 Nov 2026)

Screener dashboards: a Dashboard tab of cards you choose, six more ready-made screens, and fixes to
how the screener reads the day's open, high, low and first minutes. It includes everything in
v0.6.1. **Publish it only after v0.6.1 has installed and run one trading day. The screener is
still switched off: publishing this release changes nothing you trade with or see, and none of the
changes below shows until you switch it on (v0.6.0, "What you must do").**

### What changes for you, once the screener is on
- **The Dashboard tab on the Screener page**, shown first. Each card is one of your screens:
  - a table card: its top 10 rows with LTP, % change and two values you pick;
  - a count card: how many instruments pass now, and how that moved through the day's runs.
  Add, arrange and remove cards and set each one up; a change made on one phone shows on your
  other phones at once. Three columns from 1,200 px wide.
- **Each card says what it is waiting for**: its own next check, "Real-time" only while its prices
  tick (a count card never says it), the feed or the Upstox login when either is down, and the
  close after the close. A card whose screen feeds a watchlist names that list.
- **Six more ready-made screens**, each saying in its description how it differs from the scan it
  was modelled on.
- **Day lines are judged on the forming day candle** at its minute closes, so a screen of daily
  lines can match during the session instead of only after the close.
- **The day's open, high and low are the exchange's**, from the day candle the feed carries, not
  the first price the screener happened to see. When that candle is missing the instrument reads
  "Not checked" and names which of the three is missing.
- **A screen made or resumed during the session runs once at once** on the latest closed candle.
  After an upgrade or a restart the first run lands on the latest closed candle, marked late when
  the restart delayed it.
- **Two open points of v0.6.0 are fixed:**
  - an instrument first followed after the open reads its day as not checked, instead of judging
    its open, high, low and gap from that moment;
  - an instrument followed again the same day starts its volume from then, so its next candle is
    no false volume spike, and its gap reads not checked.
- **Restarts:** an instrument let go before a restart keeps its gap from the moment it was let go,
  and a restart during the morning contract download keeps that wait out of the first candle.
- **The 09:15 candle's volume.** When the screener starts before the open and a cash stock hears no
  pre-open price, it takes the stock's first trade as the baseline, so the pre-open auction's
  volume is not counted into the 09:15 candle. Options and futures, which have no pre-open session,
  count their first minute whole.
- **The dashboard reads less:** a card reads only its own screen's runs, and the page reads the
  dashboard once, not twice, after it opens or the phone wakes.

### What you must do
- Nothing when you publish. The screener stays off.
- On the first session with the screener on: add one table card and one count card, and check the
  09:15 candle's volume of two or three liquid option strikes against Upstox's 1-minute candles.

### What is refused now that used to be accepted
- Nothing existing changes. On the Dashboard tab, Add a card stays off until the dashboard has been
  read, so it never answers "changed on another screen" when nothing changed.

### Not in this release yet
- **A frame costs about 30 ms on a mid-range phone with 20 cards while prices stream**, about 30
  frames a second; no price is lost or held past the next frame. Planned: rebuild a card only when
  its own figures change.
- **The cash pre-open baseline has not met a live session yet**: it is checked on the first session
  with the screener on.
- **History before today** still waits for the feed's request governor, as in v0.6.0; a card for a
  screen that needs it says it waits for the history download.
- The other open points listed under v0.6.0 remain.

## v0.6.1 (9 Nov 2026)

Fixes from the sandbox checks of 9 Nov: the order ticket's buttons always in view, an exit from
the Positions table that asks first, cancel messages that say who cancelled, the kill switch's
report and History, the Strategy Builder's feed chip, and the daily loss limit's sandbox figure,
which now moves with the price. It includes everything in v0.6.0. **Publish it only after v0.6.0
has installed and run one trading day.**

### What changes for you
- **The order ticket fits a short window.** On a laptop window of about 650 px the ticket's body
  scrolls and Place and Cancel stay in view.
- **An exit from the Positions table asks first:** "Exit NIFTY 27 OCT 25900 CE: sell 130 at
  market?" with an Exit button. It sends once however it is tapped, and the message says what was
  sent ("Exit sent: sell 130 NIFTY 27 OCT 25900 CE"), not the old "BUY Order Placed ... Qty: 0" for
  closing a long.
- **Who cancelled an order.** "Cancelled by the kill switch." appears only for an order the kill
  switch's own report lists. Any other cancel you did not make on this screen says "Cancelled in
  the sandbox, not from this screen." or "Cancelled at Upstox, not from this screen." While the
  kill switch is running, its cancel messages come with its report (at most 60 s later).
- **The kill switch's report** says the sandbox's Exit all in a sentence: "The sandbox's Exit all
  closed 1 position.", with "could not close N" or "found no open positions to close" when that is
  what happened.
- **Risk History** shows an activation at the same time as the card, and says whether it came from
  Telegram or the app.
- **The Strategy Builder's feed chip** says "Real-time", "Reconnecting", "Delayed · updated 12s
  ago" or "Loading", never "Live" (LIVE is only the trading mode), and it shows on a phone too.
- **The daily loss limit's sandbox figure** is worked out at the moment it is read, from the
  sandbox's positions and prices with the sandbox's own arithmetic (a feed price at most 5 s old,
  as on Positions; Upstox's quote answers kept for 1 s). The Risk card now moves with the price and
  matches Positions, and a part exit is never counted twice. Before, the card and the limit read a
  stored figure that moved only while some screen had the sandbox positions open, so they could
  judge an old loss. An exit never waits for this figure: only an order that adds risk reads it.

### What you must do
- Nothing to set. At the first sandbox session after it installs, open Risk with a sandbox
  position open and see the day's figure move with the price and match Positions.

### What is refused now that used to be accepted
- **New sandbox orders pause when today's sandbox figure cannot be worked out**, while the daily
  loss limit is on: an open position with no feed price for 5 s and Upstox's quote refused, busy or
  slower than 4 s. The order answers "New orders are paused: today's P&L could not be read from the
  sandbox, so the daily loss limit cannot be checked. ...", and the banner, chip and card say
  "today's P&L cannot be read · new orders paused, exits go" until a price comes. Before, the order
  was judged on the last stored figure, however old. Exits, cancels, resting stops, Exit all and
  the kill switch still go.
  - An order that both closes and opens (a reversal from TradingView or the ticket) sends only its
    closing part meanwhile, as it does on a locked day.
  - A basket that adds risk is refused whole, as on a locked day; a basket of exits only goes.
  - A sandbox GTT whose order triggers meanwhile is rejected and Telegram says it will not fire
    again: place it again once prices are back.

### Not in this release yet
- The Live stage of the risk guard is unchanged and still not switched on for Live orders. When it
  is, the phone's 60 s wait for the kill switch's report may end before a Live report arrives, and
  a cancel it made then reads "Cancelled at Upstox, not from this screen."
- Under the default worker, Upstox's own wait after "too many requests" can still use up the 4 s
  quote read (the price band's quote read has the same limit).
- Today's booked P&L is still the sandbox funds row's, reset by the sandbox's daily job.
- An expired contract the sandbox has not settled yet is valued at the price its settlement will
  use (its last stored price), not at a market price.
- There is still no screen to create a GTT order.

## v0.6.0 (8 Nov 2026)

The screener: your own screens over F&O stocks, futures, indices, option strikes or a watchlist,
checked at every candle close, ranked, fed into a watchlist and told on Telegram. The portfolio
gains loans on a tab, notes on prices, yield on cost, a dashboard card, and the Upstox trade
history and P&L. It includes everything in v0.5.2. **Publish it only after v0.5.2 has installed
and run one trading day. The screener ships switched
off: publishing this release changes nothing you trade with, and nothing is screened until you
switch it on after the two checks below.**

### What changes for you
- **The Screener page (menu: Screener), switched off by this release.** While it is off the page
  says nothing is being screened because the screener is switched off on the server, the
  dashboard card is not drawn, and the service opens no database and follows no instrument at the
  price feed (when it starts, it only asks the feed to let go of any it followed before). The
  screener itself never places an order; Buy and Sell on a results row open the usual order
  ticket.
- **What it does once you switch it on:**
  - A screen is a set of conditions in plain words (price, indicators, price action, the opening
    range, VWAP, candle patterns, open interest and option figures) over a universe: F&O stocks,
    stock or index futures, indices, NIFTY 50 or NIFTY BANK, option strikes around the money, or
    one of your watchlists.
  - It is checked 5 s after every close of its candle (1 minute up to a day) or once after the
    close, from the price feed's ticks, for up to 320 instruments followed tick by tick. An
    instrument it cannot judge says why ("Not checked: ...") and never counts as a miss.
  - Matches are ranked as the screen says and can be added to a watchlist: a new
    "Screener: <name>" list or one of yours. Each row says when it came in, how long it has been
    there, whether it is missing and when it left; a row you remove stays out ("Removed by you"),
    your own items are never touched, and you can pin a row.
  - A run worth telling sends one Telegram line through market alerts, with a toast whose View
    opens the screen, under the rules you set for each screen: fewest matches, once per stock a
    day, skip empty checks, pause after the first, a cap. **Market alerts must be switched on for
    it: they ship off, and with them off the run says "No alert sent: ..." and nothing reaches
    Telegram.**
  - Results move as each run arrives, and their prices with every tick (the first 100 rows). The
    History tab rewinds any run of the last 100 days to its whole match set.
  - The Library has nine ready-made screens. Two can be used now: ORB breakout and VWAP reclaim.
    Six say what they wait for (history before today, or the daily store of open interest and
    implied volatility); Delivery spike needs the daily delivery file.
  - The dashboard gets a "Screener today" card beside Holdings.
- **The ring services share one slice**, whether the screener is on or off: together they keep the
  CPU share they had before and use at most 2.5 GB of memory (slowed from 2 GB); each service
  keeps its own smaller limits.
- **The deploy** installs `sudarshan-screener` and writes `SUDARSHAN_SCREENER='off'` into
  `/etc/sudarshan/screener.env` only when the file has no value. It never writes `on` and never
  changes a value you set.

- **Portfolio:**
  - **Loans belong to a tab.** A tab with a loan shows net worth: what you hold less what you owe.
    Loans entered before this release appear under "Loans not on a tab" on the Summary, each with
    "Move to a tab".
  - **Notes on prices.** A widget in Customize lists every price you keep by hand, with its date. A
    fund NAV you type stands on its own day and gives way to a newer AMFI NAV.
  - **Yield on cost:** the income of the last 12 months on the shares that received it, divided by
    what those shares cost; and income by financial year.
  - **The dashboard card**, off until you switch it on, on each phone: the Upstox account and up to
    4 pinned tabs. When Upstox is slow the card shows "reading"; an account it could not read shows
    "Not read" with the time of the last good read, never Rs 0.00.
  - **Trades and P&L on the Upstox tab:** the trade history of three financial years and the
    trade-wise P&L with every charge listed. A charge Upstox does not give reads "not given", never
    0. A year counts as final only when it was read in full on or after 3 April; "Read again"
    re-reads any year, still inside SUDARSHAN's own reading budget (when it is spent it says so and
    keeps the figures it had). A year with more trades than one read holds (10,000 in a segment) is
    read again in steps, each from the page the last one stopped at, and the note says so. The
    steps go on at the nightly read, when you open the year, or when you tap Read again, and the
    figures shown stay the last complete read until the last step is in.

### What you must do
- **Nothing when you publish.** The screener stays off.
- **Before switching it on, the replay check on a recorded day, with the screener still off.** After
  a trading day's close, run the commands under "The replay check" in
  `sudarshan/screener/README.md`, the part for the first switch-on. It passes when it prints a line
  for each 5-minute close and ends with "N runs replayed from ...".
- **Then one live session, on a non-expiry trading day** (about 20 minutes on the phone):
  - before 09:15, set `SUDARSHAN_SCREENER='on'` in `/etc/sudarshan/screener.env` and run
    `systemctl restart sudarshan-screener`; the Screener page no longer says it is switched off;
  - make VWAP reclaim from the Library with a new watchlist, and resume it (a ready-made screen
    starts paused), **also before 09:15**: an instrument followed from the moment you make a
    screen counts its volume from then, but its day's open, high, low and the gap are taken from
    that moment too, so a screen made at 10:00 judges them from 10:00;
  - switch market alerts on first if you want the Telegram line to arrive;
  - between 11:00 and 12:00, compare one 5-minute run with TradingView for two matches and two
    non-matches: the 5-minute VWAP and the 20-candle volume average. Nothing can match before
    about 10:55, when the average has its 20 candles. (The plan named 15-min EMA cross, which waits
    for history in this release.)
  - the phone watchlist shows the new list with its Auto tag and "New" chips; remove one row by hand
    and see it stay out ("Removed by you" on the screen's membership);
  - one Analyzer-mode Buy and Sell from a results row;
  - after the close, the replay check with `--from-db --compare` (the README's first command) ends
    with "all match", and the 1-minute bars of five instruments match Upstox's intraday candles.
- **Keep it on only when both checks are clean.** To switch it off, set `'off'` and restart: it then
  lets go of its instruments at the price feed.
- **Run the compare again after the second day on.** The faults the release review found showed
  from day 2 (yesterday's bars counted as today's, members not carried over); the fixes are tested
  across midnight but have not yet met a real second day.

- **Check the Upstox tab's Trades and P&L once against Upstox's own P&L report** for the last
  financial year: the totals and the charges. If every year shows "partial" with a count note,
  tell me: Upstox counts trades differently from what was built.

### What is refused now that used to be accepted
- Nothing existing changes. New, on the Screener page: a 13th screen checking during the day
  (made, resumed or edited), more than 6 previews or 60 changes a minute, and every screener
  request from a family (view-only) session, reads included.

### Not in this release yet
- Portfolio: the TDS flag on dividends, the PDF export and family sharing of tabs.
- **History before today and the daily stores** (open interest, implied volatility, India VIX) wait
  for the feed's request governor, which ships off. Until you turn it on, a screen that needs more
  candles than one day gives says so ("Not checked: needs 151 candles of 15 minutes; a day gives
  25, ..."), the six ready-made screens above cannot be used, and the option chain is not
  snapshotted after the close.
- **Open points from the review:**
  - an option window is not moved during the day, and a watchlist universe is read again only when
    a screen changes or the universes are rebuilt;
  - a screen that would pass 320 instruments is not refused when saved: the instruments past the
    limit read "Not checked: past the 320 instruments the screener can follow tick by tick";
  - every instrument that is not checked is still stored at every run (values 7 days, the run 100);
  - a stock in a digest that never reached Telegram still counts as told for that day;
  - after a screen moves to another watchlist, the items it added to the old list stay there;
  - a plain `systemctl stop` keeps the screener's instruments at the feed for the restart, as
    market alerts do: switch it off, or remove it, to let them go;
  - a screen paused and resumed during the day, or an instrument that leaves the screener's list
    and comes back, counts the volume traded meanwhile into its first candle after it comes back,
    so that candle can look like a volume spike. The screener ships off and this is fixed before
    it is switched on; until then, make and resume screens before 09:15;
  - the day's open, high, low and gap of an instrument first followed during the session start at
    that moment, so a screen made at 10:00 judges them from 10:00.

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
  If it does not, the install rolled back (the database goes back as it was, and the server's
  Telegram notice says it rolled back), so keep single NIFTY orders within 27 lots and BANKNIFTY
  within 30 until it is installed, and tell me. In Analyze mode, a 30-lot
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
