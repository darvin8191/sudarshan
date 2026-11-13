# sudarshan/watchlists/ - Watchlists v2 (M8.4 onwards)

Rules, contract and words for the multi-list watchlist features. Everything here is switched off
until `SUDARSHAN_WATCHLISTS_V2` is turned on. The pure modules take every input as an argument and do
no I/O, no clock and no logging; words a trader reads live in `words/<feature>.py`. This folder
starts no thread, timer or event loop. Tests: `sudarshan/watchlists/tests/` (vectors in
`tests/vectors/`, typed by hand, never made by the code they test).

## Why this runs inside OpenAlgo: the watchlists exception (9 Nov 2026)

The SUDARSHAN rule is that everything new lives in `sudarshan/` as separate systemd services that
talk to OpenAlgo through its REST API. Watchlists v2 is the one written exception. Its store
(`db/sudarshan_watchlists.db`), its bridge (`blueprints/sudarshan_watchlists.py`), the hook on
OpenAlgo's watchlist routes and the reads derived from the symbol master (search, strikes, built-in
lists, follow rows) run inside the OpenAlgo app. Nothing else moves in with it: list alerts stay in
`sudarshan-alerts`, and every other SUDARSHAN feature keeps its state in its own service. The risk
layer (`sudarshan/guard`) is in process too, but because it gates orders inside OpenAlgo's order
path; this feature judges nothing and sits in no order path, so the guard is a pattern for the
mechanics here (a file of its own in `db/`, NullPool engines, a migration in `upgrade/`), not a
precedent for placement. `.claude/rules/sudarshan.md` keeps the ring rule as written; this section
is the exception's record in the repository.

Why here:

1. **Rollback.** Every row is keyed to an OpenAlgo list id. A file in `db/` is backed up before a
   deploy and restored with `openalgo.db`; a service's own store is not, so after a rollback it
   would point at list ids the restored database no longer has.
2. **The hook must run in process.** A change made on any device, `/trading`'s panel included,
   reaches the phone through an `after_request` on OpenAlgo's watchlist routes.
3. **The master is already in memory here**, so search and follow rows read it with no copy and no
   Upstox call.
4. **A service would cost more than it buys:** a port, a unit, a health block, its own store and
   backups, a bridge, and still a sealed route in OpenAlgo for every list read and write, plus the
   hook.

**The limits.** Each is a condition of the exception, and a PR that breaks one is not merged:

1. No thread, timer, scheduler, executor or event loop in `sudarshan/watchlists/` or the bridge;
   every call is a request or an event.
2. One generation of master-derived maps, tied to OpenAlgo's current snapshot and dropped with it,
   holding references and never copies, under 15 MB on 120,000 rows.
3. NullPool engines from `database.engine_factory`, every operation in its own session, and the
   `fd-audit` skill on the store, the bridge, the push and the Smartlist read.
4. Correct under eventlet, gthread and the dev server, with barrier tests in the gthread suite for
   the bridge and the push.
5. The switch `SUDARSHAN_WATCHLISTS_V2` ships off; off, the watchlist page is v0.6.0's.
6. One transaction per file: a write never holds a transaction across this store and `openalgo.db`.

Limits 1 and 2 are held by `tests/test_exception.py` (W11 below).

**Reversible.** The pure modules import no Flask and no OpenAlgo, the store sits behind plain-data
functions, and `master.py` is the only reader of OpenAlgo's master. Moving the store into a service
of its own later is about three PRs (a unit, its store with the rows copied over, and a bridge
forwarding the same `contract.py` ops); the hook and the master reads stay here, and the phone's
client does not change.

## W3 - the contract (`contract.py`, `gentypes.py`)

`contract.py` holds every op of the owner's bridge once: method, path, class, request fields and the
record each answers with, plus the enums, the limits (50 lists, 250 symbols, 20 sections, 120-row
window ...), the error codes and the write routes. `gentypes.py` writes
`frontend/src/sudarshan/watchlist/v2/types.ts` from it; run
`python -m sudarshan.watchlists.gentypes` after a change, and `--check` (also a test) fails when the
file differs. Never edit `types.ts` by hand. A later contract change is its own PR and merges before
the phone PR that uses it.

## W14 - the follow-row rules (`dynamic.py`, `words/dynamic.py`, `numfmt.py`)

Pure rules for ATM, strike and current-month future rows. Expiries come from the master as listed;
a row rolls at the session close of expiry day, taken from OpenAlgo's market calendar and passed in
as `closes` (a special session day's own end only; any other day stops by `expiry.stop_ms`, see
W15's regime paragraph). The ATM is the strike nearest the price, a tie
going to the lower strike (a test holds it equal to OpenAlgo's own function); a held strike is kept
while the price stays inside its band (midpoint to the neighbour plus 10% of the gap, open ends).
The price is the page's own tick while its socket is up, however old, then the page snapshot, then
the feed's; none means "waiting". Vectors: `tests/vectors/dynamic.json`. `numfmt.py` holds the
Indian number and IST time formats the words use.

## W15 - follow rows (`store/rows.py`, `ops/rows.py`, `resolver.py`, `sessions.py`, `feedsnap.py`, `words/rows.py`)

`row_add`, `row_edit` and `row_remove` keep a list's follow rows in `wl_row` under the list's version,
as sections do: one transaction whose first statement claims the version, then the change; the bridge
bumps once and pushes the list (why `rows`). A write that would change nothing (adding a row the list
already follows, an edit to the same values) rolls its claim back, answers the version it was given
and bumps and pushes nothing. 20 rows a list (the 250-instrument cap, with a strike row counting 2, is
the phone's). NSE and BSE underlyings only: the request's `underlying_exchange` is a string so MCX and
the rest are refused in words (the contract changed once for this; `types.ts` regenerated). A row's
underlying must be in today's master with options (or futures, for a future row) on NFO or BFO.

`resolve` (a read) takes, for each row, the page's price, its time, its source and the strike the row
last showed (`held`), and `connected`. The expiry is the first whose session close is after now:
`sessions.closes` reads OpenAlgo's calendar for a dated special-session or holiday-open window and
otherwise `expiry.stop_ms` stops it by the regime of the day (below). The calendar's default F&O
close is deliberately not used. The price is the page's tick while
connected, its snapshot, the feed's last tick (one `/snapshot` read for every row that needs it, 0.5 s,
through the shared httpx client; any failure is an empty answer), then the page's last price when the
exchange is closed by the calendar, else the row waits. The answer carries the contracts looked up in the
master (never built), the band of the ATM, `valid_until`, and the words; a side the master lacks is
named and no contract is answered. For the first minute after a roll the words are "Rolled to ..." and the answer row's `rolled` is true
(false otherwise, for every state): the phone reads the flag, never the prefix of the words, which stay.
Nothing is written and no thread or timer is started. Tests: `tests/test_watchlists_rows.py`,
`tests/test_watchlists_follow.py`.

**Draft rows (M8.10, the add form's preview).** `resolve` also takes `drafts`, rows the page has not
stored: the fields of `row_add` without `list` and `version` (`section_id` is accepted and ignored),
and the page's `price`, `price_at`, `source` and `held` as for a stored row, all optional. Each is
checked as `row_add` checks a row (the rule, then today's master: `invalid` with the field, `failed`
with the master not loaded), resolved by the same code, and answered in the same `Resolved` shape,
in the order sent, under `drafts` with `id` 0 (a stored row's id is above 0). `drafts` is in the
answer only when the request had it, so the answer to a request without it is as before. A draft
reads nothing and writes nothing, so the add form can show "Now: NIFTY 25,900 CE, 24 Nov 2026
(NIFTY at 25,912.35)" before the row exists. Drafts count with the stored `rows` in the limit of
100 (`invalid`, field `drafts`, when the total is over it; field `rows` when the stored rows alone
are). Ids for the drafts inside the request sit above every id the page sent, so one is never taken
for the other.

**One rule for when an expiring contract stops (`expiry.py`).** `expiry.stop_ms(day, index=...,
exchange=..., window_end_ms=...)` answers the instant a contract stops on its expiry day, and
`dynamic.close_ms` (so the expiry pick, `valid_until` and the "Rolled to ... at HH:MM" words of
`resolve`, and the roll and `valid_until` of the Index futures list) asks nothing else. It reads the
regime from `sudarshan.alerts.market.sessions` (`expiry_close`, `INDEX_UNDERLYINGS`), the repository's
one table of sessions. Until 6 Dec 2026 an expiring index contract trades to the F&O close, 15:40, as a
stock contract does. From 7 Dec 2026 (SEBI circular of 15 Oct 2026) an index contract stops at 15:30 on
its expiry day while a stock contract trades to the F&O close, 15:45. A special session day takes its
end from OpenAlgo's calendar, passed as the date's entry of `closes` (`get_special_session`,
`get_holiday_exchange_window`), in either regime. A row is an index row when its underlying is in
`INDEX_UNDERLYINGS`. The older rules are gone: W12's calendar default close (15:40 on every day, which
the calendar's effective window gave) and W15's fixed 15:30. Tests: `tests/test_watchlists_expiry.py`.

## W16 - the search grammar (`search.py`, `words/search.py`)

Pure grammar over reference maps of the master (`Index`, built from rows in slices by
`IndexBuilder`; it holds the rows themselves, never copies, and is dropped with the master
generation it came from). It reads underlying aliases ("bank nifty", "vix"), expiry words ("this
week", "next week", "month", "next month", month names, "24 nov", "24nov", ISO dates), strikes (any
number beside CE, PE, C, P, CALL or PUT, any size; a bare number is a strike from 100 up, and "24
nov" is a date), ATM and ATM plus or minus n (with the price sent), FUT and an ISIN. Results come
in the design's order (exact symbol, cash or index row, futures nearest first, options by distance
from the ATM or by strike), at most 50; an underlying alone offers Pick a strike instead of its
options. A miss names up to three names one edit away (a swap counts as one). No Upstox call is made.
Vectors: `tests/vectors/search.json` (synthetic master, hand-typed expectations).

## W18 - the TradingView mapping (`tvimport.py`, `words/tvimport.py`)

Reads a TradingView watchlist file (commas or line ends, `###Name` for a section) against a
`known(exchange, symbol)` lookup of the master and says what it would make, writing nothing. NSE and
BSE symbols become cash rows or, through an alias table (CNXIT, CNXFINANCE, CNX500 ...), index rows;
`_` is tried as written, then as `-`, then as `&`; `NAME1!` and `NAME2!` become current-month and
next-month follow rows up to the 20 a list holds (the rest become today's contract, said not to
roll); other exchanges and unknown names are left out with their reason. Section names are cut to 40
letters, a repeat gets a number, an empty section is kept, duplicates are dropped. `split` cuts more
than 250 symbols into lists at section boundaries. The alias table is completed against one real
export (design fact W-5). Vectors: `tests/vectors/tvimport.json`.

## W19 - TradingView import (`ops/tvimport.py`, `words/tvimport.py`)

`tv_preview` (class `preview`, 6 a minute) takes `text` and, to say more, `name` (the new list's
name, from the file name) and `list` (the open list); both were added to the contract and
`types.ts` regenerated. A file over 64 KiB is refused in words before the master is read. It
answers W18's counts, left-out lines and conversions, then: for the open list "Swing · room for 203"
(and, when the file's new symbols exceed the room, how many it has), for a new list the split line
("... Swing (1) with 250 and Swing (2) with 62, split at a section."), OpenAlgo's 50 lists ("You have
50 lists, the most there can be. Delete one to import."), a name already taken and a list with more
than 20 sections. `hash` is `tvimport.text_hash(text)`; `lists` is the lists the import would make
or write into, each with `count` (its symbols plus its follow rows; the sum is `matched`). A follow
row, or a future past the 20 rows a list holds, for an underlying with no futures on its derivatives
exchange is left out with reason `no_futures`. Nothing is written and nothing is bumped.

`tv_import` (class `write`) takes the same `text` and the preview's `hash` (a mismatch is `invalid`,
field `hash`: "The file changed since its preview. Preview it again."), then parses again, so what is
written is what the preview said. It writes into new lists (`name`; a file over 250 makes `name (1)`,
`name (2)` split at a section) or into the open list (`list` and the `version` `view` answered,
refused as `conflict` when stale, before any write; `list` and `name` together are `invalid`).
Everything refusable is refused first with its numbers: the 50 lists, a name taken, 250 symbols, 20
sections. A new list goes through `create_watchlist` with its items in one call. Into an existing
list OpenAlgo's `add_item` runs once per symbol through `ops.copy.add_all`, yielding to the hub
(`time.sleep(0)`) after each commit, never inside an overlay transaction; `tests/` measure it under
a real eventlet hub as W8's do. Sections are made as W7 makes them (a section of the open list with
the same name is used again), follow rows as W15 makes them (`month_future`, rule `month` or
`next_month`), both in one overlay transaction that claims the version after OpenAlgo's writes (W8's
order and its reason); a future past the follow-row room is imported as the contract of today found
through the same resolver as a follow row. The answer is `TvDone`: `lists` (ids), `added` (symbols
plus follow rows written), `version` (the one list's new version; null for several), `words` ("Swing:
45 symbols in 2 sections.") and `lists_version`. The bump is `items` with every list changed, once. A
failure part way says how many went in and where ("Added 2 of 4 symbols to Swing. The rest were not
added; try again."; "Only Swing (1) was made, with 250 of the file's 312 symbols ...") and names
what it committed (`OpError.changed`), so the lists version is bumped once with those refs. An
import that finds everything already in the open list bumps nothing. No thread, timer or loop; the
parse is one pass over at most 64 KiB. Tests: `tests/test_watchlists_tvimport_ops.py`, with
`vectors/tvimport.json` through both ops.

## W20 - TradingView export (`tvexport.py`, `ops/tvexport.py`, `words/tvexport.py`)

`tv_export` (class `read`, 120 a minute: the bridge's table has a row for it, `tv_export`, so no class
was invented) takes `ref`, where the contract used to say `list`: `list:7`, `list:7/section:3` (one
section), `preset:<id>`, `flag:<n>` or `ranked:<source>:<asset>:<category>`, read as `view` reads them
(a ranked list asks Upstox as `view` does). The answer is `TvExport`: `text`, `filename` (the list's
name, or `List - Section`, without what a file name cannot hold, ending `.txt`), `symbols` and
`sections` written, `left_out` (`token`, `reason`: `no_tv_name`, `no_form`, `dated_future`, `expired`,
`waiting`, `no_contract`) and `words`. Nothing is written, bumped or pushed; no thread is started.

The mapping is W18's in reverse and pure (`tvexport.build`, with the rows read as `Block` and `Entry`
records): the text is one line of exchange-prefixed symbols separated by commas, `###Name` opening a
section, rows in no section first, a follow row before the rows of its section; the same symbol
twice is written once. A cash symbol is `NSE:INFY`, `-` and `&` written `_`; an index is its
TradingView name through the import's alias table in reverse (an index with none is left out); a
row that follows a future is `NSE:NIFTY1!` or `NSE:NIFTY2!`, and so is a dated future that is
today's current-month or next-month contract (found through the follow rows' own resolver); any
other dated future, and anything on another exchange, is left out with its reason. An at-the-money
or strike row (or an option contract in a list) is written as the contract it shows today,
`NSE:NIFTY261124C25800` (underlying, expiry YYMMDD, C or P, strike), found through `resolve`'s
resolver from the feed's last price; the answer says in words that it will not follow on
TradingView, and a row with no price is left out in words. The import does not read that form, so
those rows do not come back; every other row does (`tests/vectors/tvexport.json`, through
`tvimport.parse`, and each of W18's vectors in reverse). The option form is written from the
shape TradingView shows and is still to be checked against a real export (design fact W-5).

A comma inside a section name would split it in TradingView's import, so it is dropped from the
name, and the answer says "2 section names had a comma, which TradingView cannot read; it was left
out." A name that was only commas becomes `Section`. The master is read only when a row needs it
(a derivative contract, a rule other than the current or next month, an at-the-money row).
Tests: `tests/test_watchlists_tvexport.py`, `tests/test_watchlists_tvexport_ops.py`.

## W21 - ranked rows (`ranked.py`, `words/ranked.py`)

Pure mapping of an Upstox Smartlist answer to rows, through the master by instrument key, in
Upstox's order, with the ranking's own figure per category ("Traded Rs 1,320.9 cr", "Volume
2,45,600", "OI +12.4%", "IV 14.2%, +1.1", "Premium 38.40", "Costs Rs 4,350 to buy"), the time Upstox
made it (`time_stamp`, read as a number or an ISO text, IST when it has no offset) and a count of
contracts the master does not have. Smartlist ranks futures, options and MTF only: the catalogue
offers futures and options for Index, Stock and Commodity (commodities with the first four
categories), 44 rankings, never MTF. Words say "Ranked by Upstox" and the age (stale after ten
minutes), never "Live"; the sign-in, closed, unavailable and wait lines are here too. No read is
made here: the Smartlist client is W22's. The answer's exact envelope, the unit of traded
value and of premium, and the base of `previous` are checked on a real read (design facts W-1 and
W-10). Vectors: `tests/vectors/ranked.json`.

## W22 - ranked lists from Upstox (`smartlist.py`, `ops/ranked.py`, `refs/ranked.py`)

`smartlist.fetch` is the one read: `GET https://api.upstox.com/v2/market/smartlist/<futures|options>?
asset_type=&category=&page_number=1&page_size=50` with OpenAlgo's daily access token for the owner's
session as a bearer header (read with `auth_db.get_auth_token`, never the Analytics Token). It goes
through the shared httpx client (connect 1 s, read 8 s, no redirects, no retry) after the Upstox
plugin's standard limiter has given its turn; the limiter refusing under gthread is a `wait`. It
returns an outcome and raises nothing: `ok`, `token` (401 or 403), `wait` (429 or the plugin's
rate-limit code) or `failed` (a timeout, another status, an error body). The token goes in the
header only: it is in no log line, no answer and no message, and only a 16-character digest of it
is kept, to tell one sign-in from the next.

`ranked` (GET, class ranked) answers the `Ranked` record, always as an ok answer with a `state`
(the phone reads `state`, not an error code), in this order: no token today, or a token of another
broker, is `signin` with the design's words and **no call at all** (the broker is the web session's,
else the stored sign-in's); a fresh answer in the cache is served with no call; otherwise one call,
in a single flight per category (`utils/keyed_locks`; the requests that waited share what the first
read). Upstox refusing the token is `signin` ("Your Upstox sign-in has expired. Sign in to read it
again.", with the old ranking's age when this token read one, and no rows): the refusal is cached
for the minute so a pull does not ask again, nothing retries it, and the session and OpenAlgo's
stored token are never touched. Upstox asking us to wait is `wait`, also cached for the minute.
No usable answer is `unavailable`: the last ranking read with this token stays, with `made_at`
and the sentence "Showing the ranking from 10:41."; a failure is shared for 3 seconds only, so the
owner's next pull tries again. An answer is `ok`, `stale` after ten minutes and `closed` while the
exchange is shut by OpenAlgo's calendar (NFO, MCX for commodities): Upstox's own last ranking and
its time ("Market closed · ranking from 15:29 yesterday"). `rows` are 20 by default and 50 on
request (`size`, never more), `made_at` is Upstox's `time_stamp`, `read_at` the read, `left_out`
the rows the master lacks and `words` the status line followed by the left-out sentence. Each row
is the master's contract (looked up by instrument key through the snapshot, no map built), the
owner's flag and whether a note exists, and the ranking's own figure in `metric`. A combination the
page does not offer (MTF, a commodity premium) is `invalid` with no call.

The cache is `LockedTTLCache`: 60 s for answers, refusals and waits, 3 s for failures, and the last
good ranking of a token for 20 hours; all three are cleared the moment the token's digest changes,
and an answer in flight when it changes is not stored (the cache's generation) and carries the
digest it was read with, so a ranking of one token is never shown under another. The cache holds
Upstox's answer for the 50-row page, so 20 and 50 rows, and two devices, are one read. `state`
carries `ranked`: `{ok, message}` from OpenAlgo's own sign-in, with no Upstox call. `refs/ranked.py`
serves `ranked:<source>:<asset>:<category>` for `view` (locked, version "0", 20 rows, one line of
words), through the op's own path and cache, keeping nothing of its own. Its `members` (the 50
contracts, `valid_until` null, the unknown keys as left-out entries, no flag or note; `signin` with
the words, or `failed` when there is nothing to show, never an empty list) is not reached by the
sealed route: after the merge with W23 the route refuses a ranked ref in words first (a consumer
must not take a changing ranking for a list), and the reader keeps `members` for the interface.

`copy` takes a ranked source (M8.9, "Make a copy"): `from: {ref: "ranked:...", keys: [...]}`, the
keys being `EXCHANGE:SYMBOL` in the order the page shows the rows, as for a built-in list. The server
does not read Upstox again for it (the fixture of the test fails on a call): a key is checked
against today's master only, a key it lacks is left out and counted in `words` ("2 symbols are not in
today's contract list and were left out of the copy."), and nothing in the master is `invalid` with
field `from` ("None of these 3 symbols are in today's contract list, so nothing was copied."). No
`keys`, a `section_id`, more than 50 keys or a key that is not `EXCHANGE:SYMBOL` is `invalid`, and
`move` is `invalid` ("This is a ranking from Upstox. Make a copy of it to keep it."). The master not
loaded is `failed` in words. The 250 cut of a built-in list does not arise with 50 rows.
No thread, timer or loop. Tests: `tests/test_watchlists_ranked_bridge.py` (a fake client that
fails the test when called where it must not be) and `test/test_gthread_watchlists.py` (eight
threads, one category, one call).

To check on a live session (design section 8, reads only): W-1, that Smartlist answers for futures
and options with OpenAlgo's daily session (INDEX), with the parameter spellings and the bearer
header used here; how often `time_stamp` moves; which indices INDEX options covers; COMMODITY; the
unit of PREMIUM and DISCOUNT and of traded value; that `metric.previous` is the base of each
category's change; the answer's envelope (rows under `data.data`, `time_stamp` beside them); and
what Upstox answers once the day's token has lapsed (here 401 or 403 is read as a refusal of the
token). W-2, that every Smartlist `instrument_key` is in OpenAlgo's master (`left_out` should be
0 or small).

## W11 - master access (`master.py`) and the exception's tests (`tests/test_exception.py`)

`master.py` is the one reader of OpenAlgo's symbol master. `master.shared()` is the process's one
`Master` (made lazily with `utils/lazy`), and every reader uses it: `refs/list.py`, `refs/flag.py`
and the `symbol` and `expired_clear` ops (W9) now, and the built-in lists, follow rows, search and
the members route (W12, W13, W15, W17, W23) when they come;
no module builds a `Master` of its own (a test scans for one). It keeps one generation of reference
maps (`search.Index`: the snapshot's own rows, never copies) tied to the snapshot it came from,
builds it in slices that yield to the hub behind a keyed single flight (the first caller builds,
the others wait and share it, and the previous generation is let go before the next is built), and
drops it the moment OpenAlgo frees the snapshot it replaced: the generation holds its snapshot by a
weak reference whose callback empties the slot, so the old rows never outlive their snapshot
waiting for the next request. A snapshot that lapses is dropped at the next call. A request in
flight finishes with the maps it was handed and never builds or keeps a generation of its own.
Symbol, exchange and instrument-key lookups read OpenAlgo's snapshot and keep nothing.
`tests/test_exception.py` holds the limits that let this feature live inside OpenAlgo: no thread,
timer, executor, scheduler or event loop anywhere in `sudarshan/watchlists/` or the bridge (an AST
scan that follows every import and rename: threads of `threading` and of `utils.real_threading`
and its hub calls, `_thread`, Flask-SocketIO's `start_background_task`, eventlet and gevent spawns
and pools, executors, asyncio, schedulers, processes, signal timers, and the same names reached
through `importlib`, `__import__` or `getattr`; and a check that no thread starts for any op or
master swap); one generation for the process
(two requests in flight through the bridge share one build and `generations_alive()` is 1 while
both run; two greenlets under a real eventlet hub share one build that yields to the hub), the old
rows freed with it when OpenAlgo publishes the next snapshot; the derived maps under 15 MB on
120,000 rows (about 2 MB measured). Do not loosen these tests to land a feature.

## W1 - the store (`schema.py`, `engine.py`, `words/store.py`)

Schema 1 of `db/sudarshan_watchlists.db` (`SUDARSHAN_WATCHLISTS_DB` points elsewhere), frozen: list
guard rows, sections and their members, follow rows, flags and notes, favourite slots, view
preferences and `wl_meta`. Times are epoch ms, prices paise, every table has `principal`. The file
holds ranges and enums as checks; counts (20 sections, 20 follow rows a list) are the store's.
`schema.py` imports SQLAlchemy only, so the migration runs without OpenAlgo's packages. `engine.py`
builds one NullPool engine on first use from `database.engine_factory` (never at import, no thread),
prepares the file outside any lock (idempotent: creates what is missing, writes `schema = 1` only
when absent, never changes a value) and hands out sessions as `with open_session() as s:`. A file
whose `schema` is newer than the code, or not a number, is refused before a byte of it is touched:
`RecordTooNew` carries the owner's sentence (`words/store.py`), the cause goes to the error log, and
OpenAlgo's own lists keep working. Seeds nothing: default flag names and favourites are computed at
read. Tests: `tests/test_watchlists_store.py` (columns typed by hand from the design).

## W2 - the migration (`upgrade/migrate_sudarshan_watchlists.py`)

Creates `db/sudarshan_watchlists.db` and its tables from `schema.py` on an upgrade. Listed in
`migrate_all.py`'s `MIGRATIONS` (beside the risk layer's line), deliberately not in
`REQUIRED_MIGRATIONS`: an overlay of OpenAlgo's lists is not worth rolling a release back. With the
file missing or older the app's store makes or completes it on first use (`engine.ready()` creates
whatever is missing, as this script does), so a migration that did not run costs nothing; only a
file the store cannot open, or one from a newer release, is answered in the owner's words, while
OpenAlgo's lists keep working. A failure still shows in the summary. Safe to run again: tables, columns and indexes only when missing, the `schema`
record only when absent or lower, nothing seeded, no row or value ever written over. `--status`
opens the file read-only and prints the file, its schema, this release's schema and what is pending
(the file's bytes are unchanged; SQLite may leave its own `-wal` and `-shm` beside a file in WAL
mode). A file from a newer release is refused untouched, in the owner's sentence. Tests:
`tests/test_watchlists_migration.py` (missing, populated copy forced back, newer, the app store
opening the result).

## F1 - the v2 client (`frontend/src/sudarshan/watchlist/v2/`)

The phone's half of the contract, with no screen yet. `api.ts` calls any op by name through the
route `types.ts` gives it (`callOp`), unwraps the envelope and throws `WatchlistsError` with the
server's own sentence, or the page's words when there was no answer or the answer was not the
bridge's; a status code or an address never reaches a screen. `useListView.ts` holds the query keys
and the reads: `useV2State` is the only read made while the switch is off, and `useListView` and
`useLists` wait for it to say `enabled`. The two versions and what each is used for are
under "F fixes after the mid-point review" below. `usePush.ts` joins the owner's
room after each connect with an ack, collects pushes for 200 ms into one read (`applyPushes`),
and reads everything again after a reconnect;
nothing is read on a timer. `useOverlay(v2)` knows `?sheet=lists` only when v2 is on, so Back closes
a sheet first as before and a bookmarked address reads as nothing while it is off. Tests run against
`mockBridge.ts`, a stand-in that answers the contract's routes and reads a request as the bridge
does (see the fixes below). A write sends its path fields in the body as well as the path.


## F2 - seven favourite tabs and the All lists sheet

With v2 on, `Watchlist.tsx` hands `ListTabs` a `ListStrip` in place of its tab row (the options menu
stays): the favourites in slot order, up to seven, numbered by position, each at 48 dp, the open one
bold with a tick as well as tinted, a lock, a ranking glyph or a flag with words for a built-in,
ranked or flag list, and the All lists button outside the scrolling part so it is reachable at every
width. Until anything is pinned the strip shows the first seven own lists in OpenAlgo's order
(`tabsFor`, computed, never stored); an own list opened from the sheet that is not a favourite gets
a last tab so the open list always has one. `AllListsSheet` (`?sheet=lists` through
`useOverlay(true)`, so Back closes it first) reads the `lists` op only while open and has the blocks
Favourites (move up and down), Your lists, Fed by a screen, Built-in (locked), Ranked by Upstox and
Flags, each row 56 dp with its kind in words and a star (`aria-pressed`) that writes the slots with
the `favourites` version it read and reads state and lists again after, a refusal shown in the
server's words. At seven the star is off and the sheet says so. The 500 ms tap guard is
`BottomSheet`'s. While v2 is off nothing renders and only the state is read. Tests:
`v2/favourites.test.tsx` against `mockBridge.ts`, and one in `Watchlist.test.tsx` for the off state.
Not yet: opening the rows of a non-own favourite (placeholder), Import from TradingView (F15).

## W4 - the bridge (`blueprints/sudarshan_watchlists.py`, `ops/`, `refs/`, `bridge/`, `words/base.py`)

The owner's routes under `contract.API_PREFIX`, built from `contract.OPS`, one for each op that has a
handler: `ops/` is found with `pkgutil.iter_modules`, each module exposing `HANDLERS = {op: fn}` with
`fn(args) -> data` (refusals are `ops.OpError(code, words, field)`), so a later change adds a module
and edits nothing else; `refs/` and `bridge/` are found the same way (bridge parts define
`install(app)`, called from the blueprint's `record_once`). A test keeps the list of ops not built
yet and fails when an op is neither handled nor listed, a handler has no row, or an op has two. Every
request is checked in this order: an OpenAlgo session (`session`, in the envelope, never a redirect),
the owner (`owner_only` for any other session, reads included), CSRF on every POST, PUT, PATCH and
DELETE (the blueprint is let go by the app's own check so it runs after the owner check; a lapsed
token is `session`), then the shared limiter's per-class count (`busy`). The limit wraps only what
runs after those three checks, as the alerts and portfolio bridges place theirs, and it is counted
per owner (the session's user), not per address: a request with no session, another sign-in's
request and a change without its token are refused before they count, so nobody but the owner can
spend his budget (behind nginx every request arrives from one address, so an address key would
have let anyone who reached the paths keep his reads `busy`). Then the switch `SUDARSHAN_WATCHLISTS_V2`, read at
each request and shipped `off` in `openalgo-env.conf`: off, `state` answers `{"enabled": false}`,
every other op `off`, and nothing is opened or read. Then the record check (`engine.ready()`, its
outcome kept five seconds in a `LockedTTLCache`, a newer or unreadable file answers `record` in the
owner's words), then the request is read against the contract (query string for GET and DELETE, JSON
otherwise; a route's id must equal the same field of the body, else `invalid` with that field).
`state` answers the switch, `contract.LIMITS` and the five default flag names; versions and the push
come with W5, favourites W10, ranked and alerts availability W22. Tests: `tests/test_watchlists_bridge.py`
(in process, real CSRF, limiter and user table) and `test/test_gthread_watchlists.py` (a real gunicorn
gthread worker: concurrent reads, the switch off, the limit under a barrier, and requests without a
session released together spending none of the owner's limit).

## W5 - the version, the push and the hook (`versions.py`, `bridge/push.py`, `store/prune.py`)

The lists version is an in-memory counter read as `"<boot>:<n>"`, where `<boot>` is new at every
process start, so a reader only compares for equality. The favourites and flags versions are the
store's integers (`wl_meta.favourites_version`, `flags_version`; an absent row reads as 0), because
the writes of favourites and flags (W9, W10) name the version they were decided on and a stale one
is a `conflict`, across restarts. `versions.py` keeps an in-memory mirror of the two, loaded once
from the store on first use and moved forward by a writer right after its commit
(`advance_favourites` or `advance_flags` inside the write's transaction, one compare-and-set on the
version the write was decided on, `versions.Stale` when it has moved, the delete prune alone
advancing the favourites without one; then `committed(kind, n)`); a store that cannot be read leaves
them out of the push and of `state`, never raises, and is left alone for five seconds
(`STORE_RETRY_S`, a `LockedTTLCache`), so a run of pushes while it is broken opens it and logs the
cause once instead of once per write. A lists bump and the mirror are under a real lock
(`utils.real_threading`) whose critical section is the increment or one comparison.
`versions.bump(why, refs)` bumps the lists version once and calls `versions.push`, which emits
`contract.EVENT_PUSH` (`{v, ref, refs, why}` plus `favourites` and `flags` when known) to the room
`sudarshan:watchlists:owner` through `extensions.emit_from_any_thread`. `refs` is every ref whose
members the write changed, in the order named and each once: the target and then the source of a
move, the old and the new flag list of a mark (the design's bump table), so a per-ref sync (lane F's
`group_sync`, A5) knows every list to follow. It is empty when the change touched no list's members
(a favourites write, a flag rename, a note alone) or when the hook could not tell which list it was
(OpenAlgo's answer named none; the alerts' one-minute version check catches that). An open page
reads its open list again on every push, and `state` or `lists` when `why` names them, whatever
`refs` says. `ref` is the one ref changed, else `lists`: the phone reads `ref` today, so it stays in
the push until lane F moves to `refs`, and is then dropped as a contract change of its own. A push
that cannot be sent is logged, never raised. A write's answer (the `Done`, `CopyDone` and `TvDone`
records) carries `lists_version`, the lists version its bump produced (the push's `v`), so the phone
that wrote knows the push of its own write and skips it instead of reading again; a write that
bumped nothing (a favourites or preference write, a write that changed nothing) answers without it,
and a refusal answers the error alone (the page reads the list again after any refusal). `state`
answers `versions`. An app joins the room with `contract.EVENT_JOIN` (an ack): the owner's session
only, and refused in words with the switch off.

The one rule: every write that changes what a list, a section or a flag list holds bumps the lists
version once, after its transaction commits, from one of two sources and never both. (1) The hook:
`bridge/push.py` registers one `after_request`; for a 2xx answer to a non-GET request of OpenAlgo's
watchlist blueprint, or to the screener's internal watchlist route when its op is `watchlist_apply`
and it found the list, it bumps and pushes. An add of a symbol the list already holds changes
nothing, and OpenAlgo answers it as a success with the row it had: a look before the add route runs
(one read of the list's items, for that route only, through OpenAlgo's models) notes that row, and
an answer naming the same row bumps nothing. Every other request pays one comparison of
`request.endpoint` before and one membership test of `request.blueprint` after. A delete also
prunes that list's overlay (`store/prune.py`: list guard row, sections, section items, follow rows,
preferences, and favourite slots naming `list:<id>`, with the favourites version raised in the same
transaction) in one transaction in the overlay's own file; a clear drops section membership only,
because /trading cannot see sections or follow rows. The prune runs whatever the switch, and only
the bump and the push wait for v2 to be on: the guard's `oa_created_at` has one-second resolution,
so a list deleted while v2 is off and a new one given its id in the same second would otherwise
inherit the overlay. A store file that is missing (never created by a delete, so a server that never
turned v2 on never gets one) or newer is skipped and logged, and a failure in the hook is logged and
never changes OpenAlgo's answer. (2) The bridge's own ops: a handler marked `@ops.bumps(why, refs)`
has the bridge bump and push after it returns. After a refusal the bridge bumps only for what the
refusal names as already committed (`OpError(code, words, changed=refs)`): a write that works in
steps and commits part of its change before it answers `failed` or `conflict` (a copy that stopped
part way, an arrange whose OpenAlgo order failed) still bumps the lists version once and pushes the
refs it changed, so the tablet and the alerts' backstop learn of it; a refusal that committed
nothing bumps nothing. A write that changed nothing this time (a copy that found every symbol
already in the target) has its refs function return `ops.UNCHANGED`, and nothing is bumped or
pushed. The hook starts no thread, timer or loop. Tests: `tests/test_watchlists_push.py`;
`test/test_gthread_watchlists.py` (twelve bumps at once, twelve writes through the hook under a real
gthread worker).

## W6 - own lists and view (`view.py`, `refs/`, `ops/view.py`, `words/view.py`)

`view` (GET, class read) answers the `View` record of a `list:<id>` ref: OpenAlgo's list and the
overlay merged. `ops/view.py` picks the reader by the ref's kind; `refs/` is found with
`pkgutil.iter_modules` once, when the package is imported, and each reader is imported and kept on
its first use (`utils/lazy`), so a `view` request lists and imports nothing and importing the
bridge loads no reader. Each module names its `KIND` and gives `view(ident, user)` and
`members(ident, user)` (the sealed members route's reader, documented in `refs/__init__.py` and
implemented in W23; until then it raises `NotImplementedError` and nothing calls it). A kind that
is not built (preset W12, flag W9, ranked W22), a ref that is not `<kind>:<id>` and a list id that
is not a whole number answer `invalid` with field `ref`; a list that does not exist (or is another
user's) answers `gone`.

`refs/list.py` reads OpenAlgo's list and items through `database/watchlist_db.py` (read only, its
scoped session removed after the read) and the overlay in one read-only session of the store (one
session, not one transaction: pysqlite begins none for reads, so the rows after the guard row can
come from a later commit; the guard's version is read first, so a write in between can only make
the page's next write a `conflict`, never one decided on a wrong merge): the
`wl_list` guard row (version, `oa_created_at`), `wl_section`, `wl_section_item`, `wl_row` (follow
rows as the rules they are, never resolved: resolving is W15), each symbol's flag and whether it has
a note (a length test in the query, the note itself is never read) and `wl_pref`, or the defaults
(sort `list`, ascending, basis `close`, holdings off), never written. `view.py` is the merge and is
pure; its vectors are `tests/vectors/view.json`, typed by hand.

Order inside a list: the symbols with no section first, then each section by `position`; inside
each, the follow rows first (by their position) then the fixed symbols in OpenAlgo's item order,
which stays the only order of fixed symbols. `rows` carries the fixed symbols in that order and
`follow` the follow rows in that order (No section's, then each section's), so a page puts each
section's follow rows above its rows. A symbol OpenAlgo has with no section row (added on /trading)
is in No section at its OpenAlgo position, a section item for a symbol no longer in the list is
ignored, and a section id that is no longer a section reads as No section. A section's `count` is
its fixed symbols plus its follow rows. Follow rows are as stored, with the strike in rupees; a
month future has no strike rule, offset or strike (null).

The `oa_created_at` guard: SQLite can hand a deleted list's id to a new list. When the list's
`wl_list.oa_created_at` differs from the live list's `created_at` (both as `view.created_key`, the
text SQLite writes, which the write ops of W7 store when they make the guard row), the whole
overlay of that list is ignored: no sections, no follow rows, no preference, version "0". The read
writes nothing; the next write that touches the list prunes it (W7). A list with no guard row has
nothing to ignore and reads in OpenAlgo's order. A symbol's flag and note belong to the symbol and
apply in every list.

`version` is the list's stored `wl_list.version` as text ("0" until a write makes the row, then
the epoch milliseconds of that write, one more for each write after it).
`Row.key` (`Contract.key`) is `EXCHANGE:SYMBOL`. A row's ISIN is read through `master.py` from the
instrument key (cash rows carry it, null otherwise), and a symbol the master lacks is `expired`
and named in `missing` (with the master not loaded nothing is called expired). Prices are never in
`view`: the page streams them. Tests: `tests/test_watchlists_view.py`.

## F4 - marks

Five marks, used by sections now and by flags later, each a shape and a colour and always drawn with
its name: 1 `--mark-1` #B0246B circle, 2 `--mark-2` #5E7800 square, 3 `--mark-3` #7A3B1D triangle,
4 `--mark-4` #00796B diamond, 5 `--mark-5` #8E2F8E star; mark 0 is no mark. `watchlist/marks.tsx`
draws the glyph (`MarkGlyph`, an SVG hidden from a screen reader because the name is beside it) and
holds the names. The five mark tokens are pinned in `theme.test.ts` and are not in the rules file's
token table (`.claude/rules/sudarshan.md` is unchanged). That test computes, and does not hard-code,
that each reads at 3:1 or better as a graphic on white and on the page colour #F4F6F8, and that each
is at least 20 CIELAB (CIE76) units from every other colour in `theme.css` and from the other marks.
The upstream `--chart-*` series colours are left out of that comparison: they draw chart lines only
and are not in the D look's table (`--mark-4` is 9.6 from `--chart-4` and `--mark-5` 16.3 from
`--chart-3`). The colour-blind mode (`data-cvd`) changes none of them; a test fails if its block
names a mark.

## F3 - sections on the phone

With v2 on and an own list open, `Watchlist.tsx` reads the `view` op for it (`useListView`) and hands
`LiveList` a section layout (`v2/sections.ts`: `groupsOf`, `useCollapsed`, `writeSection`). The rows
are the OpenAlgo items as before, so prices stream exactly as they did, but `WatchlistRows` draws
them in the view's order: the symbols with no section first, then each section by position under its
header. An item the view does not list yet (the two reads crossed) is shown with the unsectioned;
follow rows are F11's and render nothing. Editing (Reorder or remove) stays the flat list until W8's
`arrange` has an F-side. With the switch off, a built-in or ranked favourite, or no `view` yet, the
page draws the flat rows it always did. `SectionHeader.tsx` is a 48 dp row in the card: a 4 px stripe
and the glyph of its mark (none for mark 0), "Banks · 8", a chevron whose accessible name is Collapse
or Expand, and a 48 dp menu button. Folded sections are kept per list in `localStorage`
(`sudarshan-watchlist-collapsed`), every read and write inside try/catch, so a refused store costs
only the memory.

The section menu, the name box (New section, Rename) and the remove confirmation are bottom sheets
(`SectionMenus.tsx`) opened through `useOverlay` as `?sheet=section`, `section-name` and
`section-remove` with `&section=<id>`, so Back closes them first and `BottomSheet`'s 500 ms guard
covers them. The menu has Rename, Mark (None and the five, each 48 dp with glyph and name, the current
one ticked and `aria-pressed`) and Remove section. The list menu gains New section for an own list
only (off with its words at 20 sections; a built-in list is offered none of it) and, with v2 on, its
Radix menu takes the open guard too. Every write sends the `version` the last `view` answered and the
list is read again whatever the answer; a refusal shows the server's sentence (a `conflict` says the
list is shown again), never a status code; a name over 40 letters and a 21st section are refused
before sending with "Section names can be up to 40 letters." and "A list can have 20 sections.", and
the second is also shown when the server answers `limit`. Moving or copying symbols and sections
between lists is F5's. Tests: `v2/sections.test.tsx` against `mockBridge.ts`.
Not covered: Back does not close the list menu itself (a Radix dropdown, not an address entry); the
sheets it opens do.

## F5 - arranging, copying and moving on the phone

With v2 on and an own list open, "Reorder or remove" edits a draft (`ArrangeSheet.tsx`, `useArrange`; the
pure parts in `v2/arrange.ts`). Symbols keep their 48 dp up and down, now inside their section; each
symbol gains "Move to section" (a sheet of No section and the list's sections, `?sheet=to-section&ci=<item>`)
and each section gains up and down for the whole section. "Save order" sends one `arrange` with the draft's
section order, every symbol's section and the item order (No section first, then each section), on the
version the last `view` answered; Remove stays an immediate write as before, and the draft drops that
symbol. A `conflict` shows its sentence, reads the list again and starts the draft over from it; the
`failed` sentence is shown as it comes and Save stays on for another try. The symbol sheet gains "Move to
section" (written at once, one `arrange`) and "Copy to another list"; a section's menu gains "Move to
another list" and "Copy to another list". All four open the list picker (`ListPicker.tsx`,
`?sheet=to-list&ci=<item>` or `&section=<id>`, `&move=1` for a move): the owner's other lists as "Swing
- 24 of 250", and New list (off at 50 lists, with the server's sentence). A list picked asks for an optional
section of it (read through `view`, whose version is then sent as `version`) and sends one `copy`, with `move`
only for a move. The 250-symbol room is checked before sending from the target's own rows when read (so
symbols it already holds do not count), in W8's sentence. After every write the affected lists and the
page's lists are read again whatever the answer. A built-in, ranked or flag list shows "A built-in list
cannot be changed. Make a copy to arrange it." where its rows would be. With the switch off the page is as it was
and the F5 sheets are unknown to the address. Follow rows are F11's and are not moved. Tests:
`v2/arrange.test.tsx`.

## F6 - the View sheet and sorting

With v2 on and an own list open, the list menu gains "View" (`?sheet=view`, `ViewSheet.tsx`): Sort by (List
order, % change, Price, Name, Exchange), High first or Low first (A to Z or Z to A for names) and Change from
(Previous close, Today's open). A pick is shown at once and saved per list with one `pref` (`sort`,
`descending`, `basis`; `held` is F7's and is never sent), so phone and tablet agree, and is read back from
`view`'s `pref`; a refusal puts the pick back and shows the server's sentence. When the choice is not the
default one line under the tabs says what applies: "Sorted by % change, high first, at 10:42" with
"Sort again" (48 dp), and "Change from today's open". A sort is a snapshot (`sort.ts` ranks, `useSortOrder.ts`
keeps the ranks): rows are ordered when the list opens (once its first prices have been read, for a price
sort), when the owner picks a sort or basis and when he taps Sort again, never on a tick; name and exchange
sorts show no Sort again. Rows sort inside each section, a row with no price goes last in both directions, a
tie keeps list order, a row added since stays at the end of its group until the next snapshot, and editing
shows the list's own order. Change from today's open is `ltp - open` from the snapshot's `open` field
(`rowView(..., 'open')`; zero or missing is not known, never the first trade the page saw): the row shows a
dash with "Open not known yet", the sheet says how many rows lack it, and a row without one is read once more
at its first tick after the opening (`useLiveRows.ts`; the snapshot already reads on the market-open event).
The list stays in LTP mode. The seam for a list longer than 250 rows is `needsFullSnapshot` in `sort.ts`
(F9 reads one snapshot of every row there before ranking); own lists stop at 250, so it stays shut for them. With the
switch off the page is as it was. Tests: `sort.test.ts`, `sort.view.test.tsx`.

## F7 - the holdings line

The View sheet's slot (`extra`) now holds "Show what I hold" (`HeldSwitch` in `HoldingLine.tsx`): a 48 dp
switch whose state is the word On or Off, saved per list with `pref` (`held`, sent with `sort`,
`descending` and `basis`) and read back from `view.pref`; a refusal puts it back with the server's sentence.
Off is the default, and with it off the page is as it was and nothing is read. With it on, a row's second line
adds "Holding 25" (the quantity held, with a briefcase) and "Position +65" or "Position −30" (the net,
signed, the word kept), in ink and not in the up or down colour; nothing shows for zero. Matching
(`HoldingLine.tsx`): a cash symbol (NSE, BSE) takes its holding by ISIN, so a BSE holding shows on the NSE
row (the view row carries the ISIN; a holding the broker gave without one, as the sandbox does, is matched by
exchange and symbol); a contract (any other exchange) never takes a holding and is never matched by ISIN. A
position is matched exactly by exchange and symbol and summed over its products. Data (`useHeld.ts`):
OpenAlgo's `/holdings` and `/positionbook` through `useBookSync` and nothing else: once when the switch turns
on, after an order event (holdings only after one that can change them), on a reconnect, on coming back to the
page and at the books' own 45 s interval while a market is open; no timer of its own and nothing stored; with
the switch off, or in a family render, nothing is read. `LiveList` takes `readOnly` (a family render): it never
reads the books, never draws the line and shows no switch, so the family view is a prop and not a fork. One
edit outside the page: Upstox's holdings mapping (`broker/upstox/mapping/order_data.py`) now passes the
`isin` it already receives, only when it is there, because the holdings payload dropped it. Tests:
`held.test.ts`, `held.view.test.tsx`.

## F8 - flags and notes on the phone

With v2 on, a row shows its flag's glyph (the F4 marks) before the name and a note icon after it when a note
exists; a screen reader hears "Flag: Breakout. Has a note." (the glyph is decorative, the words are in the
row). Markers come from the `view` rows (`flag`, `has_note`, `expired`; `v2/marks.ts`, `marksOf`) and the
names from `state.flag_names`; the note itself is never in a view. The symbol sheet (`SymbolMarks.tsx`) reads
`symbol` when it opens and adds Flag (`FlagPicker.tsx`: a radio group of the five flags and None, each 48 dp
with its glyph and name), Note (`NoteField.tsx`: a text field, "123 of 500", Save, "Saved"; a 501st character
is refused before anything is sent with the server's sentence, "A note can be up to 500 characters.") and In
lists ("Swing, Momentum, NIFTY 50 (built-in)", the `symbol` answer's `lists`, a built-in one gets the suffix).
Every write is one `mark` carrying the symbol's `version` as `symbol` answered it (null when the symbol has no
row); a refusal puts its own sentence on the sheet, a `conflict` included, and the symbol, the flag lists
and every open view are read again either way, so the next write names the version the last one made.
"Notes and flags are yours: the family never sees them." stands under the Note. In All lists the Flags block
lists each flag as "Breakout · 7" (the shape and the name), says "Flagged across your lists. Flag or unflag a
symbol from its sheet.", and holds Rename flags (five fields of up to 24 letters, one `flags` write on
`versions.flags`) and Clear expired (`expired_clear` on the same version; the answer's own words, "Cleared 2
expired contracts." or "There are no expired contracts to clear.", are shown). Picking a flag list opens it
(`pages/Watchlist.tsx`): its rows are `view`'s, newest flag first, read live like any list but with no Add,
no edit mode and no Remove. A flag or note on a contract no longer in the master shows "Expired" with a clock
on the row and in the sheet; nothing is deleted until Clear expired. A family render (`readOnly` on `LiveList`,
`SymbolSheet` and `AllListsSheet`) has no markers, no flag picker, no note field, no rename, no Clear expired and
no Flags block at all, and reads no `symbol`: the block's rows are the owner's alone, a step past the design,
which names only rename. With the switch off nothing here is drawn or read. Tests: `flags.view.test.tsx`,
`pages/Watchlist.flags.test.tsx`.

## F fixes after the mid-point review

Five findings of the mid-point review (H2, M2, M3, L11, L12) and its W5 bullet, all in the phone.

- **Where fields travel (H2).** `callOp` follows the contract and the bridge: a GET or DELETE
  sends its fields (the path's own excluded) in the query string, every other method sends the whole
  request as a JSON body. `mockBridge.ts` reads a request the same way and refuses what the bridge
  would not see (a body on a GET or DELETE, a query on any other method, a non-primitive query
  value), and hands its handlers query values as text, as the wire does. `api.test.ts` drives every
  op in `OPS` through it. Remove section no longer answers `invalid`.
- **Two versions (M3).** The lists version (`state.versions.lists`, `lists.version`, every push's `v`)
  is `"<boot>:<n>"` and is compared for equality only; it decides whether a push says anything new
  (`applyPushes` skips a push whose `v` is the last one seen, from a `state` or `lists` answer or a
  push; never a flags or favourites push, whose lists version does not move; never while a read is in
  flight, which may not have seen the change). A list's own version in `view` is a whole number as
  text ("0", then epoch milliseconds, one more per write): the page uses it as the version a write is
  decided on, and `keepNewer` orders two view answers by it as numbers ("0" is never taken for older,
  because a list made anew after its id was reused starts there). A write's own push is skipped since lane W sends `lists_version` in the answer (see
  "F - the push's refs and the write's lists version").
- **A push refreshes what the page shows (M2).** The rows and tabs come from OpenAlgo's lists
  (`WATCHLISTS_KEY`), so a push of why `items`, `lists` or `arranged` that names an own list (or
  `lists`) reads them again beside the views it names; a symbol added on /trading shows on the
  phone. A push names every list it changed: `refs` when present, else `ref`, and "lists" alone reads
  every open view. The generated `Push` carries `refs` now (the local `PushNames` is gone). A
  reconnect reads `WATCHLISTS_KEY` too. Sections, rows and preferences read the named views only.
- **A record refusal (L11).** When `state` answers `record` (the store is newer than this app, or
  cannot be read) the page shows the server's sentence above the lists, which keep working as
  before; the switch off, an unreachable bridge or a busy one say nothing.
- **Exact keys (L12).** `WATCHLISTS_KEY` is a prefix of every v2 key, so every invalidation and
  cancel of it is `exact` (`writeThenRead`, `useWatchlists`, the screener push): a write re-reads
  only what it changed. `V2_KEY` stays where it is.
- **W5 decision.** "`ref` is the one ref changed, else `lists`" is no longer all a push says: the
  phone reads `refs` and falls back to `ref` only for a bridge that does not send it.

## F - the push's refs and the write's lists version

Lane W is merged into lane F (no conflicts; the duplicate M8.4 to M8.8 rows of `STATUS.md` are one row each).
- **`refs`.** The local `PushNames` type is replaced by the generated `Push`. `refsOf(push)` returns
  `push.refs` when it is an array: every ref the write changed, each once, in order. An empty one is
  lane W's two cases (a change that touched no list's members, or one whose list the hook could not
  tell), so a push from another screen with an empty `refs` reads every open view as the "lists"
  fallback did, except a push whose `why` is `favourites` or `prefs`, which reads no list view (flags
  and favourites still read their own state and counts by `why`). Only where `refs` is absent (an older
  server) it reads the single `ref`; `isPush` accepts both shapes. A move's push names the target and
  the source and each is read, and no other.
- **`lists_version`.** Every write goes through `callOp`, which counts a write in flight and, when its
  answer (`Done`, `CopyDone`, `TvDone`) carries `lists_version`, notes it (`v2/ownWrites.ts`). A push
  with that `v` is the push of this page's own write: `applyPushes` skips it and reads nothing more
  (the write already re-read what it changed), whichever of the two arrives first. A push is held
  while a write of this page is in flight (at most 3 s) so the answer can be noted first, then read
  if it is not ours. A push from another screen has another `v` and is read as before; an answer
  without `lists_version` notes nothing. A flags or favourites push is never skipped (their own
  versions). Noted versions are dropped when matched or after 30 s.
- **Tests:** `useListView.test.tsx` (the refs and the fallback), `ownWrites.test.tsx` (own push after
  and before the answer, another screen's push, no `lists_version`, the bound, two refs, empty refs).

## F9 - the cap, the window and last ticks

With v2 on (`LiveList` passes `flagNames`), the page follows at most 250 instruments on the pooled socket, and
`follow.ts` (pure) counts them in this order, stopping at 250: the open sheet's instrument; the underlying of
each follow row on screen, once each (an input of `useLiveRows`, filled in F11); then the rows in display order,
a strike row counting 2 (an input, filled in F12). A row that does not fit whole is not followed, and nothing
after it is. A row not followed shows its last figure, grey, with a clock and "Price at 10:42 · not real-time:
this list follows 250 prices at most" (and "outside the 250 followed" to a screen reader); with no read yet it
says "No price read yet". It is never re-read on a timer: the quiet re-sync of `useLiveRows` takes followed rows
only, and the snapshot's own events (open, reconnect, market opening or closing, back after 15 s) still read
the whole list. A list longer than 250 (a built-in list, from F10; tests use a synthetic one) follows a window
of 120 rows (`useFollowWindow.ts`): centred on the first visible row, recomputed 300 ms after the scrolling
stops (every row of such a list carries `data-row-index`, read from the page), and moved only when the visible
rows leave the window's middle half, so a move swaps about 30 keys. Rows outside it say "not real-time: this
list follows 120 rows at a time" and "outside the 120 followed". The window is over the rows as they are
shown, so a sort moves it. A row that leaves the followed set keeps its last WebSocket tick (`lastTicks` in
`useLiveRows`, one entry per row of the open list, dropped with the list) and shows it with the time of it; the
newer of that tick and the latest read wins, so it is never the read made when the list opened if a tick came
after. One REST snapshot covers the whole list, `QUOTE_KEYS_PER_CALL` (500) keys a call, the calls made at
once so every row is read at one instant. A price sort on a list longer than 250 calls `readAll` first and
ranks every row on that read alone (`entryFromSnapshot`, never a tick): the line says "Sorted by % change, high
first, at 14:00, from prices read at 14:00". A failed read takes no snapshot and the list keeps its order until
Sort again. With the switch off nothing here applies: every row is followed, as it was. Tests: `follow.test.ts`
(hand-typed vectors), `useLiveRows.follow.test.tsx`, `follow.view.test.tsx`.

## W7 - sections (`store/sections.py`, `ops/sections.py`, `words/sections.py`)

`section_add`, `section_edit` and `section_remove` act on own lists only (`list:<id>`): a list or a
section that does not exist (or is another user's) is `gone`. Every write names the version it was
decided on (`view` answered it) and is one transaction in `db/sudarshan_watchlists.db`
(`openalgo.db` is never written). The transaction's first statement is the claim
(`store.claim`): one `UPDATE wl_list ... WHERE version = <held> AND oa_created_at = <live>` checked
by its row count, so a stale version is `conflict` ("Changed on another screen. The list is shown
again.") and nothing is changed, the refusals after it (`invalid` for a name over 40 letters, empty
or already used in the list, letter case aside; `invalid` for a mark outside 0 to 5; `limit` for a
21st section) roll the claim back with it. It is an UPDATE first and not a read first on purpose:
an UPDATE takes SQLite's write lock at once, while a read followed by a write fails under WAL as a
lock error no retry clears when another writer committed in between (that was a 500 in the first
write race). A list with no guard row is held as "0": the first write inserts the row (the primary
key refuses a second one, told `conflict`) with `oa_created_at` as `view.created_key` gives it and
its version at the epoch milliseconds of that write, never at 1. The delete hook removes a deleted
list's guard row and SQLite hands its id to the next list made, so a row counted from 1 again would
let a screen still holding version 1 of the deleted list write to the new one (the claim reads
`oa_created_at` from the live list, which is the new one's); no screen of an old list holds the new
number. A guard row whose `oa_created_at` differs belongs to a deleted list whose id was reused and
whose row was not removed: a write holding "0" prunes that list's overlay (`prune.drop_overlay`:
sections, items, follow rows, preference; favourite slots are the delete hook's) in the same
transaction and starts the fresh row the same way; a write holding the old version is `conflict`
and prunes nothing. A version no list can hold (not a number) is `conflict`.

A new section takes the next position (1 for the first). Removing a section deletes its section
items and sets its follow rows to No section, so its symbols read under No section in `view`.
Renaming to the section's own name is allowed. After its commit each write bumps the lists version
once with why `sections` (`ops.bumps`); `section_edit` and `section_remove` name only the section,
so their answer is a `Done` dict that also carries the ref of its list for the bump (the record
sent is the contract's `Done`). The handlers import the list reader when used, so importing the
bridge still loads no master. Tests: `tests/test_watchlists_sections.py`; `test/test_gthread_watchlists.py`
(eight threads on one version: one wins, seven `conflict`; eight first writes to one list: one row,
never a 500).

## W8 - arrange, copy and move (`ops/arrange.py`, `ops/copy.py`, `store/items.py`, `words/arrange.py`)

`arrange` (PUT, own lists) decides the section order, each symbol's section and the item order on the
version `view` answered. One overlay transaction claims the version (W7's compare-and-set), checks
that `sections` is exactly the list's sections and every item's `section_id` is one of them (else
`invalid`, field `sections` or `items`, and the claim rolls back), then writes the section
positions and replaces the list's section rows. Symbols the request leaves out keep their section
and go after the named ones; symbols it names that the list no longer has are left out. Then
OpenAlgo's item order is written through `watchlist_db.reorder_items` in section-major order (No
section first, then each section in the new order) so /trading shows the same order flat. If that
second write fails the answer is `failed` ("Saved here, but the order on /trading is unchanged
until you arrange the list again."); the overlay is saved, OpenAlgo's order is as it was, the
lists version is bumped and the list pushed all the same (the overlay changed), and the page shows
the list again.

`copy` takes `from` (`ref` of an own list, plus `keys` of `EXCHANGE:SYMBOL` or one `section_id`,
never both) and `to` (an existing `list`, or a new `name`; `section_id` only for an existing list),
and `move: true` for a move (there is no op of its own). A built-in list and a ranked list can be
the source of a copy only (a move is `invalid`; W12, and M8.9 below). A ref that is not one of the three, a
symbol the source does not hold and the same list as source and target are `invalid`
with the field named. Everything that can be refused is refused first, in words with the numbers:
a stale `version` when a section is to be set (`conflict`), 250 symbols a list holds ("Swing has
room for 3 more symbols; this section has 8. Make room in Swing or pick another list."), 50 lists
("You have 50 lists, the most there can be. Delete one to make a new list."), a name taken (for a
new list a count and one name are asked of OpenAlgo's table, never every list with its items, which
took a fifth of a second on the hub with 49 full lists). A symbol
already in the target is skipped, never an error (`skipped` in the answer); a move still takes it
out of the source. Order of writes, one file at a time: OpenAlgo's `add_item` for each symbol (one
commit each) or `create_watchlist` with all items in one call; then, only when a section is set, one
overlay transaction that claims the target's version and writes the section rows of the symbols
that went in; then for a move OpenAlgo's `remove_item` for each, and one overlay transaction
dropping the source's section rows (its failure is logged, because rows for symbols the list no
longer has are ignored). A move is a removal from the screener's point of view, which is intended.

The section rows are written after OpenAlgo's adds, not before as the design orders the two writes
(overlay first). Decided in the review round of 13 Nov 2026, for this reason: before the adds, the
rows would be for symbols the list does not hold yet. Every reader ignores such a row, and an
arrange from another screen in that window rewrites the list's section rows from the symbols it can
see, deleting them; the copy would then answer done while its symbols landed in No section. After
the adds, the section step is a claim on the version like every other overlay write, so the only
thing it can meet is a version another screen moved, and then the answer says so. A stale version
is still refused before anything is written (a read of the version, before the adds), so a refusal
that changed nothing is said as before. Every outcome leaves a valid state and its words are true
whatever ran in between: all added and placed (done); all added but the section step met another
screen's change (`conflict`: "Added 2 symbols to Long, in No section, because the list was changed
on another screen meanwhile. The list is shown again.") or could not be written (`failed`, the
same with "because the section could not be set"); the adds stopped part way (`failed`: how many of
how many went in, where they are, that the rest were not added and, for a move, that nothing was
taken out of the source; the ones that went in are placed in the section when the claim holds);
nothing went in ("Nothing was added to Long. Try again."); a move's removal stopped part way (what
stayed in the source). A move whose section step missed still takes the symbols out of the source,
because they are in the target, and says "Moved". Whatever a refusal had committed is named
(`OpError.changed`), so the lists version is bumped once and the changed lists are pushed (W5).

Bumps (`ops.bumps`), once per write and never also by the hook, because these ops call OpenAlgo's
functions in process: `arrange` with why `arranged` and `list:<id>`; `copy` and `move` with why
`items` and the target's ref (a move adds the source's: `refs` names both, target first, and `ref`
says `lists`).

The yield: writes of up to 250 items yield to the hub (`time.sleep(0)`) between OpenAlgo's per-item
commits, because SQLite's file I/O does not yield under eventlet. It is never done inside an overlay
transaction: that holds the store's write lock, and another greenlet writing to the store would
wait inside C for a holder that cannot be resumed to commit. The overlay rows go in as one batch.
Tests: `tests/test_watchlists_arrange.py`, which includes the yield under a real eventlet hub in a
subprocess (a second greenlet runs 240+ times during a 250-item copy, and not at all with the yield
removed).

## W9 - notes, flags and flag lists (`store/symbols.py`, `ops/marks.py`, `refs/flag.py`, `words/marks.py`)

`wl_symbol` holds one flag (1 to 5) and one note (up to 500 characters, trimmed) per symbol across
every list; a row with neither is deleted. `mark` (PUT, `EXCHANGE:SYMBOL`) names `wl_symbol.version`, an
integer counter, as its version (absent or null: the symbol has no row), `flags` (rename) and
`expired_clear` name `wl_meta.flags_version`; a stale one is `conflict` ("Changed on another screen. The list is shown
again.") and changes nothing. Each is a compare-and-set in SQL and the transaction's first statement,
a guarded UPDATE that changes nothing but takes SQLite's write lock (W7's reason), so the old row
read after it cannot have moved; nothing reads a version and writes it back. A field a `mark` leaves
out stays as it is and `null` clears it; the answer's `version` is the new counter as text (null
when the row went). The counter is not a time: two writes in one millisecond or a clock stepped
back cannot give equal versions. Schema 1 had not shipped in a release, so `wl_symbol.version`
(integer, default 1) was added to `schema.py` and so to the migration's create path, with no new
step; `updated_at` stays as the time of the write. `flag_at` moves only
when the flag changes, so re-sending a flag keeps its place in the flag list.

`flags` renames the five flags (1 to 24 letters, each named once). Absent rows read as Watch,
Breakout, Results, Long term and Avoid and are never written as if chosen: a name that equals its
default deletes the row. It moves `flags_version` with `versions.advance_flags(s, held)`, itself the
compare-and-set (the UPDATE matches only the held version, a row count of 0 raises `Stale`, told
`conflict`; absent reads as 0 and a first write inserts the row), as the transaction's first
statement, then `versions.committed`. `state` now reads the names from the store (the defaults
when it cannot).

`flag:<n>` (`refs/flag.py`, found by discovery, so `view` answers it) is every `wl_symbol` row with
that flag, newest flag first, `locked`, version "0", no sections or follow rows, one line of words,
the flag's name as its name. It reads `wl_symbol` alone, so a symbol keeps its place after it has
left every list; `view.merge_flag` is the pure merge. Only `has_note` is ever sent, never a note:
notes and flags are the owner's alone, and the sealed members route (W23) must not return one
either (`members` is still W23's).

Expired is decided at read against the master (a loaded master that lacks the contract; with it not
loaded nothing is called expired), in a list's rows, a flag list's rows and `symbol`. The contract
has an op for the clear, `expired_clear` (POST, `version` the flags version): it deletes the row of
every contract the master lacks, and only those, then moves the flags version once. With the master
not loaded it answers `failed` and clears nothing; with nothing expired it says so and moves
nothing.

`symbol` answers the flag, its name, the note and when it was written, `expired`, the version
`mark` must name, and `lists`: the owner's own lists holding it (`refs/list.holding`, read only). The
built-in lists (W12) join `lists` as more `Held` records after the own ones.

Bumps (`ops.bumps`): `mark` bumps once with why `flags`, naming `flag:<old>` and `flag:<new>` only
when the flag changed (a note alone changes no membership, so the push says `lists`); `flags` bumps
once with why `flags` and no refs; `expired_clear` once, naming the flag lists it removed rows from.
The refs reach the push's `refs` (the old flag list first), so a per-ref sync follows each one. A
refused write bumps nothing, and so does a write that changes nothing (`ops.UNCHANGED`, review L3):
a `mark` that sets the flag and the note the symbol already has writes nothing and keeps the
symbol's version, and a clear that finds nothing expired rolls back. Each of these ops is one
transaction in one file, so none can commit part of its change (the partial-write rule of W5 never
applies). Tests: `tests/test_watchlists_marks.py`;
`test/test_gthread_watchlists.py` (eight threads marking one symbol on one version: one wins, seven
`conflict`, including the first write to a symbol; eight renames on one flags version).

## W10 - favourites, preferences and lists (`store/favourites.py`, `store/prefs.py`, `ops/favourites.py`, `ops/prefs.py`, `ops/lists.py`, `words/favourites.py`)

`favourites` (PUT) replaces the seven slots as a whole: at most seven entries, each a `slot` and a
`ref` (`list:<id>`, `preset:<id>`, `ranked:<source>:<asset>:<category>`, `flag:<n>`), on the
favourites version `state` or `lists` answered. A list, preset or flag that does not exist is `gone`,
a ref that cannot name anything (a ranking that is not offered, flag 6, text) is `invalid` with
field `slots`, an eighth slot is `limit` ("Seven favourites at most. Remove one first."), and a ref
or a slot twice is `invalid`. The transaction's first statement is the version's compare-and-set
(`store.favourites.claim`: an UPDATE guarded on the held number, then `versions.advance_favourites`),
so a stale version is `conflict` and changes nothing; after the commit the mirror moves
(`versions.committed`) and one push goes out. It changes no list's membership, so the lists version
does not move: `versions.push(why)` is the push without the bump (`versions.bump` now calls it), and
carries the new favourites version and no `refs` (no list's members changed). Each W10 write is one
transaction in one file, so the partial-write rule of W5 has nothing to name in it; a favourites
write always moves the favourites version (its claim), so it is never a write that changed nothing. `pref` (PUT `/prefs/<ref>`) writes only the fields sent, on the
server, in one UPDATE (a ref with no row gets one, the rest at the table's defaults); it moves no
version and pushes nothing, and a ref with no row reads as the defaults and is never written as if
chosen. `refs/list.py` now reads a list's preference even when the overlay has no guard row for it
yet (before, a preference written before any section would not have shown in `view`).

The suggestion: with no slot stored and the favourites version never moved (0), `state` and `lists`
offer the first seven own lists in OpenAlgo's order, computed at read and never stored. Once the
owner has written (even an empty set) the version is above 0 and nothing is suggested. A slot that
names a deleted list, or a preset or flag that does not exist, is dropped at read and its row is
left for the delete hook or the next write.

`lists` answers the All lists sheet: own lists in OpenAlgo's order with `count` (symbols plus the
follow rows of a list whose overlay guard matches) and `room`; `presets` (the built-in lists
of W12, with their count and date); `ranked` (the 44 entries of
`ranked.catalogue()`, locked; whether Upstox is signed in today is W22's, in `state`); the five
`flags` with the count of symbols carrying each, read from `wl_symbol` directly so this change does
not wait for W9, and a renamed flag's name from `wl_flag`; and the favourites resolved. Lists a
screen feeds are not marked here: the screener is a service of its own, its state is not readable
in this process, and the page keeps marking them from the screener's state as it does today.
Tests: `tests/test_watchlists_favourites.py`; `test/test_gthread_watchlists.py` (eight threads on
one favourites version: one wins, seven `conflict`, one push for each win).

Two decisions from the mid-point review. (1) The favourites advance is a compare-and-set:
`versions.advance_favourites(s, held)` guards its UPDATE on the held number, checks the row count
and raises `versions.Stale` (the op answers `conflict`); only the delete prune, which cannot be
refused, calls it without one. W9's `advance_flags(s, held)` is the same compare-and-set: the two
branches each made one, and the merge left one exception (`versions.Stale`) and one implementation
(`versions._advance`, with W9's guard that a concurrent first insert is told `Stale`). (2) A `list:`
favourite keeps the list's `created_at` in `wl_favourite.oa_created_at`, written as
`view.created_key` gives it, and a slot whose value differs from the live list's is dropped at read,
so a favourite never follows another list that was handed a deleted list's id. The column is part
of schema 1 itself (added in `schema.py`, so the W2 migration's create path makes it) because schema
1 has not shipped in any release; there is no upgrade step for it.

## F17 - Alert on this list or section

The list menu (the options button beside the tabs) offers "Alert on this list" for the list on show,
and each section's menu "Alert on this section". Each opens the Alerts page's list form through
`listAlertAddress({ ref, name, count, fed })` (`alerts/useAlertOverlay.ts`) and builds nothing else
by hand; `watchlist/v2/listAlert.ts` decides the offer (`offerFor`) and `useAlertOffers` gives the
page its offers.
- **What it opens with.** The ref of the list on show (`list:<id>`, `preset:...`, `flag:<n>`), or a
  section's `list:<id>/section:<id>` (the form the alert service reads); the name; the row count (the
  list's rows, or the section's own count). A built-in list whose tab has no count yet passes 0, and
  the form's heading, which is only a hint, says so; the service's answer is the truth.
- **Off, with the reason, never hidden.** One sentence, the first that applies: the watchlist features
  off ("Alerts on a list need the new watchlist features, which are switched off on the server."); a
  ranked list ("A ranked list changes with every read. Set the alert on a list or a built-in list
  instead."); market alerts off (the Alerts state's `enabled` is false: "Market alerts are switched
  off on the server, so a list cannot be watched."). An unread Alerts state offers the item and the
  Alerts page says what it finds. With the v2 switch off the menus are as before apart from this
  disabled item and its sentence.
- **`fed`.** The phone already knows which lists a screener screen fills (`useFedLists`, from the
  screener state it reads already), so `fed=1` is added for an own list (and its sections) a screen
  feeds. No server call was added; a built-in or flag list is never fed.
- **A ranked list** (`ranked:` refs) arrives in a later change; the refusal is in place for it.
- **Tests:** `v2/listAlert.test.tsx` (each address, the ranked refusal, the three off states, the
  500 ms guard on the section sheet), `pages/MarketAlerts.listAlert.test.tsx` (the address opens
  lane A's form, headed "Every symbol of Swing (24)").


## F10 - built-in lists on the phone

A built-in list (`preset:<id>`, from W12) opens like any list: from the All lists sheet (the group
"Built-in (locked)", after the own lists, each with its count, or none while the master is not loaded,
and its note such as "As of 9 Nov 2026") or from a favourite tab. Nothing assumes the three lists of
W12: the page reads whatever `lists.presets` names, so W13's typed lists arrive with no phone change.

- **Rows and words.** The page reads the list from `view` alone, as it does a flag list (`itemsOf`), so
  there is no OpenAlgo item behind a row. The tab carries the lock glyph and a label for screen
  readers ("built-in, locked"); a list opened from the sheet that is not a favourite gets a tab once
  its view has said its name. Over the rows (`PresetLine.tsx`) the server's `lines` are shown as sent,
  in order, each once. F9 has no list-level line of its own: it marks each row outside the followed
  window ("not real-time: this list follows 120 rows at a time"), which stays, since a row that is not
  moving must say so. So over 250 rows the server's window line is shown once and the rows carry their
  own marker.
- **What is absent, not disabled.** The header Add, edit mode and move, remove in the symbol sheet,
  sections and "New section", and in the list menu Rename list, Reorder or remove and Delete list
  (`ListTabs` `locked`). What works: the View sheet (sort and change basis, saved under the built-in
  list's ref by `pref`), flags and notes, the holdings line, the cap and window of F9, and "Alert on
  this list".
- **Make a copy** (48 dp, under the lines) opens the existing name dialog (`NameDialog`, mode `copy`)
  prefilled "<name> (my copy)" (cut to fit 64 letters), behind the 500 ms open guard (`?sheet=copy`,
  v2 only). It sends one `copy` with `keys`: every row in the order shown at the tap, after a sort
  too. The server cuts at 250 and says so in `CopyDone.words`, shown as a toast as sent (the page's own
  "Copied N symbols to NAME." only when the server sent none). When it is done the lists are read
  again and the new list is opened. A refusal (the 50-list cap) is shown in the dialog, in the
  server's sentence, and the dialog stays open.
- **A list that cannot be shown.** The master not loaded (`failed`) and an unknown id (`gone`) show
  the server's sentence in place of rows (with Try again for the first only), never an empty list that
  looks real, and no Make a copy.
- **Alert on this list without a count.** A built-in list whose count is not known passed 0 and the
  form read "(0)". `listAlertAddress` (lane A's `alerts/useAlertOverlay.ts`, a small edit marked
  "lane F (F10)") takes `count` as optional and leaves it out of the address when not known; the page
  passes the count the open view reports (`total`), else the tab's, else none.
- **With the switch off** the page is as before: the new code is reached only through `v2`.
- **Tests:** `pages/Watchlist.preset.test.tsx` (the All lists group with a known and an unknown count,
  the lock and the lines once each, the window line once over 300 rows, no edit control, sort, flags
  and notes, Make a copy in the order shown after a sort and the server's words, the refusal, the
  500 ms guard, the `failed` and `gone` sentences, the alert address with and without a count, the
  switch off), `alerts/useAlertOverlay.test.tsx` (the address with no count).

## F - the list menu acts on the list on screen

Every item of the list menu acts on the list on screen or is absent, decided once in
`watchlist/v2/menuRules.ts` (`menuFor`, keyed on the ref kind of the open list; a kind not written
there offers nothing that edits). An own list keeps its menu, a built-in list keeps F10's, a flag list
offers "Rename this flag" (the `flags` op, the five names with the flag's own as typed and the others as
the state sent them, on the flags version read, the server's sentence shown on a refusal) and "Alert on
this list" but no Reorder or remove and no Delete list, and a ranked list offers nothing that edits it.
Delete list's confirmation names the list and takes its id from the list on screen when it opened; if
another list comes on screen while it is open it closes and sends nothing. The same rule is held for what
else used to reach the own list behind another ref: the Add sheet and the header and empty-state Add,
sections, New section, move, copy to a list and the View of an own list (all now tied to "no other list
open"), and a ranked or unknown favourite with no tab no longer shows the own list's rows. A sheet opened
by address that acts on the own list while another list is open is closed. Tests:
`pages/Watchlist.menu.test.tsx`.

## F11 - ATM and current-month FUT rows

The phone draws the rows W15 stores (`view.follow`) and keeps each on the contract the server says it
stands on now. `followRows.ts` is the pure part (words, form choices, request bodies and
`FollowResolver`), `useFollowRows.ts` its React wiring, `FollowRows.tsx` the row, `FollowRowSheet.tsx`
the form.
- **Where they are.** First in their section (or in No section), above the fixed symbols, as the view's
  order says; they do not take part in a sort and are not in edit mode's arranging (change or remove one
  from its sheet). A row's contract is an item of the list with a negative id (`-<row id>`), so it is
  subscribed, snapshotted and counted in the 250 like any row; the open sheet's contract that the row has
  moved off is held as one more item with its own id for as long as the sheet is open. Only the open own
  list shows them, never a family render, and only with the switch on.
- **A row.** Title "NIFTY 25,900 CE" (the resolved strike; "NIFTY FUT" for a future; "NIFTY ATM+2 CE"
  while it has no contract), a chip ("ATM", "ATM+2", "ATM-1", "Current-month FUT", "Fixed strike"), the
  detail "NFO · 24 Nov 2026 · follows NIFTY", the contract's price and change as any row. The ATM chip's
  help text is in the row's screen-reader text, in its tooltip and on the row's sheet. The one line under
  a row, in this order: the page's note while it lasts (60 s): "Moved from 25,850 CE at 10:12 (NIFTY
  25,931)", composed here from the strike last shown and the answer's price and time (the server sends no
  such words), or the server's "Rolled to ... at HH:MM" as sent (found by its first words, shown for 60 s
  from the answer); else the server's words for waiting, closed and missing, as sent; else, while the row
  has no contract, a sentence for a read that failed. A move is not noted across a roll, for a fixed strike
  or for a future.
- **When it asks again.** The page follows each underlying once (`follow.underlyings`, counted before any
  row) and the resolver compares the latest websocket tick with the band of the last answer (open at both
  ends, as the server's). A tick on or past an edge asks `resolve`; so do the first tick after "waiting",
  one timeout aimed at the earliest `valid_until` (sent with `held` null, as after a roll; a longer wait
  than a timer can hold is aimed again), a reconnect (the socket's epoch moving) and the page coming back.
  One call for every row that needs it, at most 100; a row has one call in flight (a reconnect or return
  that meets one is asked again when it lands); a band exit or the first price keeps to one ask in 2 s a
  row, and the tick that is turned away is simply the next tick's business. Nothing repeats on a clock:
  30 minutes with no band exit and no `valid_until` ask nothing (tested). `held` is the strike the row
  last showed; the price is the underlying's tick, or null with source null for the server to ask the feed.
- **The sheet** of a row stays on the contract it opened with. When the row moves it says "This row now
  follows 25,950 CE" with Show; Buy and Sell open the existing ticket for the contract shown (the ticket
  and its 500 ms open guard are unchanged, nothing was added to the order path). Edit and Remove are on the
  sheet; a row with no contract opens its form on tap. The sheet has no flags, notes, pin, move or copy:
  those belong to the contract, which the row does not own.
- **The form** (`FollowRowSheet`, `?sheet=follow`, carried by the history entry like the order ticket and
  never reopened by a reload): Expiry (Nearest, Next, This month's, Next month's; a future offers the two
  monthly ones), Strike (ATM, ATM+1 to +10, ATM-1 to -10), Side (CE, PE), a line saying what the row will
  follow, Add row or Save, and Remove this row after asking. Each write carries the list's version, is
  sent once however it is tapped, and the list is read again after it; the server's words are shown as
  sent. It is offered by the symbol sheet of an NSE or BSE stock or index with options in an own list
  ("Add ATM row", "Add current-month FUT row"), the row going into the symbol's section. A future of an
  underlying with options but no futures is refused by the server in words.
- **The seam for F13.** Open `{ kind: 'follow', form: { mode: 'add', kind, underlying, exchange,
  sectionId } }` through the page's `open` or `swap` (`FollowForm` in `followRows.ts`; `exchange` is
  `NSE_INDEX`, `NSE`, `BSE_INDEX` or `BSE`); nothing else is needed.
- **Decision: no preview from the server.** The design reads the preview line from "one `resolve` of a
  draft", but `resolve` takes stored row ids only, so before the row exists there is nothing to ask. The
  form's line says what the row will follow in its own words; the server's "Now: ..." line is the row's
  own once added. Tests: `followRows.test.ts` (the rules, the resolver), `follow.rows.test.tsx` (rows, ticks,
  the sheet, the form, the switch off).

## F12 - strike rows and the strike sheet

A strike row (`kind: strike_pair`, W15) is one row that follows the CE and the PE of one strike. The form of
F11 gains Side "Both, as one row" (an ATM row sends `strike_pair` with `strike_rule: atm` and the offset, no
side). `StrikeRow.tsx` draws it as three blocks: the CE's price and signed change on the left, the strike
and expiry in the middle ("25,900 · 24 Nov", with the chip), the PE's on the right, each price with its own
tick arrow; the line under it is a follow row's. The resolver keeps `Shown.pair` (both legs; `contract` is
the CE); a pair the master lacks a side of answers `missing` with both contracts null, and the row and the
sheet say the server's words and have no prices. The row is two items of the list (the CE at `-rowId`, the
PE at `-(PE_BASE + rowId)`), counts 2 in `follow.ts` through `FollowOptions.strikes` and follows both in
Quote mode through `FollowOptions.quote`: `useLiveRows` runs a second `useFrameLines` in Quote mode for the
keys in `quote`, and an instrument is in exactly one of the two sets (with the option off nothing changes).
- **The sheet** (`StrikeSheet.tsx`, pure parts in `strikeSheet.ts`): both legs' price and change, OI, IV,
  Delta, Theta, Gamma and Vega, "Straddle 340.55", Buy CE, Sell CE, Buy PE and Sell PE (the existing ticket
  through the page's `onOrder`, the contract's lot and tick from the server's answer, nothing added to the
  order path; the sheet's own 500 ms guard covers the first taps), Open option chain (`chainPath`), Edit and
  Remove. OI change is not shown. IV and Greeks are `lib/optionGreeks.ts`: the forward is the strike plus the CE
  price minus the PE price (the chain's parity), a leg's price its mid when it has a book else its last, time
  to expiry to 15:30 IST of the expiry date. "IV not worked out: no price on one side yet." stands in their
  place while a side has no price. The sheet stays on the contracts it opened with ("This row now follows
  25,950" with Show), those being followed too while it is open.
- **Other expiries.** The next four expiries after the row's, CE and PE of the same strike, tick while the
  sheet is open: they are items of the list while it is open, in `follow.sheet` (counted first in the cap)
  and Quote mode, and gone when it closes. Their contracts come from the server's `strikes` op since F13 (see
  "F13 - the search sheet"); F12 had read the page's own instrument search before that op was on this branch.
- **Two things differ from the design.** The option chain page has no interest-rate preference in this fork
  (it prices with the default 0, as the server does), so `CHAIN_RATE_PCT` is 0 and is the one place that will
  follow a preference. The other expiries' change comes from the one snapshot read when they join the list.
- **The seam for F14.** Open `{ kind: 'follow', form: { mode: 'add', kind: 'strike_pair', underlying,
  exchange, sectionId, fixed: { strike, expiry } } }` (`FollowForm` in `followRows.ts`; `expiry` is an
  `ExpiryRule`, nearest when absent): the form shows "Strike 25,900, kept for good" and sends `strike_rule:
  fixed` with the strike. Tests: `strike.rows.test.tsx`, `strike.sheet.test.tsx`, `strikeSheet.test.ts`.

## F13 - the search sheet

With the switch on, `?sheet=add` is `SearchSheet.tsx` (M8.18), not today's Add sheet; with it off the page renders
`AddSymbolSheet` exactly as before. The pure parts are `searchSheet.ts` (the chips, the group headings, the exchange of
an underlying) and `recents.ts`.
- **What it asks.** One `search` op per search the owner typed or chip he touched: `q`, `seg`, `limit` 50 and `list`
  (the open list's id, so the server marks `added`). Typing waits 250 ms for a pause and starts at two letters; a
  chip sends at once; a request that is overtaken is aborted. Nothing asks on a clock, and the sheet sends no `p`,
  so a query that needs the underlying's price (ATM) is answered by the server in its own words.
- **What it shows.** The field "Symbol, name or contract"; the chips All, Stocks, Futures, Options, Indices and
  Commodities (48 dp, scrolling sideways, the chosen one with a tick as well as a tint); the understood line and the
  near-miss sentence exactly as sent; the hits in the server's order with one heading for each run of the same group;
  each hit's title and detail as sent with Add, or Added when the server marks it or the list holds it (the page's own
  held set covers a symbol added a moment ago). A full list disables Add and says why, as the old sheet did.
- **Underlying with options.** `underlying` set shows Pick a strike, Add ATM row and Add current-month FUT row. The
  last two open F11's form through the page's `swap` with `{ mode: 'add', kind, underlying, exchange, sectionId:
  null }`; the exchange is read from the answer's own cash hit for that name, else from where the options trade
  (NFO or BFO) and whether the name is a known index, and with neither the offer is not made.
- **The seam for F14.** Pass `onPickStrike(underlying, exchange)` to `SearchSheet`; while it is absent Pick a strike is
  shown disabled. The page passes nothing yet. F14 should call `strikes` with `u` and `x` from that callback.
- **Recent.** An empty field shows the last 20 symbols added from this sheet on this device (title, detail, Add or
  Added), newest first, with Clear recents. They live in `localStorage` under `sudarshan-watchlist-recents`; every read
  and write is wrapped, so a refused storage leaves the sheet working with no recents and no error. A refused add
  records nothing.
- **`useSameStrike`** now reads the server's `strikes` op: one answer for the open expiries, then one for each of the
  next four after the row's, each asked for the strike alone (`p` the strike, `n` 0) on the underlying's own exchange
  (`underlying_exchange`, no longer the derivatives one). It keeps F12's subscription: the contracts are followed only
  while the strike sheet is open, and the reads are aborted with it. The `expiry` and `strike` fields F12 added to
  `SearchResult`, `otherExpiries` and `expiryIso` are removed, since nothing else used them.
- **One fix on the merge.** `followFixtures.ts` gains `rolled: false`, which lane W's generated `Resolved` now requires.
- **Tests.** `search.sheet.test.tsx` (the chips and their `seg`, the debounce, grouping, Added, the understood line
  and the near miss, Add and recents, the last 20 and Clear recents, a refused storage, 48 dp, the 500 ms guard, the
  ATM and FUT forms, F14's callback), `Watchlist.search.test.tsx` (the switch on and off, the form from the page),
  and `strike.sheet.test.tsx` (the `strikes` op and the release on close).

## W12 - built-in lists from the master (`presets/`, `refs/preset.py`, `ops/presets.py`, `words/presets.py`)

Three built-in lists made from today's master: **Indices**, **F&O stocks** and **Index futures**
(`preset:indices`, `preset:fno_stocks`, `preset:index_futures`). The catalogue
(`presets/__init__.py`: id, name, kind, rule or file, and the two pure functions that build a list)
is found by discovery like the other registries: a source is a module with `ENTRIES`, so W13's typed
files add `presets/typed_lists.py` (kind `typed`, an `ORDER` below `master_lists`' 20 puts them
first); W13 also made the catalogue asked at each call (see W13). `presets/master_lists.py` holds the pure builders over the master's
rows as `search.Index` keeps them (references to the snapshot's own rows, never copies).

- **Indices** are a typed list of the 24 OpenAlgo index symbols of design 3.5, in code, checked
  against today's master. A name the master lacks (or lists on another exchange) is left out and
  said: "2 names in this list are not in today's contract list: ABC, XYZ. The list is checked at
  each index review."
- **F&O stocks** are the screener's rule: the NSE cash listing of every stock with an NFO future at
  any expiry, index underlyings excluded. It is mirrored, not imported: the screener's function
  reads its own `Listing` copies of the master, and making those for 100,000 rows is the copy this
  feature promises not to make. `test_fno_stocks_equals_the_screeners_rule_on_the_same_master` runs
  both on one synthetic master, and the index underlyings are held equal to
  `sudarshan.alerts.market.sessions.INDEX_UNDERLYINGS`. The one difference is a report: an
  underlying with futures and no cash row is named ("1 stock has futures but no stock listing
  today: ABC.") where the screener drops it without a word.
- **Index futures** are, per index underlying (NFO and BFO, so SENSEX and BANKEX are in; the
  screener's index futures read NFO only), the first future whose expiry close is still ahead
  (3.7's roll): at the stop of expiry day the row is the next month's. The stop is
  `expiry.stop_ms`, the one rule of the regime of the day (see W15), the same as a follow row's. The
  market calendar is asked for NFO only when an expiry falls today, and only for a special session
  day's own end (any other day stops before or after now whatever the calendar says). An underlying with no future left is
  not listed. The list depends on the clock, so only what the master fixes is cached.

**One generation.** `refs/preset.py` builds from `master.shared()` and keeps each prepared list
(references and the names the master lacks) in a `WeakKeyDictionary` keyed by the master's
`Generation`: it goes the moment the generation does, a test asserts one generation alive and that
no row of a replaced master is kept. No thread, timer or loop, no Upstox call and nothing downloaded
(a test scans the new files' imports). The date in the words is the master's own, the day OpenAlgo's
session for the snapshot began (`session_start`); without one the line says "date not recorded".

**On the wire.** `view?ref=preset:<id>` answers the `View` record: `locked` true, version "0", no
sections or follow rows, `as_of` the master's ISO date, `lines` = ["Built-in list, locked. F&O
stocks: 213 symbols.", "From the Upstox contract list of 9 Nov 2026: every stock with futures.
Updated each morning after the day's login.", then the 120-row window line when the list is over
250, then the missing-names line], `missing` as `EXCHANGE:SYMBOL`, each row with the owner's flag and
whether a note exists (never the note). Rows are in the list's own order; the page sorts. With the
master not loaded the answer is `failed` in words ("Today's contract list is not loaded yet. It
loads after the day's login; open this list again then."). `lists` now names the three lists with
`count` (the rows now, null while the master is not loaded: not known is never 0) and `note` ("As of
9 Nov 2026"). `symbol` adds the built-in lists that hold a symbol after the own ones. `pref` and
favourites already took a `preset:<id>` ref and now find it. `refs/preset.members` is the sealed
route's reader (W23): contracts, `valid_until` for Index futures (the close at which a row becomes
the next month's), the names left out; no flag or note.

**Locked.** No write op takes a ref (they take a list's number), so none can be pointed at a
built-in list; the one write that names one is `copy`, as its source. A `move` out of one is
`invalid` with field `from` and "This is a built-in list, locked. Make a copy of it to change it."
and changes nothing; a `section_id` source is refused ("A built-in list has no sections..."). View,
sort, flags, notes and copy work. `copy {from: {ref: "preset:<id>", keys?}, to}` makes an OpenAlgo
list. `keys` are taken in the order sent (the order shown: the server cannot know a sort by price
or change); with none the whole list in its own order. Over 250 it copies the first 250 and the
answer's `words` say "Copied the first 250 of 500, in the order shown. A list holds 250." The
contract did not change.

## W13 - built-in lists from typed files (`presets/typed_lists.py`, `tests/vectors/typed.json`)

Fifteen built-in lists made from files a person typed: NIFTY 50 and NIFTY BANK (the repository's
two files, the screener's), NIFTY 100, 200 and 500, and ten sector lists. Nothing is downloaded from
NSE or anywhere else, ever, and no constituent file is added to the repository (the two it has were
the screener's already; they now carry the header line `# as of 2026-09-30`, X1).

**Where a file comes from.** The server folder `/etc/sudarshan/universes/` first (the environment
name `SUDARSHAN_UNIVERSES_DIR` points a test, or an install that keeps it elsewhere, at another
folder), then, for NIFTY 50 and NIFTY BANK only, the repository's `sudarshan/screener/universes/`.
The server copy of a name wins. A list whose file is in neither place is not offered: it is not in
`lists`, `symbol` does not look in it, and `view?ref=preset:<id>` answers that it is not offered
("That built-in list is not offered. Pick another list."). A folder that cannot be read (a file
or folder the app's user has no right to, a loop of links) is treated as absent and logged once, and
logged again only after it has been readable in between; a folder that is simply not there is the
normal state before the owner makes it, and is not logged. A file over 1 MB is not a list somebody
typed and is treated the same way.

**The files the owner types on the server** (a list is offered the moment its file is there):

| list | ref | file |
|---|---|---|
| NIFTY 50 | `preset:nifty50` | `NIFTY50.txt` (also shipped in the repository) |
| NIFTY BANK | `preset:niftybank` | `NIFTYBANK.txt` (also shipped in the repository) |
| NIFTY 100 | `preset:nifty100` | `NIFTY100.txt` |
| NIFTY 200 | `preset:nifty200` | `NIFTY200.txt` |
| NIFTY 500 | `preset:nifty500` | `NIFTY500.txt` |
| NIFTY IT | `preset:niftyit` | `NIFTYIT.txt` |
| NIFTY AUTO | `preset:niftyauto` | `NIFTYAUTO.txt` |
| NIFTY PHARMA | `preset:niftypharma` | `NIFTYPHARMA.txt` |
| NIFTY METAL | `preset:niftymetal` | `NIFTYMETAL.txt` |
| NIFTY FMCG | `preset:niftyfmcg` | `NIFTYFMCG.txt` |
| NIFTY PSU BANK | `preset:niftypsubank` | `NIFTYPSUBANK.txt` |
| NIFTY PRIVATE BANK | `preset:niftypvtbank` | `NIFTYPVTBANK.txt` |
| NIFTY REALTY | `preset:niftyrealty` | `NIFTYREALTY.txt` |
| NIFTY ENERGY | `preset:niftyenergy` | `NIFTYENERGY.txt` |
| NIFTY FINANCIAL SERVICES | `preset:niftyfinservice` | `NIFTYFINSERVICE.txt` |

**The format.** One NSE symbol a line, in capital letters, digits, `&`, `-` or `_` (`M&M`,
`BAJAJ-AUTO`); `#` starts a comment, alone or after a symbol; blank lines are ignored; the date of
the index review is the header line `# as of YYYY-MM-DD`. A file with no such line reads "date not
recorded". A line that is not a symbol (a space in it, lower case) or that repeats a symbol already
on an earlier line is left out of the list and named by its line number in the list's words; the
rest of the list stands. The screener's reader is more forgiving (it upper-cases a lower-case line);
for the two shipped files, which are clean, both readers give the same symbols and a test holds
them equal. Example (made-up symbols):

```
# as of 2026-09-30
AAAA
BBBB   # a comment may follow a symbol
```

**Words.** "Built-in list, locked. NIFTY 50 as of 30 Sep 2026: 50 symbols." (the file's own date, never
the master's; "NIFTY 50 (date not recorded)" for a file with no header), then the window line when
the list is over 250, then the names today's master lacks in W12's words ("2 names in this list are
not in today's contract list: ABC, XYZ. The list is checked at each index review."), then the lines
left out ("2 lines in this list's file were left out: line 3 ("ab cd" is not a symbol), line 5 (AAA
is already on line 2). Symbols are capital letters and digits.", the first five named). Names are
mapped to NSE cash listings of the master (EQ rows), in the file's order. Over 250 (NIFTY 500) W12's
window line applies and `copy` cuts at 250 with W12's sentence ("Copied the first 250 of 300, in the
order shown. A list holds 250."). On the wire a typed list is the same `View` as W12's: `locked`,
version "0", `as_of` the file's ISO date or null, `missing` as `NSE:<SYMBOL>`; `lists` gives each
offered list its `count` and `note` ("As of 30 Sep 2026", or null for a file with no date).

**A changed file is read again, without a restart.** One `stat` per call (path, modification time,
size and inode) is the check; the symbols are parsed again only when it changes. The prepared list
(references to the master's own rows and the names the master lacks) stays tied to the master's
generation as W12's are, and `refs/preset.py` now keeps it with that stamp too, so the next call
after the file changed prepares it again against the same generation. No thread, timer or file
watcher. The seam W12 left grew by one idea: `presets.CATALOGUE` is now asked at each call (a module
attribute that answers the lists offered now), because a typed list comes and goes with its file;
an `Entry` may carry `offered` and `stamp`, and a `Prepared` or `Built` may carry `notes`.

**To add or update a file on the server** (the owner, as the server's administrator; the app runs as
its own user, which only needs to read the folder and the files):

```
sudo mkdir -p /etc/sudarshan/universes
sudo chmod 755 /etc/sudarshan/universes
sudo nano /etc/sudarshan/universes/NIFTY100.txt      # type or paste the symbols, with the header line
sudo chmod 644 /etc/sudarshan/universes/NIFTY100.txt
```

Mode 755 on the folder and 644 on each file let the app's user read them while only an administrator
can change them. If `/etc/sudarshan` itself exists with a stricter mode, it needs the execute bit for
others (`sudo chmod o+x /etc/sudarshan`) so the app's user can reach the folder. Nothing needs a restart:
open the list again and it is read. At each index review, retype the file with the new date in its
header line; the lists say the date they are as of, so a file that was not updated shows its old date.

## Refs: the grammar

A ref is a string naming one list. The phone builds them and so does the alerts service, so the
grammar is written here once; `refs.split` and the readers of `refs/` are its code.

| ref | what it names |
|---|---|
| `list:<id>` | one of the owner's lists, `<id>` being OpenAlgo's list id (a whole number) |
| `list:<id>/section:<sid>` | one section of that list (members only: a view is of the whole list) |
| `preset:<id>` | a built-in list, `<id>` from the catalogue (`indices`, `fno_stocks`, `index_futures`, a typed list's file name) |
| `flag:<n>` | every symbol carrying flag `n`, 1 to 5 |
| `ranked:<source>:<asset>:<category>` | a live ranking from Upstox, for example `ranked:futures:INDEX:TOP_TRADED` |

A ranked list changes with every read, so it has rows but no members: the members route and the
alerts refuse it in words. A section ref is only for members (a list alert on one section).

## F14 - the strike picker

With the switch on, Pick a strike in the search sheet (`onPickStrike(underlying, exchange)`, F13's seam) swaps
`?sheet=add` for `?sheet=strikes` (`useOverlay`: the pick rides in the history entry's state like the follow
form, so a reload never reopens it). `StrikePicker.tsx` is the sheet, `useStrikeLadder.ts` its reads of the
`strikes` op, `strikePicker.ts` the pure parts (`atmOf`, `ruleFor`), and `LiveList` holds it because the page's
socket is there. With the switch off nothing changes: the address is not known and `LiveList` takes no picker.
- **What it shows.** The underlying's price with its feed line ("Real-time" only while ticks arrive, as
  every other line), the expiry tabs as the server labels them (the chosen one with a tick as well as a tint),
  the server's `words` as sent, and the 21 strikes of ATM plus or minus 10 (`n` 10), each with its CE and PE
  price ticking, Add CE, Add PE and Add as one row; the ATM strike carries the word "ATM" and a tint. Add ATM row
  opens F11's form and Open option chain is the page's `chainPath`. Every control is `min-h-12`; the sheet is
  the existing `BottomSheet`, so its 500 ms open guard covers the first taps.
- **What it follows.** The underlying is an item of the live list (read once over REST like a row, so a closed
  market still shows its last price), and the CE and PE of the strikes on screen are followed in LTP mode with
  no snapshot of their own. All 43 are `follow.sheet`, counted first in the 250. They are released when the
  picker closes and the moment a tab is left (the new tab's follow from the answer that names them). A strike's
  price shows a dash until its first tick.
- **When it asks.** `strikes` is asked on opening without `p` (the tabs, the middle of the ladder and the
  server's words), then with `p` from the underlying's own price (the newer of its tick and its read, as a
  row's) as soon as there is one; again only when that price's nearest strike (a tie to the lower, the server's
  rule) is no longer the ATM of the answer on screen, and on a tab change. One read at a time (a tab change
  takes the place of the one in flight), and at most one in 2 s: a move that comes inside the gap waits for it
  with one timeout, set only while there is such a need. A price that stays inside its band asks nothing, and a
  price already asked with is not asked again, so a server and a page that disagree on a tie cannot loop. Thirty
  minutes with a still price ask nothing (tested).
- **An expiry that ended.** `invalid` on `exp` re-reads the ladder without `exp` and shows the server's sentence
  above it, and the tab on screen is the one the server then names.
- **Add CE and Add PE** use the page's own add (`watchlists.addItem`, as a search result does) and mark the
  strike "Added" with a tick; a symbol the list already holds is Added from the start, a full list disables the
  adds and says so, and a refused add toasts the server's words and stays addable.
- **Add as one row** opens F12's form through the page's `swap` with `fixed: { strike, expiry }`. The form keeps
  a rule, not a date, so the tab maps to one: the first tab is Nearest, the second Next, a monthly expiry in
  this calendar month (IST) This month's, one in the next month Next month's. **A tab no rule says** (a weekly
  from the third on, a month after next) disables Add as one row and says why under the tabs; Add CE and Add PE
  work there. This is the one place the picker offers less than the design's wording.
- **Tests.** `strikePicker.test.ts` (the ATM tie, the rules), `strike.picker.test.tsx` (the tabs and the word
  ATM, the price and its feed line, ATM moving only past the midpoint with ten ticks inside it asking nothing,
  the 2 s gap, 30 minutes asking nothing, a tab change, an ended expiry, every subscription released on close
  and on a tab change, the cap, Add CE and Add PE and Added, the form's seam, 48 dp, the 500 ms guard, the
  switch off) and `Watchlist.search.test.tsx` (from the search to the picker to the form, and a reload).

## W17 - the search and strikes ops (`ops/search.py`, `words/strikes.py`)

Two read ops over the one master generation (`master.shared().index()`: built once per snapshot in
slices that yield to the hub, behind the single flight, dropped with the snapshot; this module keeps
nothing of its own, starts no thread and makes no Upstox call).

- **`search`** (class `search`, 300 a minute) takes `q`, `seg` (the chip), `limit` (at most 50), `p`
  (the underlying's price from the page's own tick, which only orders options by distance and
  resolves ATM) and `list` (the OpenAlgo list the sheet adds to). It answers the hits grouped in the
  design's order, stocks, indices, futures, options, commodities, each group keeping the grammar's
  order, so the page prints one heading a group; each hit is the group, the title ("NIFTY 25,900
  CE"), the detail ("NFO · 24 Nov 2026 · lot 65"), the master's `Contract` and `added` (true when the
  list holds that exchange and symbol; a list that is not the owner's is `gone`). `understood` is the
  query in one line, `underlying` is set when options exist (Pick a strike) and `underlying_exchange`
  beside it is the exchange `strikes` takes as `x` (`NSE_INDEX`, `NSE`, `BSE_INDEX`, `BSE`, `MCX`),
  read from the master and never guessed (the cash listing on the exchange the options trade
  against, so RELIANCE answers `NSE` though BSE lists it too; an underlying with no cash row takes
  its derivatives exchange); both are null together, and `underlying` keeps the name it always
  had, so an older page is unaffected. `suggestion` is
  the near miss ("No exact match for RELAINCE. Did you mean RELIANCE?"), also said when the chip
  hides what All would show ("Nothing for RELIANCE under Indices. Try All."); it is null while
  the answer waits for a price (ATM with none sent). A blank query answers empty: Recent is the
  page's. `contract.py` gained the optional `list` field of `search` and `types.ts` was regenerated.
- **`strikes`** takes `u` (the underlying), `x` (its exchange), `exp` (an ISO date from the answer,
  else the nearest), `n` (strikes each side, default 10, at most 25) and `p`. It answers the open
  expiries ("Nearest 24 Nov", "1 Dec", "29 Dec (month)", with `month` true for the last expiry of a
  calendar month, the nearest one included), and for the chosen expiry the strikes ATM plus or minus
  `n` with `atm` true on one of them (W14's rule: nearest, a tie to the lower), each with its CE and
  PE `Contract` from the master, never a built name. A side the master lacks is null and said in
  `words`. With no price no strike is marked: the middle of the ladder is shown and `words` says the
  price is not known yet, so the page asks again with `p` on the first tick. An expiry is open until
  it stops by the one rule of `expiry.stop_ms` (see "One rule for when an expiring contract stops"),
  not by a close of its own: an expiring index contract at 15:40 until 6 Dec 2026 and 15:30 from
  7 Dec, a stock contract at the F&O close (15:40, then 15:45), a special session day at the
  calendar's end of that day (read at most once a minute for each exchange). On its last day it is
  offered and `words` says that real time ("This expiry ends today at 15:40."); after it the expiry
  is gone, and asking for it is `invalid` with field `exp`. An MCX underlying is not covered by the
  rule, so `words` says nothing about its end time rather than a wrong one, and its expiry drops at
  the end of MCX's own calendar window for the day if the calendar has one, else at the end of the
  day.

The add form's preview of the row a chosen strike or ATM rule would make is `resolve` with `drafts` (W15,
"Draft rows"): the page sends the underlying and rule it picked from these two answers, never a stored
id, and reads `words` and `rolled` back.

Both answer `failed` in words while the master is not loaded and `off` with the switch off. Tests:
`tests/test_watchlists_search_ops.py` (the grammar's vectors through the bridge, grouping, Added, the
understood line and near miss, chips, strikes with and without a price, a last-day expiry, a missing
side, the class limit, one generation and no new thread, and timing on 120,000 synthetic rows).

## W23 - the sealed members route (`bridge/internal.py`, `members.py`, `words/members.py`)

`POST /sudarshan/watchlists/internal/read?op=members|version` is how `sudarshan-alerts` reads a
list. It follows the screener's internal route: no session and no CSRF token (the service has no
browser; its seal is its proof), one Fernet token as the body under the vault purpose
`WATCHLISTS_INTERNAL_INFO`, a nonce book that refuses a replayed request (`NoncesFull` answers 503
rather than forget a nonce that could still be replayed), 16 KiB at most, and a 404 for any request
that carries a proxy header (the service calls 127.0.0.1 directly). The seal is checked before the
switch, so an unsealed caller learns nothing. The route is found by discovery (`bridge/internal.py`
defines `install(app)`), reads only, and is not in `contract.WRITE_ROUTES`; a viewer's session is no
key to it. The helpers are the screener's (`open_sealed`, `NonceBook`, `fernet_for`); only the proxy
header list is repeated, and a test keeps it equal to the screener's.

`members {ref}` answers `{version, ref_version, created_at, list_key, rows, left_out, valid_until}`:

- `version` is the lists version, the text `op=version` answers alone. `ref_version` is the ref's
  own: a 12 character digest of its instruments and what it leaves out, so a consumer can tell its
  own list changed when the lists version moved for another's.
- `created_at` and `list_key` (the same text) are a `list:` ref's identity, OpenAlgo's `created_at`
  of the list. OpenAlgo gives a deleted list's id to the next list made; the alerts compare it and
  stop a group whose list id now names another list. Absent for built-in and flag lists. The alerts
  client reads the field `created_at`.
- `rows`: `{symbol, exchange, instrument_key, tick_size, expiry, lot, valid_until?}` through the
  master, in the list's order, one per instrument. A future (a month-future follow row resolved to
  the contract valid at the call, or a future held as a symbol, or a built-in list's) carries
  `valid_until`, the epoch ms its contract stops by `expiry.stop_ms` (a special session day's close
  from the calendar); the consumer asks again then. The answer's own `valid_until` is the earliest.
- `left_out`: `{symbol, why, reason}`. `atm` is an at-the-money follow row and `strike` a strike
  row, both left out because they change contract during the day; `missing` is a symbol today's
  master lacks (or a future row with no contract this month). `reason` is words for the owner.
- Never a flag, a note or a price.

Refusals use the bridge's envelope: `off` with the switch off (both ops; the alerts freeze the group
on it), `gone` (404) for a list, section or built-in list that is not there, `invalid` for a ref that
is not one (and, in words, for a ranked ref), `failed` when today's contract list is not loaded, so
the caller changes nothing and asks again. The readers (`refs/list.py`, `refs/flag.py`,
`refs/preset.py`) each build a `Members` record; `members.py` is the pure part (cuts, the record).
A list reads its view once, resolves its month-future rows with the same code as `resolve`
(`resolver.resolve_rows`, no price needed) and takes OpenAlgo's `created_at` from the same read.
Tests: `tests/test_watchlists_members.py` (the readers) and `tests/test_watchlists_internal.py` (the
seal, each kind of ref, futures, the versions, the switch, and the alerts service's own members
client reading the route through a test app).
