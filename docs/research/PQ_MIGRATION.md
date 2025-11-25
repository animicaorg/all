# Post-Quantum Migration — Long-Term Plan & Data Formats

This document lays out Animica’s **algorithm-agile** plan for post-quantum (PQ) cryptography across addresses, transactions, P2P, and tooling. It also specifies **stable data formats** so nodes, wallets, SDKs, and explorers can interoperate safely during rotations, deprecations, and emergency cutovers.

> TL;DR  
> - **Schemes**: Signatures = *Dilithium3*, *SPHINCS+ (SHAKE-128s)*; KEM = *Kyber-768*.  
> - **Address model**: `address_payload = alg_id || sha3_256(pubkey)` → **bech32m** `anim1…`.  
> - **Policy**: a **Merkle-rooted alg-policy** object (hash: SHA3-512) pinned in chain params & headers.  
> - **Rotation**: staged rollouts with allow/deny, weights, and deprecation windows.  
> - **Formats**: canonical JSON (for UX/tooling) and CBOR (on-wire), with explicit `alg_id`.

See also:
- `spec/pq_policy.yaml` — knobs & layout for the **alg-policy tree**
- `pq/alg_ids.yaml` — canonical IDs
- `pq/py/address.py`, `pq/py/sign.py`, `pq/py/verify.py` — reference codecs/APIs
- `spec/tx_format.cddl`, `spec/header_format.cddl` — CBOR schemas
- `pq/alg_policy/*.json` and `pq/test_vectors/*` — test vectors

---

## 1) Goals & Principles

1. **Algorithm agility**: switch, add, or deprecate PQ primitives without breaking addresses or history.
2. **Defense in depth**: two signature families available (lattice-based + stateless hash-based).
3. **Deterministic policy**: network-chosen algorithms captured in a **policy Merkle root** that nodes verify.
4. **Backwards compatibility**: old signatures remain valid **for historical data**; new tx rules apply forward.
5. **Operational guardrails**: staged activates, telemetry, and explicit kill-switches (policy flips) for emergency response.

---

## 2) Roadmap Phases

| Phase | Name | What changes | Notes |
|---|---|---|---|
| 0 | PQ-Ready | Address & tx formats include `alg_id`. P2P supports Kyber handshake. | Default policy enables Dilithium3; SPHINCS+ optional. |
| 1 | Hybrid Comfort | Wallets support **either** Dilithium3 **or** SPHINCS+; node verifies both per policy. | Encourage dual-provisioning for high-value accounts. |
| 2 | PQ-Only | (If any non-PQ interop existed) disable legacy entirely. | Not applicable if chain launched PQ-first. |
| 3 | Rotation | Add new scheme(s) or bump security levels. | Policy carries **enabled + deprecated_at_height**. |
| E | Emergency | Fast deactivation of a scheme due to break. | Mempool rejects immediately; chain header pins new policy root. |

Activation toggles are encoded in the **alg-policy**; consensus & mempool enforce the current root.

---

## 3) Canonical Identifiers

IDs are short, ASCII, **lowercase/kebab**:

- `dilithium3`
- `sphincs-shake-128s`
- `kyber768`

The mapping lives in `pq/alg_ids.yaml` / `pq/py/registry.py` and is mirrored in RPC/SDKs. Exact key/signature sizes are provided by the registry (avoid hardcoding elsewhere).

---

## 4) Address Format (PQ Signers)

**Payload**  

address_payload = alg_id (varuint) || sha3_256(pubkey_bytes)

**Human string**  
- **bech32m** with HRP `anim` → `anim1…`
- Checksum: bech32m per BIP-350.
- Canonical casing: **lowercase**.

**Notes**
- `alg_id` ensures that **two different schemes** with identical public keys (bytewise) do **not** collide.
- Wallets may present a **label** “Dilithium3” / “SPHINCS+” next to the address.

---

## 5) Signature Envelope (Tx/Receipt Domains)

On-wire CBOR (see `spec/tx_format.cddl`), conceptually:

```jsonc
{
  "sig_alg": "dilithium3",           // alg_id
  "pubkey": "0x…",                   // required unless account pre-registered key hash
  "signature": "0x…",                // scheme-specific bytes
  "domain": "tx/v1",                 // domain separation tag
  "signBytesHash": "0x…"             // SHA3-256 of canonical SignBytes
}

	•	Verification: verify(sig_alg, pubkey, signBytes) == true and sha3_256(pubkey) matches the address payload.
	•	Domain tags: versioned strings (see core/encoding/canonical.py); prevents cross-protocol replay.

⸻

6) P2P Handshake (KEM + HKDF)

Nodes use Kyber-768 for ECDH-like key establishment, then HKDF-SHA3-256 to derive AEAD keys.

Transcript summary (conceptual JSON):

{
  "proto": "animica/p2p/1",
  "kem": "kyber768",
  "client_hello": { "kem_pub": "0x…" },
  "server_hello": { "kem_ct": "0x…", "kem_ss_tag": "0x…" },
  "hkdf": { "alg": "sha3-256", "salt": "0x…", "info": "handshake-v1" },
  "aead": "chacha20poly1305",
  "transcript_hash": "0x…"
}

	•	The peer identity signature (Dilithium3/SPHINCS+) signs the transcript hash for authentication.
	•	Rotation to future KEMs is a policy update; multi-suite negotiation is permitted.

⸻

7) Alg-Policy Object (Merkle-Rooted)

A canonical JSON object lists permitted algorithms and statuses. Its Merkle root (SHA3-512) is pinned in:
	•	spec/params.yaml → genesis
	•	Block headers (policy root field) for live networks

Example policy JSON (simplified)

{
  "version": 1,
  "suites": {
    "sign": [
      { "id": "dilithium3", "status": "enabled", "weight": 1.0 },
      { "id": "sphincs-shake-128s", "status": "enabled", "weight": 0.25, "notes": "fallback/stateless" }
    ],
    "kem": [
      { "id": "kyber768", "status": "enabled" }
    ]
  },
  "deprecations": [
    { "id": "dilithium2", "effective_height": 1, "reason": "not used / not enabled on this chain" }
  ],
  "meta": { "updated_by": "gov/prop-12", "timestamp": "2025-02-10T12:00:00Z" }
}

Hashing & Canon
	•	Key order: sorted lexicographically; arrays sorted by id.
	•	Numbers: integers unless a real number is required (e.g., weight).
	•	Whitespace: none in hashing form; UTF-8.
	•	Hash: SHA3-512(policy_canon_json).
	•	Merkle: if split across leaves (large policies), leaf hashing uses alg_policy.schema.json.

Tools:
	•	pq/alg_policy/build_root.py — computes the canonical root
	•	zk/registry/update_vk.py — uses similar canonicalization for VK trees

⸻

8) Wallet Migration Strategy
	1.	Seed: PBKDF2/HKDF-SHA3 with per-algorithm derivation paths, e.g.:

m / pq / dilithium3 / account / index
m / pq / sphincs-shake-128s / account / index


	2.	Key storage: tagged by alg_id. Export includes explicit algorithm labels.
	3.	Rotation: when a scheme is deprecated, new addresses default to the replacement; old addresses remain displayable and verifiable.
	4.	Backup: recommend dual-scheme export for high-value users.
	5.	Watch-only: addresses remain valid even after deprecation; wallet disables sign but allows receive and watch.

⸻

9) Node/Mempool Enforcement
	•	Admission: reject txs whose sig_alg is not enabled by the current policy root.
	•	Headers: blocks include the policy root; validators check it matches the locally configured policy.
	•	Grace windows: poies_policy.yaml/params.yaml may define activation height + deprecation height to allow client upgrades.
	•	RPC: chain.getParams returns active alg_policy_root and parsed suites.

⸻

10) Data Formats (Stable)

10.1 Canonical JSON (UX/Tooling)
	•	Encoding: UTF-8, sorted keys, no insignificant whitespace.
	•	Hex: 0x prefixed lowercase.
	•	IDs: alg_id is always explicit.

10.2 CBOR (On-Wire)
	•	spec/tx_format.cddl and spec/header_format.cddl include:
	•	sig_alg: tstr (e.g., "dilithium3")
	•	sig: bstr
	•	pubkey: bstr (optional depending on account model)
	•	Determinism: canonical CBOR (sorted map keys, definite lengths).

⸻

11) Governance & Upgrades
	•	Proposal includes: motivation, new algorithms, security notes, performance, wallet readiness, SDK & test vectors.
	•	Staging:
	1.	Shadow (wallets can opt-in; nodes accept but discourage)
	2.	Default (new accounts default to new alg)
	3.	Required (mempool enforces)
	•	Emergency: a compact policy patch flips status: "disabled" for an alg. The new policy root becomes effective at a designated height.

⸻

12) Interop & APIs
	•	RPC:
	•	chain.getParams → includes alg_policy_root & parsed suites
	•	tx.sendRawTransaction → validates sig_alg
	•	SDKs: Signers expose alg_id and serialize envelopes accordingly.
	•	Explorer: Decodes alg_id for addresses/txs; warns on deprecated algs.

⸻

13) Testing & Vectors
	•	Registry tests: pq/tests/test_registry.py sanity-checks IDs/sizes.
	•	Sign/Verify: pq/tests/test_sign_verify.py covers pass/fail.
	•	Policy: pq/tests/test_alg_policy_root.py ensures reproducible roots.
	•	End-to-end: mempool admission with mismatched/disabled alg_id is rejected.

⸻

14) Migration Matrix (Examples)

Context	Before	After	Action
New wallet create	Dilithium3 default	NewAlgX default	Change UI default, still allow Dilithium3 import
Tx signing	Dilithium3	Dilithium3 or NewAlgX	Node accepts both while status=enabled
Mempool	Dilithium3	NewAlgX only	Flip policy at activation height
P2P KEM	Kyber-768	Kyber-1024 (future)	Negotiate both until cutover, governed policy flips


⸻

15) Security Notes
	•	Stateless fallback: SPHINCS+ remains available to mitigate potential lattice surprises.
	•	Key-prefix isolation: alg_id in address payload prevents cross-scheme ambiguities.
	•	Replay: domain tags + chain id in SignBytes prevent cross-context replays.
	•	Supply chain: pin library versions; record sizes from the registry, not constants scattered in code.

⸻

16) Worked Examples

16.1 Address (Dilithium3)

{
  "alg_id": "dilithium3",
  "pubkey": "0x…",
  "payload": "0x0103…",          // varuint(alg_id) || sha3_256(pubkey)
  "address": "anim1qxy…"
}

16.2 Tx Signature (SPHINCS+)

{
  "sig_alg": "sphincs-shake-128s",
  "pubkey": "0x…",
  "signature": "0x…",
  "domain": "tx/v1"
}

16.3 Policy Root

{
  "root": "0xabc…",  // SHA3-512(canon policy)
  "policy": { "version": 1, "suites": { "sign": [ … ], "kem": [ … ] } }
}


⸻

17) Backfill & Archival
	•	Historical blocks remain as is; verifiers keep codecs for any past alg_id present on chain.
	•	API responses include sig_alg so indexers can partition by scheme.
	•	Wallets may mark deprecated addresses as “receive/watch only”.

⸻

18) Emergency Playbook (Condensed)
	1.	Raise Security Advisory with minimal details, push policy with status: "disabled" for the affected alg_id.
	2.	Bump alg_policy_root and schedule activation H+Δ.
	3.	Mempool: immediately reject the disabled alg_id once activated.
	4.	Broadcast wallet hotfix to default to safe algorithm.

⸻

19) References & Pointers
	•	NIST PQC finalists (Dilithium, Kyber, SPHINCS+)
	•	Bech32m (BIP-350)
	•	SHA-3 / SHAKE
	•	Animica spec files: spec/params.yaml, spec/pq_policy.yaml, spec/alg_policy.schema.json, spec/tx_format.cddl

⸻

Summary.
Animica achieves durable PQ posture through explicit algorithm IDs, policy-root pinning, and stable data formats. With staged rollouts, dual-family signatures, and emergency levers, we can rotate or retire algorithms without breaking history or UX, while keeping on-wire and UX encodings deterministic and verifiable.

