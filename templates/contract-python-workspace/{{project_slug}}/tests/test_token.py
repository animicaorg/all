# -*- coding: utf-8 -*-
"""
Tests for the Token (Animica-20–like) contract in the workspace template.

These tests are intentionally conservative so they:
- run purely locally (no devnet required),
- validate that the build pipeline produces sane artifacts,
- check that the ABI exposes the expected functions and events,
- ensure the IR code hash is recorded and matches the compiled bytes.

If you have the local VM installed (e.g. `animica-vm-py`), you can extend
this file with execution tests (transfer, approve, transferFrom, etc.).
For the base template, we keep those out to avoid hard SDK/VM version pins.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha3_256
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pytest


# ---------------------------------------------------------------------------
# Paths & helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BUILD = ROOT / "build"
CONTRACTS = ROOT / "contracts"
TOKEN_SRC = CONTRACTS / "token" / "contract.py"
TOKEN_MANIFEST_SRC = CONTRACTS / "token" / "manifest.json"


def _run_build_all(extra_args: Iterable[str] = ()) -> subprocess.CompletedProcess:
    """
    Runs the workspace build script. The script is expected to compile
    all contracts and emit per-contract artifacts into ./build/<name>/
    """
    script = SCRIPTS / "build_all.py"
    assert script.exists(), f"Missing build script: {script}"
    cmd = [sys.executable, str(script), *list(extra_args)]
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)


def _possible_token_build_dirs() -> List[Path]:
    """
    Heuristics: return candidate build directories that could hold
    the token build artifacts. We default to ./build/token first.
    """
    candidates: List[Path] = []
    # A likely canonical path:
    candidates.append(BUILD / "token")
    # Fallbacks: any subdir that contains a manifest.json with a token-like name.
    if BUILD.exists():
        for d in sorted(p for p in BUILD.iterdir() if p.is_dir()):
            if (d / "manifest.json").exists():
                try:
                    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                    name = (m.get("name") or "").lower()
                    if "token" in name or name in {"animica20", "erc20", "fungible"}:
                        candidates.append(d)
                except Exception:
                    pass
    # Deduplicate while preserving order
    uniq: List[Path] = []
    seen = set()
    for p in candidates:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq


def _first_existing_dir(paths: Iterable[Path]) -> Optional[Path]:
    for p in paths:
        if p.exists() and p.is_dir():
            return p
    return None


def _load_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sources_exist():
    """Sanity: ensure the token sources exist in the template."""
    assert TOKEN_SRC.exists(), f"Expected source missing: {TOKEN_SRC}"
    assert TOKEN_MANIFEST_SRC.exists(), f"Expected manifest missing: {TOKEN_MANIFEST_SRC}"


def test_build_produces_artifacts_and_code_hash():
    """
    Build the workspace and assert that token artifacts exist:
      - build/<token>/manifest.json
      - build/<token>/code.ir
      - (optional) build/<token>/package.json with code_hash
    Also verify that any recorded code_hash matches sha3-256(code.ir).
    """
    # Build all contracts (idempotent)
    cp = _run_build_all()
    # Useful for debugging CI logs if something subtle fails:
    sys.stdout.write(cp.stdout)
    sys.stderr.write(cp.stderr)

    token_build = _first_existing_dir(_possible_token_build_dirs())
    assert token_build is not None, f"Could not find token build dir under {BUILD}"
    manifest_out = token_build / "manifest.json"
    ir_out = token_build / "code.ir"
    package_out = token_build / "package.json"

    assert manifest_out.exists(), f"Missing built manifest: {manifest_out}"
    assert ir_out.exists(), f"Missing compiled IR: {ir_out}"

    ir_bytes = ir_out.read_bytes()
    computed_hash = sha3_256(ir_bytes).hexdigest()

    # Prefer checking package.json if present
    if package_out.exists():
        pkg = _load_json(package_out)
        recorded = (pkg.get("code_hash") or pkg.get("codeHash") or "").lower().removeprefix("0x")
        assert recorded == computed_hash, (
            "code_hash in package.json does not match compiled IR.\n"
            f" recorded: 0x{recorded}\n"
            f" computed: 0x{computed_hash}"
        )

    # Some pipelines also record code_hash in the manifest; validate if present.
    manifest = _load_json(manifest_out)
    m_code_hash = (manifest.get("code_hash") or manifest.get("codeHash") or "").lower().removeprefix("0x")
    if m_code_hash:
        assert m_code_hash == computed_hash, (
            "code_hash in manifest.json does not match compiled IR.\n"
            f" recorded: 0x{m_code_hash}\n"
            f" computed: 0x{computed_hash}"
        )


def test_manifest_shape_minimums():
    """
    Validate the built manifest has essential fields:
      - name: str
      - abi: list
    """
    token_build = _first_existing_dir(_possible_token_build_dirs())
    assert token_build, "Token build directory not found (run the build step first)."
    manifest = _load_json(token_build / "manifest.json")

    assert isinstance(manifest.get("name"), str) and manifest["name"].strip(), "Manifest must have a non-empty name"
    assert isinstance(manifest.get("abi"), list), "Manifest must include an ABI array"


def test_abi_exposes_core_token_functions_and_events():
    """
    Confirm the ABI includes core ERC20-like entries:
    Functions: name, symbol, decimals, totalSupply, balanceOf, transfer, approve, allowance, transferFrom
    Events:    Transfer, Approval
    """
    token_build = _first_existing_dir(_possible_token_build_dirs())
    assert token_build, "Token build directory not found (run the build step first)."
    abi = _load_json(token_build / "manifest.json").get("abi", [])
    assert isinstance(abi, list), "ABI must be a list"

    fn_names = {e.get("name") for e in abi if e.get("type") in (None, "function", "func", "method")}
    ev_names = {e.get("name") for e in abi if e.get("type") in ("event",)}

    expected_functions = {
        "name",
        "symbol",
        "decimals",
        "totalSupply",
        "balanceOf",
        "transfer",
        "approve",
        "allowance",
        "transferFrom",
    }
    missing = expected_functions - (fn_names or set())
    assert not missing, f"ABI is missing required token functions: {sorted(missing)}"

    expected_events = {"Transfer", "Approval"}
    missing_e = expected_events - (ev_names or set())
    assert not missing_e, f"ABI is missing required token events: {sorted(missing_e)}"


def test_token_metadata_is_sane():
    """
    Light-weight checks on token metadata-producing functions to catch obvious template mistakes.
    (We do not execute the VM here; we check declared ABI constant outputs if present.)
    """
    token_build = _first_existing_dir(_possible_token_build_dirs())
    assert token_build, "Token build directory not found (run the build step first)."
    manifest = _load_json(token_build / "manifest.json")
    abi = manifest.get("abi", [])

    # Some templates pre-encode constant return values in the ABI (e.g., for 'name', 'symbol', 'decimals').
    # If present, validate their shapes, otherwise this test is a no-op.
    def get_abi_entry(name: str) -> Optional[Dict]:
        for e in abi:
            if e.get("name") == name and e.get("type") in (None, "function", "func", "method"):
                return e
        return None

    name_fn = get_abi_entry("name")
    symbol_fn = get_abi_entry("symbol")
    decimals_fn = get_abi_entry("decimals")

    # If constant encodings are present, do a sanity check
    for entry, label, typ in ((name_fn, "name", "string"), (symbol_fn, "symbol", "string")):
        if entry and entry.get("constant") is True:
            outs = entry.get("outputs") or entry.get("returns") or []
            if outs and isinstance(outs, list) and outs[0].get("type") == typ:
                # If a concrete default value is provided, ensure it's non-empty
                default_val = outs[0].get("default") or outs[0].get("example") or ""
                if default_val is not None:
                    assert str(default_val).strip(), f"{label} default/example must not be empty"
            # else: schema not providing concrete value — that's fine for the template

    if decimals_fn and decimals_fn.get("constant") is True:
        outs = decimals_fn.get("outputs") or decimals_fn.get("returns") or []
        if outs and isinstance(outs, list) and outs[0].get("type") in {"uint8", "uint"}:
            default_val = outs[0].get("default") or outs[0].get("example")
            if default_val is not None:
                try:
                    dv = int(default_val)
                    assert 0 <= dv <= 30, "decimals out of a reasonable range (0..30)"
                except Exception:
                    # If not parseable, don't fail template tests
                    pass


@pytest.mark.parametrize(
    "required_key",
    [
        "functions",  # optional in some manifests — keep flexible; this paramization doubles as documentation
        "events",
    ],
)
def test_manifest_optionally_lists_sections(required_key: str):
    """
    Some pipelines provide extra sections like 'functions' (a reshaping of ABI)
    and 'events'. If they appear, verify their general shape.
    """
    token_build = _first_existing_dir(_possible_token_build_dirs())
    assert token_build, "Token build directory not found (run the build step first)."
    manifest = _load_json(token_build / "manifest.json")

    if required_key in manifest:
        val = manifest[required_key]
        assert isinstance(val, (list, dict)), f"manifest['{required_key}'] must be list/dict when present"
        # Not enforcing a schema here; templates vary across toolchains.


def test_ir_is_non_trivial_and_reasonably_sized():
    """
    Ensure the compiled IR is not empty and not absurdly tiny (which often signals
    a failed or stubbed compilation).
    """
    token_build = _first_existing_dir(_possible_token_build_dirs())
    assert token_build, "Token build directory not found (run the build step first)."
    ir_path = token_build / "code.ir"
    data = ir_path.read_bytes()
    assert len(data) > 64, f"IR is suspiciously small ({len(data)} bytes)"
    # Upper bound is intentionally very high to avoid false positives in CI.
    # This is a smoke check rather than an optimization gate.
    assert len(data) < 10_000_000, f"IR is unexpectedly huge ({len(data)} bytes)"


def test_build_is_idempotent(tmp_path: Path):
    """
    Build twice and ensure stable code hash for the token. This catches
    non-determinism in the compiler/packager layer.
    """
    # First build
    _run_build_all()
    d1 = _first_existing_dir(_possible_token_build_dirs())
    assert d1, "Missing token build directory after first build"
    h1 = sha3_256((d1 / "code.ir").read_bytes()).hexdigest()

    # Second build
    _run_build_all()
    d2 = _first_existing_dir(_possible_token_build_dirs())
    assert d2, "Missing token build directory after second build"
    h2 = sha3_256((d2 / "code.ir").read_bytes()).hexdigest()

    assert h1 == h2, "IR code hash changed across two successive builds (non-deterministic build?)"


# ---------------------------------------------------------------------------
# Optional: gated VM execution tests (skipped if the local VM is not available)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not any(
        __import__(m, globals(), locals(), [], 0) or True
        for m in ["animica_vm_py", "vm_py", "animica.vm_py"]
        if __import__("importlib").util.find_spec(m) is not None
    ),
    reason="Local VM not installed; execution-level tests are optional for the template.",
)
def test_optional_vm_smoke(monkeypatch):
    """
    Example skeleton for a VM-level smoke test. This section is skipped unless a
    local VM package is available in the environment. Feel free to replace with
    your team's canonical harness.
    """
    # This block is intentionally lightweight and pseudo-generic.
    # Replace with your actual VM harness if available.
    try:
        import importlib

        vm_mod_name = next(
            m
            for m in ["animica_vm_py", "vm_py", "animica.vm_py"]
            if importlib.util.find_spec(m) is not None
        )
        vm = importlib.import_module(vm_mod_name)
    except Exception as e:
        pytest.skip(f"Could not import a VM module: {e}")

    token_build = _first_existing_dir(_possible_token_build_dirs())
    manifest = _load_json(token_build / "manifest.json")
    ir_bytes = (token_build / "code.ir").read_bytes()

    # Hypothetical deploy & call API; adapt to your VM:
    # vm_instance = vm.LocalVM() or vm.VM()
    # addr = vm_instance.deploy(manifest=manifest, code_ir=ir_bytes, sender="alice")
    # name = vm_instance.call(addr, "name", [])
    # assert isinstance(name, str) and name

    # As this is just a template, we won't assert real calls here.
    # The presence of this test demonstrates how to gate VM-dependent checks.
    assert manifest and ir_bytes  # noop to silence linters


if __name__ == "__main__":
    # Allow running this file directly for quick iteration
    sys.exit(pytest.main([__file__]))
