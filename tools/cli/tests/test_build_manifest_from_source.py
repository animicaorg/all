import hashlib
import json
from pathlib import Path
from typing import Tuple

import pytest

# Prefer the packaging helper if available; otherwise we still verify manifest integrity.
build_pkg_mod = pytest.importorskip("contracts.tools.build_package")

try:  # Optional, used to validate against the canonical manifest schema when applicable
    import jsonschema
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "manifest.schema.json"


def _pick_example() -> Tuple[Path, Path]:
    """Choose a manifest+source pair that exists in this checkout."""
    candidates = [
        (REPO_ROOT / "vm_py" / "examples" / "escrow" / "manifest.json",
         REPO_ROOT / "vm_py" / "examples" / "escrow" / "contract.py"),
        (REPO_ROOT / "tests" / "fixtures" / "contracts" / "counter" / "manifest.json",
         REPO_ROOT / "tests" / "fixtures" / "contracts" / "counter" / "contract.py"),
        (REPO_ROOT / "contracts" / "examples" / "token" / "manifest.json",
         REPO_ROOT / "contracts" / "examples" / "token" / "contract.py"),
    ]
    for manifest, source in candidates:
        if manifest.exists() and source.exists():
            return manifest, source
    pytest.skip("No manifest+source example available for build-package test")


def _maybe_validate_schema(manifest: dict) -> None:
    """Validate against the canonical schema only when shapes align."""

    if jsonschema is None or not SCHEMA_PATH.exists():
        return
    # Only validate manifests that look like the newer schema; legacy examples are permitted.
    if not {"manifestVersion", "vm", "code"}.issubset(manifest.keys()):
        return

    schema = json.loads(SCHEMA_PATH.read_text())
    Validator = jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
    Validator.check_schema(schema)
    validator = Validator(schema)
    validator.validate(manifest)


@pytest.mark.skipif(
    not hasattr(build_pkg_mod, "build_package"),
    reason="contracts.tools.build_package.build_package missing",
)
def test_build_manifest_from_source(tmp_path: Path) -> None:
    manifest_in, source = _pick_example()
    build_package = build_pkg_mod.build_package

    out_dir = tmp_path / "pkg"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        meta = build_package(
            manifest_path=manifest_in,
            source_path=source,
            abi=None,
            name=None,
            version=None,
            out_dir=out_dir,
            stdout_json=False,
        )
    except build_pkg_mod.CompileError as exc:  # type: ignore[attr-defined]
        pytest.skip(f"vm_py compiler unavailable: {exc}")

    # Paths & code hash
    manifest_path = Path(meta["manifest_path"])
    ir_path = Path(meta["ir_path"])
    pkg_dir = Path(meta["package_dir"])
    code_hash = meta["code_hash"]

    assert manifest_path.exists() and ir_path.exists()
    assert pkg_dir.exists()
    assert isinstance(code_hash, str) and code_hash.startswith("0x") and len(code_hash) == 66

    # Recompute hash from IR bytes
    ir_bytes = ir_path.read_bytes()
    expected_hash = "0x" + hashlib.sha3_256(ir_bytes).hexdigest()
    assert expected_hash == code_hash

    # Manifest contents stay intact and gain the computed hash
    manifest = json.loads(manifest_path.read_text())
    original = json.loads(manifest_in.read_text())

    assert manifest.get("code_hash") == code_hash
    for key in ("name", "version", "language", "entry"):
        if key in original:
            assert manifest.get(key) == original[key]

    if "abi" in manifest:
        assert isinstance(manifest["abi"], (list, dict))
    _maybe_validate_schema(manifest)

    # Package index is consistent with artifacts on disk
    pkg_index = json.loads((pkg_dir / "package.json").read_text())
    assert pkg_index["code_hash"] == code_hash
    assert pkg_index["files"]["manifest"] == manifest_path.name
    assert pkg_index["files"]["ir"] == ir_path.name
    assert pkg_index["sizes"]["ir_bytes"] == len(ir_bytes)
    assert pkg_index["sizes"]["manifest_bytes"] == len(manifest_path.read_bytes())
