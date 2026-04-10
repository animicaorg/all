# PQ Test Vectors

This folder contains *reproducible* vectors for Animica’s post-quantum layer:
signature (Dilithium3, SPHINCS+-SHAKE-128s), KEM (Kyber-768), the Kyber-HKDF
P2P handshake transcript, address encoding, and the alg-policy Merkle root.

Vectors are consumed by:
- `pq/tests/` (pytest) — authoritative checks
- CLI demos in `pq/cli/` — quick manual verification
- SDK tests (optional cross-check)

All vectors use **canonical lowercase hex with `0x` prefix** unless stated
otherwise. Binary blobs (e.g., ciphertexts) are hex as well.

---

## What’s here

- `dilithium3.json` — sign/verify
- `sphincs_shake_128s.json` — sign/verify
- `kyber768.json` — encaps/decaps (shared secret equality)
- `handshake.json` — end-to-end P2P handshake (Kyber768 + HKDF-SHA3-256)
- `addresses.json` — bech32m round-trips for `anim1…` addresses
- `alg_policy_root` is generated from `pq/alg_policy/example_policy.json`
  (see below)

---

## Quick start (one-liners)

**Run all PQ tests (preferred):**
```bash
pytest -q pq/tests

Verify all vectors with the CLI tools:

# Dilithium3 (reads vector file internally)
python3 -m pq.cli.pq_verify --vectors pq/test_vectors/dilithium3.json

# SPHINCS+ (SHAKE-128s)
python3 -m pq.cli.pq_verify --vectors pq/test_vectors/sphincs_shake_128s.json

# Kyber encaps/decaps
python3 -m pq.cli.pq_handshake_demo --vectors pq/test_vectors/kyber768.json

# P2P handshake (Alice/Bob seeds => identical derived keys both sides)
python3 -m pq.cli.pq_handshake_demo --vectors pq/test_vectors/handshake.json

# Address round-trips
python3 - <<'PY'
from pq.py.utils import bech32
import json, sys
with open("pq/test_vectors/addresses.json") as f:
    vec = json.load(f)
for c in vec["cases"]:
    hrp, data = c["hrp"], bytes.fromhex(c["payload"][2:])
    s = bech32.bech32m_encode(hrp, data)
    assert s == c["bech32m"], (c["name"], s, c["bech32m"])
    hrp2, data2 = bech32.bech32m_decode(s)
    assert hrp2 == hrp and data2 == data
print("address vectors OK")
PY

Recompute the alg-policy Merkle root (must match test fixtures):

python3 pq/alg_policy/build_root.py pq/alg_policy/example_policy.json --dump-leaves


⸻

Vector formats

All JSON is UTF-8, canonical spacing not required, keys are case-sensitive.
Numbers that represent sizes/lengths are decimal; byte strings are hex with 0x.

1) Signature vectors (dilithium3.json, sphincs_shake_128s.json)

{
  "algorithm": "dilithium3",
  "cases": [
    {
      "name": "d3_basic_1",
      "seed": "0x8f...32",          // 32 bytes seed for deterministic keygen (test mode)
      "msg":  "0x48656c6c6f",       // "Hello"
      "pk":   "0x...",              // public key
      "sk":   "0x...",              // OPTIONAL: present only if the upstream ref provides it
      "sig":  "0x...",              // expected signature (domain: Animica:TxSign)
      "valid": true
    },
    {
      "name": "d3_bad_sig",
      "seed": "0x....",
      "msg":  "0x00",
      "pk":   "0x....",
      "sig":  "0xdeadbeef",         // deliberately wrong
      "valid": false
    }
  ]
}

	•	Tests accept either {seed} (preferred) or {pk, sk} (when deterministic keygen is
unavailable for an upstream artifact). If both present, {seed} is used and keys are
cross-checked.
	•	The signing domain must match the spec’s domain separator for the tested vector
(e.g., "Animica:TxSign:v1"); test harness enforces it.

2) KEM vectors (kyber768.json)

{
  "algorithm": "kyber768",
  "cases": [
    {
      "name": "k768_enc_1",
      "seed": "0xa1...5c",          // keypair seed (deterministic in test mode)
      "ek_seed": "0x77...aa",       // encapsulation randomness
      "pk": "0x...",                // expected public key
      "ct": "0x...",                // expected ciphertext
      "ss": "0x...",                // expected shared secret (encaps and decaps)
      "valid": true
    }
  ]
}

3) Handshake vectors (handshake.json)

{
  "protocol": "kyber768+hkdf-sha3-256",
  "cases": [
    {
      "name": "hs_1",
      "alice_seed": "0x...",        // deterministic seeds for each role
      "bob_seed":   "0x...",
      "alice_ek_seed": "0x...",     // encaps rand (if role encapsulates first)
      "context": "Animica:P2P:handshake:v1",
      "a_info": "0x",               // optional HKDF info bytes (hex)
      "b_info": "0x",
      "derived": {
        "a_tx_key": "0x...",        // Alice's TX key
        "a_rx_key": "0x...",        // Alice's RX key
        "b_tx_key": "0x...",        // Bob's TX key
        "b_rx_key": "0x..."         // Bob's RX key  (a_rx == b_tx, a_tx == b_rx)
      }
    }
  ]
}

The test ensures both participants derive identical opposing keys and that the
transcript hash is stable.

4) Address vectors (addresses.json)

{
  "hrp": "anim",
  "cases": [
    {
      "name": "addr_d3_1",
      "payload": "0x0101...ee",     // alg_id || sha3_256(pubkey)
      "bech32m": "anim1qz...xyz"
    }
  ]
}


⸻

Determinism & RNG seeds
	•	All vectors use a test RNG pathway (see pq/py/utils/rng.py) which is enabled
automatically by the tests when a seed/ek_seed is present.
	•	Production code never uses deterministic RNG; seeds exist only to make vectors
reproducible across machines/RNG backends.
	•	If a backend cannot produce bit-identical outputs (e.g., a fallback impl), the tests
will skip exact-match assertions and verify semantic properties (e.g., verify(sig)).

⸻

Backend selection (liboqs vs pure-python stubs)
	•	The test harness will prefer the liboqs backend when available.
	•	You can force a backend with an env var:
	•	ANIMICA_PQ_BACKEND=oqs — require liboqs (fail if missing)
	•	ANIMICA_PQ_BACKEND=stub — use slow educational fallbacks where available
	•	Backends advertise capabilities via pq.py.algs.oqs_backend.is_available().

⸻

Running specific checks

# Only Dilithium3 vector tests
pytest -q pq/tests/test_sign_verify.py -k dilithium3

# Handshake only
pytest -q pq/tests/test_kem_handshake.py

# Address codec only
pytest -q pq/tests/test_registry.py -k address


⸻

Updating / adding vectors
	1.	Add/modify a JSON file here following the schemas above.
	2.	Keep cases small and targeted (few dozen entries per file).
	3.	Use deterministic seeds rather than embedding long-term secret keys.
	4.	Recompute the alg-policy root if policy changed:

python3 pq/alg_policy/build_root.py pq/alg_policy/example_policy.json > /tmp/root.txt


	5.	Run pytest -q pq/tests.

Lint/CI: the repo’s CI will ensure:
	•	hex strings are lowercase with 0x prefix;
	•	all msg/pk/sk/sig/ct/ss lengths are valid for the algorithm;
	•	handshake derived keys satisfy a_rx == b_tx and a_tx == b_rx.

⸻

Troubleshooting
	•	“Backend not available”: install liboqs or set ANIMICA_PQ_BACKEND=stub for
non-cryptographic regression checks.
	•	Length mismatch errors: re-check hex lengths vs algorithm constants in
pq/py/registry.py.
	•	Handshake mismatch: verify the HKDF context/info bytes and seeds exactly
match the vector.

Happy verifying! 🧪🔐
