"""Manifest generation and validation helpers."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List

logger = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_manifest(root: Path, include: Iterable[Path]) -> Dict[str, object]:
    files: List[Dict[str, object]] = []
    for item in include:
        if not item.exists():
            continue
        rel = item.relative_to(root)
        files.append(
            {
                "path": rel.as_posix(),
                "sha256": _sha256(item),
                "size": item.stat().st_size,
            }
        )
    return {"root": str(root), "files": files}


def walk_files(root: Path) -> List[Path]:
    files = []
    for path in root.rglob("*"):
        if path.is_file():
            files.append(path)
    return files


def write_manifest(path: Path, payload: Dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_manifest(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_manifest(root: Path, manifest: Dict[str, object]) -> List[str]:
    errors: List[str] = []
    for entry in manifest.get("files", []):
        path = root / entry["path"]
        if not path.exists():
            errors.append(f"missing: {path}")
            continue
        expected = entry.get("sha256")
        actual = _sha256(path)
        if expected and expected != actual:
            errors.append(f"sha256 mismatch: {path}")
    return errors


def resolve_version() -> str:
    try:
        import tomllib
        repo_root = Path(__file__).resolve().parents[4]
        pyproject = repo_root / "apps" / "miner-gui" / "pyproject.toml"
        return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    except Exception as exc:
        logger.debug("Could not resolve version: %s", exc)
        return "0.1.0"


def resolve_commit() -> str:
    try:
        import subprocess

        repo_root = Path(__file__).resolve().parents[4]
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as exc:
        logger.debug("Could not resolve commit: %s", exc)
    return "unknown"


def generate_node_manifest(node_root: Path) -> Dict[str, object]:
    payload = build_manifest(node_root, walk_files(node_root))
    payload["version"] = resolve_version()
    payload["commit"] = resolve_commit()
    payload["kind"] = "node"
    return payload


def generate_gui_manifest(app_bundle: Path) -> Dict[str, object]:
    contents = app_bundle / "Contents"
    candidates = [
        contents / "MacOS" / "Animica Miner GUI",
        contents / "Info.plist",
    ]
    payload = build_manifest(app_bundle, [p for p in candidates if p.exists()])
    payload["version"] = resolve_version()
    payload["commit"] = resolve_commit()
    payload["kind"] = "gui"
    return payload


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate Animica GUI manifests")
    parser.add_argument("--node-root", type=Path, required=True)
    parser.add_argument("--app-bundle", type=Path, required=True)
    parser.add_argument("--node-out", type=Path, required=True)
    parser.add_argument("--gui-out", type=Path, required=True)
    args = parser.parse_args()

    node_manifest = generate_node_manifest(args.node_root)
    gui_manifest = generate_gui_manifest(args.app_bundle)
    write_manifest(args.node_out, node_manifest)
    write_manifest(args.gui_out, gui_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
