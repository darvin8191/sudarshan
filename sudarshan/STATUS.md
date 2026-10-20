# सुदर्शन / SUDARSHAN — feature status

One row per feature id from the (private) feature list. Status: `todo` → `partial` → `done`
(`done` = merged, released, and checked on the server). Keep rows sorted by id.

| id | feature | status | release | PR / notes |
|---|---|---|---|---|
| M0.1 | Fork, CI (`SUDARSHAN CI`), release build + server auto-deploy | partial | — | branch `sudarshan` created from OpenAlgo 2.0.2.8 (`9d55811e`) |
| M18.1 | Feed service `sudarshan-feed` (= M13.1): one Upstox V3 socket, ZMQ 5581 + HTTP 5580 fan-out, recorder/replayer; with M18.8 snapshot store | partial | — | branch `feat/feed`: built + tested offline (fake Upstox); live test pending (Wed 21 Oct: U7 cap, U9 drops) |
| M1.6 | Light-only lock: store stays light, no theme switch anywhere, toasts light | partial | — | branch `feat/brand`; awaiting review and release |
