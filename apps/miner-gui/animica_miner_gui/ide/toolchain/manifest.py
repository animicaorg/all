"""Manifest loading and validation utilities for the IDE toolchain."""

from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from animica_miner_gui.ide.toolchain.utils import canonical_json_bytes, sha3_256_hex


@dataclass(frozen=True)
class ManifestValidationIssue:
    message: str
    path: str = ""
    severity: str = "error"


class ManifestLoadError(RuntimeError):
    pass


def resolve_manifest_path(workspace: Path) -> Optional[Path]:
    manifest = workspace / "manifest.json"
    if manifest.is_file():
        return manifest
    matches = sorted(workspace.rglob("manifest.json"))
    if matches:
        return matches[0]
    return None


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestLoadError(f"Failed to parse manifest: {exc}") from exc


def resolve_source_path(manifest: Dict[str, Any], manifest_path: Path) -> Path:
    candidates: List[str] = []
    for key in ("source", "entry"):
        value = manifest.get(key)
        if isinstance(value, str):
            candidates.append(value)
    code_entry = manifest.get("code", {}).get("entry")
    if isinstance(code_entry, str):
        candidates.append(code_entry)
    if not candidates:
        candidates.append("contract.py")
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            path = manifest_path.parent / path
        if path.is_file():
            return path
    raise ManifestLoadError("Manifest does not reference a valid source file.")


def resolve_abi(manifest: Dict[str, Any], manifest_path: Path) -> Any:
    abi = manifest.get("abi")
    if isinstance(abi, str):
        abi_path = Path(abi)
        if not abi_path.is_absolute():
            abi_path = manifest_path.parent / abi_path
        return json.loads(abi_path.read_text(encoding="utf-8"))
    return abi


def default_manifest(
    name: str,
    version: str,
    abi: Any,
    vm_version: str,
    code_hash: str,
    code_size: int,
    entry: str,
    description: Optional[str] = None,
    authors: Optional[List[str]] = None,
    license_name: Optional[str] = None,
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "manifestVersion": 1,
        "name": name,
        "version": version,
        "language": "python",
        "vm": {
            "name": "vm_py",
            "version": vm_version,
        },
        "abi": abi,
        "code": {
            "kind": "vm_py/ir",
            "hash": code_hash,
            "alg": "sha3_256",
            "size": code_size,
            "entry": entry,
        },
    }
    if description:
        manifest["description"] = description
    if authors:
        manifest["authors"] = authors
    if license_name:
        manifest["license"] = license_name
    return manifest


def compute_source_descriptor(source_text: str, entry: str) -> Dict[str, Any]:
    return {
        "kind": "vm_py/source",
        "hash": sha3_256_hex(source_text.encode("utf-8")),
        "alg": "sha3_256",
        "size": len(source_text.encode("utf-8")),
        "entry": entry,
    }


def validate_manifest(
    manifest: Dict[str, Any],
    abi: Optional[Any] = None,
) -> List[ManifestValidationIssue]:
    issues: List[ManifestValidationIssue] = []
    schema = _load_schema("manifest.schema.json")
    issues.extend(_validate_with_schema(manifest, schema))
    if abi is not None:
        abi_schema = _load_schema("abi.schema.json")
        issues.extend(_validate_with_schema(abi, abi_schema, prefix="abi"))
    return issues


def _load_schema(filename: str) -> Dict[str, Any]:
    local_path = Path(__file__).resolve().parent / "schemas" / filename
    if local_path.is_file():
        return json.loads(local_path.read_text(encoding="utf-8"))
    raise ManifestLoadError(f"Schema file missing: {filename}")


def _validate_with_schema(
    data: Any,
    schema: Dict[str, Any],
    prefix: str = "",
) -> List[ManifestValidationIssue]:
    if importlib.util.find_spec("jsonschema") is None:
        return [
            ManifestValidationIssue(
                message="jsonschema not installed; skipping strict validation",
                path=prefix,
                severity="warning",
            )
        ]
    jsonschema = importlib.import_module("jsonschema")
    validator = jsonschema.Draft202012Validator(schema)
    issues: List[ManifestValidationIssue] = []
    for error in sorted(validator.iter_errors(data), key=lambda e: e.path):
        path = ".".join(str(p) for p in error.path)
        if prefix and path:
            path = f"{prefix}.{path}"
        elif prefix:
            path = prefix
        issues.append(
            ManifestValidationIssue(
                message=error.message,
                path=path,
            )
        )
    if not issues:
        return [ManifestValidationIssue(message="Schema OK", severity="info")]
    return issues


def manifest_fingerprint(manifest: Dict[str, Any]) -> str:
    return sha3_256_hex(canonical_json_bytes(manifest))
