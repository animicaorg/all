# Public Devnet: Architecture, Endpoints, TLS & Monitoring

This document describes the **public devnet** topology for Animica: the services we run, how traffic reaches them (DNS ↔ TLS ↔ reverse proxy), ports, and how we observe health and performance.

> **Chain IDs (CAIP-2)**: `animica:1` (mainnet), `animica:2` (testnet), **`animica:1337` (devnet)**

---

## 1) High-level architecture

               +--------------------------+

Internet         |  TLS Termination / WAF   |
─────────▶       |  (nginx or Caddy)        |
rpc/ws/https     +———–+–––––––+
|
┌──────────────┴─────────────────────────────────────┐
|                                                    |
/rpc, /ws, /openrpc.json                             /api (studio-services)
+––v––+         +––––––––––+            +––v—————–+
|  RPC    |         |  P2P (QUIC/TCP/WS) |            |  Studio Services     |
| FastAPI |         |  Gossip + Sync     |            |  (FastAPI)           |
+––+––+         +———+–––––+            +———+————+
|                          |                               |
|                          |                               |
+––v––+               +—–v––+                  +—––v——+
|  Core   |<———––>|  P2P Node|<––––––––>|  AICF         |
|  (DBs)  |               +–––––+                  |  (workers)    |
+––+––+                                            +—––+––––+
|                                                         |
|               +—————–+                       |
+———––> |  DA Retrieval   | <———————+
|  (FastAPI)      |
+––––+––––+
|
+—–v––+
|  Storage |
| (FS/SQL) |
+–––––+

Optional UIs (static over HTTPS):
	•	explorer-web: /explorer
	•	studio-web:   /studio  (talks to /api for deploy/verify/faucet)

**Core components**
- **rpc/**: JSON-RPC (`/rpc`), WS (`/ws`), OpenRPC (`/openrpc.json`)
- **p2p/**: QUIC/TCP/WS transports, gossip, sync
- **da/**: Data Availability POST/GET/PROOF API
- **aicf/**: AI Compute Fund registry, queue, settlement (read-only RPC mounted + internal workers)
- **studio-services/**: Proxy for *deploy / verify / faucet / artifacts* (no server-side signing)
- **explorer-web/** & **studio-web/**: Static SPAs consuming `rpc/` and `studio-services/`

---

## 2) DNS layout

Public devnet uses subdomains (examples):

| Purpose            | Hostname                         | Notes                                |
|-------------------|----------------------------------|--------------------------------------|
| RPC (HTTP)        | `rpc.devnet.animica.xyz`         | Proxies to FastAPI `/rpc`            |
| RPC (WS)          | `ws.devnet.animica.xyz`          | Upgrades to `/ws`                    |
| OpenRPC JSON      | `openrpc.devnet.animica.xyz`     | Serves `spec/openrpc.json`           |
| DA API            | `da.devnet.animica.xyz`          | FastAPI `/da` REST surface           |
| Studio Services   | `api.devnet.animica.xyz`         | `/deploy`, `/verify`, `/faucet`      |
| Explorer (UI)     | `explorer.devnet.animica.xyz`    | Static site                          |
| Studio Web (UI)   | `studio.devnet.animica.xyz`      | Static site                          |
| P2P advertise     | `p2p.devnet.animica.xyz`         | Optional A/AAAA for bootstraps       |

> You may also terminate everything at `devnet.animica.xyz` and mount paths (`/rpc`, `/ws`, `/da`, `/api`, `/explorer`, `/studio`). Both patterns are supported.

---

## 3) Ports (defaults)

These defaults mirror `tests/devnet/env.devnet.example`. If you change them, update both the compose file and this doc.

| Service            | Port (container) | Public LB / Host | Proto | Path / Notes                         |
|-------------------|------------------|------------------|-------|--------------------------------------|
| RPC HTTP          | 8545             | 443 via TLS      | HTTPS | `/rpc` (JSON-RPC 2.0 POST)           |
| RPC WebSocket     | 8546             | 443 via TLS      | WSS   | `/ws` (subscriptions)                |
| OpenRPC           | 8545             | 443 via TLS      | HTTPS | `/openrpc.json`                      |
| P2P TCP           | 30303            | 30303            | TCP   | NAT opened if public                  |
| P2P QUIC          | 30304            | 30304            | UDP   | Optional, advertised via seeds       |
| DA API            | 8688             | 443 via TLS      | HTTPS | `/da/*`                              |
| Studio Services   | 8787             | 443 via TLS      | HTTPS | `/api/*`                             |
| Explorer Web      | 8081             | 443 via TLS      | HTTPS | `/explorer` or subdomain             |
| Studio Web        | 8082             | 443 via TLS      | HTTPS | `/studio` or subdomain               |
| Prometheus        | 9090             | 9090 (ops-only)  | HTTP  | Private admin network only           |
| Grafana           | 3000             | 3000 (ops-only)  | HTTP  | Private admin network only           |

> For **local devnet**, the same ports are exposed on `localhost` without TLS. For **public devnet**, all user-facing HTTP/WS traffic is terminated at the reverse proxy on **443**.

---

## 4) TLS & reverse proxy

We recommend **Caddy** (ACME built-in) or **nginx** + Certbot.

### Caddy (example)
```caddy
# /etc/caddy/Caddyfile
{
  email ops@animica.xyz
}

rpc.devnet.animica.xyz, ws.devnet.animica.xyz {
  encode zstd gzip
  @ws {
    header Connection *Upgrade*
    header Upgrade    websocket
    path /ws
  }
  reverse_proxy @ws   rpc:8546
  reverse_proxy /rpc* rpc:8545
  reverse_proxy /openrpc.json rpc:8545
}

da.devnet.animica.xyz {
  encode zstd gzip
  reverse_proxy da:8688
}

api.devnet.animica.xyz {
  encode zstd gzip
  reverse_proxy studio-services:8787
}

explorer.devnet.animica.xyz {
  root * /srv/explorer-web
  file_server
}

studio.devnet.animica.xyz {
  root * /srv/studio-web
  file_server
}

TLS policy
	•	Enforce TLS 1.2+ (prefer 1.3), modern ciphers, HSTS (subdomains optional).
	•	Redirect HTTP→HTTPS, set strict CORS allow-list on APIs.
	•	Large uploads (DA) should allow 8–64 MiB bodies with rate-limit buckets.

⸻

5) Monitoring & observability

Metrics

All major services expose Prometheus metrics at /metrics:
	•	rpc/metrics.py, p2p/metrics.py, mempool/metrics.py
	•	da/metrics.py, randomness/metrics.py
	•	aicf/metrics.py, mining/metrics.py
	•	studio-services/studio_services/metrics.py

Scrape config (snippet)

scrape_configs:
  - job_name: 'rpc'
    static_configs: [{ targets: ['rpc:8545'] }]
    metrics_path: /metrics
  - job_name: 'da'
    static_configs: [{ targets: ['da:8688'] }]
    metrics_path: /metrics
  - job_name: 'studio-services'
    static_configs: [{ targets: ['studio-services:8787'] }]
    metrics_path: /metrics
  - job_name: 'p2p'
    static_configs: [{ targets: ['p2p:9100'] }]  # if exported separately

Dashboards
	•	Grafana: import dashboards for RPC latency, mempool size, DA post/GET latency, P2P peers/gossip, AICF queue depth, randomness rounds.
	•	Logs: JSON-structured logs (see core/logging.py, rpc/middleware/logging.py, studio-services/.../logging.py). Optional Loki + promtail pipeline is supported.

Alerts (examples)
	•	Head not advancing for > 2 epochs
	•	WS subscription error rate > 1% over 5m
	•	DA proof verification failures > 0 over 10m
	•	AICF queue age p95 > SLO
	•	Randomness beacon finalize latency > round length

⸻

6) Security & rate limits
	•	CORS: strict origin allow-list; only necessary Content-Type and Authorization headers.
	•	Rate limits: token buckets at RPC and studio-services layers:
	•	Per-IP and per-method (see rpc/middleware/rate_limit.py, studio-services/.../rate_limit.py).
	•	Faucet (devnet only): gated by API key, per-IP/day limits.
	•	P2P: seed lists are curated; enable DoS protections for gossip & INV floods.
	•	Upload size: DA and verify endpoints enforce size and content-type caps.

⸻

7) Seeds, discovery & NAT
	•	Bootstrap list is published at https://p2p.devnet.animica.xyz/seeds.txt (also mirrored in p2p/fixtures/seed_list.txt).
	•	QUIC preferred; fall back to TCP. Enable UPnP/NAT-PMP on public seed nodes only if necessary.
	•	Nodes advertise peer-id derived from PQ identity (see p2p/crypto/peer_id.py).

⸻

8) Backups & data retention
	•	Core DBs: snapshot volumes nightly; retain 7/30 days.
	•	DA store: artifact blobs are content-addressed; replicate or back up manifest DB + GC policy.
	•	AICF: settlement & staking tables backed up before epoch rollovers.
	•	Studio Services: artifacts and verification metadata are write-once; keep checksums.

⸻

9) Rollouts & maintenance
	•	Rolling restarts: drain WS clients; pause miner on a single node during protocol upgrades.
	•	Schema changes: run migrations (SQLite/Rocks/SQL) with maintenance window on public devnet; announce in status.animica.xyz.
	•	Versioning: each service exposes /healthz, /readyz, and /version.

⸻

10) Quick reference (environment)

The compose and k8s manifests consume a standard set of env vars (see tests/devnet/env.devnet.example):
	•	RPC_HTTP_PORT, RPC_WS_PORT
	•	P2P_TCP_PORT, P2P_QUIC_PORT
	•	DA_API_PORT
	•	SERVICES_PORT
	•	EXPLORER_PORT, STUDIO_PORT
	•	CHAIN_ID=1337
	•	CORS_ALLOW_ORIGINS, RATE_LIMITS_*

⸻

11) Troubleshooting
	•	WS timeouts: check proxy idle timeouts (set ≥ 120s) and keepalive pings from server.
	•	Head stuck: verify consensus Θ schedule, miner availability, and mempool congestion.
	•	DA proof fails: confirm namespace ranges & erasure params match spec/blob_format.cddl and da/schemas/*.
	•	Verify mismatch: ensure studio-services compiles with the same VM version as the node (see /version).

⸻

12) Contacts
	•	Ops on-call: ops@animica.xyz
	•	Status page: https://status.animica.xyz
	•	Security: security@animica.xyz (PGP preferred)

