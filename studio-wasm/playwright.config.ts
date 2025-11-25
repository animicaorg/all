import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E config for studio-wasm.
 *
 * We run a Vite dev server so that:
 *  - dynamic module imports work (ESM)
 *  - /examples and /vendor (Pyodide assets) are served directly
 *
 * Ensure Pyodide assets are present in ./vendor before running.
 * The repo's package.json typically wires a predev step:
 *   "predev": "node scripts/fetch_pyodide.mjs"
 */

export default defineConfig({
  testDir: 'test/e2e',
  // Give the browser + Pyodide a little breathing room.
  timeout: 120_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? 'github' : [['list']],
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  // Launch a Vite dev server; reuse it locally for faster cycles.
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    // Mobile viewports (optional; uncomment if desired)
    // { name: 'Mobile Chrome', use: { ...devices['Pixel 5'] } },
    // { name: 'Mobile Safari', use: { ...devices['iPhone 12'] } },
  ],
});
