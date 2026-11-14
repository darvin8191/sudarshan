# सुदर्शन / SUDARSHAN: the TRINETRA view (`sudarshan-signals`, M13)

TRINETRA is the owner's other trading system on the same server. SUDARSHAN shows the readings
TRINETRA chooses to publish, beside the owner's own screens, and nothing else of it.

**The hard rule.** SUDARSHAN never opens a TRINETRA file, port, database, unit or log, never
restarts or signals it, and never asks it anything. TRINETRA reaches SUDARSHAN only by dropping a
JSON document into a folder SUDARSHAN owns (the letterbox). If TRINETRA publishes nothing,
SUDARSHAN shows nothing and says so.

`contract.py` is the one contract; `frontend/src/sudarshan/signals/types.ts` is generated from it
(`python -m sudarshan.signals.gentypes`, and `--check` in a test). This page says the same in
prose. The contract is frozen: a later change is additive only (a new optional field, op or word
key) and is named in the report that makes it.

```
TRINETRA (its own account) --file--> /var/spool/sudarshan-signals/in/
   sudarshan-signals (User=sudarshan, 127.0.0.1:5588, off in code)
     inbox.py     scan once a second, owner check, read, remove
     contract.py  one document -> a snapshot or a refusal (pure)
     diff.py      previous readings + new snapshot -> changes (pure); words.py the sentences
     store.py     /var/lib/sudarshan-signals/signals.db
     api.py       sealed ops; GET /health
     (push.py  ---sealed---> OpenAlgo /sudarshan/signals/internal/push -> owner's screens: B2)
     (relay.py ---sealed---> sudarshan-alerts 5582 signals_relay -> owner's Telegram: AL2)
   OpenAlgo bridge /sudarshan/signals/api/*  ---sealed---> 5588  (the page, the assistant: B1)
   sudarshan-journal ---sealed, reads only---> 5588  state_at(ts), state  (J)
```

## 1. The letterbox (for TRINETRA's operators)

This section can be handed to TRINETRA's operators as it stands.

- **What we ask.** One function in TRINETRA, called wherever a module's state is computed or an
  alert is raised, writes one small JSON file into `/var/spool/sudarshan-signals/in/`. Nothing
  else: no socket, no SUDARSHAN code, no secret. If the folder is missing or full the function
  gives up silently; TRINETRA must never fail, wait or log loudly because of it. Keep it behind a
  flag of your own, off until you choose.
- **The folder.** Made once by the owner with `sudo sudarshan/ops/signals/bootstrap.sh --publisher
  <account>`: owner `sudarshan`, mode `1733` (anyone may add a file, only its writer or
  `sudarshan` may remove it, nobody but `sudarshan` may list it). TRINETRA's account needs no new
  group and its unit no change, unless the unit confines its own writes (`ProtectSystem=strict` or
  `ReadWritePaths=`), in which case add this one path.
- **Who is believed.** The set-up writes the numeric id of the one account that may publish to
  `/etc/sudarshan/signals.env` (`SUDARSHAN_SIGNALS_PUBLISHER_UID=<number>`). A file is read only if
  its owner is that account, it is a regular file with one link and no execute, group-write,
  other-write, set-id or sticky bit, and it is at most 16 KiB. Any other file is removed and
  counted. The kernel, not a shared secret, says who wrote a document.
- **How to write a file.** Write to a temporary name beginning with a dot in the same folder, mode
  0644, then rename it to `<at>-<seq>.json` (13 digits, a dash, 1 to 9 digits). Dot names are
  never read. SUDARSHAN removes a file once it has stored it; a file older than 24 hours that
  nobody collected may be removed by its writer.
- **When to write.** Whenever a published state changes, whenever an alert is raised, and
  otherwise before the `next_by` of the last document (a heartbeat): suggested five minutes ahead
  from 08:30 to 16:00 IST on trading days, one hour ahead at other times. At a planned stop, a last
  document with `"stopping": true`.
- **Never in a document:** a credential, token, account id, position, order, P&L or personal
  detail. The letterbox is for market readings only.

```python
# Reference only, standard library, for TRINETRA's operators to adapt.
def publish(doc: dict, folder="/var/spool/sudarshan-signals/in") -> None:
    try:
        body = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > 16384:
            return
        name = f"{doc['at']}-{doc['seq']}.json"
        tmp = os.path.join(folder, f".{name}.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, body)
        finally:
            os.close(fd)
        os.replace(tmp, os.path.join(folder, name))
    except Exception:
        pass  # never TRINETRA's problem
```

## 2. The document, version 1

One JSON object, UTF-8, at most 16 KiB. Every document is a whole snapshot: SUDARSHAN works out
what changed. Lengths are Unicode code points, counted after control characters are taken out
(tabs and line breaks become a space in one-line fields; `why` and an alert's `text` keep line
breaks). Bidirectional overrides are taken out; the joiners Indian scripts need are kept.

| key | rule | if it is broken |
|---|---|---|
| `v` | `1` | a higher whole number: the file is left in the letterbox unread ("newer format", told once a day); anything else: refused |
| `run` | text, 1 to 40, changes when TRINETRA starts again | refused |
| `seq` | whole number 0 to 999,999,999, rising by 1 within a `run` | a gap is counted ("2 documents ... did not arrive today"); a repeat or a lower number in the same run is dropped, not counted as refused |
| `at` | epoch ms, 13 digits, TRINETRA's clock; the file's name carries the same `at` and `seq` | refused |
| `next_by` | epoch ms, after `at`, at most 6 h after it | refused |
| `stopping` | `true` or `false`, optional | refused if not a boolean |
| `signals` | a list of at most 16 readings, one per (`module`, `instrument`) | refused |
| `module` | `regime`, `gap_bias`, `swing_eod`, `swing_intraday` (DRISHTI, PRABHAT, SANDHI end-of-day, SANDHI intraday); any other matching `[a-z][a-z0-9_]{0,39}` is kept and shown under "Other" | refused if malformed |
| `instrument` | `NIFTY` or `BANKNIFTY` | that reading is skipped and the rest read |
| `state` | `[A-Z0-9_]{1,40}`, TRINETRA's own name (no list is kept here) | refused |
| `label` | text, 1 to 40, shown as written | refused |
| `bias` | `bull`, `bear` or `neutral`: the only field that picks a colour, always beside the label and an arrow | refused |
| `score` | -1 to 1, optional (absent or null: not published) | refused |
| `confidence` | 0 to 1, optional; a swing module's probability goes here | refused |
| `since`, `as_of`, `valid_until` | epoch ms, 13 digits; `as_of` required, `since` and `valid_until` may be null or absent | refused |
| `horizon` | `open`, `intraday` or `swing` | refused |
| `quality` | `ok`, `degraded` or `no_data` (TRINETRA's word on its inputs) | refused |
| `levels` | optional object of prices `trigger`, `invalid`, `target`, each above 0 and under 10,000,000, or null | refused |
| `inputs` | optional list of at most 8 `{name, value}`, text of 1 to 60 and 1 to 40 | refused |
| `why` | optional object; `en`, `hi` (Hinglish), `gu`, each text of at most 600, any may be missing | refused |
| `alerts` | optional list of at most 4: `id` (`[A-Za-z0-9._-]{1,64}`, unique for ever), `module`, `instrument` (or null), `level` (`info`, `watch`, `act`), `title` (1 to 80), `text` (0 to 700), `lang` (`en`, `hi`, `gu` or null) | refused; an id seen before is ignored |

Unknown keys are ignored at every level, so TRINETRA may add fields without a SUDARSHAN release.
A number must be a JSON number (never `true`), finite; a key may appear once in an object. Every
string is data: SUDARSHAN never renders it as markup, never sends it to Telegram with a parse
mode, and hands it to the assistant inside the envelope that marks it as untrusted text.

## 3. How SUDARSHAN reads the letterbox

- Once a second, `os.scandir` on the folder (a mailbox, not market data: the "no timed polling"
  rule does not apply). At most 64 files a scan, oldest name first.
- The folder must be a real folder owned by `sudarshan` with mode 1733; otherwise nothing is read
  and every screen says `letterbox_wrong`.
- A name that is not `<13 digits>-<1 to 9 digits>.json` is removed and counted. A dot name of the
  publisher is a file being written and is left alone; a dot name of anyone else is removed.
- Each file is opened without following links and without blocking, then checked on the open
  file itself (owner, regular, one link, mode, size). A file failing a check is removed and
  counted; nothing from it is read, parsed or kept.
- A file read whole is parsed as JSON with no code run and no module imported, and checked by
  `contract.read_document`. Accepted, repeated, older and refused files are removed once the
  verdict is stored; a newer-format file is left for a later release.
- More than 500 files waiting: the oldest beyond 500 are removed, said once.

## 4. Freshness, changes and time

- **Fresh** while the server's clock is before the last accepted document's `next_by` plus 60 s.
  A document is "clock differs" when its `at` is more than 120 s ahead of the time the file was
  written (the kernel's time on the file, the server's clock) or more than 24 h behind it. Its own
  times are then not used: it is shown at the time it was written and is fresh until that time
  plus (`next_by` - `at`) plus 60 s, and the note `clock` says by how much the clocks differ.
- **Published time** shown everywhere is the accepted document's `at` (the time it was written,
  when the clocks differ). The age is the server's clock less that time.
- **Colour.** Past 30 minutes of age no colour of bias is shown (a stale chip keeps its stale
  style: grey edge, clock icon). Never "Real-time" and never "Live" for a reading.
- **A change** is, for one (`module`, `instrument`): a first reading (none before, or the one
  before had lapsed or was not in the last document); another `state`; the same state with
  another `bias`; `quality` going to `no_data`; `valid_until` passing (`lapsed`, once, by the
  clock too). A move of `score` or `confidence` alone updates the current reading and the screens,
  not the timeline. Changes of every kind but `lapsed` go to Telegram when the owner's relay is on;
  alerts of level `watch` and `act` go too, `info` stays in the app.
- **Times in words** are IST: HH:MM:SS in the fresh header, HH:MM elsewhere, with "14 Nov " in
  front when it is not today's. An age or a difference of clocks is "under a minute", "1 minute",
  "N minutes" under 120 minutes, "N hours" under 48 hours, else "N days" (whole units, rounded
  down).
- **Language.** The owner picks English, Hinglish or Gujarati (`en`, `hi`, `gu`; default `hi`,
  owner decision 16). A reading without it shows the first of `en`, `hi`, `gu` TRINETRA sent, with
  `why_fallback` ("TRINETRA did not write this in Gujarati; shown in English.").

## 5. The words

Every sentence is in `contract.WORDS` (and `WORDS` in `types.ts`): templates with `{time}`,
`{age}`, `{difference}`, `{count}`, `{wanted}`, `{shown}`. `words.py` fills them; the page uses
the same keys. The header line, by `freshness.status`:

| status | words |
|---|---|
| `fresh` | "TRINETRA · updated 10:42:05" |
| `stale` | "TRINETRA has not published since 10:42. This is its last reading, 14 minutes old." |
| `stopped` | "TRINETRA was stopped by its operators at 16:40." |
| `never` | "TRINETRA is not connected yet. Publishing has not been switched on in TRINETRA." |

Notes under it (`notes`, on `/trinetra`): `refused` ("3 files from TRINETRA could not be read
today. Its last good reading is shown.", or "None of its readings can be shown yet." when nothing
was ever accepted), `newer_format`, `gaps` ("2 documents from TRINETRA did not arrive today."),
`clock` ("TRINETRA's clock and the server's differ by 4 minutes; its times may be off."). On a
card: `degraded`, `no_data`, `lapsed` ("Lapsed 09:15"), `why_fallback`, `why_none`. The view
itself: `off`, `not_set_up`, `letterbox_wrong`, `store_newer`, `store_failed`, and
`not_answering` from the bridge.

## 6. The service

- **Switch** `SUDARSHAN_SIGNALS` in OpenAlgo's `.env` (the release ships it `off`; off in code).
  Off, the service runs, reads nothing and answers every op `{"enabled": false, "message":
  WORDS["off"]}`; on but with no `/etc/sudarshan/signals.env`, the same with `not_set_up`; its
  `/health` says which.
- **Port** 127.0.0.1:5588, one HTTP listener: `GET /health` and `POST /v1/<op>`. A taken port is
  said in words and tried again every minute; the service never takes another port.
- **Store** `signals.db` in `/var/lib/sudarshan-signals` (folder 750, file 640), migrations at
  start, `python -m sudarshan.signals.migrate --status`. Documents 30 days; the timeline, alerts
  and marks for ever (a few rows a day).
- **`GET /health`** (open on loopback, never TRINETRA's text): `service`, `release`, `version`,
  `state`, `signals` (`on`, `off`, `not_set_up`), `letterbox` (`ok`, `missing`, `wrong`,
  `unknown`), `heard_ms`, `next_by`, `fresh`, `refused_today`, `gaps_today`.

## 7. The sealed local API

Each `POST /v1/<op>` body is one Fernet token (`sudarshan.autologin.handoff.seal`): `{"op",
"nonce", "principal", "args": {...}}`, valid 60 s, nonce once, the op in the path the op inside.
Which purpose a seal belongs to is which key opens it.

| purpose | key `info` | principal | at most | ops |
|---|---|---|---|---|
| api | `sudarshan/signals/api/v1` | `owner` | 16 KiB | `state`, `timeline`, `change_get`, `performance`, `settings_get`, `settings_set` |
| journal | `sudarshan/signals/journal/v1` | `sudarshan-journal` | 4 KiB | `state_at`, `state` |
| push | `sudarshan/signals/push/v1` | `sudarshan-signals` | 32 KiB | `push` (opened by OpenAlgo, section 9) |
| relay | `sudarshan/alerts/signals/v1` | `sudarshan-signals` | 16 KiB | `signals_relay` (opened by sudarshan-alerts, section 10) |

The service's answer: `{"status": "success", "data": <answer>}`, or `{"status": "error", "code",
"message", "field"?}` with the message in the owner's words. While off or not set up, every op's
data is the `Off` record `{"enabled": false, "message": ...}`.

| op | request (`args`) | answer |
|---|---|---|
| `state` | none | `State` |
| `timeline` | `module?`, `instrument?`, `before?` (a change id; older rows), `after_ms?`, `limit?` (1 to 200, default 50) | `TimelinePage` (newest first; `next_before` for the next page, null at the end) |
| `change_get` | `id` | `Change` |
| `performance` | `module?` (every module when absent) | `Performance` |
| `settings_get` | none | `Settings` |
| `settings_set` | `version`, `lang?`, `relay?`, `night?` | `Settings` (`version_conflict` when another screen saved first) |
| `state_at` | `ts` (epoch ms) | `StateAt`: the readings in effect then, only if a fresh document stood then |

The shapes are `RECORDS` in `contract.py` (interfaces of the same names in `types.ts`).

## 8. OpenAlgo's bridge for the page (B1)

`blueprints/sudarshan_signals.py`, built like the journal's bridge. Routes, all under
`/sudarshan/signals/api` (the `OPS` table: route, method, class):

| route | op | class |
|---|---|---|
| `GET /state` | `state` | read |
| `GET /timeline?module=&instrument=&before=&after_ms=&limit=` | `timeline` | read |
| `GET /changes/<id>` | `change_get` | read |
| `GET /performance?module=` | `performance` | read |
| `GET /settings` | `settings_get` | read |
| `PUT /settings` (JSON body) | `settings_set` | write |

Each route checks, in order: an OpenAlgo session (401), the owner himself (403 for any other
session, reading included), CSRF on the write, and a limiter per class (reads 120 a minute,
writes 30). Then who listens on 5588 (nothing goes to a port another user's program holds), one
sealed op (3 s for a read, 6 s for the write, the shared httpx client, no redirect, a write never
retried). No thread, scheduler or event loop is added to OpenAlgo.

The page's envelope (it reads the code, never the HTTP status):

| case | body | HTTP |
|---|---|---|
| success | `{"status": "success", "enabled": true, "data": <answer>}` | 200 |
| switched off or not set up | `{"status": "success", "enabled": false, "data": null, "message": <the service's sentence>}` | 200 |
| refused or failed | `{"status": "error", "error": {"code": <code>, "message": <sentence>, "field"?: <name>}}` | by code |

| code | HTTP | when | words |
|---|---|---|---|
| `unauthorized` | 401 | no OpenAlgo session (signed out) | `unauthorized` |
| `forbidden` | 403 | a session that is not the owner's | `forbidden` |
| `rate_limited` | 429 | past the class's limit | `rate_limited` |
| `bad_request` | 400 | a query or body that cannot be read; a bad module, instrument or language (`field` named) | `bad_request`, `bad_module`, `bad_instrument`, `bad_lang`, `too_big` |
| `not_found` | 404 | no change of that id | `not_found` |
| `version_conflict` | 409 | settings saved meanwhile on another screen | `version_conflict` |
| `not_ready` | 501 | an op this service does not have yet | `not_ready` |
| `unavailable` | 503 | the service does not answer, another program holds its port, or the seal was refused | `not_answering`, `held`, `sealed`, `failed`, `busy` |
| `not_confirmed` | 504 | the settings were sent and no answer came | `not_confirmed` |

When the service does not answer, the bridge reads its `/health` once: `signals` `off` gives the
off envelope with `off`, `not_set_up` with `not_set_up`; otherwise `unavailable` with
`not_answering` (a sent write: `not_confirmed`). The bridge never reads a second switch.

## 9. The push to the owner's screens (B2)

- The service calls `POST http://127.0.0.1:5000/sudarshan/signals/internal/push`, the body one
  token sealed for op `push` under `sudarshan/signals/push/v1`, principal `sudarshan-signals`, at
  most 32 KiB, with `items` in the seal: `{"op": "push", "nonce", "principal", "items":
  [{"event": "sudarshan_signals_update", "data": SignalsUpdate}]}`.
- OpenAlgo's route: no session and no CSRF (the seal authenticates it); 404 when nginx forwarded
  it (`X-Forwarded-For`, `X-Real-IP`, `Forwarded`, `X-Forwarded-Proto`, `X-Forwarded-Host`), 411
  without a length, 413 past 32 KiB, 403 a seal it cannot open, 503 when its nonce book is full,
  400 a bad push. It emits each item whose event is in `PUSH_EVENTS` to the room
  `sudarshan:signals:owner` only, inside the request (`extensions.emit_from_any_thread`), and
  answers `{"status": "success", "screens": <sockets in the room>}`. Never retried by the service.
- Every open app of the owner emits `sudarshan_signals_join` after each connect; OpenAlgo joins it
  to the room only when its session is the owner's (SIO1) and answers `{"ok": true}`, else
  `{"ok": false, "message": WORDS["forbidden"]}`.
- `SignalsUpdate` is `{"cause": "document" | "lapse" | "settings", "state": State | null}`: one
  push after each accepted document that changed the current readings, each lapse and each
  settings change. `state` is the `state` answer, or null when it would not fit in 32 KiB: the
  page then reads `GET /state`. The page reads `state` once at mount and after every reconnect.

## 10. The relay to the owner's Telegram (AL2)

- The service calls `POST http://127.0.0.1:5582/v1/signals_relay` (the alerts' one listener), the
  body one token sealed for op `signals_relay` under `sudarshan/alerts/signals/v1`, principal
  `sudarshan-signals`, at most 16 KiB, with `args` `RelayRequest` = `{"changes": RelayChange[],
  "alerts": RelayAlert[]}`, at most 8 items in one call (the sender splits).
- `RelayChange`: `id` ("c-<change id>"), `at_ms`, `module`, `module_name`, `instrument`, `kind`,
  `from_label`, `to_label`, `bias`, `confidence`, `why` (TRINETRA's sentence in the owner's
  language, plain text, cut to 300 characters), `why_lang`, `night` (may wait at night, the
  owner's setting). `RelayAlert`: `id` ("a-<TRINETRA's alert id>"), `at_ms`, `module`,
  `module_name`, `instrument`, `level` (`watch` or `act` only), `title`, `text` (cut to 300),
  `lang`, `night`.
- The desk claims each `id` in `alerts.db` before sending (once only, across restarts), makes the
  words (one message per change, merged over 5 s, TRINETRA's sentence quoted as plain text) and
  answers `{"status": "success", "data": {"taken": [ids], "known": [ids]}}` (`known`: claimed
  before). The service marks an item `handed` on that answer, retries a pending one while it is
  younger than 30 minutes, and then marks it `expired`. Changes of kind `lapsed`, `info` alerts and
  everything while the owner's relay is off are `skipped`, never sent. This service never holds
  the owner's API key; it hands the alerts a change, not a message.
- The seam: `service.HAND_ON` (`@hand_on("name")`) receives each stored batch of changes and
  alerts; `relay.py` attaches there with one marked line, and its retry loop with `@job`.

## 11. The journal's purpose (J)

`state_at(ts)` answers `StateAt`: `fresh` true only when an accepted document stood at `ts` (its
fresh window covered `ts`), with the readings then in effect (`module`, `instrument`, `state`,
`label`, `bias`, `confidence`, `since_ms`, `lapsed`); otherwise `fresh` false and no readings, which
the journal stores as "not known", never a guess. `state` is the page's answer.

## 12. Notices on Telegram (T9)

The service records notices in `/var/lib/sudarshan-signals/events.jsonl` (`ops/events.py`, src
`signals`); `sudarshan-alerts`' notice desk words and sends them once each, in the notices lane:

| kind | when | words | in market hours |
|---|---|---|---|
| `signals_quiet` | a trading day, nothing since 08:30 by 09:20 | `not_today` | at once |
| `signals_stale` | stale for 10 minutes inside market hours | `stale_notice` | at once |
| `signals_back` | publishing again after `signals_stale` | `back_notice` | at once |
| `signals_format` | a newer format, once a day | `newer_format` | after the close |

## 13. Files

| what | where |
|---|---|
| letterbox | `/var/spool/sudarshan-signals/in` (1733, `sudarshan`), remade by the set-up after a rebuild, never backed up |
| set-up file | `/etc/sudarshan/signals.env` (root:sudarshan 640) |
| store, notices | `/var/lib/sudarshan-signals/` (750): `signals.db` (640), `events.jsonl` |
| unit | `sudarshan/ops/systemd/sudarshan-signals.service` |
| set-up and its check | `sudarshan/ops/signals/bootstrap.sh`, `python -m sudarshan.signals.check` |
