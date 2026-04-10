# Animica Spec

This directory is the **single source of truth** for Animica’s on-chain formats, math, and policy roots. Everything else (node, SDKs, wallet, explorer, studio) is generated to conform to what lives here.

## What lives here

- **`params.yaml`** — Canonical chain/economic/consensus parameters (Θ, Γ caps, gas tables, block limits, issuance).
- **`poies_policy.yaml`** — PoIES ψ-mapping knobs, per-type caps, diversity/escort rules, total Γ cap.
- **`pq_policy.yaml`** — Post-quantum signature/KEM policy; alg IDs; thresholds; alg-policy Merkle layout.
- **`chains.json`** — CAIP-2 style chain registry (`animica:1` mainnet, `:2` testnet, `:1337` devnet).
- **`domains.yaml`** — Domain separators/personalization strings for hashing, signing, and nullifiers.
- **`*.cddl`** — CBOR schemas (transactions, headers, blob/DA envelopes).
- **`abi.schema.json`** — JSON-Schema for Python-VM contract ABIs.
- **`manifest.schema.json`** — Deployable package schema (code/ABI/caps/resources).
- **`openrpc.json`** — JSON-RPC surface for nodes and tools.
- **`opcodes_vm_py.yaml`** — Deterministic Python-VM opcode set and gas costs.
- **`alg_policy.schema.json`** — JSON-Schema for PQ alg-policy tree objects (for hashing into a root).
- **`poies_math.md`** — Human-readable PoIES math: \( S = -\ln(u) + \sum \psi(p) \ge \Theta \), retarget & fairness notes.
- **`test_vectors/`** — Canonical round-trip/accept-reject vectors (txs, headers, proofs, VM programs).
- **`formal/`** — Optional formal artifacts (Lean lemmas for PoIES; K-framework small-step for the VM IR).

> Golden rule: **the spec drives the code**. Implementations must not “extend” formats beyond these files.

---

## Canonical encoding conventions

### CBOR (CTAP2/Deterministic)
All on-chain byte encodings MUST be **deterministic CBOR**:
- **No indefinite-length** items.
- **No floating points** (integers and byte/text strings only).
- **Smallest encodings** for integers/lengths (major type rules).
- **Map key ordering:** bytewise lexicographic on the *canonical* encoded keys.
- **Tags:** forbidden, unless explicitly allowed in the corresponding `.cddl`.
- **Semantic versioning:** schemas may add *optional* fields only; never change meaning of existing ones.

### JSON
Used for manifests/ABIs and tooling, never for consensus:
- All objects must be **key-sorted** when hashed.
- Numbers are **strings** if they could exceed 2⁵³−1.
- Byte arrays are **0x-prefixed lowercase hex**. (No base64 in consensus paths.)

### Hashing & domain separation
- Default hashes: **SHA3-256** (digests), **SHA3-512** (roots/alg-policy).
- Optional fast path: **BLAKE3** (never consensus-critical unless permitted in `domains.yaml`).
- **Domain separation** is mandatory. See `domains.yaml` for IDs like:
  - `sign/tx`, `sign/header`, `nullifier/hashshare`, `policy/alg-root`, `da/blob`, etc.

### Addressing & PQ
- Addresses = `bech32m("anim", alg_id || sha3_256(pubkey))`.
- Sig algs: **Dilithium3**, **SPHINCS+ SHAKE-128s**. KEM: **Kyber-768** (P2P handshake).
- An **alg-policy Merkle root** is committed in the header; wallets and nodes must enforce it.

---

## PoIES (Proof-of-Integrated & Effective-Shares) summary

Consensus acceptance:
\[
S \;=\; -\ln(u) \;+\; \sum_{p \in \text{included proofs}} \psi(p) \;\;\ge\;\; \Theta
\]
- `u` is drawn from the header nonce domain (uniform on (0,1)).
- Each proof contributes **ψ(p)** per the current `poies_policy.yaml` (caps and diversity/escort rules apply).
- Difficulty retarget updates **Θ** fractionally (EMA) to hit the target inter-block time.
- **Fairness tuner α** slowly reweights proof types to avoid single-type capture.

Formal notes live in `poies_math.md` and optional Lean lemmas in `formal/poies_equations.lean`.

---

## Reproducibility & versioning

- Every normative file is part of the **spec version** (semver): bump **minor** for additive schema fields; **major** for breaking changes.
- The node commits **hashes** of:
  - `params.yaml`, `poies_policy.yaml`, `pq_policy.yaml`, and the **alg-policy Merkle root**.
- Test vectors pin inputs/outputs. Implementations MUST pass vectors before release.

---

## Validating schemas & vectors (Ubuntu 22.04)

You can validate everything locally with standard tools. We’ll also provide `make` targets (`make spec-validate`) later, but here are direct commands.

### 1) JSON-Schema (ABI, manifest, alg-policy)
Option A — Python (`check-jsonschema`):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install check-jsonschema==0.28.6

check-jsonschema --schema spec/abi.schema.json spec/test_vectors/vm_programs.json
check-jsonschema --schema spec/manifest.schema.json spec/test_vectors/vm_programs.json
check-jsonschema --schema spec/alg_policy.schema.json pq/alg_policy/example_policy.json

Option B — Node (ajv-cli):

npm -g install ajv-cli@5
ajv -s spec/abi.schema.json -d spec/test_vectors/vm_programs.json
ajv -s spec/manifest.schema.json -d spec/test_vectors/vm_programs.json
ajv -s spec/alg_policy.schema.json -d pq/alg_policy/example_policy.json

2) CDDL (CBOR schemas)

Use the Rust cddl tool (fast & strict):

cargo install cddl --locked
# Validate that examples conform to the CDDL:
cddl compile spec/tx_format.cddl
cddl compile spec/header_format.cddl
cddl compile spec/blob_format.cddl

(Example CBOR instances for validation come with the vectors and can be round-tripped via the node’s encoder tests.)

3) OpenRPC

npx --yes @open-rpc/validator@1.22.1 -s spec/openrpc.json

4) Policy roots (PQ)

Build and print the alg-policy root from a JSON policy:

python3 pq/alg_policy/build_root.py pq/alg_policy/example_policy.json --hash sha3-512
# The printed root must match the one referenced in headers.

5) Test vectors (round-trip & acceptance)

Once the repos are laid out, run:

pytest -q spec/test_vectors  # node & libs consume vectors and assert equality


⸻

Backward compatibility & deprecation
	•	Adding new optional fields in CDDL/JSON-Schema is OK; removing or retyping is breaking.
	•	Deprecations require:
	1.	Marking the field/variant as deprecated in the schema comments.
	2.	A grace period across two minor spec versions.
	3.	Consensus signaling (header feature bits) before removal.

⸻

Deterministic “SignBytes” rules

When signing or hashing consensus objects:
	•	Serialize with deterministic CBOR.
	•	Prepend the domain tag from domains.yaml.
	•	Append the chainId (from chains.json) when applicable.
	•	Example: sign/tx || chainId || CBOR(tx_without_sigs).

⸻

Gas tables & VM

opcodes_vm_py.yaml defines the opcode whitelist and gas costs for the deterministic Python VM. The VM compiler/runtime and all SDK codegen consume this file to ensure identical costs across environments (node, WASM simulator, Studio).

⸻

Chain IDs (CAIP-2 style)

chains.json tracks human-friendly IDs:
	•	animica:1 — mainnet
	•	animica:2 — testnet
	•	animica:1337 — devnet (local)

Tooling and the wallet extension read this file to resolve defaults and to guard against chainId mismatches.

⸻

Linting & formatting
	•	YAML: yamllint with project config (coming in repo root).
	•	JSON: jq -S . for canonical pretty-print (spec files are stored minified unless readability is needed).
	•	Markdown: markdownlint (CI gate).
	•	Formal: Lean/K build steps are optional (documented in formal/README.md).

⸻

Directory map (quick)

spec/
  params.yaml
  poies_policy.yaml
  pq_policy.yaml
  chains.json
  domains.yaml
  tx_format.cddl
  header_format.cddl
  blob_format.cddl
  abi.schema.json
  manifest.schema.json
  openrpc.json
  opcodes_vm_py.yaml
  alg_policy.schema.json
  poies_math.md
  test_vectors/
    txs.json
    headers.json
    proofs.json
    vm_programs.json
    README.md
  formal/
    poies_equations.lean
    vm_smallstep.k
    README.md


⸻

Contribution workflow
	1.	Propose edits via PR targeting this directory.
	2.	Update relevant test vectors.
	3.	Bump spec version (and note migration plans).
	4.	Get a green run on schema validators + vectors.
	5.	Merge; downstream codegen/SDKs pick up the change.

⸻

FAQ
	•	Why CBOR? Compact, deterministic, binary-safe, and well-suited for Merkle hashing and light-client proofs.
	•	Why multiple PQ algs? Diversity: Dilithium3 (lattice, fast), SPHINCS+ (stateless hash-based, conservative). Policy controls which are valid at any time.
	•	Can I add new proof types? Yes, via poies_policy.yaml and the proofs registry—must supply a ψ mapping and caps.

