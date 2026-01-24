"""Build pipeline for Animica Python-VM contracts inside the IDE."""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from animica_miner_gui.ide.toolchain.diagnostics import Diagnostic, parse_diagnostics
from animica_miner_gui.ide.toolchain.manifest import (
    ManifestLoadError,
    default_manifest,
    load_manifest,
    resolve_abi,
    resolve_manifest_path,
    resolve_source_path,
)
from animica_miner_gui.ide.toolchain.utils import (
    atomic_write_bytes,
    atomic_write_text,
    canonical_json_str,
    load_vm_py,
    sha3_256_hex,
)


@dataclass(frozen=True)
class BuildArtifacts:
    build_dir: Path
    manifest_path: Path
    abi_path: Path
    contract_path: Path
    sources_path: Path
    code_hash: str


@dataclass(frozen=True)
class BuildResult:
    success: bool
    message: str
    artifacts: Optional[BuildArtifacts] = None
    diagnostics: Optional[List[Diagnostic]] = None
    manifest: Optional[Dict[str, Any]] = None


def build_contract(workspace: Path) -> BuildResult:
    diagnostics: List[Diagnostic] = []
    if not workspace or not workspace.exists():
        return BuildResult(
            success=False,
            message="Workspace path is invalid.",
            diagnostics=[Diagnostic(message="Workspace path is invalid")],
        )

    os.environ.setdefault("ANIMICA_STRICT_VM", "1")
    if sys.version_info < (3, 11):
        diagnostics.append(
            Diagnostic(
                message="Python 3.11+ recommended for deterministic VM builds.",
                severity="warning",
            )
        )

    manifest_path = resolve_manifest_path(workspace)
    if not manifest_path:
        return BuildResult(
            success=False,
            message="No manifest.json found in workspace.",
            diagnostics=[Diagnostic(message="manifest.json not found")],
        )

    try:
        manifest = load_manifest(manifest_path)
        source_path = resolve_source_path(manifest, manifest_path)
        abi = resolve_abi(manifest, manifest_path)
    except ManifestLoadError as exc:
        diagnostics.append(Diagnostic(message=str(exc)))
        return BuildResult(
            success=False,
            message=str(exc),
            diagnostics=diagnostics,
        )

    if not abi:
        diagnostics.append(Diagnostic(message="Manifest missing ABI definition."))
        return BuildResult(
            success=False,
            message="Manifest missing ABI definition.",
            diagnostics=diagnostics,
        )

    try:
        vm_py = load_vm_py()
    except Exception as exc:
        diagnostics.append(Diagnostic(message=f"vm_py unavailable: {exc}"))
        return BuildResult(
            success=False,
            message="vm_py module not available.",
            diagnostics=diagnostics,
        )
    if vm_py is None:
        diagnostics.append(Diagnostic(message="vm_py module not available."))
        return BuildResult(
            success=False,
            message="vm_py module not available.",
            diagnostics=diagnostics,
        )

    try:
        source_text = source_path.read_text(encoding="utf-8")
    except Exception as exc:
        diagnostics.append(Diagnostic(message=f"Failed to read source: {exc}"))
        return BuildResult(
            success=False,
            message="Failed to read contract source.",
            diagnostics=diagnostics,
        )

    try:
        ir_bytes = vm_py.compile_source(source_text)
    except Exception as exc:  # pragma: no cover - depends on VM
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        diagnostics.extend(parse_diagnostics(trace, workspace))
        diagnostics.append(Diagnostic(message=str(exc)))
        return BuildResult(
            success=False,
            message="Compilation failed.",
            diagnostics=diagnostics,
        )

    code_hash = sha3_256_hex(ir_bytes)
    vm_version = getattr(vm_py, "version", lambda: "0.0.0")()

    name = str(manifest.get("name") or source_path.stem)
    version = str(manifest.get("version") or "0.1.0")
    description = manifest.get("description")
    authors = manifest.get("authors") or manifest.get("metadata", {}).get("authors")
    license_name = manifest.get("license") or manifest.get("metadata", {}).get("license")

    manifest_out = default_manifest(
        name=name,
        version=version,
        abi=abi,
        vm_version=str(vm_version),
        code_hash=code_hash,
        code_size=len(ir_bytes),
        entry=source_path.name,
        description=description,
        authors=authors if isinstance(authors, list) else None,
        license_name=license_name,
    )

    build_dir = workspace / ".animica_build"
    abi_path = build_dir / "abi.json"
    manifest_out_path = build_dir / "manifest.json"
    contract_path = build_dir / "contract.bin"
    sources_path = build_dir / "sources.json"

    source_descriptor = {
        "path": str(source_path.relative_to(workspace))
        if source_path.is_relative_to(workspace)
        else str(source_path),
        "hash": sha3_256_hex(source_text),
        "size": len(source_text.encode("utf-8")),
    }
    sources_payload = {
        "entry": source_descriptor["path"],
        "sources": [source_descriptor],
    }

    atomic_write_bytes(contract_path, ir_bytes)
    atomic_write_text(abi_path, canonical_json_str(abi))
    atomic_write_text(manifest_out_path, canonical_json_str(manifest_out))
    atomic_write_text(sources_path, canonical_json_str(sources_payload))

    artifacts = BuildArtifacts(
        build_dir=build_dir,
        manifest_path=manifest_out_path,
        abi_path=abi_path,
        contract_path=contract_path,
        sources_path=sources_path,
        code_hash=code_hash,
    )

    message = f"Built {name}@{version} → {build_dir}"
    return BuildResult(
        success=True,
        message=message,
        artifacts=artifacts,
        diagnostics=diagnostics,
        manifest=manifest_out,
    )
