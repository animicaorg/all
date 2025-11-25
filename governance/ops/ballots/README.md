# Ballots — How to Submit & Sign Votes
_This guide shows voters/delegates how to create, sign, verify, and submit ballots for Animica governance._

**Applies to:** On-chain votes referenced by proposal IDs (e.g., `GOV-2025-11-VM-OPC-01`).  
**Artifacts used here:** `governance/scripts/generate_ballot.py`, `governance/scripts/tally_votes.py`, schemas in `governance/schemas/`.

---

## 0) Prerequisites

- A supported signing key and address:
  - **ed25519** or **secp256k1** (classic)  
  - **dilithium3** (PQ) — if enabled by policy
- Your **voter address** (bech32m `am1…`) and **public key**.
- The **proposal ID** you are voting on.
- Snapshot parameters (chain ID + height or timestamp) supplied in the vote announcement.

Optional tools (recommended):
- Python 3.10+ with `pip install jsonschema pyyaml` (repo already uses these).
- A wallet/CLI capable of signing **arbitrary bytes** (message hash) with the above keys.

> Ballot shape is validated by `governance/schemas/ballot.schema.json`. CI and tally tooling assume that schema.

---

## 1) Generate a Ballot Template

Use the helper to produce a minimal, schema-compliant JSON you will sign.

```bash
python governance/scripts/generate_ballot.py \
  --proposal-id GOV-2025-11-VM-OPC-01 \
  --voter am1youraddresshere... \
  --vote yes \
  --weight 1.0 \
  --chain-id 2 \
  --snapshot-type height \
  --snapshot-value 1234567 \
  --out my_ballot.json --pretty
Fields (summary):

proposalId — exact string from the proposal header

voter — your bech32m address

vote — yes | no | abstain

weight — voting power you intend to cast (≤ your eligible power)

chainId, snapshot — the voting snapshot (height or ISO-timestamp)

signature — empty for now; you will fill it in §3

2) Canonicalize & Hash Before Signing
Signatures must be over a canonical JSON (stable key order, no extra spaces).

bash
Copy code
python - <<'PY'
import json, sys, hashlib
src = "my_ballot.json"
dst = "my_ballot.canonical.json"
with open(src, "r", encoding="utf-8") as f:
    obj = json.load(f)
# Drop any prior signature block before signing:
obj.pop("signature", None)
payload = json.dumps(obj, sort_keys=True, separators=(",",":")).encode("utf-8")
open(dst, "wb").write(payload)
open("my_ballot.sha256", "w").write(hashlib.sha256(payload).hexdigest()+"\n")
print("Wrote", dst, "and my_ballot.sha256")
PY
Files produced:

my_ballot.canonical.json — bytes you will sign

my_ballot.sha256 — convenience hash (hex)

3) Sign the Canonical Payload
Choose one method that matches your key type. The signature object you’ll attach later looks like:

json
Copy code
"signature": {
  "algorithm": "ed25519",          // or "secp256k1" or "dilithium3"
  "publicKey": "<base64 or hex>",  // encoding noted below
  "sig": "<base64 or hex>",        // signature over canonical bytes
  "encoding": "base64",            // "base64" or "hex"
  "payloadHash": "sha256:<hex>"    // matches my_ballot.sha256
}
A) ed25519 (OpenSSL 3.x)
Generate a key once (PEM), then sign:

bash
Copy code
# (one-time) create ed25519 keypair
openssl genpkey -algorithm ED25519 -out ed25519_sk.pem
openssl pkey -in ed25519_sk.pem -pubout -out ed25519_pk.pem

# sign canonical JSON bytes
openssl pkeyutl -sign -inkey ed25519_sk.pem -rawin \
  -in my_ballot.canonical.json -out my_ballot.sig

# export public key raw (SubjectPublicKeyInfo → raw)
openssl pkey -in ed25519_pk.pem -pubout -outform DER | tail -c 32 > ed25519_pk.raw
Base64-encode for JSON:

bash
Copy code
SIG_B64=$(base64 -b 0 < my_ballot.sig)
PK_B64=$(base64 -b 0 < ed25519_pk.raw)
B) secp256k1 (OpenSSL + ECDSA)
bash
Copy code
# one-time key
openssl ecparam -name secp256k1 -genkey -noout -out secp256k1_sk.pem
openssl ec -in secp256k1_sk.pem -pubout -out secp256k1_pk.pem

# sign SHA-256 digest over canonical bytes
shasum -a 256 my_ballot.canonical.json | awk '{print $1}' > digest.hex
# DER ECDSA sign requires digest bytes:
xxd -r -p digest.hex > digest.bin
openssl pkeyutl -sign -inkey secp256k1_sk.pem -in digest.bin -out my_ballot.sig

# export uncompressed pubkey (DER→raw is tool-specific; simplest: keep DER base64)
PK_B64=$(base64 -b 0 < secp256k1_pk.pem)
SIG_B64=$(base64 -b 0 < my_ballot.sig)
For ECDSA we sign the digest. The payloadHash must match sha256:<hex> you computed.

C) dilithium3 (PQ)
Use your PQ-enabled wallet/CLI. If using a dev tool that outputs raw signature & pubkey:

ini
Copy code
SIG_B64=<tool output>
PK_B64=<tool output>
algorithm="dilithium3"
encoding="base64"
4) Attach the Signature Block
Append the signature to the original ballot JSON:

bash
Copy code
python - <<'PY'
import json, base64, sys, hashlib
ballot = json.load(open("my_ballot.json","r",encoding="utf-8"))
canon = json.dumps({k:v for k,v in ballot.items() if k!="signature"}, sort_keys=True, separators=(",",":")).encode()
h = "sha256:"+hashlib.sha256(canon).hexdigest()
# Fill these from step 3:
algorithm = "ed25519"         # or "secp256k1" | "dilithium3"
encoding  = "base64"          # or "hex"
publicKey = open("ed25519_pk.raw","rb").read()
sig       = open("my_ballot.sig","rb").read()
ballot["signature"] = {
  "algorithm": algorithm,
  "publicKey": base64.b64encode(publicKey).decode(),
  "sig": base64.b64encode(sig).decode(),
  "encoding": encoding,
  "payloadHash": h
}
json.dump(ballot, open("my_ballot.signed.json","w",encoding="utf-8"), indent=2)
print("Wrote my_ballot.signed.json")
PY
Quick validation:

bash
Copy code
python governance/scripts/validate_proposal.py my_ballot.signed.json --schemas-dir governance/schemas --strict --pretty
(Validator prints a report; exit code 0 means OK.)

5) Submit Your Ballot
Preferred: On-chain transaction via wallet UI/CLI when the vote is open.

Fallback (for examples/tests): Submit the signed JSON to the designated repository path or upload portal stated in the vote announcement.

Keep a copy of my_ballot.signed.json and your signature proof.

6) Verify & Tally (Local Sanity Check)
You can check that your ballot would be counted under published thresholds:

bash
Copy code
python governance/scripts/tally_votes.py \
  --ballots-dir governance/examples/ballots \
  --proposal-id GOV-2025-11-VM-OPC-01 \
  --eligible-power 1.0 \
  --quorum-percent 10.0 \
  --approval-threshold-percent 66.7 \
  --chain-id 2 \
  --snapshot-type height \
  --snapshot-value 1234567 \
  --pretty
Replace --ballots-dir with a folder containing your my_ballot.signed.json to test your own sample.

7) Troubleshooting
Schema errors: Re-run the generator and avoid adding extra fields.

Signature mismatch: Ensure you signed the canonical payload (no signature field included).

Wrong hash: Re-compute after any edit; the payloadHash must match.

Clock/snapshot: Ballots outside the configured window are rejected; check the announcement.

Weight > eligible: Tally will clamp/ignore excess; use your published voting power.

8) Security Notes
Never commit private keys.

Prefer hardware-backed signing when possible.

PQ (dilithium3) usage should follow governance/PQ_POLICY.md; publish your attestation if required.

Quick Reference
Template: generate_ballot.py … --out my_ballot.json

Canonicalize: Python snippet in §2

Sign: OpenSSL/CLI as per §3

Attach signature: Python snippet in §4

Validate: validate_proposal.py my_ballot.signed.json --strict

Tally: tally_votes.py … --pretty

