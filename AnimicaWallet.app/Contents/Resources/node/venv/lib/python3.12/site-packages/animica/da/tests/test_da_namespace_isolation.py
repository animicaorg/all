from __future__ import annotations

import pathlib
import random
import sys

import pytest

# Ensure the local "python/animica" package is importable when running tests
# from the repo root.
THIS_FILE = pathlib.Path(__file__).resolve()
# .../python/animica/da/tests/test_da_namespace_isolation.py
# parents[0] = tests, [1] = da, [2] = animica, [3] = python
PYTHON_ROOT = THIS_FILE.parents[3]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from animica.da.pipeline import encode_blob_for_da, reconstruct_blob_from_da


def _random_blob(length: int, seed: int = 4242) -> bytes:
    rnd = random.Random(seed)
    return bytes(rnd.getrandbits(8) for _ in range(length))


def _ns(tag: int) -> bytes:
    """
    Construct a deterministic 8-byte namespace from a small integer tag.
    """
    return tag.to_bytes(8, "big")


COMMON_PARAMS = {
    "chunk_size": 1024,
    "data_shards": 8,
    "parity_shards": 4,
}


def test_different_namespaces_produce_different_roots_for_same_blob() -> None:
    blob = _random_blob(10_000, seed=1)

    ns_a = _ns(0x0102030405060708)
    ns_b = _ns(0x1112131415161718)

    commitment_a = encode_blob_for_da(blob, namespace=ns_a, **COMMON_PARAMS)
    commitment_b = encode_blob_for_da(blob, namespace=ns_b, **COMMON_PARAMS)

    # Namespaces should differ.
    assert commitment_a["namespace"] != commitment_b["namespace"]

    # Roots must differ as well; otherwise namespace separation has no effect.
    root_a = commitment_a["root"]
    root_b = commitment_b["root"]

    assert isinstance(root_a, (bytes, bytearray))
    assert isinstance(root_b, (bytes, bytearray))
    assert root_a != root_b, "DA roots should differ for different namespaces"


def test_shards_cannot_cross_namespaces() -> None:
    blob_a = _random_blob(5000, seed=2)
    blob_b = _random_blob(5000, seed=3)

    ns_a = _ns(0xAAAA_BBBB_CCCC_DD01)
    ns_b = _ns(0xAAAA_BBBB_CCCC_DD02)

    commit_a = encode_blob_for_da(blob_a, namespace=ns_a, **COMMON_PARAMS)
    commit_b = encode_blob_for_da(blob_b, namespace=ns_b, **COMMON_PARAMS)

    shards_a = commit_a["shards"]
    shards_b = commit_b["shards"]

    # Sanity: both sets of shards are non-empty.
    assert isinstance(shards_a, list) and shards_a
    assert isinstance(shards_b, list) and shards_b

    # Using shards from A with commitment for B must fail verification
    with pytest.raises(ValueError):
        reconstruct_blob_from_da(commit_b, shards_a)

    # And vice versa
    with pytest.raises(ValueError):
        reconstruct_blob_from_da(commit_a, shards_b)


def test_mixed_shard_sets_fail_verification() -> None:
    blob_a = _random_blob(8000, seed=4)
    blob_b = _random_blob(8000, seed=5)

    ns_a = _ns(0xDEADBEEF00000001)
    ns_b = _ns(0xDEADBEEF00000002)

    commit_a = encode_blob_for_da(blob_a, namespace=ns_a, **COMMON_PARAMS)
    commit_b = encode_blob_for_da(blob_b, namespace=ns_b, **COMMON_PARAMS)

    shards_a = list(commit_a["shards"])
    shards_b = list(commit_b["shards"])

    assert shards_a and shards_b

    # Build a "poisoned" shard set that mixes data from both namespaces.
    # Even if counts look reasonable, the commitment root must catch this.
    mixed = []

    # Take first half of shards from A and then first half from B.
    half_a = max(1, len(shards_a) // 2)
    half_b = max(1, len(shards_b) // 2)

    mixed.extend(shards_a[:half_a])
    mixed.extend(shards_b[:half_b])

    # Reconstruction under A's commitment must fail due to root mismatch.
    with pytest.raises(ValueError):
        reconstruct_blob_from_da(commit_a, mixed)

    # And reconstruction under B's commitment must fail as well.
    with pytest.raises(ValueError):
        reconstruct_blob_from_da(commit_b, mixed)
