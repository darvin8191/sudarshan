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
