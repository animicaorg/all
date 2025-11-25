# pytest: validate registry.json schema and basic integrity/uniqueness
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except Exception as e:  # pragma: no cover
    pytest.skip("jsonschema is required for this test (pip install jsonschema)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
CHAINS_DIR = ROOT / "chains"
REGISTRY_PATH = CHAINS_DIR / "registry.json"
CHAIN_SCHEMA_PATH = CHAINS_DIR / "schemas" / "chain.schema.json"
REGISTRY_SCHEMA_PATH = CHAINS_DIR / "schemas" / "registry.schema.json"

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def registry():
    assert REGISTRY_PATH.exists(), f"missing {REGISTRY_PATH}"
    return _load_json(REGISTRY_PATH)


@pytest.fixture(scope="session")
def schemas():
    assert CHAIN_SCHEMA_PATH.exists(), f"missing {CHAIN_SCHEMA_PATH}"
    assert REGISTRY_SCHEMA_PATH.exists(), f"missing {REGISTRY_SCHEMA_PATH}"
    return {
        "chain": _load_json(CHAIN_SCHEMA_PATH),
        "registry": _load_json(REGISTRY_SCHEMA_PATH),
    }


def test_registry_schema_valid(registry, schemas):
    """registry.json conforms to its schema."""
    v = Draft202012Validator(schemas["registry"])
    errs = list(v.iter_errors(registry))
    assert not errs, "registry.json failed schema validation:\n" + "\n".join(
        f"- {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errs
    )


def test_registry_entries_unique_and_paths_exist(registry):
    """Keys/chainIds unique; paths resolve; icon paths (when present) exist."""
    entries = registry.get("entries", [])
    assert isinstance(entries, list) and entries, "registry.entries must be a non-empty array"

    keys = [e["key"] for e in entries]
    chain_ids = [e["chainId"] for e in entries]
    paths = [e["path"] for e in entries]

    assert len(keys) == len(set(keys)), "duplicate registry keys detected"
    assert len(chain_ids) == len(set(chain_ids)), "duplicate chainId detected"
    assert len(paths) == len(set(paths)), "duplicate file paths detected in registry"

    for e in entries:
        p = ROOT / e["path"]
        assert p.exists(), f"chain file does not exist: {p}"

        # Optional icons block
        icons = e.get("icons") or {}
        for k in ("svg", "svgDark", "png64", "png128"):
            if k in icons and icons[k]:
                ip = ROOT / icons[k]
                assert ip.exists(), f"icon missing: {k} -> {ip}"


def test_registry_checksums_format(registry):
    """Each entry.checksum should be a 64-hex or an allowed placeholder."""
    allowed_placeholder = {"<sha256-to-be-generated>"}
    for e in registry.get("entries", []):
        csum = e.get("checksum")
        assert isinstance(csum, str), f"checksum must be string for key={e.get('key')}"
        ok = csum in allowed_placeholder or bool(HEX64.match(csum.lower()))
        assert ok, f"bad checksum format for key={e.get('key')}: {csum}"


def test_chain_files_conform_to_chain_schema(registry, schemas):
    """Spot-check: every listed chain JSON validates against chain.schema.json."""
    v = Draft202012Validator(schemas["chain"])
    for e in registry.get("entries", []):
        p = ROOT / e["path"]
        data = _load_json(p)
        errs = list(v.iter_errors(data))
        assert not errs, f"{p} failed chain.schema.json:\n" + "\n".join(
            f"- {'/'.join(map(str, er.path)) or '(root)'}: {er.message}" for er in errs
        )
