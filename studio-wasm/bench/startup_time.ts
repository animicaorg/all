/**
 * Measure Pyodide boot latency (cold vs warm) for the studio-wasm bundle.
 * Usage:
 *   pnpm -C studio-wasm dlx tsx bench/startup_time.ts
 *
 * Prints a single JSON line:
 *   {"name":"startup_time","cold_ms":1234,"warm_ms":210,"pyodide_version":"0.24.1","python":"3.11.4"}
 */

import { performance } from 'node:perf_hooks';
import { getPyodide } from '../src/pyodide/loader';

async function main() {
  const out: Record<string, any> = { name: 'startup_time' };

  try {
    // Cold start
    const t0 = performance.now();
    const py = await getPyodide();
    const t1 = performance.now();

    // Warm (cached) start
    const t2 = performance.now();
    const py2 = await getPyodide(); // should reuse the same instance
    const t3 = performance.now();

    // Gather versions
    const pyodide_version =
      // @ts-expect-error: Pyodide typings are not strict here
      (py && (py as any).version) || 'unknown';
    const python = py.runPython<string>('import platform; platform.python_version()');

    out.cold_ms = Math.round(t1 - t0);
    out.warm_ms = Math.round(t3 - t2);
    out.pyodide_version = String(pyodide_version);
    out.python = String(python);
  } catch (err: any) {
    out.error = String(err?.message || err);
    out.stack = err?.stack;
  }

  // Single JSON line to stdout
  // eslint-disable-next-line no-console
  console.log(JSON.stringify(out));
}

main();
