# studio-wasm/examples

Small, **browser-run** demos that exercise the Pyodide-powered Python VM shipped by `studio-wasm`.  
These examples are used by **studio-web** later, but are **fully runnable standalone** during local
development.

---

## What’s inside

- **counter/** — minimal deterministic Counter contract
  - `contract.py`: contract source
  - `manifest.json`: ABI + metadata
  - `demo.ts`: loads the library, compiles the contract, runs `inc()` then `get()`, prints logs
- **escrow/** — simple escrow with a tiny “treasury” stub
  - `contract.py`, `manifest.json`, `demo.ts`

Each `demo.ts` is a TypeScript **ES module** that imports from the `studio-wasm` library and runs
in the browser via Vite’s dev server. There is **no node backend** here — all compile/run happens
inside the browser using Pyodide.

---

## Prerequisites

- Node 18+ (or Bun 1.1+). We test with Node 20 LTS.
- A modern Chromium/Firefox/Safari (WebAssembly + Web Workers enabled).
- This repo checked out with the surrounding `studio-wasm` package.

> If you use `pnpm`, commands are equivalent (`pnpm i`, `pnpm dev`, etc.).

---

## Quickstart (dev server + dev preview page)

1) Install deps and start Vite:

```bash
# from repo root or packages/studio-wasm
npm install
npm run dev
# Vite will print a local URL (typically http://localhost:5173)

	2.	Open the dev preview page:

	•	Navigate to the URL Vite printed (e.g. http://localhost:5173).
	•	The preview UI lets you paste/edit a contract and run it.
	•	To run the prebuilt examples below, use one of the two methods:

Method A — import the demo module from the browser console

Open DevTools Console on the preview page and run:

// Counter demo
import('/examples/counter/demo.ts');
// Escrow demo
// import('/examples/escrow/demo.ts');

Vite serves TS modules directly; the demo will execute immediately and log progress/results.

Method B — add a script tag while dev server is running

Temporarily inject the demo into the preview page by adding this to the Console:

const s = document.createElement('script');
s.type = 'module';
s.src = '/examples/counter/demo.ts'; // or /examples/escrow/demo.ts
document.head.appendChild(s);

Reload the page later to remove it.

⸻

Expected output (Counter)

The Counter demo:
	•	boots Pyodide,
	•	compiles examples/counter/contract.py using the trimmed VM compiler,
	•	estimates gas,
	•	calls inc(),
	•	then calls get() to read the value,
	•	prints deterministic event logs.

You should see console messages like:

[studio-wasm] pyodide loaded v0.24.x
[demo] gas_upper_bound ~ 51_000
[demo] inc(): ok, gas_used=...
[demo] events: [{"name":"Inc","args":{"by":1}}]
[demo] get() -> 1


⸻

Expected output (Escrow)

The Escrow demo:
	•	compiles the contract,
	•	funds an escrow,
	•	attempts a release,
	•	prints the resulting event and state.

You should see logs similar to:

[demo] deposit(amount=100) ok
[demo] release(to=...) ok
[demo] events: [{"name":"Released","args":{"amount":100,"to":"...}}]


⸻

How it works (high level)
	•	studio-wasm hosts Pyodide and a trimmed Python VM package (py/vm_pkg/*) that mirrors the on-chain VM’s deterministic surface.
	•	The TypeScript Simulator API wraps the worker and the Py bridge:
	•	compileSource, compileIR, simulateCall, estimateGas, ephemeral state handles.
	•	The examples import these APIs and stitch together:
	•	manifest + source → compile,
	•	encoded call → run in the sandbox,
	•	decoded events + return values back to the browser.

See library entry points:
	•	src/api/simulator.ts
	•	src/api/compiler.ts
	•	src/api/state.ts

And the Py bridge (executed inside Pyodide):
	•	py/bridge/entry.py — compile_bytes, run_call, simulate_tx

⸻

Build & Preview (production)

# Build the library (ESM) and worker bundle
npm run build

# Preview the built site (static preview server)
npm run preview


⸻

Tests & E2E

# Unit tests (Vitest)
npm run test

# End-to-end (Playwright) — launches a real browser, boots Pyodide, runs counter demo
npm run e2e


⸻

Troubleshooting
	•	Blank page / 404 on demo import
Ensure npm run dev is running and that you used the leading slash:
import('/examples/counter/demo.ts').
	•	Pyodide fails to download
You might be offline or behind a proxy. Set the env var in .env.example to pin a CDN
or switch to vendored assets, then run scripts/fetch_pyodide.mjs:

# one-time fetch of pyodide.{js, wasm, data} into vendor/
node scripts/fetch_pyodide.mjs


	•	Determinism concerns
Examples run with the deterministic sandbox: no network, no time, no sys I/O. If you change the
contract to use disallowed features, the validator will raise ValidationError.
	•	Type errors in VSCode
Ensure the workspace TypeScript version matches the dev dependency and that vite types are available.

⸻

Security Notes
	•	All execution happens locally in your browser. No server-side signing or key material.
	•	Do not paste secrets into the examples or preview.
	•	For production usage integrate through studio-web and wallet-extension.

⸻

License

The examples inherit the repo’s license. See the root LICENSE file.

