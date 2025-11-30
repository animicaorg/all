# -*- coding: utf-8 -*-
"""
Basic template contract tests.

These tests are designed to be:
- Fast: no RPC/devnet required.
- Robust: they validate structure and determinism rules even if the local VM toolchain
  isn't available yet.
- Helpful: failures print actionable hints for users adopting the template.

What we check:
1) Manifest integrity: the ABI exists and exposes the canonical "inc" and "get" functions.
2) Build pipeline: the template build script runs and produces artifacts in ./build/.
3) Determinism subset: the source avoids non-deterministic / disallowed imports and primitives.

Optional checks (best-effort):
- If the build process emits a JSON artifact containing a code hash, we lightly sanity-check it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Project layout
ROOT = Path(__file__).resolve().parents[1]  # {{project_slug}}/
CONTRACTS_DIR = ROOT / "contracts"
SRC_FILE = CONTRACTS_DIR / "contract.py"
MANIFEST_FILE = CONTRACTS_DIR / "manifest.json"
SCRIPTS_DIR = ROOT / "scripts"
BUILD_DIR = ROOT / "build"


# ------------------------- helpers -------------------------


def _read_manifest() -> dict:
    assert MANIFEST_FILE.is_file(), f"manifest.json not found at: {MANIFEST_FILE}"
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AssertionError(f"manifest.json is not valid JSON: {e}") from e


def _collect_functions_from_manifest(manifest: dict) -> set[str]:
    """
    Accept both shapes:
      { "abi": { "functions": [...] } }  or  { "abi": [...] }
    Each function entry should have a "name" key.
    """
    abi = manifest.get("abi")
    funcs = set()

    if isinstance(abi, dict) and isinstance(abi.get("functions"), list):
        for f in abi["functions"]:
            name = f.get("name")
            if isinstance(name, str):
                funcs.add(name)
    elif isinstance(abi, list):
        for f in abi:
            name = f.get("name") if isinstance(f, dict) else None
            if isinstance(name, str):
                funcs.add(name)
    else:
        # Some templates flatten ABI differently; attempt a best-effort scan
        for k, v in manifest.items():
            if isinstance(v, list) and all(isinstance(x, dict) for x in v):
                for f in v:
                    name = f.get("name")
                    if isinstance(name, str):
                        funcs.add(name)
    return funcs


def _run_build_script() -> subprocess.CompletedProcess:
    """
    Invoke the template's build script.
    We use the project's Python to maximize success in constrained environments.
    """
    script = SCRIPTS_DIR / "build.py"
    assert script.is_file(), f"scripts/build.py not found at: {script}"

    cmd = [sys.executable, str(script)]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # If the SDK/VM isn't installed yet, provide a friendly hint
    if proc.returncode != 0:
        msg = (
            "[build] scripts/build.py failed.\n"
            f"  Command : {' '.join(cmd)}\n"
            f"  CWD     : {ROOT}\n"
            f"  STDOUT  :\n{proc.stdout}\n"
            f"  STDERR  :\n{proc.stderr}\n"
            "If this is a fresh checkout, ensure your environment is prepared, e.g.:\n"
            "  python -m venv .venv && source .venv/bin/activate\n"
            "  pip install -r requirements.txt\n"
        )
        raise AssertionError(msg)
    return proc


# ------------------------- tests -------------------------


def test_manifest_has_required_functions():
    """
    The starter template should expose a minimal Counter-style ABI:
      - inc(): state-changing, increments an internal counter
      - get(): view, returns current counter value
    We only assert presence by name to remain implementation-agnostic.
    """
    manifest = _read_manifest()
    funcs = _collect_functions_from_manifest(manifest)

    assert (
        "inc" in funcs
    ), f'"inc" function not found in manifest ABI. Found: {sorted(funcs)}'
    assert (
        "get" in funcs
    ), f'"get" function not found in manifest ABI. Found: {sorted(funcs)}'

    # Nice-to-have: names are unique
    assert len(funcs) == len(
        set(funcs)
    ), "Duplicate function names in ABI (should be unique)."


def test_source_uses_deterministic_subset():
    """
    Contracts must avoid non-deterministic or host-interacting modules/APIs.
    This test scans for a set of common offenders and helpful red flags.

    NOTE: This is a heuristic content check for the starter template. The canonical
    validator is vm_py/validate.py; this test provides fast feedback before compile time.
    """
    assert SRC_FILE.is_file(), f"Contract source not found: {SRC_FILE}"
    text = SRC_FILE.read_text(encoding="utf-8")

    # Ban explicit imports of risky modules
    banned_modules = [
        r"\bimport\s+os\b",
        r"\bfrom\s+os\s+import\b",
        r"\bimport\s+sys\b",
        r"\bfrom\s+sys\s+import\b",
        r"\bimport\s+time\b",
        r"\bfrom\s+time\s+import\b",
        r"\bimport\s+socket\b",
        r"\bfrom\s+socket\s+import\b",
        r"\bimport\s+subprocess\b",
        r"\bfrom\s+subprocess\s+import\b",
        r"\bimport\s+threading\b",
        r"\bfrom\s+threading\s+import\b",
        r"\bimport\s+multiprocessing\b",
        r"\bfrom\s+multiprocessing\s+import\b",
        r"\bimport\s+asyncio\b",
        r"\bfrom\s+asyncio\s+import\b",
        r"\bimport\s+requests\b",
        r"\bfrom\s+requests\s+import\b",
        r"\bimport\s+urllib\b",
        r"\bfrom\s+urllib\s+import\b",
        r"\bimport\s+http\b",
        r"\bfrom\s+http\s+import\b",
    ]
    banned_primitives = [
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\bopen\s*\(",
        r"\b__import__\s*\(",
    ]

    violations = []
    for pat in banned_modules + banned_primitives:
        if re.search(pat, text):
            violations.append(pat)

    assert not violations, (
        "Detected disallowed imports/APIs in the contract source. "
        "Use only the stdlib surface exposed by the VM (e.g., from stdlib import storage, events, abi, hash, treasury, syscalls).\n"
        f"Matches: {violations}"
    )


def test_build_script_produces_artifacts(tmp_path):
    """
    The build script should complete successfully and produce something in ./build.
    We don't over-specify exact file names to keep the template flexible,
    but we require at least one JSON artifact (e.g., package or metadata).
    """
    proc = _run_build_script()
    # Mirror output in test logs for debugging
    sys.stdout.write(proc.stdout)

    assert BUILD_DIR.exists(), f"Build directory not found: {BUILD_DIR}"
    artifacts = list(BUILD_DIR.glob("**/*"))
    assert artifacts, f"No build artifacts found in {BUILD_DIR}"

    # Optional: try to find a JSON artifact that looks like a package/metadata with a code hash
    json_candidates = [p for p in artifacts if p.suffix == ".json"]
    if not json_candidates:
        pytest.skip(
            "Build produced no JSON artifacts to inspect (acceptable for minimal templates)."
        )

    # Best-effort validation of any 'codeHash' field
    found_any_hash = False
    for jp in json_candidates:
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Common shapes:
        #  - {"codeHash":"0x..."} or nested {"result":{"codeHash":"0x..."}}
        candidate = None
        if isinstance(data, dict):
            if "codeHash" in data and isinstance(data["codeHash"], str):
                candidate = data["codeHash"]
            elif isinstance(data.get("result"), dict) and isinstance(
                data["result"].get("codeHash"), str
            ):
                candidate = data["result"]["codeHash"]

        if candidate:
            found_any_hash = True
            assert (
                candidate.startswith("0x") and len(candidate) >= 10
            ), f"Suspicious codeHash value in {jp}: {candidate!r}"
            break

    if not found_any_hash:
        pytest.skip(
            "No codeHash field found in build JSON artifacts (acceptable for minimal templates)."
        )


# ------------------------- optional: VM smoke (best-effort) -------------------------


@pytest.mark.optionalhook
def test_vm_toolchain_present():
    """
    Optional sanity: confirm the VM CLI entrypoint can be discovered.
    We don't fail the suite if it's missing; the template aims to be runnable
    out-of-the-box, but users may adopt it before installing the VM toolchain.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "vm_py.cli.run", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            pytest.skip("VM CLI not available yet (vm_py not installed).")
    except FileNotFoundError:
        pytest.skip(
            "Python not found in PATH (unexpected), skipping optional VM check."
        )
