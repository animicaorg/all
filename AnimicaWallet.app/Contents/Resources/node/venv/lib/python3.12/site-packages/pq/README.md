# `pq/` — Post-Quantum Primitives for Animica

This module provides **production-grade, post-quantum cryptography (PQC)** building blocks used across the Animica stack:

- **Signatures:** CRYSTALS-**Dilithium3** and **SPHINCS+ SHAKE-128s**
- **KEM:** CRYSTALS-**Kyber-768**
- **Handshake:** Kyber-768 KEM + **HKDF-SHA3-256** → AEAD keys (for P2P)
- **Addresses:** `anim1…` **bech32m** addresses: `payload = alg_id || sha3_256(pubkey)`
- **Alg-policy root:** Merkle root over enabled PQ algs per `spec/alg_policy.schema.json`

> Everything here is **PQ-first**. Classical curves are not part of the consensus surface.

---

## What’s in this package

pq/
├─ README.md        ← this file
├─ alg_ids.yaml     ← canonical IDs for Dilithium3 / SPHINCS+ / Kyber-768
├─ POLICY.md        ← network-level PQ policy guidance
├─ py/…             ← Python library (registry, sign/verify, KEM, handshake)
├─ cli/…            ← Command-line tools (keygen/sign/verify/handshake/policy-root)
└─ test_vectors/…   ← Known-answer tests (signatures, KEM, addresses, handshake)

Key Python modules:
- `py/registry.py` – map `alg_id ↔ name`, sizes, feature flags
- `py/sign.py`, `py/verify.py` – uniform signature API (domain-separated)
- `py/kem.py` – Kyber encapsulation/decapsulation
- `py/handshake.py` – Kyber+HKDF handshake transcript generator
- `py/utils/{hash,bech32,hkdf,rng}.py` – SHA3/Blake3, bech32m, HKDF-SHA3-256, RNG wrappers
- `py/algs/{dilithium3,sphincs_shake_128s,kyber768}.py` – algorithm bridges
- `py/algs/oqs_backend.py` – **optional** liboqs FFI (fast path)
- `py/algs/pure_python_fallbacks.py` – slow educational fallbacks (non-production)

---

## Dependencies

### Required (Python)
- Python **3.10+**
- `msgspec` (CBOR/JSON), `pysha3` (or `hashlib` w/ SHA3), `blake3` (optional)
- `bech32`, `click`/`typer` for CLI, `pydantic` for strict models
- `pytest` for tests

Install (module local):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
# If the repo has a root requirements, otherwise install essentials:
pip install msgspec pysha3 bech32 hashlib blake3 typer[all] pydantic pytest

Optional (fast path)
	•	liboqs (https://openquantumsafe.org) + Python FFI
	•	Linux: install from packages or build from source
	•	If present, pq/py/algs/oqs_backend.py will auto-load and prefer high-performance kernels.

Verify detection:

python -c "from pq.py.algs.oqs_backend import have_oqs; print('liboqs=', have_oqs())"

If liboqs is not available, the module uses slower reference shims or raises NotImplementedError for disabled algorithms. Do not use fallbacks in production.

⸻

Quick demos (CLI)

Assume you’re in the repo root with the venv activated.

1) Generate a key

python -m pq.cli.pq_keygen --alg dilithium3 --out sk.bin --pub pk.bin
python -m pq.cli.pq_keygen --alg sphincs_shake_128s --out sk2.bin --pub pk2.bin

2) Sign & verify

echo -n "hello animica" > msg.bin

# Dilithium3
python -m pq.cli.pq_sign --alg dilithium3 --sk sk.bin --in msg.bin --out sig.bin
python -m pq.cli.pq_verify --alg dilithium3 --pk pk.bin --in msg.bin --sig sig.bin

# SPHINCS+
python -m pq.cli.pq_sign --alg sphincs_shake_128s --sk sk2.bin --in msg.bin --out sig2.bin
python -m pq.cli.pq_verify --alg sphincs_shake_128s --pk pk2.bin --in msg.bin --sig sig2.bin

All signatures are domain-separated. To sign for a specific domain:

python -m pq.cli.pq_sign --alg dilithium3 --sk sk.bin --in msg.bin \
  --domain "animica:tx:signbytes:v1"

3) Derive an address (bech32m)

python - <<'PY'
from pq.py.address import pubkey_to_address
with open('pk.bin','rb') as f:
    pk=f.read()
print(pubkey_to_address(alg_name='dilithium3', pubkey=pk))
PY
# -> anim1qq… (bech32m)

4) Handshake demo (Kyber + HKDF)

python -m pq.cli.pq_handshake_demo
# prints both parties’ transcript hashes and derived AEAD keys (should match)

5) Build alg-policy Merkle root

python -m pq.cli.pq_alg_policy_root pq/alg_policy/example_policy.json
# => root: 0x… (matches spec/alg_policy.schema.json hashing rules)


⸻

Python API (minimal)

from pq.py import registry
from pq.py.sign import sign
from pq.py.verify import verify
from pq.py.kem import encaps, decaps
from pq.py.handshake import handshake

# pick an algorithm
alg = registry.get_by_name("dilithium3")

# sign/verify
sig = sign(alg, sk_bytes, b"payload", domain=b"animica:tx:v1")
ok  = verify(alg, pk_bytes, b"payload", sig, domain=b"animica:tx:v1")

# KEM (Kyber768)
ct, ss_sender  = encaps(pk_kem)
ss_receiver    = decaps(sk_kem, ct)

# P2P handshake (Kyber + HKDF-SHA3-256)
hs = handshake(initiator_static_sig_key, responder_static_sig_key)
# hs.aead_key_{tx,rx}, hs.transcript_hash, hs.alg_policy_root …

All byte interfaces are exact length-checked. Errors raise typed exceptions (PqError, VerifyError, …).

⸻

Domains & policies
	•	Domain separators live in spec/domains.yaml and are enforced by sign/verify APIs.
	•	Chain PQ algorithm-policy:
	•	pq/alg_policy/example_policy.json → Merkle root (via CLI)
	•	The root is advertised in P2P HELLO and baked into genesis.

⸻

Running tests & vectors

pytest -q pq/tests
# Or run vector checks only:
pytest -q pq/tests/test_sign_verify.py pq/tests/test_kem_handshake.py

You may also round-trip the official vectors in pq/test_vectors/*.json.
Set PQ_TEST_ALLOW_SLOW=1 to include SPHINCS+ fallback timing-heavy tests.

⸻

Performance notes
	•	With liboqs: Dilithium3 and Kyber768 are fast enough for online flows; SPHINCS+ remains larger/slower (use strategically).
	•	Without liboqs: only use for development or CI smoke; production nodes should run with liboqs enabled.

⸻

Security notes
	•	Keys are zeroized in best-effort fashion on drop in the Python process (do not rely solely on this).
	•	Signatures are domain-separated; never reuse a key across domains.
	•	The KEM handshake derives independent send/recv keys with an explicit transcript hash to bind meta (chainId, policies).
	•	This package intentionally avoids leaking timing via Python-level branches on secret values; constant-time guarantees ultimately depend on the underlying library (prefer liboqs).

⸻

Troubleshooting
	•	ImportError: oqs — liboqs not found. Either install it, or set PQ_DISABLE_OQS=1 to force fallbacks.
	•	VerifyError: domain mismatch — ensure the domain tag matches what the signer used (see spec/domains.yaml).
	•	Address decode failed — make sure you’re using bech32m (not bech32) and the chain HRP matches network.

⸻

License & third-party notices

See pq/LICENSE-THIRD-PARTY.md for liboqs & upstream license details.

⸻

What’s next
	•	Add Dilithium2/5 and Kyber1024 as optional algs when policy allows.
	•	Hybrid sigs (PQC + classical) can be modeled in the policy tree, but Animica consensus remains PQ-native.

Happy hacking 🔐
