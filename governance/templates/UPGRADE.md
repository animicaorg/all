---
proposal_id: "GOV-YYYY.MM-UPGRADE-<ShortSlug>"
proposal_kind: "Upgrade"
network: "<mainnet | testnet | devnet>"
authors:
  - name: "<Your Name>"
    contact: "<@handle or email>"
sponsors: []
created: "YYYY-MM-DD"
discussion: "https://forum.example.org/t/…"
related_proposals: []
snapshot:
  height: 0
  time: "YYYY-MM-DDTHH:MM:SSZ"
voting_window:
  start: "YYYY-MM-DDTHH:MM:SSZ"
  end: "YYYY-MM-DDTHH:MM:SSZ"
---

# Protocol Upgrade: <Codename or Short Title>

> **Status:** Draft → RFC → Vote → Enact → Review  
> **Scope:** Binary/protocol upgrade gated by feature flags and activation schedule.  
> **Schemas:** `governance/schemas/proposal.schema.json`, `governance/schemas/upgrade.schema.json`.

---

## 1) Executive Summary

- **From → To:** `vA.B.C` → `vX.Y.Z`  
- **Activation:** `<height | epoch | time T>` (authoritative in payload)  
- **Fork class:** `<hard | soft | none>`  
- **Feature gates:** `["capabilities.v1", "poies.alpha_tuner", …]`  
- **Breaking risks:** <low|medium|high> (see Risk Matrix)  
- **Rollout:** canary → testnet soak → mainnet staged activation  
- **Abort switches:** documented in §7

---

## 2) Motivation

What problems this upgrade solves (security, performance, economics, UX), with data and links to issues/PRs.
Non-goals and constraints (back-compat windows, storage stability, APIs that MUST NOT change).

---

## 3) Scope of Changes

- **Consensus:** headers/blocks/Θ/Γ math, acceptance rules (if any)
- **Networking:** P2P message ids, handshake, rate limits
- **Execution:** gas tables, VM opcodes/stdlib
- **RPC:** new/changed endpoints (OpenRPC diff)
- **DA/Randomness/AICF:** schema updates or params
- **Migrations:** DB/state/index format deltas (see §5)

---

## 4) Risk & Compatibility Matrix

| Area | Change | Compat Mode | Backward Reads | Forward Reads | Notes / Invariants |
|------|--------|-------------|----------------|---------------|--------------------|
| Consensus | New feature gates | minor | yes | guard-railed | Pre-activation must be no-op |
| DB | State snapshot vN+1 | migrate-at-height | read-only via shim | no | Online, bounded-time migration |
| RPC | Method `x.y` added | additive | n/a | n/a | Old clients unaffected |
| VM | Gas cost updates | feature flag | deterministic | deterministic | Reproducible vectors updated |

**Release gating invariants:**  
- All consensus-affecting vectors pass on `linux-amd64`, `linux-arm64`, `macOS`.  
- P2P cross-version interop: old(node=vA) ⇄ new(node=vX) sync headers pre-activation.

---

## 5) Data & State Migrations

- **When:** block `<height>` ± grace `Δ`  
- **What:** tables/keys/indexes; expected runtime and worst-case bounds  
- **How:** online, chunked; resumable checkpoints (`meta:migration:<id>`)  
- **Rollback note:** pre-migration snapshot kept until §8 “post-activation review”.

**Operator command (example):**
```bash
animica-node migrate --target vX.Y.Z --at-height <H> --db sqlite:///animica.db


⸻

6) Activation Plan (Multi-Stage)

Stage A — Canary Build
	•	Branch/tag: <release/x.y>; artifacts for a small operator set (<5% stake).
	•	Success criteria: no crash, stable mempool, head lag < threshold for 24h.

Stage B — Testnet Soak
	•	Activate at testnet height <Ht>; run ≥ N epochs.
	•	KPIs: orphan rate, PoIES acceptance %, TPS p50/p95, DA sampling pass%.

Stage C — Mainnet Pre-Activation
	•	Roll binary rollout window: 30% → 60% → 90% nodes upgraded (observed via identify caps).
	•	Ensure min_version quorum met: min_version = vX.Y.0.

Stage D — Mainnet Activation
	•	Gate: feature_flags.enable_at_height = <H> (authoritative).
	•	Freeze policy: no non-critical merges 72h prior.

⸻

7) Abort Switches & Rollback

Abort (pre-activation):
	•	Set feature_flags.abort=true via on-chain param or ops knob → deactivates at <H>; upgrade becomes no-op.

Emergency Pause (post-activation but within grace K blocks):
	•	Switch consensus.feature_<name>=off (subset of gates), keeps nodes interop.
	•	Hard abort (if consensus fault detected): coordinated rollback to snapshot at H-1 and release vX.Y.Z-hotfix.

Triggers (any ⇒ abort):
	•	Invalid block acceptance divergence
	•	Head lag > threshold for 30 min across ≥ X% peers
	•	DA sampling failure rate > Y%
	•	Crash loop rate > Z / hour

Rollback Steps (scripted):

animica-node stop
animica-tools snapshot-restore --db animica.db --snapshot /backups/pre-H
animica-node pin-version vA.B.C && animica-node start --safe-mode


⸻

8) Monitoring & Runbooks
	•	Dashboards: consensus health, fork choice, mempool, DA, randomness, P2P RTTs
	•	Alerts: thresholds pre/post activation with overrides for soak period
	•	Runbooks: §7 scripts + operator paging tree

⸻

9) Operator Actions

Before activation:
	•	Upgrade binaries; verify --version and OpenRPC schema hash
	•	Dry-run migrations; ensure disk/IO headroom
	•	Backup snapshot; test restore

At activation:
	•	Watch logs for feature gates; verify head/Θ updates

After activation:
	•	Remove abort flag if set; prune pre-H snapshot after T days

⸻

10) Communications
	•	Release notes, operator guide, wallet/SDK notes
	•	Public calendar for activation window
	•	Status page banners; social & dev channels

⸻

11) Machine-Readable Payload

Provide proposal.json (envelope) and payload.json (upgrade specifics).

proposal.json (envelope):

{
  "proposal_id": "GOV-2025.05-UPGRADE-aurora",
  "kind": "Upgrade",
  "title": "Aurora Upgrade",
  "network": "mainnet",
  "discussion": "https://forum.example.org/t/aurora",
  "voting_window": { "start": "2025-05-01T00:00:00Z", "end": "2025-05-08T00:00:00Z" }
}

payload.json (authoritative):

{
  "from_version": "v1.6.3",
  "to_version": "v1.7.0",
  "activation": { "height": 1234567 },
  "hardfork": true,
  "min_version": "v1.7.0",
  "feature_gates": ["vm_py.strict_mode", "da.nmt.v2"],
  "abort_switch": { "key": "upgrade.aurora.abort", "default": false },
  "migrations": [
    {"id": "state_v7", "component": "db.state", "online": true, "bounded_ms": 180000}
  ],
  "prerequisites": ["testnet-soak:passed", "dashboards:green", "backup:complete"]
}

Validate:

npx --yes ajv-cli validate \
  -s governance/schemas/proposal.schema.json -d proposal.json --strict=true
npx --yes ajv-cli validate \
  -s governance/schemas/upgrade.schema.json -d payload.json --strict=true

Canonical hashes (record in notes & forum):

python - <<'PY'
import json,hashlib
canon=lambda o: json.dumps(o,sort_keys=True,separators=(',',':')).encode()
env=json.load(open('proposal.json')); pay=json.load(open('payload.json'))
print("proposal_hash=0x"+hashlib.sha3_256(canon(env)).hexdigest())
print("payload_hash=0x"+hashlib.sha3_256(canon(pay)).hexdigest())
PY


⸻

12) Test Plan
	•	Unit/integration tests for all changed modules
	•	Cross-version sync tests (old↔new) pre-activation
	•	Deterministic vectors updated (IDs, receipts, gas)
	•	Devnet dress rehearsal: scripted activation + abort drill

⸻

13) Post-Activation Review (T+7 days)
	•	KPIs vs targets; incident report if thresholds exceeded
	•	Decide to unpin abort switch permanently
	•	Archive artifacts (binaries, SBOMs, checksums, dashboards)

⸻

Sign-offs
	•	Module owners ✅
	•	Security ✅
	•	Economics ✅
	•	Ops/Docs ✅
	•	Release/Comms ✅

