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
