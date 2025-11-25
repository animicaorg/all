# Fullstack Monorepo (Animica)

A batteries-included template that scaffolds a **production-grade monorepo** for building on the Animica stack. It can render a mix of frontends, services, off-chain agents, SDK starters, and infra—wired to a local devnet or a remote network with minimal configuration.

This template is configurable via `variables.json` (see below) so you can include only what you need.

---

## Contents

- [What you get](#what-you-get)
- [Prerequisites](#prerequisites)
- [Render this template](#render-this-template)
- [Variables & toggles](#variables--toggles)
- [Resulting layout](#resulting-layout)
- [Bring up a local devnet](#bring-up-a-local-devnet)
- [Develop each component](#develop-each-component)
  - [Dapp (React + TS)](#dapp-react--ts)
  - [AICF Provider (FastAPI)](#aicf-provider-fastapi)
  - [Oracle DA Poster](#oracle-da-poster)
  - [Indexer Lite](#indexer-lite)
  - [SDK Starters (TS / Python / Rust)](#sdk-starters-ts--python--rust)
- [CI/CD & Quality](#cicd--quality)
- [Security & Secrets](#security--secrets)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [License](#license)

---

## What you get

Depending on the toggles you choose at render time:

**Core**
- 📦 Monorepo structure with optional JS and Python workspaces
- 🧪 Unified test scaffolding and sensible lint/format configs
- 🧰 Scripts for building, running, and verifying

**Apps & Services**
- 🌐 **Dapp (React+TS)**: wallet connect, send tx, call contracts, live head.
- 🤖 **AICF Provider (FastAPI)**: worker + HTTP for AI/quantum jobs.
- 📡 **Oracle DA Poster**: periodic feed → Data Availability commit → on-chain oracle.
- 📊 **Indexer Lite**: RPC ingest, SQLite/ClickHouse schema, optional Grafana.

**SDK Starters**
- 🟦 **TypeScript**: sample deploy/call.
- 🐍 **Python**: sample deploy/call.
- 🦀 **Rust**: sample deploy/call.

**Infra**
- 🐳 **Docker Compose devnet** (node + miner + services + explorer + metrics).
- ☸️ **Kubernetes/Helm stubs** for devnet, ingress/TLS, and observability.

---

## Prerequisites

> You don’t need every tool for every subproject—install what you plan to use.

- **OS**: macOS, Linux, or WSL2
- **Git**: `git --version`
- **Node.js**: ≥ 18 (LTS recommended). Package manager: `pnpm` (recommended) or `npm`
- **Python**: ≥ 3.11 (per-service virtualenvs recommended)
- **Rust**: stable toolchain (for the Rust SDK starter)
- **Docker**: ≥ 24.x and **Docker Compose** plugin
- **Optional (K8s)**: kubectl, Helm, a cluster (k3d/kind/minikube/GKE/EKS/AKS)
- **Make**: GNU make (for convenience targets if present)

---

## Render this template

Use the template engine to generate a new monorepo:

```bash
# From the repository root that contains templates/engine/
python -m templates.engine.cli render \
  --template templates/fullstack-monorepo \
  --output ./animica-fullstack \
  --set project_slug=animica-fullstack \
        org_name=animica-labs \
        repo_name=animica-fullstack \
        chain_id=1337 \
        rpc_url=http://localhost:8545 \
        services_url=http://localhost:8787 \
        include_dapp=true \
        include_provider=true \
        include_oracle_poster=true \
        include_indexer=true \
        include_sdks=true \
        include_infra=true \
        enable_js_workspace=true \
        enable_py_workspace=true \
        init_git=true

Tip: You can also pass a JSON file with --vars-file my-vars.json. See variables.json for all available fields, defaults, and UI hints.

⸻

Variables & toggles

Key fields you may want to change:
	•	project_slug: Folder/repo-safe name (e.g., animica-fullstack).
	•	org_name: Your organization (used in docs/scopes).
	•	chain_id: Default chain id for apps/services (devnet default: 1337).
	•	rpc_url: Default RPC endpoint (e.g., http://localhost:8545).
	•	services_url: Studio-services proxy base URL (e.g., http://localhost:8787).

Feature switches:
	•	include_dapp: Include React+TS dapp scaffold.
	•	include_provider: Include AICF provider backend (FastAPI + worker).
	•	include_oracle_poster: Include DA oracle poster.
	•	include_indexer: Include indexer-lite (SQLite/ClickHouse).
	•	include_sdks: Include TS/Py/Rs SDK starter packages.
	•	include_infra: Include Docker/K8s/Helm devnet & observability.
	•	enable_js_workspace / enable_py_workspace: Create workspace manifests and common tooling.

If include_infra is false, bring_up_devnet will be disabled automatically.

⸻

Resulting layout

A typical rendered structure (varies with toggles):

animica-fullstack/
  README.md
  .gitignore
  .editorconfig
  .pre-commit-config.yaml

  apps/
    dapp-react-ts/                # optional: React dapp

  services/
    aicf-provider/                # optional: FastAPI provider (AI/quantum)

  tools/
    oracle-da-poster/             # optional: oracle DA poster

  indexer/
    indexer-lite/                 # optional: lightweight indexer

  sdks/                           # optional: language SDK starters
    sdk-ts-starter/
    sdk-py-starter/
    sdk-rs-starter/

  infra/                          # optional: devnet & observability
    devnet/
      docker-compose.yml
      .env.example
      Makefile
    k8s/
      # kustomize/helm stubs, ingress/TLS, observability

  # Workspace manifests when enabled
  package.json
  pnpm-workspace.yaml
  pyproject.toml

  # CI and linters (inherited from _common)
  .github/workflows/
  linters/


⸻

Bring up a local devnet

Requires include_infra=true at generation time and Docker installed.

cd infra/devnet
cp .env.example .env
# Tweak ports, image tags, or RPC URL if needed
make up            # or: docker compose up -d
./wait_for.sh      # optional helper to block until ready

Quick smoke:

# Explorer (if included) should show a head height that increases
curl -s http://localhost:8545/health | jq .

Stop & clean:

make down
make clean   # removes volumes (dbs, blobs, etc.)


⸻

Develop each component

Dapp (React + TS)

cd apps/dapp-react-ts
pnpm install
pnpm dev            # Vite dev server on http://localhost:5173
# Set RPC/services in .env if not using defaults

What’s inside:
	•	Wallet connect, chain info, send tx, call contracts
	•	Simple pages: Home / Contracts / Send
	•	Minimal provider abstraction and SDK glue

Build & preview:

pnpm build
pnpm preview

AICF Provider (FastAPI)

cd services/aicf-provider
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit AICF_* settings, RPC_URL, CHAIN_ID
uvicorn aicf_provider.server:app --reload --port 8787
# In another shell: run worker
python -m aicf_provider.worker

What’s inside:
	•	HTTP API (/health, /jobs, /results)
	•	Background worker loop to consume jobs and post proofs/results
	•	Quantum example handler (if enabled)

Oracle DA Poster

cd tools/oracle-da-poster
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Configure feed URLs, posting cadence, RPC_URL, SERVICES_URL
python -m oracle_poster.main

What’s inside:
	•	Pluggable feed adapters (feeds.py)
	•	DA client (da_client.py) to post blobs & commitments
	•	Tx client (tx_client.py) to update on-chain oracle contracts
	•	Systemd unit & Dockerfile included

Indexer Lite

SQLite quick start

cd indexer/indexer-lite
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
sqlite3 ./db/sqlite/indexer.db < db/sqlite/schema.sql
python -m indexer.ingest

ClickHouse (optional)
	•	Run ClickHouse (docker or managed)
	•	Apply db/clickhouse/schema.sql
	•	Set DB_URL to ClickHouse in .env
	•	Start ingest: python -m indexer.ingest

Metrics & dashboard:
	•	If infra/observability included, the Grafana dashboard JSON is under indexer/dashboards/.

SDK Starters (TS / Python / Rust)

TypeScript

cd sdks/sdk-ts-starter
pnpm install
# Update RPC_URL/CHAIN_ID in env or script args
pnpm ts-node src/examples/deploy_counter.ts

Python

cd sdks/sdk-py-starter
python -m venv .venv && source .venv/bin/activate
pip install -e .
python examples/deploy_counter.py

Rust

cd sdks/sdk-rs-starter
cargo build
cargo run --example deploy_counter


⸻

CI/CD & Quality

This template can include common CI (GitHub Actions) and linters from _common:
	•	Linters/Formatters:
	•	JS/TS: ESLint, Prettier
	•	Python: Ruff, (optional) mypy
	•	Caching: Node and Python caches keyed by lockfiles
	•	Workspaces:
	•	JS: pnpm -r to run across packages
	•	Python: per-project virtualenvs (or uv/poetry if you prefer to adapt)

Recommended CI jobs:
	•	lint — run linters across all enabled subprojects
	•	test — unit/integration tests where applicable
	•	build — dapp build, service Docker images (if desired)
	•	smoke — spin up devnet compose and run a minimal RPC sanity check

Be sure to add repository secrets for any deployments (e.g., REGISTRY_USER, REGISTRY_TOKEN, CLOUD_KUBE_CONFIG, etc.).

⸻

Security & Secrets
	•	Never commit real keys; use .env files and secret managers (GH Secrets, Vault, SSM).
	•	Rotate keys used by oracle/provider regularly.
	•	Consider allowlists for outbound calls in providers.
	•	Lock down CORS for public-facing APIs.
	•	Pin container images or use verified digests for prod.
	•	Review alerts and dashboards under infra to catch anomalies early.

⸻

Troubleshooting

Devnet won’t start
	•	Check Docker RAM/CPU allocation.
	•	Confirm ports are free (lsof -i :8545, etc.).
	•	Run docker compose logs -f in infra/devnet.

Dapp can’t connect
	•	Verify RPC_URL in .env matches the devnet.
	•	Browser mixed-content: use HTTPS dev proxy or disable strict if testing.

Python packages fail to build
	•	Ensure Python 3.11+ and build-essential (Linux).
	•	Recreate venv after switching Python versions.

Indexer lagging
	•	RPC endpoint throttling: increase rate limits or run a local node.
	•	For ClickHouse, validate credentials and network connectivity.

⸻

FAQ

Q: Can I exclude the dapp/provider/oracle/indexer and add later?
A: Yes. You can re-render a smaller template into a new directory and copy the subproject over, or add a new subproject from its standalone template.

Q: How do I point everything to a remote testnet?
A: Set rpc_url and services_url at render time, or change the relevant .env files in each subproject to the remote endpoints.

Q: How do I deploy to Kubernetes?
A: Use the stubs under infra/k8s or the Helm chart skeleton. Provide ingress/TLS and external-dns values. Then helm upgrade --install with your values.

Q: Can I use npm or yarn instead of pnpm?
A: Yes—adjust commands accordingly. We ship pnpm for performance and workspace UX.

⸻

License

Default license is chosen at render time (e.g., Apache-2.0). Make sure it aligns with your organization’s policy.

⸻

Next steps
	1.	Render your monorepo with the toggles you need.
	2.	Bring up the local devnet (infra/devnet).
	3.	Run the dapp and iterate on contracts and services.
	4.	Wire CI, observability, and deploy to a staging cluster.
	5.	Ship 🚀

