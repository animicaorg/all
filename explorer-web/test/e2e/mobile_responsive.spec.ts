import { test, expect, devices } from '@playwright/test';

/**
 * Mobile responsiveness tests
 * Validates that the explorer-web app renders correctly on mobile devices
 * and doesn't have horizontal overflow issues.
 */

test.describe('Mobile Responsiveness', () => {
  test.describe('Small Phone (360px)', () => {
    test.use({ ...devices['Pixel 5'] }); // 393x851

    test('should not have horizontal scroll', async ({ page }) => {
      await page.goto('/');
      
      // Check body doesn't overflow
      const bodyScroll = await page.evaluate(() => {
        return {
          scrollWidth: document.body.scrollWidth,
          clientWidth: document.body.clientWidth,
          overflow: window.getComputedStyle(document.body).overflowX
        };
      });
      
      expect(bodyScroll.overflow).toBe('hidden');
      expect(bodyScroll.scrollWidth).toBeLessThanOrEqual(bodyScroll.clientWidth + 1); // Allow 1px tolerance
    });

    test('should display mobile navigation', async ({ page }) => {
      await page.goto('/');
      
      // Check if sidenav has mobile styles applied
      const sidenav = page.locator('.sidenav').first();
      await expect(sidenav).toBeVisible();
      
      // On mobile, sidenav should be horizontal
      const isHorizontal = await sidenav.evaluate((el) => {
        const styles = window.getComputedStyle(el);
        return styles.display === 'flex';
      });
      
      expect(isHorizontal).toBe(true);
    });

    test('should have touch-friendly navigation links', async ({ page }) => {
      await page.goto('/');
      
      // Check that navlinks have minimum 44px height
      const navlinks = page.locator('.navlink');
      const count = await navlinks.count();
      
      expect(count).toBeGreaterThan(0);
      
      for (let i = 0; i < Math.min(count, 3); i++) {
        const link = navlinks.nth(i);
        const box = await link.boundingBox();
        
        if (box) {
          expect(box.height).toBeGreaterThanOrEqual(44); // Touch target minimum
        }
      }
    });
  });

  test.describe('Tablet (768px)', () => {
    test.use({ 
      viewport: { width: 768, height: 1024 }
    });

    test('should not have horizontal scroll', async ({ page }) => {
      await page.goto('/');
      
      const bodyScroll = await page.evaluate(() => {
        return {
          scrollWidth: document.body.scrollWidth,
          clientWidth: document.body.clientWidth,
        };
      });
      
      expect(bodyScroll.scrollWidth).toBeLessThanOrEqual(bodyScroll.clientWidth + 1);
    });

    test('should render search bar properly', async ({ page }) => {
      await page.goto('/');
      
      // TopBar should be visible with search
      const searchInput = page.locator('input[type="search"]');
      await expect(searchInput).toBeVisible();
      
      // Search input should be large enough
      const box = await searchInput.boundingBox();
      expect(box?.height).toBeGreaterThanOrEqual(32);
    });
  });

  test.describe('Desktop (1024px+)', () => {
    test.use({
      viewport: { width: 1280, height: 720 }
    });

    test('should display sidebar navigation', async ({ page }) => {
      await page.goto('/');
      
      // Sidebar should be visible on desktop
      const sidenav = page.locator('.sidenav').first();
      await expect(sidenav).toBeVisible();
    });

    test('should not have horizontal scroll', async ({ page }) => {
      await page.goto('/');
      
      const bodyScroll = await page.evaluate(() => {
        return {
          scrollWidth: document.body.scrollWidth,
          clientWidth: document.body.clientWidth,
        };
      });
      
      expect(bodyScroll.scrollWidth).toBeLessThanOrEqual(bodyScroll.clientWidth + 1);
    });
  });

  test.describe('Responsive Components', () => {
    test('toasts should be visible and properly sized on mobile', async ({ page, viewport }) => {
      // Use mobile viewport
      await page.setViewportSize({ width: 360, height: 640 });
      await page.goto('/');
      
      // Trigger a toast by dispatching custom event
      await page.evaluate(() => {
        window.dispatchEvent(new CustomEvent('explorer:toast', { 
          detail: { 
            message: 'Test toast message for mobile',
            kind: 'info'
          }
        }));
      });
      
      // Wait for toast to appear
      await page.waitForTimeout(100);
      
      const toast = page.locator('.toast').first();
      await expect(toast).toBeVisible({ timeout: 2000 });
      
      // Toast should not overflow viewport
      const box = await toast.boundingBox();
      if (box) {
        expect(box.width).toBeLessThanOrEqual(viewport.width! - 16); // Account for margins
      }
    });
  });
});
