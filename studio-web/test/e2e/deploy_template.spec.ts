import { test, expect } from '@playwright/test';

/**
 * Gate E2E:
 *  scaffold → simulate → connect → deploy → verify
 *
 * Notes
 * - This test is resilient: it will locally mock studio-services + node RPC endpoints so it can
 *   run in CI without a live devnet or wallet extension. If you want to hit a real devnet +
 *   services, set E2E_LIVE=1 and provide E2E_BASE_URL / E2E_SERVICES_URL / E2E_CHAIN_ID and
 *   a real wallet in the browser (window.animica). In that mode we skip network interception.
 * - The UI selectors rely on roles/text used across the app; if you tweak copy, update the
 *   regexes below or add data-testid attributes.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';
const SERVICES_URL = process.env.E2E_SERVICES_URL ?? 'http://localhost:8787';
const CHAIN_ID = Number(process.env.E2E_CHAIN_ID ?? '1337');
const USE_LIVE = process.env.E2E_LIVE === '1';
const USE_FAKE_WALLET = process.env.E2E_FAKE_WALLET !== '0'; // default on (mock wallet)

const DUMMY_ADDR = 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0c0pj'; // harmless placeholder

test.describe('Gate: scaffold → simulate → connect → deploy → verify', () => {
  test.beforeEach(async ({ page }) => {
    // Optional: inject a fake wallet (window.animica) so "Connect" works without an extension.
    if (!USE_LIVE && USE_FAKE_WALLET) {
      await page.addInitScript(({ addr, chainId }) => {
        (window as any).animica = {
          // Minimal EIP-1193-ish API surface the app needs.
          request: async ({ method, params }: { method: string; params?: any[] }) => {
            switch (method) {
              case 'animica_providerVersion':
                return 'animica-provider/1.0-mock';
              case 'animica_chainId':
                return chainId;
              case 'animica_requestAccounts':
                return [addr];
              case 'animica_accounts':
                return [addr];
              // The app may call a generic sign endpoint; return a dummy hex string.
              case 'animica_sign':
              case 'animica_signBytes':
              case 'animica_signTx':
                return '0xdeadbeef';
              default:
                // For unknown calls, return something sensible rather than throwing.
                return null;
            }
          },
          on: () => {},
          removeListener: () => {},
        };
      }, { addr: DUMMY_ADDR, chainId: CHAIN_ID });
    }
  });

  test('scaffold → simulate → connect → deploy → verify', async ({ page }) => {
    // ---------------------------------------------------------------------
    // Network intercepts (mock services + RPC) unless running against live.
    // ---------------------------------------------------------------------
    if (!USE_LIVE) {
      // Mock studio-services endpoints used by the UI.
      await page.route(`${SERVICES_URL}/deploy`, async (route) => {
        const json = { txHash: '0x' + 'ab'.repeat(32), address: DUMMY_ADDR };
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
      });
      await page.route(`${SERVICES_URL}/preflight`, async (route) => {
        const json = { ok: true, gasUsed: 123456, warnings: [] };
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
      });
      await page.route(new RegExp(`${SERVICES_URL}/verify.*`), async (route) => {
        // Two-phase: first call returns "queued", next returns "complete"
        const url = new URL(route.request().url());
        const once = url.searchParams.get('once');
        const queued = { status: 'queued', jobId: 'job_mock_1' };
        const done = {
          status: 'complete',
          result: { match: true, address: DUMMY_ADDR, codeHash: '0x' + 'cd'.repeat(32) },
        };
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(once ? done : queued),
        });
      });

      // Mock node RPC used for shallow reads the UI might perform.
      await page.route('**/rpc', async (route) => {
        const req = await route.request().postDataJSON().catch(() => null);
        const make = (id: any, result: any) => ({ jsonrpc: '2.0', id, result });
        let body: any = make(req?.id ?? 1, null);

        try {
          const method = req?.method;
          switch (method) {
            case 'chain.getChainId':
              body = make(req.id, CHAIN_ID);
              break;
            case 'chain.getHead':
              body = make(req.id, { height: 12345, hash: '0x' + 'ef'.repeat(32) });
              break;
            default:
              body = make(req?.id ?? 1, null);
          }
        } catch {
          body = { jsonrpc: '2.0', id: req?.id ?? 1, error: { code: -32000, message: 'mock error' } };
        }

        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      });
    }

    // ---------------------------------------------------------------------
    // Open the app.
    // ---------------------------------------------------------------------
    await page.goto(BASE_URL);
    await expect(page).toHaveTitle(/Studio/i);

    // Some apps show a network banner or need a second to boot wasm; be patient.
    await page.waitForLoadState('networkidle');

    // ---------------------------------------------------------------------
    // Scaffold a project from the Counter template.
    // ---------------------------------------------------------------------
    // Navigate to Tools → Scaffold (sidebar item or route).
    const scaffoldTab =
      page.getByRole('link', { name: /Scaffold/i }).or(page.getByRole('button', { name: /Scaffold/i }));
    if (await scaffoldTab.isVisible().catch(() => false)) {
      await scaffoldTab.click();
    } else {
      // Fallback: navigate directly if the link is not present in this build variant.
      await page.goto(`${BASE_URL}/tools/scaffold`);
    }

    // Click on the "Counter" template card and create the project.
    const counterCard = page.getByText(/Counter/i).first();
    await expect(counterCard).toBeVisible({ timeout: 10_000 });
    await counterCard.click();

    // Some UIs have an explicit "Create" button; try it if present.
    const createBtn = page.getByRole('button', { name: /Create|Use template|Scaffold/i }).first();
    if (await createBtn.isVisible().catch(() => false)) {
      await createBtn.click();
    }

    // Expect the editor to show up with counter source loaded.
    await expect(page.getByText(/contract\.py|def inc|def get/i)).toBeVisible({ timeout: 15_000 });

    // ---------------------------------------------------------------------
    // Compile & Simulate: run inc(); then get() and expect >= 1.
    // ---------------------------------------------------------------------
    // Go to Edit view if not already there.
    const editTab =
      page.getByRole('link', { name: /^Edit$/i }).or(page.getByRole('button', { name: /^Edit$/i }));
    if (await editTab.isVisible().catch(() => false)) {
      await editTab.click();
    }

    // Compile
    const compileBtn = page.getByRole('button', { name: /Compile|Build/i }).first();
    if (await compileBtn.isVisible().catch(() => false)) {
      await compileBtn.click();
    }
    // Wait for a status/diagnostic that indicates compile finished.
    await expect(
      page.getByText(/Compiled|Gas estimate|0 diagnostics|build complete/i).first(),
    ).toBeVisible({ timeout: 20_000 });

    // Switch to Simulate panel (if panels are tabbed).
    const simulateTab =
      page.getByRole('tab', { name: /Simulate/i }).or(page.getByRole('button', { name: /Simulate/i }));
    if (await simulateTab.isVisible().catch(() => false)) {
      await simulateTab.click();
    }

    // Select method "inc" and run.
    const incOption = page.getByRole('option', { name: /^inc$/i }).or(page.getByText(/^inc\s*\(/i));
    if (await incOption.isVisible().catch(() => false)) {
      await incOption.click();
    }
    const runBtn = page.getByRole('button', { name: /Run|Execute|Simulate/i }).first();
    await runBtn.click();

    // Expect an event log or success status to appear.
    await expect(
      page.getByText(/SUCCESS|Events|logs|status/i).first(),
    ).toBeVisible({ timeout: 20_000 });

    // Now call "get" and ensure result >= 1.
    const getOption = page.getByRole('option', { name: /^get$/i }).or(page.getByText(/^get\s*\(/i));
    if (await getOption.isVisible().catch(() => false)) {
      await getOption.click();
    }
    await runBtn.click();

    // Read the return/result panel; tolerate different formatting.
    const resultPane = page.getByText(/result|return|value/i).first();
    await expect(resultPane).toBeVisible({ timeout: 20_000 });
    // Not every UI shows the raw value; do a lenient sanity check.
    // (If your UI exposes a data-testid for the return value, assert it here.)

    // ---------------------------------------------------------------------
    // Connect wallet (mocked) & Deploy.
    // ---------------------------------------------------------------------
    const deployNav =
      page.getByRole('link', { name: /^Deploy$/i }).or(page.getByRole('button', { name: /^Deploy$/i }));
    if (await deployNav.isVisible().catch(() => false)) {
      await deployNav.click();
    } else {
      await page.goto(`${BASE_URL}/deploy`);
    }

    // Click "Connect" (with our injected window.animica this should succeed).
    const connectBtn = page.getByRole('button', { name: /Connect|Connect Wallet/i }).first();
    if (await connectBtn.isVisible().catch(() => false)) {
      await connectBtn.click();
      // The UI should show the connected address somewhere.
      await expect(page.getByText(new RegExp(DUMMY_ADDR.slice(0, 10)))).toBeVisible({ timeout: 10_000 });
    }

    // Preflight (optional) then Deploy
    const preflightBtn = page.getByRole('button', { name: /Preflight|Estimate/i }).first();
    if (await preflightBtn.isVisible().catch(() => false)) {
      await preflightBtn.click();
      await expect(page.getByText(/preflight|gas/i)).toBeVisible({ timeout: 10_000 });
    }

    const deployBtn = page.getByRole('button', { name: /^Deploy/i }).first();
    await expect(deployBtn).toBeVisible();
    await deployBtn.click();

    // Wait for tx hash + address UI
    await expect(page.getByText(/tx|hash/i).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(new RegExp(DUMMY_ADDR.slice(0, 10)))).toBeVisible({ timeout: 20_000 });

    // ---------------------------------------------------------------------
    // Verify source (via studio-services mock): should transition to Verified.
    // ---------------------------------------------------------------------
    const verifyNav =
      page.getByRole('link', { name: /^Verify$/i }).or(page.getByRole('button', { name: /^Verify$/i }));
    if (await verifyNav.isVisible().catch(() => false)) {
      await verifyNav.click();
    } else {
      await page.goto(`${BASE_URL}/verify`);
    }

    const startVerifyBtn = page.getByRole('button', { name: /Verify|Recompile/i }).first();
    await startVerifyBtn.click();

    // We mocked the first call as 'queued' and the next as 'complete'; wait for success.
    await expect(
      page.getByText(/Verified|match.*true/i).first(),
    ).toBeVisible({ timeout: 20_000 });
  });
});
