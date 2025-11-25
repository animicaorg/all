# Quantum RNG App — Full Project Template

This template scaffolds a complete, batteries-included project that demonstrates **verifiable randomness** by **mixing the on-chain randomness beacon with quantum bytes** received through the platform capabilities. It includes:

- A **Python smart contract** (`QuantumRng`) that:
  - Commits/reveals to the randomness beacon.
  - Accepts quantum entropy receipts (via the Quantum capability).
  - Mixes beacon output and quantum bytes into a single, unbiased random value.
  - Exposes read methods/events for dapps and explorers.

- A **React + TypeScript dapp** that:
  - Connects to a wallet/extension.
  - Lets users trigger entropy requests and read the latest mixed randomness.
  - Shows transaction status and live head updates.

- **Dev tooling & scripts** to build, deploy, and interact—plus optional **CI** and **Docker** bits controlled by template variables.

---

## 1) Rendering the Template

You can render this template with either the project’s template engine CLI or via the Python module entry point.

### Option A — CLI (if installed)
```bash
animica-templates render templates/quantum-rng-app \
  --out ./quantum-rng-app \
  --vars templates/quantum-rng-app/variables.json

Option B — Python module

python -m templates.engine.cli render templates/quantum-rng-app \
  --out ./quantum-rng-app \
  --vars templates/quantum-rng-app/variables.json

If you omit --vars, the renderer will prompt interactively for all variables defined in variables.json.

Key variables you’ll be asked for (with sensible defaults):
	•	project_name, project_slug
	•	rpc_url (e.g. http://localhost:8545)
	•	chain_id (e.g. 1337 for devnet)
	•	contract_name (default: QuantumRng)
	•	dapp_port (default: 5173)
	•	Feature toggles like include_github_ci, include_docker

⸻

2) Resulting Layout

After rendering, you’ll see a structure like:

quantum-rng-app/
├─ contracts/
│  ├─ contract.py                 # QuantumRng contract
│  ├─ manifest.json               # ABI + metadata bundle
│  ├─ pyproject.toml / requirements.txt
│  ├─ Makefile                    # build / deploy helpers
│  └─ scripts/
│     ├─ build.py                 # compile → IR → package
│     ├─ deploy.py                # deploy via sdk/python
│     └─ call.py                  # quick local calls (read-only / test)
├─ dapp/
│  ├─ package.json / tsconfig.json / vite.config.ts
│  ├─ src/
│  │  ├─ services/                # provider + sdk glue
│  │  ├─ pages/                   # Home, Interact views
│  │  └─ components/              # Connect, TxStatus, etc.
│  └─ public/index.html
├─ .env.example                   # RPC_URL, CHAIN_ID, etc.
├─ README.md                      # project-local readme
└─ (optional) .github/workflows   # CI if enabled

The exact file set depends on the variables you choose when rendering.

⸻

3) Prerequisites
	•	Node.js ≥ 18.x and npm or pnpm (for the dapp)
	•	Python ≥ 3.10 (for contracts + scripts)
	•	Access to an Animica node RPC (devnet/testnet/mainnet)
	•	A funded deployer wallet (mnemonic or private key) for deployments

⸻

4) Configure Environment

Copy the root .env.example to .env and set your values:

cp .env.example .env

Typical values:

RPC_URL=http://localhost:8545
CHAIN_ID=1337
DEPLOYER_MNEMONIC="test test test test test test test test test test test junk"
CONTRACT_NAME=QuantumRng

(If your wallet uses a private key, the scaffolded scripts allow DEPLOYER_PRIVKEY=... as an alternative.)

⸻

5) Build & Deploy the Contract

From the contracts directory:

cd contracts
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build (compile → IR → package)
make build
# or: python scripts/build.py

# Deploy to the network set in .env
make deploy
# or: python scripts/deploy.py

The deploy script writes the deployed address to a small artifact file (e.g. ./build/deployed.json) so the dapp and scripts can reference it automatically.

⸻

6) Run the Dapp (Vite Dev Server)

In another terminal:

cd dapp
npm install
npm run dev

Open the printed local URL (default http://localhost:5173) in your browser. Connect your wallet/extension (devnet or local) and:
	•	Request Quantum Entropy: Enqueue a quantum job with traps. The node capabilities pick it up and deliver the result on the next block(s).
	•	Mix With Beacon: The contract mixes the delivered quantum bytes with the randomness beacon output.
	•	Read Latest Randomness: The UI polls/streams the latest mixed random value and renders it.

⸻

7) Contract Interface (high level)

The QuantumRng contract typically exposes:
	•	request_quantum_entropy(job_params) -> tx
	•	Enqueues a quantum task (with optional traps and size).
	•	Emits an event with the task id for tracking via explorer or logs.
	•	commit_to_round(round_id) -> tx
	•	Commits to a randomness round (deterministic window based on chain params).
	•	reveal_for_round(round_id, nonce) -> tx
	•	Reveals the commitment so VDF / beacon verification can include it.
	•	ingest_quantum_bytes(task_id, receipt_bytes) -> tx
	•	Called by the designated updater (often the task consumer) after the provider posts a verified receipt.
	•	latest_mix() -> bytes32
	•	Read-only view returning the most recent mixed randomness (beacon ⊕ quantum).
	•	Events
	•	QuantumTaskEnqueued(task_id, requester)
	•	QuantumBytesIngested(task_id, len)
	•	RandomnessMixed(round_id, mix)

Exact method names/ABI are in contracts/manifest.json after build.

⸻

8) Common Workflows

A) End-to-end local loop (devnet)
	1.	Start a local devnet (see repository tests/devnet or ops/docker/docker-compose.devnet.yml).
	2.	Deploy the contract (make deploy).
	3.	In the dapp:
	•	Connect wallet → select devnet chain.
	•	Click Request Entropy → wait for inclusion.
	•	After provider result is available → contract ingests the bytes.
	•	Read Latest Mix → confirm value changes block-to-block as expected.

B) Reset & redeploy

cd contracts
rm -rf build .venv
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make build && make deploy


⸻

9) CI (optional)

If include_github_ci=true, the template adds a minimal workflow that:
	•	Installs Python deps.
	•	Builds the contract package.
	•	(Optionally) runs dapp type checks and a fast lint step.
You’ll likely extend this to run integration tests against a devnet.

⸻

10) Docker (optional)

If include_docker=true, the dapp includes a small Dockerfile that:
	•	Builds the static site via Vite.
	•	Serves with nginx.
You can docker build -t quantum-rng-dapp . and run behind your preferred reverse proxy.

⸻

11) Troubleshooting
	•	Wallet can’t connect / chain mismatch
	•	Ensure CHAIN_ID in .env matches your node’s chain ID.
	•	Verify RPC_URL is reachable and CORS is configured for local dev.
	•	Deploy fails (insufficient funds)
	•	Fund the deployer address on the target network (faucet for dev/test).
	•	Entropy never arrives
	•	Confirm the AICF/Quantum provider is running and reachable (see provider templates).
	•	Check logs for the task_id emitted by QuantumTaskEnqueued.
	•	Mixed value doesn’t change
	•	Ensure both the beacon round is progressing and new quantum bytes are ingested.
	•	Re-check commit/reveal windows for correctness.

⸻

12) Next Steps & Customization
	•	Add ACL (Ownable/Roles) to restrict who can ingest_quantum_bytes.
	•	Emit domain-specific events when randomness is consumed by downstream contracts.
	•	Wire a service that externally monitors task completion and calls ingest_quantum_bytes automatically.
	•	Extend the dapp with history charts and explorer links to verify proofs.

⸻

13) Commands Reference

Contracts

make build            # compile → IR → package
make deploy           # deploy using env credentials
python scripts/call.py --fn latest_mix   # read-only calls

Dapp

npm run dev           # local dev server
npm run build         # production build
npm run preview       # preview built assets


⸻

14) Security Notes
	•	Treat quantum receipts as untrusted input until validated by the capability layer.
	•	Keep a replay-protection nonce if you store receipts on-chain.
	•	Consider circuit-breaker / pause mechanics if downstream consumers rely on timely updates.
	•	Always pin code hashes if using proxies/upgrades (see stdlib patterns).

⸻

Happy building! If you expand this template, consider contributing back improvements (docs, scripts, or UI polish) so others can benefit.
