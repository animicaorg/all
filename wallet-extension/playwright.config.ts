import { defineConfig, devices } from '@playwright/test';

const isCI = !!process.env.CI;

export default defineConfig({
  testDir: 'test/e2e',
  timeout: 60_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,

  reporter: isCI
    ? [['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:4400',
    trace: isCI ? 'on-first-retry' : 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    // Headed makes extension/Dapp flows easier to debug locally; CI stays headless
    headless: isCI,
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
    // Uncomment if you want to try WebKit locally:
    // { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],

  // Serve the tiny demo dapp (test/e2e/dapp) without extra deps.
  // Cross-platform single-line static server using Node's http module.
  webServer: {
    command:
      'node -e "const http=require(\'http\'),fs=require(\'fs\'),path=require(\'path\');' +
      'const base=path.resolve(\'test/e2e/dapp\');' +
      'const srv=http.createServer((req,res)=>{' +
      ' const u=(req.url||\'/\').split(\'?\')[0];' +
      ' let p=path.join(base,u.endsWith(\'/\')?u+\'index.html\':u);' +
      ' fs.readFile(p,(e,d)=>{if(e){res.statusCode=404;res.end(\'not found\');}else{res.end(d);}});' +
      '}); srv.listen(4400);"',
    port: 4400,
    reuseExistingServer: true,
    timeout: 20_000,
  },
});
