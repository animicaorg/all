"""
Regression test for the `CoreChainAdapter._head_header` bug that left
pip-installed nodes stuck at genesis.

`P2PDeps.head()` returns `(height, head_hash_bytes)` despite its type
annotation suggesting `(int, Header)`. The previous implementation of
`_head_header` returned that hash directly, which crashed every outbound
dial from the core p2p service with::

    AttributeError: 'bytes' object has no attribute 'height'

This test pins the corrected behavior: the adapter must resolve the hash
back to a real `Header` (via `header_by_hash` or `header_by_number`) so
`best_header()` -> `_encode_header()` works.
"""
from __future__ import annotations

from types import SimpleNamespace

from p2p.core_p2p.chain_adapter import CoreChainAdapter


class _FakeHeader:
    """Duck-typed stand-in for `core.types.header.Header`."""

    def __init__(self, *, height: int, timestamp: int, parent_hash: bytes, head_hash: bytes):
        self.height = height
        self.timestamp = timestamp
        self.parentHash = parent_hash
        self._hash = head_hash

    def hash(self) -> bytes:
        return self._hash


def _make_deps(*, height: int, head_hash: bytes, header: _FakeHeader, allow_hash: bool, allow_height: bool):
    by_hash = {head_hash: header} if allow_hash else {}
    by_height = {height: header} if allow_height else {}
    return SimpleNamespace(
        head=lambda: (height, head_hash),
        header_by_hash=lambda h: by_hash.get(h),
        header_by_number=lambda n: by_height.get(n),
    )


def _encode_for(height: int, timestamp: int, parent_hash: bytes, head_hash: bytes) -> bytes:
    return (
        height.to_bytes(4, "little", signed=False)
        + timestamp.to_bytes(4, "little", signed=False)
        + parent_hash
        + head_hash
        + (b"\x00" * 8)
    )


def test_best_header_resolves_hash_to_header_via_header_by_hash():
    head_hash = b"\x11" * 32
    parent = b"\x00" * 32
    header = _FakeHeader(height=12180, timestamp=1775433600, parent_hash=parent, head_hash=head_hash)
    deps = _make_deps(
        height=12180, head_hash=head_hash, header=header, allow_hash=True, allow_height=False
    )

    adapter = CoreChainAdapter(deps=deps)
    encoded = adapter.best_header()
    assert encoded == _encode_for(12180, 1775433600, parent, head_hash)


def test_best_header_falls_back_to_header_by_number():
    head_hash = b"\x22" * 32
    parent = b"\x33" * 32
    header = _FakeHeader(height=42, timestamp=10, parent_hash=parent, head_hash=head_hash)
    deps = _make_deps(
        height=42, head_hash=head_hash, header=header, allow_hash=False, allow_height=True
    )

    adapter = CoreChainAdapter(deps=deps)
    encoded = adapter.best_header()
    assert encoded == _encode_for(42, 10, parent, head_hash)


def test_best_header_returns_empty_when_unresolvable():
    head_hash = b"\xaa" * 32
    deps = _make_deps(
        height=1,
        head_hash=head_hash,
        header=_FakeHeader(height=1, timestamp=0, parent_hash=b"\x00" * 32, head_hash=head_hash),
        allow_hash=False,
        allow_height=False,
    )

    adapter = CoreChainAdapter(deps=deps)
    assert adapter.best_header() == b""


def test_best_header_accepts_direct_header():
    """When `deps.head()` does return a real Header object the adapter
    should pass it straight through to `_encode_header`."""
    from core.types.header import Header

    head_hash = b"\xbb" * 32
    parent = b"\xcc" * 32
    # Use the real Header dataclass; only `height`, `timestamp`,
    # `parentHash` and `.hash()` matter for `_encode_header`.
    real_header = Header.genesis(
        chain_id=1,
        timestamp=99,
        state_root=b"\x00" * 32,
        txs_root=b"\x00" * 32,
        receipts_root=b"\x00" * 32,
        proofs_root=b"\x00" * 32,
        da_root=b"\x00" * 32,
        mix_seed=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        pq_alg_policy_root=b"\x00" * 32,
        theta_micro=1_000_000,
        extra=b"",
    )
    deps = SimpleNamespace(
        head=lambda: (0, real_header),
        header_by_hash=lambda h: None,
        header_by_number=lambda n: None,
    )
    adapter = CoreChainAdapter(deps=deps)
    encoded = adapter.best_header()
    assert encoded
    assert encoded[:4] == int(real_header.height).to_bytes(4, "little", signed=False)
