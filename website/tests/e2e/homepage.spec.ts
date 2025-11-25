import { test, expect } from '@playwright/test';

/**
 * Gate: homepage has working links to Studio / Explorer / Docs.
 * We assert both presence (visible link with expected label and href)
 * and navigation (click → lands on the correct page).
 */

const paths = {
  studio: '/studio',
  explorer: '/explorer',
  docs: '/docs',
};

test.describe('Homepage CTA links', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Basic sanity: hero should be present
    await expect(page.getByRole('heading', { name: /animica/i })).toBeVisible({ timeout: 10_000 });
  });

  test('Open Studio link works', async ({ page }) => {
    const link = page.getByRole('link', { name: /^open studio$/i });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', paths.studio);

    await test.step('navigate to /studio', async () => {
      await Promise.all([
        page.waitForURL((url) => url.pathname.endsWith(paths.studio)),
        link.click(),
      ]);
      // Check we actually landed and page renders expected content
      await expect(page.getByRole('heading', { name: /studio/i })).toBeVisible();
    });

    // Return home for the next test
    await page.goto('/');
  });

  test('Open Explorer link works', async ({ page }) => {
    const link = page.getByRole('link', { name: /^open explorer$/i });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', paths.explorer);

    await test.step('navigate to /explorer', async () => {
      await Promise.all([
        page.waitForURL((url) => url.pathname.endsWith(paths.explorer)),
        link.click(),
      ]);
      await expect(page.getByRole('heading', { name: /explorer/i })).toBeVisible();
    });

    await page.goto('/');
  });

  test('Read Docs link works', async ({ page }) => {
    const link = page.getByRole('link', { name: /^read docs$/i });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', paths.docs);

    await test.step('navigate to /docs (or docs splash/redirect)', async () => {
      await Promise.all([
        // Allow either /docs or a redirect that still includes /docs in path.
        page.waitForURL((url) => url.pathname.startsWith(paths.docs), { timeout: 15_000 }),
        link.click(),
      ]);
      // Be lenient about the exact heading; match "Docs" anywhere in h1/h2
      const heading = page.locator('h1,h2').filter({ hasText: /docs|documentation/i });
      await expect(heading.first()).toBeVisible();
    });
  });
});
