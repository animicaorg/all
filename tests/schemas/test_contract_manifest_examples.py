import json
from pathlib import Path
from typing import List, Set

import pytest

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]


# Relative path to the canonical contract manifest schema.
MANIFEST_SCHEMA_REL = Path("contracts/schemas/manifest.schema.json")

# Where example manifests live.
# We assume:
#   - contracts/examples/** contain example manifest JSONs
#     (either directly or in subdirs, e.g. contracts/examples/counter/manifest.json)
MANIFEST_EXAMPLE_GLOBS: List[str] = [
    "contracts/examples/**/*.json",
]

# Known examples that are currently out-of-sync with the manifest schema.
KNOWN_BROKEN_MANIFEST_EXAMPLES: Set[Path] = {
    Path("contracts/examples/escrow/manifest.json"),
    Path("contracts/examples/registry/manifest.json"),
}


def _repo_root() -> Path:
    """
    Resolve the repository root assuming this file lives under tests/schemas/.
    """
    return Path(__file__).resolve().parents[2]


def _load_manifest_schema() -> dict:
    root = _repo_root()
    schema_path = root / MANIFEST_SCHEMA_REL
    if not schema_path.is_file():
        raise AssertionError(
            f"Contract manifest schema not found at {MANIFEST_SCHEMA_REL!s} "
            f"(resolved: {schema_path})"
        )
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _looks_like_manifest(path: Path) -> bool:
    """
    Very lightweight filter to avoid picking up arbitrary JSON files
    that are clearly not manifests (if you add other JSON in examples).
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False

    if not isinstance(data, dict):
        return False

    # Heuristic: manifests should have at least these fields.
    keys = set(data.keys())
    required = {"manifestVersion", "name", "version"}
    return required.issubset(keys)


def _discover_example_paths() -> List[Path]:
    """
    Find all contract manifest example JSON files under the configured globs.

    Returns a list of **relative** paths (from repo root) so test output is stable.
    """
    root = _repo_root()
    seen: set[Path] = set()
    results: List[Path] = []

    for pattern in MANIFEST_EXAMPLE_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if path.suffix != ".json":
                continue
            if not _looks_like_manifest(path):
                continue
            rel = path.relative_to(root)
            if rel in seen:
                continue
            seen.add(rel)
            results.append(rel)

    # Keep test order deterministic
    results.sort()
    return results


EXAMPLE_PATHS: List[Path] = _discover_example_paths()


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
@pytest.mark.parametrize(
    "rel_path",
    EXAMPLE_PATHS,
    ids=lambda p: str(p),
)
def test_contract_manifest_examples_validate_against_schema(rel_path: Path) -> None:
    """
    Validate all example contract manifest JSON files in contracts/examples/**
    against contracts/schemas/manifest.schema.json.

    This keeps:
      * example contracts used in docs,
      * sample packages in contracts/examples,

    in sync with the canonical manifest schema.

    Some older/out-of-sync manifests are marked as xfail so they remain
    visible without breaking the suite.
    """
    if not EXAMPLE_PATHS:
        pytest.skip(
            "No contract manifest example JSON files found for configured globs"
        )

    root = _repo_root()
    schema = _load_manifest_schema()

    # Let jsonschema pick the right validator (2020-12, etc.).
    Validator = jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
    Validator.check_schema(schema)
    validator = Validator(schema)

    target_path = root / rel_path
    assert target_path.is_file(), f"Example manifest file missing: {rel_path}"

    with target_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if rel_path in KNOWN_BROKEN_MANIFEST_EXAMPLES:
        pytest.xfail(
            f"Known out-of-sync contract manifest example: {rel_path}. "
            "Update example or schema to re-enable."
        )

    validator.validate(data)
