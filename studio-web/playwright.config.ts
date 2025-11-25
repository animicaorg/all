import { defineConfig, devices } from '@playwright/test';

const isCI = !!process.env.CI;
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';
const useLive = process.env.E2E_LIVE === '1';

// Only start a local dev server if we're not pointing at an explicit BASE_URL or running live.
const webServer = useLive || process.env.E2E_BASE_URL
  ? undefined
  : {
      command: 'npm run dev -- --port 5173 --strictPort',
      url: baseURL,
      reuseExistingServer: !isCI,
      timeout: 120_000,
      cwd: __dirname,
    };

export default defineConfig({
  testDir: 'test/e2e',
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: isCI ? '50%' : undefined,
  reporter: isCI
    ? [['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],

  timeout: 120_000,
  expect: { timeout: 20_000 },

  use: {
    baseURL,
    trace: isCI ? 'retain-on-failure' : 'on-first-retry',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ignoreHTTPSErrors: true,
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox',  use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit',   use: { ...devices['Desktop Safari'] } },
  ],

  // Start Vite dev server for local E2E runs unless using an explicit external BASE_URL.
  webServer,
});
