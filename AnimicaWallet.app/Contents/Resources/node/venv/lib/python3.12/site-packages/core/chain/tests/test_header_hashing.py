from __future__ import annotations

from core.types.header import Header
from core.utils.hash import sha3_256


def test_header_hash_matches_cbor_digest() -> None:
    zero = b"\x00" * 32
    header = Header.genesis(
        chain_id=1,
        timestamp=1_700_000_000,
        state_root=zero,
        txs_root=zero,
        receipts_root=zero,
        proofs_root=zero,
        da_root=zero,
        mix_seed=zero,
        poies_policy_root=zero,
        pq_alg_policy_root=zero,
        theta_micro=1_000_000,
    )
    cbor_bytes = header.to_cbor()
    assert header.hash() == sha3_256(cbor_bytes)
