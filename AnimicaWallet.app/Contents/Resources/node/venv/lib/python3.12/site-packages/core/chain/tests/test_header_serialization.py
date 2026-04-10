from __future__ import annotations

from core.chain.block_import import compute_header_hash
from core.types.header import Header, serialize_header
from core.utils.hash import sha3_256


def test_header_serialization_matches_node_hash() -> None:
    zero32 = b"\x00" * 32
    header = Header(
        v=1,
        chainId=1,
        height=1,
        parentHash=zero32,
        timestamp=1_700_000_000,
        stateRoot=zero32,
        txsRoot=zero32,
        receiptsRoot=zero32,
        proofsRoot=zero32,
        daRoot=zero32,
        mixSeed=zero32,
        poiesPolicyRoot=zero32,
        pqAlgPolicyRoot=zero32,
        thetaMicro=1_000_000,
        nonce=42,
        extra=b"",
    )

    serialized = serialize_header(header)
    cli_hash = sha3_256(serialized)
    node_hash = compute_header_hash(header)

    assert serialized == header.to_cbor()
    assert cli_hash == node_hash
