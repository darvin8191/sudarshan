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
| `core/ledger_actions.py` | splits, bonus, rights, transfers | PA2 |
| `core/valuation.py`, `core/freshness.py` | value, Today, how old a price is | PA3 |
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
most 64 KiB (`import_preview`: 1.25 MiB). The answer is plain JSON.

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
the 50-tab limit, the 900 KB import limit. Anything that depends on a number or a name is built
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
  accounts. A transfer from or to outside counts at its `value_paise`.
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
| `ok` | a listed price | `ok`, or `unknown` when the previous close is not known: "Today: not known yet", never 0 |
| `pending` | a mutual fund whose NAV is not dated today | `pending`: "NAV changes count when AMFI publishes them, around 9 PM." |
| `none` | a value kept by hand | `none` |

A tab's Today adds the lines that are `ok`; `counted`, `unknown` and `pending` say how many rows
are in it and how many are left out.

### Totals

- value = the sum of line values; **invested** = the sum of open lot cost (charges included);
  unrealised = value - invested.
- **overall gain** = unrealised + realised + income - fees. Realised is the sum of sale gains,
  income the dividends and interest (gross), fees the standalone fees.
- **absolute return** = (value + money out - money in - fees) / (money in + fees), money in and out
  as the table above has them. Absent when nothing was put in.
- `TabTotals` holds these at the service's own prices; `TabFixed` holds the parts that do not move
  with a price, so the phone can recompute the rest from ticks.

### Closing auction

A stock that closes by auction (`cas`) stops continuous trading before the cash close. Every
listed row in `tab_get`, `summary` and `upstox_book` carries `cas`, `auction_from` and
`auction_until` (epoch ms, from `sessions.auction_window`); the phone never computes sessions. Its
words are `frontend/src/sudarshan/live/auction.ts`.

### The Upstox tab

`upstox_book` answers the parts asked for. Each part carries `state` (`ok`, `stale`,
`unavailable`), when it was read and a sentence when it is not fresh ("Upstox did not answer.
Figures from 10:41:05."). The phone starts from Upstox's own figures at the read and moves them
with ticks: value = quantity x tick; P&L = Upstox's P&L + quantity x (tick - the price at the
read); Today = quantity x (tick - Upstox's close). This is Upstox's convention, so the tab agrees
with the Upstox app; custom tabs use the exact rule above. The funds tree is data-driven (groups
of labelled lines). Funds are not read between 00:00 and 05:30 IST.

### Import and export

`import_preview` takes the file as UTF-8 text (`text`), at most 900,000 bytes and 5,000 rows. Text,
not base64: base64 inside the seal would not fit 1.25 MiB. It stores the normalised rows, never
the file, and answers the counts, the problem rows first in words, and the tab version it saw.
`import_commit` writes them in one transaction if the tab version still matches. `import_discard`
drops a preview, or removes a committed import's rows if the tab stays valid (otherwise it is
refused and names the entry that depends on them). A row's duplicate key is the format's own id,
else a hash of its content plus its occurrence number in the file. `export_csv` writes the same
generic format, so that exporting and importing into an empty tab gives the same ledger.

## Ledger, valuation, performance, allocation, imports

The ledger is written (`PA1`); valuation, performance, allocation and the two file formats are
written with the PR that builds each (`PA3` to `PA8`): the Today rule above in full, XIRR and
TWR, benchmarks, allocation and filters.

### The ledger

A tab's ledger is its **posted entries replayed from the start**, never a running total
(`core/ledger.py`, `replay`). An edit, a delete or an addition is checked by replaying the tab with
the change, so the ledger can never disagree with the entries it is made of. Drafts count nowhere,
and `as_of` (a day) stops the replay there.

**FIFO is per account and asset within a tab.** A sale takes the lots of its own account first
bought first: by the day the lot was acquired, and for lots of one day in the order they were
made. A lot keeps its day when it moves (a transfer; `PA2`), so it counts as bought when it was
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

Inside a group the entry's `seq` decides (an import keeps file order), then its `id`. Which
handlers run: purchases, rights, sales, dividends, interest and fees today; splits, bonus and
transfers join in `PA2` (`core/ledger_actions.py`).

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

**Vectors** (`tests/vectors/ledger.json`, `money.json`): the design's F1 to F6 and its two dry-run
sentences (D1, D2), plus cases for a half paisa of cost, four-decimal fund units, a purchase and
its sale on one day, drafts, `as_of`, income and fees, FIFO by acquisition day and lots of one day.
`make_pa1.py` next to them writes both files, works every number out with exact fractions and
Decimal, and imports nothing from `sudarshan`. A naive second implementation of the same rules
(`tests/portfolio_oracle.py`, plain dictionaries) is run on every vector and on hundreds of random
tabs and must agree with the engine to the paisa.

### Valuation, performance, allocation, imports

Written with `PA3` to `PA8`.

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
