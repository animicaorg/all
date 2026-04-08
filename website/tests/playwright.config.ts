import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for the Animica website.
 *
 * Usage:
 *   # Start preview yourself (arbitrary port) and point tests at it:
 *   SITE_BASE_URL="http://127.0.0.1:4410" pnpm run test:e2e
 *
 *   # Or let Playwright start a preview on :4410 automatically:
 *   pnpm run build
 *   pnpm run test:e2e
 *
 * Notes:
 * - If SITE_BASE_URL is set, we assume an external preview is already running
 *   and we DO NOT start a webServer here.
 * - Otherwise, we start Astro preview on a dedicated port and use that base URL.
 */

const previewPort = 4410;
const baseURL = process.env.SITE_BASE_URL ?? `http://127.0.0.1:${previewPort}`;
const startServer = !process.env.SITE_BASE_URL;

export default defineConfig({
  testDir: './e2e',
  outputDir: './.artifacts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 60_000,

  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: process.env.CI ? 'retain-on-failure' : 'off',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },

  reporter: [
    ['list'],
    ['html', { outputFolder: './playwright-report', open: 'never' }],
  ],

  // Start Astro preview locally if SITE_BASE_URL is not provided.
  webServer: startServer
    ? {
        command: `pnpm exec astro preview --host 127.0.0.1 --port ${previewPort}`,
        url: baseURL,
        reuseExistingServer: false,
        timeout: 120_000,
        cwd: process.cwd().endsWith('/website') ? process.cwd() : `${process.cwd()}/website`,
      }
    : undefined,

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
    // Mobile view sanity (optional)
    {
      name: 'mobile-chromium',
      use: { ...devices['Pixel 7'] },
    },
  ],
});
