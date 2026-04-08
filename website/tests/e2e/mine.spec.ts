import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

type MockResponse = {
  status: number;
  body: unknown;
};

const SITE_BASE_URL = process.env.SITE_BASE_URL ?? 'http://127.0.0.1:4410';

async function mockMiningApi(page: Page, responses: Record<string, MockResponse>) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const response = responses[url.pathname];

    if (!response) {
      await route.continue();
      return;
    }

    await route.fulfill({
      status: response.status,
      contentType: 'application/json',
      body: JSON.stringify(response.body),
    });
  });
}

test.describe('/mine', () => {
  test('renders live mining config and downloads when the mining API succeeds', async ({ page }) => {
    await mockMiningApi(page, {
      '/api/mining/config': {
        status: 200,
        body: {
          network: 'mainnet',
          profile: 'public',
          algorithm: 'sha3',
          device_type: 'cpu',
          stratum_host: 'pool.animica.org',
          stratum_port: 3333,
          stratum_scheme: 'stratum+tcp',
          stratum_url: 'stratum+tcp://pool.animica.org:3333',
          default_worker: 'office-cpu',
          default_threads: 6,
          payout_instructions: 'Use a mainnet wallet address.',
          worker_instructions: 'Name each worker after its host machine.',
          manual_commands: {
            windows: 'animica-miner.exe --config animica-miner.config.json',
          },
          status: {
            online: true,
            miners: 18,
            workers: 24,
            pool_hashrate: 1250000,
            height: 4242,
            latest_block: '0xfeedbeef',
          },
        },
      },
      '/api/mining/downloads': {
        status: 200,
        body: {
          items: [
            {
              platform: 'windows',
              label: 'Windows',
              filename: 'animica-miner-windows.zip',
              version: '1.2.3',
              launcher: '.bat launcher',
              sha256: 'deadbeef',
              size_bytes: 1024,
              url: '/api/mining/downloads/windows',
              notes: 'Recommended for Windows miners.',
            },
          ],
        },
      },
      '/api/mining/status': {
        status: 200,
        body: {
          online: true,
          miners: 18,
          workers: 24,
          pool_hashrate: 1250000,
          height: 4242,
          latest_block: '0xfeedbeef',
        },
      },
      '/api/pool/summary': {
        status: 200,
        body: {
          miners: 18,
          workers: 24,
          pool_hashrate: 1250000,
          height: 4242,
          latest_block: '0xfeedbeef',
        },
      },
    });

    await page.goto(`${SITE_BASE_URL}/mine/`);

    await expect(page.getByTestId('mine-status-badge')).toContainText('Pool online');
    await expect(page.locator('#stratum-url')).toContainText('stratum+tcp://pool.animica.org:3333');
    await expect(page.getByTestId('mine-active-miners')).toContainText('18');
    await expect(page.getByTestId('mine-pool-height')).toContainText('4,242');
    await page.getByRole('button', { name: 'Windows' }).click();
    await expect(page.locator('#command-output')).toContainText('animica-miner.exe --config animica-miner.config.json');
    await expect(page.locator('[data-download-link="windows"]')).toHaveAttribute(
      'href',
      /\/api\/mining\/downloads\/windows$/
    );
    await expect(page.getByTestId('mine-fallback')).toBeHidden();
  });

  test('keeps the page useful when downloads fail but config succeeds', async ({ page }) => {
    await mockMiningApi(page, {
      '/api/mining/config': {
        status: 200,
        body: {
          network: 'mainnet',
          profile: 'public',
          algorithm: 'sha3',
          device_type: 'cpu',
          stratum_host: 'pool.animica.org',
          stratum_port: 3333,
          stratum_scheme: 'stratum+tcp',
          stratum_url: 'stratum+tcp://pool.animica.org:3333',
          payout_instructions: 'Use a mainnet wallet address.',
          worker_instructions: 'Name each worker after its host machine.',
          status: {
            online: true,
            miners: 4,
            workers: 6,
            pool_hashrate: 98000,
            height: 1000,
          },
        },
      },
      '/api/mining/downloads': {
        status: 503,
        body: { error: 'downloads unavailable' },
      },
      '/api/mining/status': {
        status: 200,
        body: {
          online: true,
          miners: 4,
          workers: 6,
          pool_hashrate: 98000,
          height: 1000,
        },
      },
      '/api/pool/summary': {
        status: 200,
        body: {
          miners: 4,
          workers: 6,
          pool_hashrate: 98000,
          height: 1000,
        },
      },
    });

    await page.goto(`${SITE_BASE_URL}/mine/`);

    await expect(page.getByTestId('mine-status-badge')).toContainText('Pool online');
    await expect(page.locator('#stratum-url')).toContainText('stratum+tcp://pool.animica.org:3333');
    await expect(page.getByTestId('mine-fallback')).toContainText('download metadata is temporarily unavailable');
  });

  test('shows a safe fallback state when every mining endpoint fails', async ({ page }) => {
    await mockMiningApi(page, {
      '/api/mining/config': {
        status: 503,
        body: { error: 'offline' },
      },
      '/api/mining/downloads': {
        status: 503,
        body: { error: 'offline' },
      },
      '/api/mining/status': {
        status: 503,
        body: { error: 'offline' },
      },
      '/api/pool/summary': {
        status: 503,
        body: { error: 'offline' },
      },
    });

    await page.goto(`${SITE_BASE_URL}/mine/`);

    await expect(page.getByTestId('mine-status-badge')).toContainText('Live status unavailable');
    await expect(page.getByTestId('mine-fallback')).toContainText('Live mining data is temporarily unavailable');
    await expect(page.locator('#command-output')).toContainText('Live mining commands are unavailable');
  });
});
