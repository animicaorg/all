from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from vm_py.runtime.manifest_provenance import (
    compute_manifest_hash_for_provenance,
    is_provenance_hash_valid,
)


def _load_manifest() -> Path:
    root = Path(__file__).resolve().parents[3]
    manifest = root / "tests" / "fixtures" / "contracts" / "counter" / "manifest.json"
    if not manifest.exists():
        pytest.skip("counter manifest fixture missing")
    return manifest


def _with_provenance(manifest: dict) -> dict:
    manifest = deepcopy(manifest)
    manifest_hash = compute_manifest_hash_for_provenance(manifest)
    manifest["provenance"] = {"hashAlgo": "sha3_256", "hash": manifest_hash, "signatures": []}
    return manifest


def test_manifest_roundtrip_preserves_hash_and_fields(tmp_path: Path) -> None:
    manifest_path = _load_manifest()
    manifest_in = json.loads(manifest_path.read_text())

    enriched = _with_provenance(manifest_in)
    roundtrip_path = tmp_path / "manifest.json"
    roundtrip_path.write_text(json.dumps(enriched, sort_keys=True, ensure_ascii=False))

    manifest_out = json.loads(roundtrip_path.read_text())
    assert is_provenance_hash_valid(manifest_out)
    assert set(manifest_out.keys()) == set(enriched.keys())
    assert manifest_out["metadata"] == manifest_in["metadata"]


def test_manifest_edit_updates_hash_but_remains_canonical(tmp_path: Path) -> None:
    manifest_path = _load_manifest()
    manifest_in = json.loads(manifest_path.read_text())

    edited = deepcopy(manifest_in)
    edited["metadata"]["description"] = "Edited description"

    enriched_before = _with_provenance(manifest_in)
    enriched_after = _with_provenance(edited)

    assert enriched_before["provenance"]["hash"] != enriched_after["provenance"]["hash"]

    out_path = tmp_path / "manifest_edited.json"
    out_path.write_text(json.dumps(enriched_after, sort_keys=True, ensure_ascii=False))
    manifest_out = json.loads(out_path.read_text())

    assert is_provenance_hash_valid(manifest_out)
    assert manifest_out["metadata"]["description"] == "Edited description"
