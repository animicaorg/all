# Studio Web (Animica Studio)

A web IDE to **edit → simulate → deploy → verify** Python-VM contracts — plus panels for **AI/Quantum jobs, Data Availability (DA), and the Randomness beacon**. Everything runs client-side where possible (compile/simulate via WASM), with secure calls to your local node and the optional studio-services proxy.

---

## Features

- **Project IDE**
  - File tree + Monaco editor with diagnostics and formatting
  - ABI/manifest helpers and artifact preview
- **In-browser compiler & simulator**
  - Powered by **studio-wasm** (Pyodide + trimmed `vm_py`)
  - Deterministic execution, gas estimate, event logs
- **Deploy & Verify**
  - Connects to **wallet-extension** (MV3) for post-quantum signing (Dilithium3/SPHINCS+)
  - Sends signed CBOR txs to your node via RPC
  - Optional **studio-services** backend for `/deploy`, `/verify`, `/faucet`, `/artifacts`
- **AI / Quantum**
  - Enqueue jobs (AICF), view results; aligns with `capabilities/` and `aicf/` flows
- **Data Availability (DA)**
  - Pin blobs, view NMT commitments, verify light proofs
- **Randomness**
  - Commit/reveal helpers and beacon browser
- **Explorer-lite**
  - Blocks, txs, addresses, events; PoIES breakdown hooks
- **First-class SDK integration**
  - Uses `@animica/sdk` (TypeScript) throughout
  - Works with `wallet-extension` via `window.animica` provider

---

## Architecture

[studio-web (React, Vite)]
├── In-browser compile/simulate → studio-wasm (Pyodide/WASM)
├── Wallet connect & sign       → wallet-extension (PQ keys)
├── Node RPC (HTTP/WS)          → rpc/ (JSON-RPC + WS)
├── Optional services           → studio-services/ (deploy, verify, faucet, artifacts)
└── SDK                         → @animica/sdk (TS)

- **No server-side signing.** Private keys never leave the browser (wallet-extension).
- **CORS-safe by design.** Use the included dev proxy during local development.

---

## Quickstart (Devnet)

### 0) Prerequisites

- **Node.js** ≥ 18 (LTS recommended)
- **pnpm** ≥ 8 (or npm/yarn; examples use pnpm)
- Running **devnet node** (Animica repo, `core/ + rpc/`), or attach to an existing node
- (Optional) **studio-services** running locally for deploy/verify/faucet/artifacts
- (Optional) **wallet-extension** installed in your browser (MV3 build)

> Tip: The repo includes a cross-language **SDK test harness** you can use to spin up a devnet and run smoke tests. See `sdk/test-harness/README.md`.

### 1) Environment

Create `.env.local` (or copy `.env.example`) and set:

```ini
# Node RPC (HTTP & WS). For devnet defaults:
VITE_RPC_URL=http://localhost:8545
VITE_CHAIN_ID=1

# Optional studio-services (deploy/verify/faucet/artifacts)
VITE_SERVICES_URL=http://localhost:8080

If you run via the dev proxy (recommended in dev), you may point the UI to http://localhost:5173 and let the proxy forward /rpc and /services.

2) Start your node & (optional) services
	•	Node (example): start your Python node with RPC + WS enabled
	•	studio-services (optional):

cd studio-services
make dev   # or: uvicorn studio_services.main:app --reload --port 8080



Populate example artifacts & a sample deploy (optional):

# (in studio-services/)
python scripts/load_fixtures.py --services http://localhost:8080

3) Run the dev proxy (CORS-friendly)

In another shell:

cd studio-web
pnpm i
node scripts/dev_proxy.mjs \
  --rpc http://localhost:8545 \
  --services http://localhost:8080 \
  --port 5173

This starts:
	•	Vite dev server at http://localhost:5173
	•	Proxied routes:
	•	/rpc/* → Node RPC
	•	/services/* → studio-services

4) Launch Studio

Open http://localhost:5173 in a supported browser (Chrome/Edge/Firefox recent).
	•	Load a template (Counter / Escrow / AI Agent / Quantum RNG)
	•	Simulate in-browser (no node required) — see gas & logs
	•	Connect wallet (wallet-extension) to sign and deploy
	•	Verify source against on-chain code via studio-services

⸻

Scripts

From the studio-web directory:

pnpm dev        # runs the Vite dev server
pnpm build      # production build to dist/
pnpm preview    # preview the built bundle
pnpm test       # unit tests (vitest)
pnpm e2e        # Playwright E2E (see below)

E2E Gate

A comprehensive E2E test proves: scaffold → simulate → connect → deploy → verify on devnet.

pnpm e2e
# Executes: test/e2e/deploy_template.spec.ts

Ensure your node and studio-services are reachable at the URLs in .env.local (or use the dev proxy as above).

⸻

Configuration

Var	Description	Default
VITE_RPC_URL	Node JSON-RPC base URL (HTTP)	http://localhost:8545
VITE_CHAIN_ID	Chain ID (must match node)	1
VITE_SERVICES_URL	studio-services base URL	http://localhost:8080

Additional tunables live in src/state/network.ts and various service adapters:
	•	src/services/provider.ts — detects window.animica (wallet-extension)
	•	src/services/rpc.ts — thin wrapper over @animica/sdk RPC
	•	src/services/wasm.ts — load and cache studio-wasm
	•	src/services/servicesApi.ts — deploy/verify/faucet/artifacts via studio-services

⸻

Folder Guide
	•	src/pages/Edit/* — IDE views: compile, simulate, events, artifacts, DA
	•	src/pages/Deploy/* — build → sign → send → receipt
	•	src/pages/Verify/* — source/manifest upload, job status/result
	•	src/pages/AI/* & src/pages/Quantum/* — AICF flows
	•	src/pages/Randomness/* — beacon tools
	•	src/pages/Explorer/* — blocks/tx/address explorer-lite
	•	src/services/* — adapters to wallet/provider, RPC, services, wasm
	•	src/hooks/* — typed hooks for core behaviors
	•	src/fixtures/templates/* — project templates surfaced in the UI
	•	test/unit/*, test/e2e/* — vitest + Playwright tests

⸻

Security Notes
	•	Keys never leave the browser. Signing is performed by wallet-extension via MV3 service worker / web crypto / WASM PQ paths.
	•	CBOR domain separation and chainId enforcement match the node spec.
	•	CORS/Rate limit: Use the dev proxy locally; in production, allowlist only required origins on your node and studio-services.

⸻

Troubleshooting
	•	CORS errors: use scripts/dev_proxy.mjs or correctly configure CORS on node and studio-services.
	•	Chain ID mismatch: set VITE_CHAIN_ID to match node; the wallet will also enforce domain separation.
	•	Wallet not detected: ensure window.animica provider is injected (install/run wallet-extension).
	•	Verify fails: confirm the node is fully synced to the tx’s block; studio-services must reach the node and compile deterministically.
	•	Pyodide load issues: see network tab; CDN/offline settings are handled by studio-wasm loader.

⸻

License

This package is part of the Animica project and inherits the repo’s license. See LICENSE at the repository root.

