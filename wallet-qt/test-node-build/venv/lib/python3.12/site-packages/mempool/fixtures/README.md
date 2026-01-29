# mempool/fixtures

Curated datasets used by tests, benchmarks, and load/replay tools for the Animica mempool.
Everything here is **non-sensitive**, reproducible, and safe to share.

## What lives here

- **Small correctness sets**
  - `tiny_valid.json` — a handful of well-formed, valid transactions.
  - `edge_cases.json` — oversize, low-fee, nonce-gap, bad-sig, etc.
- **Load / replay corpora**
  - `bursts_1k.json` — 1,000 valid txs for burst/eviction testing.
  - `mixed_10k.cbor` — larger CBOR snapshot for sustained-rate replays.
- **Utility indexes**
  - `SHA256SUMS` — optional checksums for integrity.
  - `MANIFEST.json` — optional metadata (provenance, counts, notes).

> Tip: You don’t need all of these to run tests. Pytests create tiny, inline
> fixtures. This directory is mainly for **manual experiments** and **benchmarks**.

---

## File formats

We keep two interchange formats so tools can stay simple and fast.

### 1) JSON snapshot

Top-level can be either an object with `"entries"` or a raw list. Entries may be:

- A hex string: `"0x<hex of CBOR-encoded Tx>"`
- An object with at least `"raw"` (hex string *or* bytes-if-JSON-was-generated programmatically)
- (Optional) `"meta"` for human/diagnostics only (ignored by tools)

```jsonc
{
  "entries": [
    "0x...cbor_of_tx...",
    { "raw": "0x...cbor_of_tx...", "meta": { "label": "tip=5gwei, size=120b" } }
  ]
}

Notes:
	•	Hex may be "0x..." or bare hex (tools will normalize).
	•	Extra keys are ignored by replayers/validators.

2) CBOR snapshot

Either:
	•	An array of byte strings (each is a CBOR-encoded Tx), or
	•	A map with "entries" → array of maps where "raw" is a byte string.

This keeps parsing fast and compact for high-rate replay.

⸻

Where do these transactions come from?
	1.	Spec vectors
Many valid/invalid examples can be derived from spec/test_vectors/txs.json
(these are canonical, deterministic). For load corpora we typically repeat,
permute nonces, bump tips, and randomize access lists.
	2.	Synthetic generators
For replay/load tests without a live chain, we can synthesize transactions
with deterministic keys and signatures that pass the mempool’s stateless
checks. See “Generate your own” below.
	3.	Live sampling (devnet/testnet)
You can export the current pending pool from a node in a lab/devnet and
normalize it into these formats. Do not export from mainnet with real
keys/funds; keep labs isolated.

⸻

How to validate a snapshot locally
	•	Dry parse: ensure every entry is well-formed hex or bytes and decodes to a Tx

python - <<'PY'



import json, sys
from core.encoding.cbor import cbor_decode
from core.types.tx import Tx
from core.utils.bytes import from_hex
path = sys.argv[1]
blob = json.load(open(path))
entries = blob[“entries”] if isinstance(blob, dict) else blob
ok = 0
for i,e in enumerate(entries):
raw = e.get(“raw”, e) if isinstance(e, dict) else e
if isinstance(raw, str) and raw.startswith(“0x”): raw = bytes.fromhex(raw[2:])
elif isinstance(raw, str): raw = bytes.fromhex(raw)
tx = Tx.from_cbor(cbor_decode(raw))  # throws on failure
ok += 1
print(f”decoded {ok} txs OK”)
PY
~/animica/mempool/fixtures/tiny_valid.json

- **Replay into a node:**
```bash
python -m mempool.cli.replay \
  --rpc http://127.0.0.1:8645/rpc \
  --rate 250 --concurrency 16 \
  --input ~/animica/mempool/fixtures/bursts_1k.json \
  --progress 2


⸻

Generate your own fixtures

A) From spec vectors (deterministic, valid)

This converts canonical spec vectors to a JSON snapshot compatible with replayers.

python - <<'PY'
import json, sys
from core.encoding.cbor import cbor_encode
from core.types.tx import Tx

spec = json.load(open('~/animica/spec/test_vectors/txs.json'))
entries = []
for v in spec["valid"]:
    tx = Tx(
        chain_id=v["chain_id"],
        from_addr=bytes.fromhex(v["from"][2:]),
        to_addr=(bytes.fromhex(v["to"][2:]) if v["to"] else None),
        nonce=v["nonce"],
        value=int(v["value"]),
        gas_limit=v["gas_limit"],
        gas_price=int(v["gas_price"]),
        data=bytes.fromhex(v["data"][2:]) if v.get("data") else b"",
        access_list=[(bytes.fromhex(a[0][2:]), [bytes.fromhex(k[2:]) for k in a[1]]) for a in v.get("access_list",[])],
        sig=v.get("sig")  # already domain-separated in vectors
    )
    raw = cbor_encode(tx.to_cbor())
    entries.append({"raw": "0x"+raw.hex(), "meta": {"source": "spec/test_vectors/txs.json"}})

json.dump({"entries": entries}, open('~/animica/mempool/fixtures/tiny_valid.json','w'), indent=2)
print("wrote tiny_valid.json with", len(entries), "txs")
PY

B) Synthesize many valid txs (nonce/fee permutations)

python - <<'PY'
import json, os, random
from core.encoding.cbor import cbor_encode
from core.types.tx import Tx

random.seed(1337)
base_from = bytes.fromhex("00"*32)  # dev key placeholder (not secret)
to = bytes.fromhex("11"*32)
entries = []
nonce=0
for i in range(1000):
    tip_wei = 10_000 + i  # tiny ascending tips
    tx = Tx(chain_id=1337, from_addr=base_from, to_addr=to, nonce=nonce+i,
            value=0, gas_limit=21000, gas_price=tip_wei, data=b"", access_list=[],
            sig=None)  # leave sig=None if your mempool only prechecks statelessly
    raw = cbor_encode(tx.to_cbor())
    entries.append("0x"+raw.hex())
json.dump({"entries": entries}, open('~/animica/mempool/fixtures/bursts_1k.json','w'), indent=2)
print("wrote bursts_1k.json")
PY

If your node enforces signature precheck on admission, plug in pq signing to
produce valid signatures for the generator above.

C) Create CBOR snapshot

python - <<'PY'
import json
from binascii import unhexlify
src = json.load(open('~/animica/mempool/fixtures/bursts_1k.json'))
entries = src["entries"] if isinstance(src, dict) else src
with open('~/animica/mempool/fixtures/mixed_10k.cbor','wb') as f:
    import cbor2
    # example: just write first 10k (or all)
    raws = []
    for e in entries:
        h = e.get("raw", e) if isinstance(e, dict) else e
        if isinstance(h, str) and h.startswith("0x"): h = unhexlify(h[2:])
        elif isinstance(h, str): h = unhexlify(h)
        raws.append(h)
    cbor2.dump(raws, f)
print("wrote mixed_10k.cbor with", len(raws), "items")
PY


⸻

Naming conventions
	•	tiny_*.json — < 200 txs, hand-curated/deterministic.
	•	bursts_*.json — N txs designed to stress short-term admission/eviction.
	•	mixed_*.cbor — bigger corpora (≥ 10k) for sustained-rate replay.
	•	edge_cases.json — includes malformed entries for negative testing.

⸻

Integrity & reproducibility
	•	Prefer deterministic seeds and record them in MANIFEST.json.
	•	Optionally maintain SHA256SUMS:

(cd ~/animica/mempool/fixtures && sha256sum *.json *.cbor > SHA256SUMS)



⸻

Privacy & safety
	•	Never include real private keys or personally identifying metadata.
	•	Do not dump mainnet mempools or real-fund signatures into this repo.
	•	Fixtures here are for testing only and should not be broadcast to public networks.

⸻

Quick checks
	•	JSON validity:

jq . ~/animica/mempool/fixtures/bursts_1k.json >/dev/null


	•	Replay sanity:

python -m mempool.cli.replay --rpc http://127.0.0.1:8645/rpc --rate 200 \
  --concurrency 8 --input ~/animica/mempool/fixtures/bursts_1k.json --progress 2



