---
proposal_id: "GOV-YYYY.MM-PARAM-<ShortSlug>"
proposal_kind: "ParamChange"
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

# Param Change: <Short Title>

> **Status:** Draft → RFC → Vote → Enact → Review  
> **Scope:** Protocol parameters only (no code changes).  
> **Schemas:** `governance/schemas/proposal.schema.json`, `governance/schemas/param_change.schema.json`.

---

## 1) Summary

3–5 sentences on **what** parameters change and **why now**.

- **Impact:** <low|medium|high>  
- **Breaking changes:** <none | describe user-visible effects>  
- **Rollout target:** <block height/version or “epoch N”>

---

## 2) Motivation

- Current values + observed issues (data, graphs, incidents)
- Goals (throughput, latency, security margin, economics)
- Non-goals and constraints (back-compat, limits)

---

## 3) Parameter Deltas (Authoritative)

Fill the **Risk & Bounds Matrix** below. Every entry must include **units** and **bounds**.

### 3.1 Risk & Bounds Matrix

| # | Parameter | Module | Old | New | Min | Max | Unit | Risk (L/M/H) | Invariants / Notes |
|---|-----------|--------|-----|-----|-----|-----|------|---------------|--------------------|
| 1 | `<paramName>` | `<module>` | `X` | `Y` | `>= a` | `<= b` | `<unit>` | `M` | e.g., "Must satisfy a ≤ value ≤ b; affects gas pricing." |
| 2 | … | … | … | … | … | … | … | … | … |

**Risk scale guidance:**  
- **L** = No safety impact, minor perf/econ movement.  
- **M** = Shifts incentives or limits; bounded by invariant checks.  
- **H** = Reduces security margin or DoS headroom if mis-set.

### 3.2 Machine-Readable Payload

Provide `payload.json` (validated by schema) mirroring the table above.

Example skeleton:
```json
{
  "changes": [
    {
      "module": "mempool",
      "name": "min_gas_price",
      "old": "1000",
      "new": "1500",
      "min": "0",
      "max": "1000000000",
      "unit": "wei",
      "rationale": "Align floor with recent EMA; reduce spam.",
      "risk": "M"
    }
  ]
}


⸻

4) Rationale & Alternatives
	•	Why these values (show data, simulations, backtests)
	•	Alternatives considered (and why rejected)
	•	Prior art / other networks

⸻

5) Invariants & Safety Checks

List concrete checks that must hold before/after activation.
	•	DoS headroom: <param> ensures TPS_99 within limits.
	•	Economic balance: base fee burn ≤ X% of rewards (rolling 1k blocks).
	•	Consensus safety: Θ/Γ schedule remains within retarget bounds.
	•	State growth: daily state delta ≤ threshold.
	•	Unit tests updated for new bounds.

⸻

6) Backwards Compatibility
	•	Node operators: required flags/config migrations
	•	Wallets/SDKs: expected no-op unless serialization changes (not allowed here)
	•	Indexers/Explorers: any display/threshold updates

⸻

7) Security Considerations
	•	Abuse scenarios if set too low/high
	•	Interaction with spam/fee markets and rate limiters
	•	Monitoring/alerts to detect regressions; rollback triggers

⸻

8) Economic / Resource Impact
	•	Expected effect on fees/throughput/finality latency
	•	Miner/provider incentives; treasury inflow/outflow deltas
	•	Hardware/IO/gas usage considerations

⸻

9) Rollout Plan
	•	Testnet window & success criteria
	•	Mainnet activation (height/version), canary nodes
	•	Kill switch / revert plan
	•	Communications (release notes, operator guide)

⸻

10) Testing & Validation
	•	Unit / integration tests to update/add
	•	Reproducible data bundles (hashes, time ranges)
	•	Shadow runs or simulators, with links to artifacts

⸻

11) Observability & Success Metrics
	•	Dashboards to watch (head lag, TPS, mempool size, reject rates)
	•	Alert thresholds before/after change
	•	Post-change review date & owner

⸻

Required Files & Schema Validation

proposal.json (envelope) — example:

{
  "proposal_id": "GOV-2025.03-PARAM-fee-floor",
  "kind": "ParamChange",
  "title": "Raise min gas price",
  "network": "mainnet",
  "discussion": "https://forum.example.org/t/…",
  "voting_window": {
    "start": "2025-03-01T00:00:00Z",
    "end":   "2025-03-08T00:00:00Z"
  }
}

Validate:

# Envelope
npx --yes ajv-cli validate \
  -s governance/schemas/proposal.schema.json \
  -d proposal.json --strict=true

# Param payload
npx --yes ajv-cli validate \
  -s governance/schemas/param_change.schema.json \
  -d payload.json --strict=true

Canonical hashes (record in discussion & release notes):

python - <<'PY'
import json,hashlib
def canon(p): return json.dumps(p, separators=(',',':'), sort_keys=True).encode()
env = json.load(open('proposal.json')); pay = json.load(open('payload.json'))
print("proposal_hash=0x"+hashlib.sha3_256(canon(env)).hexdigest())
print("payload_hash=0x"+hashlib.sha3_256(canon(pay)).hexdigest())
PY


⸻

Sign-offs
	•	Module owners ✅
	•	Security review ✅
	•	Economics ✅
	•	Ops/Docs ✅
	•	Rollout lead ✅

Signers (optional): include detached PQ signatures if your process requires them.

