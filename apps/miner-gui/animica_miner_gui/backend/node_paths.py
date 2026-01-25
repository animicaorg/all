"""Node path resolution helpers for the Animica Miner GUI."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from animica_miner_gui.backend.app_paths import get_resources_dir, is_frozen


@dataclass(frozen=True)
class NodePaths:
    """Resolved node executable details."""

    exe_path: Optional[Path]
    base_dir: Optional[Path]
    mode: str
    reason: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _validate_executable(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "missing"
    if not path.is_file():
        return False, "not a file"
    if not os.access(path, os.X_OK):
        return False, "not executable"
    try:
        if path.stat().st_size == 0:
            return False, "empty file"
    except OSError as exc:
        return False, f"stat failed: {exc}"
    return True, "ok"


def resolve_node_executable() -> NodePaths:
    """Resolve the node executable path with clear diagnostics."""
    mode = "frozen" if is_frozen() else "dev"
    candidates: list[tuple[str, Path]] = []
    reasons: list[str] = []

    env_override = os.environ.get("ANIMICA_NODE_PATH")
    if env_override:
        candidates.append(("ANIMICA_NODE_PATH", Path(env_override)))

    if is_frozen():
        resources_dir = get_resources_dir()
        candidates.append(
            (
                "bundle_resources",
                resources_dir / "node" / "animica-node" / "animica-node",
            )
        )
    else:
        repo_root = _repo_root()
        candidates.extend(
            [
                (
                    "repo_payload",
                    repo_root
                    / "apps"
                    / "miner-gui"
                    / "animica_miner_gui"
                    / "node"
                    / "animica-node"
                    / "animica-node",
                ),
                ("dist_payload", repo_root / "dist" / "animica-node" / "animica-node"),
            ]
        )
        path_lookup = shutil.which("animica-node")
        if path_lookup:
            candidates.append(("PATH", Path(path_lookup)))

    for label, candidate in candidates:
        ok, status = _validate_executable(candidate)
        if ok:
            return NodePaths(
                exe_path=candidate,
                base_dir=candidate.parent,
                mode=mode,
                reason=f"Resolved node executable via {label}: {candidate}",
            )
        reasons.append(f"{label}: {candidate} ({status})")

    reason_text = "No runnable node executable found."
    if reasons:
        reason_text = f"{reason_text} Tried:\n" + "\n".join(f"- {item}" for item in reasons)
    return NodePaths(
        exe_path=None,
        base_dir=None,
        mode=mode,
        reason=reason_text,
    )
