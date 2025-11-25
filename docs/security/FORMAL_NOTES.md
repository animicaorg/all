# Formal Methods Notes
_Pointers to Lean/K specs, proof obligations, and how they intersect with the implementation._

These notes orient reviewers toward the formal artifacts that accompany the implementation. They complement:
- `spec/formal/README.md` — build instructions and scope
- `consensus/tests/*`, `vm_py/tests/*` — executable test suites that shadow the formal intents

---

## Where the formal artifacts live

- **Lean (consensus/PoIES math)**
  - `spec/formal/poies_equations.lean` — core equations and lemmas:
    - Acceptance predicate: \( S = H(u) + \sum \psi \ge \Theta \)
    - Non-negativity and capping of \( \psi \) per proof type
    - Total-Γ and per-type caps imply bounded contribution
    - Monotonicity of \( S \) vs. added valid proofs (under caps)
    - Retarget EMA invariants (clamps; stability sketch)
  - Status: skeleton with statements + partial proofs; references mirror `consensus/math.py`, `consensus/caps.py`, `consensus/scorer.py`.

- **K Framework (VM core IR small-step)**
  - `spec/formal/vm_smallstep.k` — operational semantics for the Python-VM IR:
    - Deterministic step relation
    - Gas is non-increasing; halts on OOG
    - Event/log ordering is preserved
    - Access-list read/write discipline matches ABI
  - Status: skeleton of rules; intended to be used with kompile (Haskell backend).

- **Build/readme**
  - `spec/formal/README.md` — authoritative instructions to build and run Lean/K artifacts locally, with version pins.

---

## Proof obligations (guide for auditors)

### Consensus (Lean)
1. **Acceptance Correctness**  
   If each included proof meets policy and nullifier constraints, then \( S \ge \Theta \) iff block is accepted.  
   _Artifacts:_ lemmas in `poies_equations.lean`; mirrors `consensus/validator.py`.

2. **Caps Soundness**  
   Per-proof, per-type, and total-Γ caps enforce \( \sum \psi \le \Gamma_{\text{max}} \); no single type can dominate once caps bind.  
   _Artifacts:_ cap lemmas; mirrors `consensus/caps.py`.

3. **Monotonicity & Fairness Hooks**  
   Adding a valid proof never decreases \( S \), but α-tuner keeps long-run fairness within bounds.  
   _Artifacts:_ monotonicity lemmas (Lean), comments reference `consensus/alpha_tuner.py`.

4. **Retarget Stability**  
   EMA-based Θ update converges; clamps prevent oscillation under bounded inter-block jitter.  
   _Artifacts:_ stability assertions; mirrors `consensus/difficulty.py`.

### Execution (K)
1. **Determinism**  
   For any given state and IR program with fixed inputs, step relation is single-valued.  
   _Artifacts:_ no-overlap proof sketches; check with `kprove` where feasible.

2. **Gas Accounting**  
   Total gas consumed equals sum of debits minus refunds; OOG iff meter < cost prior to step.  
   _Artifacts:_ invariants on `Gas` cell; mirrors `vm_py/runtime/gasmeter.py`.

3. **Progress / Preservation (Partial)**  
   Well-typed programs either step or halt with `SUCCESS`/`REVERT`/`OOG`; storage/events shape preserved.  
   _Artifacts:_ typing judgments in K; references `vm_py/compiler/typecheck.py`.

---

## How to build & run (summary; see `spec/formal/README.md`)

### Lean (recommended: elan + Lean 4)
```bash
# From repo root
cd spec/formal
# If using Lake (Lean 4)
lake build
# Or leanpkg if configured
lean --make poies_equations.lean

K Framework (Haskell backend)

cd spec/formal
kompile vm_smallstep.k --backend haskell -O2
# Sample run/claim checks (examples referenced in README)
krun examples/ir_counter.json
kprove proofs/determinism.k


⸻

Trace equivalence & executable oracles

To connect proofs to the live implementations:
	•	Consensus mirror:
Python reference functions (consensus/math.py, scorer.py) export α/Θ/ψ computations used by tests. Lean lemmas annotate the exact formulas; CI pins inputs via test vectors in spec/test_vectors/proofs.json, consensus/tests/*.
	•	VM equivalence:
The interpreter (vm_py/runtime/engine.py) can emit step traces (feature-flag) that are convertible into K configurations for differential checking on small programs (Counter/Escrow). This is used for spot checks, not full mechanized equivalence.

⸻

Trusted Computing Base (formal scope)
	•	Lean/K toolchains, plus tactic engines and backends
	•	Hashing primitives assumed correct (SHA3/Keccak), validated by vectors elsewhere
	•	Poseidon parameters/VKs treated as inputs; correctness of parameters is out-of-scope here (pinned digests in zk/registry/vk_cache.json)

⸻

Reproducibility & pinning
	•	Record the following digests alongside a release (see docs/security/AUDIT_CHECKLIST.md):
	•	poies_equations.lean normalized hash (whitespace-insensitive)
	•	vm_smallstep.k kompiled hash + backend version
	•	spec/formal/README.md toolchain version pins (elan/channel, K version)
	•	CI should run lake build and kompile with exact versions and attach artifacts.

⸻

Open items / roadmap
	•	Formalize Θ retarget stability more rigorously (bounded variance model)
	•	Extend K semantics to cover a larger IR subset (calls/ABI edge cases)
	•	Mechanize cap/fairness proofs for more complex α-tuner dynamics
	•	Optional: Coq/Lean extraction to generate small certified checkers for parts of the consensus logic

⸻

Cross-references
	•	Implementation: consensus/*, vm_py/*
	•	Vectors: spec/test_vectors/*, vm_py/fixtures/*
	•	Tests: consensus/tests/*, vm_py/tests/*
	•	Docs: docs/spec/poies/*, docs/vm/*

