# Docker & Compose — Animica Ops

This folder documents how to run Animica services with **Docker Compose** using
profiles to pick the stack you need (single node, full devnet, or targeted
APIs). It also explains persistent **volumes**, **TLS/HTTPS** termination, and
recommended production-ish settings.

> Requirements: Docker 24+, Compose v2.17+ (integrated `docker compose`), GNU
> coreutils. On Apple Silicon, multi-arch images are supported (see
> `ops/scripts/push_images.sh`).

---

## Quick Start (dev profile)

```bash
# Copy and adjust environment
cp ops/.env.example ops/.env

# Bring up the devnet profile (node + rpc + da + aicf + services + explorer)
docker compose -f ops/devnet/docker-compose.yml --env-file ops/.env --profile devnet up -d

# Wait until endpoints are healthy (optional helper)
bash ops/devnet/wait_for_services.sh

Stop & clean (keeps named volumes):

docker compose -f ops/devnet/docker-compose.yml --profile devnet down

Wipe volumes (⚠ destructive):

bash tests/devnet/cleanup.sh


⸻

Compose Profiles

We use Compose profiles to keep the stack modular. File layouts:
	•	tests/devnet/docker-compose.yml
CI/devnet focused; tiny chain params; sensible defaults.
	•	(Optional) You may add a production ops/docker/compose.yml in your fork
that reuses the same service names and volumes.

Common profiles (names used across our compose files):
	•	devnet — full local network: node, RPC, DA service, AICF, studio-services,
explorer, and a CPU miner.
	•	node — only the core node (core + rpc + p2p), suitable for local test or
as a seed.
	•	rpc — only the FastAPI RPC server + dependencies (talks to an external DB).
	•	da — Data-Availability retrieval API & store.
	•	aicf — AI Compute Fund queues & APIs.
	•	explorer — explorer-web (static site) behind a simple HTTP server.
	•	services — studio-services (deploy/verify/faucet/artifacts).
	•	miner — built-in CPU miner (connects to local RPC).

Use --profile <name> to enable any subset:

docker compose -f ops/devnet/docker-compose.yml --profile node --profile rpc up -d


⸻

Environment (.env)

Copy ops/.env.example → ops/.env and customize:
	•	NETWORK / CHAIN: CHAIN_ID, RPC URL overrides.
	•	HOSTNAMES: ROOT_DOMAIN or per-service DOMAIN_RPC, DOMAIN_WS, …
Used by scripts and ingress rendering.
	•	PORTS: host ports for RPC/WS/services/explorer.
	•	RATES & LIMITS: per-service rate limits.
	•	FAUCET / API KEYS: FAUCET_KEY, SERVICES_API_KEY (dev only).
	•	TLS: TLS_ENABLED, CERT_CLUSTER_ISSUER (if you also deploy to k8s).

Compose consumes --env-file ops/.env. Never commit secrets.

⸻

Volumes & Persistence

Named volumes keep state between restarts. Typical volumes:
	•	animica-core-db — chain databases (SQLite/RocksDB).
	•	animica-da-store — DA blobs + NMT metadata.
	•	animica-aicf-db — AICF registry/queue/stakes/payouts.
	•	animica-services-store — studio-services artifacts & verification DB.
	•	animica-metrics — Prometheus/Grafana (if enabled).

List volumes:

docker volume ls | grep animica

Back up a volume:

VOL=animica-core-db
docker run --rm -v $VOL:/v -v $PWD:/out alpine sh -c "cd /v && tar czf /out/${VOL}.tgz ."

Restore:

VOL=animica-core-db
docker run --rm -v $VOL:/v -v $PWD:/in alpine sh -c "cd /v && tar xzf /in/${VOL}.tgz"

To use bind mounts instead (handy in dev), adapt your compose overrides to
mount ./.data/core:/data, etc.

⸻

TLS / HTTPS / WSS

We recommend terminating TLS at a reverse proxy (Traefik, Caddy, or NGINX):

Option A — Local Dev (self-signed)
	1.	Generate a self-signed cert for localhost (and any local domains).
	2.	Run a proxy on 443 that forwards to:
	•	RPC → http://rpc:8545
	•	WS → ws://ws:8546
	•	services → http://studio-services:8080
	•	explorer → http://explorer-web:80
	3.	Trust certs in your OS/browser. For Chrome extensions (MV3), you must also
set correct CORS and permissions (see wallet-extension docs).

Option B — Real Domains (Let’s Encrypt)
	•	Use Traefik (Docker labels) or NGINX + certbot, or Kubernetes with cert-manager.
	•	Our helper script ops/scripts/render_ingress_values.py renders Helm values
for k8s Ingress + ClusterIssuer (CERT_CLUSTER_ISSUER).
	•	Ensure WSS for websockets: the proxy must pass through Upgrade/Connection.

CORS
	•	RPC (/rpc) and Services endpoints enforce strict CORS by config. Adjust
allowlists in rpc/config.py and studio-services/studio_services/config.py,
or via env (e.g. RPC_CORS_ORIGINS=https://studio.example,https://app.example).

⸻

Ports (defaults)

Service	Container	Host (devnet)	Notes
JSON-RPC (HTTP)	8545	8545	/rpc JSON-RPC 2.0
WebSocket (WS)	8546	8546	/ws subscriptions
DA API	8081	8081	post/get/proof
AICF API	8080	8082	may share with services
Studio Services	8080	8080	deploy/verify/faucet
Explorer Web	80	3000	static SPA
Metrics	9090	9090	Prometheus (optional)

Override via .env or compose overrides.

⸻

Images

Use our published images or build your own:
	•	Consume: set IMAGE_TAG / VERSION in .env to a released tag (e.g. v0.1.0).
	•	Build & push: ./ops/scripts/push_images.sh --latest
Publishes multi-arch images to your registry (see that script for envs).

⸻

Health & Readiness

Most services expose /healthz and /readyz. In devnet we gate startup using:

bash ops/devnet/wait_for_services.sh

Troubleshoot:

docker compose -f ops/devnet/docker-compose.yml logs -f
docker compose -f ops/devnet/docker-compose.yml ps


⸻

Production Notes
	•	Do not expose RPC write methods publicly. Use allowlists, auth, or a
private RPC for writes.
	•	Rate limits: enable per-IP token buckets on RPC and services.
	•	Backups: snapshot DB volumes (see “Volumes & Persistence”).
	•	Monitoring: Prometheus scrapes /metrics; wire alerts for:
	•	head not advancing
	•	mempool saturation
	•	DA post errors / proof failures
	•	AICF SLA violations / slashing spikes
	•	Secrets: Prefer Docker/Swarm/K8s secrets. For Compose, mount read-only files
and point env vars to paths (e.g. FAUCET_KEY_FILE=/run/secrets/faucet).

⸻

Common Pitfalls
	•	CORS: browser requests failing? Check allowed origins and TLS scheme.
	•	WSS: ensure proxy sets Connection: Upgrade & Upgrade: websocket.
	•	Clock drift: randomness and consensus windows are time-sensitive.
	•	ARM64: use our multi-arch images; building locally? set PLATFORMS=linux/arm64.

⸻

Appendix: Minimal Traefik example (dev)

# traefik.dev.yaml
entryPoints:
  websecure:
    address: ":443"
providers:
  docker: {}
api: { insecure: true }
certificatesResolvers:
  dev:
    acme:
      email: you@example.com
      storage: /acme.json
      caServer: https://acme-staging-v02.api.letsencrypt.org/directory
      httpChallenge: { entryPoint: websecure } # for local, use tls challenge or custom CA

Attach labels to services to define Host() rules for rpc, ws, services,
explorer hosts. For Kubernetes, generate Ingress values via:

python ops/scripts/render_ingress_values.py --env ops/.env --out ops/ingress.values.yaml

—

If something here is unclear for your environment, open an issue in your fork’s
ops docs and we’ll extend this guide.
