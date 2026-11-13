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

## W6 - own lists and view (`view.py`, `refs/`, `ops/view.py`, `words/view.py`)

`view` (GET, class read) answers the `View` record of a `list:<id>` ref: OpenAlgo's list and the
overlay merged. `ops/view.py` picks the reader by the ref's kind; `refs/` is found with
`pkgutil.iter_modules` and each module names its `KIND` and gives `view(ident, user)` and
`members(ident, user)` (the sealed members route's reader, documented in `refs/__init__.py` and
implemented in W23; until then it raises `NotImplementedError` and nothing calls it). A kind that
is not built (preset W12, flag W9, ranked W22), a ref that is not `<kind>:<id>` and a list id that
is not a whole number answer `invalid` with field `ref`; a list that does not exist (or is another
user's) answers `gone`.

`refs/list.py` reads OpenAlgo's list and items through `database/watchlist_db.py` (read only, its
scoped session removed after the read) and the overlay in one read-only session of the store: the
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

`version` is the list's stored `wl_list.version` as text ("0" until a write makes the row).
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
  because a list made anew after its id was reused starts there). Writes are not skipped by their own
  push: the write's answer carries the list's version, not the lists version, so the push of a
  device's own write costs one extra read of what the write already re-read. Lane W could add the
  lists version to a write's answer to close that.
- **A push refreshes what the page shows (M2).** The rows and tabs come from OpenAlgo's lists
  (`WATCHLISTS_KEY`), so a push of why `items`, `lists` or `arranged` that names an own list (or
  `lists`) reads them again beside the views it names; a symbol added on /trading shows on the
  phone. A push names every list it changed: `refs` when present, else `ref`, and "lists" alone reads
  every open view. `PushNames` in `useListView.ts` is a local extension carrying `refs`: remove it
  and read `Push.refs` when lane W's contract change is merged and the types are regenerated. A
  reconnect reads `WATCHLISTS_KEY` too. Sections, rows and preferences read the named views only.
- **A record refusal (L11).** When `state` answers `record` (the store is newer than this app, or
  cannot be read) the page shows the server's sentence above the lists, which keep working as
  before; the switch off, an unreachable bridge or a busy one say nothing.
- **Exact keys (L12).** `WATCHLISTS_KEY` is a prefix of every v2 key, so every invalidation and
  cancel of it is `exact` (`writeThenRead`, `useWatchlists`, the screener push): a write re-reads
  only what it changed. `V2_KEY` stays where it is.
- **W5 decision.** "`ref` is the one ref changed, else `lists`" is no longer all a push says: the
  phone reads `refs` and falls back to `ref` only for a bridge that does not send it.

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
key refuses a second one, told `conflict`) with `oa_created_at` as `view.created_key` gives it. A
guard row whose `oa_created_at` differs belongs to a deleted list whose id was reused: a write
holding "0" prunes that list's overlay (`prune.drop_overlay`: sections, items, follow rows,
preference; favourite slots are the delete hook's) in the same transaction and starts a fresh row
at the epoch milliseconds, which no client of the old overlay can hold; a write holding the old
version is `conflict` and prunes nothing. A version no list can hold (not a number) is `conflict`.

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
until you arrange the list again."); the overlay is saved, OpenAlgo's order is as it was, and the
page shows the list again.

`copy` takes `from` (`ref` of an own list, plus `keys` of `EXCHANGE:SYMBOL` or one `section_id`,
never both) and `to` (an existing `list`, or a new `name`; `section_id` only for an existing list),
and `move: true` for a move (there is no op of its own). A built-in or ranked source, a ref that is
not a list, a symbol the source does not hold and the same list as source and target are `invalid`
with the field named. Everything that can be refused is refused first, in words with the numbers:
a stale `version` when a section is to be set (`conflict`), 250 symbols a list holds ("Swing has
room for 3 more symbols; this section has 8. Make room in Swing or pick another list."), 50 lists
("You have 50 lists, the most there can be. Delete one to make a new list."), a name taken. A symbol
already in the target is skipped, never an error (`skipped` in the answer); a move still takes it
out of the source. Order of writes, one file at a time: OpenAlgo's `add_item` for each symbol (one
commit each) or `create_watchlist` with all items in one call; then, only when a section is set, one
overlay transaction that claims the target's version and writes the section rows; then for a move
OpenAlgo's `remove_item` for each, and one overlay transaction dropping the source's section rows
(its failure is logged, because rows for symbols the list no longer has are ignored). A failure
between steps answers `failed` with what was done, and every state it leaves is valid: symbols
OpenAlgo has and the overlay does not know read as No section. A move is a removal from the
screener's point of view, which is intended.

Bumps (`ops.bumps`), once per write and never also by the hook, because these ops call OpenAlgo's
functions in process: `arrange` with why `arranged` and `list:<id>`; `copy` and `move` with why
`items` and the target's ref (a move adds the source's, so the push says `lists`).

The yield: writes of up to 250 items yield to the hub (`time.sleep(0)`) between OpenAlgo's per-item
commits, because SQLite's file I/O does not yield under eventlet. It is never done inside an overlay
transaction: that holds the store's write lock, and another greenlet writing to the store would
wait inside C for a holder that cannot be resumed to commit. The overlay rows go in as one batch.
Tests: `tests/test_watchlists_arrange.py`, which includes the yield under a real eventlet hub in a
subprocess (a second greenlet runs 240+ times during a 250-item copy, and not at all with the yield
removed).

