import { test, expect } from '@playwright/test';

/**
 * E2E: boot the dev preview, import the WASM simulator in-page,
 * compile the Counter example, and verify inc/get round-trip.
 *
 * The Playwright webServer is configured in playwright.config.ts to run `vite`.
 * This test assumes:
 *   - Pyodide assets are either fetched to /vendor or available via CDN per loader.
 *   - Examples are served at /examples/counter/{contract.py,manifest.json}.
 */

test('simulate Counter in real browser', async ({ page }) => {
  // Visit the dev preview root served by Vite.
  await page.goto('/');
  await page.waitForLoadState('domcontentloaded');

  // Evaluate in the page context to use Vite module resolution.
  const result = await page.evaluate(async () => {
    const [{ compileSource }, { simulateCall }, { createState }] = await Promise.all([
      import('/src/api/compiler'),
      import('/src/api/simulator'),
      import('/src/api/state'),
    ]);

    // Load example files via fetch (served by Vite).
    const [manifest, source] = await Promise.all([
      fetch('/examples/counter/manifest.json').then(r => {
        if (!r.ok) throw new Error('failed to load manifest.json: ' + r.status);
        return r.json();
      }),
      fetch('/examples/counter/contract.py').then(r => {
        if (!r.ok) throw new Error('failed to load contract.py: ' + r.status);
        return r.text();
      }),
    ]);

    const compiled = await compileSource({ manifest, source });
    const state = await createState();

    // Sanity: get() should be 0 initially.
    const get0 = await simulateCall({ compiled, manifest, entry: 'get', args: {}, state });
    if (!get0.ok) throw new Error('get() failed: ' + JSON.stringify(get0.error));

    // inc() should return 1 and likely emit an event.
    const inc = await simulateCall({ compiled, manifest, entry: 'inc', args: {}, state });
    if (!inc.ok) throw new Error('inc() failed: ' + JSON.stringify(inc.error));

    // get() again should be 1.
    const get1 = await simulateCall({ compiled, manifest, entry: 'get', args: {}, state });
    if (!get1.ok) throw new Error('second get() failed: ' + JSON.stringify(get1.error));

    return {
      get0: get0.returnValue,
      inc: inc.returnValue,
      get1: get1.returnValue,
      gasUsed: inc.gasUsed,
      events: inc.events ?? [],
    };
  });

  // Expectations in Node (test runner) context.
  expect(result).toBeTruthy();
  expect(result.get0).toBe(0);
  expect(result.inc).toBe(1);
  expect(result.get1).toBe(1);
  expect(typeof result.gasUsed).toBe('number');
  expect(result.gasUsed).toBeGreaterThan(0);
  expect(Array.isArray(result.events)).toBe(true);
  // If events exist, ensure they look event-like (name + args)
  if (result.events.length > 0) {
    const e = result.events[0];
    expect(typeof e.name).toBe('string');
    expect(e.args && typeof e.args).toBe('object');
  }
}, 120_000);
