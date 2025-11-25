/**
 * Escrow demo — loads the studio-wasm library, compiles the example contract,
 * configures payer/payee, deposits funds, checks balance, and releases.
 *
 * Usage (with `npm run dev` running for studio-wasm):
 *   In the dev preview page console:
 *     import('/examples/escrow/demo.ts');
 */

import { compileSource } from '/src/api/compiler';
import { estimateGas, simulateCall } from '/src/api/simulator';
import { createState } from '/src/api/state';

type Manifest = {
  name: string;
  version: string;
  abi: {
    functions: Array<{
      name: string;
      inputs?: any[];
      outputs?: any[];
      mutates?: boolean;
      payable?: boolean;
    }>;
  };
  resources: { code: string };
};

function log(...args: any[]) {
  // eslint-disable-next-line no-console
  console.log('[escrow-demo]', ...args);
}

async function loadExample(): Promise<{ manifest: Manifest; source: string }> {
  const [m, s] = await Promise.all([
    fetch('/examples/escrow/manifest.json').then(r => {
      if (!r.ok) throw new Error(`failed to load manifest: ${r.status}`);
      return r.json();
    }),
    fetch('/examples/escrow/contract.py').then(r => {
      if (!r.ok) throw new Error(`failed to load contract.py: ${r.status}`);
      return r.text();
    }),
  ]);
  return { manifest: m, source: s };
}

const te = new TextEncoder();
const toBytes = (s: string) => te.encode(s); // ABI accepts Uint8Array for bytes

(async () => {
  try {
    log('loading example files…');
    const { manifest, source } = await loadExample();

    log('compiling source…');
    const compiled = await compileSource({ manifest, source });

    // Create an ephemeral in-worker state for this demo session.
    const state = await createState();

    // 1) configure(payer, payee)
    const payer = toBytes('payer-demo-addr');
    const payee = toBytes('payee-demo-addr');

    log('calling configure(payer, payee)…');
    const cfg = await simulateCall({
      compiled,
      manifest,
      entry: 'configure',
      args: { payer, payee },
      state,
    });
    if (!cfg.ok) {
      log('configure failed:', cfg.error);
      return;
    }
    log('configure: ok, gas_used=', cfg.gasUsed);

    // 2) deposit(amount)
    const amount = 100;

    const gasEst = await estimateGas({
      compiled,
      manifest,
      entry: 'deposit',
      args: { amount },
      state,
    });
    log('deposit gas_upper_bound ~', gasEst.upperBound);

    log(`calling deposit(${amount})…`);
    const dep = await simulateCall({
      compiled,
      manifest,
      entry: 'deposit',
      args: { amount },
      state,
    });
    if (!dep.ok) {
      log('deposit failed:', dep.error);
      return;
    }
    log('deposit: ok, new balance =', dep.returnValue, 'gas_used=', dep.gasUsed);
    if (dep.events?.length) log('events:', JSON.stringify(dep.events, null, 2));

    // 3) balance()
    const bal = await simulateCall({
      compiled,
      manifest,
      entry: 'balance',
      args: {},
      state,
    });
    if (!bal.ok) {
      log('balance() failed:', bal.error);
      return;
    }
    log('balance() ->', bal.returnValue);

    // 4) release()
    log('calling release()…');
    const rel = await simulateCall({
      compiled,
      manifest,
      entry: 'release',
      args: {},
      state,
    });
    if (!rel.ok) {
      log('release() failed:', rel.error);
      return;
    }
    log('release(): amount =', rel.returnValue, 'gas_used=', rel.gasUsed);
    if (rel.events?.length) log('events:', JSON.stringify(rel.events, null, 2));

    // 5) balance() after release
    const bal2 = await simulateCall({
      compiled,
      manifest,
      entry: 'balance',
      args: {},
      state,
    });
    if (!bal2.ok) {
      log('post-release balance() failed:', bal2.error);
      return;
    }
    log('post-release balance() ->', bal2.returnValue);
  } catch (err) {
    console.error('[escrow-demo] error:', err);
  }
})();
