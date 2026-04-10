from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from vm_py.runtime import manifest as manifest_utils


def _write_contract(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "def get():",
                "    return 1",
                "",
                "def set(n):",
                "    return n",
                "",
                "def inc():",
                "    return 2",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_manifest_source_field_resolves_relative_to_manifest(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "pkg"
    manifest_dir.mkdir(parents=True)
    contract = manifest_dir / "contract.py"
    _write_contract(contract)

    manifest = {"name": "Test", "source": "contract.py"}
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resolved = manifest_utils.resolve_contract_source(
        manifest,
        manifest_path=manifest_path,
    )
    assert resolved.source_paths[0] == contract.resolve()


def test_source_field_has_precedence_over_entry(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "pkg"
    manifest_dir.mkdir(parents=True)
    contract = manifest_dir / "contract.py"
    _write_contract(contract)
    manifest = {"name": "Test", "source": "missing.py", "entry": "contract.py"}
    try:
        manifest_utils.resolve_contract_source(
            manifest,
            manifest_path=manifest_dir / "manifest.json",
        )
    except FileNotFoundError:
        return
    raise AssertionError("expected missing source path to fail before entry fallback")


def test_manifest_entry_and_sources_dict_resolve_consistently(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "pkg"
    manifest_dir.mkdir(parents=True)
    contract = manifest_dir / "contract.py"
    _write_contract(contract)

    manifest = {
        "name": "Test",
        "entry": "contract.py",
        "sources": {"contract.py": "./contract.py"},
    }
    resolved = manifest_utils.resolve_contract_source(
        manifest,
        manifest_path=manifest_dir / "manifest.json",
    )
    assert resolved.source_paths[0] == contract.resolve()
    assert resolved.selected_field == "entry"


def test_cli_run_accepts_entry_plus_sources_manifest(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest_dir = tmp_path / "pkg"
    manifest_dir.mkdir(parents=True)
    contract = manifest_dir / "contract.py"
    _write_contract(contract)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "CompatExample",
                "entry": "contract.py",
                "sources": {"contract.py": "./contract.py"},
                "abi": {
                    "functions": [
                        {"name": "get", "inputs": [], "outputs": [{"type": "int"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    out_ir = manifest_dir / "out.ir"
    compile_cmd = [
        sys.executable,
        "-m",
        "vm_py.cli.compile",
        "--manifest",
        str(manifest_path),
        "--out",
        str(out_ir),
    ]
    compile_res = subprocess.run(
        compile_cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compile_res.returncode == 0, compile_res.stderr
    assert out_ir.exists() and out_ir.stat().st_size > 0

    run_cmd = [
        sys.executable,
        "-m",
        "vm_py.cli.run",
        "--manifest",
        str(manifest_path),
        "--call",
        "get",
    ]
    res = subprocess.run(
        run_cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert '"result": 1' in res.stdout


def test_manifest_path_resolution_avoids_double_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path
    manifest_dir = root / "vm_py" / "examples" / "min_counter"
    manifest_dir.mkdir(parents=True)
    contract = manifest_dir / "contract.py"
    _write_contract(contract)

    # This value is cwd-relative and would be wrong if manifest dir is prepended twice.
    manifest = {"name": "Test", "source": "vm_py/examples/min_counter/contract.py"}
    monkeypatch.chdir(root)
    resolved = manifest_utils.resolve_contract_source(
        manifest,
        manifest_path=manifest_dir / "manifest.json",
    )
    assert resolved.source_paths[0] == contract.resolve()


def test_counter_compile_and_run_cli_smoke(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = repo_root / "vm_py" / "examples" / "counter" / "manifest.json"
    out_ir = tmp_path / "counter.ir"

    compile_cmd = [
        sys.executable,
        "-m",
        "vm_py.cli.compile",
        "--manifest",
        str(manifest),
        "--out",
        str(out_ir),
    ]
    compile_res = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    assert compile_res.returncode == 0, compile_res.stderr
    assert out_ir.exists() and out_ir.stat().st_size > 0

    for call, args in (("get", None), ("set", "[5]"), ("inc", None)):
        run_cmd = [
            sys.executable,
            "-m",
            "vm_py.cli.run",
            "--manifest",
            str(manifest),
            "--call",
            call,
        ]
        if args is not None:
            run_cmd.extend(["--args", args])
        run_res = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            cwd=repo_root,
            check=False,
        )
        assert run_res.returncode == 0, run_res.stderr
        assert '"ok": true' in run_res.stdout.lower()
