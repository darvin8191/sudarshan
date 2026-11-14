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

- **Closing not seen** (status `unmatched`, ended holding more than its fills took off). The
  opening is read once and the catch-up reaches back 3 trading days, so after the journal was off
  for longer (a rollback and forward, or the switch off), a share trade whose sale it missed
  would stay an open LONG for ever. Each night the 22:00 catch-up holds every open delivery trade
  of a share not traded that day against Upstox's holdings (settled and yesterday's). Where the
  account holds fewer shares than the trade, the trade is ended there, out of the figures, and
  the journal goes on from what the account holds: a later sale of the rest is listed apart, a
  later purchase is a new trade. The count is kept by its day (`key_recount`, schema step 5) and
  read again each night until the share's next fill, so a read that was wrong is put right and
  the trade comes back. If the history later brings the missed sale, the trade closes with its
  result.

## Actual charges and the tax working paper (`SUDARSHAN_TAX`)

One switch, the portfolio's too, read from OpenAlgo's `.env`; off in code.

- **Charges, night by night** (`jobs/charges.py`, a step of the 22:00 catch-up). Upstox's own
  charges are read through the portfolio (`pnl_report`, part `charges`, with `from` and `to`) for
  the last 3 trading days, for each ended month from the 3rd of the next, and then walking back
  5 earlier days a night to the journal's first fill; at most 20 reads a night. An answer that is
  all zero or empty while the journal holds fills is "not ready" and asked again, 7 nights in
  all; after that the month's row, less the days that did answer, answers for the day.
  Upstox's month is the whole account's, so it answers only in a month the journal covers
  whole: every trading day of it, from the first, checked against Upstox's list by the close
  check or rebuilt from the history (`actual.covers`). In any other month (the journal's
  first, or one with a day it was down) the trades keep the estimate and the trade sheet says
  "Upstox gave only the month's total, and the journal does not cover the whole month".
- **Into orders and trades** (`actual.py`, pure rule in `core/spread.py`). A day's row is shared
  among that day's orders (`charges_order`, schema step 6) and each order among the trades that
  hold its fills, so a day's trades add up to Upstox's total to the paisa. A trade carries the
  actual, and its net is gross less the actual, only once every order of it has a share. A
  replay keeps it; a late fill is shared again from the kept row. Live journal only.
- **Estimate against actual** (`charges_reconcile(fy)`, `core/reconcile.py`): per month and
  Upstox segment, flagged above 5%, with the part that is off most named in a sentence. A
  month the journal does not cover whole is not compared and never marked "Check".
- **The Tax pack** asks the portfolio for the tax working paper (`tax_pack`) and prints it
  (`taxpack.py`). The journal works out no tax figure and holds no lot matching of its own; the
  paper printed is kept whole in the archive row, so an archived pack never changes until it is
  built again. Without the paper (switch off, portfolio not answering) the pack says why and
  holds the journal's own blocks.

Every period report and the tax pack carry one line when the period has any: "Not in these
figures: N positions opened before the journal started, M contracts that ended at expiry, K
trades the journal did not see closed. Upstox's own report has them." The Day report says the
same of its trade count.

Limits: a read that does not happen (the portfolio service down) is tried again at the next
replay of the instrument, so a derivative first seen on a day the portfolio could not be asked
can read as a short until it is. A contract that expired before the first read says nothing of
what was held of it, so a position opened before the journal and closed on a day rebuilt from
Upstox's history after its expiry shows as ended at expiry, not as opened before; either way it
is out of the figures.

The nightly count covers shares only, runs at 22:00 (so a trade whose sale was missed reads as
open until the first 22:00 after the journal is back), and leaves a share traded that day for
another night. More shares than the trade (a bonus, a transfer in) change nothing; fewer for a
reason other than a sale (a reverse split, a transfer out) end the trade the same way. A
derivative is not counted again: one whose closing was missed ends at its expiry.

A day rebuilt from Upstox's trade history (the journal was down, or the three trading days
before the install) has no products. A derivative trade is taken as carried. A share's trades of
one day are taken as intraday when that day's bought and sold quantities are equal (nothing of
it was carried either way), else as delivery. So an intraday short on such a day is one closed
short, and a delivery sale of shares held before is still listed apart. Limit: a day that mixes
both in one share with unequal quantities (shorted intraday and also bought for delivery) is
taken as delivery whole; check that share against the Upstox app.

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
