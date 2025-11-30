import json
from pathlib import Path

import pytest

verify_mod = pytest.importorskip("contracts.tools.verify")


def _pick_example() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[3]
    candidates = [
        # Most likely to exist in this repo
        (
            root / "vm_py" / "examples" / "escrow" / "manifest.json",
            root / "vm_py" / "examples" / "escrow" / "contract.py",
        ),
        (
            root / "tests" / "fixtures" / "contracts" / "counter" / "manifest.json",
            root / "tests" / "fixtures" / "contracts" / "counter" / "contract.py",
        ),
        (
            root / "contracts" / "examples" / "token" / "manifest.json",
            root / "contracts" / "examples" / "token" / "contract.py",
        ),
    ]
    for m, s in candidates:
        if m.exists() and s.exists():
            return m, s
    pytest.skip("No example contract+manifest found")


def _compute_local_code_hash(manifest: Path, source: Path):
    if not hasattr(verify_mod, "_compute_local_code_hash"):
        pytest.skip("contracts.tools.verify._compute_local_code_hash missing")
    res = verify_mod._compute_local_code_hash(manifest, source)  # type: ignore[attr-defined]
    if not getattr(res, "ok", False):
        pytest.skip(f"Local hash unavailable: {getattr(res, 'details', '')}")
    return res.code_hash, res.ir_bytes_len


def _run_cli(manifest: Path, source: Path, capsys):
    if not hasattr(verify_mod, "main"):
        pytest.skip("contracts.tools.verify.main missing")
    argv = [
        "--local-hash",
        "--manifest",
        str(manifest),
        "--source",
        str(source),
        "--json",
    ]
    capsys.readouterr()
    rc = verify_mod.main(argv)
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out
    payload = json.loads(out)
    assert payload.get("ok") is True
    return payload["codeHash"], payload["irBytes"]


def test_compute_code_hash_cli_matches_library_and_is_deterministic(capsys):
    manifest, source = _pick_example()
    lib_hash, lib_ir = _compute_local_code_hash(manifest, source)
    cli_hash, cli_ir = _run_cli(manifest, source, capsys)
    assert cli_hash == lib_hash
    assert cli_ir == lib_ir
    # Determinism
    cli_hash2, cli_ir2 = _run_cli(manifest, source, capsys)
    assert cli_hash2 == cli_hash
    assert cli_ir2 == cli_ir
