/**
 * Measure a tiny call sequence over an ephemeral state using the WASM simulator:
 *   get() -> inc() -> get()
 *
 * Usage:
 *   pnpm -C studio-wasm dlx tsx bench/run_time.ts
 *
 * Prints a single JSON line, e.g.:
 *   {
 *     "name":"run_time",
 *     "get0_ms":42,
 *     "inc_ms":87,
 *     "get1_ms":41,
 *     "total_ms":208,
 *     "gas_used_inc":1234,
 *     "events_emitted":1,
 *     "pyodide_version":"0.24.1"
 *   }
 */

import { performance } from 'node:perf_hooks';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { getPyodide } from '../src/pyodide/loader';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const r = (p: string) => path.resolve(__dirname, '..', p);

type SimOk = {
  ok: true;
  returnValue: any;
  gasUsed?: number;
  events?: Array<{ name: string; args: Record<string, any> }>;
};
type SimErr = { ok: false; error: any };

async function main() {
  const out: Record<string, any> = { name: 'run_time' };

  try {
    const [{ compileSource }, { simulateCall }, { createState }] = await Promise.all([
      import('../src/api/compiler'),
      import('../src/api/simulator'),
      import('../src/api/state'),
    ]);

    const manifestPath = r('../examples/counter/manifest.json');
    const sourcePath = r('../examples/counter/contract.py');

    const [manifestRaw, source] = await Promise.all([
      readFile(manifestPath, 'utf8'),
      readFile(sourcePath, 'utf8'),
    ]);
    const manifest = JSON.parse(manifestRaw);

    const compiled = await compileSource({ manifest, source });
    const state = await createState();

    const seqStart = performance.now();

    // get() baseline
    const t0a = performance.now();
    const resGet0 = (await simulateCall({
      compiled,
      manifest,
      entry: 'get',
      args: {},
      state,
    })) as SimOk | SimErr;
    const t0b = performance.now();
    if (!resGet0.ok) throw new Error('get() failed: ' + JSON.stringify(resGet0));
    out.get0_ms = Math.round(t0b - t0a);

    // inc()
    const t1a = performance.now();
    const resInc = (await simulateCall({
      compiled,
      manifest,
      entry: 'inc',
      args: {},
      state,
    })) as SimOk | SimErr;
    const t1b = performance.now();
    if (!resInc.ok) throw new Error('inc() failed: ' + JSON.stringify(resInc));
    out.inc_ms = Math.round(t1b - t1a);

    // get() again
    const t2a = performance.now();
    const resGet1 = (await simulateCall({
      compiled,
      manifest,
      entry: 'get',
      args: {},
      state,
    })) as SimOk | SimErr;
    const t2b = performance.now();
    if (!resGet1.ok) throw new Error('second get() failed: ' + JSON.stringify(resGet1));
    out.get1_ms = Math.round(t2b - t2a);

    out.total_ms = Math.round(performance.now() - seqStart);
    out.gas_used_inc = (resInc as SimOk).gasUsed ?? null;
    out.events_emitted = (resInc as SimOk).events?.length ?? 0;

    // Useful context
    const py = await getPyodide();
    // @ts-expect-error loosened typing for Pyodide runtime
    out.pyodide_version = String((py && (py as any).version) || 'unknown');

    // Sanity values
    out.get0_value = (resGet0 as SimOk).returnValue;
    out.inc_value = (resInc as SimOk).returnValue;
    out.get1_value = (resGet1 as SimOk).returnValue;
  } catch (err: any) {
    out.error = String(err?.message || err);
    out.stack = err?.stack;
  }

  // Single JSON line
  // eslint-disable-next-line no-console
  console.log(JSON.stringify(out));
}

main();
