from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from animica.cli.paths import ensure_dir, ensure_file_dir

ARTIFACT_INDEX_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def contracts_home() -> Path:
    env_home = os.environ.get("ANIMICA_HOME")
    base = Path(env_home).expanduser() if env_home else Path.home() / ".animica"
    return ensure_dir(base / "contracts", mode=0o755)


def artifacts_root() -> Path:
    return ensure_dir(contracts_home() / "artifacts", mode=0o755)


def deployments_root() -> Path:
    return ensure_dir(contracts_home() / "deployments", mode=0o755)


def _index_path() -> Path:
    return artifacts_root() / "index.json"


def _load_index() -> dict[str, Any]:
    path = _index_path()
    if not path.exists():
        return {"version": ARTIFACT_INDEX_VERSION, "artifacts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": ARTIFACT_INDEX_VERSION, "artifacts": []}
    if not isinstance(data, dict):
        return {"version": ARTIFACT_INDEX_VERSION, "artifacts": []}
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        data["artifacts"] = []
    data.setdefault("version", ARTIFACT_INDEX_VERSION)
    return data


def _save_index(index: dict[str, Any]) -> None:
    path = _index_path()
    ensure_file_dir(path)
    path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def sha3_256_hex(data: bytes) -> str:
    return "0x" + hashlib.sha3_256(data).hexdigest()


def default_artifact_path(contract_name: str, code_hash: str) -> Path:
    short_hash = code_hash[2:10] if code_hash.startswith("0x") else code_hash[:8]
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in contract_name)
    return artifacts_root() / f"{safe_name}-{short_hash}.avm"


def default_abi_path(contract_name: str, code_hash: str) -> Path:
    short_hash = code_hash[2:10] if code_hash.startswith("0x") else code_hash[:8]
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in contract_name)
    return artifacts_root() / f"{safe_name}-{short_hash}.abi.json"


def default_manifest_path(contract_name: str, code_hash: str) -> Path:
    short_hash = code_hash[2:10] if code_hash.startswith("0x") else code_hash[:8]
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in contract_name)
    return artifacts_root() / f"{safe_name}-{short_hash}.manifest.json"


def resolve_manifest_for_source(source_path: Path) -> Optional[Path]:
    candidates = [
        source_path.parent / "manifest.json",
        source_path.with_suffix(".manifest.json"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _safe_json_load(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def detect_contract_name(source_path: Path, manifest_path: Optional[Path] = None) -> str:
    if manifest_path is not None:
        manifest = _safe_json_load(manifest_path)
        if isinstance(manifest, dict):
            name = manifest.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return source_path.stem


def load_abi_from_manifest(manifest_path: Optional[Path]) -> Optional[Any]:
    if manifest_path is None:
        return None
    manifest = _safe_json_load(manifest_path)
    if not isinstance(manifest, dict):
        return None

    abi_obj = manifest.get("abi")
    if isinstance(abi_obj, (list, dict)):
        return abi_obj

    abi_path_value = manifest.get("abiPath") or manifest.get("abi_path")
    if isinstance(abi_path_value, str) and abi_path_value.strip():
        abi_path = (manifest_path.parent / abi_path_value).resolve()
        try:
            data = json.loads(abi_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(data, dict) and "abi" in data:
            return data.get("abi")
        return data

    return None


def resolve_abi_path_from_manifest(manifest_path: Optional[Path]) -> Optional[Path]:
    if manifest_path is None:
        return None
    manifest = _safe_json_load(manifest_path)
    if not isinstance(manifest, dict):
        return None
    abi_path_value = manifest.get("abiPath") or manifest.get("abi_path")
    if not isinstance(abi_path_value, str) or not abi_path_value.strip():
        return None
    abi_path = (manifest_path.parent / abi_path_value).resolve()
    if abi_path.exists() and abi_path.is_file():
        return abi_path
    return None


def generate_abi_from_source(source_path: Path) -> Optional[Any]:
    try:
        from contracts.tools.abi_gen import extract_abi_from_source

        return extract_abi_from_source(source_path)
    except Exception:
        return None


def compile_source_to_ir_bytes(
    source_path: Path,
    *,
    entrypoint: Optional[str] = None,
    optimize: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    source_text = source_path.read_text(encoding="utf-8")

    # Preferred in-process path.
    try:
        from vm_py.cli.compile import compile_source_to_ir

        ir_bytes, metadata = compile_source_to_ir(source_text, filename=str(source_path))
        if not isinstance(metadata, dict):
            metadata = {"compiler_meta": str(metadata)}
        metadata = dict(metadata)
        metadata.setdefault("optimize", bool(optimize))
        if entrypoint:
            metadata.setdefault("entrypoint", entrypoint)
        return bytes(ir_bytes), metadata
    except Exception as inproc_exc:
        # Fallback to subprocess compile.
        with tempfile.NamedTemporaryFile(suffix=".ir", delete=False) as tf:
            tmp_out = Path(tf.name)
        try:
            cmd = [
                sys.executable,
                "-m",
                "vm_py.cli.compile",
                str(source_path),
                "--out",
                str(tmp_out),
                "--quiet",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()
                raise RuntimeError(
                    "contract compilation failed: "
                    f"{stderr or 'unknown error'} (in-process error: {inproc_exc})"
                )
            ir_bytes = tmp_out.read_bytes()
            return ir_bytes, {
                "compiler": "vm_py.cli.compile",
                "optimize": bool(optimize),
                "entrypoint": entrypoint,
                "fallback": "subprocess",
            }
        finally:
            try:
                tmp_out.unlink(missing_ok=True)
            except Exception:
                pass


def write_artifact_bytes(path: Path, data: bytes, *, overwrite: bool) -> Path:
    out = path.expanduser().resolve()
    if out.exists() and not overwrite:
        raise FileExistsError(f"artifact already exists: {out} (use --overwrite)")
    ensure_file_dir(out)
    out.write_bytes(data)
    return out


def write_json_file(path: Path, payload: Any, *, overwrite: bool) -> Path:
    out = path.expanduser().resolve()
    if out.exists() and not overwrite:
        raise FileExistsError(f"file already exists: {out} (use --overwrite)")
    ensure_file_dir(out)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return out


def infer_artifact_metadata(
    artifact_path: Path,
    *,
    source_path: Optional[Path] = None,
    code_hash: Optional[str] = None,
    contract_name: Optional[str] = None,
    abi_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    compiler_meta: Optional[dict[str, Any]] = None,
    optimize: bool = True,
    project_root: Optional[Path] = None,
    entrypoint: Optional[str] = None,
) -> dict[str, Any]:
    resolved_artifact = artifact_path.expanduser().resolve()
    record = {
        "name": contract_name or resolved_artifact.stem,
        "artifact_path": str(resolved_artifact),
        "source_path": str(source_path.expanduser().resolve()) if source_path else None,
        "abi_path": str(abi_path.expanduser().resolve()) if abi_path else None,
        "manifest_path": str(manifest_path.expanduser().resolve()) if manifest_path else None,
        "code_hash": code_hash,
        "created_at": _utc_now_iso(),
        "optimize": bool(optimize),
        "project_root": str(project_root.expanduser().resolve()) if project_root else None,
        "entrypoint": entrypoint,
        "compiler_meta": compiler_meta or {},
    }
    return record


def save_artifact_record(record: dict[str, Any]) -> None:
    index = _load_index()
    items = index.get("artifacts")
    if not isinstance(items, list):
        items = []
        index["artifacts"] = items

    artifact_path = str(record.get("artifact_path") or "")
    code_hash = str(record.get("code_hash") or "")

    replaced = False
    for idx, existing in enumerate(items):
        if not isinstance(existing, dict):
            continue
        if artifact_path and str(existing.get("artifact_path") or "") == artifact_path:
            items[idx] = record
            replaced = True
            break
        if code_hash and str(existing.get("code_hash") or "") == code_hash:
            items[idx] = record
            replaced = True
            break

    if not replaced:
        items.append(record)

    _save_index(index)


def list_saved_artifacts() -> list[dict[str, Any]]:
    index = _load_index()
    items = index.get("artifacts")
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
    normalized.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return normalized


def find_artifact_by_path(path: Path) -> Optional[dict[str, Any]]:
    resolved = str(path.expanduser().resolve())
    for item in list_saved_artifacts():
        if str(item.get("artifact_path") or "") == resolved:
            return item
    return None


def find_artifact_by_source(path: Path) -> Optional[dict[str, Any]]:
    resolved = str(path.expanduser().resolve())
    for item in list_saved_artifacts():
        if str(item.get("source_path") or "") == resolved:
            return item
    return None


def infer_saved_abi_path_for_source(source_path: Path) -> Optional[Path]:
    saved = find_artifact_by_source(source_path)
    if not isinstance(saved, dict):
        return None
    raw = saved.get("abi_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser().resolve()
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def infer_fixture_abi_path_for_source(source_path: Path) -> Optional[Path]:
    source = source_path.expanduser().resolve()
    search_start = source.parent if source.is_file() else source
    fixture_dir: Optional[Path] = None
    for parent in [search_start, *search_start.parents]:
        candidate = parent / "tests" / "fixtures" / "abi"
        if candidate.exists() and candidate.is_dir():
            fixture_dir = candidate
            break
    if fixture_dir is None:
        return None

    names: list[str] = []
    for value in (source.stem, source.parent.name, source.parent.parent.name):
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    for name in names:
        candidate = (fixture_dir / f"{name}.json").resolve()
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def infer_abi_path_for_source(source_path: Path, manifest_path: Optional[Path]) -> Optional[Path]:
    for candidate in (
        resolve_abi_path_from_manifest(manifest_path),
        infer_saved_abi_path_for_source(source_path),
        infer_fixture_abi_path_for_source(source_path),
    ):
        if candidate is not None:
            return candidate
    return None


def load_artifact_file(path: Path) -> tuple[bytes, Optional[dict[str, Any]]]:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() == ".json":
        data = _safe_json_load(resolved)
        if not isinstance(data, dict):
            raise ValueError(f"artifact JSON must be an object: {resolved}")
        code_hex = data.get("code_hex") or data.get("ir_hex")
        if isinstance(code_hex, str) and code_hex:
            raw_hex = code_hex[2:] if code_hex.startswith("0x") else code_hex
            return bytes.fromhex(raw_hex), data
        code_b64 = data.get("code_b64") or data.get("ir_b64")
        if isinstance(code_b64, str) and code_b64:
            import base64

            return base64.b64decode(code_b64), data
        raise ValueError(
            f"artifact JSON is missing code_hex/ir_hex or code_b64/ir_b64: {resolved}"
        )
    return resolved.read_bytes(), None


def discover_sidecar_abi(path: Path) -> Optional[Path]:
    base = path.with_suffix("")
    candidates = [
        path.with_suffix(".abi.json"),
        base.with_suffix(".abi.json"),
        path.parent / f"{path.stem}.abi.json",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def discover_sidecar_manifest(path: Path) -> Optional[Path]:
    base = path.with_suffix("")
    candidates = [
        path.with_suffix(".manifest.json"),
        base.with_suffix(".manifest.json"),
        path.parent / f"{path.stem}.manifest.json",
        path.parent / "manifest.json",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None
