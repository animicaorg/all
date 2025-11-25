# Animica — Formal Models (`spec/formal/`)

This folder hosts two complementary formal artifacts:

1) **Lean 4 (mathlib)** — a small library of PoIES lemmas and safety facts:  
   `poies_equations.lean` formalizes the entropy draw `H(u)`, non-negativity and caps on per-proof scores `ψ`, and the *acceptance predicate* `H(u) + Σψ ≥ Θ`.

2) **K Framework** — an executable small-step semantics for the Animica core IR used by the Python VM:  
   `vm_smallstep.k` models a deterministic stack machine with gas, storage, events, and **pure** (uninterpreted) syscall stubs (blob pin, AI/quantum enqueue, random). It is suitable for testing determinism, control-flow safety, and gas monotonicity.

---

## 0) What lives here

spec/formal/
├─ README.md                 ← this file
├─ poies_equations.lean      ← Lean 4 + mathlib lemmas for PoIES acceptance
└─ vm_smallstep.k            ← K small-step semantics for the VM IR

> The VM IR is an *abstract* target for the Python VM. Gas figures here are illustrative; production costs are defined canonically in `spec/opcodes_vm_py.yaml`.

---

## 1) Build prerequisites (Ubuntu 22.04)

### Lean 4 + mathlib
- **elan** (Lean toolchain manager)
- **lake** (Lean build tool) – bundled with modern Lean toolchains
- **git**, **curl**

Install:
```bash
# Elan (Lean toolchain manager)
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh -s -- --default-toolchain stable
exec $SHELL

# Check versions
lean --version
lake --version

We recommend Lean 4 “stable”. If you need a pin, write a lean-toolchain file with a concrete version (e.g. leanprover/lean4:stable).

K Framework
	•	K (backend: LLVM recommended), Java ≥ 11, clang/llvm, cmake, make

Install K from binaries or build from source (see kframework.org). Verify:

kompile --version
krun --version


⸻

2) Building & checking the Lean model

This repo does not force a Lake project in spec/formal/, but the fastest way to use mathlib caching is to give Lake a tiny project file. Create these once if you don’t already have a Lean project at the repo root:

Minimal lakefile.lean (optional but recommended):

import Lake
open Lake DSL

package animica_formal

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "stable"

lean_lib AnimicaFormal

Minimal lean-toolchain:

leanprover/lean4:stable

Now build:

# From repo root or spec/formal (if you placed lake files there)
lake exe cache get    # fetch mathlib build cache (first time only)
lake build            # compile

Quick direct check without Lake (works for small files, but no cache):

lean --make spec/formal/poies_equations.lean

What to expect
	•	The file should compile without sorry.
	•	You’ll see named theorems like H_nonneg, psi_nonneg, accept_of_H_add_sumpsi_ge_Theta, etc.

⸻

3) Running the K small-step VM

Compile the semantics

cd spec/formal
mkdir -p .build/k-llvm
kompile vm_smallstep.k --backend llvm -d .build/k-llvm

Run a tiny program

Below, we push 2 and 3, add, return, with enough gas:

krun -d .build/k-llvm --output pretty \
  --depth 100 \
  --pattern "<animica>
               <k> PUSH 2 ~> PUSH 3 ~> ADD ~> RETURN </k>
               <gas> 100 </gas>
               <status> RUNNING </status>
               <stack> .List </stack>
               <mem> .Map </mem>
               <store> .Map </store>
               <logs> .List </logs>
               <ret> 0 </ret>
               <env> <blockTs> 0 </blockTs> <coinbase> 0 </coinbase> <caller> 0 </caller> <value> 0 </value> <chainId> 1 </chainId> </env>
               <seeds> <randSeed> 1 </randSeed> </seeds>
             </animica>"

Expected end state highlights:
	•	<status> becomes "SUCCESS".
	•	<ret> becomes 5.
	•	<gas> equals 100 - (gasOf PUSH + PUSH + ADD + RETURN).

Tip: You can also pass cell values via CLI flags (e.g. -ck='PUSH 1 ~> …' -cgas=50) if your K install supports per-cell overrides. The explicit <animica>…</animica> pattern above is the most portable.

Control-flow & gas safety smoke
	•	OOG case:

krun -d .build/k-llvm --output pretty \
  --pattern "<animica> <k> PUSH 1 ~> ADD </k> <gas> 1 </gas> ... </animica>"

Should end with <status> "OOG" and an empty <k>.

	•	Conditional jump fall-through:

PUSH 0 ~> JUMPI 2 ~> PUSH 1 ~> HALT

Leaves the PUSH 1 in place because the condition is 0.

⸻

4) Scope & limits

Lean PoIES model

In scope
	•	Entropy mapping H(u) = -log(u) (over reals), domain safety (u ∈ (0,1]).
	•	Non-negativity of ψ-components and capped totals.
	•	Acceptance predicate: H(u) + Σψ ≥ Θ.
	•	Monotonicity and stability lemmas (if Θ tightens, acceptance doesn’t spuriously increase).

Out of scope (here)
	•	Full on-chain difficulty retargeting EMA and fork choice (covered in executable code + tests).
	•	Vendor-specific attestation semantics (TEE/QPU) — treated parametrically via assumptions on ψ-inputs.

K VM small-step

In scope
	•	Deterministic evaluation, gas accounting, halting (RETURN, REVERT, HALT, OOG).
	•	Memory & storage as total maps (default 0).
	•	Pure syscall stubs: BLOB_PIN, AI_ENQ, Q_ENQ, RANDOM as uninterpreted functions.

Out of scope
	•	Real cryptographic hashes, DA pinning, AI/Quantum execution, networking — these are modeled abstractly to prove determinism and control. Concrete verification happens in the Python code with test vectors.

⸻

5) How these models are used
	•	Lean gives machine-checked invariants that the acceptance predicate cannot accept a block unless its total score meets the threshold and that component scores are never negative or exceed caps. These facts guide consensus test design and are referenced in the spec.
	•	K provides an executable semantics for the IR used by the Python VM. It is used for:
	•	Round-trip meta-tests (e.g., control-flow well-formedness, gas monotonicity).
	•	Checking that transformations (e.g., peephole compilation) preserve observable behavior.

⸻

6) Keeping versions reproducible
	•	Pin Lean toolchain via lean-toolchain; fetch mathlib cache with lake exe cache get.
	•	Keep K pinned via your package manager or a nix/container recipe in ops/ (see repo).
	•	Gas costs must match spec/opcodes_vm_py.yaml. The K file’s gasOf table is a mirror for proofs/tests; if you change the spec, change it here too.

⸻

7) Troubleshooting
	•	Lean can’t find mathlib
Ensure lake update runs without errors and that your lean-toolchain is a Lean 4 release compatible with the chosen mathlib revision.
	•	K kompile fails on LLVM backend
Make sure clang and llvm development packages are installed; try the --backend kore or --backend haskell as a sanity check.
	•	krun complains about configuration variables
Prefer the explicit <animica> ... </animica> pattern shown above, which does not rely on CLI cell overrides.

⸻

8) Next steps
	•	Add small example programs and golden end-states under spec/formal/examples/ and drive them with a tiny shell script that calls krun and diffs the <ret>, <status>, and <gas> cells.
	•	Extend poies_equations.lean with retarget window inequalities and fairness bounds as you finalize the consensus schedule.

If you keep the Lean & K artifacts aligned with the spec, they become living documentation: when the spec changes, the proofs or semantics will fail loudly until you update them.

