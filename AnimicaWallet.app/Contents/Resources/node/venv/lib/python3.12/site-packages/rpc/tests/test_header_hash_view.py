from rpc.methods.block import _compute_header_hash as block_header_hash
from rpc.methods.chain import _compute_header_hash as chain_header_hash


class _StubHeader:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def hash(self) -> bytes:
        return self._value


class _DictHeader(dict):
    def __init__(self, value: bytes) -> None:
        super().__init__({"hash": value})


def test_block_view_hash_prefers_header_hash_method() -> None:
    expected = b"\x11" * 32
    header = _StubHeader(expected)

    out = block_header_hash(header)

    assert out == "0x" + expected.hex()


def test_chain_view_hash_prefers_header_hash_method() -> None:
    expected = b"\x22" * 32
    header = _StubHeader(expected)

    out = chain_header_hash(header)

    assert out == "0x" + expected.hex()


def test_block_view_hash_accepts_dict_hash_bytes() -> None:
    expected = b"\x33" * 32
    header = _DictHeader(expected)

    out = block_header_hash(header)

    assert out == "0x" + expected.hex()


def test_chain_view_hash_accepts_dict_hash_bytes() -> None:
    expected = b"\x44" * 32
    header = _DictHeader(expected)

    out = chain_header_hash(header)

    assert out == "0x" + expected.hex()
