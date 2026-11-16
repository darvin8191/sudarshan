# sudarshan/charts/ - chart documents across devices (M4.18)

The owner's drawings, the phone chart's indicator set and the terminal's saved layouts, kept on
the server so every device he signs in on shows the same ones. Switched off until
`SUDARSHAN_CHARTS_SYNC` is on; off, every device keeps its own copy exactly as before, and the
bridge answers that it is switched off without opening anything.

## The table

`chart_doc(user, kind, key, revision, body, updated_ms)` in `db/sudarshan_charts.db` (`schema.py`):

| kind | key | body | written by |
|---|---|---|---|
| `drawings` | the engine's instrument key (`NSE:INFY`, each part URI-encoded) | the draw tier's document (`{version, drawings, groups?}`) | the phone chart |
| `phone_set` | `phone` | the phone's indicator set (`{version: 1, studies: [{id, settings}]}`) | the phone chart |
| `workspace` | the terminal's catalogue namespace, URI-encoded | the workspace tier's catalogue (layouts and indicator templates) | `/trading` |
| `template` | - | - | nobody yet: the terminal's indicator templates travel inside its catalogue |

A body is at most 5 MB (the engine's own limit on a document), nested at most 32 deep with at most
100,000 values, and is stored as compact JSON text. The server checks those bounds and the top of
each kind's shape; each device checks the whole document with the engine's own parsers when it
reads one. Documents per kind are capped (`store.LIMITS`): 2,000 instruments of drawings, 8 sets,
8 catalogues. Nothing is ever deleted: an instrument cleared of drawings keeps an empty document.

## Revisions and conflicts

Every write names the revision it replaces (0 for a document the device knows of none). The
store's compare-and-set is the transaction's first statement, an UPDATE that takes SQLite's write
lock, so two writes from one revision never both land. The loser is answered `stale` (409) with
the document as it is now, and the device shows that copy and says so in words, offering its own
copy back where it can (the phone's "Use this phone's copy"). A revision is never below the
write's epoch milliseconds, so a file put back by a rollback cannot repeat a number a device
holds for other content.

Each device keeps its own storage as a cache and works on it when it is offline or the switch is
off; with the switch on and the server reachable, the server's copy is the copy of record.

## Why this runs inside OpenAlgo

The same exception, on the same conditions, as the watchlist store
(`sudarshan/watchlists/README.md`, "the watchlists exception"): a file of its own in `db/`, backed
up and rolled back with `openalgo.db` by the deploy; NullPool engines from
`database.engine_factory`; every operation in its own session; no thread, timer, scheduler,
executor or event loop (held by `tests/test_charts_exception.py`); correct under eventlet, gthread
and the dev server; the switch ships off. The bridge is `blueprints/sudarshan_charts.py`; the
migration is `upgrade/migrate_sudarshan_charts.py` (idempotent, `--status`), listed in
`upgrade/migrate_all.py` but not required, because the store makes or completes its file on first
use as the migration does. A service of its own would cost a port, a unit, a health block and a
bridge for three small request-driven routes.

## Tests

`tests/test_charts_store.py` (the store, the bounds, the compare-and-set under a barrier, the
descriptors), `tests/test_charts_bridge.py` (the checks in order, a family viewer and another
sign-in refused on every route, the switch, the envelope), `tests/test_charts_migration.py` (a
missing file, a populated copy forced back, a newer file) and `tests/test_charts_exception.py`.
