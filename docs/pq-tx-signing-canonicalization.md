# PQ TX signing canonicalization (v1)

Canonical signing preimage for transactions is defined in `python/animica/tx/signing.py::tx_signing_preimage`.

## Preimage format

Encoding: canonical CBOR (deterministic key ordering, definite lengths, no floats).

CBOR map (int keys):

1. domain (text) = `animica.tx.v1`
2. chain_id (uint)
3. genesis_hash (bytes)
4. network_name (text)
5. message_type (text) = `tx`
6. tx_version (uint)
7. tx_body (canonical body map, signatures excluded)

## Rules

- Signature fields are excluded from preimage.
- Domain separation is explicit via `domain + chain_id + genesis_hash + network_name + message_type + tx_version`.
- The same preimage function must be used by signing and verification.

## TXID

`txid = sha3_256(canonical_signed_tx_bytes)` where canonical signed tx bytes are full canonical tx envelope bytes, including signature(s).

## Migration / compatibility

- v1 canonical preimage is active for node verification paths using `tx_signing_preimage`.
- Legacy body-only sign-bytes remain available in helper APIs for compatibility (`build_signable_tx_bytes`) but should not be used for new tooling.
