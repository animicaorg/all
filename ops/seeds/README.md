# Seeds & Bootstrap Nodes

This directory documents how we **select**, **rotate**, and **check liveness** for public bootstrap nodes (aka *seeds*). Seeds are **untrusted discovery helpers** that return initial peer addresses for new nodes. They must never be relied on for data integrity or consensus.

> TL;DR: Keep a small, diverse, rate-limited set of highly available endpoints; rotate them safely; and continuously probe that they speak the current protocol and serve fresh peers.

---

## What counts as a “seed”?

A seed is an endpoint reachable on at least one supported P2P transport that can answer **HELLO/IDENTIFY** and return a small list of peers. For Animica, transports may include:

- **QUIC** (preferred): `quic://seedX.animica.net:443`
- **TCP** (fallback): `tcp://seedX.animica.net:30333`
- **WS** (ops/debug only): `ws://seedX.animica.net:30334`

Seeds are typically long-lived, well-monitored nodes with open firewall to inbound connections and **no mining** enabled.

---

## Files & flow

- **Generated list (authoritative for ops)**
  `ops/seeds/bootstrap_nodes.json` – populated by ops when rotating.
  Format (see schema below) is consumed by:
  - `ops/k8s/configmaps/seeds.yaml`
  - Helm chart: `ops/helm/animica-devnet/templates/configmap-seeds.yaml`
  - Docker compose: `ops/docker/docker-compose.devnet.yml` (via env/volume)

- **Profile defaults (used by local runners)**
  `ops/seeds/devnet.json`, `ops/seeds/testnet.json`, `ops/seeds/mainnet.json` – light
  seed lists loaded by `ops/run.sh` to set `ANIMICA_P2P_SEEDS` and pre-populate the
  local peer store (`~/.animica/p2p/peers.json`).

- **Scripts** (already in `ops/scripts/`):
  - `gen_bootstrap_list.py` – scrape live nodes (RPC/metrics), score, emit JSON  
  - `rotate_seeds.py` – merge current + candidates, drop unhealthy, dedupe/sort

Typical rotation:

1) Discover & score candidates from live telemetry

python ops/scripts/gen_bootstrap_list.py –rpc https://rpc.devnet.animica.org 
–min-score 0.7 –max 16 > ops/seeds/bootstrap_nodes.json

2) Optionally refine (pin, drop, shuffle) with policy rules

python ops/scripts/rotate_seeds.py ops/seeds/bootstrap_nodes.json 
–min-uptime 0.98 –geo-diversity –max-per-asn 2 
–write ops/seeds/bootstrap_nodes.json

Commit the updated `bootstrap_nodes.json` and bump the chart/compose values that reference it if you pin hashes.

---

## JSON schema (minimal)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AnimicaBootstrapNodes",
  "type": "object",
  "required": ["chain_id", "generated_at", "seeds"],
  "properties": {
    "chain_id": { "type": "string", "pattern": "^animica:(1|2|1337)$" },
    "generated_at": { "type": "string", "format": "date-time" },
    "seeds": {
      "type": "array",
      "minItems": 3,
      "items": {
        "type": "object",
        "required": ["peer_id", "multiaddrs"],
        "properties": {
          "peer_id":   { "type": "string", "minLength": 8 },
          "multiaddrs": {
            "type": "array",
            "minItems": 1,
            "items": { "type": "string" }
          },
          "region":    { "type": "string" },
          "asn":       { "type": "integer" },
          "score":     { "type": "number", "minimum": 0, "maximum": 1 },
          "comment":   { "type": "string" }
        }
      }
    }
  }
}

Example (ops/seeds/bootstrap_nodes.json):

{
  "chain_id": "animica:2",
  "generated_at": "2025-10-03T12:00:00Z",
  "seeds": [
    {
      "peer_id": "12D3KooWb2…4a",
      "multiaddrs": [
        "/ip4/203.0.113.10/udp/443/quic-v1",
        "/dns/seed-eu-1.animica.dev/udp/443/quic-v1",
        "/ip4/203.0.113.10/tcp/30333"
      ],
      "region": "eu-west",
      "asn": 64496,
      "score": 0.91,
      "comment": "EU primary"
    },
    {
      "peer_id": "12D3KooWc9…fz",
      "multiaddrs": [
        "/dns/seed-us-1.animica.dev/udp/443/quic-v1",
        "/ip4/198.51.100.23/tcp/30333"
      ],
      "region": "us-east",
      "asn": 64497,
      "score": 0.88,
      "comment": "US east"
    }
  ]
}


⸻

Seed policy

Eligibility
	•	Runs current node release (±1 minor) with P2P: QUIC enabled, TCP fallback.
	•	Public IPv4 or DNS with stable A/AAAA; supports SNI/ALPN animica/1 for QUIC.
	•	Uptime ≥ 99% weekly; p95 handshake < 400 ms cross-region.
	•	No mining, no RPC open to public, rate-limits P2P handshakes & peer requests.
	•	Diversity: at least 3 regions and 2+ ASNs; max 2 seeds per ASN.

Configuration must
	•	Enable HELLO/IDENTIFY; advertise correct chain_id and alg-policy root.
	•	Maintain peerstore with fresh head height (not lagging > 5 blocks from public head).
	•	Expose /metrics (Prometheus) gated to ops or via private network.

⸻

Rotation policy
	•	Cadence: weekly scheduled rotation; ad-hoc rotations allowed for incidents.
	•	Size target: 5–9 seeds per network (mainnet/testnet), 3–5 for devnet.
	•	Promotion: candidates with 7-day health ≥ 0.98, diverse region/ASN, stable latency.
	•	Demotion: any seed with 24h health < 0.95, persistent lag, or abuse reports.
	•	Emergency: immediate removal on equivocation, malware, or repeated DoS behavior.

Change management
	1.	Stage new list in a PR; CI runs liveness checks (see below).
	2.	On merge, publish chart/configmap updates; roll out gradually (25% → 50% → 100%).
	3.	Monitor connect success rate, mesh degree, and time-to-first-peer on fresh nodes.

⸻

Liveness & health checks

Use these to validate before promoting a seed and in CI/CD:

1) Transport reachability

# QUIC (UDP/443): requires a small probe client or netcat alternative is insufficient.
# For CI, we rely on the node's /metrics + handshake check below.

# TCP fallback port open?
bash ops/docker/healthchecks/http_health.sh tcp seed-us-1.animica.dev:30333 --timeout 5

2) Handshake + IDENTIFY + head freshness

We maintain a lightweight probe (part of test jobs) that:
	•	Performs Kyber768 handshake, derives AEAD keys, sends HELLO.
	•	Reads IDENTIFY (chain_id, peer_id, head height).
	•	Fails if chain_id mismatches or head lags by >5 blocks.

(Implemented in test pipelines using the node’s P2P client libs.)

3) Prometheus surface

Scrape basic health to compute score:
	•	p2p_connected_peers, p2p_handshake_rtt_ms, chain_head_height,
	•	gossip_mesh_degree, rate_limit_drops_total.

Example manual scrape (if accessible):

curl -fsSL "http://seed-us-1.animica.dev:9090/metrics" | head

4) CI entrypoint

The rotation PR runs:

python ops/scripts/gen_bootstrap_list.py --rpc "$RPC_URL" --max 16 > ops/seeds/bootstrap_nodes.json
python ops/scripts/rotate_seeds.py ops/seeds/bootstrap_nodes.json --geo-diversity --max-per-asn 2 --write ops/seeds/bootstrap_nodes.json
jq . ops/seeds/bootstrap_nodes.json >/dev/null # sanity


⸻

Security notes
	•	Seeds provide addresses only; they are not trusted for headers, blocks, or randomness.
	•	Do not co-locate seeds with public RPC gateways. Keep separate rate limits and firewalls.
	•	Enable token-bucket on handshake & GETDATA to mitigate spray attacks.
	•	Keep OS & kernel up-to-date; enable UDP flood protections; monitor for SYN/UDP storms.

⸻

How to propose a seed

Open a PR that edits ops/seeds/bootstrap_nodes.json with:
	•	peer_id, multiaddrs, region, ASN (if known), optional comment.
	•	Evidence of uptime/latency (7-day window acceptable).
	•	Contact for incident response.

CI will run the liveness suite; maintainers decide admission per policy above.

Local profile defaults (devnet/testnet/mainnet)
----------------------------------------------
- Edit the corresponding `ops/seeds/<profile>.json` file to add or remove a seed.
- Keep the schema aligned with `bootstrap_nodes.json` (peer_id + multiaddrs).
- Run `python -m ops.seeds.profile_loader --profile <profile> --write-peerstore` to
  preview the comma-separated seed list and pre-fill `~/.animica/p2p/peers.json`.
- `ops/run.sh --profile <profile> node` automatically wires these defaults on startup.

⸻

FAQ

Q: Why not >10 seeds?
A: Bigger lists add blast radius & maintenance; a healthy gossip mesh quickly discovers more peers.

Q: Why QUIC first?
A: Better NAT traversal, built-in congestion control, and lower tail latencies vs TCP.

Q: Can we pin IPs instead of DNS?
A: Use DNS where possible for rotation without client updates; include one IP form as fallback.

⸻

Appendix: jq filter to list host:port pairs

jq -r '.seeds[].multiaddrs[]' ops/seeds/bootstrap_nodes.json

