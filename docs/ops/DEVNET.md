# Devnet Quickstart — Docker & Kubernetes

Spin up a **single-node Animica devnet** with HTTP JSON-RPC, WebSocket subscriptions, DA (data availability) endpoints, and optional Studio Services — all on your laptop or a small VM. These instructions mirror the layouts in `/ops` (compose + k8s), so you can copy the snippets there verbatim or adapt them to your environment.

> **Who is this for?** Protocol developers, SDK authors, wallet/Explorer teams, and QA who need a repeatable local network that mines blocks, accepts transactions, and exposes the standard APIs.

---

## Topology (minimal set)

- **rpc** — FastAPI app mounting JSON-RPC at `/rpc` and WS at `/ws` (module: `rpc/server.py`).
- **core** — headers/blocks/tx/state DBs (module: `core/*`), linked behind the RPC process.
- **consensus** — PoIES acceptance & Θ retarget (module: `consensus/*`).
- **mempool** — admission/fees/selection (module: `mempool/*`), in-process adapter.
- **mining (optional)** — CPU hash share loop for dev blocks (module: `mining/*`).
- **da (optional)** — blob post/get + NMT proofs (module: `da/*`).
- **randomness (optional)** — commit→reveal→VDF beacon (module: `randomness/*`).
- **studio-services (optional)** — stateless proxy for deploy/verify/faucet (module: `studio-services/*`).

> All services export `/healthz` and `/readyz` when applicable, and **Prometheus** metrics at `/metrics`.

### Default Ports (suggested)
- HTTP JSON-RPC: `8545` (path `/rpc`)
- WS subscriptions: `8545` (path `/ws`) or alt `8546` if you prefer split ports
- DA REST: `8650`
- Studio Services: `8787`
- Prometheus metrics: co-hosted on each service (same port), path `/metrics`

> **Port already in use?** If Docker reports `failed to bind host port 0.0.0.0:8545`, free it with the repo helper:
>
> ```bash
> python scripts/kill_port.py 8545
> ```
>
> Add `--signal SIGKILL` if the process ignores SIGTERM.

---

## 0) Prerequisites

- **Docker** ≥ 24 and **docker compose plugin** (or Compose V2)
- **kubectl** ≥ 1.26 (for k8s section)
- **(optional)** Helm ≥ 3.13
- For native builds (if you don’t use prebuilt images): Rust toolchain + Python 3.11

If you plan to run miners locally, favor a CPU with AVX2; otherwise the pure-Python loop still works (slower, fine for dev).

---

## 1) Docker Compose (single machine)

Below is a minimal, production-hardened-ish **dev** stack. It provides a node with RPC/WS, a DA service, and Studio Services for compile/verify.

> You can place this as `/ops/docker-compose.devnet.yml` or run it inline via `docker compose -f - up` with a here-doc.

```yaml
# docker-compose.devnet.yml
name: animica-devnet

services:
  node:
    image: ghcr.io/animica/animica-node:dev    # replace with your built image
    container_name: animica-node
    # If building locally from repo root:
    # build:
    #   context: .
    #   dockerfile: ops/docker/Dockerfile.node
    environment:
      RPC_HOST: "0.0.0.0"
      RPC_PORT: "8545"
      CHAIN_ID: "1"
      LOG_LEVEL: "info"
      DB_URI: "sqlite:////data/animica.db"
      # Feature toggles (optional)
      ENABLE_DA: "true"
      ENABLE_RANDOMNESS: "true"
      ENABLE_MINER: "false"   # set true if you want the built-in CPU miner
    ports:
      - "8545:8545"   # HTTP /rpc and /ws
    volumes:
      - node-data:/data
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8545/healthz"]
      interval: 10s
      timeout: 3s
      retries: 6

  da:
    image: ghcr.io/animica/animica-da:dev
    container_name: animica-da
    environment:
      DA_HOST: "0.0.0.0"
      DA_PORT: "8650"
      STORAGE_DIR: "/data/da"
      LOG_LEVEL: "info"
    ports:
      - "8650:8650"
    volumes:
      - da-data:/data
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8650/healthz"]
      interval: 10s
      timeout: 3s
      retries: 6

  studio-services:
    image: ghcr.io/animica/studio-services:dev
    container_name: animica-studio
    environment:
      RPC_URL: "http://node:8545"
      CHAIN_ID: "1"
      STORAGE_DIR: "/data/storage"
      LOG_LEVEL: "info"
      # Faucet is OFF by default; set a throwaway key to enable
      # FAUCET_KEY: "0xdead...beef"
    ports:
      - "8787:8787"
    volumes:
      - studio-data:/data
    depends_on:
      node:
        condition: service_healthy

volumes:
  node-data: {}
  da-data: {}
  studio-data: {}

Bring it up

docker compose -f docker-compose.devnet.yml up -d
docker compose ps

Smoke test

# Health
curl -sS http://localhost:8545/healthz
curl -sS http://localhost:8545/readyz

# Chain params
curl -sS -X POST http://localhost:8545/rpc \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getParams","params":[]}'

# Head
curl -sS -X POST http://localhost:8545/rpc \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"chain.getHead","params":[]}'

Optional: Start the built-in CPU miner

If your node image includes the miner CLI, or you run it from source on your host:

# In another shell, attach to the node container
docker exec -it animica-node bash -lc "python -m mining.cli.miner --threads 2 --device cpu"

Studio Services quickcheck

curl -sS http://localhost:8787/healthz
# (Optional) Preflight simulate a read-only call
curl -sS -X POST http://localhost:8787/preflight -H 'content-type: application/json' \
  -d '{"to":"anim1qqqq...", "call":{"fn":"get","args":[]}}'

Tear down

docker compose -f docker-compose.devnet.yml down -v

Tip: Keep volumes if you want to preserve chain state; omit -v.

⸻

2) Kubernetes (kind, k3d, minikube, or any cluster)

This is a single-replica devnet deployment with a NodePort (or ClusterIP + port-forward) exposing /rpc and /ws. For a real testnet, add persistent volumes, ingress, and resource limits.

Suggested layout in /ops/k8s/devnet/: config.yaml, deployment.yaml, service.yaml, ingress.yaml (optional).

Namespace

kubectl create namespace animica-devnet

ConfigMap (env)

# ops/k8s/devnet/config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: animica-config
  namespace: animica-devnet
data:
  RPC_PORT: "8545"
  CHAIN_ID: "1"
  LOG_LEVEL: "info"
  DB_URI: "sqlite:////data/animica.db"
  ENABLE_DA: "true"
  ENABLE_RANDOMNESS: "true"
  ENABLE_MINER: "false"

Deployment

# ops/k8s/devnet/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: node
  namespace: animica-devnet
spec:
  replicas: 1
  selector:
    matchLabels: { app: animica-node }
  template:
    metadata:
      labels: { app: animica-node }
    spec:
      containers:
        - name: node
          image: ghcr.io/animica/animica-node:dev
          envFrom:
            - configMapRef:
                name: animica-config
          ports:
            - name: rpc
              containerPort: 8545
          readinessProbe:
            httpGet: { path: /readyz, port: rpc }
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /healthz, port: rpc }
            initialDelaySeconds: 10
            periodSeconds: 10
          volumeMounts:
            - name: data
              mountPath: /data
      volumes:
        - name: data
          emptyDir: {}  # swap for a PVC on real clusters

Service (NodePort example)

# ops/k8s/devnet/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: node
  namespace: animica-devnet
spec:
  type: NodePort
  selector: { app: animica-node }
  ports:
    - name: http-rpc
      port: 8545
      targetPort: rpc
      nodePort: 30545  # adjust if taken

Apply

kubectl apply -f ops/k8s/devnet/config.yaml
kubectl apply -f ops/k8s/devnet/deployment.yaml
kubectl apply -f ops/k8s/devnet/service.yaml

Access
	•	NodePort: http://<node-ip>:30545
	•	Port-forward (alternative):

kubectl -n animica-devnet port-forward svc/node 8545:8545

Health & Head

curl -sS http://localhost:8545/healthz
curl -sS -X POST http://localhost:8545/rpc -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'


⸻

3) Building Images (optional)

If you’re iterating locally:

# From repo root; adjust Dockerfiles/contexts to your layout
docker build -t ghcr.io/animica/animica-node:dev -f ops/docker/Dockerfile.node .
docker build -t ghcr.io/animica/animica-da:dev   -f ops/docker/Dockerfile.da .
docker build -t ghcr.io/animica/studio-services:dev -f ops/docker/Dockerfile.studio-services .

For Kubernetes, push to a registry your cluster can pull from (GHCR, Docker Hub, or a local registry mirrored into kind/minikube).

⸻

4) Persistence & Data Paths
	•	Default DB path for node: /data/animica.db (SQLite).
	•	DA storage: /data/da.
	•	Studio Services artifacts: /data/storage.

In Compose, use named volumes. In K8s, replace emptyDir with PVCs:

# snippet
volumeClaimTemplates:
- metadata: { name: data }
  spec:
    accessModes: ["ReadWriteOnce"]
    resources: { requests: { storage: 10Gi } }


⸻

5) Metrics & Dashboards

Every service exports Prometheus metrics at /metrics on its HTTP port. Common labels include:
	•	animica_build_info{version=..., git=...}
	•	RPC request counters & histograms
	•	Mempool admits/rejects, fee floors
	•	Consensus Θ, inter-block λ estimates
	•	Mining shares/sec (if enabled)
	•	DA post/get/proof counters
	•	Randomness round transitions, VDF verify latency

For Grafana, scrape the services in Compose (add a Prometheus container) or via your cluster Prometheus.

⸻

6) Security & Networking (dev-focused defaults)
	•	CORS in RPC is strict by default; allow your origin explicitly in rpc/config.py or via env.
	•	Rate limits: token-bucket middlewares protect /rpc and DA endpoints; tune in config.
	•	P2P is typically off on a single-node devnet; enable in p2p/config.py if testing sync/gossip.
	•	No private keys live in containers. Studio Services never stores secrets; faucet is disabled unless you provide a throwaway test key.

⸻

7) Troubleshooting
	•	readyz stays failing: Check logs for genesis load, DB initialization, or policy root mismatches.
	•	RPC 500: Inspect rpc/errors.py mapping; many errors are typed and returned with structured codes.
	•	Slow mining: Enable the built-in miner or submit external shares via Stratum/WS getwork; reduce Θ in consensus/fixtures for demo.
	•	DA proof verify fails: ensure blob size within da/constants.py limits; NMT roots must match the header when integrated.
	•	VDF verify too slow: use smaller iterations in randomness/vdf/params.py for dev; production uses stronger params.
	•	K8s CrashLoopBackOff: missing image pull secrets or wrong envs; kubectl logs and check probes.

⸻

8) Clean Exit & Data Reset
	•	Compose: docker compose down -v (removes volumes).
	•	K8s: kubectl delete ns animica-devnet or delete the resources individually.

⸻

9) What’s Next
	•	Use the SDKs (TS/Py/Rust) to deploy and call the Counter example.
	•	Try Studio Web (browser simulator via studio-wasm) and Studio Services for verify flows.
	•	Wire Explorer to the node and DA endpoints for a full local stack.

⸻

Appendix A — Minimal /ops File Map

If you maintain a dedicated /ops folder, a simple map:

ops/
├─ docker-compose.devnet.yml
├─ docker/
│  ├─ Dockerfile.node
│  ├─ Dockerfile.da
│  └─ Dockerfile.studio-services
└─ k8s/
   └─ devnet/
      ├─ config.yaml
      ├─ deployment.yaml
      ├─ service.yaml
      └─ ingress.yaml   (optional)

Keep image tags consistent (:dev for local, git-sha for CI). Pin Python/Rust base images to avoid drift.

