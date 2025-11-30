from __future__ import annotations

from da.adapters.core_chain import (BlobInclusion, compute_da_root,
                                    validate_da_root)
from da.nmt.codec import encode_leaf


def test_da_root_recomputes_from_inclusions():
    """Recomputing a DA root from inclusions should match the header value."""

    leaves = [encode_leaf(7, b"hello"), encode_leaf(7, b"world")]
    inclusion = BlobInclusion(namespace=7, commitment=b"c" * 32, size=10, leaves=leaves)

    da_root = compute_da_root([inclusion], mode="leaves")
    # validate raises on mismatch, so a matching root should be silent
    validate_da_root(header_da_root=da_root, inclusions=[inclusion], mode="leaves")


def test_da_root_mismatch_is_detected():
    leaves = [encode_leaf(1, b"a")]
    inclusion = BlobInclusion(namespace=1, commitment=b"d" * 32, size=1, leaves=leaves)

    da_root = compute_da_root([inclusion], mode="leaves")

    try:
        validate_da_root(
            header_da_root=b"\x00" * len(da_root), inclusions=[inclusion], mode="leaves"
        )
    except Exception as exc:
        assert "DA root mismatch" in str(exc)
    else:  # pragma: no cover - should not reach
        raise AssertionError("validate_da_root should reject mismatched roots")
