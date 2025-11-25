# @animica/studio-wasm

**Run the Animica Python VM entirely in the browser.**  
This package bundles a trimmed, deterministic subset of the Python VM via **Pyodide/WASM**, plus a typed TypeScript API and a Web Worker that lets you **compile**, **simulate**, and **estimate gas** for contracts locally — without a node backend or server-side signing.

- ✅ **Deterministic**: pure compute, no network I/O, no system clock/files.
- ✅ **Fast startup** (cached Pyodide assets) and **isolated** execution in a Worker.
- ✅ **Same IR/ABI** as the on-chain Python VM; results match node execution semantics for read-only sim.
- ✅ **Production-ready**: version-pinned Pyodide, integrity checks, explicit resource caps.

> This package is used by **studio-web** for the browser IDE and can be embedded in any dapp tooling or docs site to provide instant “try it” experiences.

---

## Contents

- **TypeScript library** (`src/index.ts`) exporting:
  - `simulateCall`, `simulateDeploy`, `estimateGas` (from `src/api/simulator.ts`)
  - `compileSource`, `compileIR`, `linkManifest` (from `src/api/compiler.ts`)
  - `createState`, `resetState` (from `src/api/state.ts`)
- **Pyodide loader** (`src/pyodide/loader.ts`) with caching & version pinning.
- **Web Worker** (`src/worker/pyvm.worker.ts`) and a typed protocol.
- **Trimmed Python package** (`py/vm_pkg/…`) with the deterministic interpreter core.
- **Lockfile & fetch script** for Pyodide assets (`pyodide.lock.json`, `scripts/fetch_pyodide.mjs`).

---

## Browser Support

- **Chromium (Chrome/Edge)**: latest two versions (WASM + Workers + ES2020).
- **Firefox**: latest two versions.
- **Safari**: 16.4+ (WASM in Workers required).
- **Mobile**: iOS/iPadOS 16.4+, Android Chrome 114+.

> Service Worker–based caching is recommended on slow networks. Incognito/private contexts may re-download assets.

---

## Security & Determinism

- No network access from the VM. All host calls are stubs in this package.
- Deterministic PRNG seeded per call for tests/examples.
- Gas metering is deterministic; **no state writes** are persisted across simulation unless you use the in-worker ephemeral state handle.
- Pin the Pyodide version (see **Version Pinning** below). Optionally enforce **SRI** when loading from CDN.

---

## Installation

```bash
# with npm
npm i @animica/studio-wasm

# or yarn
yarn add @animica/studio-wasm

# or pnpm
pnpm add @animica/studio-wasm

Version Pinning & CDN toggle

This package ships with a lockfile for Pyodide. You can override via env:

# .env (consumed by vite/webpack define plugin or your env loader)
PYODIDE_VERSION=0.24.1
PYODIDE_CDN=https://cdn.jsdelivr.net/pyodide/v0.24.1/full   # optional; defaults to vendored files
PYODIDE_OFFLINE_DIR=/your/local/cache                       # optional local fallback

The default build uses vendored assets (see vendor/), falling back to CDN only if enabled.

⸻

Quickstart

1) Simulate in a Worker (recommended)

// src/main.ts
import { simulateCall, compileSource, createState } from '@animica/studio-wasm';

const source = `
from stdlib import storage, events, abi

def inc():
    v = storage.get_int(b"c") or 0
    storage.set_int(b"c", v + 1)
    events.emit(b"Inc", {"value": v + 1})

def get() -> int:
    return storage.get_int(b"c") or 0
`;

const manifest = {
  name: "Counter",
  version: "1.0.0",
  abi: {
    functions: [
      { name: "inc", inputs: [], outputs: [] },
      { name: "get", inputs: [], outputs: [{ type: "int" }] }
    ],
    events: [{ name: "Inc", fields: [{ name: "value", type: "int" }] }]
  }
};

(async () => {
  // Compile (Python → IR) in Pyodide
  const { ir, gasUpperBound, diagnostics } = await compileSource({ source, manifest });
  if (diagnostics.length) console.warn('compile diagnostics', diagnostics);

  // Create an ephemeral state inside the worker
  const state = await createState();

  // Run inc()
  await simulateCall({
    state,
    manifest,
    ir,
    function: "inc",
    args: []
  });

  // Read value with get()
  const res = await simulateCall({
    state,
    manifest,
    ir,
    function: "get",
    args: []
  });

  console.log('Counter value =', res.returnValue); // => 1
  console.log('Gas used (inc/get):', res.gasUsed);
})();

2) Estimate gas for a call

import { estimateGas } from '@animica/studio-wasm';

const { gasEstimate } = await estimateGas({
  manifest,
  ir,
  function: "inc",
  args: []
});
console.log('Estimated gas (upper bound):', gasEstimate);

3) Compile from prebuilt IR

If you already have IR bytes (e.g., from CI):

import { compileIR } from '@animica/studio-wasm';

const { module } = await compileIR({ irBytes });


⸻

Minimal API Overview

compileSource({ source, manifest })
	•	Input: Python source string + manifest JSON.
	•	Output: { ir: Uint8Array, gasUpperBound: bigint, diagnostics: Diagnostic[] }.

compileIR({ irBytes })
	•	Input: IR bytes (Uint8Array).
	•	Output: { module: CompiledModule }.

linkManifest({ module, manifest })
	•	Validates ABI ↔ symbols; returns linked module and typed call map.

createState() / resetState(state)
	•	Ephemeral, in-worker key/value storage and logs used by the simulator.

simulateCall({ state, manifest, ir|module, function, args })
	•	Executes a contract function deterministically.
	•	Returns { returnValue, logs, gasUsed, trace? }.

simulateDeploy({ state, manifest, ir|module, initArgs? })
	•	Runs constructor/init phase if applicable; returns { address?, gasUsed, logs }.

estimateGas({ ... })
	•	Static upper bound + (optional) dynamic refine; returns { gasEstimate }.

See TypeScript typings in src/types/index.ts for exact structures.

⸻

Performance Tips
	•	Worker only: keep Pyodide in a dedicated Worker to avoid blocking the UI.
	•	Warm cache: pre-load Pyodide at boot (loader.preload()); show a short “warming up” toast.
	•	Chunked loads: host Pyodide assets on the same domain with HTTP/2 to parallelize.
	•	Memoize IR: compile once per edit; re-run only on source changes.

⸻

Integrity & Caching
	•	The loader verifies file sizes and optional checksums from pyodide.lock.json.
	•	You can provide SRI hashes when using a CDN.
	•	For production, set long-lived cache headers on vendored assets; rely on content hashes.

⸻

Troubleshooting
	•	“Pyodide failed to load”: check CSP (worker-src, connect-src, script-src) and that WASM MIME type is served correctly.
	•	Safari stalls: ensure 16.4+; older versions have partial WASM-in-Worker support.
	•	Large bundles: keep only the minimal vm_pkg subset; don’t import extra wheels.
	•	Different results vs node: confirm you’re not using nondeterministic stdlib APIs and that your manifest/ABI matches the runtime.

⸻

Development (in this repo)

# fetch and pin pyodide assets into vendor/
pnpm --filter studio-wasm run fetch:pyodide
# or
node studio-wasm/scripts/fetch_pyodide.mjs

# dev library build
pnpm --filter studio-wasm dev

# unit + e2e tests (vitest + playwright)
pnpm --filter studio-wasm test
pnpm --filter studio-wasm e2e


⸻

License

This package follows the repository root LICENSE. See ../../LICENSE.

⸻

Changelog & Support
	•	Changes are tracked in the monorepo release notes.
	•	For issues and security reports, see sdk/docs/SECURITY.md and open an issue with a minimal repro.

