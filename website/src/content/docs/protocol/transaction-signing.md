---
title: "Transaction signing canonical specification"
description: "The canonical signing pipeline: the unsigned canonical-CBOR body, the domain-separated animica.tx.v1 preimage, the SHA3-512 sign-hash, and how nodes verify the signature envelope."
group: "protocol"
order: 3
draft: false
---

*Source: `docs/tx-signing.md` — this page mirrors the repository documentation.*

This document defines the single canonical transaction signing pipeline used by node verification and CLI signing.

## Canonical tx body bytes

- Start from tx envelope.
- Extract `tx` (normalized envelope) or `body` (legacy envelope).
- Remove signature fields (`sig`, `signature`, `sigs`).
- Canonically CBOR-encode the resulting body map (`cbor2.dumps(..., canonical=True)` semantics).

Reference implementation: `animica.tx.signing.tx_canonical_bytes_unsigned`.

## Canonical sign-bytes/preimage

Use `animica.tx.signing.tx_signing_preimage`:

```text
preimage_obj = {
  1: "animica.tx.v1",       # signing domain/version namespace
  2: chain_id,               # integer
  3: genesis_hash_bytes,     # bytes
  4: network_name,           # string
  5: "tx",                  # message type
  6: tx_version,             # integer (from body.v/body.version, default 1)
  7: canonical_tx_body_map,  # map
}
sign_bytes = canonical_cbor(preimage_obj)
```

## Canonical sign-hash

`sign_hash = SHA3-512(sign_bytes)`.

This hash is for diagnostics and parity checks. PQ signing APIs sign the canonical preimage bytes with `domain="tx"`, `chain_id`, `fork_id`, and `prehash` metadata.

## Verification

- Node parses `algId`/`pubkey`/`sig`/`domain`/`prehash` from tx signature envelope.
- Node reconstructs canonical sign bytes from the normalized tx envelope.
- Node verifies PQ signature using declared `scheme_id` and signature metadata.
- Node also validates optional `from` address binding to pubkey fingerprint via address derivation when available.

Reference: `rpc.methods.tx._verify_pq_signature` and `animica.tx.signing.tx_verify_signature`.
