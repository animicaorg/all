from core.types.header import HASH32_LEN, Header


def _make_minimal_genesis_header() -> Header:
    zero = b"\x00" * HASH32_LEN
    return Header.genesis(
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
        extra=b"animica-test",
    )


def test_header_hash_api() -> None:
    header = _make_minimal_genesis_header()
    assert hasattr(header, "hash")
    header_hash = header.hash()
    assert isinstance(header_hash, (bytes, bytearray))
    assert len(header_hash) == HASH32_LEN
