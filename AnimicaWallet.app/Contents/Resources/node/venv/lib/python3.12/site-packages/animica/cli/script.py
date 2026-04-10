"""Script artifact management CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from animica.scripts import (
    ScriptArtifact,
    ScriptArtifactError,
    ScriptSource,
    ScriptVector,
    build_artifact,
    compute_commitment,
    ensure_script_store,
    load_container,
    load_vector,
    normalize_hex,
)

app = typer.Typer(help="Manage deterministic script artifacts.")

PINNED_FILENAME = "pinned.json"


def _scripts_root() -> Path:
    root = os.environ.get("ANIMICA_SCRIPTS_DIR")
    if root:
        return ensure_script_store(Path(root).expanduser())
    return ensure_script_store(Path.home() / ".animica" / "scripts")


def _pinned_path(root: Path) -> Path:
    return root / PINNED_FILENAME


def _read_pins(root: Path) -> List[str]:
    path = _pinned_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def _write_pins(root: Path, pins: List[str]) -> None:
    path = _pinned_path(root)
    path.write_text(json.dumps(sorted(set(pins)), indent=2) + "\n", encoding="utf-8")


def _artifact_dir(root: Path, artifact_hash: str) -> Path:
    return root / normalize_hex(artifact_hash).removeprefix("0x")


def _write_artifact_dir(root: Path, artifact: ScriptArtifact) -> Path:
    artifact_hash = artifact.artifact_hash_hex()
    dest = _artifact_dir(root, artifact_hash)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "manifest.json").write_text(
        json.dumps(artifact.manifest, indent=2) + "\n", encoding="utf-8"
    )
    (dest / "compiled.bin").write_bytes(artifact.compiled)
    for src in artifact.sources:
        out_path = dest / src.path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(src.content)
    (dest / "artifact.json").write_text(
        json.dumps(artifact.to_container(), indent=2) + "\n", encoding="utf-8"
    )
    return dest


@app.command("list")
def list_scripts() -> None:
    """List installed script artifacts."""
    root = _scripts_root()
    pins = set(_read_pins(root))
    entries = sorted(p for p in root.iterdir() if p.is_dir())
    if not entries:
        typer.echo("No scripts installed.")
        return
    for entry in entries:
        manifest_path = entry / "manifest.json"
        name = "unknown"
        version = "unknown"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    name = str(manifest.get("name") or name)
                    version = str(manifest.get("version") or version)
            except Exception:
                pass
        artifact_hash = "0x" + entry.name
        pin_mark = "*" if artifact_hash in pins else " "
        typer.echo(f"{pin_mark} {artifact_hash}  {name}@{version}")


@app.command("verify")
def verify_script(
    artifact: Path = typer.Argument(..., help="Path to a script artifact JSON container"),
) -> None:
    """Verify a script artifact container hash."""
    try:
        parsed = load_container(artifact)
    except ScriptArtifactError as exc:
        raise typer.BadParameter(str(exc))
    computed = parsed.artifact_hash_hex()
    container = json.loads(artifact.read_text(encoding="utf-8"))
    expected = str(container.get("artifact_hash") or "")
    if expected:
        typer.echo(f"Expected: {expected}")
    typer.echo(f"Computed: {computed}")
    if expected and normalize_hex(expected) != normalize_hex(computed):
        raise typer.Exit(code=1)


@app.command("install")
def install_script(
    artifact: Path = typer.Argument(..., help="Path to script artifact JSON container"),
    pin: bool = typer.Option(False, "--pin", help="Pin script for consensus use"),
) -> None:
    """Install a script artifact into the local scripts directory."""
    try:
        parsed = load_container(artifact)
    except ScriptArtifactError as exc:
        raise typer.BadParameter(str(exc))
    root = _scripts_root()
    dest = _write_artifact_dir(root, parsed)
    typer.echo(f"Installed to {dest}")
    if pin:
        pins = _read_pins(root)
        pins.append(parsed.artifact_hash_hex())
        _write_pins(root, pins)
        typer.echo("Pinned script")


@app.command("pin")
def pin_script(
    script_hash: str = typer.Argument(..., help="Script hash to pin (0x...)"),
) -> None:
    """Pin a script hash for consensus use."""
    root = _scripts_root()
    normalized = normalize_hex(script_hash)
    pins = _read_pins(root)
    if normalized not in pins:
        pins.append(normalized)
        _write_pins(root, pins)
    typer.echo(f"Pinned {normalized}")


@app.command("test-vector-verify")
def verify_vector(
    vector_file: Path = typer.Argument(..., help="Path to test vector JSON"),
) -> None:
    """Verify a deterministic script test vector."""
    vec = load_vector(vector_file)
    inputs = bytes.fromhex(vec.inputs_cbor_hex.replace("0x", ""))
    outputs = bytes.fromhex(vec.outputs_cbor_hex.replace("0x", ""))
    computed = compute_commitment(outputs)
    expected = normalize_hex(vec.outputs_commit_hex)
    if normalize_hex(computed) != expected:
        raise typer.Exit(code=1)
    typer.echo(f"script_hash: {normalize_hex(vec.script_hash)}")
    typer.echo(f"inputs_cbor: {vec.inputs_cbor_hex}")
    typer.echo(f"outputs_cbor: {vec.outputs_cbor_hex}")
    typer.echo(f"outputs_commit: {computed}")
