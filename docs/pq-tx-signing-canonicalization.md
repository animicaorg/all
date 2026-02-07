# PQ Tx signing canonicalization

This document defines the transaction-signing canonicalization used by RPC tx verification and P2P relay handling.

## TXSIG_V1

- **Domain tag:** `ANIMICA_TXSIG_V1`
- **Serialization:** canonical CBOR of transaction `body` only.
- **Excluded fields:** all signature fields (`sig`, `signature`, `sigs`) and transport-only wrapper fields.
- **Hashing for diagnostics:** `sha3_256`.

## Verification strategy

To keep backward compatibility while converging to deterministic behavior, verification tries:

1. `txsig_legacy.body_cbor`: canonical CBOR of signable body.
2. `txsig_v1.domain_prefixed`: `b"ANIMICA_TXSIG_V1" + body_cbor`.

The first path preserves existing mainnet/client behavior. The second is available for explicit TXSIG_V1 migration.

## Relay v2 expectations

- Relay carries signed tx bytes.
- Receivers normalize tx bytes once and keep the canonical bytes in tx relay store (`canonical_bytes`).
- Signature checks use canonical body bytes derived from the received envelope body (not lossy object reconstruction).

## Anti-storm behavior

When a tx is definitively invalid (hash mismatch, oversize, or mempool reject), tx relay marks request state as `invalid_final` and applies invalid cooldown (`ANIMICA_P2P_TX_INVALID_COOLDOWN_SEC`, default `1800s`).

Peers repeatedly sending invalid tx data are temporarily penalized and excluded from request candidate selection.
