/**
 * Counter demo — loads the studio-wasm library, compiles the example contract,
 * estimates gas, runs inc(by=1) and then get(), and prints results & events.
 *
 * Usage (with `npm run dev` running for studio-wasm):
 *   In the dev preview page console:
 *     import('/examples/counter/demo.ts');
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
    events?: Array<{
      name: string;
      args: Array<{ name: string; type: string }>;
    }>;
  };
  resources: { code: string };
};

function log(...args: any[]) {
  // eslint-disable-next-line no-console
  console.log('[demo]', ...args);
}

async function loadExample(): Promise<{ manifest: Manifest; source: string }> {
  const [m, s] = await Promise.all([
    fetch('/examples/counter/manifest.json').then(r => {
      if (!r.ok) throw new Error(`failed to load manifest: ${r.status}`);
      return r.json();
    }),
    fetch('/examples/counter/contract.py').then(r => {
      if (!r.ok) throw new Error(`failed to load contract.py: ${r.status}`);
      return r.text();
    }),
  ]);
  return { manifest: m, source: s };
}

(async () => {
  try {
    log('loading example files…');
    const { manifest, source } = await loadExample();

    log('compiling source…');
    const compiled = await compileSource({ manifest, source });
    // `compiled` likely includes IR/module bytes and metadata required by simulator

    // Create an ephemeral state snapshot inside the worker for this demo session.
    const state = await createState();

    // Estimate gas for inc(by=1)
    const incArgs = { by: 1 };
    const gasEst = await estimateGas({
      compiled,
      manifest,
      entry: 'inc',
      args: incArgs,
      state,
    });
    log('gas_upper_bound ~', gasEst.upperBound);

    // Run inc(by=1)
    const incRes = await simulateCall({
      compiled,
      manifest,
      entry: 'inc',
      args: incArgs,
      state,
    });

    if (!incRes.ok) {
      log('inc() failed:', incRes.error);
      return;
    }
    log('inc(): ok, gas_used=', incRes.gasUsed);
    if (incRes.events?.length) {
      log('events:', JSON.stringify(incRes.events, null, 2));
    }
    log('return:', incRes.returnValue);

    // Run get()
    const getRes = await simulateCall({
      compiled,
      manifest,
      entry: 'get',
      args: {},
      state,
    });

    if (!getRes.ok) {
      log('get() failed:', getRes.error);
      return;
    }
    log('get() ->', getRes.returnValue);
  } catch (err) {
    console.error('[demo] error:', err);
  }
})();
