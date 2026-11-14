# SUDARSHAN trade journal and reports (M14, M15)

Skeleton written with the contract (JF0). The owner-facing sections come with the release.

## What the journal is

The journal records every fill Upstox reports, pairs the fills of one instrument and product into
round trips ("trades"), and measures them: win rate, profit factor, expectancy, R, hold time,
equity curve and drawdown, calendar, heat map, expiry split, excursions, a week against NIFTY 50.
The reports (M15) are words and files built from the same figures: the day report, the morning
brief, the week in review, the monthly statement and the tax pack.

The pure part lives in `core/` and performs no I/O of any kind. The service, the store, the
bridge and the pages are built on `core/kinds.py`, which is the one contract.

## What the journal cannot see

The journal pairs only the fills it has seen. Two kinds of position have no fill it could pair,
and both are kept out of every figure (the win rate, the analytics, the Day report's trade count,
the week in review, the monthly statement and the tax pack) and listed apart:

- **Opened before the journal started** (status `unmatched`). When the journal first sees an
  instrument in a carry product it reads, once, what the account held of it at the start of that
  day: Upstox's overnight quantity for a derivative, its holdings (settled and yesterday's) for a
  share. The fills that run that position down are one trade whose buying (or selling) price is
  not known here: it has no average, no gross and no live P&L, and it is never shown as LONG or
  SHORT. A delivery sale is never read as a short, read or not: the part of a sale past what the
  journal saw bought is listed the same way. Upstox's own report has the result.
- **Ended at expiry** (status `expired`). An expiry has no fill, so a contract left open to its
  expiry never closes by itself. The close check marks every open trade whose contract can no
  longer trade (its expiry day has passed, or it is an NSE or BSE derivative expiring that day)
  as ended at expiry, out of the figures until a settlement value is known (Upstox's matched
  pairs, which the 22:00 catch-up already keeps, can supply it in a later release).

Every period report and the tax pack carry one line when the period has any: "Not in these
figures: N positions opened before the journal started, M contracts that ended at expiry.
Upstox's own report has them." The Day report says the same of its trade count.

Limits: a read that does not happen (the portfolio service down) is tried again at the next
replay of the instrument, so a derivative first seen on a day the portfolio could not be asked
can read as a short until it is. A contract that expired before the first read says nothing of
what was held of it, so a position opened before the journal and closed on a day rebuilt from
Upstox's history after its expiry shows as ended at expiry, not as opened before; either way it
is out of the figures.

## Units

| what | unit | name |
|---|---|---|
| money | integer paise | `*_paise` |
| quantity | integer units | `qty` |
| price | integer rupees x 10^4 (Y31: currency ticks at 0.0025) | `price_u`, `*_avg_u` |
| ratio | Decimal string, fixed places | `win_rate` "60.00", `beta` "2.0000" |
| instant | epoch milliseconds | `*_ms` |
| day | integer YYYYMMDD in IST | `day`, `open_day`, `close_day` |

`qty x price_u` is exact in 10^-4 rupee; it becomes paise once, half-up to the paisa.

## Service

| | |
|---|---|
| service | `sudarshan-journal` |
| port | 5586 (loopback, one listener, one `/health`) |
| switch | `SUDARSHAN_JOURNAL` (off in code) |
| bridge | `/sudarshan/journal/api` (owner only) |

## Op names

state, trips, trip_get, trip_update, tags_list, tag_create, tag_update, playbooks_list,
playbook_create, playbook_update, analytics, reports_list, report_get, report_file, report_build,
settings_get, settings_set, reconcile_now, delivery, family_day. The table with methods, routes,
requests and answers is `OPS` in `core/kinds.py`.

## The TypeScript twin

`frontend/src/sudarshan/journal/types.ts` is generated: change `core/kinds.py`, then run

    uv run python -m sudarshan.journal.gentypes

`--check` writes nothing and exits 1 when the file differs; a test asserts the same.

## Vectors

`tests/vectors/*.json` are plain JSON, synthetic, money in paise and prices in `price_u`. The
TypeScript side reads `pairing.json` and `metrics.json` for the live open-trade figures.
