from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(shards: list[Path], out_path: Path, provenance: dict[str, str]) -> dict:
    payload = {
        "version": 1,
        "provenance": provenance,
        "shards": [
            {"path": p.name, "sha256": sha256_file(p), "size": p.stat().st_size}
            for p in shards
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
