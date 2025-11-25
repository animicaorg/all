# Studio Services

FastAPI microservice used by **studio-web** and CLI tools to **deploy**, **verify**, **simulate**, and **host artifacts** for Animica contracts — without ever handling user private keys.

> **Security model:** This service **never signs** on behalf of users. All transactions are signed client-side (wallet-extension, SDKs, or cold tooling). The service validates and relays, compiles for verification, rate-limits, and exposes read-only helpers.

---

## What this provides

- **Deploy relay**: Accepts **already-signed CBOR** txs and relays to a node RPC; returns tx hash and tracks receipt.  
- **Preflight simulate**: Offline compile + dry-run to estimate gas and basic correctness before sending.  
- **Source verification**: Re-compile (using `vm_py`) and **match code hash** against on-chain; stores & serves results.  
- **Artifacts storage**: Content-addressed artifact blobs (ABI, manifests) with deterministic IDs; optional S3 backend.  
- **Faucet (optional/dev)**: Drip small test funds under strict **API-key + rate limits**.  
- **OpenAPI** docs, **Prometheus** metrics, structured logging, and SQLite storage by default.

### Non-goals

- Key custody or server-side signing (explicitly out of scope).  
- Mutating node state beyond relaying a user-signed transaction.  
- Running consensus or execution — talk to your node via RPC.

---

## Architecture (at a glance)

client (studio-web / SDKs)
│
▼
studio-services  ──► node RPC (relay, state queries)
│
├─ adapters/vm_compile (vm_py)
├─ adapters/light_verify (sdk/python)
├─ storage/sqlite + fs (or s3)
└─ security: CORS, API-key, rate limits

Key modules:
- `adapters/node_rpc.py` — JSON-RPC client (send tx, head/receipt).  
- `adapters/vm_compile.py` — load `vm_py` to compile & hash contracts for verification and simulate.  
- `adapters/light_verify.py` — minimal header/DA verification via Python SDK.  
- `services/*.py` — deploy, verify, artifacts, faucet, simulate orchestration.  
- `routers/*.py` — FastAPI endpoints.  
- `tasks/*.py` — verification queue worker, faucet pacer.  
- `storage/*` — SQLite schema & content-addressed FS/S3.

---

## Security model

- **No server-side signing**: Users sign in browser/CLI. Service rejects unsigned txs.  
- **Origin allowlist + strict CORS**: Only configured frontends may call from browsers.  
- **API keys**: For sensitive endpoints (e.g., faucet) and higher rate tiers.  
- **Rate limiting**: Token buckets per-IP and per-key; distinct per-route budgets.  
- **Deterministic content storage**: Artifacts are stored by content hash; immutability by default.  
- **Verification integrity**: Rebuilds code hash from submitted source/manifest; compares with chain.

---

## Requirements

- Python **3.11+**  
- SQLite (default) or optional **S3** compatible storage  
- A reachable Animica **node RPC** (devnet/testnet/local)  
- (Optional) `make`, `uvicorn`, `pipx`/`uv`

---

## Quickstart (local dev)

```bash
# From repo root
cd studio-services

# 1) Create and populate a virtual env (using uv or pip)
pipx run uv venv .venv && source .venv/bin/activate
uv pip install -r studio-services/requirements.txt

# 2) Copy and edit env
cp .env.example .env
# Set RPC_URL, CHAIN_ID, allowed origins, rate limits, optional FAUCET_KEY, STORAGE_DIR

# 3) Initialize DB schema
python -m studio_services.cli migrate

# 4) (Optional) create an API key for faucet/tests
python -m studio_services.cli create-api-key

# 5) Run server (auto-reload)
uvicorn studio_services.main:app --reload
# or: make dev

OpenAPI docs will be at: http://127.0.0.1:8000/docs
Health endpoints: /healthz, /readyz, /version
Metrics: /metrics

Quickstart (Docker)

# Build and run
make docker
# or:
docker build -t animica/studio-services .
docker run --rm -p 8000:8000 --env-file .env -v $(pwd)/.data:/data animica/studio-services


⸻

Configuration

.env.example shows the available settings:
	•	RPC_URL — Node RPC endpoint (e.g., http://127.0.0.1:8545 or your devnet)
	•	CHAIN_ID — Expected chain ID; mismatches rejected on deploy/verify
	•	ALLOWED_ORIGINS — CSV for CORS allowlist (e.g., http://localhost:5173)
	•	RATE_LIMITS — JSON or simple tokens/sec settings per route
	•	FAUCET_KEY — Optional private key for dev/test drip; if unset, faucet is disabled
	•	STORAGE_DIR — Local storage path for artifacts (default: ./.data)
	•	S3_* — Optional S3-compatible backend settings

⸻

Endpoints (summary)

Method	Path	Purpose
GET	/healthz	Liveness
GET	/readyz	Readiness
GET	/version	Version + git describe
GET	/metrics	Prometheus metrics
POST	/deploy	Relay signed CBOR tx; returns txHash
POST	/preflight	Compile + simulate (no state write)
POST	/verify	Queue verification job (source + manifest)
GET	/verify/{address}	Latest verification result by address
GET	/verify/{txHash}	Verification result linked to a tx
POST	/artifacts	Put artifact blob + metadata (content-addressed)
GET	/artifacts/{id}	Fetch artifact by content id
GET	/address/{addr}/artifacts	List artifacts linked to address
POST	/faucet/drip	(Optional) Drip test funds (API-key + rate-limit)
POST	/simulate	Compile + run single call locally (readonly)

OpenAPI with examples is served at /docs. Schema at /openapi.json.

⸻

Example workflows

1) Deploy a contract (client-signed)

Sign a CBOR tx client-side (wallet/SDK), then relay:

# Signed tx file (CBOR) produced by wallet/SDK
curl -X POST http://127.0.0.1:8000/deploy \
  -H 'Content-Type: application/cbor' \
  --data-binary @fixtures/counter/deploy_signed_tx.cbor

Response:

{"txHash":"0xabc123...","submitted":true}

2) Preflight simulation

curl -X POST http://127.0.0.1:8000/preflight \
  -H 'Content-Type: application/json' \
  -d '{
        "manifest": {...},
        "source": "def inc(): ...",
        "entry": "inc",
        "args": {}
      }'

Response includes gas estimate and return data:

{"ok":true,"gasUsed":12345,"events":[...],"returnValue":null}

3) Verify source

curl -X POST http://127.0.0.1:8000/verify \
  -H 'Content-Type: application/json' \
  -d '{
        "address":"anim1....",
        "manifest": {...},
        "source": "def inc(): ...",
        "codeHash": "0x...",
        "linkArtifacts": true
      }'

Then poll:

curl http://127.0.0.1:8000/verify/anim1...
# => {"status":"passed","codeHash":"0x...","matched":true,"artifactId":"art_..."}

4) Artifacts

# Put an artifact (ABI/manifest/metadata)
curl -X POST http://127.0.0.1:8000/artifacts \
  -H 'Content-Type: application/json' \
  -d '{"kind":"abi","content":{"functions":[...]}}'

# Fetch
curl http://127.0.0.1:8000/artifacts/art_abcdef

# List by address
curl http://127.0.0.1:8000/address/anim1.../artifacts

5) Faucet (dev/test only)

API_KEY="your-created-key"
curl -X POST http://127.0.0.1:8000/faucet/drip \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"to":"anim1...","amount":"100000"}'


⸻

Observability
	•	Logs: Structured JSON via structlog, with request IDs and durations.
	•	Metrics: /metrics exports Prometheus counters/histograms:
	•	request durations, rate-limit hits/misses
	•	deploy/verify/simulate successes & failures
	•	faucet droplets, artifacts stored/served
	•	Health: /healthz, /readyz, and /version.

⸻

Data & storage
	•	SQLite for verification jobs, artifacts metadata, rate counters (storage/schema.sql).
	•	File store in STORAGE_DIR (content-addressed). Optional S3 backend.
	•	Deterministic IDs via storage/ids.py (hash of manifest/code/ABI/address).

⸻

Background workers
	•	Verification queue: tasks/worker.py rebuilds code hash using vm_py and updates results.
	•	Faucet pacer: tasks/faucet_pacer.py throttles drips by policy.
	•	Launch/stop via tasks/scheduler.py (wired in app startup/shutdown).

⸻

Running tests

make test
# or:
pytest -q


⸻

Tips & troubleshooting
	•	CORS errors: ensure ALLOWED_ORIGINS includes your frontend origin(s).
	•	Chain mismatch: set CHAIN_ID to the node’s chain; mismatches are hard errors.
	•	Faucet disabled: leave FAUCET_KEY unset in prod; set for dev/test only.
	•	S3 backend: configure S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY.

⸻

Licenses
	•	See LICENCE-THIRD-PARTY.md for third-party notices.
	•	This package follows the repository root license.

⸻

Roadmap / Extensibility
	•	Batch verify endpoints
	•	Artifact indexing by tx hash + block number
	•	Optional webhook callbacks for verification state
	•	Multi-tenant API keys & quotas

