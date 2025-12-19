import hashlib

from p2p.core_p2p.netaddress import NetAddress
from p2p.core_p2p.protocol import (
    AddrMessage,
    HeadersMessage,
    InventoryVector,
    InvMessage,
    decode_header,
    encode_compact_size,
    encode_message,
    read_compact_size,
)


def test_compact_size_roundtrip():
    values = [0, 1, 252, 253, 254, 65535, 70000, 2**32, 2**40]
    for value in values:
        encoded = encode_compact_size(value)
        decoded, offset = read_compact_size(encoded, 0)
        assert decoded == value
        assert offset == len(encoded)


def test_message_envelope_roundtrip():
    payload = b"hello"
    raw = encode_message("ping", payload)
    command, length, checksum = decode_header(raw[:24])
    assert command == "ping"
    assert length == len(payload)
    assert checksum == hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def test_addr_message_roundtrip():
    addr = NetAddress(services=1, ip="127.0.0.1", port=8333)
    msg = AddrMessage([addr])
    parsed = AddrMessage.parse(msg.serialize())
    assert parsed.addresses[0].ip == "127.0.0.1"
    assert parsed.addresses[0].port == 8333


def test_inv_roundtrip():
    inv = InventoryVector(inv_type=2, inv_hash=b"\x11" * 32)
    msg = InvMessage([inv])
    parsed = InvMessage.parse(msg.serialize())
    assert parsed.inventory[0].inv_type == 2
    assert parsed.inventory[0].inv_hash == b"\x11" * 32


def test_headers_roundtrip():
    header = b"\x00" * 80
    msg = HeadersMessage([header])
    parsed = HeadersMessage.parse(msg.serialize())
    assert parsed.headers == [header]
