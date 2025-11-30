from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

HERE = Path(__file__).resolve()
# .../animica/vm_py/tests/test_manifest_contract_metadata.py
REPO_ROOT = HERE.parents[2]


def _load_manifest(rel_path: str) -> Dict[str, Any]:
    """
    Load a manifest JSON relative to repo root.
    """
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"manifest not found: {rel_path}"
    return json.loads(path.read_text(encoding="utf8"))


def _maybe_get_code_hash(m: Dict[str, Any]) -> Optional[Any]:
    """
    Try to discover a "code hash" field in the assorted legacy formats.
    """
    build = m.get("build") or {}
    candidates = [
        m.get("codeHash"),
        m.get("code_hash"),
        m.get("code_hash_hex"),
        build.get("codeHash"),
        build.get("code_hash"),
    ]
    for val in candidates:
        if val is not None:
            return val
    return None


def _assert_code_hash_sane(ch: Optional[Any]) -> None:
    """
    For this vertical we only care that:
      - it's either None, or
      - some sort of string
      - and if it *looks* like 0x-hex, it's actually valid hex with even length.
    """
    if ch is None:
        return
    assert isinstance(ch, str), "code hash should be string or null"
    if ch.startswith("0x"):
        hex_part = ch[2:]
        assert len(hex_part) % 2 == 0, "0x-hex code hash must have even length"
        int(hex_part, 16)  # will raise if invalid


def _resolve_entry_path(rel_manifest: str, manifest: Dict[str, Any]) -> Optional[Path]:
    """
    Given a manifest, try to resolve where the main contract source file lives.

    Supported shapes seen in this repo:
      - "entry": repo-root or manifest-dir relative path
      - "entrypoint": repo-root or manifest-dir relative path
      - "sources.main": repo-root relative path (newer style)
      - "source.path": manifest-dir relative path (older token manifest)
    """
    # Prefer explicit entry / entrypoint
    entry = manifest.get("entry") or manifest.get("entrypoint")
    if entry:
        p = REPO_ROOT / entry
        if p.is_file():
            return p
        # Fallback: treat as manifest-dir relative
        man_path = REPO_ROOT / rel_manifest
        p2 = man_path.parent / entry
        if p2.is_file():
            return p2

    # Newer source.main style
    sources = manifest.get("sources") or {}
    main_rel = sources.get("main")
    if isinstance(main_rel, str):
        p = REPO_ROOT / main_rel
        if p.is_file():
            return p

    # Older "source": {"path": "contract.py"} style
    source = manifest.get("source") or {}
    if isinstance(source, dict):
        spath = source.get("path")
        if isinstance(spath, str):
            man_path = REPO_ROOT / rel_manifest
            p = man_path.parent / spath
            if p.is_file():
                return p

    # Some manifests are ABI-only (e.g. multisig), so returning None is OK.
    return None


# ---------------------------------------------------------------------------
# Escrow manifest: slightly stronger expectations, since it's our canonical.
# ---------------------------------------------------------------------------


def test_escrow_manifest_core_metadata_fields() -> None:
    """
    The Escrow example manifest should have sane core fields and a resolvable
    contract path.
    """
    rel = "contracts/examples/escrow/manifest.json"
    m = _load_manifest(rel)

    assert m.get("name") == "Escrow"
    version = m.get("version")
    assert isinstance(version, str) and version

    # language format has varied a bit; we only require it's present and non-empty
    lang = m.get("language")
    assert isinstance(lang, str) and lang

    # Must have a contract source file we can point to.
    entry_path = _resolve_entry_path(rel, m)
    assert entry_path is not None, "Escrow manifest must point to a contract file"
    assert entry_path.is_file(), f"Escrow contract path does not exist: {entry_path}"

    # ABI must exist with at least one function.
    abi = m.get("abi") or {}
    fns = abi.get("functions")
    assert isinstance(fns, list) and fns, "Escrow ABI must have at least one function"

    # Code hash, if present, must be sane.
    _assert_code_hash_sane(_maybe_get_code_hash(m))


# ---------------------------------------------------------------------------
# All contracts/examples manifests: flexible but strict-enough checks
# ---------------------------------------------------------------------------

EXAMPLE_MANIFESTS: List[str] = [
    "contracts/examples/oracle/manifest.json",
    "contracts/examples/escrow/manifest.json",
    "contracts/examples/quantum_rng/manifest.json",
    "contracts/examples/ai_agent/manifest.json",
    "contracts/examples/token/manifest.json",
    "contracts/examples/registry/manifest.json",
    "contracts/examples/multisig/manifest.json",
]


@pytest.mark.parametrize("rel_path", EXAMPLE_MANIFESTS)
def test_example_manifests_metadata_and_abi(rel_path: str) -> None:
    """
    For every manifest in contracts/examples/* we assert a *minimal* but useful
    set of invariants:

      - name / version are non-empty strings.
      - language, if present, is a non-empty string.
      - some ABI is present and exposes at least one callable function.
      - if the manifest advertises a source/entry file, it actually exists.
      - if a code hash is present (under any known key), it has a sane shape.
      - if manifestVersion is present, it is 1 (but it's optional).
    """
    m = _load_manifest(rel_path)

    # Basic metadata
    name = m.get("name")
    version = m.get("version")
    assert isinstance(name, str) and name, f"{rel_path}: name must be non-empty string"
    assert (
        isinstance(version, str) and version
    ), f"{rel_path}: version must be non-empty string"

    # Optional manifestVersion
    mv = m.get("manifestVersion")
    if mv is not None:
        assert mv == 1, f"{rel_path}: manifestVersion, when present, must be 1"

    # Optional language
    if "language" in m:
        assert (
            isinstance(m["language"], str) and m["language"]
        ), f"{rel_path}: language must be non-empty string"

    # ABI shape: some manifests have abi: {functions: [...]}, others abi: [...function/event...]
    abi = m.get("abi")
    assert abi is not None, f"{rel_path}: abi must be present"

    has_function = False
    if isinstance(abi, dict):
        fns = abi.get("functions")
        if isinstance(fns, list) and fns:
            has_function = True
    elif isinstance(abi, list):
        # e.g. multisig: list of descriptors
        for item in abi:
            if isinstance(item, dict) and item.get("type") == "function":
                has_function = True
                break

    assert has_function, f"{rel_path}: abi must expose at least one function"

    # Source / entry path: only required if the manifest actually declares one.
    entry_path = _resolve_entry_path(rel_path, m)
    # Some contracts are ABI-only (e.g. multisig). In that case, entry_path
    # will be None and that's acceptable.
    if (
        ("entry" in m)
        or ("entrypoint" in m)
        or ("source" in m)
        or (
            "sources" in m and isinstance(m["sources"], dict) and "main" in m["sources"]
        )
    ):
        assert (
            entry_path is not None
        ), f"{rel_path}: manifest declares an entry but it could not be resolved"
        assert (
            entry_path.is_file()
        ), f"{rel_path}: resolved entry path does not exist: {entry_path}"

    # Code hash sanity (if present).
    _assert_code_hash_sane(_maybe_get_code_hash(m))


# ---------------------------------------------------------------------------
# vm_py example manifests: cross-check against their Python sources
# ---------------------------------------------------------------------------

VM_PY_EXAMPLE_MANIFESTS: List[str] = [
    "vm_py/examples/counter/manifest.json",
    "vm_py/examples/escrow/manifest.json",
]


@pytest.mark.parametrize("rel_path", VM_PY_EXAMPLE_MANIFESTS)
def test_vm_py_example_manifests_have_metadata_and_sources(rel_path: str) -> None:
    """
    The vm_py/examples/* manifests are wired into docs/tutorials. We require:

      - name / version non-empty.
      - language present and non-empty.
      - ABI exposes at least one function.
      - entry/source path (if declared) exists.
      - manifestVersion, if present, is 1.
    """
    m = _load_manifest(rel_path)

    name = m.get("name")
    version = m.get("version")
    assert isinstance(name, str) and name, f"{rel_path}: name must be non-empty string"
    assert (
        isinstance(version, str) and version
    ), f"{rel_path}: version must be non-empty string"

    mv = m.get("manifestVersion")
    if mv is not None:
        assert mv == 1, f"{rel_path}: manifestVersion, when present, must be 1"

    lang = m.get("language")
    assert (
        isinstance(lang, str) and lang
    ), f"{rel_path}: language must be non-empty string"

    abi = m.get("abi")
    assert abi is not None, f"{rel_path}: abi must be present"

    has_function = False
    if isinstance(abi, dict):
        fns = abi.get("functions")
        if isinstance(fns, list) and fns:
            has_function = True
    elif isinstance(abi, list):
        for item in abi:
            if isinstance(item, dict) and item.get("type") == "function":
                has_function = True
                break

    assert has_function, f"{rel_path}: abi must expose at least one function"

    entry_path = _resolve_entry_path(rel_path, m)
    if (
        ("entry" in m)
        or ("entrypoint" in m)
        or ("source" in m)
        or (
            "sources" in m and isinstance(m["sources"], dict) and "main" in m["sources"]
        )
    ):
        assert (
            entry_path is not None
        ), f"{rel_path}: declared contract path could not be resolved"
        assert (
            entry_path.is_file()
        ), f"{rel_path}: resolved contract path does not exist: {entry_path}"

    _assert_code_hash_sane(_maybe_get_code_hash(m))
