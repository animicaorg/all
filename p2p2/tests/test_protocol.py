"""
Unit tests for P2P2 protocol encoding/decoding.
"""

import pytest
from p2p2.protocol import (
    Message,
    MsgType,
    encode_message,
    decode_frame,
    create_hello,
    create_ping,
    create_pong,
    InvItem,
    create_inv,
)


def test_message_encoding_decoding():
    """Test basic message encode/decode."""
    msg = Message(type=MsgType.PING, payload={"nonce": "test123"})
    
    # Encode
    data = encode_message(msg)
    assert len(data) > 4  # At least length prefix
    
    # Decode
    decoded_msg, consumed = decode_frame(data)
    assert decoded_msg is not None
    assert decoded_msg.type == MsgType.PING
    assert decoded_msg.payload["nonce"] == "test123"
    assert consumed == len(data)


def test_partial_frame():
    """Test decoding partial frame."""
    msg = Message(type=MsgType.PING, payload={"test": "data"})
    data = encode_message(msg)
    
    # Only first 2 bytes
    partial = data[:2]
    decoded_msg, consumed = decode_frame(partial)
    assert decoded_msg is None
    assert consumed == 0
    
    # First 4 bytes (length only)
    partial = data[:4]
    decoded_msg, consumed = decode_frame(partial)
    assert decoded_msg is None
    assert consumed == 0
    
    # Partial payload
    partial = data[:len(data) - 5]
    decoded_msg, consumed = decode_frame(partial)
    assert decoded_msg is None
    assert consumed == 0


def test_hello_message():
    """Test HELLO message creation."""
    hello = create_hello(
        node_id="test-node-123",
        network_id="testnet",
        chain_id=1337,
        genesis_hash="abc123",
        protocol_version=1,
        services=3,
        listen_addrs=["127.0.0.1:9333"],
        best_height=100,
        best_hash="def456",
    )
    
    assert hello.type == MsgType.HELLO
    assert hello.payload["node_id"] == "test-node-123"
    assert hello.payload["chain_id"] == 1337
    assert hello.payload["best_height"] == 100


def test_ping_pong():
    """Test PING/PONG messages."""
    ping = create_ping()
    assert ping.type == MsgType.PING
    assert "nonce" in ping.payload
    
    pong = create_pong(ping.payload["nonce"])
    assert pong.type == MsgType.PONG
    assert pong.payload["nonce"] == ping.payload["nonce"]


def test_inv_message():
    """Test INV message creation."""
    items = [
        InvItem(type="block", hash="hash1"),
        InvItem(type="tx", hash="hash2"),
    ]
    
    inv = create_inv(items)
    assert inv.type == MsgType.INV
    assert len(inv.payload["items"]) == 2
    assert inv.payload["items"][0]["type"] == "block"


def test_large_message_limit():
    """Test message size limit."""
    from p2p2.protocol import MAX_MESSAGE_SIZE
    
    # Create huge payload
    huge_payload = {"data": "x" * (MAX_MESSAGE_SIZE + 1000)}
    msg = Message(type=MsgType.BLOCK, payload=huge_payload)
    
    with pytest.raises(ValueError, match="too large"):
        encode_message(msg)


def test_multiple_frames():
    """Test decoding multiple frames in one buffer."""
    msg1 = Message(type=MsgType.PING, payload={"nonce": "1"})
    msg2 = Message(type=MsgType.PONG, payload={"nonce": "2"})
    
    data1 = encode_message(msg1)
    data2 = encode_message(msg2)
    
    # Concatenate
    combined = data1 + data2
    
    # Decode first
    decoded1, consumed1 = decode_frame(combined)
    assert decoded1 is not None
    assert decoded1.type == MsgType.PING
    assert consumed1 == len(data1)
    
    # Decode second
    decoded2, consumed2 = decode_frame(combined[consumed1:])
    assert decoded2 is not None
    assert decoded2.type == MsgType.PONG
    assert consumed2 == len(data2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
