# -*- coding: utf-8 -*-
"""
Template tests for the AI Agent contract in the Python workspace scaffold.

What these tests cover (pure local, no devnet required):
- The workspace build pipeline compiles the AI Agent contract deterministically.
- The built manifest/ABI has the expected shape and contains core AI-capability methods.
- The IR blob exists, has sane size bounds, and its sha3-256 code hash is stable.
- (Optional) A tiny VM-level smoke test, skipped unless a local VM package is installed.

Notes:
- Names in ABI may vary (snake vs camel). We normalize to leniently detect required methods.
- The optional VM test is deliberately sketch-like; adapt to your project's harness.
"""

from __future__ import annotations

import json
import subprocess
import sys
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
AGENT_SRC = CONTRACTS / "ai_agent" / "contract.py"
AGENT_MANIFEST_SRC = CONTRACTS / "ai_agent" / "manifest.json"


def _run_build_all(extra_args: Iterable[str] = ()) -> subprocess.CompletedProcess:
    """
    Execute the workspace build-all script. It should compile each contract and emit:
      ./build/<contract>/{manifest.json, code.ir, package.json?}
    """
    script = SCRIPTS / "build_all.py"
    assert script.exists(), f"Missing build script: {script}"
    cmd = [sys.executable, str(script), *list(extra_args)]
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)


def _candidate_agent_build_dirs() -> List[Path]:
    """
    Likely locations for AI agent artifacts, ordered by preference. We also scan any
    build/* dir that claims 'ai' in its manifest.name as a fallback.
    """
    candidates: List[Path] = [
        BUILD / "ai_agent",
        BUILD / "ai-agent",
        BUILD / "agent_ai",
    ]
    if BUILD.exists():
        for d in sorted(p for p in BUILD.iterdir() if p.is_dir()):
            mpath = d / "manifest.json"
            if mpath.exists():
                try:
                    m = json.loads(mpath.read_text(encoding="utf-8"))
                    name = str(m.get("name", "")).lower()
                    if any(k in name for k in ("ai", "agent")):
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
    """Normalize ABI names for robust presence checks."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sources_exist():
    """AI Agent source and manifest must exist in the template tree."""
    assert AGENT_SRC.exists(), f"Missing AI Agent source: {AGENT_SRC}"
    assert (
        AGENT_MANIFEST_SRC.exists()
    ), f"Missing AI Agent manifest: {AGENT_MANIFEST_SRC}"


def test_build_outputs_and_code_hash_match():
    """
    Build the workspace and verify AI Agent artifacts:
      - build/<agent>/manifest.json
      - build/<agent>/code.ir
      - (optional) build/<agent>/package.json with code_hash
    Validate any recorded code_hash equals sha3-256(code.ir).
    """
    cp = _run_build_all()
    # Print logs to aid CI triage if assertions fail.
    sys.stdout.write(cp.stdout)
    sys.stderr.write(cp.stderr)

    out_dir = _first_dir(_candidate_agent_build_dirs())
    assert out_dir, f"AI Agent build dir not found under {BUILD}"
    manifest_path = out_dir / "manifest.json"
    ir_path = out_dir / "code.ir"
    pkg_path = out_dir / "package.json"

    assert manifest_path.exists(), f"Missing manifest: {manifest_path}"
    assert ir_path.exists(), f"Missing IR: {ir_path}"

    ir_bytes = ir_path.read_bytes()
    computed = sha3_256(ir_bytes).hexdigest()

    if pkg_path.exists():
        pkg = _jload(pkg_path)
        recorded = (
            (pkg.get("code_hash") or pkg.get("codeHash") or "")
            .lower()
            .removeprefix("0x")
        )
        assert recorded == computed, (
            "package.json code_hash mismatch\n"
            f" recorded: 0x{recorded}\n"
            f" computed: 0x{computed}"
        )

    manifest = _jload(manifest_path)
    m_hash = (
        (manifest.get("code_hash") or manifest.get("codeHash") or "")
        .lower()
        .removeprefix("0x")
    )
    if m_hash:
        assert m_hash == computed, (
            "manifest.json code_hash mismatch\n"
            f" recorded: 0x{m_hash}\n"
            f" computed: 0x{computed}"
        )


def test_manifest_minimum_shape():
    """Built manifest should include a non-empty name and an ABI array."""
    out_dir = _first_dir(_candidate_agent_build_dirs())
    assert out_dir, "AI Agent build dir missing (did build step run?)"
    manifest = _jload(out_dir / "manifest.json")

    assert (
        isinstance(manifest.get("name"), str) and manifest["name"].strip()
    ), "manifest.name must be non-empty"
    assert isinstance(manifest.get("abi"), list), "manifest.abi must be a list"


def test_abi_includes_core_ai_capability_surface():
    """
    The AI Agent template should expose a minimal capability surface for AICF:
      Required functions (normalized names accepted):
        - enqueue / submit / request     (submits a model+prompt job)
        - readresult / consume           (reads next-block result by task_id or internal pointer)
      Recommended accessors (not asserted hard):
        - lasttaskid / lastrequest / lastresult
      Events (recommended; at least one should be present):
        - JobEnqueued, ResultAvailable, ResultConsumed
    """
    out_dir = _first_dir(_candidate_agent_build_dirs())
    assert out_dir, "AI Agent build dir missing (did build step run?)"
    abi = _jload(out_dir / "manifest.json").get("abi", [])
    assert isinstance(abi, list), "ABI must be a list"

    fn_names: Set[str] = {
        _normalize(e.get("name", ""))
        for e in abi
        if e.get("type") in (None, "function", "func", "method")
    }
    ev_names: Set[str] = {
        _normalize(e.get("name", "")) for e in abi if e.get("type") in ("event",)
    }

    # Required primitives (lenient aliasing)
    has_enqueue = any(
        n in fn_names
        for n in ("enqueue", "submit", "request", "submitprompt", "enqueueprompt")
    )
    has_read = any(
        n in fn_names for n in ("readresult", "consume", "getresult", "read")
    )
    assert (
        has_enqueue
    ), "ABI must include an 'enqueue/submit/request' style function for AI jobs"
    assert has_read, "ABI must include a 'readResult/consume' style function"

    # Events — require at least one recognizable AICF-related event
    core_events_present = any(
        n in ev_names
        for n in ("jobenqueued", "resultavailable", "resultconsumed", "jobcompleted")
    )
    assert core_events_present, (
        "ABI is missing recognizable AI-job events "
        "(expected one of JobEnqueued/ResultAvailable/ResultConsumed/JobCompleted)"
    )


def test_ir_non_trivial_size():
    """IR should not be empty or suspiciously tiny/huge."""
    out_dir = _first_dir(_candidate_agent_build_dirs())
    assert out_dir, "AI Agent build dir missing (did build step run?)"
    ir = (out_dir / "code.ir").read_bytes()
    assert len(ir) > 64, f"IR is too small ({len(ir)} bytes) — likely a bad/stub build"
    assert len(ir) < 10_000_000, f"IR unexpectedly large ({len(ir)} bytes)"


def test_build_idempotence_by_code_hash():
    """Two successive builds should yield the same IR code hash (deterministic)."""
    _run_build_all()
    d1 = _first_dir(_candidate_agent_build_dirs())
    assert d1, "AI Agent build dir missing after first build"
    h1 = sha3_256((d1 / "code.ir").read_bytes()).hexdigest()

    _run_build_all()
    d2 = _first_dir(_candidate_agent_build_dirs())
    assert d2, "AI Agent build dir missing after second build"
    h2 = sha3_256((d2 / "code.ir").read_bytes()).hexdigest()

    assert h1 == h2, "IR code hash changed across builds — non-deterministic output?"


# ---------------------------------------------------------------------------
# Optional VM smoke (skipped unless a local VM is importable)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not any(
        __import__(m, globals(), locals(), [], 0) or True
        for m in ["animica_vm_py", "vm_py", "animica.vm_py"]
        if __import__("importlib").util.find_spec(m) is not None
    ),
    reason="Local VM not installed; execution tests are optional for the template.",
)
def test_optional_vm_enqueue_then_consume_flow():
    """
    Sketch for a tiny VM-level flow (pseudo-harness):
      - deploy AI Agent
      - enqueue a prompt
      - (pretend the next block landed a result) — inject or mock a result read
      - verify the agent's state reflects the consumed result
    Replace with your project's actual VM harness + capabilities adapter.
    """
    import importlib

    vm_mod_name_candidates = ["animica_vm_py", "vm_py", "animica.vm_py"]
    vm_mod_name = next(
        m for m in vm_mod_name_candidates if importlib.util.find_spec(m) is not None
    )
    vm = importlib.import_module(vm_mod_name)

    out_dir = _first_dir(_candidate_agent_build_dirs())
    manifest = _jload(out_dir / "manifest.json")
    ir = (out_dir / "code.ir").read_bytes()

    # Pseudocode — adapt for your harness:
    # vm_instance = vm.LocalVM()
    # user = "addr_user"
    # addr = vm_instance.deploy(manifest=manifest, code_ir=ir, sender=user)
    # # Enqueue a tiny prompt
    # task_id = vm_instance.call(addr, "enqueue", ["toy-model", b"hello"], sender=user)
    # # Simulate that the next block provided a result (your harness may let you inject it)
    # vm_instance.inject_capability_result(task_id=task_id, bytes_result=b"world")
    # res = vm_instance.call(addr, "readResult", [task_id], sender=user)
    # assert res == b"world"
    # # Optional: check state accessors the template might provide
    # last_id = vm_instance.call(addr, "lastTaskId", [], sender=user)
    # assert last_id == task_id

    assert manifest and ir  # keep lint quiet for the template skeleton


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
