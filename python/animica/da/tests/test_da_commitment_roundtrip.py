from __future__ import annotations

import pathlib
import random
import sys

import pytest

# Ensure the local "python/animica" package is importable when running tests
# from the repo root.
THIS_FILE = pathlib.Path(__file__).resolve()
# .../python/animica/da/tests/test_da_commitment_roundtrip.py
# parents[0] = tests, [1] = da, [2] = animica, [3] = python
PYTHON_ROOT = THIS_FILE.parents[3]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from animica.da.pipeline import encode_blob_for_da, reconstruct_blob_from_da


def _random_blob(length: int, seed: int = 1337) -> bytes:
    rnd = random.Random(seed)
    return bytes(rnd.getrandbits(8) for _ in range(length))


@pytest.mark.parametrize(
    "blob_len",
    [
        1,          # tiniest non-empty blob
        137,        # odd, non-aligned length
        4096,       # typical "page" sized blob
        32_768,     # larger multi-chunk blob
    ],
)
def test_da_commitment_roundtrip(blob_len: int) -> None:
    """
    Full DA pipeline:

        blob
          → (chunking + RS layout)
          → (NMT over data namespace)
          → commitment (namespace + root + layout/meta)
          → "store" shards (here: just keep them in-memory)
          → retrieve & reconstruct
          → exact original blob bytes

    This test only asserts the "happy path" where no shards are lost.
    RS error cases are already covered by the animica_native RS tests.
    """
    blob = _random_blob(blob_len)
    # Use a fixed namespace key to keep the test deterministic.
    namespace = (0x01_02_03_04_05_06_07_08).to_bytes(8, "big")

    # Reasonable DA params; these should be accepted by the pipeline.
    params = {
        "chunk_size": 1024,
        "data_shards": 8,
        "parity_shards": 4,
    }

    commitment = encode_blob_for_da(
        blob,
        namespace=namespace,
        **params,
    )

    # Basic shape checks so we catch obvious wiring bugs.
    assert isinstance(commitment, dict), "commitment should be a dict-like payload"

    # Required keys for DA commitment.
    for key in ("namespace", "root", "params", "shards"):
        assert key in commitment, f"commitment missing required key {key!r}"

    assert commitment["namespace"] == namespace
    root = commitment["root"]
    shards = commitment["shards"]
    meta = commitment["params"]

    # Root should be bytes-like and reasonably sized (e.g., 32+ bytes).
    assert isinstance(root, (bytes, bytearray))
    assert len(root) >= 32

    # Shards should be a non-empty list of bytes-like slices.
    assert isinstance(shards, list)
    assert len(shards) > 0
    for i, s in enumerate(shards):
        assert isinstance(s, (bytes, bytearray)), f"shard {i} is not bytes-like"
        assert len(s) > 0, f"shard {i} is empty"

    # Params should at least round-trip the high-level DA parameters.
    for key, value in params.items():
        assert meta.get(key) == value, f"params[{key!r}] did not round-trip correctly"

    # Now reconstruct the blob from the commitment + shards.
    recovered = reconstruct_blob_from_da(commitment, shards)

    assert isinstance(recovered, (bytes, bytearray))
    assert bytes(recovered) == blob, "DA roundtrip must yield the exact original blob"
