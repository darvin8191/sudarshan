# sudarshan/watchlists/ - Watchlists v2 (M8.4 onwards)

Rules, contract and words for the multi-list watchlist features. Everything here is switched off
until `SUDARSHAN_WATCHLISTS_V2` is turned on. The pure modules take every input as an argument and do
no I/O, no clock and no logging; words a trader reads live in `words/<feature>.py`. This folder
starts no thread, timer or event loop. Tests: `sudarshan/watchlists/tests/` (vectors in
`tests/vectors/`, typed by hand, never made by the code they test).

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

## F1 - the v2 client (`frontend/src/sudarshan/watchlist/v2/`)

The phone's half of the contract, with no screen yet. `api.ts` calls any op by name through the
route `types.ts` gives it (`callOp`), unwraps the envelope and throws `WatchlistsError` with the
server's own sentence, or the page's words when there was no answer or the answer was not the
bridge's; a status code or an address never reaches a screen. `useListView.ts` holds the query keys
and the reads: `useV2State` is the only read made while the switch is off, and `useListView` and
`useLists` wait for it to say `enabled`. The version rule is `keepNewer`: versions are `<boot>:<n>`,
an older answer than the one held is dropped (looked at when the answer arrives, not when the read
began), and two boots cannot be ordered so the later answer wins. `usePush.ts` joins the owner's
room after each connect with an ack, collects pushes for 200 ms into one read (`applyPushes`, which
skips a push whose version the view already holds), and reads everything again after a reconnect;
nothing is read on a timer. `useOverlay(v2)` knows `?sheet=lists` only when v2 is on, so Back closes
a sheet first as before and a bookmarked address reads as nothing while it is off. Tests run against
`mockBridge.ts`, a stand-in that answers the contract's routes. Still to check on a real bridge:
whether a write wants its path fields in the body as well as the path (the client sends both).


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
