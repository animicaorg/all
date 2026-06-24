"""Hatchling build hook for the slim `animica-node` package.

Vendors the node-runtime sibling packages into `_build_vendor/` before each
build, mirroring python/hatch_build.py — but with the heavy AI/GPU packages
(agent_runtime, flagship_agent, training data) DELIBERATELY EXCLUDED. The result
is a node + JSON-RPC runtime with no torch/transformers/CUDA footprint.

What's vendored is listed in ``_VENDOR_MAP`` below; entries are
``(external_path_relative_to_this_dir, vendor_subdir_name)``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


# (source_path_relative_to_node-pkg_dir, vendor_subdir_name)
# Node runtime only — NO ai/agent_runtime, ai/flagship_agent, or AI training data.
_VENDOR_MAP: list[tuple[str, str]] = [
    ("../aicf", "aicf"),
    ("../capabilities", "capabilities"),
    ("../consensus", "consensus"),
    ("../core", "core"),
    ("../coretx", "coretx"),
    ("../da", "da"),
    ("../execution", "execution"),
    ("../mempool", "mempool"),
    ("../mempool2", "mempool2"),
    ("../mining", "mining"),
    ("../p2p", "p2p"),
    ("../pq", "pq"),
    ("../rpc", "rpc"),
    ("../stdlib", "stdlib"),
    ("../vm_py", "vm_py"),
    # The node runtime imports a handful of helpers from the `animica` CLI
    # package (animica.config, animica.bootstrap, animica.seeds, animica.sync,
    # animica.tx, animica.cli.tx, animica.autoupdate). Bundle the package but
    # prune its heavy, node-irrelevant subtrees (see _PRUNE below).
    ("../python/animica", "animica"),
]

# Subdirs pruned from copied trees: build caches everywhere, plus the big
# AI/pool/desktop subtrees of the `animica` package that the node never imports
# (kept out so the slim wheel stays small and torch-free at runtime).
_PRUNE = {
    "__pycache__", ".pytest_cache", ".mypy_cache",
    "node_modules", ".venv", ".cache",
    "stratum_pool", "ena", "studio", "bridge_banm", "tests",
}


def _ignore(_dir: str, names: list[str]) -> set[str]:
    """Skip caches + build artifacts + node-irrelevant heavy subtrees."""
    return {n for n in names if n in _PRUNE or n.endswith((".pyc", ".pyo"))}


class VendorNodeHook(BuildHookInterface):
    PLUGIN_NAME = "vendor-node"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        project_root = Path(self.root)
        vendor_root = project_root / "_build_vendor"
        vendor_root.mkdir(exist_ok=True)
        marker = vendor_root / ".vendor-managed"
        marker.write_text(
            "Generated at build time by node-pkg/hatch_build.py. Do not edit by hand.\n",
            encoding="utf-8",
        )

        copied = 0
        skipped = 0
        for rel_src, vendor_name in _VENDOR_MAP:
            src = (project_root / rel_src).resolve()
            dst = vendor_root / vendor_name
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst, ignore=_ignore, symlinks=False)
                copied += 1
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
            else:
                if not dst.exists():
                    skipped += 1
        if skipped:
            self.app.display_warning(
                f"vendor-node: {skipped} sibling path(s) missing and not already "
                f"vendored — wheel will be incomplete"
            )
        self.app.display_info(
            f"vendor-node: {copied}/{len(_VENDOR_MAP)} entries refreshed in {vendor_root}"
        )
