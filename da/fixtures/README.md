# Data Availability Fixtures

This directory contains **small, reproducible sample blobs** used by DA unit/integration tests and local dev workflows.  
They are deliberately tiny so tests run fast, while still exercising the full pipeline: chunk → erasure → NMT → commit → sample → proof.

## What's here

| File | Purpose | Nominal size | Notes |
|---|---|---:|---|
| `blob_small.bin` | Tiny blob for smoke tests and examples | 4 KiB | Deterministic bytes (see regeneration below) |
| `blob_medium.bin` | Moderate blob for erasure/NMT shape tests | 256 KiB | Deterministic bytes |
| `blob_manifest.json` | Example envelope metadata | ~ | Includes `namespace`, `mime`, `sha3_256`, optional `tags` |

> The exact namespace width and ranges are governed by **`da/config.py`** and **`da/constants.py`**; the fixtures themselves are namespace-agnostic binary data.

---

## How these are used

- **Unit tests**:  
  - `da/tests/test_retrieval_api.py` posts `blob_small.bin`, retrieves it by commitment, and checks the returned bytes.  
  - `da/tests/test_integration_post_get_verify.py` runs an end-to-end post → proof → light-verification flow.
- **CLI examples**:  
  - Post: `python -m da.cli.put_blob --ns 24 da/fixtures/blob_small.bin`  
  - Get: `python -m da.cli.get_blob --commit 0x… > /tmp/out.bin`  
  - Inspect root: `python -m da.cli.inspect_root 0x… --json`  
  - Sample sizing: `python -m da.cli.sim_sample --n 512 --k 256 --samples 80 --explain`

> The `--ns` value is an example; pick any namespace allowed by your network policy.

---

## Verifying checksums (local)

While Animica uses SHA3 for protocol hashing, your OS tools typically expose SHA-256. Here are portable ways to compute both:

### SHA3-256 (Python, cross-platform)
```bash
python - <<'PY'
import hashlib,sys,Pathlib as _; from pathlib import Path
for p in ["da/fixtures/blob_small.bin", "da/fixtures/blob_medium.bin"]:
    b = Path(p).read_bytes()
    print(p, hashlib.sha3_256(b).hexdigest())
PY

SHA-256 (common CLI)

# Linux
sha256sum da/fixtures/blob_small.bin da/fixtures/blob_medium.bin

# macOS (brew coreutils) or BSD shasum
shasum -a 256 da/fixtures/blob_small.bin da/fixtures/blob_medium.bin

Record these locally if you need reproducible provenance for CI artifacts.

⸻

Re-generating fixtures (deterministic)

If you ever need to recreate the blobs from scratch, run this deterministic generator. It uses a fixed seed so bytes and checksums are stable across machines.

python - <<'PY'
import os, random
os.makedirs("da/fixtures", exist_ok=True)

def write_det(path, size, seed):
    rnd = random.Random(seed)
    buf = bytearray(size)
    # Fill with deterministic pseudo-random bytes
    for i in range(size):
        buf[i] = rnd.randrange(256)
    with open(path, "wb") as f:
        f.write(buf)

write_det("da/fixtures/blob_small.bin",  4 * 1024,       seed=0xA11CE)
write_det("da/fixtures/blob_medium.bin", 256 * 1024,     seed=0xBEE5)
print("Rebuilt fixtures with deterministic content.")
PY

Recreate blob_manifest.json with your preferred fields (example):

{
  "namespace": 24,
  "mime": "application/octet-stream",
  "sha3_256": "<hex of blob_small.bin>",
  "tags": ["fixture", "example"]
}

The manifest is not a consensus object; it’s convenience metadata used by tooling and examples.

⸻

Posting & inspecting (quickstart)
	1.	Post a blob and print the commitment (NMT root):

python -m da.cli.put_blob --ns 24 da/fixtures/blob_small.bin

	2.	Inspect the commitment (digest-only or augmented form):

python -m da.cli.inspect_root 0x<commitment-hex> --json

	3.	Retrieve it back to confirm byte-for-byte integrity:

python -m da.cli.get_blob --commit 0x<commitment-hex> > /tmp/roundtrip.bin
cmp -l da/fixtures/blob_small.bin /tmp/roundtrip.bin && echo "OK"

	4.	Reason about DAS parameters (how many samples s you need for a target failure probability):

python -m da.cli.sim_sample --n 512 --k 256 --target-pfail 1e-9


⸻

Provenance, licensing, safety
	•	Provenance: Binary contents are deterministically generated from fixed seeds; no third-party or proprietary data.
	•	License: Fixtures may be redistributed under the repository’s root license.
	•	Security: Files contain no secrets/PII. Treat blob_* as inert random data.

⸻

Troubleshooting
	•	“Namespace out of range”: Check da/constants.py for valid namespace ranges and pass a permitted --ns.
	•	“Commitment size invalid”: Some tools accept augmented commitments (minNS‖maxNS‖digest) while others accept digest-only. Use da/cli/inspect_root.py to identify the form you have.
	•	“Proof validation fails”: Ensure n,k/erasure parameters match the node/service configuration (da/config.py) and that the NMT leaf codec matches da/nmt/codec.py.

⸻

Happy sampling! 🧪
