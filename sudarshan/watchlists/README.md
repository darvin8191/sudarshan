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
as `closes` (15:30 IST when the calendar has no row). The ATM is the strike nearest the price, a tie
going to the lower strike (a test holds it equal to OpenAlgo's own function); a held strike is kept
while the price stays inside its band (midpoint to the neighbour plus 10% of the gap, open ends).
The price is the page's own tick while its socket is up, however old, then the page snapshot, then
the feed's; none means "waiting". Vectors: `tests/vectors/dynamic.json`. `numfmt.py` holds the
Indian number and IST time formats the words use.

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

## W21 - ranked rows (`ranked.py`, `words/ranked.py`)

Pure mapping of an Upstox Smartlist answer to rows, through the master by instrument key, in
Upstox's order, with the ranking's own figure per category ("Traded Rs 1,320.9 cr", "Volume
2,45,600", "OI +12.4%", "IV 14.2%, +1.1", "Premium 38.40", "Costs Rs 4,350 to buy"), the time Upstox
made it (`time_stamp`, read as a number or an ISO text, IST when it has no offset) and a count of
contracts the master does not have. Smartlist ranks futures, options and MTF only: the catalogue
offers futures and options for Index, Stock and Commodity (commodities with the first four
categories), 44 rankings, never MTF. Words say "Ranked by Upstox" and the age (stale after ten
minutes), never "Live"; the sign-in, closed, unavailable and wait lines are here too. No read is
made here: the Smartlist client is a later PR. The answer's exact envelope, the unit of traded
value and of premium, and the base of `previous` are checked on a real read (design facts W-1 and
W-10). Vectors: `tests/vectors/ranked.json`.

## W11 - master access (`master.py`) and the exception's tests (`tests/test_exception.py`)

`master.py` is the one reader of OpenAlgo's symbol master. It keeps one generation of reference
maps (`search.Index`: the snapshot's own rows, never copies) tied to the snapshot it came from,
builds it in slices that yield to the hub behind a keyed single flight, and drops it when the
snapshot is replaced or lapses. Symbol, exchange and instrument-key lookups read OpenAlgo's snapshot
and keep nothing. `tests/test_exception.py` holds the limits that let this feature live inside
OpenAlgo: no thread, timer, executor, scheduler or event loop anywhere in `sudarshan/watchlists/`
or the bridge (an AST scan, and a check that no thread starts for any op or master swap); one
generation, the old rows freed with it; the derived maps under 15 MB on 120,000 rows (about 2 MB
measured). Do not loosen these tests to land a feature.

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
`REQUIRED_MIGRATIONS`: an overlay of OpenAlgo's lists is not worth rolling a release back, and with
the file missing the bridge answers in words while OpenAlgo's lists keep working. A failure still
shows in the summary. Safe to run again: tables, columns and indexes only when missing, the `schema`
record only when absent or lower, nothing seeded, no row or value ever written over. `--status`
opens the file read-only and prints the file, its schema, this release's schema and what is pending
(the file's bytes are unchanged; SQLite may leave its own `-wal` and `-shm` beside a file in WAL
mode). A file from a newer release is refused untouched, in the owner's sentence. Tests:
`tests/test_watchlists_migration.py` (missing, populated copy forced back, newer, the app store
opening the result).

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
token is `session`), and the shared limiter's per-class count (`busy`; it acts as the request
arrives, so it may answer before the checks above). Then the switch `SUDARSHAN_WATCHLISTS_V2`, read at
each request and shipped `off` in `openalgo-env.conf`: off, `state` answers `{"enabled": false}`,
every other op `off`, and nothing is opened or read. Then the record check (`engine.ready()`, its
outcome kept five seconds in a `LockedTTLCache`, a newer or unreadable file answers `record` in the
owner's words), then the request is read against the contract (query string for GET and DELETE, JSON
otherwise; a route's id must equal the same field of the body, else `invalid` with that field).
`state` answers the switch, `contract.LIMITS` and the five default flag names; versions and the push
come with W5, favourites W10, ranked and alerts availability W22. Tests: `tests/test_watchlists_bridge.py`
(in process, real CSRF, limiter and user table) and `test/test_gthread_watchlists.py` (a real gunicorn
gthread worker: concurrent reads, the switch off, the limit under a barrier).

## W5 - the version, the push and the hook (`versions.py`, `bridge/push.py`, `store/prune.py`)

The lists version is an in-memory counter read as `"<boot>:<n>"`, where `<boot>` is new at every
process start, so a reader only compares for equality. The favourites and flags versions are the
store's integers (`wl_meta.favourites_version`, `flags_version`; an absent row reads as 0), because
the writes of favourites and flags (W9, W10) name the version they were decided on and a stale one
is a `conflict`, across restarts. `versions.py` keeps an in-memory mirror of the two, loaded once
from the store on first use and moved forward by a writer right after its commit
(`advance_favourites` or `advance_flags` inside the write's transaction, one atomic update, then
`committed(kind, n)`); a store that cannot be read leaves them out of the push and of `state`, never
raises. A lists bump and the mirror are under a real lock (`utils.real_threading`) whose critical
section is the increment or one comparison. `versions.bump(why, refs)` bumps the lists version once
and emits `contract.EVENT_PUSH` (`{v, ref, why}` plus `favourites` and `flags` when known) to the
room `sudarshan:watchlists:owner` through `extensions.emit_from_any_thread`; `ref` is the one ref
changed, else `lists`. A push that cannot be sent is logged, never raised. `state` answers
`versions`. An app joins the room with `contract.EVENT_JOIN` (an ack): the owner's session only, and
refused in words with the switch off.

The one rule: every write that changes what a list, a section or a flag list holds bumps the lists
version once, after its transaction commits, from one of two sources and never both. (1) The hook:
`bridge/push.py` registers one `after_request`; for a 2xx answer to a non-GET request of OpenAlgo's
watchlist blueprint, or to the screener's internal watchlist route when its op is `watchlist_apply`
and it found the list, it bumps and pushes. Every other request pays one membership test of
`request.blueprint`. A delete also prunes that list's overlay (`store/prune.py`: list guard row,
sections, section items, follow rows, preferences, and favourite slots naming `list:<id>`, with the
favourites version raised in the same transaction) in one transaction in the overlay's own file; a
clear drops section membership only, because /trading cannot see sections or follow rows. A store file that is missing (never
created by a delete) or newer is skipped and logged, and a failure in the hook is logged and never
changes OpenAlgo's answer. (2) The bridge's own ops: a handler marked `@ops.bumps(why, refs)` has
the bridge bump and push after it returns, never after a refusal. The hook starts no thread, timer
or loop. Tests: `tests/test_watchlists_push.py`; `test/test_gthread_watchlists.py` (twelve bumps at
once, twelve writes through the hook under a real gthread worker).
