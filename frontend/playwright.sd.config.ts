import { defineConfig } from '@playwright/test'

/**
 * SUDARSHAN: the phone and tablet specs (`e2e/sd-*.spec.ts`) in headless Chromium. The
 * `phone-specs` job of sudarshan-ci.yml runs them with this configuration, and so does a run on
 * your own machine (README.md, "The phone specs"):
 *
 *   npx playwright test -c playwright.sd.config.ts
 *
 * Upstream's `playwright.config.ts` is left as it is: it runs every spec in five browsers, and
 * these specs are Chromium only (touch and CPU throttling go through its DevTools protocol).
 * Each spec sets its own screens (360 x 800 upright, 800 x 360 on its side, the tablet sizes);
 * nothing here changes them.
 *
 * Nothing reaches a server. Each spec answers its requests and plays the market-data socket
 * itself, and the browser is started so that no host name resolves: a request to another host
 * fails in the browser instead of leaving the machine.
 *
 * What the environment can change:
 *   SD_PREVIEW    `1`: the production build in `dist/`, served by `vite preview` (CI does this
 *                 after `vite build`). A folder: the build in that folder, made with
 *                 `npx vite build --outDir <folder>`, which leaves the tracked `dist/` alone.
 *                 Unset: the dev server.
 *   SD_PORT       the port the server is started on (5173)
 *   SD_BASE_URL   a server that is already running; none is started
 *   SD_CHROMIUM   the path of a Chromium to start, where Playwright's own is not installed
 *   SD_WORKERS    how many tests run side by side (CI: 2; elsewhere Playwright decides)
 */
const ci = !!process.env.CI
const port = Number(process.env.SD_PORT) || 5173
// 127.0.0.1, not localhost: the browser below resolves no name at all.
const baseURL = process.env.SD_BASE_URL || `http://127.0.0.1:${port}`
const preview = process.env.SD_PREVIEW
const server = preview
  ? `npx vite preview${preview === '1' ? '' : ` --outDir ${JSON.stringify(preview)}`}`
  : 'npx vite'

export default defineConfig({
  testDir: './e2e',
  testMatch: /sd-.*\.spec\.ts$/,
  outputDir: 'test-results',
  fullyParallel: true,
  forbidOnly: ci,
  // No retry: a test that passes on its second try is a race to fix, not a pass.
  retries: 0,
  workers: Number(process.env.SD_WORKERS) || (ci ? 2 : undefined),
  // CI uploads this folder only when a test failed, so it keeps only the failed tests' output.
  preserveOutput: ci ? 'failures-only' : 'always',
  reporter: ci
    ? [['github'], ['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // A request a service worker answers never reaches a spec's routes: none is let in.
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        launchOptions: {
          executablePath: process.env.SD_CHROMIUM || undefined,
          // Chromium calls Google's hosts on its own unless told not to.
          args: ['--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1'],
        },
      },
    },
  ],
  webServer: process.env.SD_BASE_URL
    ? undefined
    : {
        command: `${server} --host 127.0.0.1 --port ${port} --strictPort`,
        url: baseURL,
        // A server already on the port is refused, not used: on a shared machine it can be
        // another checkout's. SD_BASE_URL uses one on purpose.
        reuseExistingServer: false,
        timeout: 120_000,
      },
})
