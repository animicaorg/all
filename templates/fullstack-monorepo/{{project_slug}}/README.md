# {{project_slug}} — Animica Full-Stack Monorepo Template

A batteries-included, multi-language workspace for building on Animica: smart contracts (Python VM), dapps (React/TS), off-chain services (FastAPI), indexers, AICF providers, and ops (Docker/K8s/Helm). This template is intentionally **modular**—you can start tiny and grow into a production setup without throwing anything away.

---

## Why this template?

- **One repo, many apps**: contracts, dapps, services, providers, indexers.
- **Polyglot dev**: TypeScript, Python, and Rust side-by-side with shared schemas.
- **Determinism & security**: opinionated linting and contract rules (Animica Python-VM subset).
- **Dev → prod continuity**: local devnet, metrics/logging, CI hooks, and deployment manifests.
- **Scaffold friendly**: generate new components from curated templates with consistent layouts.

---

## What’s inside (suggested layout)

> The template itself only creates this README and a minimal skeleton; you’ll **generate** the rest using the component templates listed below. A typical structure ends up like:

{{project_slug}}/
├─ apps/
│  ├─ dapp/                  # React + @animica/sdk starter (from dapp-react-ts)
│  ├─ studio-services/       # Optional FastAPI proxy (from provider templates)
│  ├─ oracle-da-poster/      # DA-backed oracle poster (from oracle-da-poster)
│  ├─ indexer-lite/          # Light indexer + dashboards (from indexer-lite)
│  └─ aicf-provider/         # AI/Quantum provider skeleton (from provider-aicf-fastapi)
├─ contracts/
│  ├─ token/                 # Example Animica-20 token (from contract-python-basic/workspace)
│  ├─ escrow/                # Escrow example
│  └─ ai_agent/              # AICF-consuming contract
├─ packages/
│  ├─ sdk-ts/                # TS starter using @animica/sdk (from sdk-ts-starter)
│  ├─ sdk-py/                # Python starter using omni_sdk (from sdk-py-starter)
│  └─ sdk-rs/                # Rust starter (from sdk-rs-starter)
├─ infra/
│  ├─ docker/                # Compose files to run a local devnet + observability
│  ├─ k8s/helm/              # Production-minded Kubernetes & Helm charts
│  └─ scripts/               # DX utilities: wait_for.sh, smoke checks, etc.
├─ tools/                    # Monorepo tooling, schema sync, codegen hooks
├─ .gitignore
└─ README.md

---

## Prerequisites

- **Node.js** ≥ 20 and **pnpm** ≥ 9 (JS/TS apps and tooling)
- **Python** ≥ 3.11 (contracts, services, scripts)
- **Rust** (optional; for the Rust SDK starter)
- **Docker** & **docker compose** (local devnet and observability)
- **jq**, **openssl** (common scripts)

> For Python workflows we recommend **uv** (fast package manager), but `pip` works too.

---

## Quick start

1. **Create the monorepo directory (you’re here).**
2. **Generate components** from the curated templates:
   - Contracts (single): `contract-python-basic`
   - Contracts (workspace with token/escrow/ai_agent): `contract-python-workspace`
   - Dapp: `dapp-react-ts`
   - Providers: `provider-aicf-fastapi`
   - Oracle poster: `oracle-da-poster`
   - Indexer: `indexer-lite`
   - SDK starters: `sdk-ts-starter`, `sdk-py-starter`, `sdk-rs-starter`

   Using the Animica templates engine:

   ```bash
   # Example: generate a React dapp into apps/dapp
   python -m templates.engine.cli \
     new dapp-react-ts \
     --out ./apps/dapp \
     --vars '{"project_slug":"dapp","name":"Animica Dapp"}'

   # Example: generate a contracts workspace into ./contracts
   python -m templates.engine.cli \
     new contract-python-workspace \
     --out ./contracts \
     --vars '{"project_slug":"contracts"}'

	3.	Install dependencies
	•	TypeScript workspace(s):

pnpm install
pnpm -r build


	•	Python (example for app/service folders):

# Using uv (recommended)
uv venv
uv pip install -e ./apps/* -e ./packages/* -e ./contracts || true

# Or with pip
python -m venv .venv && source .venv/bin/activate
pip install -e ./apps/* -e ./packages/* -e ./contracts || true


	4.	Point to a node
	•	Local devnet (recommended): run a node + RPC from your Animica ops stack (e.g., ops/docker/docker-compose.devnet.yml) or connect to an existing testnet.
	•	Set envs where applicable:

RPC_URL=http://localhost:8545
CHAIN_ID=1            # Or your dev chain


	5.	Run things
	•	Dapp:

cd apps/dapp
pnpm dev


	•	Oracle poster / provider / indexer:

# Example (FastAPI provider)
cd apps/aicf-provider
uv pip install -e .
uv run python -m aicf_provider.server


	6.	Deploy a sample contract
	•	Using the Python SDK starter:

cd packages/sdk-py
uv run python examples/deploy_counter.py --rpc $RPC_URL --chain-id $CHAIN_ID



⸻

Environment variables (common)

Variable	Where	Description
RPC_URL	apps, packages, services	HTTP endpoint for node JSON-RPC
CHAIN_ID	apps, contracts, services	Chain id (matches your genesis/params)
SERVICES_URL	studio-services (optional)	Deploy/verify proxy base URL
WS_URL	apps needing subscriptions	WebSocket endpoint for newHeads/pendingTxs

Each generated component ships its own .env.example—copy it to .env and fill in values.

⸻

Recommended workflows

Contracts
	•	Author contracts in contracts/<name>/contract.py with the VM Python deterministic subset.
	•	Build/package using the generated scripts (build.py) which produce IR + manifests.
	•	Deploy via SDK (deploy_counter.py) or the sample scripts in each contract package.
	•	Verify via studio-services or directly against on-chain code hash.

Dapp
	•	Use @animica/sdk for RPC, contracts, events, and DA/AICF/Randomness helpers.
	•	Inject the wallet extension (window.animica) in browsers or use a local signer in dev.

Providers / Oracle / Indexer
	•	Provider: implement AICF job handlers (AI/Quantum) and heartbeat endpoints.
	•	Oracle poster: fetch feeds, post blob to DA, update on-chain contract pointer.
	•	Indexer: ingest headers/blocks/tx/proofs, persist summaries, expose dashboards.

⸻

DX & Quality
	•	Formatting/Linting
	•	TS: ESLint + Prettier (configs included in generated projects)
	•	Python: ruff + mypy (configs included)
	•	Rust: cargo fmt, cargo clippy
	•	Testing
	•	TS: Vitest/Jest depending on starter
	•	Python: pytest
	•	Rust: cargo test
	•	Scripts
	•	Each component ships Makefile or npm/pnpm scripts (format, lint, test, dev).
	•	Schemas
	•	ABI/OpenRPC schemas are kept in sync across SDKs; codegen is available from sdk/codegen.

⸻

Local devnet & observability (optional)

If you’re running the Animica devnet locally:
	•	Start services (node, miner, services, explorer, metrics) via Docker Compose.
	•	Dashboards (Grafana) show PoIES/Θ, mempool, P2P, DA, AICF, and randomness health.
	•	Logs (Loki/Promtail) + alerts (Alertmanager) complete the feedback loop.

You can point this monorepo’s apps at that devnet using RPC_URL and (optionally) SERVICES_URL.

⸻

Security notes
	•	Never commit real mnemonics, API keys, or private certs. Use .env and secrets managers.
	•	Contracts must follow the Animica determinism rules; avoid forbidden imports and I/O.
	•	Providers should sandbox untrusted workloads and implement rate-limits & attestations.

⸻

FAQ

Q: Can I skip Python or Rust?
Yes. Generate only the components you need. The workspace is modular.

Q: Do I need the wallet extension?
For browser dapps, yes (or a mock signer in dev). For scripts/SDKs, local signers are fine.

Q: How do I add another contract/service later?
Re-run the templates engine with a new output path inside contracts/ or apps/.

⸻

Next steps
	•	Generate your first contract: token or escrow.
	•	Spin up a local devnet, deploy, and watch events from the dapp.
	•	Add an oracle poster or AICF provider when you need off-chain capabilities.

Happy building 💫
