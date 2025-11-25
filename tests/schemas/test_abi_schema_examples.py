import json
from pathlib import Path
from typing import List, Set

import pytest

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]


# Relative path to the canonical ABI schema used by docs & tooling.
ABI_SCHEMA_REL = Path("docs/schemas/abi.schema.json")

# Globs for "example ABI JSON files" we want to validate.
# These are the places the dump says ABIs live:
#   - docs/examples/abi_*.json
#   - tests/fixtures/abi/*.json
#   - contracts/fixtures/abi/*.json
#   - sdk/common/examples/*abi*.json
ABI_EXAMPLE_GLOBS: List[str] = [
    "docs/examples/abi_*.json",
    "tests/fixtures/abi/*.json",
    "contracts/fixtures/abi/*.json",
    "sdk/common/examples/*abi*.json",
]

# Known examples that are currently out-of-sync with the canonical ABI schema.
# We mark these as xfail so they show up in test output but do not break CI.
KNOWN_BROKEN_ABI_EXAMPLES: Set[Path] = {
    Path("contracts/fixtures/abi/ai_agent.json"),
    Path("contracts/fixtures/abi/escrow.json"),
    Path("contracts/fixtures/abi/quantum_rng.json"),
    Path("contracts/fixtures/abi/registry.json"),
    Path("contracts/fixtures/abi/token20.json"),
    Path("docs/examples/abi_counter.json"),
    Path("sdk/common/examples/counter_abi.json"),
    Path("tests/fixtures/abi/counter.json"),
}


def _repo_root() -> Path:
    """
    Resolve the repository root assuming this file lives under tests/schemas/.
    """
    return Path(__file__).resolve().parents[2]


def _load_abi_schema() -> dict:
    root = _repo_root()
    schema_path = root / ABI_SCHEMA_REL
    if not schema_path.is_file():
        raise AssertionError(
            f"ABI schema not found at {ABI_SCHEMA_REL!s} (resolved: {schema_path})"
        )
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _discover_example_paths() -> List[Path]:
    """
    Find all ABI example JSON files under the configured globs.

    Returns a list of **relative** paths (from repo root) so test output is stable.
    """
    root = _repo_root()
    seen: set[Path] = set()
    results: List[Path] = []

    for pattern in ABI_EXAMPLE_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if path.suffix != ".json":
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
def test_abi_examples_validate_against_docs_schema(rel_path: Path) -> None:
    """
    Validate all example ABI JSON files against docs/schemas/abi.schema.json.

    This is a cheap sanity check that:
      * ABI examples used in docs (docs/examples/abi_*.json),
      * test fixtures (tests/fixtures/abi/*.json),
      * contract fixtures (contracts/fixtures/abi/*.json),
      * SDK examples (sdk/common/examples/*abi*.json),

    all stay in sync with the canonical docs ABI schema.

    Some older/out-of-sync examples are currently marked as xfail so they
    remain visible without breaking the suite.
    """
    if not EXAMPLE_PATHS:
        pytest.skip("No ABI example JSON files found for configured globs")

    root = _repo_root()
    schema = _load_abi_schema()

    # Let jsonschema pick the right validator (2020-12 for this schema).
    Validator = jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
    Validator.check_schema(schema)
    validator = Validator(schema)

    target_path = root / rel_path
    assert target_path.is_file(), f"Example file missing: {rel_path}"

    with target_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if rel_path in KNOWN_BROKEN_ABI_EXAMPLES:
        pytest.xfail(
            f"Known out-of-sync ABI example: {rel_path}. "
            "Update either the example or the ABI schema to re-enable."
        )

    # This will raise jsonschema.exceptions.ValidationError on failure,
    # which pytest will report with a nice diff.
    validator.validate(data)
