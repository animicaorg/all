from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_checkpoint_package(package_dir: str, payloads: dict[str, bytes], metadata: dict[str, Any]) -> str:
    root = Path(package_dir)
    blobs = root / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    for name, data in payloads.items():
        digest = _sha256(data)
        rel = f"blobs/{digest}.blob"
        (root / rel).write_bytes(data)
        entries.append({"name": name, "path": rel, "sha256": digest})
    manifest = {"kind": "ena-mm-package", "modality_flags": metadata.get("modality_flags", {}), "metadata": metadata, "blobs": entries}
    (root / "package_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(root / "package_manifest.json")


def read_checkpoint_package(package_dir: str) -> dict[str, Any]:
    root = Path(package_dir)
    manifest = json.loads((root / "package_manifest.json").read_text(encoding="utf-8"))
    for b in manifest.get("blobs", []):
        data = (root / str(b["path"])).read_bytes()
        if _sha256(data) != str(b.get("sha256")):
            raise ValueError(f"blob sha mismatch: {b.get('name')}")
    return manifest
