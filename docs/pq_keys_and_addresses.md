# Animica PQ keys and addresses

This document summarizes how Animica represents post-quantum (PQ) public keys and how those keys are rendered into user-facing addresses. The primitives live in `pq.py`, notably `pq.py.keygen` for keypair generation and `pq.py.address` for address derivation.

## Key generation recap

Signature-capable PQ keypairs are produced via `pq.py.keygen.keygen_sig`, which returns a `SigKeypair` containing:

- `alg_id` / `alg_name`: algorithm identifiers loaded from `pq/alg_ids.yaml` (for example, Dilithium3 is `0x0103`).
- `public_key` and `secret_key`: raw byte strings suitable for signing and verification.
- `address`: the bech32m-encoded address derived from the public key (see below).

`keygen_sig` accepts an optional `seed` (for deterministic dev/test keys) and an optional human-readable prefix (`hrp`, default `anim`) to namespace addresses.

## Address derivation steps

Animica addresses are bound to a specific PQ algorithm and public key. The codec in `pq.py.address` follows these steps:

1. Compute the 34-byte payload as `alg_id_be16 || sha3_256(public_key)`, where `alg_id_be16` is the 2-byte big-endian algorithm identifier and `sha3_256(public_key)` is a 32-byte digest of the raw public key.
2. Convert the payload from 8-bit bytes to 5-bit words using bech32 `convertbits` with padding enabled.
3. Encode the result using bech32m with the desired HRP (default `anim`).

The output is a checksummed, lowercase bech32m string such as `anim1...`. Decoding reverses these steps and validates the checksum, HRP, and payload length before returning the algorithm id and digest.

## Sample derivations

The examples below were generated with the canonical codec and can be used as fixtures in integration tests:

- Dilithium3 (`alg_id = 0x0103`), public key `0x01` repeated 48 times → `anim1zqqhej6vejt4rren3q9rxs7gqw56ecfs4fw64sfh939vxv9x4xc7x0slx80pd`
- SPHINCS+-SHAKE-128s (`alg_id = 0x0201`), public key `0x02` repeated 32 times → `anim1zqpyvevaz2ac7crapa0wrueqyr67gwy55v5ccaeq82znh6u5vqs3e4gcet4vs`

If you need to derive fresh addresses in code, prefer the helper `pq.py.address.address_from_pubkey(pubkey, alg_id, hrp="anim")`, which performs the exact procedure outlined above.
