# AICF Fixtures

This folder holds small, deterministic sample payloads used by tests, CLIs, and docs
for the **AI Compute Fund (AICF)** module. Files here are intentionally tiny and
human-readable so you can understand flows end-to-end.

> Tip: Fixtures are stable inputs — keep keys sorted and avoid whitespace churn so
> hashing and golden tests remain deterministic.

## What belongs here

- **Provider samples** – minimal provider registrations and attestations.
- **Job specs** – representative AI/Quantum job requests used by the queue/dispatcher.
- **Heartbeats** – provider liveness samples for P2P/RPC examples.
- **Proof claims** – canonical “completed work → proof → claim” examples.
- **Payout snapshots** – settlement inputs/outputs for economics tests.

At the moment this directory only contains this README. Add files as your tests or
docs require them (see examples below).

## Conventions

- **Format:** JSON (`.json`) or JSON Lines (`.jsonl`) for streams. Use UTF-8.
- **Key ordering:** sort keys in files you commit (tools in this repo do this by default).
- **IDs:** Use short, readable IDs like `prov_demo_01`, `task_demo_ai_01`.
- **Timestamps:** Unix seconds (ints).
- **Units:** Keep job “units” consistent with `aicf/economics/pricing.py`.

## Example fixtures (copy/paste templates)

### `provider_demo.json`
```json
{
  "provider_id": "prov_demo_01",
  "caps": { "ai": true, "quantum": false },
  "stake": { "amount": 1000000, "locked_until": 0 },
  "endpoints": { "rpc": "https://demo.provider.example/rpc", "p2p": "12D3KooDemoPeerId" },
  "attestation": {
    "kind": "tee",
    "vendor": "intel-tdx",
    "bundle_hash": "0x" 
  },
  "status": "active"
}

job_ai_demo.json

{
  "job_id": "task_demo_ai_01",
  "kind": "AI",
  "request": {
    "model": "demo-llm-1",
    "prompt": "Count to three.",
    "max_tokens": 8
  },
  "units": 50,
  "fee_tip": 1234,
  "created_at": 1710000000
}

job_quantum_demo.json

{
  "job_id": "task_demo_q_01",
  "kind": "Quantum",
  "request": {
    "circuit_ir": "OPENQASM 3.0; // tiny",
    "shots": 64,
    "traps": 4
  },
  "units": 20,
  "fee_tip": 777,
  "created_at": 1710000001
}

heartbeat_demo.json

{
  "provider_id": "prov_demo_01",
  "height": 12345,
  "timestamp": 1710000100,
  "capacity_ai": 10,
  "capacity_qp": 0,
  "qos": 0.99,
  "nonce": 3
}

proof_claim_ai_demo.json

{
  "task_id": "task_demo_ai_01",
  "nullifier": "0x",
  "height": 12346,
  "metrics": { "tokens": 42, "qos": 0.98, "latency_ms": 250 },
  "proof_ref": { "kind": "tee", "digest": "0x" }
}

payouts_demo.jsonl

Each line is a settlement row:

{"provider_id":"prov_demo_01","task_id":"task_demo_ai_01","amount": 4242,"epoch": 7}

How tests typically use fixtures
	•	Load JSON with json.load(...) and feed into dataclass constructors in
aicf/types/*.
	•	Keep “wire” vs “state” representations separate: fixtures are usually wire-shaped.
	•	Golden tests may compare sorted, minified JSON (separators=(",",":")).

Adding new fixtures
	1.	Choose a descriptive filename (<kind>_<scenario>.json).
	2.	Keep fields to the minimum needed for the target test.
	3.	Validate against the dataclasses/schemas under aicf/types/*.
	4.	Commit with sorted keys so diffs are stable.

