# Templates — Changelog

This file tracks **source-level** changes to first-party templates under `contracts/templates/`.
It follows a simplified “Keep a Changelog” structure with **semantic versioning** semantics for
templates:

- **MAJOR**: Breaking change to a template’s **ABI**, **storage layout**, or **behavioral invariants**.
- **MINOR**: Backwards-compatible feature additions (new functions/events, optional params, docs),
  or changes that only affect **build tooling** without altering on-chain interface/behavior.
- **PATCH**: Bug fixes and non-functional changes (comments, readability, tests, lints) that do not
  change code hash in a material release. (Note: even a whitespace change will change code hash;
  see **Reproducibility & Code Hashes** below.)

> **Important:** A template’s **code hash** (and therefore deploy verification) changes with any
> source change, even when the ABI does not. If you are pinning code hashes in manifests or proxies,
> expect to bump those pins when you adopt a new template version.

---

## Unreleased

### Added
- **Template guidance & playbook**: expanded `templates/TEMPLATES.md` with:
  - A matrix of purposes, storage layouts, ABIs, and events for each template.
  - Step-by-step scaffold → build → deploy → verify flow.
  - Capability integration notes (AI/Quantum/DA/Randomness).
- **Operational notes** for template maintainers: policy on versioning, migration, and review checklist.

### Changed
- Clarified **determinism** caveats for capability-aware templates:
  - Emphasized “enqueue now, consume next block” contract pattern for AI jobs.
  - Documented size caps and transcript binding for quantum receipts.

### Fixed
- Minor wording fixes in docs (no functional changes).

### Migration
- No action required until a concrete release below is adopted.

---

## v0.3.0 — 2025-10-04

**Release theme:** Capability-aware scaffolds and a fuller developer path from simulator → devnet.

### Added
- **New template: `ai_agent/`**
  - ABI:
    - `enqueue(model: bytes, prompt: bytes) -> {"task": bytes}`
    - `consume(task: bytes) -> {"ok": bool, "result": bytes}`
  - Events: `AIEnqueued(task, model)`, `AIConsumed(task, ok)`
  - Integrates with `capabilities` host (AI). Deterministic **task_id** derivation matches
    capabilities/jobs/id rules (height|txHash|caller|payload).
  - Includes `tests_local.py` for VM-only simulation.

- **New template: `quantum_rng/`**
  - ABI:
    - `mix(commitment: bytes, receipt: bytes) -> {"mix": bytes32}`
    - `get() -> {"mix": bytes32}`
  - Event: `QuantumMixed(mix)`
  - Demonstrates extract-then-xor of the **randomness beacon** with quantum bytes (receipt-bound).

- **Docs**
  - `templates/TEMPLATES.md` (catalog + playbook).
  - Capability pitfalls and sizing recommendations.

### Changed
- **`escrow/` documentation**: clarified state enum and deadline semantics (block-context,
  not wall-clock).
- **`counter/`**: tightened comments around gas and receipt determinism; no logic/ABI change.

### Fixed
- N/A (no functional bug fixes in templates themselves for this release).

### Migration
- If you are adopting `ai_agent/` or `quantum_rng/`, ensure your devnet has:
  - `capabilities` mounted (AI/Quantum), and
  - `randomness` beacon enabled (for `quantum_rng/`).
- Rebuild packages and update any **code hash pins** in manifests or proxies.

---

## v0.2.0 — 2025-08-18

**Release theme:** Practical stateful workflows.

### Added
- **New template: `escrow/`**
  - Storage layout:
    - `payer`, `payee`, `amount`, `state`, `deadline`
  - ABI: `fund`, `release`, `refund`, `dispute`, `status`
  - Events: `EscrowFunded`, `EscrowReleased`, `EscrowRefunded`, `EscrowDisputed`
  - Deterministic deadline using **block context**, not wall-clock.

### Changed
- **`counter/`**: added `CounterIncremented(value)` event for better UX in explorers and tests.

### Fixed
- N/A

### Migration
- If you previously consumed `counter/` without events, consider subscribing to logs now.
- Rebuild & re-verify to refresh code hash if you switch to the evented `counter/`.

---

## v0.1.0 — 2025-07-01

**Initial release of templates.**

### Added
- **`counter/`** — minimal stateful example:
  - ABI: `inc()`, `get() -> {"value": int}`
  - Single storage key `b"count" -> int`
- Basic README stubs.

---

## Reproducibility & Code Hashes

- **Deterministic builds:** The Python-VM toolchain yields a deterministic **code hash** from the
  exact source bytes and compilation pipeline. Any textual change (even whitespace or comments)
  will change the hash.
- **Manifests:** Keep `manifest.json` in the template directory authoritative for ABI and metadata,
  but treat the **code hash** as the build output’s responsibility. When you adopt a new template
  version, rebuild the package and update any on-chain verification or proxy pins.
- **Verification:** Use `contracts/tools/verify.py` (via studio-services or locally) to confirm that
  the deployed code matches your source and manifest after upgrades.

---

## Versioning Policy (templates)

- **MAJOR**: Storage layout or ABI change (breaking). Expect downstream migrations.
- **MINOR**: New functions/events that do not break existing callers; doc improvements; example tests.
- **PATCH**: Internal refactors, lint/format, comments. (Still changes code hash if the source file
  changes—plan verifications accordingly.)

**Template set version vs individual templates:** The version here tracks the **set** of templates
as shipped together. Individual templates may note per-template impact in each entry.

---

## Migration Checklist

1. **Read the entry** for the version you’re adopting.
2. **Rebuild** your package(s):  
   `python -m contracts.tools.build_package --source <template>/contract.py --manifest <template>/manifest.json --out contracts/build/<name>.pkg.json`
3. **Re-verify** on the target network (or through studio-services).
4. **Update pins**: if using proxy pinning or code-hash checks, bump pins to the new hash.
5. **Run local tests** shipped with the template:
   `pytest -q contracts/examples/*/tests_local.py` (or your app tests).
6. If using **capabilities** or **randomness**, make sure your devnet is configured to expose those
   modules (see ops/devnet docs).

---

## Template Inventory (at a glance)

| Template       | Introduced | Latest Affected | ABI Stability | Notes                                     |
|----------------|------------|-----------------|---------------|-------------------------------------------|
| `counter/`     | v0.1.0     | v0.2.0          | Stable        | Event added in v0.2.0 (non-breaking)      |
| `escrow/`      | v0.2.0     | v0.2.0          | Stable        | Deterministic state machine                |
| `ai_agent/`    | v0.3.0     | v0.3.0          | Stable        | Requires capabilities (AI)                 |
| `quantum_rng/` | v0.3.0     | v0.3.0          | Stable        | Requires capabilities (Quantum) + beacon   |

---

## How to Compare Releases (no remote links)

- Show changes since a tag:
  ```bash
  git log --oneline v0.2.0..v0.3.0 -- templates/

	•	Inspect diffs for a specific template:

git diff v0.2.0..v0.3.0 -- contracts/templates/ai_agent



⸻

Maintainer Notes
	•	Update this changelog in the same PR that changes template sources or manifests.
	•	For capability-aware templates, record minimum node module versions if relevant.
	•	Keep entries concise but explicit about ABI, storage, and behavior changes.

