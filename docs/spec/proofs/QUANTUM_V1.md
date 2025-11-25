# QUANTUM_V1 Proofs — trap-based sanity + attested runner

**Status:** Draft, production-intent  
**Scope:** Verifier-facing specification for the *Quantum useful-work* proof (version 1).  
**Consumes:** CBOR envelope per `proofs/schemas/proof_envelope.cddl`, Quantum attestation bundle per `proofs/schemas/quantum_attestation.schema.json`.  
**Produces:** `ProofMetrics.Quantum` used by PoIES scorer; compact receipt for `proofsRoot`.

> PoIES acceptance remains \( S = -\ln u + \sum \psi \ge \Theta \).  
> This document defines how a QUANTUM_V1 proof contributes via a non-negative term \( \psi_{\text{Q}} \) derived from **trap circuits**, **attested runner identity**, **declared resources (width × depth × shots)**, and **QoS**. Caps and diversity rules are enforced by consensus policy (`consensus/caps.py`).

---

## 1) Goals & adversary model

**Goals**
- Attest that declared quantum circuits ran on an identified provider/hardware configuration.
- Verify *minimum correctness* via independent, randomly-chosen **trap circuits** with known outputs.
- Report auditable *work units* aligned with provider resources (qubits, depth, shots) and reference benchmarks.

**Adversary model**
- A provider might fabricate measurement results, replay an earlier run, or simulate with insufficient fidelity.  
- Identities are verified by a provider certificate chain (`proofs/quantum_attest/provider_cert.py`) and optional device attestation.  
- Trap-circuit success must make spoofing computationally/economically unattractive.

---

## 2) Body — CBOR layout (informative)

Canonicalized by `proofs/cbor.py`. Exact schema: `proofs/schemas/quantum_attestation.schema.json`.

QuantumBodyV1 = {

Task & bindings

“chainId”:        uint,                # CAIP-2
“height”:         uint,                # block height anchor
“task_id”:        bstr .size 32,       # H(chainId|height|txHash|caller|payload)
“circuit_id”:     tstr,                # declarative circuit id/version
“circuit_commit”: bstr .size 32,       # H(qasm/circ bytes + params)
“output_commit”:  bstr .size 32,       # H(result histogram / packed shots)

Declared resources (work)

“resources”: {
“qubits”:       uint,                # logical qubits
“depth”:        uint,                # effective circuit depth
“shots”:        uint                 # total measurement repetitions
},

Provider identity & attestation

“provider”: {
“cert_chain”:   [bstr, …],         # X.509/EdDSA/PQ hybrid per provider_cert.py
“id”:           tstr,                # provider id (CN / SAN)
“device”: {
“model”:      tstr,                # e.g., “Rigetti Aspen-M3”, “IonQ Forte”, “IBM Q Nairobi”
“topology”?:  tstr,                # optional topology tag
“fw_rev”?:    tstr                 # firmware/calibration rev (optional)
},
“nonce”:        bstr .size 16,       # anti-replay
“signature”:    bstr                 # signature over BIND blob (see below)
},

Trap circuits

“traps”: {
“seed”:         bstr .size 32,       # derived from beacon(height-1) and task_id
“count”:        uint,                # number of trap instances run
“passes”:       uint,                # traps that passed correctness test
“details”?:     bstr                 # optional compressed per-trap mini-report
},

QoS (optional but recommended)

“qos”: {
“queued_at”:    uint,                # unix ms
“started_at”:   uint,                # unix ms
“finished_at”:  uint,                # unix ms
“availability”: uint                 # percentage × 100 (0–10000)
},

Domain/versioning

“domain”:         tstr,                # “QUANTUM_V1”
}

**Envelope:**
- `type_id = 0x03` (Quantum V1)  
- `body = cbor(QuantumBodyV1)`  
- `nullifier = H("PROOF_NULLIFIER/QUANTUM_V1" || body)[:32]`

All hashes are **SHA3-256** unless stated otherwise.

---

## 3) Deterministic bindings

To prevent replay & cross-context reuse, the provider signs the binding:

BIND = H(
“QUANTUM_V1/BIND” ||
task_id || circuit_id || circuit_commit ||
output_commit || encode(resources) || provider.nonce
)
provider.signature = Sign_provider(BIND)

- `task_id` is deterministic (see `capabilities/jobs/id.py`).
- `seed = H("Q_TRAP_SEED" || beacon(height-1) || task_id)` controls trap selection.
- Policy may enforce a **freshness window** on `height` and `queued_at`.

---

## 4) Provider identity attestation

Verifier (`proofs/quantum_attest/provider_cert.py`) MUST:

1. **Validate certificate chain:** matches trusted **QPU provider roots** in `proofs/attestations/vendor_roots/`.  
2. **Extract provider id & public key** (supports Ed25519 + PQ hybrid per policy).  
3. **Verify** `provider.signature` over `BIND`.  
4. **Optional device attestation:** when providers expose device-level attest tokens, verify firmware/topology claims (out of scope for QUANTUM_V1; policy may require device allowlist).

**Failure modes:** `AttestationError::{ChainInvalid,SignatureInvalid,ProviderNotAllowed,DeviceNotAllowed}`.

---

## 5) Trap circuits — construction & verification

**Intent:** Detect gross fabrication without full tomography.

- Trap family: **Clifford + Pauli** patterns (stabilizer circuits) + simple **randomized compiling**; results are efficiently simulable on CPU.  
- The trap set is chosen deterministically from the global corpus using `traps.seed`.  
- Each trap has a **known distribution** \( P_{\text{trap}} \) over bitstrings.

**Verification rule**

Given observed histogram \( \hat{P} \) from trap shots:

- Compute **passing predicate** per trap:
  - For deterministic traps: majority outcome must match expected bitstring with error tolerance ε.
  - For probabilistic traps: compute total variation distance \( \mathrm{TV}(\hat{P}, P_{\text{trap}}) \le \tau \).

Aggregate:
- `count` = number of traps issued, `passes` = traps that meet predicate.  
- Require `count ≥ m_min` and `passes / count ≥ r_min`. Thresholds are policy-configured and may vary by device class.

This logic is implemented in `proofs/quantum_attest/traps.py` with helper math in `proofs/utils/math.py`.

---

## 6) Resource-to-units mapping

The verifier emits *work units*; pricing/ψ mapping remains policy-only.

Let:
- `Q = resources.qubits`
- `D = resources.depth`
- `S = resources.shots`

From `proofs/quantum_attest/benchmarks.py`, the network maintains **reference scaling** per device family and circuit class:

\[
\text{quantum\_units} = \alpha_{\text{family}} \cdot Q \cdot D \cdot \log(1 + S)
\]

- `α_family` comes from reference benches; policy may clamp `Q, D, S` to maxima to prevent inflation.

---

## 7) QoS metrics

If provided, compute:
- `latency_ms = finished_at - started_at`
- `queue_ms = started_at - queued_at` (optional)
- `availability = availability / 100.0` (0–100 → 0–1)

Policy maps QoS to a multiplier \( w_{\text{qos}} \in [w_{\min}, 1] \).

---

## 8) Metrics → ψ mapping (policy adapter)

The verifier outputs **metrics only**:

ProofMetrics.Quantum = {
quantum_units: float,
traps_ratio:   float,   # passes / count
qubits:        uint,
depth:         uint,
shots:         uint,
latency_ms?:   uint,
availability?: float    # 0..1
}

Network policy (see `consensus/policy.py`) selects:

\[
\psi_{\text{Q}} = \min\Big( w_{\text{family}} \cdot U \cdot f_{\text{traps}}(r) \cdot f_{\text{qos}}(\ell, A),\; \text{cap}_{\text{Q}} \Big)
\]

- \(U = \text{quantum\_units}\), \( r = \text{traps\_ratio} \).

---

## 9) Reference verification algorithm

Pseudocode (see `proofs/quantum.py` for authoritative logic):

```python
def verify_quantum_v1(env):
    body = decode_cbor(env.body, schema="quantum_attestation.schema.json")
    assert body["domain"] == "QUANTUM_V1"

    # 1) Provider identity
    bind = H(b"QUANTUM_V1/BIND" +
             body.task_id + enc(body.circuit_id) + body.circuit_commit +
             body.output_commit + enc(body.resources) + body.provider.nonce)
    verify_provider_cert(body.provider.cert_chain, allowlist=POLICY.providers)
    verify_provider_signature(body.provider, bind)

    # 2) Trap set
    seed = H(b"Q_TRAP_SEED" + beacon(body.height - 1) + body.task_id)
    r = verify_traps(seed, body.traps, policy=POLICY.traps)  # returns passes/count

    # 3) Units
    fam = classify_device_family(body.provider.device.model)
    alpha = BENCH.alpha_for(fam)
    units = alpha * body.resources.qubits * body.resources.depth * log1p(body.resources.shots)

    # 4) Nullifier
    assert env.nullifier == H(b"PROOF_NULLIFIER/QUANTUM_V1" + env.body)[:32]

    # 5) Emit metrics
    metrics = ProofMetrics.Quantum(
        quantum_units=units,
        traps_ratio=r,
        qubits=body.resources.qubits,
        depth=body.resources.depth,
        shots=body.resources.shots,
        latency_ms=(body.qos.finished_at - body.qos.started_at) if "qos" in body else None,
        availability=(body.qos.availability / 100.0) if "qos" in body else None
    )
    return metrics

Failures raise ProofError::{Schema, Attestation, TrapFail, Binding}.

⸻

10) Privacy & data volumes
	•	Only commitments to circuit and outputs are required on-chain.
	•	Optional traps.details may be compressed; verifiers need only the aggregate (count, passes) if they can recompute traps deterministically from seed.

⸻

11) Interactions & economics
	•	Capabilities bridge: capabilities/jobs/attest_bridge.py normalizes provider results to QuantumBodyV1.
	•	AICF pricing: aicf/economics/pricing.py converts quantum_units (+ QoS) into rewards; SLA evaluation in aicf/sla/* re-checks traps and trends.
	•	Settlement & slashing: aicf/economics/settlement.py, aicf/sla/slash_engine.py.

⸻

12) Parameters & policy

Per-network configurable:
	•	Allowlisted providers and (optionally) device models.
	•	Trap thresholds: m_min, r_min, and TV distance τ for probabilistic traps.
	•	Unit caps for qubits, depth, shots and α_family weights.
	•	Freshness windows for height, queue/start timestamps.

⸻

13) Security considerations
	•	Signature & nonce: Ensure provider.nonce is unique per submission; reject replayed signatures.
	•	Seed secrecy: Trap selection is public but selection depends on chain randomness; front-running benefits are limited because predicate is not easily improvable without real runs.
	•	Simulator risk: High trap ratios at nontrivial sizes reduce feasibility of classical spoofing; policy can require minimum qubits/depth and higher trap strictness for large claims.
	•	Device drift: If device-level drift affects traps, providers should expose calibration snapshots linked in device.fw_rev; policy may penalize outdated revs.

⸻

14) Test vectors
	•	proofs/test_vectors/quantum.json — success & failure cases; varying trap ratios.
	•	aicf/tests/test_quantum_attest.py — provider cert parsing & signature checks.
	•	proofs/tests/test_quantum.py — end-to-end metric extraction.

⸻

15) Versioning
	•	domain = "QUANTUM_V1", type_id = 0x03.
	•	Future QUANTUM_V2 may support:
	•	Multi-provider cross-checking proofs.
	•	Attested device telemetry (error rates) folded into policy.
	•	SNARK-wrapped trap verification for concise on-chain proofs.

⸻

Notation & hashing
	•	H(x) = SHA3-256; CBOR is canonical.
	•	Fixed-point conventions per consensus/math.py.

