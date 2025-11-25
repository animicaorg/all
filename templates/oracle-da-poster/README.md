# {{ service_name }} — Data-Availability Oracle Poster

A minimal FastAPI-based microservice that **posts oracle values to Animica Data Availability (DA)** and (optionally) **notifies an on-chain oracle contract** by submitting the blob commitment. This template is designed to be:

- **Deterministic & spec-aligned**: Uses the Animica SDKs and DA commitment rules (NMT roots).
- **Chain-agnostic**: Works with any Animica chain via `CHAIN_ID` and `RPC_URL`.
- **Simple to operate**: Single container, health checks, metrics, and straightforward configuration.

> Typical flow: your system sends a new value (e.g., price feed) → the service pins it to DA (namespaced blob) → returns the **commitment** (NMT root). If an `ORACLE_ADDRESS` is configured, the service also **calls the oracle contract** to record the latest commitment on-chain.

---

## ✨ What gets generated

After rendering this template, you’ll have a project like:

{{ project_slug }}/
├─ aicf_oracle_da_poster/           # Python service package (name derived from project slug)
│  ├─ init.py
│  ├─ config.py                      # Env/config loader (RPC_URL, CHAIN_ID, DA_NAMESPACE, ORACLE_ADDRESS, PORT, LOG_LEVEL)
│  ├─ da_client.py                   # Thin DA client; post/get/proof via SDK
│  ├─ oracle_client.py               # Optional: call on-chain oracle to record commitment
│  ├─ models.py                      # Pydantic request/response models
│  ├─ server.py                      # FastAPI app (routes, health, metrics)
│  └─ version.py
├─ pyproject.toml
├─ requirements.txt
├─ .env.example
├─ Makefile
├─ Dockerfile
├─ k8s/
│  ├─ deployment.yaml
│  └─ service.yaml
└─ README.md                         # (this file)

> If you picked a different slug, the Python package name is computed as `{{ py_package }}`.

---

## 🔧 Prerequisites

- **Python** 3.11+ (for local runs)
- **Node RPC** URL (HTTP) reachable from where this service runs
- **Animica Chain ID** you’ll target (e.g., `1` mainnet / `2` testnet / `1337` devnet)
- (Optional) **Oracle contract** deployed and its address (bech32m `anim1…`)

---

## ⚙️ Configuration

All configuration is via environment variables (see `.env.example`):

| Variable          | Description                                                                          | Example                         |
|-------------------|--------------------------------------------------------------------------------------|---------------------------------|
| `RPC_URL`         | Animica JSON-RPC endpoint                                                            | `http://localhost:8545`         |
| `CHAIN_ID`        | Chain ID                                                                              | `1337`                          |
| `DA_NAMESPACE`    | DA namespace (0–255) used for blobs                                                   | `24`                            |
| `ORACLE_ADDRESS`  | (Optional) Oracle contract address to update after posting                            | `anim1…` (or empty)             |
| `LOG_LEVEL`       | Logging level                                                                         | `INFO`                          |
| `PORT`            | FastAPI listen port                                                                   | `8088`                          |

> In the rendered template, these default to values chosen during generation (from `variables.json`).

---

## 🏃 Quickstart (Local)

1) Create and configure your `.env`:

```bash
cp .env.example .env
# edit values as needed (RPC_URL, CHAIN_ID, DA_NAMESPACE, ORACLE_ADDRESS)

	2.	Install and run:

python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

# Run the service (reload for local dev)
uvicorn aicf_oracle_da_poster.server:app --host 0.0.0.0 --port "${PORT:-{{ port_api }}}" --reload

	3.	Health and docs:

	•	Health: GET http://localhost:${PORT}/healthz → { "ok": true }
	•	OpenAPI: GET http://localhost:${PORT}/docs

⸻

🧩 API

POST /v1/oracle/value

Posts the given payload as a DA blob under the configured namespace. Returns the commitment (NMT root) and metadata. If ORACLE_ADDRESS is set, also submits a contract call to record the commitment.

Request (JSON)

{
  "payload": "base64-or-hex-or-utf8", 
  "encoding": "auto", 
  "metadata": {
    "symbol": "ETHUSD",
    "timestamp": 1700000000
  },
  "notify_contract": true
}

	•	payload: The data to commit. If encoding = "auto", the service will:
	•	accept UTF-8 strings directly,
	•	accept 0x… prefixed hex,
	•	accept base64.
	•	metadata: Optional, stored alongside for convenience (not part of commitment).
	•	notify_contract: If true, and ORACLE_ADDRESS is configured, the service performs the on-chain update.

Response (JSON)

{
  "namespace": 24,
  "size": 128,
  "commitment": "0xabc123…", 
  "txHash": "0xdeadbeef…",
  "notifiedContract": true,
  "contractTxHash": "0xfeedcafe…",
  "chainId": 1337
}

	•	commitment is the DA NMT root for the posted blob.
	•	txHash is the DA post transaction (if posted via node where applicable).
	•	If notifiedContract == true, contractTxHash is the transaction that updated the on-chain oracle.

The oracle contract is expected to store only the commitment and minimal metadata (size, namespace, optional content digest). Clients then fetch/verify the blob via DA and interpret it off-chain or within their apps.

⸻

🧪 cURL examples
	•	Post a JSON oracle value:

curl -sS -X POST "http://localhost:${PORT}/v1/oracle/value" \
  -H "Content-Type: application/json" \
  -d '{"payload":"{\"price\": 3123.45, \"ts\": 1700000000}","encoding":"auto","notify_contract":true}'

	•	Post a hex-encoded blob without contract notification:

curl -sS -X POST "http://localhost:${PORT}/v1/oracle/value" \
  -H "Content-Type: application/json" \
  -d '{"payload":"0x48656c6c6f2c20444121","encoding":"auto","notify_contract":false}'


⸻

🧱 Contract integration

This service pairs with the example oracle contract in the Animica repo (see contracts/examples/oracle). The expected pattern:
	•	Contract exposes a method like update(commitment, size, namespace) (names may vary by implementation).
	•	The service calls that method only when ORACLE_ADDRESS is set and notify_contract=true.
	•	Consumers query the contract for the latest commitment, then fetch the blob via DA (and optionally verify proofs).

If you need a specific ABI shape, adjust oracle_client.py to match your contract’s ABI/manifest.json.

⸻

🐳 Docker

Build and run:

docker build -t {{ image_ref }} .
docker run --rm -p {{ port_api }}:{{ port_api }} \
  -e RPC_URL="{{ rpc_url }}" \
  -e CHAIN_ID="{{ chain_id }}" \
  -e DA_NAMESPACE="{{ da_namespace }}" \
  -e ORACLE_ADDRESS="{{ oracle_address }}" \
  -e PORT="{{ port_api }}" \
  -e LOG_LEVEL="{{ log_level }}" \
  {{ image_ref }}


⸻

☸️ Kubernetes (example)

apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ k8s_name }}
spec:
  replicas: 1
  selector:
    matchLabels: { app: {{ k8s_name }} }
  template:
    metadata:
      labels: { app: {{ k8s_name }} }
    spec:
      containers:
        - name: poster
          image: "{{ image_ref }}"
          ports: [{ containerPort: {{ port_api }} }]
          env:
            - { name: RPC_URL, value: "{{ rpc_url }}" }
            - { name: CHAIN_ID, value: "{{ chain_id }}" }
            - { name: DA_NAMESPACE, value: "{{ da_namespace }}" }
            - { name: ORACLE_ADDRESS, value: "{{ oracle_address }}" }
            - { name: LOG_LEVEL, value: "{{ log_level }}" }
            - { name: PORT, value: "{{ port_api }}" }
---
apiVersion: v1
kind: Service
metadata:
  name: {{ k8s_name }}
spec:
  selector: { app: {{ k8s_name }} }
  ports:
    - name: http
      port: {{ port_api }}
      targetPort: {{ port_api }}


⸻

📈 Observability
	•	Health: GET /healthz → { "ok": true }
	•	Metrics: GET /metrics (Prometheus text format)
	•	Structured logs with request IDs & durations

Suggested Prometheus alerts:
	•	High error rate on POST /v1/oracle/value
	•	DA post failures or latency p95 above SLO
	•	Contract notify failures

⸻

🛡️ Security & Ops Notes
	•	Put this service behind an API gateway and/or mTLS if exposed publicly.
	•	Consider rate limiting client requests that trigger DA posts.
	•	For contract calls, ensure your submitter/fee policy is controlled (tip/base fee caps).
	•	Log payload sizes only (avoid logging raw payloads in production).
	•	Rotate credentials and keep RPC_URL restricted.

⸻

🧰 Development

Project tasks (Makefile):
	•	make dev — Run with auto-reload (uvicorn)
	•	make lint — ruff/mypy
	•	make test — unit tests
	•	make docker — build image

Local unit test idea: mock DA client and contract client to validate:
	•	payload encoding → deterministic commitment
	•	contract notification path and retries
	•	error mapping (400 invalid payload, 502 DA errors, 504 timeouts)

⸻

❓ Troubleshooting
	•	Invalid namespace: Ensure DA_NAMESPACE is in [0,255].
	•	Contract call failed: Check ORACLE_ADDRESS and the ABI alignment; confirm your node’s gas policy.
	•	DA post timeout: Increase client timeout; check node and DA endpoints are reachable.
	•	Wrong chainId: Ensure CHAIN_ID matches the node you are talking to.

⸻

📄 License

{{ license }} (set during template generation). Update headers as needed ({{ author_name }}).

⸻

🔗 Related
	•	Animica contracts/examples/oracle — reference contract that accepts DA commitments.
	•	Animica SDKs — Python/TypeScript/Rust clients for RPC/DA.
	•	Animica DA spec — NMT roots, proofs, sampling math.

