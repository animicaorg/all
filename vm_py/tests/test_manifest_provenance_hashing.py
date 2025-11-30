from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from vm_py.runtime.manifest_provenance import (
    compute_manifest_hash_for_provenance, is_provenance_hash_valid)


def _base_manifest() -> Dict[str, Any]:
    """
    Minimal-but-nontrivial manifest used for provenance hashing tests.
    """
    return {
        "name": "Counter",
        "version": "1.0.0",
        "language": "python-vm",
        "entry": "contract.py",
        "abi": {
            "functions": [
                {
                    "name": "inc",
                    "inputs": [],
                    "outputs": [],
                },
                {
                    "name": "get",
                    "inputs": [],
                    "outputs": [
                        {"name": "value", "type": "int"},
                    ],
                },
            ],
            "events": [],
            "errors": [],
        },
        "resources": {
            "caps": [],
            "limits": {
                "max_blob_bytes": 0,
            },
        },
    }


def test_manifest_hash_is_deterministic_under_key_reordering() -> None:
    """
    Reordering top-level keys must not change the provenance hash.
    """
    m1 = _base_manifest()
    # Reverse top-level key order to simulate a different JSON layout.
    m2 = dict(reversed(list(m1.items())))

    h1 = compute_manifest_hash_for_provenance(m1)
    h2 = compute_manifest_hash_for_provenance(m2)

    # Basic shape: 0x + 64 hex chars for sha3_256.
    assert h1.startswith("0x") and len(h1) == 66
    assert h2.startswith("0x") and len(h2) == 66

    # Deterministic with respect to key ordering.
    assert h1 == h2


def test_is_provenance_hash_valid_true_when_hash_matches_normalized_manifest() -> None:
    """
    When provenance.hash matches the recomputed normalized manifest hash,
    is_provenance_hash_valid() should return True.
    """
    m = _base_manifest()
    h = compute_manifest_hash_for_provenance(m)

    m_with_prov = deepcopy(m)
    m_with_prov["provenance"] = {
        "hashAlgo": "sha3_256",
        "hash": h,
        "signatures": [
            {
                "alg": "dilithium3",
                "pubkey": "0x" + ("00" * 48),
                "sig": "0x" + ("11" * 96),
            }
        ],
    }

    assert is_provenance_hash_valid(m_with_prov)


def test_is_provenance_hash_valid_false_when_hash_does_not_match() -> None:
    """
    If the manifest is mutated after the provenance.hash was recorded,
    validation should fail.
    """
    m = _base_manifest()
    stale_hash = compute_manifest_hash_for_provenance(m)

    # Change a field so the normalized JSON changes.
    m_bad = deepcopy(m)
    m_bad["description"] = "This mutation should change the hash."

    m_bad["provenance"] = {
        "hashAlgo": "sha3_256",
        "hash": stale_hash,
        "signatures": [],
    }

    assert not is_provenance_hash_valid(m_bad)
