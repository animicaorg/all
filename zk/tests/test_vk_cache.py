import json
from pathlib import Path
from typing import Dict, Any

import pytest

from zk.tests import configure_test_logging
from zk.integration.types import compute_vk_hash

# The registry package should expose helpers and default paths.
try:
    from zk.registry import load_vk_cache, get_vk_record, VK_CACHE_PATH  # type: ignore
except Exception:  # pragma: no cover - fallback if symbols differ
    load_vk_cache = None
    get_vk_record = None
    VK_CACHE_PATH = Path("zk/registry/vk_cache.json")

configure_test_logging()


def _load_cache_fallback(path: Path) -> Dict[str, Any]:
    """
    If zk.registry.load_vk_cache is unavailable, read JSON directly.
    """
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _have_cache() -> bool:
    return Path(VK_CACHE_PATH).exists()


@pytest.mark.skipif(not _have_cache(), reason="vk_cache.json not present")
def test_vk_cache_integrity_hash_matches():
    """
    Integrity: every record in vk_cache.json must contain a correct vk_hash
    equal to the computed hash over canonical {kind, vk_format, vk, fri_params}.
    """
    cache = (
        load_vk_cache()  # type: ignore[operator]
        if callable(load_vk_cache)
        else _load_cache_fallback(Path(VK_CACHE_PATH))
    )

    assert isinstance(cache, dict) and cache, "vk_cache must be a non-empty dict"

    for circuit_id, rec in cache.items():
        assert isinstance(rec, dict), f"record must be dict for {circuit_id}"
        for key in ("kind", "vk_format", "vk_hash"):
            assert key in rec, f"missing '{key}' in record {circuit_id}"

        kind = rec["kind"]
        vk_format = rec["vk_format"]
        vk = rec.get("vk")
        fri_params = rec.get("fri_params")

        # compute expected hash
        expected = compute_vk_hash(kind, vk_format, vk, fri_params)
        got = rec["vk_hash"]
        assert isinstance(got, str) and got.startswith("sha3-256:"), "vk_hash must be sha3-256:<hex>"
        assert expected == got, f"vk_hash mismatch for {circuit_id}: expected {expected}, got {got}"


@pytest.mark.skipif(not _have_cache(), reason="vk_cache.json not present")
def test_get_vk_record_by_exact_circuit_id():
    """
    Lookup: get_vk_record(exact_id) should return the same record as in cache.
    """
    cache = (
        load_vk_cache()  # type: ignore[operator]
        if callable(load_vk_cache)
        else _load_cache_fallback(Path(VK_CACHE_PATH))
    )
    circuit_id = next(iter(cache.keys()))
    rec = cache[circuit_id]

    if callable(get_vk_record):
        looked = get_vk_record(circuit_id)  # type: ignore[misc]
        assert looked is not None, f"get_vk_record returned None for {circuit_id}"
        # Compare a few stable fields
        assert looked.get("vk_hash") == rec.get("vk_hash")
        assert looked.get("kind") == rec.get("kind")
        assert looked.get("vk_format") == rec.get("vk_format")
    else:
        pytest.skip("get_vk_record not available; registry helper API not exported.")


@pytest.mark.skipif(not _have_cache(), reason="vk_cache.json not present")
def test_vk_cache_records_have_minimum_fields():
    """
    Schema sanity: each record should provide minimum metadata required by verifiers.
    """
    cache = (
        load_vk_cache()  # type: ignore[operator]
        if callable(load_vk_cache)
        else _load_cache_fallback(Path(VK_CACHE_PATH))
    )
    for cid, rec in cache.items():
        assert isinstance(rec.get("kind"), str) and rec["kind"], f"{cid}: empty kind"
        assert rec.get("vk_format") in {"snarkjs", "plonkjs", "fri"}, f"{cid}: unknown vk_format"
        assert isinstance(rec.get("vk_hash"), str) and rec["vk_hash"].startswith("sha3-256:")
        # VK bytes must exist unless the runtime only uses vk_ref (not typical for tests)
        assert "vk" in rec and isinstance(rec["vk"], (dict, list)), f"{cid}: missing or invalid vk payload"
