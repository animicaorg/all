import json
from pathlib import Path
from typing import List

import pytest

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]


# Relative path to the canonical **docs-level package manifest** schema.
PACKAGE_MANIFEST_SCHEMA_REL = Path("docs/schemas/manifest.schema.json")

# Where example package manifests live.
# We assume:
#   - docs/examples/** contain example *package* manifests
#     e.g. docs/examples/counter/manifest.json
PACKAGE_MANIFEST_EXAMPLE_GLOBS: List[str] = [
    "docs/examples/**/*.json",
]


def _repo_root() -> Path:
    """
    Resolve the repository root assuming this file lives under tests/schemas/.
    """
    return Path(__file__).resolve().parents[2]


def _load_package_manifest_schema() -> dict:
    root = _repo_root()
    schema_path = root / PACKAGE_MANIFEST_SCHEMA_REL
    if not schema_path.is_file():
        raise AssertionError(
            "Docs-level package manifest schema not found at "
            f"{PACKAGE_MANIFEST_SCHEMA_REL!s} (resolved: {schema_path})"
        )
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _looks_like_package_manifest(path: Path) -> bool:
    """
    Lightweight heuristic to distinguish docs-level **package manifests**
    from other JSON docs/examples.

    The docs manifest schema in the dump has:
      - encoding: "animica.manifest.v1"
      - schema_version: "1.0" / "1.1"
      - contract: {...}
      - code: {...}
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False

    if not isinstance(data, dict):
        return False

    encoding = data.get("encoding")
    schema_version = data.get("schema_version")
    has_contract = isinstance(data.get("contract"), dict)
    has_code = isinstance(data.get("code"), dict)

    if encoding != "animica.manifest.v1":
        return False
    if not isinstance(schema_version, str):
        return False
    if not has_contract or not has_code:
        return False

    return True


def _discover_example_paths() -> List[Path]:
    """
    Find all docs-level package manifest example JSON files.

    Returns a list of **relative** paths (from repo root) so test output is stable.
    """
    root = _repo_root()
    seen: set[Path] = set()
    results: List[Path] = []

    for pattern in PACKAGE_MANIFEST_EXAMPLE_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if path.suffix != ".json":
                continue
            if not _looks_like_package_manifest(path):
                continue
            rel = path.relative_to(root)
            if rel in seen:
                continue
            seen.add(rel)
            results.append(rel)

    # Deterministic order for stable parametrization
    results.sort()
    return results


EXAMPLE_PATHS: List[Path] = _discover_example_paths()


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
@pytest.mark.parametrize(
    "rel_path",
    EXAMPLE_PATHS,
    ids=lambda p: str(p),
)
def test_docs_package_manifest_examples_validate_against_schema(rel_path: Path) -> None:
    """
    Validate all docs-level package manifest JSON files in docs/examples/**
    against docs/schemas/manifest.schema.json.

    This keeps:
      * example packages referenced in documentation,
      * any sample manifests under docs/examples/**,

    in sync with the canonical docs manifest schema used by tooling.
    """
    if not EXAMPLE_PATHS:
        pytest.skip("No docs-level package manifest examples found for configured globs")

    root = _repo_root()
    schema = _load_package_manifest_schema()

    # Let jsonschema pick the correct validator (2020-12, etc.).
    Validator = jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
    Validator.check_schema(schema)
    validator = Validator(schema)

    target_path = root / rel_path
    assert target_path.is_file(), f"Example package manifest file missing: {rel_path}"

    with target_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    validator.validate(data)
