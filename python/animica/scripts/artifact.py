from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.encoding.cbor import cbor_dumps
from core.utils.hash import sha3_256

SCRIPT_ARTIFACT_FORMAT = "animica-script-artifact-v1"
DEFAULT_ARTIFACT_FILENAME = "script_artifact.json"


@dataclass(frozen=True)
class ScriptSource:
    path: str
    content: bytes


@dataclass(frozen=True)
class ScriptArtifact:
    manifest: Dict[str, Any]
    sources: Tuple[ScriptSource, ...]
    compiled: bytes
    vm_version: str
    abi_version: str

    def canonical_payload(self) -> Dict[str, Any]:
        return _canonical_payload(
            manifest=self.manifest,
            sources=self.sources,
            compiled=self.compiled,
            vm_version=self.vm_version,
            abi_version=self.abi_version,
        )

    def canonical_cbor(self) -> bytes:
        return cbor_dumps(self.canonical_payload())

    def artifact_hash(self) -> bytes:
        return sha3_256(self.canonical_cbor())

    def artifact_hash_hex(self) -> str:
        return "0x" + self.artifact_hash().hex()

    def to_container(self) -> Dict[str, Any]:
        return build_container(
            manifest=self.manifest,
            sources=self.sources,
            compiled=self.compiled,
            vm_version=self.vm_version,
            abi_version=self.abi_version,
        )


@dataclass(frozen=True)
class ScriptVector:
    script_hash: str
    inputs_cbor_hex: str
    outputs_cbor_hex: str
    outputs_commit_hex: str


class ScriptArtifactError(ValueError):
    pass


def _canonical_payload(
    *,
    manifest: Mapping[str, Any],
    sources: Sequence[ScriptSource],
    compiled: bytes,
    vm_version: str,
    abi_version: str,
) -> Dict[str, Any]:
    return {
        "format": SCRIPT_ARTIFACT_FORMAT,
        "manifest": normalize_manifest(manifest),
        "sources": [
            {"path": src.path, "bytes": bytes(src.content)}
            for src in _sorted_sources(sources)
        ],
        "compiled": bytes(compiled),
        "vmVersion": str(vm_version),
        "abiVersion": str(abi_version),
    }


def _sorted_sources(sources: Sequence[ScriptSource]) -> List[ScriptSource]:
    return sorted(sources, key=lambda s: s.path)


def normalize_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ScriptArtifactError("manifest must be an object")
    out = dict(manifest)
    name = str(out.get("name") or "").strip()
    version = str(out.get("version") or "").strip()
    if not name:
        raise ScriptArtifactError("manifest.name is required")
    if not version:
        raise ScriptArtifactError("manifest.version is required")
    entrypoints = out.get("entrypoints")
    if entrypoints is None:
        entrypoints = out.get("exports")
    if entrypoints is None:
        raise ScriptArtifactError("manifest.entrypoints is required")
    if not isinstance(entrypoints, list) or not all(
        isinstance(e, str) and e for e in entrypoints
    ):
        raise ScriptArtifactError("manifest.entrypoints must be a list of strings")
    out["entrypoints"] = list(entrypoints)
    return out


def build_artifact(
    *,
    manifest: Mapping[str, Any],
    sources: Sequence[ScriptSource],
    compiled: bytes,
    vm_version: str,
    abi_version: str,
) -> ScriptArtifact:
    if not sources:
        raise ScriptArtifactError("at least one source is required")
    if not isinstance(compiled, (bytes, bytearray)):
        raise ScriptArtifactError("compiled must be bytes")
    normalized = normalize_manifest(manifest)
    return ScriptArtifact(
        manifest=normalized,
        sources=tuple(_sorted_sources(sources)),
        compiled=bytes(compiled),
        vm_version=str(vm_version),
        abi_version=str(abi_version),
    )


def build_container(
    *,
    manifest: Mapping[str, Any],
    sources: Sequence[ScriptSource],
    compiled: bytes,
    vm_version: str,
    abi_version: str,
) -> Dict[str, Any]:
    artifact = build_artifact(
        manifest=manifest,
        sources=sources,
        compiled=compiled,
        vm_version=vm_version,
        abi_version=abi_version,
    )
    return {
        "format": SCRIPT_ARTIFACT_FORMAT,
        "manifest": artifact.manifest,
        "sources": [
            {
                "path": src.path,
                "content_b64": base64.b64encode(src.content).decode("ascii"),
            }
            for src in artifact.sources
        ],
        "compiled_b64": base64.b64encode(artifact.compiled).decode("ascii"),
        "vm_version": artifact.vm_version,
        "abi_version": artifact.abi_version,
        "artifact_hash": artifact.artifact_hash_hex(),
    }


def parse_container(data: Mapping[str, Any]) -> ScriptArtifact:
    if not isinstance(data, Mapping):
        raise ScriptArtifactError("artifact container must be an object")
    fmt = data.get("format")
    if fmt != SCRIPT_ARTIFACT_FORMAT:
        raise ScriptArtifactError(f"unsupported artifact format: {fmt}")
    manifest = data.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ScriptArtifactError("artifact manifest missing or invalid")
    sources_raw = data.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise ScriptArtifactError("artifact sources missing")
    sources: List[ScriptSource] = []
    for item in sources_raw:
        if not isinstance(item, Mapping):
            raise ScriptArtifactError("artifact source entry invalid")
        path = str(item.get("path") or "").strip()
        if not path:
            raise ScriptArtifactError("artifact source path missing")
        b64 = item.get("content_b64")
        if not isinstance(b64, str):
            raise ScriptArtifactError(f"artifact source {path} missing content_b64")
        try:
            content = base64.b64decode(b64)
        except Exception as exc:  # pragma: no cover - base64 failure
            raise ScriptArtifactError(
                f"artifact source {path} has invalid base64"
            ) from exc
        sources.append(ScriptSource(path=path, content=content))
    compiled_b64 = data.get("compiled_b64")
    if not isinstance(compiled_b64, str):
        raise ScriptArtifactError("artifact compiled_b64 missing")
    try:
        compiled = base64.b64decode(compiled_b64)
    except Exception as exc:  # pragma: no cover
        raise ScriptArtifactError("artifact compiled_b64 invalid") from exc
    vm_version = str(data.get("vm_version") or "")
    abi_version = str(data.get("abi_version") or "")
    artifact = build_artifact(
        manifest=manifest,
        sources=sources,
        compiled=compiled,
        vm_version=vm_version,
        abi_version=abi_version,
    )
    return artifact


def load_container(path: Path) -> ScriptArtifact:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return parse_container(raw)


def write_container(path: Path, artifact: ScriptArtifact) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_container(), indent=2) + "\n", encoding="utf-8")


def load_vector(path: Path) -> ScriptVector:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ScriptArtifactError("vector must be a JSON object")
    return ScriptVector(
        script_hash=str(raw.get("script_hash") or ""),
        inputs_cbor_hex=str(raw.get("inputs_cbor_hex") or ""),
        outputs_cbor_hex=str(raw.get("outputs_cbor_hex") or ""),
        outputs_commit_hex=str(raw.get("outputs_commit_hex") or ""),
    )


def normalize_hex(value: str) -> str:
    v = value.strip().lower()
    if not v:
        return ""
    return v if v.startswith("0x") else f"0x{v}"


def compute_commitment(cbor_bytes: bytes) -> str:
    return "0x" + sha3_256(cbor_bytes).hex()


def ensure_script_store(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o775)
    except (OSError, PermissionError):
        pass
    return root
