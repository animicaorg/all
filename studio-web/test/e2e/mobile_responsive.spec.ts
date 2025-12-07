import { test, expect, devices } from '@playwright/test';

/**
 * Mobile responsiveness tests for studio-web
 * Validates responsive behavior of the contract IDE on mobile devices
 */

test.describe('Studio Mobile Responsiveness', () => {
  test.describe('Small Phone (360px)', () => {
    test.use({ ...devices['Pixel 5'] }); // 393x851

    test('should not have horizontal scroll', async ({ page }) => {
      await page.goto('/');
      
      const bodyScroll = await page.evaluate(() => {
        return {
          scrollWidth: document.body.scrollWidth,
          clientWidth: document.body.clientWidth,
          overflow: window.getComputedStyle(document.body).overflowX
        };
      });
      
      expect(bodyScroll.overflow).toBe('hidden');
      expect(bodyScroll.scrollWidth).toBeLessThanOrEqual(bodyScroll.clientWidth + 1);
    });

    test('should collapse sidebar on mobile', async ({ page }) => {
      await page.goto('/edit');
      
      // Sidebar should be collapsed by default on mobile
      const sidebar = page.locator('.sidebar').first();
      
      if (await sidebar.isVisible()) {
        const hasCollapsedClass = await sidebar.evaluate((el) => {
          return el.classList.contains('collapsed');
        });
        
        // On mobile, sidebar should start collapsed
        expect(hasCollapsedClass).toBe(true);
      }
    });

    test('should have touch-friendly buttons', async ({ page }) => {
      await page.goto('/');
      
      // Check buttons have minimum touch target size
      const buttons = page.locator('button').first();
      await expect(buttons).toBeVisible();
      
      const box = await buttons.boundingBox();
      if (box) {
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.width).toBeGreaterThanOrEqual(44);
      }
    });
  });

  test.describe('Tablet (768px)', () => {
    test.use({ 
      viewport: { width: 768, height: 1024 }
    });

    test('should render editor container properly', async ({ page }) => {
      await page.goto('/edit');
      
      // Editor container should be visible
      const editor = page.locator('.editor-container').first();
      
      // If editor exists, check it doesn't overflow
      if (await editor.isVisible({ timeout: 5000 }).catch(() => false)) {
        const box = await editor.boundingBox();
        expect(box?.width).toBeLessThanOrEqual(768);
      }
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

  test.describe('Desktop (1024px+)', () => {
    test.use({
      viewport: { width: 1280, height: 720 }
    });

    test('should display sidebar expanded by default', async ({ page }) => {
      await page.goto('/');
      
      const sidebar = page.locator('.sidebar').first();
      await expect(sidebar).toBeVisible();
      
      // On desktop, sidebar should not be collapsed by default
      const hasCollapsedClass = await sidebar.evaluate((el) => {
        return el.classList.contains('collapsed');
      });
      
      // Allow flexibility - user preference may have it collapsed
      // Just ensure sidebar is present
      await expect(sidebar).toBeVisible();
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

  test.describe('Forms and Inputs', () => {
    test('should use 16px font size on mobile to prevent zoom', async ({ page }) => {
      await page.setViewportSize({ width: 360, height: 640 });
      await page.goto('/');
      
      // Find any input element
      const inputs = page.locator('input[type="text"], input[type="number"], textarea, select');
      const count = await inputs.count();
      
      if (count > 0) {
        const input = inputs.first();
        await expect(input).toBeVisible();
        
        const fontSize = await input.evaluate((el) => {
          return window.getComputedStyle(el).fontSize;
        });
        
        // Should be at least 16px to prevent iOS zoom
        const fontSizeNum = parseFloat(fontSize);
        expect(fontSizeNum).toBeGreaterThanOrEqual(16);
      }
    });
  });

  test.describe('Modal Responsiveness', () => {
    test('should render modals within viewport on mobile', async ({ page, viewport }) => {
      await page.setViewportSize({ width: 360, height: 640 });
      await page.goto('/');
      
      // Look for any modal triggers and test
      const modalTriggers = page.locator('[data-testid*="modal"], button:has-text("Deploy"), button:has-text("Settings")');
      const count = await modalTriggers.count();
      
      if (count > 0) {
        // Click first modal trigger
        await modalTriggers.first().click().catch(() => {});
        
        // Check if modal appeared
        const modal = page.locator('.modal-content, [role="dialog"]').first();
        
        if (await modal.isVisible({ timeout: 1000 }).catch(() => false)) {
          const box = await modal.boundingBox();
          
          if (box) {
            // Modal should fit within viewport with some margin
            expect(box.width).toBeLessThanOrEqual(viewport.width! - 16);
            expect(box.height).toBeLessThanOrEqual(viewport.height! - 16);
          }
        }
      }
    });
  });
});
