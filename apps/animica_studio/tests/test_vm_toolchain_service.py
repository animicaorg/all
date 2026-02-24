"""Tests for VmToolchainService — determinism checks, validation, compile helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from animica_studio.services.vm_toolchain_service import (
    DeterminismError,
    DiagnosticEntry,
    VmToolchainService,
)


@pytest.fixture()
def svc() -> VmToolchainService:
    return VmToolchainService()


# ---------------------------------------------------------------------------
# Determinism checks
# ---------------------------------------------------------------------------


def test_determinism_clean_file(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "contract.py"
    py.write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    result = svc.check_determinism(tmp_path)
    assert result.valid
    assert not result.issues


def test_determinism_banned_import(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "contract.py"
    py.write_text("import random\n\ndef get_val():\n    return random.randint(0, 100)\n")
    result = svc.check_determinism(tmp_path)
    assert not result.valid
    errors = [i for i in result.issues if i.severity == "error"]
    assert any("random" in e.message.lower() for e in errors)
    assert all(e.code == "DET001" for e in errors)


def test_determinism_banned_socket(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "contract.py"
    py.write_text("import socket\n\ndef connect():\n    socket.connect(('127.0.0.1', 8080))\n")
    result = svc.check_determinism(tmp_path)
    assert not result.valid


def test_determinism_nondeterministic_call_warning(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "contract.py"
    py.write_text("import time\n\ndef sleep():\n    time.sleep(1)\n")
    result = svc.check_determinism(tmp_path)
    # time.sleep is flagged as warning (DET002); time import is not in ALWAYS_BANNED
    # so the result may be valid (warnings only) but should have issues
    assert result.issues
    assert any(i.code in ("DET001", "DET002") for i in result.issues)


def test_determinism_multiple_files(svc: VmToolchainService, tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("def foo(): return 1\n")
    (tmp_path / "dirty.py").write_text("import secrets\n")
    result = svc.check_determinism(tmp_path)
    assert not result.valid
    dirty_issues = [i for i in result.issues if "dirty.py" in i.file]
    assert dirty_issues


# ---------------------------------------------------------------------------
# Syntax checks
# ---------------------------------------------------------------------------


def test_syntax_error_reported(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "bad.py"
    py.write_text("def broken(\n", encoding="utf-8")
    issues = svc._check_syntax(py, "bad.py")
    assert any(i.severity == "error" and i.code == "SYN001" for i in issues)


def test_syntax_ok_no_issues(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "good.py"
    py.write_text("def f(x: int) -> int:\n    return x * 2\n")
    issues = svc._check_syntax(py, "good.py")
    assert not issues


# ---------------------------------------------------------------------------
# JSON / manifest validation
# ---------------------------------------------------------------------------


def test_validate_project_missing_manifest_field(svc: VmToolchainService, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"version": "1.0.0"}', encoding="utf-8")  # missing "name"
    (tmp_path / "contract.py").write_text("def init(ctx): pass\n")
    result = svc.validate_project(tmp_path)
    issues = result.issues
    errors = [i for i in issues if i.code == "MAN001"]
    assert errors


def test_validate_project_invalid_json(svc: VmToolchainService, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{bad json}", encoding="utf-8")
    (tmp_path / "contract.py").write_text("def init(ctx): pass\n")
    result = svc.validate_project(tmp_path)
    assert not result.valid
    assert any(i.code == "JSON001" for i in result.issues)


def test_validate_abi_duplicate_selector(svc: VmToolchainService, tmp_path: Path) -> None:
    abi = tmp_path / "abi.json"
    abi.write_text(
        '[{"name":"foo","type":"function","inputs":[]},{"name":"foo","type":"function","inputs":[]}]',
        encoding="utf-8",
    )
    (tmp_path / "contract.py").write_text("def init(ctx): pass\n")
    result = svc.validate_project(tmp_path)
    assert any(i.code == "ABI001" for i in result.issues)


# ---------------------------------------------------------------------------
# VM version / ABI types
# ---------------------------------------------------------------------------


def test_get_vm_version_returns_string(svc: VmToolchainService) -> None:
    v = svc.get_vm_version()
    assert isinstance(v, str)


def test_get_supported_abi_types_non_empty(svc: VmToolchainService) -> None:
    types = svc.get_supported_abi_types()
    assert isinstance(types, list)
    assert len(types) > 0
    assert "uint256" in types
    assert "address" in types


# ---------------------------------------------------------------------------
# Compile (no vm_py installed — fallback path)
# ---------------------------------------------------------------------------


def test_compile_contract_nonexistent_file(svc: VmToolchainService, tmp_path: Path) -> None:
    result = svc.compile_contract(tmp_path / "nonexistent.py")
    assert not result.success
    assert result.diagnostics


def test_compile_contract_syntax_error(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "contract.py"
    py.write_text("def broken(\n", encoding="utf-8")
    result = svc.compile_contract(py)
    assert not result.success
    assert result.diagnostics


def test_compile_contract_determinism_block(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "contract.py"
    py.write_text("import random\ndef bad(): return random.randint(0, 1)\n")
    with pytest.raises(DeterminismError):
        svc.compile_contract(py)


def test_compile_contract_clean(svc: VmToolchainService, tmp_path: Path) -> None:
    py = tmp_path / "contract.py"
    py.write_text("def init(ctx): ctx.storage['x'] = 1\n")
    result = svc.compile_contract(py)
    # Without vm_py installed, syntax check passes
    assert result.success


# ---------------------------------------------------------------------------
# Manifest export
# ---------------------------------------------------------------------------


def test_export_manifest_reads_existing(svc: VmToolchainService, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"name": "MyContract", "version": "2.0.0"}', encoding="utf-8")
    data = svc.export_manifest(tmp_path)
    assert data["name"] == "MyContract"
    assert data["version"] == "2.0.0"


def test_export_manifest_synthesizes_missing(svc: VmToolchainService, tmp_path: Path) -> None:
    (tmp_path / "contract.py").write_text("pass\n")
    data = svc.export_manifest(tmp_path)
    assert data["name"] == tmp_path.name
    assert "version" in data


def test_export_manifest_invalid_json_raises(svc: VmToolchainService, tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{bad}", encoding="utf-8")
    from animica_studio.services.vm_toolchain_service import VmToolchainError

    with pytest.raises(VmToolchainError):
        svc.export_manifest(tmp_path)


# ---------------------------------------------------------------------------
# Simulate (no vm_py — graceful degradation)
# ---------------------------------------------------------------------------


def test_simulate_call_no_entry_file(svc: VmToolchainService, tmp_path: Path) -> None:
    result = svc.simulate_call(tmp_path, {"method": "get"})
    assert not result.success


def test_simulate_call_returns_degraded_result(svc: VmToolchainService, tmp_path: Path) -> None:
    (tmp_path / "contract.py").write_text("def get(ctx): return 42\n")
    result = svc.simulate_call(tmp_path, {"method": "get"})
    # Without vm_py, we expect a non-success with informational message
    assert isinstance(result.success, bool)
    if not result.success:
        assert result.diagnostics


# ---------------------------------------------------------------------------
# Build package
# ---------------------------------------------------------------------------


def test_build_package_no_entry(svc: VmToolchainService, tmp_path: Path) -> None:
    result = svc.build_package(tmp_path)
    assert not result.success
    assert any("entry" in d.message.lower() for d in result.diagnostics)


def test_build_package_valid_project(svc: VmToolchainService, tmp_path: Path) -> None:
    (tmp_path / "contract.py").write_text("def init(ctx): pass\n")
    (tmp_path / "manifest.json").write_text('{"name": "Test", "version": "1.0.0"}')
    result = svc.build_package(tmp_path)
    assert result.success
    assert result.build_info
    assert (tmp_path / "build" / "build-info.json").exists()
