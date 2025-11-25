import hashlib
import json
from pathlib import Path
import pytest

# Prefer the packaging helper if available; otherwise we still verify manifest integrity.
build_pkg_mod = pytest.importorskip("contracts.tools.build_package")
verify_mod = pytest.importorskip("contracts.tools.verify")

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "vm_py" / "examples" / "escrow" / "manifest.json"
SOURCE = REPO_ROOT / "vm_py" / "examples" / "escrow" / "contract.py"

@pytest.mark.skipif(not MANIFEST.exists() or not SOURCE.exists(), reason="escrow example missing")
def test_build_manifest_from_source(tmp_path: Path) -> None:
    build_package = getattr(build_pkg_mod, "build_package", None)
    if build_package is None:
        pytest.skip("contracts.tools.build_package.build_package missing")
    out_dir = tmp_path / "pkg"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = build_package(
        manifest_path=MANIFEST,
        source_path=SOURCE,
        abi=None,
        name=None,
        version=None,
        out_dir=out_dir,
        stdout_json=False,
    )
    # Paths & code hash
    manifest_path = Path(meta["manifest_path"])
    ir_path = Path(meta["ir_path"])
    code_hash = meta["code_hash"]
    assert manifest_path.exists() and ir_path.exists()
    assert isinstance(code_hash, str) and code_hash.startswith("0x") and len(code_hash) == 66

    # Recompute hash from IR bytes
    ir_bytes = ir_path.read_bytes()
    assert "0x" + hashlib.sha3_256(ir_bytes).hexdigest() == code_hash

    # Manifest contents
    manifest = json.loads(manifest_path.read_text())
    assert manifest.get("code_hash") == code_hash
    assert isinstance(manifest.get("abi"), (list, dict))
    assert isinstance(manifest.get("schema"), str) and "animica.contract_manifest" in manifest["schema"]
