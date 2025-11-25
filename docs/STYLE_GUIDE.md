# Animica Docs — Style Guide

A compact, practical reference for writing across the Animica project: **terminology**, **notation**, **capitalization**, and **code fences**. When in doubt, optimize for clarity and consistency.

---

## 1) Terminology & Notation

### Core consensus terms (PoIES)
- **PoIES** — “Proof-of-Integrated External Signals”. Always uppercase **P-o-I-E-S**.  
  - Examples: “PoIES scorer”, “PoIES acceptance predicate”.
- **Θ (Theta)** — difficulty/acceptance threshold.
- **ψ (psi)** — contribution from a proof (non-negative, capped).
- **Γ (Gamma)** — aggregate cap (global and/or per-type).
- **Σψ ≥ Θ** — canonical acceptance inequality; write as **`Σψ ≥ Θ`** inline.

> ASCII fallbacks (when Greek is impractical): `Theta`, `psi`, `Gamma`. Prefer the Greek symbols in docs and UI unless the context forbids Unicode.

### Proofs & ZK
- **HashShare**, **AIProof**, **QuantumProof**, **StorageHeartbeat**, **VDFProof** — use these exact type names.
- **Groth16**, **PLONK**, **KZG** — capitalize exactly like this.
- **BN254** — no dash; not `BN-254`.
- **Poseidon** — capital P, no “hash” suffix unless disambiguating (“Poseidon hash”).

### Chains & addressing
- **Animica** — the project and chain family name.
- Chain names: **Animica mainnet**, **Animica testnet**, **Animica localnet**.
- Bech32m addresses: prefix **`anim`** → e.g., `anim1…` (monospace, no quotes).

### Modules & surfaces
- **Studio Web**, **Explorer**, **Wallet** (browser extension / Flutter app), **SDK** (Python/TS/Rust).  
  Capitalize as shown.

---

## 2) Capitalization & Naming

- Headings: **Sentence case** (“How proofs are scored”), not title case.
- Proper nouns & acronyms remain capitalized (PoIES, DA, AICF, TEE, RPC).
- Files/paths inline: **monospace** → `core/types/tx.py`.
- Flags, env vars, and schema keys: **monospace**, exact case → `PUBLIC_RPC_URL`, `chainId`, `alg_id`.
- Units & numerics:
  - Use **SI** with narrow non-breaking space before unit (optional in Markdown): `256 KiB`, `10 ms`.
  - Gas/fees: show both symbol and unit if helpful, e.g., `gasUsed: 21,000`.

---

## 3) Punctuation & Formatting

- Use **en-dashes** for ranges (e.g., “blocks 100–120”), and **em-dashes** for asides—sparingly.
- Prefer Oxford commas.
- Avoid smart quotes inside code; prefer straight quotes in code fences.
- Hex: lower-case, `0x` prefix, group with thin spaces only in prose if needed (never in code).

---

## 4) Code Fences (Required Conventions)

Always use fenced blocks with a language tag. Keep copy-pasteability high.

### Shell
```bash
# Do:
omni-sdk call --rpc http://127.0.0.1:8545 --to anim1xyz... --data 0x...

# Show outputs separately; never bake prompts into the command
# Output (truncated):
# status: 200
# result: 0xdeadbeef…

Rules
	•	No leading $  in commands (makes copy/paste harder).
	•	Mark outputs as comments or in a separate block labeled “Output”.

JSON / JSONL

{
  "chainId": 1,
  "thresholdTheta": "1234567",
  "policy": { "GammaCap": "4000000" }
}

	•	Keys use exact casing from the API/spec.
	•	Numbers that can overflow should be strings if that’s what the API expects.

YAML

alg-policy:
  version: 1
  allowed:
    - groth16_bn254
    - plonk_kzg_bn254

Python

from omni_sdk.tx.build import transfer
tx = transfer(to="anim1xyz...", amount=123_000)

TypeScript

import { Client } from "@animica/sdk";
const c = new Client({ rpcUrl: import.meta.env.PUBLIC_RPC_URL });

Rust

let client = animica_sdk::rpc::http::Client::new(rpc_url)?;

CBOR / Hex Examples
	•	Hex values in code blocks MUST be 0x-prefixed and lower-case.
	•	Avoid ellipses inside hex literals; if truncating, comment it:

0xabcdef12…  # truncated for display



⸻

5) Math & Symbols
	•	Inline math: wrap symbols in backticks: Σψ ≥ Θ, ψ_i, Γ_total.
	•	Avoid LaTeX blocks in user-facing docs unless rendering is guaranteed.
	•	When mixing prose and math, prefer short, declarative statements:
“A block is accepted if Σψ ≥ Θ given current policy caps and Γ.”

⸻

6) Examples & Placeholders
	•	Use realistic but non-sensitive examples.
	•	Redact secrets with REDACTED or XXXXXXXXXXXXXXXX.
	•	Use consistent placeholder addresses: anim1qq... and 0xdeadbeef… (comment that it’s truncated).

⸻

7) Links, References, and Cross-Docs
	•	Prefer relative links inside the repo: [PoIES](../consensus/README.md).
	•	External links: describe the destination succinctly:
“See the OpenRPC spec for method shapes.”
	•	Keep link text descriptive (avoid “click here”).

⸻

8) Diagrams
	•	Prefer Mermaid for architecture and flows in docs:

flowchart LR
  Proof[Proof Envelope] --> Map[Adapter → Verifier Input]
  Map --> Verify[Verifier]
  Verify --> Policy{Policy / Γ caps}
  Policy -->|accept| Block
  Policy -->|reject| Error


	•	If static assets are required, use SVG with proper alt text and short captions.

⸻

9) Terminology Do/Don’t

Do
	•	“PoIES acceptance predicate Σψ ≥ Θ.”
	•	“Γ cap per proof type.”
	•	“PLONK with KZG (BN254).”
	•	“Poseidon hash personalization.”

Don’t
	•	“PoIES’s” (avoid possessive forms; rephrase).
	•	“bn-254”, “Bn254”, “Kzg”.
	•	“psi’s” (write ψ or “psi” consistently).

⸻

10) Glue Across Repos
	•	Keep naming in sync with:
	•	spec/*.cddl, spec/*.json (schemas)
	•	zk/docs/* (ZK formats & adapters)
	•	website/src/* (public terminology and UI labels)
	•	If you introduce or rename a term, grep & update across docs and examples.

⸻

11) Quick Checklist (before you open a PR)
	•	Headings use sentence case and outline is logical.
	•	PoIES symbols (Θ, ψ, Γ) used correctly; ASCII fallbacks only if necessary.
	•	Code fences have language tags, copy-pasteable commands, correct casing.
	•	Hex is 0x-prefixed, lower-case; addresses look like anim1….
	•	Links are valid (run the site build + link checker if touching website docs).

⸻

This guide evolves. Propose improvements via PR with concrete examples.
