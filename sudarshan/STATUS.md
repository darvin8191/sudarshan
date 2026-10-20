# सुदर्शन / SUDARSHAN — feature status

One row per feature id from the (private) feature list. Status: `todo` → `partial` → `done`
(`done` = merged, released, and checked on the server). Keep rows sorted by id.

| id | feature | status | release | PR / notes |
|---|---|---|---|---|
| M0.1 | Fork, CI (`SUDARSHAN CI`), release build + server auto-deploy | partial | — | branch `sudarshan` created from OpenAlgo 2.0.2.8 (`9d55811e`) |
| M1.1 | Bilingual wordmark: navbar lockup सुदर्शन / SUDARSHAN (both scripts at every width), stacked lockup on login and on the broker connect screen; the setup page welcomes to सुदर्शन · SUDARSHAN; the dashboard's Quick Access says "Symbols" and has no upstream course link | partial | — | branch `feat/brand`; awaiting review and release |
| M1.2 | `<title>`, page titles, meta description, og:title / og:description | partial | — | branch `feat/brand`; awaiting review and release |
| M1.3 | Icons: सु monogram (Tiro Devanagari Sanskrit drawn as paths) in white on an indigo tile with a thin saffron ring, inside the maskable safe zone; `favicon.ico` 16/32/48 (16 and 32 drop the ring for a larger, bolder glyph), `apple-touch-icon.png` 180, `android-chrome-192x192.png` and `logo.png` 192. Source `frontend/public/images/sudarshan-icon.svg`, rendered by `frontend/scripts/render-sudarshan-icons.mjs` | partial | — | branch `feat/brand`; no 512 px icon or web manifest yet (nothing references one) |
| M1.4 | Footer: सुदर्शन / SUDARSHAN, About link, "Built on OpenAlgo (AGPL-3.0)" attribution; upstream social links removed | partial | — | branch `feat/brand`; awaiting review and release |
| M1.5 | Chart corner mark: vector सुदर्शन wordmark, SUDARSHAN on hover, no outbound link (`/trading` terminal) | partial | — | branch `feat/brand`; awaiting review and release |
| M1.6 | Light-only lock: store stays light, no theme switch anywhere, toasts light | partial | — | branch `feat/brand`; awaiting review and release |
| M1.7 | Analyze mode in light form: violet accent, ANALYZE / સેન્ડબોક્સ banner (tint, 1px violet top border, icon) in both shells, LIVE/ANALYZE chips, chart stays light | partial | — | branch `feat/brand`; awaiting review and release |
| M1.8 | Design tokens: shadcn + trading tokens mapped to the spec, new `--up/--down/--brand/--family/--sandbox/--warning-text/--info/--up-cvd/--down-cvd` (`frontend/src/sudarshan/theme.css`) | partial | — | branch `feat/brand`; awaiting review and release |
| M1.9 | `color-scheme: only light`, `<meta name="color-scheme" content="light">` before CSS, `theme-color #F8F7F3` | partial | — | branch `feat/brand`; awaiting review and release |
| M1.10 | Self-hosted fonts (WOFF2, same origin, unicode-range): Inter latin 400-700 (tabular figures), Noto Sans Devanagari / Gujarati 400-700, Tiro Devanagari Sanskrit 400 for the wordmark; OFL text at `/docs/licenses/fonts-OFL.txt` | partial | — | branch `feat/brand`; awaiting review and release |
| M1.12 | One brand accent: upstream accent themes collapse to indigo, picker removed | partial | — | branch `feat/brand`; awaiting review and release |
| M1.13 | Colour-blind mode: `<html data-cvd="1">` blue/orange up/down, Profile > Display switch (per device) | partial | — | branch `feat/brand`; awaiting review and release |
| M1.16 | About page `/about` (route, Flask known path, profile menu): release, commit and source link from the release build, OpenAlgo version, licences (AGPL-3.0, Apache-2.0, OFL, ISC) | partial | — | branch `feat/brand`; awaiting review and release |
| M1.21 | Motion: shell has no tick flash or pulsing dot; Analyze banner fades in 160 ms; `prefers-reduced-motion` stops all animation and transition (spinners keep turning) | partial | — | branch `feat/brand`; pages outside the shell still flash on ticks (OptionChain, strategy builder P&L, agent live cards) |
| M18.1 | Feed service `sudarshan-feed` (= M13.1): one Upstox V3 socket, ZMQ 5581 + HTTP 5580 fan-out, recorder/replayer; with M18.8 snapshot store | partial | — | branch `feat/feed`: built + tested offline (fake Upstox); live test pending (Wed 21 Oct: U7 cap, U9 drops) |
