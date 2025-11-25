import { test, expect } from '@playwright/test'

/**
 * This gate test verifies that:
 *  1) The app attempts to open a WebSocket connection for live data.
 *  2) Incoming `newHeads` notifications update the live dashboard charts (Γ / Fairness / Mix).
 *
 * We inject a lightweight WebSocket shim before any app code runs. The shim:
 *  - Mimics a JSON-RPC subscription handshake (eth_subscribe/newHeads-style).
 *  - Emits a short sequence of synthetic heads with Γ/fairness/proof-mix fields.
 *  - Exposes helpers on window.__WS_TEST__ for the test to drive additional updates.
 *
 * The test then asserts that:
 *  - The app shows a connected/streaming state.
 *  - The Gamma, Fairness, and Proof-Mix charts render and reflect additional datapoints.
 *
 * Notes:
 *  - Selectors use tolerant fallbacks to accommodate minor differences in component test-ids.
 *  - This does not hit a real node; it validates the end-to-end UI reaction to live data frames.
 */

const HEADS_TO_PUSH = 6;

// Tolerant query helpers (try a list of selectors until one matches)
async function getFirstLocator(page, selectors: string[]) {
  for (const sel of selectors) {
    const loc = page.locator(sel);
    if (await loc.count().catch(() => 0)) {
      return loc.first();
    }
  }
  throw new Error(`None of selectors matched: ${selectors.join(', ')}`);
}

async function countChartDrawnNodes(chartRoot: import('@playwright/test').Locator) {
  // Count a few typical SVG/canvas children to detect updates
  const svgPaths = await chartRoot.locator('svg path').count().catch(() => 0);
  const svgRects = await chartRoot.locator('svg rect').count().catch(() => 0);
  const svgCircles = await chartRoot.locator('svg circle').count().catch(() => 0);
  const canvasNodes = await chartRoot.locator('canvas').count().catch(() => 0);

  return svgPaths + svgRects + svgCircles + canvasNodes;
}

test.describe('Live dashboard (WS → charts)', () => {
  test.beforeEach(async ({ page }) => {
    // Install a WS shim before app code executes
    await page.addInitScript(() => {
      // @ts-ignore
      (window as any).__WS_TEST__ = {
        opened: false,
        subId: null as string | null,
        instances: [] as any[],
        headsPushed: 0,
        // Utilities for deterministic head emission
        mkHead(height: number) {
          // Construct a synthetic "head" payload with Γ/fairness/mix
          const gamma = Math.max(0, Math.min(1, 0.3 + 0.05 * Math.sin(height / 2)));
          const fairness = Math.max(0, Math.min(1, 0.7 - 0.04 * Math.cos(height / 3)));
          const mix = [
            { k: 'hashshare', v: Math.max(0.05, 0.45 + 0.1 * Math.sin(height / 5)) },
            { k: 'ai',        v: Math.max(0.05, 0.25 + 0.08 * Math.cos(height / 4)) },
            { k: 'quantum',   v: Math.max(0.05, 0.15 + 0.06 * Math.sin(height / 6)) },
            { k: 'storage',   v: 0 }, // will normalize below
          ];
          const sumFirst3 = mix[0].v + mix[1].v + mix[2].v;
          mix[3].v = Math.max(0.05, 1 - sumFirst3);
          // normalize to 1.0
          const sum = mix.reduce((a, b) => a + b.v, 0);
          mix.forEach(m => (m.v = m.v / sum));

          return {
            number: '0x' + height.toString(16),
            poies: {
              gamma,
              fairness,
              mix: Object.fromEntries(mix.map(m => [m.k, m.v])),
            },
            // minimal header-ish fields other UIs may consult
            timestamp: Math.floor(Date.now() / 1000),
            parentHash: '0x' + ('deadbeef'.repeat(8)),
            hash: '0x' + ('cafe'.repeat(8)) + height.toString(16).padStart(2, '0'),
          };
        },
        pushHeads(n: number = 1) {
          const self = (window as any).__WS_TEST__;
          for (let i = 0; i < n; i++) {
            self.headsPushed++;
            const head = self.mkHead(self.headsPushed);
            for (const ws of self.instances) {
              const sub = ws.__subId || self.subId || '0xsub';
              const notif = {
                jsonrpc: '2.0',
                method: 'eth_subscription', // widely supported shape
                params: {
                  subscription: sub,
                  result: head,
                },
              };
              ws.__emitMessage(notif);
            }
          }
        },
      };

      class FakeWebSocket {
        url: string;
        readyState: number = 0; // CONNECTING
        onopen: ((ev: any) => any) | null = null;
        onclose: ((ev: any) => any) | null = null;
        onmessage: ((ev: any) => any) | null = null;
        onerror: ((ev: any) => any) | null = null;

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        private listeners: Record<string, ((ev: any) => any)[]> = { open: [], close: [], message: [], error: [] };
        __subId: string | null = null;

        constructor(url: string) {
          this.url = url;
          // Track instance
          // @ts-ignore
          (window as any).__WS_TEST__.instances.push(this);
          // Simulate async open
          setTimeout(() => {
            this.readyState = 1; // OPEN
            // @ts-ignore
            (window as any).__WS_TEST__.opened = true;
            this.__emit('open', {});
          }, 10);
        }

        send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
          try {
            const txt = typeof data === 'string' ? data : new TextDecoder().decode(data as ArrayBuffer);
            const msg = JSON.parse(txt);
            // Handle common subscribe calls (eth_subscribe / subscribeNewHeads / omni_subscribe etc.)
            const m = String(msg.method || '');
            if (m.includes('subscribe') && JSON.stringify(msg.params || {}).includes('newHeads')) {
              // Confirm subscription
              this.__subId = '0xsub';
              const resp = { jsonrpc: '2.0', id: msg.id, result: this.__subId };
              this.__emitMessage(resp);
              // Immediately stream a first head
              // @ts-ignore
              (window as any).__WS_TEST__.pushHeads(1);
            }
          } catch {
            // ignore
          }
        }

        close() {
          this.readyState = 3; // CLOSED
          this.__emit('close', {});
        }

        addEventListener(type: 'open'|'message'|'close'|'error', cb: (ev: any) => any) {
          this.listeners[type].push(cb);
        }

        removeEventListener(type: 'open'|'message'|'close'|'error', cb: (ev: any) => any) {
          this.listeners[type] = this.listeners[type].filter(fn => fn !== cb);
        }

        __emit(type: 'open'|'message'|'close'|'error', ev: any) {
          const fn = (this as any)['on' + type];
          if (typeof fn === 'function') fn(ev);
          for (const cb of this.listeners[type]) cb(ev);
        }

        __emitMessage(payload: unknown) {
          const ev = { data: JSON.stringify(payload) };
          this.__emit('message', ev);
        }

        // ReadyState constants
        static readonly CONNECTING = 0;
        static readonly OPEN = 1;
        static readonly CLOSING = 2;
        static readonly CLOSED = 3;
      }

      // Patch global WebSocket
      // @ts-ignore
      (window as any).WebSocket = FakeWebSocket;
    });
  });

  test('connects and updates Γ / Fairness / Mix charts upon newHeads', async ({ page }) => {
    await page.goto('/');

    // Basic smoke that page is up (TopBar or main container present)
    await expect(page.locator('header, [data-testid="TopBar"]')).toBeVisible();

    // Expect our shim to report connected soon
    await expect.poll(async () => {
      const opened = await page.evaluate(() => (window as any).__WS_TEST__?.opened ?? false);
      return opened ? 'yes' : 'no';
    }, { timeout: 10_000 }).toBe('yes');

    // Locate charts with tolerant selectors
    const gammaChart = await getFirstLocator(page, [
      '[data-testid="gamma-chart"]',
      '[data-testid="GammaPanel"]',
      '[data-testid="chart-gamma"]',
      'section:has-text("Γ")',
      'section:has-text("Gamma")',
    ]);

    const fairnessChart = await getFirstLocator(page, [
      '[data-testid="fairness-chart"]',
      '[data-testid="FairnessPanel"]',
      '[data-testid="chart-fairness"]',
      'section:has-text("Fairness")',
    ]);

    const mixChart = await getFirstLocator(page, [
      '[data-testid="proof-mix-chart"]',
      '[data-testid="PoIESBreakdown"]',
      '[data-testid="chart-mix"]',
      'section:has-text("Proof Mix")',
      'section:has-text("PoIES")',
    ]);

    // Baseline node counts
    const baseGammaNodes = await countChartDrawnNodes(gammaChart);
    const baseFairNodes = await countChartDrawnNodes(fairnessChart);
    const baseMixNodes = await countChartDrawnNodes(mixChart);

    // Push a small burst of heads
    await page.evaluate((n) => (window as any).__WS_TEST__.pushHeads(n), HEADS_TO_PUSH);

    // Expect rendered node counts to increase (charts received new samples)
    await expect.poll(async () => countChartDrawnNodes(gammaChart)).toBeGreaterThan(baseGammaNodes);
    await expect.poll(async () => countChartDrawnNodes(fairnessChart)).toBeGreaterThan(baseFairNodes);
    await expect.poll(async () => countChartDrawnNodes(mixChart)).toBeGreaterThan(baseMixNodes);

    // Optional: check the current Γ numeric label (if present) becomes a finite number
    const gammaValueEl = await getFirstLocator(page, [
      '[data-testid="gamma-current"]',
      '[data-testid="GammaPanel-current"]',
      '[data-testid="metric-gamma"]',
      'text=/^Γ\\s*[:=]?\\s*[0-9.]+/i',
    ]).catch(() => null);

    if (gammaValueEl) {
      const txt = (await gammaValueEl.textContent()) || '';
      const num = parseFloat((txt.match(/[0-9.]+/) || [])[0]);
      expect(Number.isFinite(num)).toBeTruthy();
      expect(num).toBeGreaterThanOrEqual(0);
      expect(num).toBeLessThanOrEqual(1);
    }
  });
});
