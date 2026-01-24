import pytest

from p2p.protocol import handshake_v1 as hs_v1


def test_handshake_roundtrip() -> None:
    msg = hs_v1.HandshakeV1(
        protocol_version=hs_v1.HANDSHAKE_VERSION,
        network="mainnet",
        chain_id=1,
        genesis_hash="00" * 32,
        node_id="11" * 32,
        pubkey="",
        listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
        timestamp=1710000000,
        nonce=42,
        capabilities={"agent": "animica/test", "capabilities": ["tx"]},
        head_height=1,
        head_hash="22" * 32,
        network_best_height=1,
        consensus_id="poies/test",
        fork_id=1,
        protocol_version_str="1.0",
        network_magic="aa" * 4,
    )
    payload = hs_v1.encode_handshake(msg)
    decoded = hs_v1.decode_handshake(payload)
    assert decoded.network == msg.network
    assert decoded.chain_id == msg.chain_id
    assert decoded.genesis_hash == msg.genesis_hash
    assert decoded.node_id == msg.node_id
    assert decoded.capabilities["agent"] == "animica/test"
    assert decoded.head_height == 1


def test_handshake_invalid_magic() -> None:
    with pytest.raises(hs_v1.HandshakeDecodeError):
        hs_v1.decode_handshake(b"INVALID\n{}")


def test_handshake_invalid_schema() -> None:
    data = hs_v1.HANDSHAKE_MAGIC + b'{"type":"handshake","v":1,"payload":{"network":"mainnet"}}\n'
    with pytest.raises(hs_v1.HandshakeValidationError):
        hs_v1.decode_handshake(data)
