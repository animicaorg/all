/**
 * Measure compile time for the Counter example using the same public API
 * that the app uses (src/api/compiler.ts).
 *
 * Usage:
 *   pnpm -C studio-wasm dlx tsx bench/compile_time.ts
 *
 * Prints a single JSON line:
 *   {"name":"compile_time","compile_ms":421,"ir_size_bytes":3124,"pyodide_version":"0.24.1"}
 */

import { performance } from 'node:perf_hooks';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { getPyodide } from '../src/pyodide/loader';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function r(p: string) {
  return path.resolve(__dirname, '..', p);
}

function guessIrSize(compiled: any): number {
  if (!compiled) return 0;
  // Common fields we might expose
  const candidates = [
    compiled.irBytes,
    compiled.ir,
    compiled.moduleBytes,
    compiled.bytes,
  ].filter(Boolean);

  for (const c of candidates) {
    if (c instanceof Uint8Array) return c.length;
    if (ArrayBuffer.isView(c) && c.buffer) return (c as any).byteLength ?? 0;
  }

  // Some compilers return a base64-encoded string
  if (typeof compiled.ir_b64 === 'string') {
    try {
      return Buffer.from(compiled.ir_b64, 'base64').length;
    } catch (_) {
      /* ignore */
    }
  }
  return 0;
}

async function main() {
  const out: Record<string, any> = { name: 'compile_time' };

  try {
    // Lazy import to keep script startup snappy
    const { compileSource } = await import('../src/api/compiler');

    const manifestPath = r('../examples/counter/manifest.json');
    const sourcePath = r('../examples/counter/contract.py');

    const [manifestRaw, source] = await Promise.all([
      readFile(manifestPath, 'utf8'),
      readFile(sourcePath, 'utf8'),
    ]);

    const manifest = JSON.parse(manifestRaw);

    const t0 = performance.now();
    const compiled = await compileSource({ manifest, source });
    const t1 = performance.now();

    // Get Pyodide version for context
    const py = await getPyodide();
    // @ts-expect-error not typed strictly
    const pyodide_version = (py && (py as any).version) || 'unknown';

    out.compile_ms = Math.round(t1 - t0);
    out.ir_size_bytes = guessIrSize(compiled);
    out.pyodide_version = String(pyodide_version);
  } catch (err: any) {
    out.error = String(err?.message || err);
    out.stack = err?.stack;
  }

  // Single JSON line to stdout
  // eslint-disable-next-line no-console
  console.log(JSON.stringify(out));
}

main();
