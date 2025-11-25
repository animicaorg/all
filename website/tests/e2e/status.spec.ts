import { test, expect } from '@playwright/test';

/**
 * Status page E2E
 * Mocks the site API (/api/status.json) so we don't rely on a real RPC.
 * Gate: page shows a live head height (and basic metrics) derived from the mocked payload.
 */

const mockStatus = {
  ok: true,
  head: {
    height: 12345,
    hash: '0xabc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd',
  },
  tps: 12.34,
  peers: 42,
  latency_ms: 85,
  ws: { connected: true },
};

test.describe('Status page (mocked RPC)', () => {
  test('renders head height and metrics from /api/status.json', async ({ page }) => {
    // Intercept the website API the UI uses for live status
    await page.route('**/api/status.json**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockStatus),
      });
    });

    await page.goto('/status');

    // Height: prefer data-testid if available; otherwise fall back to text match
    const heightEl = page.locator('[data-testid="metric-height"]');
    const tpsEl = page.locator('[data-testid="metric-tps"]');
    const peersEl = page.locator('[data-testid="metric-peers"]');
    const badgeEl = page.locator('[data-testid="status-badge"]');

    // Be resilient: if data-testids are missing, try semantic fallbacks.
    const heightCandidate = heightEl.or(page.getByText(/height/i).locator('..'));
    const tpsCandidate = tpsEl.or(page.getByText(/tps/i).locator('..'));
    const peersCandidate = peersEl.or(page.getByText(/peers/i).locator('..'));
    const badgeCandidate = badgeEl.or(page.getByRole('status').first());

    // Assertions
    await expect(heightCandidate).toContainText(/12,345|12345/);
    await expect(tpsCandidate).toContainText(/12\.34|12/);
    await expect(peersCandidate).toContainText(/42/);

    // Badge should indicate healthy/online in some form; allow a few variants
    const badgeText = (await badgeCandidate.innerText()).toLowerCase();
    expect(
      badgeText.includes('healthy') ||
        badgeText.includes('online') ||
        badgeText.includes('ok') ||
        badgeText.includes('connected')
    ).toBeTruthy();
  });
});
