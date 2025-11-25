# Tests — Scope, How to Run (Fast vs Full), and CI Matrix

This repository contains multiple packages (SDKs, services, simulators, and UIs). Each package ships its own test suite. This document explains what exists, how to run **fast** vs **full** test passes locally, and how CI ties it all together.

---

## Test Scope by Package

**SDKs**
- **Python**: `sdk/python/tests/` — wallet/signing, address, tx encode/send, contracts client/codegen, DA/AICF/Randomness clients.
- **TypeScript**: `sdk/typescript/test/` — utils, RPC, wallet/signing (WASM-gated), tx encode/send, contracts client/codegen.
- **Rust**: `sdk/rust/tests/` — wallet/signing (feature-gated), tx encode/send, contracts client/events; plus examples.

**Simulator (WebAssembly)**
- **studio-wasm**: `studio-wasm/test/` (unit) & `studio-wasm/test/e2e/` (Playwright) — Pyodide boot, compile/run contracts, ABI helpers.

**Services**
- **studio-services**: `studio-services/tests/` — API routes (deploy, preflight/simulate, verify, faucet, artifacts), security, rate limits.

**Web Apps**
- **studio-web**: `studio-web/test/` (unit) & `studio-web/test/e2e/` (Playwright) — scaffold → simulate → deploy → verify flows.
- **explorer-web**: `explorer-web/test/` (unit) & `explorer-web/test/e2e/` (Playwright) — WS newHeads stream, charts (Γ/fairness/mix), lists.

**Cross-Language E2E Harness**
- **sdk/test-harness/** — spin/attach to a devnet, then run end-to-end flows using Python/TS/Rust SDKs.

---

## Fast vs Full Runs

### Fast (local, no external deps)
Runs unit tests and lightweight integration with mocks. Skips E2E and live network calls.

```bash
# Python (SDK + Services)
(cd sdk/python && pytest -q)
(cd studio-services && pytest -q)

# TypeScript (SDK + WASM + Web apps)
(cd sdk/typescript && npm ci && npm test)
(cd studio-wasm && npm ci && npm run test)
(cd studio-web && npm ci && npm run test)
(cd explorer-web && npm ci && npm run test)

# Rust
(cd sdk/rust && cargo test --all-features)

Tips:
	•	To filter Python tests: pytest -k "wallet or encode" -q
	•	To run a single TS test: npm test -- -t "wallet sign"
	•	To run Rust a specific test: cargo test contract_codegen::

Full (local or CI, includes E2E)

Runs everything, including Playwright browser tests and cross-language E2E against a devnet and (optionally) running services.

1) Start a devnet and services

You can either spin up a devnet or attach to an existing one.

# Option A: spin up devnet (foreground or background)
python sdk/test-harness/devnet_env.py --start &

# Start studio-services (auto-reload off for stability; or `make dev` for live reload)
(cd studio-services && make run) &
# or: (cd studio-services && python -m studio_services.main)

2) Export standard env

export RPC_URL="http://127.0.0.1:8545"
export WS_URL="ws://127.0.0.1:8546"
export CHAIN_ID="1337"
export SERVICES_URL="http://127.0.0.1:8787"
# optional for tests that post blobs / AICF:
export DA_URL="${RPC_URL}"
export AICF_URL="${RPC_URL}"

3) Install Playwright browsers (once per machine)

npx playwright install --with-deps

4) Run E2E suites

# Simulator (Pyodide) E2E
(cd studio-wasm && npm run e2e)

# Studio Web E2E
(cd studio-web && npm run e2e)

# Explorer Web E2E
(cd explorer-web && npm run e2e)

# Cross-language harness via SDKs
python sdk/test-harness/run_e2e_py.py
node sdk/test-harness/run_e2e_ts.mjs
bash sdk/test-harness/run_e2e_rs.sh

If your devnet or services are already running elsewhere, just point the env vars at them.

⸻

Common Environment Variables

Variable	Purpose	Default (suggested)
RPC_URL	Node HTTP JSON-RPC	http://127.0.0.1:8545
WS_URL	Node WebSocket endpoint	ws://127.0.0.1:8546
CHAIN_ID	Chain ID for signing/domain separation	1337
SERVICES_URL	studio-services base URL	http://127.0.0.1:8787
DA_URL	Data-availability RPC	falls back to RPC_URL
AICF_URL	AICF RPC	falls back to RPC_URL
PLAYWRIGHT_BROWSERS_PATH	Cache path	(Playwright default)


⸻

Notes on Test Tags / Filtering
	•	Python tests are organized to be runnable with or without a live node. Use -k to include/exclude:
	•	-k "not e2e" to skip expensive flows
	•	-k "rpc_roundtrip" to run a single flow
	•	TS tests use Vitest; E2E use Playwright. Unit tests mock network by default.
	•	Rust tests avoid network unless explicitly running the example-based E2E scripts.

⸻

CI Matrix (Example)

The repository includes per-package workflows (see sdk/test-harness/ci_matrix.yml for a multi-lang template). A typical GitHub Actions layout:

Job	OS	Runtime	What runs
sdk-python	ubuntu-latest	Python 3.11, 3.12	pytest (fast); optional E2E behind flag
sdk-typescript	ubuntu-latest	Node 18, 20, 22	npm test
sdk-rust	ubuntu-latest	Rust stable (and/or beta)	cargo test --all-features
studio-wasm-unit	ubuntu-latest	Node 20	npm test
studio-wasm-e2e	ubuntu-latest	Node 20 + Playwright (chromium)	npm run e2e
studio-services	ubuntu-latest	Python 3.11	pytest + coverage
studio-web-unit	ubuntu-latest	Node 20	npm test
studio-web-e2e	ubuntu-latest	Node 20 + Playwright (chromium)	npm run e2e (needs RPC/WS/services)
explorer-web-unit	ubuntu-latest	Node 20	npm test
explorer-web-e2e	ubuntu-latest	Node 20 + Playwright (chromium)	npm run e2e (needs RPC/WS)
e2e-harness	ubuntu-latest	Python + Node + Rust	Run devnet_env.py, then run_e2e_{py,ts,rs}

Coordination / dependencies
	•	E2E jobs can:
	•	start a lightweight devnet and services within the job,
	•	or depend on a “devnet” job and consume its exposed endpoints (via outputs or job-level artifacts like a TCP tunnel).
	•	Cache pip/npm/cargo for speed.
	•	For Playwright on Linux: run npx playwright install --with-deps before tests.

Gating strategy
	•	PRs: run fast matrix (unit + lightweight integration).
	•	Main branch: run full matrix (includes E2E).
	•	Nightly: optional long-running/regression (benchmarks, broader browser matrix).

⸻

Troubleshooting
	•	Playwright cannot launch: ensure npx playwright install --with-deps has run in CI image; confirm DISPLAY not required (headless).
	•	CORS errors in web E2E: use local scripts/dev_proxy.mjs in studio-web / explorer-web during manual dev, or ensure services allow the test origin.
	•	WS connection fails: verify WS_URL and that the proxy/CDN allows WebSocket upgrade.
	•	Signature/Chain mismatch: confirm CHAIN_ID in env matches the devnet.

⸻

TL;DR
	•	Fast: run unit tests in each package (no external deps).
	•	Full: start devnet + services → run Playwright E2E + cross-language harness.
	•	Use the provided ci_matrix.yml as a reference to wire up a robust multi-language CI.

