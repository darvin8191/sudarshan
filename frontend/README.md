# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

## The phone specs (SUDARSHAN)

`e2e/sd-*.spec.ts` open the phone and tablet screens in a real browser: the dashboard, the phone
chart, options, the TRINETRA view, the trade marks and charges line, and /trading on a tablet.
Each spec sets its own screens (360 x 800 upright, 800 x 360 on its side, the tablet sizes),
answers the requests the screen makes and plays the market-data stream from a synthetic tape.
The browser is started so that no host name resolves, and without Chromium's own calls to
Google. The specs have a configuration of their own, `playwright.sd.config.ts`; upstream's
`playwright.config.ts` is left as it is.

### On your own machine

```sh
npm ci
npx playwright install --only-shell chromium   # once: the build of the Playwright version package-lock.json holds
npx playwright test -c playwright.sd.config.ts                       # every spec, on the dev server
npx playwright test -c playwright.sd.config.ts e2e/sd-chart.spec.ts  # one of them
```

What CI runs, on the production build, leaving the tracked `dist/` alone (`CI=1` gives its skips
and its reports):

```sh
npx vite build --outDir /tmp/sd-dist
CI=1 SD_PREVIEW=/tmp/sd-dist npx playwright test -c playwright.sd.config.ts
```

The server is started on 127.0.0.1:5173 and stopped at the end. A server already on that port is
refused rather than used, since on a shared machine it can be another checkout's: pick another
port with `SD_PORT`, or name a server you started on purpose with `SD_BASE_URL`. Both servers pass
the OpenAlgo paths a spec does not answer on to port 5000, so run the specs with no OpenAlgo
listening there.

| variable | what it does |
|---|---|
| `SD_PREVIEW` | `1` serves the production build in `dist/` with `vite preview` (run `npm run build` first); a folder serves the build in that folder. CI sets `1`. |
| `SD_PORT` | the port the server is started on (5173) |
| `SD_BASE_URL` | uses a server that is already running, and starts none |
| `SD_CHROMIUM` | the path of a Chromium to start, where Playwright's own download is not installed |
| `SD_WORKERS` | how many tests run side by side (CI: 2) |
| `SD_COST_RUN=1` | runs the cost runs even where `CI` is set |
| `SD_COST_STUDIES`, `SD_WIDTH` | read by `sd-chart.spec.ts` and `sd-dashboard.spec.ts`; their headers say how |

The cost runs (the chart's frame timing, and the options screen's main-thread tasks: no long task
over 50 ms) are tripwires for the owner's phone, measured on a quiet machine and a production
build. Where `CI` is set they skip and say so; the options ones also skip on the dev server.

The dashboard's app bar checks "before the fonts arrive" refuse the app's own fonts, so they
measure the machine's `system-ui` face. CI gives it Roboto, as an Android phone has. Elsewhere the
result depends on that machine's fonts: with Ubuntu's DejaVu Sans, SUDARSHAN overruns its room by
under a pixel at 320 and 360 px, and those checks fail.

### In CI

The `phone-specs` job of `.github/workflows/sudarshan-ci.yml` runs on every run of that workflow:
`npm ci`, the headless Chromium of the Playwright version `package-lock.json` holds (with its
system libraries), `vite build`, then every spec against the build with `SD_PREVIEW=1`, two tests
at a time and no retry. A test that fails fails the job; a test that cannot run there skips with
its reason in the log. When a test fails the job keeps `playwright-report/` and `test-results/`
(the failed tests' traces, screenshots and the specs' own pictures) as the artifact
`phone-specs-report` for seven days: download it and open `playwright-report/index.html`, or a
trace with `npx playwright show-trace <trace.zip>`.

### Writing one

- Answer every request the screen makes, and fail the test on one no fixture answers, as
  `sd-trip-charges.spec.ts` does: a route added by a later change then fails the spec in CI
  instead of going unseen.
- Wait for the condition a step needs (`expect(...)`, `expect.poll(...)`), never only for a step
  of the fake clock or a fixed pause. Between steps the page's clock runs in real time, so a step
  gets the page somewhere sooner; it does not promise that the page is there.
- A sheet takes no tap for half a second after it opens and is marked `data-settling` until then:
  wait for the mark to go, as `settled()` in the chart and options specs does.
- Run the whole set three times in a row before you push.
