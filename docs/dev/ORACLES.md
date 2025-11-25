# Oracles — DA Posters & On-chain Consumers

This guide explains how to build **data oracles** on Animica using **DA blobs**
for large payloads and **on-chain attestations** for trust and usability by
contracts written for the Python VM.

It covers:
- Roles (posters, aggregators, consumers) and the **push-based** flow,
- A canonical **feed envelope** (signed, content-addressed),
- Posting strategies: **direct on-chain** vs **DA + commitment**,
- A minimal **OracleRegistry** & **PriceOracle** contract pattern,
- Security (key rotation, staleness, consensus with DEX anchors),
- Gas/storage and performance considerations.

> Background reading:  
> - [DA Overview](../da/OVERVIEW.md), [Erasure Layout](../da/ERASURE_LAYOUT.md), [Sampling](../da/SAMPLING.md)  
> - [VM ABI](../vm/ABI.md), [Encoding](../spec/ENCODING.md)

---

## 1) Roles & Flow

    off-chain                         on-chain

┌────────────────────┐          ┌────────────────────────────┐
│  Data Providers    │          │ Oracle Contracts           │
│  (exchanges, APIs) │          │ - Registry (signers/feeds) │
└─────────┬──────────┘          │ - Feed (median/EMA)        │
│                     └─────────┬──────────────────┘
▼                               │
┌────────────────────┐  blobs  ┌──────────▼─────────┐
│  Posters / Nodes   │────────▶│   DA Service      │  (NMT root)
│  - sign feed       │         └──────────┬─────────┘
│  - upload blob     │                    │ root / receipt
└─────────┬──────────┘                    ▼
│                      ┌──────────────────────┐
│ attest (tx)          │   L1 Transaction     │
└─────────────────────▶│ - commit(feed_id,    │
│          nmt_root,   │
│          digest, …)  │
└──────────────────────┘
│
▼
Consumer Contracts
- pull latest price
- enforce staleness bounds

**Key pattern:** Posters **push** new data by uploading it to DA (if large) and
submitting a **small on-chain attestation** (commitment + quorum signature).
Consumer contracts **pull** compact, on-chain summaries (e.g., median, EMA) and
optionally verify that the summary matches the posted commitment/digest.

---

## 2) Canonical Feed Envelope (Signed)

Feeds are signed messages; large payloads (per-symbol details) may live in DA.

**CDDL sketch** (see `docs/spec/ENCODING.md` for tags & DS domains):

```cddl
; domain tag: 6001 ("animica/oracle/feed")
FeedEnvelope = #6.6001 {
  v:        0,                          ; envelope version
  feed_id:  tstr,                       ; e.g., "prices.spot.v1"
  slot:     uint,                       ; unix seconds or block height
  chain_id: uint,                       ; target chain id
  payload:  bstr,                       ; CBOR map or Merkle root preimage
  digest:   bstr .size 32,              ; BLAKE3-256 payload digest
  signers:  [+ Sig]                     ; 1..N signatures
}

Sig = {
  kid:     tstr,                        ; key id (registry key name or fingerprint)
  alg:     tstr,                        ; "dilithium3" | "sphincs_shake_128s"
  sig:     bstr,                        ; signature bytes
}

Payload options
	•	Compact map for direct on-chain posting:

{ "pairs": { "ANM/USDT": 0.123456e6, "ANM/ETH": 0.00045e9, ... },
  "ts": 1712345678, "twap_s": 60 }


	•	Merkleized rows (for very large universes): payload is the root preimage,
and each row is (key, value) included in the DA blob; the on-chain contract
validates a row-level Merkle proof against the posted digest (optional).

Digest: BLAKE3-256 over canonical CBOR of payload with
domain-separation prefix b"animica/oracle/payload/v0".

⸻

3) Posting Strategies

3.1 Direct On-chain

When the feed is small (e.g., ≤ 16–32 pairs), posters submit the entire envelope
as calldata. The oracle contract verifies quorum signatures and updates stored
values.

Pros: immediate availability, simplest to consume.
Cons: gas heavy for large sets; calldata fees.

3.2 DA + Commitment (Recommended for large feeds)
	•	Upload the FeedEnvelope (or just the payload rows) to DA; obtain:
	•	nmt_root (namespaced Merkle tree root),
	•	a DA receipt (availability proof).
	•	Submit a tiny attestation tx:

commit(feed_id, slot, digest, nmt_root, [Sig...], da_receipt_hash)


	•	The contract verifies signatures/quorum and stores digest + a compact
summary (median, ema, count). Consumer contracts need only the summary.

Optionally, consumers that require per-pair proofs can accept an inclusion
proof against the posted digest (Merkleized payload). This pushes verification
costs to callers who need them.

⸻

4) On-chain Contracts (Patterns)

4.1 OracleRegistry (signers & quorum)
	•	Maintains feed metadata:
	•	active signer keys (PQ pubkeys),
	•	quorum rule (e.g., M-of-N),
	•	staleness bound (e.g., max age seconds),
	•	decimals per symbol (or feed-wide).
	•	Supports key rotation with timelocks.

Storage sketch

feeds[feed_id] = {
  chain_id, decimals, quorum_m, quorum_n, max_age_s,
  signers: { kid -> PubKey }, version
}

Events
	•	SignerAdded(feed_id, kid)
	•	SignerRevoked(feed_id, kid)
	•	QuorumUpdated(feed_id, m, n)
	•	FeedCommitted(feed_id, slot, digest, nmt_root?)

4.2 PriceOracle (commit & read)
	•	commit(envelope|attestation):
	•	Verify chain_id matches,
	•	Check quorum on signatures over (feed_id, slot, digest),
	•	Enforce non-decreasing slot,
	•	Store: latest[feed_id] = {slot, digest, summary, nmt_root?},
	•	Emit FeedCommitted.
	•	get_price(pair) -> (price, slot):
	•	Return median or EMA for the pair from latest summary,
	•	require(now - slot ≤ max_age_s).
	•	Optional:
	•	verify_pair(pair, value, proof) -> bool:
	•	Validate Merkle proof vs digest if payload is merkleized.

Design note: Summary must be deterministically computed by posters
(median, ema) and provided in the attestation to avoid recomputation on-chain.
The contract verifies the summary binding (either includes it in digest or
recomputes from a compact proof subset).

⸻

5) Example: Minimal Python VM Contracts

Pseudocode (illustrative; see docs/vm/ABI.md for concrete decorators/types).

# contracts/oracles.py
from animica.vm.abi import public, view, event, storage, ensure
from animica.crypto.pq import verify_sig  # deterministic PQ verify
from animica.hash import blake3_256

class OracleRegistry:
    feeds = storage.map(bytes, dict)     # feed_id -> meta
    keys  = storage.map(bytes, bytes)    # kid -> pubkey

    @public
    def add_signer(feed_id: bytes, kid: bytes, pubkey: bytes):
        meta = self.feeds.get(feed_id, default={ "m": 0, "n": 0, "max_age": 0, "signers": {} })
        meta["signers"][kid] = pubkey
        meta["n"] = len(meta["signers"])
        self.feeds[feed_id] = meta
        event("SignerAdded", feed_id, kid)

    @public
    def set_quorum(feed_id: bytes, m: int, max_age: int):
        meta = self.feeds[feed_id]; meta["m"] = m; meta["max_age"] = max_age
        self.feeds[feed_id] = meta
        event("QuorumUpdated", feed_id, m)

    @view
    def get_meta(feed_id: bytes) -> dict:
        return self.feeds[feed_id]


class PriceOracle:
    registry: OracleRegistry
    latest = storage.map(bytes, dict)  # feed_id -> {slot, digest, nmt_root?, summary}

    @public
    def commit(self, feed_id: bytes, slot: int, digest: bytes, nmt_root: bytes|None,
               sigs: list[dict], summary: dict):
        meta = self.registry.get_meta(feed_id)
        ensure(meta["n"] >= meta["m"] and meta["m"] > 0, "oracle/quorum-unset")
        # Domain-separated sign bytes
        msg = b"animica/oracle/attest/v0" + feed_id + slot.to_bytes(8,"big") + digest
        # Verify unique signers
        seen = set(); ok = 0
        for s in sigs:
            kid, alg, sig = s["kid"], s["alg"], s["sig"]
            ensure(kid in meta["signers"], "oracle/unknown-signer")
            ensure(kid not in seen, "oracle/dup-signer")
            pub = meta["signers"][kid]
            ensure(verify_sig(alg, pub, msg, sig), "oracle/bad-sig")
            seen.add(kid); ok += 1
        ensure(ok >= meta["m"], "oracle/quorum-failed")

        prev = self.latest.get(feed_id, default=None)
        ensure(prev is None or slot > prev["slot"], "oracle/non-monotonic-slot")

        # Optionally bind summary to digest to avoid tampering:
        # ensure(blake3_256(encode(summary)) == summary_digest)

        self.latest[feed_id] = { "slot": slot, "digest": digest, "nmt_root": nmt_root, "summary": summary }
        event("FeedCommitted", feed_id, slot, digest, nmt_root)

    @view
    def get_price(self, feed_id: bytes, pair: str) -> tuple[int,int]:
        rec = self.latest[feed_id]
        meta = self.registry.get_meta(feed_id)
        # Staleness check
        now = env.block_time()  # deterministic time source
        ensure(now - rec["slot"] <= meta["max_age"], "oracle/stale")
        price = rec["summary"]["pairs"][pair]  # median/ema already aggregated
        return price, rec["slot"]

Notes
	•	verify_sig supports PQ algorithms available to the chain (e.g., Dilithium3).
	•	For Merkleized payloads, add a helper verify_pair(feed_id, pair, value, proof) that
recomputes leaf = H(pair || value) and verifies path to digest.

⸻

6) Security Considerations
	•	Quorum & Diversity: prefer 3-of-5 or 5-of-8 with operators on
independent infra and data sources.
	•	Staleness Bounds: every consumer must guard now - slot ≤ max_age.
	•	Anchors: for price feeds, cross-check against DEX TWAP to bound drift:
	•	If |oracle - dex_twap| / dex_twap > ε, freeze or reduce leverage.
	•	Key Rotation:
	•	Add new keys, increase n, then after a delay, drop old keys.
	•	Use key ids (KIDs) stable across rotations.
	•	Replay Protection:
	•	Sign (feed_id, slot, digest) with domain tag; enforce strictly increasing slot.
	•	Byzantine Posters:
	•	Quorum rules limit single-source bias. Consider economic bonds for posters.
	•	DA Withholding:
	•	Contracts rely on the on-chain summary, not on reading DA mid-call.
	•	Off-chain watchers should sample DA availability and alert/failover posters.

⸻

7) Gas & Storage
	•	Direct on-chain posting of 16 pairs (price+ts) fits well under typical block
gas limits. Beyond ~64–128 pairs, switch to DA+commitment and keep only
the summary on-chain.
	•	For per-pair verification on-chain, Merkle proofs are ~32 * depth bytes;
depth ~ceil(log2(N)). Keep branching factor/bucketization in mind.

⸻

8) Feed Design Guidelines
	•	Deterministic Summary: Compute median (robust) or EMA off-chain;
include it in the signed envelope to avoid recomputation.
	•	Decimals & Rounding: Use scaled integers (e.g., 1e8) and document per-feed.
	•	Symbols: Canonical uppercase with / separator, e.g., ANM/USDT.
	•	Timebase: Prefer unix seconds for slot. If you must use block height,
provide a mapping slot_ts in the summary for consumer convenience.
	•	Failure Modes: Define status fields (OK|DEGRADED|FROZEN) and react in consumers.

⸻

9) Example DA Posting (Off-chain Pseudocode)

from cbor2 import dumps
from blake3 import blake3
from pqsig import sign  # wraps Dilithium3/Sphincs

payload = { "pairs": { "ANM/USDT": 129_876_543, "ANM/ETH": 456_700, "ANM/BTC": 2_345 }, "ts": 1712345678 }
digest  = blake3(b"animica/oracle/payload/v0" + dumps(payload)).digest()
env     = { "v":0, "feed_id":"prices.spot.v1", "slot":1712345678, "chain_id":1,
            "payload": dumps(payload), "digest": digest, "signers":[] }

for kid, key in active_signers:
    sig = sign("dilithium3", key.sk, b"animica/oracle/attest/v0" + env["feed_id"].encode()
               + env["slot"].to_bytes(8,"big") + env["digest"])
    env["signers"].append({ "kid": kid, "alg":"dilithium3", "sig": sig })

# Upload payload to DA → get nmt_root, receipt_hash
nmt_root, receipt = da_upload(dumps(payload))
tx_call_oracle_commit(feed_id=env["feed_id"], slot=env["slot"], digest=digest,
                      nmt_root=nmt_root, sigs=env["signers"],
                      summary={"pairs":{"ANM/USDT":129_876_543,"ANM/ETH":456_700,"ANM/BTC":2_345},
                               "twap_s":60, "count":3})


⸻

10) Consumer Patterns
	•	Lending / Perps: check stale ≤ max_age, guard with deviation caps vs
on-chain DEX TWAP; allow grace periods during network incidents.
	•	Insurance / Settlements: require multi-source quorum across different feeds.
	•	Off-chain Services: explorers can show both digest and nmt_root and
link DA receipts (see docs/dev/INDEXER.md for schema ideas).

⸻

11) Testing
	•	Unit test: signature verification, quorum math, slot monotonicity, staleness.
	•	Property test: random signer sets; ensure no combination below quorum passes.
	•	Integration test: devnet + DA mock; post feed, read from a sample consumer, simulate staleness.

⸻

12) FAQs

Q: Can a contract pull data from DA during execution?
A: No. Contracts must use on-chain commitments/summaries only. DA is enforced by
consensus; availability is monitored off-chain.

Q: Why PQ signatures for posters?
A: Aligns with chain-wide PQ guarantees. The VM exposes deterministic PQ verify.

Q: How do we rotate signers?
A: Add new keys → raise n → wait grace period → lower m if needed → revoke old keys.

⸻

References
	•	DA Security
	•	VM Capabilities
	•	Encoding & Domain Tags
	•	Indexer Schema

