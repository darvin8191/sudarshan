# sudarshan-portfolio (M7, M3.11): the Upstox tab and the owner's own portfolios

The owner reads his money from this, so every number is an integer or an exact decimal, and every
rule that produces one is pinned by golden vectors that Python and the phone both run.

There are two kinds of tab. The **Upstox tab** is a read-only view of the Upstox account
(holdings, positions, MTF positions, funds). A **custom tab** is a portfolio the owner keeps
himself, by hand or by file: shares, ETFs, mutual funds, gold, deposits, anything with a value.
Every figure on a custom tab comes from entries (a ledger) and prices; nothing is typed in as a
total. It never places or changes an order.

Status: this branch (`feat/portfolio`) holds the contract, the pure core, the store and the
service skeleton. The Upstox reader, prices, closing records, mutual-fund NAVs, the import ops,
the bridge in OpenAlgo and the screens follow; `sudarshan/STATUS.md` has one row per feature.

```
Phone (/portfolio)
  prices   OpenAlgo's WebSocket proxy, ticks drawn once a frame        (valuation.ts)
  data     /sudarshan/portfolio/api/*  (session, owner, CSRF, limiter)
             -> blueprints/sudarshan_portfolio.py   (inside OpenAlgo, thin; no database)
             -> sealed POST http://127.0.0.1:5583/v1   (loopback)

sudarshan-portfolio (systemd, outside OpenAlgo; one writer)
  store    /var/lib/sudarshan-portfolio/portfolio.db
  core/    pure engines: money, ledger, valuation, performance, history, allocation
  imports/ generic CSV and the Zerodha tradebook
```

Absolute imports only: upstream owns a top-level `portfolio` package (the backtester) and
`/api/v1/portfolio/*`. This service is `sudarshan.portfolio` and `/sudarshan/portfolio/api/*`.

## Where things are

| path | what | built in |
|---|---|---|
| `core/kinds.py` | the contract: units, kinds, limits, sentences, op names | PF0 |
| `../../frontend/src/sudarshan/portfolio/types.ts` | its twin on the phone, and every JSON shape | PF0 |
| `core/days.py`, `core/money.py`, `core/words.py`, `core/model.py`, `core/ledger.py` | days, scales and rounding, the owner's words, the FIFO ledger | PA1 |
| `core/ledger_actions.py` | splits, bonus shares, transfers | PA2 |
| `core/valuation.py`, `core/daylines.py`, `core/freshness.py` | value, Today and a tab's totals; one day's holding lines from the ledger; how old a price is | PA3 |
| `core/performance.py`, `core/history.py` | XIRR, TWR, net money put in, daily series, benchmarks | PA4, PA5 |
| `core/allocation.py`, `core/filters.py` | allocation by class, sector, cap, account, member, tag; filters | PA6 |
| `imports/` | generic CSV, Zerodha tradebook | PA7, PA8 |
| `store.py`, `migrations.py`, `migrate.py`, `book.py` | portfolio.db, numbered migrations, tabs, entries | PB1, PB2 |
| `service.py`, `api.py`, `config.py`, `__main__.py`, `ops/` | the process and its sealed local API | PB3 |
| `tests/vectors/` | the golden vectors (JSON), computed independently of the code | every PR |

`core/` performs no I/O of any kind (no database, network, file, clock or logging); a structural
test holds every module in it to that.

## Units

Every service boundary and every store uses one unit (cross-design X3).

| what | unit | name | example |
|---|---|---|---|
| money | integer paise | `*_paise` | `142825` is ₹1,428.25 |
| quantity | integer `qty_u`, units x 10^6 | `qty_u` | `10_000_000` is 10 shares, `109_459_000` is 109.459 units |
| price | integer `price_u`, rupees x 10^4 | `price_u` | `456789` is ₹45.6789 |
| percentage | Decimal string, in percent, 4 decimals | `*_pct` | `"8.0000"`, `"-2.5000"`; never a float |
| instant | epoch milliseconds | `*_at` | |
| day | integer `YYYYMMDD` in IST | `day`, `trade_date`, `lot_date` | `20261108`, a trading day or not |

- The **value** of a holding line (one account's quantity of one asset) is
  `round(qty_u x price_u / 10^8)` paise, half away from zero. Lines are rounded one by one and
  then added, so every sum on a screen is a sum of figures on that screen.
- A listed price from the feed becomes `price_u` as `paise(v) x 100` (`sudarshan.alerts.market.grain`).
- At most 10^9 units of one holding: `qty_u` stays below 2^53 as a browser number, and the phone
  multiplies with BigInt. `price_u` and every sum of paise stay below 2^53 too (the limits are in
  `kinds.py`).
- A percentage is computed from integers, rounded half away from zero to four decimals, written
  with a plain minus, and never `-0.0000`. It is never stored.
- Shares of a listed asset are whole numbers. Mutual-fund units have up to four decimals, grams
  three, and a crypto quantity is rounded to six.

## The sealed local API

### Transport

The phone calls OpenAlgo's bridge, `/sudarshan/portfolio/api/*`: an OpenAlgo session, the owner's
and nobody else's, CSRF on writes, and a limiter (reads 120/min, writes 60/min, imports 10/min,
dry runs 120/min). The bridge forwards each request to the service on `127.0.0.1:5583` as one
sealed POST. The body is one Fernet token holding `{"op": "<name>", "nonce": "<32 hex>", "args":
{...}}`, under the key purpose `sudarshan/portfolio/api/v1` (`sudarshan/vault/keys.py`): it
expires after 60 s, its nonce is accepted once (`NonceBook`, answering 503 when full), and it is at
most 64 KiB (`import_preview`: 2 MiB, because a token is base64 and a file of 1.2 MB seals to
1.5 to 1.9 MiB). The JSON inside is written compact, with non-ASCII text left as UTF-8. The answer
is plain JSON.

The bridge waits 3 s for a read, 6 s for a write, 15 s for an import and 12 s for the Upstox ops.
A write is never retried by the bridge. A timeout on a write is told as "Not confirmed", never
as a failure.

The service also answers an unsealed `GET /health`: switch, versions, job states and counts, no
owner data (`PortfolioHealth`).

### Arguments

- `id` is the number in the route (`/tabs/<id>`). On `activity_update`, `activity_delete` it is the
  entry; on `import_commit` and `import_discard` the batch; on `asset_update` and
  `price_point_set` the asset; on the routes under `/tabs/<id>` the tab.
- A GET's other arguments come from the query string, read as the *query* column says. `day` is
  `YYYYMMDD`; `ints` and `strs` are comma-separated lists.
- Every other op takes a JSON body of at most 64 KiB.
- The bridge builds `args` from these three sources and checks only their size and JSON type;
  the service validates everything else.

### The answer

```
{"status": "success", "enabled": true,  "data": {...}}
{"status": "success", "enabled": false, "data": null}
{"status": "error", "enabled": true, "error": {"code": "refused", "message": "...", "entry_id": 12}}
```

A switched-off portfolio is not an error: every op answers `enabled: false` and the screen says
"The portfolio is switched off on the server." An error carries a `code` for the program and a
`message`, a sentence for the owner: never a status code, an exception class or a protocol term
(`log/errors.jsonl` has those).

| code | HTTP | who | when |
|---|---|---|---|
| `not_ready` | 501 | service | the op is not built yet |
| `bad_request` | 400 | service | an argument is missing, of the wrong kind or out of range |
| `not_found` | 404 | service | the tab, entry, asset or batch is gone |
| `version_conflict` | 409 | service | it was changed on another screen; nothing was written |
| `refused` | 422 | service | the ledger or a rule says no; `entry_id` names the entry in the way |
| `unavailable` | 503 | service, bridge | the record could not be opened, or the service is not answering |
| `not_confirmed` | 504 | bridge | a write was sent and no answer came back |
| `unauthorized` | 401 | bridge | no session |
| `forbidden` | 403 | bridge | a session that is not the owner's |
| `rate_limited` | 429 | bridge | over the limiter |

The phone reads the code, never the HTTP status.

### Ops

*request* and *answer* are the TypeScript types in `types.ts`; the *query* column lists
`name:type` for the query-string arguments. `PortfolioOps` in `types.ts` pairs every op with its
two types, and a test holds this table, that map and `kinds.py` to each other.

| op | method | route | limiter | query | request | answer | notes |
|---|---|---|---|---|---|---|---|
| `state` | GET | `/state` | read | | `NoArgs` | `PortfolioState` | tabs, members, accounts, job states |
| `summary` | GET | `/summary` | read | | `NoArgs` | `PortfolioSummary` | the dashboard card's live-math inputs |
| `tab_create` | POST | `/tabs` | write | | `TabCreateArgs` | `TabInfo` | |
| `tab_get` | GET | `/tabs/<id>` | read | `as_of:day, view:int, accounts:ints, classes:strs, tags:strs, held:str` | `TabGetArgs` | `TabView` | |
| `tab_update` | PATCH | `/tabs/<id>` | write | | `TabUpdateArgs` | `TabInfo` | |
| `tab_delete` | DELETE | `/tabs/<id>` | write | | `TabDeleteArgs` | `Deleted` | archived tabs only, name typed |
| `activities_list` | GET | `/tabs/<id>/activities` | read | `before:str, limit:int, asset:int` | `ActivitiesListArgs` | `ActivitiesPage` | newest first |
| `activity_create` | POST | `/tabs/<id>/activities` | write | | `ActivityCreateArgs` | `ActivitySaved` | `dry_run`, `request_id` |
| `activity_update` | PATCH | `/activities/<id>` | write | | `ActivityUpdateArgs` | `ActivitySaved` | the entry replaces the old one |
| `activity_delete` | DELETE | `/activities/<id>` | write | | `ActivityDeleteArgs` | `ActivityDeleted` | |
| `asset_create` | POST | `/assets` | write | | `AssetCreateArgs` | `Asset` | |
| `asset_update` | PATCH | `/assets/<id>` | write | | `AssetUpdateArgs` | `Asset` | |
| `price_point_set` | POST | `/assets/<id>/prices` | write | | `PricePointSetArgs` | `PricePoint` | a manual value or a liability's balance |
| `mf_search` | GET | `/mf/search` | read | `q:str, limit:int` | `MfSearchArgs` | `MfSearch` | |
| `account_create` | POST | `/accounts` | write | | `AccountCreateArgs` | `Account` | |
| `account_update` | PATCH | `/accounts/<id>` | write | | `AccountUpdateArgs` | `Account` | |
| `member_create` | POST | `/members` | write | | `MemberCreateArgs` | `Member` | |
| `member_update` | PATCH | `/members/<id>` | write | | `MemberUpdateArgs` | `Member` | |
| `import_preview` | POST | `/tabs/<id>/imports` | import | | `ImportPreviewArgs` | `ImportPreview` | upload |
| `import_commit` | POST | `/imports/<id>/commit` | write | | `ImportCommitArgs` | `ImportCommitted` | |
| `import_discard` | DELETE | `/imports/<id>` | write | | `ImportDiscardArgs` | `ImportDiscarded` | drops a preview or undoes an import |
| `export_csv` | GET | `/tabs/<id>/export.csv` | read | | `Targets` | `ExportFile` | download |
| `performance` | GET | `/tabs/<id>/performance` | read | | `Targets` | `PerformanceReport` | |
| `layout_get` | GET | `/tabs/<id>/layout` | read | | `Targets` | `LayoutState` | |
| `layout_set` | PUT | `/tabs/<id>/layout` | write | | `LayoutSetArgs` | `LayoutState` | `spec: null` resets |
| `view_list` | GET | `/tabs/<id>/views` | read | | `Targets` | `ViewList` | |
| `view_create` | POST | `/tabs/<id>/views` | write | | `ViewCreateArgs` | `SavedView` | |
| `view_update` | PATCH | `/views/<id>` | write | | `ViewUpdateArgs` | `SavedView` | |
| `view_delete` | DELETE | `/views/<id>` | write | | `Targets & Versioned` | `Deleted` | |
| `upstox_book` | GET | `/upstox` | upstox | `parts:strs, max_age:int` | `UpstoxBookArgs` | `UpstoxBook` | reads Upstox |
| `upstox_history` | GET | `/upstox/history` | upstox | `limit:int` | `UpstoxHistoryArgs` | `UpstoxHistory` | |

The bridge builds all of these routes from one table (`OPS` in `kinds.py`), so a later op never
means editing it. An op the service does not have yet answers `not_ready` ("This part of the
portfolio is not ready yet."). A `dry_run` on `activity_create` and `activity_update` counts
against the dry-run limiter, not the write limiter.

### Sentences said word for word

`WORDS` in `kinds.py` and `types.ts` holds the sentences the bridge, the service and the phone
say identically: the switched-off line, "not ready", "not answering", "Not confirmed: open the tab
to see whether it was saved.", the version-conflict line, the store failures, the draft-date rule,
the 50-tab limit, the 1.2 MB import limit. Anything that depends on a number or a name is built
where the number is known.

## What the shapes mean

`types.ts` is the single list of fields. These are the rules behind them.

### Versions and request ids

- A write that changes something existing sends the `version` it was shown. A **tab's version**
  moves with every change to the tab or its entries: an entry saved, edited or deleted, an import
  committed or undone, a rename. The layout, an asset, an account, a member and a saved view each
  have their own. A stale version answers `version_conflict` and writes nothing.
- Every create sends a `request_id` (a UUID). The service keeps it with the row, and a create
  sent again with the same id (the first answer was lost) returns the row already made, never a
  second one. An edit or a delete is protected by its version instead.
- The answer to a write carries the new version (`tab_version` for an entry).

### Fields by kind

An entry (`ActivityInput`) is a row of the ledger. `status` is `posted` or `draft`; a draft counts
nowhere, and a date after today can be saved only as a draft. Every kind takes `note`.
`amount_paise` is authoritative; when it is missing and `price_u` is given, the service uses
`round(qty_u x price_u / 10^8)`.

| kind | needs | may have | lots | money for returns |
|---|---|---|---|---|
| `BUY` | account, asset, date, quantity, amount | price, charges (and their breakdown) | a new lot: cost = amount + charges, dated the trade | in: amount + charges |
| `SELL` | account, asset, date, quantity, amount | price, charges | FIFO consumes lots; gain = (amount - charges) - FIFO cost | out: amount - charges |
| `RIGHTS` | as `BUY` | | a new lot, as a buy | in |
| `DIVIDEND` | account, asset, date, amount (gross) | TDS | none | out: the gross amount |
| `INTEREST` | account, date, amount (gross) | asset, TDS | none | out: the gross amount |
| `FEE` | account, date, amount | asset | none | a cost: neither in nor out, counted against the return |
| `TRANSFER` | asset, date, quantity, `account_id` (from; null is from outside), `to_account_id` (to; null is to outside), not both null | from outside: `amount_paise` (the lot's cost), `lot_date` (its first purchase), `value_paise`; to outside: `value_paise` | between accounts: lots move with cost and date; from outside: an opening lot; to outside: lots leave at cost, no gain | between accounts: none; from outside: in at the value; to outside: out at the value |
| `SPLIT` | asset, ex-date, `ratio_num`, `ratio_den` | account (null: every account holding it) | quantity x num / den; cost and date kept | none |
| `BONUS` | account, asset, date (allotment), quantity | | a new lot at nil cost dated the allotment | none |

"Each share becomes 5" is `ratio_num` 5, `ratio_den` 1; "1 for 10" is 1 and 10. An opening holding
is a transfer from outside dated its original purchase date, with the value equal to the cost. A
valuable or a loan is an asset of kind `manual` or `liability`; a liability's balance is a price
point, and it stays outside every return figure.

### Holding rows, lines and Today

A tab answers `rows`, one per asset. Each row has the asset, its price (`PriceInfo`), how fresh
the price is, and `lines`, one per account that holds it. Everything the phone needs to price a
line as ticks arrive is an integer on the line or the row.

For a listed line the service sends `qty_u` (now), `open_qty_u`, `in_today_paise`,
`out_today_paise` and `cost_paise`, and the row's `prev_close_u`.

- `open_qty_u` is the quantity at the open: after today's splits and bonuses (they take effect
  before trading, and the exchange's previous close is already adjusted for them), before today's
  trades.
- `in_today_paise` and `out_today_paise` are today's acquisitions and disposals at their gross
  trade amounts, charges left out. A transfer between two accounts of the tab counts on both
  sides at the previous close, so it nets to nothing in the tab and moves no gain between
  accounts. A transfer from or to outside counts at its `value_paise`. Dividends, interest and
  fees never count.
- The previous close is the latest official close before today. When the price on hand is from
  an earlier session (the market has not opened, or it is a holiday), the price is that close,
  and Today is only the effect of today's trades.

**Today** for a line is `value now - (open value + in - out)` in paise, where `value now` is
`round(qty_u x price_u / 10^8)` and `open value` is `round(open_qty_u x prev_close_u / 10^8)`.
Its percentage is Today over `open value + in`. A holding sold out today still counts in the
tab's Today (`closed_today_paise`) and is hidden from the list. Income and fees never count in
Today.

| `today_mode` | meaning | state of the figure |
|---|---|---|
| `ok` | a listed price, or a fund whose NAV is dated today | `ok`, or `unknown` when a price it needs is not known: "Today: not known yet", never 0 |
| `pending` | a mutual fund whose NAV is not dated today | `pending`: "NAV changes count when AMFI publishes them, around 9 PM." |
| `none` | a value kept by hand | `none` |

A tab's Today adds the rows that are `ok` and the holdings sold out today; `counted`, `unknown` and
`pending` say how many listed rows are in it and how many are left out. The rules in full are
under "Valuation and Today" below.

### Totals

- value = the sum of line values; **invested** = the sum of open lot cost (charges included);
  unrealised = value - invested. A holding with no price yet is counted at its cost, and
  `TabTotals.unpriced` says how many.
- **overall gain** = unrealised + realised + income - fees. Realised is the sum of sale gains,
  income the dividends and interest (gross), fees the standalone fees.
- **absolute return** = (value + money out - money in - fees) / (money in + fees), money in and out
  as the table above has them. Absent when nothing was put in.
- `TabTotals` holds these at the service's own prices; `TabFixed` holds the parts that do not move
  with a price, so the phone can recompute the rest from ticks.

### Closing auction

A stock that closes by auction (`cas`) stops continuous trading before the cash close. Every
listed row in `tab_get`, `summary` and `upstox_book` carries `cas` and three moments in epoch ms:
`auction_from` (continuous trading has ended), `auction_until` (the latest the auction's price may
come) and `auction_close` (when it is expected, for "price at about 15:30"), from
`sessions.auction_window` and `sessions.auction_close`. All three are null for a stock that does not
close by auction. The phone never computes sessions; its words are
`frontend/src/sudarshan/live/auction.ts`.

### The Upstox tab

`upstox_book` answers the parts asked for. Each part carries `state` (`ok`, `stale`,
`unavailable`), when it was read and a sentence when it is not fresh ("Upstox did not answer.
Figures from 10:41:05."). The phone starts from Upstox's own figures at the read and moves them
with ticks: value = quantity x tick; P&L = Upstox's P&L + quantity x (tick - the price at the
read); Today = quantity x (tick - Upstox's close). This is Upstox's convention, so the tab agrees
with the Upstox app; custom tabs use the exact rule above. The funds tree is data-driven (groups
of labelled lines). Funds are not read between 00:00 and 05:30 IST.

### Import and export

`import_preview` takes the file as UTF-8 text (`text`), at most 1,200,000 bytes and 5,000 rows;
the sheet checks both before it sends, and a bigger file is told in the `import_too_big` sentence
and never sent. Text, not base64: the seal is base64 already, and base64 inside it would not fit
2 MiB. If a file under the limit still seals to more than 2 MiB (every field quoted and then
some), the bridge tells the same sentence. It stores the normalised rows, never
the file, and answers the counts, the problem rows first in words, and the tab version it saw.
`import_commit` writes them in one transaction if the tab version still matches. `import_discard`
drops a preview, or removes a committed import's rows if the tab stays valid (otherwise it is
refused and names the entry that depends on them). A row's duplicate key is the format's own id,
else a hash of its content plus its occurrence number in the file. `export_csv` writes the same
generic format, so that exporting and importing into an empty tab gives the same ledger.

## Ledger, valuation, performance, allocation, imports

The ledger (`PA1`, `PA2`), the valuation (`PA3`) and the performance figures (`PA4`) are written;
the daily history and benchmarks, allocation and the two file formats are written with the PR that
builds each (`PA5` to `PA8`).

### The ledger

A tab's ledger is its **posted entries replayed from the start**, never a running total
(`core/ledger.py`, `replay`). An edit, a delete or an addition is checked by replaying the tab with
the change, so the ledger can never disagree with the entries it is made of. Drafts count nowhere,
and `as_of` (a day) stops the replay there.

**FIFO is per account and asset within a tab.** A sale takes the lots of its own account first
bought first: by the day the lot was acquired, and for lots of one day in the order they were
made. A lot keeps its day when it moves (a transfer), so it counts as bought when it was
bought. A sale beyond what the account holds on that day is refused; it never goes short or
borrows from another account.

**The day has its own order**, not the order the owner typed. A sale typed before the purchase of
the same day is valid.

| group | entries | why |
|---|---|---|
| 0 | splits, bonus | the ex-date, before trading |
| 1 | purchases, rights, transfers in from outside | what was bought is there to sell |
| 2 | transfers between two accounts of the tab | moved shares can be sold in the new account the same day |
| 3 | sales, transfers out to outside | |
| 4 | dividends, interest, fees | they touch no lot |

Inside a group the entry's `seq` decides (an import keeps file order), then its `id`. Purchases,
rights, sales, dividends, interest and fees are handled in `core/ledger.py`; splits, bonus shares
and transfers in `core/ledger_actions.py`, which joins the same table of handlers.

**Numbers.**

- A purchase or rights lot costs `amount + charges`. The amount is what was typed; only when none
  was typed does it come from `round(qty_u x price_u / 10^8)`.
- A sale's gain is `amount - charges - cost of the lots it took`. A sale of part of a lot takes
  `round(remaining cost x part / remaining quantity)` paise of its cost, half away from zero, and
  taking a whole lot takes exactly what is left. Three sales of one unit from a 10,000 paise lot
  of three cost 3,333, 3,334 and 3,333, so a lot's cost is used up to the paisa and never lost or
  invented. Allocating from the original lot instead would give 3,333, 3,333 and 3,334.
- Income (dividend, interest) is counted **gross**; the TDS is shown beside it, not taken off.
  Fees are counted on their own. Neither changes a lot.
- Every figure is an integer. Rounding happens in exactly one function, `money.round_div`
  (half away from zero, so a loss mirrors a gain). Python's `round` and floats never touch a
  figure; a test compares the code with Decimal's `ROUND_HALF_UP` on thousands of numbers.
- Percentages are `money.pct(numerator, denominator)`: four decimals, half away from zero, a plain
  minus, never `-0.0000`, and `None` when there is nothing to divide by.

**Held more than a year** (`days.held_over_year`) means strictly after the first anniversary: a lot
bought 5 Jan 2024 is over a year old from 6 Jan 2025, and one bought on 29 Feb 2024 has its
anniversary on 28 Feb 2025. A figure for the screen, not tax advice. The financial year runs April
to March (`days.financial_year`).

**Splits.** `SPLIT` multiplies every lot of an asset by `ratio_num / ratio_den` (each share
becomes 5 is 5 over 1; one new share for every 10 old is 1 over 10). The lots keep their cost and
their day, so the cost of a unit changes and the cost of the holding does not, and a later sale
makes the gain it would have made had nothing happened to the shares. A split without an account
applies to every account of the tab that holds the asset that day, with one move for each, lowest
account first; with an account, to that account only. It is decided for every account before any
is changed, so an account that cannot take it refuses the split for all, and the lowest such
account is named. A split on an asset that nobody (or that account) holds that day is refused: "No
account holds RELIANCE on 12 Mar 2025, so a split cannot be saved. Check the date, or add the
missing purchase first." A split dated before the first purchase or after the last sale is almost
always a wrong date, and the sentence says to check the date first.

**What a split may leave.** The new quantity of a position must be exact and must fit the asset.
`replay(..., decimals={asset_id: places})` says how many decimals a quantity of each asset may
have (shares 0, fund units 4, grams 3); an asset left out is counted in whole units. A split that
would leave 1.5 shares is refused: "This split would leave 1.5 shares in Zerodha. Record the sale
of the fraction first, then the split." (the unit word is the asset's own: shares, units, grams).
A ratio that the total does not divide evenly (10 units by 1 over 3) is refused too, for any number
of decimals, because the ledger never invents the end of a repeating decimal. The lots themselves
may be fractions of a share when the total is whole: 5 + 5 shares consolidated 1 for 10 are two
lots of half a share, which together are one. They are rounded on the running total (the first
lot's share of the new total, then the first two lots', and so on) so that they always add up to
the new quantity: three lots of 1 share split 1 for 3 become 0.333333, 0.333334 and 0.333333, one
share together, where rounding each lot alone would make 0.999999. A split that would round a lot
to nothing (lots of a millionth of a unit) is refused with the same sentence, since a lot cannot
hold a cost and no quantity. Nothing re-checks the 100 crore unit limit of an entry after a split.

**Bonus shares.** `BONUS` adds a lot of `qty_u` at no cost, dated the allotment, to an account
that holds the asset that day, and is refused otherwise (same sentence as a split, "bonus"). A
bonus and a split dated the same day are applied in the order they were entered: a bonus entered
first is split with the rest, one entered after it is not. **Rights** are a purchase (`RIGHTS`): a
lot at the price paid, dated the day it was paid.

**Transfers** say which accounts they join. Between two accounts of the tab, the oldest lots move
with their cost and their acquisition day, in the account that receives them each lot queues by
that day (behind the lots it already has for the same day), so the units held over a year and the
order of a later sale are what they would have been had the shares always been there. Part of a
lot moves at `share_of_cost`, exactly as a sale takes it: moving 1 unit twice out of a 10,000 paise
lot of three costs 3,333 and then 3,334, and nothing is lost between the accounts. A transfer makes
no gain or loss and moves no money; its journal has two moves, one leaving and one reaching. *From
outside* (`to_account_id` only: the shares come from an account the tab does not track) opens one
lot at the cost entered, dated `lot_date` (the first purchase, if the owner knows it) or else the
transfer, so that it sorts with the other lots by the day it was really bought; `value_paise` is
what counts as money put in on that day (the value then) and defaults to the cost; the lot's cost
is always the cost entered. *To outside* (`account_id` only) takes the oldest lots out at their
cost with no gain or loss; `value_paise` says what counts as money taken out and defaults to the
cost.

**What a replay keeps.** `Ledger.positions` has one `Position` per (account, asset) with its lots,
its units and cost, and its realised gain, income and fees; an asset-less position holds an
account's own interest and fees. `Ledger.journal` has one `Move` per entry in the order applied: the
units and cost it added or took, the gain, the gross money, and for a sale the lots it used with
the cost given to each. Later numbers (Today, money put in, figures by financial year) are read
from the journal, so none of them needs a "today" argument inside the replay.

**Refusals are sentences for the owner.** `Refusal` carries `entry_id` (the entry that cannot
stand, `None` for one not saved yet), `reason` and `facts` (the numbers behind the sentence), and
its message is the sentence, for example "Zerodha holds 10 RELIANCE on 12 Mar 2025, so a sale of
12 cannot be saved. Add the missing purchase first." (`core/words.py`). `words.knock_on` says what
a delete, a change or an addition would break. An entry that is not well formed raises `BadEntry`
with a sentence that says what to fix ("A purchase needs a quantity."). Nothing in `core/` shows a
code, a class name or a field name to the owner.

**Vectors** (`tests/vectors/ledger.json`, `money.json`, `actions.json`): the design's F1 to F7,
C1 to C5 and its two dry-run sentences (D1, D2), plus cases for a half paisa of cost,
four-decimal fund units, a purchase and its sale on one day, drafts, `as_of`, income and fees,
FIFO by acquisition day and lots of one day; and for splits (lots that do not divide, a fraction,
every account or one, the day's order), bonus shares, rights and each way of transferring
(C1 to C9, TF1 to TF8). `make_pa1.py` and `make_pa2.py` next to them write the files, work every
number out with exact fractions and Decimal, and import nothing from `sudarshan`. A naive second implementation of the same rules
(`tests/portfolio_oracle.py`, plain dictionaries) is run on every vector and on hundreds of random
tabs and must agree with the engine to the paisa.

### Valuation and Today

Three small pieces, each pure. `daylines.lines_of(ledger, today, prev_close_u)` turns a ledger
replayed to `today` into the lines a screen prices. `valuation` turns lines and the price of the
moment into the figures of a holding (`row_figures`) and of the tab (`tab_totals`); the phone runs
the same sums as ticks arrive (`valuation.ts`) on the same vectors. `freshness` says how old a price
is (next section).

**A holding's figures.** Value is the sum of its line values, each rounded on its own; cost is the
sum of the lines' cost; unrealised is value less cost; the gain percentage is unrealised over cost,
absent when nothing was paid (bonus shares). A holding with **no price yet** is counted at its cost,
so that one missing price does not look like the loss of everything in it: it has no gain
percentage, its Today is `unknown`, and the tab's `unpriced` says how many there are. The screen
must say so ("2 holdings have no price yet and are counted at cost").

**Today for a line** is `value now - (open value + in - out)`. Each of the two values is rounded to
a whole paisa before they are subtracted, so Today is the difference of two figures on the screen
and not a rounded difference (one share at 0.0050 is worth a paisa at the open and, at 0.0149,
a paisa now: Today is 0, not the 0.99 of a paisa between the prices). It needs the previous
close only when units were held at the open, and the price only when units are held now: a share
bought today needs no previous close, a holding sold out today needs no price. A figure it needs
and does not have is `unknown`, never 0, and one line that cannot be worked out makes the whole
holding `unknown`. The percentage is Today over `open value + in`.

**Which close.** `previous_close_u` is the close Today is measured from. A price dated an earlier
day than today is itself the latest close (before the open, on a weekend or a holiday), so Today
is only the effect of today's trades; any other price is measured from the close it carries.
`today_mode` is `ok` for a listed stock, `ok` for a fund once a NAV dated today is in and `pending`
until then, and `none` for a value kept by hand and for a loan.

**The tab.** `tab_totals(rows, fixed)` adds the holdings held now (a row nobody holds is skipped, so
passing every row is harmless) and reads the rest from `Fixed`. Overall gain, absolute return and the
meaning of money in and out are as under "Totals". The tab's Today adds the rows that are `ok`
and **the holdings sold out today**, whose figure comes from `Fixed.closed_today_*` and is worked out
by `closed_today(rows)`: the sum of their Today and of their base, or `None` when one of them has
units at the open and no previous close, because a sum with a holding missing from it would be wrong
without saying so (the screen adds "not counting a holding sold today"). A fund still waiting for its
NAV is left out of it, as it is from the list. The tab's state is `ok` as soon as one row or a
closed holding is in the figure, else `unknown` when a row (or the closed figure) is not known,
else `pending`, else `none`; with nothing in the figure there is no number at all.

**Day lines.** The open is the quantity after today's splits and bonus shares and before today's
trades (the exchange's previous close is already adjusted for the split). Money in and out are
today's acquisitions and disposals at their gross trade amounts; shares brought in from or taken
out to outside count at the value entered; shares moved between two accounts count in one and out
of the other at the previous close, each side valued as a holding of that many shares would be, and
at nothing when the close is not known (that holding's Today is then unknown anyway). A position
that holds nothing and traded nothing today has no line. The units held more than a year are
counted as of today.

**What this does not model.**

- A day shares move between two accounts: the rupee Today is exact, but the percentage's base
  counts the moved shares once more (as money in on the receiving side), so the percentage is a
  little lower than the holding's own change that day.
- Today on the ex-date of a split or bonus takes the exchange's previous close to be adjusted. A
  price kept by hand does not know the split, so that one day's Today is wrong.
- Today uses the gross amount of a trade. Charges are in the cost and the gain, not in Today.

**Vectors** (`tests/vectors/valuation.json`, `daylines.json`): the design's VA3 to VA8 and cases
for every rule above (rounding line by line, the missing previous close or price, the fund that
waits, the value kept by hand, the sold-out holding with and without a close, the tab's states in
each order, the absolute return with fees, a loss, nothing put in), the close Today is measured from
and the replays R1 to R3 (the frames of a morning's ticks, as the phone repeats them); fourteen
ledgers turned into lines by hand in `daylines.json`. `make_pa3.py` writes them, works every number
out again with exact fractions and imports nothing from `sudarshan`. The tests run the engine on
every vector, run the generator's evaluator on thousands of random rows, and check identities
(a dearer price never lowers a value or a Today, the counts add up, no float is ever written);
`tests/portfolio_oracle.py` reads the lines from a naive replay on hundreds of random tabs.

### Freshness

Every price a holding is valued at carries the day it belongs to. `freshness.of_price` says
whether it is `fresh` or `old` and gives a sentence where a number alone would mislead. `today` is
the IST day now and `last_session_day` the latest trading day whose session has begun (the
Friday, before the market opens on a Monday); the service works out both, since `core/` knows
neither the clock nor the exchange's calendar.

| price | old when | words |
|---|---|---|
| a fund's NAV | more than 3 business days behind `last_session_day` (weekdays less the exchange's holidays) | "NAV of 22 Oct", and "; AMFI has not published a newer one." when old |
| a value kept by hand, a loan's balance | older than the tab's limit in days (30 unless the tab says; read as 7 to 365) | "Value from 12 Sep" or "Balance from 12 Sep", then "45 days old", "1 day old" or "today" |
| a listed price | its day is before `last_session_day` | none for a tick (the feed line speaks); "Last price 10:41:05 (quote)"; "Last price 15:29:58, 23 Oct (closing record)", ", taken late" when the service was down; "Close of 23 Oct"; "Price of 22 Oct; no newer price yet." |
| none yet | always | "No price yet" |

A "business day" needs the exchange's holiday list, which only the server has: the phone repeats
these rules on the same vectors (`freshness.ts`) for a value kept by hand and for a listed price,
and shows the server's answer for a fund's NAV. A day is written "27 Oct", with the year ("30 Dec 2025") when it is not the year of the day it is
compared with. The words say nothing about the feed ("Real-time" is for ticks arriving).
`days.business_days_between(start, end, holidays)` counts the weekdays after `start` up to and
including `end`, less the holidays that are weekdays in that range; `day_of_instant` and
`clock_of_instant` turn epoch milliseconds into the IST day and `HH:MM:SS` (the milliseconds are
dropped, not rounded). **Vectors** (`tests/vectors/freshness.json`): the design's FR1 to FR4, the
edge of every rule, the business days between two days with and without holidays, and the
instants around midnight, a leap day and the turn of a year.

### Performance

`core/performance.py` turns what moved in and out of a tab into the figures of the Performance
view: the money put in, the absolute return, the XIRR, the time-weighted return and the year's
figures. Money is paise and every percentage is text of four decimals (half away from zero); no
float is ever written. The one number that cannot be exact, a yearly rate found by solving an
equation, is found in `Decimal` to forty digits and shown to four.

**Money flows.** `flows_of(ledger)` reads them from the replayed ledger, one `Flow` for each day on
which any moved, oldest first (a day on which nothing moved has none):

| entry | in | out | fee |
|---|---|---|---|
| purchase, rights issue | amount + charges | | |
| sale | | amount - charges | |
| dividend, interest | | the gross amount (TDS is tax paid, not a loss) | |
| shares brought in from outside | the value entered, else the cost | | |
| shares taken out to outside | | the value entered, else the cost of the lots | |
| a fee on its own | | | the fee |
| a transfer between accounts, a split, bonus shares | nothing | nothing | nothing |

A sale whose charges are above its amount is a negative amount out (money in); nothing is clamped.
Charges on a trade are inside its flow and never fees. **Money put in** up to a day is
in + fees - out, **total gain** is the value less that, and the **absolute return** is the gain over
(in + fees): the figure of "Totals", worked out here from the flows. A tab that took out more than
it put in has a negative money put in. `summary(flows, value, day)` returns the money figures of
the `PerformanceReport` (those up to `xirr`) for a valuation day; a flow dated after it counts for
nothing.

**XIRR** is the yearly rate r at which the cash of each day, out less in and fees, with the value now
added on the valuation day, adds up to nothing when each amount is discounted by
(1 + r) ^ (days / 365): actual days over 365, as Excel counts. With q = (1 + r) ^ (-1 / 365) the sum
is a polynomial in q, so the solver (no package, no float) adds, multiplies and raises to whole
powers of the gaps between days in `Decimal`, which Horner's rule makes cheap (3,000 flows take a
third of a second). It walks outward from 0% on both sides together, in steps of a tenth of the log of
the rate over the span (growing 3% a step once past thirty-three), takes the first sign change on
each side and halves it to a bracket of 1e-17 of q. **When several rates fit, the one nearest zero on
the log scale wins**, a gain and a loss of the same size being equally near (+100% and -50%) and the
gain winning; cash that adds up to nothing has the rate 0 exactly.

- `shown` when the valuation day is at least 365 days after the first day with cash (a day whose
  purchases and sales cancel out starts nothing): 366 days of 1 Jan 2024 to 1 Jan 2025 and
  `1.1 ^ (365 / 366) - 1 = 9.9714%`.
- `later` before that: the view shows the absolute return and `from_day` with the sentence "XIRR
  appears after one year (from 1 May 2027)". The year is counted in days, so across a 29 February
  it arrives the day before the anniversary.
- `none` with "Return needs at least one purchase and a value." when no day pays or no day
  receives (only money in and nothing left; a value and no purchase), checked before the year so
  that a tab that lost everything is never promised a figure it will not get. A tab that sold
  everything still has an XIRR from its flows alone. `none` with "These purchases and sales do not
  give a single yearly return." when the cash changes sign but no rate fits (money in, money out at
  a profit, then in again and lost).

**TWR** multiplies the returns of the periods between complete days (a day with a closing value for
everything), each (value + out - fees) / (value before + in): money in works from the start of the
period and money out leaves at its end. A day that is not complete joins the next complete day's
period together with its money (Modified Dietz across the gap). A period with nothing at work is
skipped, and none loses more than everything in it, so a period that reaches nothing makes the
product nothing for good. The first period begins at the first complete day, or earlier when the
tab was known to be empty before the series (`opening_paise` 0, so that the first day's own return
counts). The product is exact (whole numbers, one division at the end) and the figure is labelled
"TWR since 3 Nov 2026"; it is annualised, as the same rate over 365 days, only when it spans more
than 365 days. With no period there is no figure, only "TWR needs closing prices for at least two
days."

**The year's figures.** `by_financial_year(ledger)` gives the realised gain, the gross income and the
fees of each financial year (April to March; 2026 is FY 2026-27) that has any, oldest first, and
`income_report(ledger, day)` the income of the financial year the day is in and of each of the
twelve months ending with its own, none shown as 0 and nothing after the day counted.

**What this does not model.**

- Cash that changes sign several times can have several rates; the one nearest zero is shown. Two
  rates closer together than one step of the walk are taken for none, and a rate beyond a loss of
  99.99999999% or a gain of ten billion times a year is not looked for.
- A dividend is its gross amount, whatever tax was withheld; charges on a trade are in its flow.
- The time-weighted return starts when every holding had a closing price; nothing before it is
  rebuilt.

**Vectors** (`tests/vectors/performance.json`): the design's X1 to X5, N1 and T1 to T5 and the edges
around them: the 365th day, a leap day, a round trip on the first day, a loss, a doubling, no gain
at all, fees, a flow after the day asked for, thirteen monthly purchases, rates beyond a hundred
thousand percent and a loss of nearly everything; rates of cash alone with two and three rates, a
tie, a rate too small to round and a day of one percent; the flows of sixteen small ledgers; the
periods of twenty-one daily series; the year's figures through 31 March and 1 April, a January
reading, the turn of a century and an income dated after the day asked for. Each case has its
derivation, and `make_pa4.py` finds every rate by another route than the code's (floats walked out
from 0%, halved, then polished with Newton's method on `Decimal` exponentials and checked by
substituting) and refuses to write a percentage that sits near a rounding tie. The tests also run
the code on random tabs and random cash against the flows read from the entries
(`portfolio_oracle.py`), a scan for every rate in floats and the periods multiplied as fractions,
and check what must hold whatever the numbers are: money put in is what the positions cost less
what was earned, scaling the money or moving the dates changes no rate, time read backwards
inverts it, and deposits in proportion change no time-weighted return.

### History, benchmarks, allocation, imports

Written with `PA5` to `PA8`.

## Storage

Written with `PB1`: `portfolio.db` under `/var/lib/sudarshan-portfolio`, numbered migrations,
`python -m sudarshan.portfolio.migrate --status`, the single writer, versions.

## The service

Written with `PB3`: the unit, its settings, the port, `/health`, the jobs.

## Tests

`uv run pytest sudarshan/portfolio/tests` (nice it on the shared box). Golden vectors are JSON in
`tests/vectors/`, written by the `make_*.py` generators beside them, which import nothing from
`sudarshan` and are the only place a number changes; each case carries the derivation that
explains its expected value. `valuation.json` and `freshness.json` are also read by the
phone's tests, so the two languages cannot drift. Fixtures are typed from documented field lists
and are synthetic: nothing is ever captured from an account.
