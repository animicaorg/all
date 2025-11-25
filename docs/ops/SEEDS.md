# Seed Policy, Rotation, and Liveness Checks

This document defines how **Animica** bootstraps new peers onto the network using *seed nodes* and *seed registries* (DNS + JSON mirrors), how we **rotate** the advertised seeds, and how we **measure liveness** and quality before listing a host.

> TL;DR  
> - Seeds are *discoverability beacons*, not trusted authorities.  
> - We publish seeds via **DNS TXT** and a **signed JSON mirror**.  
> - Operators must meet uptime, freshness, and rate-limit requirements.  
> - We rotate weekly, with low churn and emergency delists available.  
> - Liveness checks validate handshake, head freshness, and basic sync.

---

## 1) What counts as a seed?

A **seed** is a publicly reachable node that answers the P2P handshake and returns a minimal set of peer addresses so a cold client can find the network.

Seeds do **not** have special consensus power. They’re simply well-run, discoverable endpoints that help new peers connect quickly.

We publish seeds through two mechanisms:

1. **DNS TXT registry (canonical)**  
   - Domain: `seeds.animica.org` (network subdomains below)  
   - Records enumerate multiaddrs and minimal metadata.
2. **JSON mirror (signed)**  
   - URL: `https://seeds.animica.org/<network>.json`  
   - Same content as DNS, with a signature and timestamp for ops & tooling.

> Networks:  
> - `animica.mainnet` (reserved)  
> - `animica.testnet` (public dev/test)  
> - `animica.localnet` (docs/examples; not Internet-routed)

---

## 2) Formats

### 2.1 DNS TXT (authoritative)
- Name: `_p2p.<network>.seeds.animica.org`
- Multiple TXT records; each key=value pair separated by spaces.

**Fields**
- `v` — format version (`1`)
- `addr` — P2P multiaddr (TCP/QUIC/WS)
- `proto` — wire protocol tag (e.g., `animica/1`)
- `region` — ISO or cloud region hint (e.g., `us`, `eu-west`)
- `prio` — positive int; lower is preferred
- `id` — short operator id or label

**Example**

; dig TXT _p2p.animica.testnet.seeds.animica.org +short
“v=1 addr=/ip4/203.0.113.10/tcp/9000 proto=animica/1 region=us prio=10 id=seed-us-1”
“v=1 addr=/dns4/seed-eu-1.animica.org/tcp/9000 proto=animica/1 region=eu prio=20 id=seed-eu-1”
“v=1 addr=/ip6/2001:db8::5/tcp/9000 proto=animica/1 region=ap prio=30 id=seed-ap-1”

### 2.2 JSON Mirror (signed)
Served over HTTPS with detached signature header `X-Animica-Signature: ed25519:<base64>`.

```jsonc
{
  "version": 1,
  "network": "animica.testnet",
  "updated": "2025-10-01T12:00:00Z",
  "seeds": [
    {
      "id": "seed-us-1",
      "addr": "/ip4/203.0.113.10/tcp/9000",
      "region": "us",
      "prio": 10
    },
    {
      "id": "seed-eu-1",
      "addr": "/dns4/seed-eu-1.animica.org/tcp/9000",
      "region": "eu",
      "prio": 20
    }
  ],
  "signature": {
    "alg": "ed25519",
    "key_id": "seeds-2025-q3",
    "signature_b64": "<detached-or-inline>"
  }
}

Client behavior
	•	Prefer DNS TXT; fall back to JSON mirror if DNS unavailable.
	•	De-duplicate and randomize within priority tiers.
	•	Enforce per-seed connection caps and backoffs.

⸻

3) Operator requirements

A host may be listed as a seed if it satisfies the rolling 7-day SLO:
	•	Uptime: ≥ 99.0% process availability (accepting connections).
	•	Handshake: ≥ 99.0% successful P2P handshakes (HELLO) within 2s.
	•	Freshness: Head height lag ≤ 2 blocks for ≥ 99.5% of probes.
	•	Bandwidth: Sustained egress ≥ 50 Mbit/s (burst ≥ 200 Mbit/s).
	•	Limits: Token-bucket rates configured per IP & per topic.
	•	Version: Runs a supported Animica build (not EOL).
	•	Logging & Metrics: Prometheus exporter enabled; basic redaction on.

Recommended:
	•	Anycast or multi-AZ (or at minimum, hot standby) for DNS names.
	•	QUIC enabled where possible; WS transport as a fallback for NAT’d clients.

⸻

4) Rotation policy
	•	Cadence: Weekly (Mondays, 00:00 UTC).
	•	Churn limit: ≤ 25% of listed seeds per cycle (except emergencies).
	•	Entry policy: Candidate must pass soak test for 7 days.
	•	Exit policy: Two consecutive SLO breaches or security incident.
	•	Priority: Weights reflect latency probes & failure rates (lower prio wins).
	•	Regions: Maintain geographic diversity (≥ 3 distinct regions).

Emergency delist
	•	Immediate removal on security events (compromise, malicious behavior, or repeated protocol violations).
	•	A hotfix TXT entry is published within 15 minutes; JSON mirror updated at once.

⸻

5) Liveness checks

The CI/ops job runs from multiple vantage points every 60s:
	1.	TCP/QUIC dial
	•	Connect to addr; complete the HELLO handshake.
	2.	Protocol sanity
	•	Validate proto matches supported set (e.g., animica/1).
	3.	Head freshness
	•	Ask IDENTIFY or HEAD light endpoint; ensure lag ≤ 2 blocks vs reference.
	4.	Ping/RTT
	•	Rolling median; track 95th percentile per region.
	5.	Peer availability hint (optional)
	•	Request a small inventory; ensure non-empty in normal operations.

Thresholds
	•	Handshake success ratio (last 10 probes) ≥ 0.9
	•	Median RTT ≤ 300 ms (per vantage region; warning above 500 ms)
	•	Freshness lag ≤ 2 blocks (critical above 5)

⸻

6) Metrics & alerting

Seeds MUST expose Prometheus (scrape-only; no auth over public Internet unless behind IP ACL).

Core metrics (examples)
	•	p2p_handshake_success_total{peer_id=...,region=...}
	•	p2p_handshake_fail_total{reason=...}
	•	p2p_rtt_ms_bucket / _sum / _count
	•	p2p_head_lag_blocks
	•	p2p_ingress_bytes_total, p2p_egress_bytes_total
	•	p2p_active_conns{remote_region=...,transport=...}

Sample alerts (YAML)

groups:
- name: seeds
  rules:
  - alert: SeedHeadLag
    expr: p2p_head_lag_blocks > 5 for: 5m
    labels: { severity: warning }
    annotations: { summary: "Seed head lag > 5 blocks" }

  - alert: SeedHandshakeErrors
    expr: rate(p2p_handshake_fail_total[5m]) > 2
    for: 10m
    labels: { severity: critical }
    annotations: { summary: "Handshake failures sustained >2/5m" }


⸻

7) Security & abuse controls
	•	Rate limits: Per-IP and global token buckets on handshake & topic ingress.
	•	Connection caps: Limit concurrent conns per CIDR; prefer short TTL for idle conns.
	•	Request validation: Drop malformed frames early (before decoding).
	•	Logging: No full payload logs; redact peer identifiers where unnecessary.
	•	Isolation: Run seeds in DMZ with minimal egress; auto-updates off; pinned artifacts.
	•	Key rotation: Node identity keys rotated quarterly (staggered).

⸻

8) Change management
	•	All additions/removals are done via PR to the seed registry repo with:
	•	Operator contact & on-call rotation.
	•	Proof of soak results (exported metrics or signed attestation).
	•	Region and capacity notes.
	•	CI validates:
	•	DNS TXT format + JSON signature.
	•	Liveness from 3 vantage points.
	•	Churn limit & regional balance.

⸻

9) How to test (operator & user)

9.1 DNS

dig TXT _p2p.animica.testnet.seeds.animica.org +short

9.2 JSON mirror (signature header)

curl -sI https://seeds.animica.org/animica.testnet.json | grep X-Animica-Signature
curl -s https://seeds.animica.org/animica.testnet.json | jq .

9.3 P2P handshake probe (CLI)

# Connect and print IDENTIFY (example CLI)
python -m p2p.cli.peer --connect /dns4/seed-eu-1.animica.org/tcp/9000 --timeout 5

9.4 Freshness check

# Ask a public head endpoint (if exposed) or light RPC:
curl -s https://seed-eu-1.animica.org/healthz


⸻

10) Example seed list (text file, used in tests)

p2p/fixtures/seed_list.txt

# id, addr, region, prio
seed-us-1,/ip4/203.0.113.10/tcp/9000,us,10
seed-eu-1,/dns4/seed-eu-1.animica.org/tcp/9000,eu,20
seed-ap-1,/ip6/2001:db8::5/tcp/9000,ap,30


⸻

11) Operational playbooks

Add a new seed
	1.	Deploy node with supported version; enable metrics & rate limits.
	2.	Soak for 7 days; collect SLO evidence.
	3.	Submit PR adding TXT + JSON entries (with initial prio).
	4.	On merge, CI publishes DNS + mirror; watch alerts for 48h.

Emergency delist
	1.	Open incident; commit a PR removing the entry; merge with admin approval.
	2.	Publish notice in ops channel; monitor client bootstrap success.
	3.	Audit root cause; re-qualify operator before re-listing.

Planned maintenance
	•	Mark as draining (increase prio to deprioritize).
	•	Schedule window; keep one regional seed available at all times.

⸻

12) Privacy considerations
	•	Seeds should not log full client IPs long-term. Aggregate counters are preferred.
	•	Avoid embedding unique IDs in bootstrap responses.
	•	Respect regional data handling requirements.

⸻

13) FAQs

Q: Do seeds need static IPs?
A: Recommended, but DNS A/AAAA behind a stable name is fine. Keep TTL ≤ 300s.

Q: Can a single operator run multiple seeds?
A: Yes, but we cap per-operator presence per region to maintain diversity.

Q: Are WS transports supported for seeds?
A: Yes; advertise a WS multiaddr where helpful for restrictive networks.

⸻

14) Appendix: JSON schema (informal)

{
  "version": "number",
  "network": "string",
  "updated": "RFC3339 timestamp",
  "seeds": [
    { "id": "string", "addr": "multiaddr", "region": "string", "prio": "number" }
  ],
  "signature": { "alg": "ed25519", "key_id": "string", "signature_b64": "string" }
}


⸻

Last updated: 2025-10-10
Owners: @netops, @p2p-core

