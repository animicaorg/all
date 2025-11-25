# -*- coding: utf-8 -*-
"""
Template tests for the Escrow contract in the Python workspace scaffold.

Goals of this suite:
- Prove the workspace build pipeline compiles the escrow contract deterministically.
- Sanity-check the built manifest/ABI shape and make sure core escrow methods/events exist.
- Compute and validate the IR code hash recorded in artifacts (if present).
- Keep tests local-only (no devnet dependency); optionally allow VM-level tests when
  a local VM package is available in the environment (skipped otherwise).

These tests intentionally avoid pinning to a specific SDK/VM version so the
template remains portable. Extend freely in your project.
"""

from __future__ import annotations

import json
import sys
import subprocess
from hashlib import sha3_256
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import pytest


# ---------------------------------------------------------------------------
# Paths & helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BUILD = ROOT / "build"
CONTRACTS = ROOT / "contracts"
ESCROW_SRC = CONTRACTS / "escrow" / "contract.py"
ESCROW_MANIFEST_SRC = CONTRACTS / "escrow" / "manifest.json"


def _run_build_all(extra_args: Iterable[str] = ()) -> subprocess.CompletedProcess:
    """
    Execute the workspace build script. It should compile all contracts and place
    artifacts in ./build/<contract-name>/
    """
    script = SCRIPTS / "build_all.py"
    assert script.exists(), f"Missing build script: {script}"
    cmd = [sys.executable, str(script), *list(extra_args)]
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)


def _candidate_escrow_build_dirs() -> List[Path]:
    """
    Likely locations for escrow artifacts, ordered by preference.
    """
    candidates: List[Path] = [BUILD / "escrow"]
    if BUILD.exists():
        for d in sorted(p for p in BUILD.iterdir() if p.is_dir()):
            mpath = d / "manifest.json"
            if mpath.exists():
                try:
                    m = json.loads(mpath.read_text(encoding="utf-8"))
                    name = str(m.get("name", "")).lower()
                    if "escrow" in name:
                        candidates.append(d)
                except Exception:
                    pass
    # Deduplicate preserving order
    seen = set()
    uniq: List[Path] = []
    for c in candidates:
        if c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq


def _first_dir(paths: Iterable[Path]) -> Optional[Path]:
    for p in paths:
        if p.exists() and p.is_dir():
            return p
    return None


def _jload(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize(name: str) -> str:
    """
    Normalize function/event names across snake_case vs camelCase differences.
    """
    return "".join(ch for ch in name.lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sources_exist():
    """Escrow source and manifest must exist in the template tree."""
    assert ESCROW_SRC.exists(), f"Missing escrow source: {ESCROW_SRC}"
    assert ESCROW_MANIFEST_SRC.exists(), f"Missing escrow manifest: {ESCROW_MANIFEST_SRC}"


def test_build_outputs_and_code_hash_match():
    """
    Build the workspace and verify escrow artifacts:
      - build/<escrow>/manifest.json
      - build/<escrow>/code.ir
      - (optional) build/<escrow>/package.json with code_hash
    Validate that any recorded code_hash equals sha3-256(code.ir).
    """
    cp = _run_build_all()
    # Emit build logs to help CI diagnostics if assertions fail.
    sys.stdout.write(cp.stdout)
    sys.stderr.write(cp.stderr)

    out_dir = _first_dir(_candidate_escrow_build_dirs())
    assert out_dir, f"Escrow build dir not found under {BUILD}"
    manifest_path = out_dir / "manifest.json"
    ir_path = out_dir / "code.ir"
    pkg_path = out_dir / "package.json"

    assert manifest_path.exists(), f"Missing manifest: {manifest_path}"
    assert ir_path.exists(), f"Missing IR: {ir_path}"

    ir_bytes = ir_path.read_bytes()
    computed = sha3_256(ir_bytes).hexdigest()

    if pkg_path.exists():
        pkg = _jload(pkg_path)
        recorded = (pkg.get("code_hash") or pkg.get("codeHash") or "").lower().removeprefix("0x")
        assert recorded == computed, (
            "package.json code_hash mismatch\n"
            f" recorded: 0x{recorded}\n"
            f" computed: 0x{computed}"
        )

    manifest = _jload(manifest_path)
    m_hash = (manifest.get("code_hash") or manifest.get("codeHash") or "").lower().removeprefix("0x")
    if m_hash:
        assert m_hash == computed, (
            "manifest.json code_hash mismatch\n"
            f" recorded: 0x{m_hash}\n"
            f" computed: 0x{computed}"
        )


def test_manifest_minimum_shape():
    """Built manifest should include a non-empty name and an ABI array."""
    out_dir = _first_dir(_candidate_escrow_build_dirs())
    assert out_dir, "Escrow build dir missing (did build step run?)"
    manifest = _jload(out_dir / "manifest.json")

    assert isinstance(manifest.get("name"), str) and manifest["name"].strip(), "manifest.name must be non-empty"
    assert isinstance(manifest.get("abi"), list), "manifest.abi must be a list"


def test_abi_includes_core_escrow_functions_and_events():
    """
    Core escrow surface (names can vary snake/camel; we normalize):
      Required functions (minimum):
        - deposit
        - release OR refund (at least one present; commonly both)
        - state (or getState) to inspect escrow status  [optional but recommended]
      Recommended additional functions:
        - opendispute / open_dispute
        - resolve
        - beneficiary / seller / buyer getters (any subset)

      Events (recommended; some templates may omit names or differ):
        - Deposited
        - Released
        - Refunded
        - DisputeOpened
        - DisputeResolved
    """
    out_dir = _first_dir(_candidate_escrow_build_dirs())
    assert out_dir, "Escrow build dir missing (did build step run?)"
    abi = _jload(out_dir / "manifest.json").get("abi", [])
    assert isinstance(abi, list), "ABI must be a list"

    fn_names: Set[str] = {
        _normalize(e.get("name", ""))
        for e in abi
        if e.get("type") in (None, "function", "func", "method")
    }
    ev_names: Set[str] = {
        _normalize(e.get("name", ""))
        for e in abi
        if e.get("type") in ("event",)
    }

    # Required primitives
    assert "deposit" in fn_names, "ABI must include a 'deposit' function"
    assert (
        "release" in fn_names or "refund" in fn_names
    ), "ABI should include at least one of 'release' or 'refund'"

    # Optional but strong recommendations
    # We don't assert, but we keep as documentation & gentle guardrails:
    _ = {
        "state" in fn_names or "getstate" in fn_names,
        "opendispute" in fn_names,
        "resolve" in fn_names,
    }

    # Events: warn via assertion message only if *none* of the core events exist
    core_events_present = any(
        n in ev_names
        for n in ("deposited", "released", "refunded", "disputeopened", "disputeresolved")
    )
    assert core_events_present, (
        "ABI is missing recognizable escrow events "
        "(expected one of Deposited/Released/Refunded/DisputeOpened/DisputeResolved)"
    )


def test_ir_non_trivial_size():
    """IR should not be empty or suspiciously tiny/huge."""
    out_dir = _first_dir(_candidate_escrow_build_dirs())
    assert out_dir, "Escrow build dir missing (did build step run?)"
    ir = (out_dir / "code.ir").read_bytes()
    assert len(ir) > 64, f"IR is too small ({len(ir)} bytes) — likely a bad/stub build"
    assert len(ir) < 10_000_000, f"IR unexpectedly large ({len(ir)} bytes)"


def test_build_idempotence_by_code_hash():
    """Two successive builds should yield the same IR code hash (deterministic)."""
    _run_build_all()
    d1 = _first_dir(_candidate_escrow_build_dirs())
    assert d1, "Escrow build dir missing after first build"
    h1 = sha3_256((d1 / "code.ir").read_bytes()).hexdigest()

    _run_build_all()
    d2 = _first_dir(_candidate_escrow_build_dirs())
    assert d2, "Escrow build dir missing after second build"
    h2 = sha3_256((d2 / "code.ir").read_bytes()).hexdigest()

    assert h1 == h2, "IR code hash changed across builds — non-deterministic output?"


# ---------------------------------------------------------------------------
# Optional: VM smoke (skipped unless a local VM is importable)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not any(
        __import__(m, globals(), locals(), [], 0) or True
        for m in ["animica_vm_py", "vm_py", "animica.vm_py"]
        if __import__("importlib").util.find_spec(m) is not None
    ),
    reason="Local VM not installed; execution tests are optional for the template.",
)
def test_optional_vm_deposit_then_release_flow():
    """
    Sketch for a tiny VM-level flow:
      - deploy escrow
      - deposit amount
      - release (or refund) to the counterparty
    This is intentionally pseudocode-ish; adapt to your project's VM harness.
    """
    import importlib

    vm_mod_name = next(
        m for m in ["animica_vm_py", "vm_py", "animica.vm_py"]
        if importlib.util.find_spec(m) is not None
    )
    vm = importlib.import_module(vm_mod_name)

    out_dir = _first_dir(_candidate_escrow_build_dirs())
    manifest = _jload(out_dir / "manifest.json")
    ir = (out_dir / "code.ir").read_bytes()

    # Pseudocode — replace with your harness:
    # vm_instance = vm.LocalVM()
    # buyer, seller = "addr_buyer", "addr_seller"
    # addr = vm_instance.deploy(manifest=manifest, code_ir=ir, sender=buyer)
    # vm_instance.call(addr, "deposit", [seller, 100])
    # vm_instance.call(addr, "release", [seller])
    # balance_seller = vm_instance.call(addr, "balanceOf", [seller])
    # assert balance_seller >= 100

    assert manifest and ir  # placeholder to keep linters happy


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
