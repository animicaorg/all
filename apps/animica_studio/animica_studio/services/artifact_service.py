from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactService:
    def hash_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def hash_file(self, path: Path) -> str:
        return self.hash_bytes(path.read_bytes())

    def build_manifest(self, files: list[Path], metadata: dict[str, Any]) -> dict[str, Any]:
        rows = []
        for file_path in sorted(files, key=lambda p: p.name):
            rows.append(
                {
                    "name": file_path.name,
                    "size": file_path.stat().st_size,
                    "sha256": self.hash_file(file_path),
                }
            )
        manifest = {
            "schema": "animica.ena.artifact.v1",
            "metadata": metadata,
            "files": rows,
        }
        manifest["manifest_sha256"] = self.hash_manifest(manifest)
        return manifest

    def hash_manifest(self, manifest: dict[str, Any]) -> str:
        clean = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        payload = json.dumps(clean, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def verify_manifest(self, manifest: dict[str, Any], base_dir: Path) -> tuple[bool, str]:
        expected = manifest.get("manifest_sha256")
        actual = self.hash_manifest(manifest)
        if expected and expected != actual:
            return False, f"manifest hash mismatch: expected {expected}, got {actual}"
        for row in manifest.get("files", []):
            fp = base_dir / str(row.get("name", ""))
            if not fp.exists():
                return False, f"missing file: {fp.name}"
            got = self.hash_file(fp)
            if got != row.get("sha256"):
                return False, f"file hash mismatch for {fp.name}: expected {row.get('sha256')}, got {got}"
        return True, "verification passed"

    def rebuild_manifest(self, manifest: dict[str, Any], base_dir: Path) -> dict[str, Any]:
        files = [base_dir / str(row["name"]) for row in manifest.get("files", [])]
        return self.build_manifest(files, manifest.get("metadata", {}))
