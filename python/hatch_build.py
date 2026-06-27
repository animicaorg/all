"""Hatchling build hook that vendors sibling packages into the sdist.

Background
----------
The `animica` Python package re-exports several sibling top-level
packages (`aicf`, `consensus`, `agent_runtime`, …) that live next to
`python/` in the monorepo rather than inside `python/animica/`. Hatchling's
sdist `include` directive silently ignores paths above the project root,
so `python -m build` (which builds a wheel *from* the sdist) used to
fail with "Forced include not found: …/ai/agent_runtime/src/agent_runtime"
because those paths never made it into the sdist tarball.

This hook fixes the round-trip by copying the external sources into
`python/_build_vendor/` (gitignored) before each build. Pyproject's
force-include + sdist-include directives then reference the local
vendor copy, which exists in both the source tree (after the hook
runs) AND inside an unpacked sdist (because the sdist is configured to
include `_build_vendor`).

What gets vendored is listed in ``_VENDOR_MAP`` below; entries are
``(external_path_relative_to_python_dir, vendor_subdir_name)``. Subdirs
intentionally mirror the import path so a force-include like
``"_build_vendor/agent_runtime" = "agent_runtime"`` Just Works.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


# (source_path_relative_to_python_dir, vendor_subdir_name)
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
    ("../randomness", "randomness"),
    ("../rpc", "rpc"),
    ("../stdlib", "stdlib"),
    ("../vm_py", "vm_py"),
    ("../ai/agent_runtime/src/agent_runtime", "agent_runtime"),
    ("../ai/flagship_agent/src/flagship_agent", "flagship_agent"),
    ("../ai/configs", "_data_ai_configs"),
    ("../ai/flagship_agent/scripts", "_data_ai_flagship_agent_scripts"),
    ("../ops/docker/standalone", "_data_ops_docker_standalone"),
]


def _ignore(_dir: str, names: list[str]) -> set[str]:
    """Skip caches + build artifacts that bloat the sdist."""
    return {n for n in names if n in {
        "__pycache__", ".pytest_cache", ".mypy_cache",
        "node_modules", ".venv", ".cache",
    } or n.endswith((".pyc", ".pyo"))}


class VendorExternalsHook(BuildHookInterface):
    PLUGIN_NAME = "vendor-externals"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        project_root = Path(self.root)
        vendor_root = project_root / "_build_vendor"
        vendor_root.mkdir(exist_ok=True)
        # Drop a marker so the dir is preserved when packaged into the
        # sdist (hatchling skips empty dirs without it).
        marker = vendor_root / ".vendor-managed"
        marker.write_text(
            "Generated at build time by python/hatch_build.py. Do not edit by hand.\n",
            encoding="utf-8",
        )

        copied = 0
        skipped = 0
        for rel_src, vendor_name in _VENDOR_MAP:
            src = (project_root / rel_src).resolve()
            dst = vendor_root / vendor_name
            if src.is_dir():
                # Always re-sync: refuses stale vendor contents between
                # successive builds when upstream source changed.
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst, ignore=_ignore, symlinks=False)
                copied += 1
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
            else:
                # Not present at the expected sibling path. This is
                # expected when building a wheel from an UNPACKED sdist:
                # the externals were materialised into _build_vendor at
                # sdist time so the dst dir already exists. Only warn
                # when neither src nor dst are present.
                if not dst.exists():
                    skipped += 1
        if skipped:
            self.app.display_warning(
                f"vendor-externals: {skipped} sibling path(s) missing and not "
                f"already vendored — wheel will be incomplete"
            )
        self.app.display_info(
            f"vendor-externals: {copied}/{len(_VENDOR_MAP)} entries refreshed "
            f"in {vendor_root}"
        )
