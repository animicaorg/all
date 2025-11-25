import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

try:
    import jsonschema
    from jsonschema import exceptions as jsonschema_exceptions
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]
    jsonschema_exceptions = None  # type: ignore[assignment]


SCHEMAS_DIR_REL = Path("governance/schemas")
REGISTRIES_DIR_REL = Path("governance/registries")


def _repo_root() -> Path:
    """
    Resolve the repository root assuming this file lives under tests/schemas/.
    """
    return Path(__file__).resolve().parents[2]


def _discover_registry_schema_pairs() -> List[Tuple[Path, Path]]:
    """
    Discover governance registry JSON files and their corresponding schemas.

    Convention (from the Animica dump):
      - Schema files live under: governance/schemas/*.schema.json
      - Registry files live under: governance/registries/<basename>.json

    Example pairs:
      - governance/schemas/contracts.schema.json
        ↔ governance/registries/contracts.json

      - governance/schemas/upgrade_paths.schema.json
        ↔ governance/registries/upgrade_paths.json
    """
    root = _repo_root()
    schemas_dir = root / SCHEMAS_DIR_REL
    registries_dir = root / REGISTRIES_DIR_REL

    if not schemas_dir.is_dir():
        raise AssertionError(
            f"Governance schemas directory not found at {SCHEMAS_DIR_REL!s} "
            f"(resolved: {schemas_dir})"
        )
    if not registries_dir.is_dir():
        raise AssertionError(
            f"Governance registries directory not found at {REGISTRIES_DIR_REL!s} "
            f"(resolved: {registries_dir})"
        )

    pairs: List[Tuple[Path, Path]] = []

    for schema_path in schemas_dir.glob("*.schema.json"):
        if not schema_path.is_file():
            continue

        base_name = schema_path.name.replace(".schema.json", "")
        registry_path = registries_dir / f"{base_name}.json"

        if registry_path.is_file():
            pairs.append(
                (
                    schema_path.relative_to(root),
                    registry_path.relative_to(root),
                )
            )

    # Stable ordering for parametrization
    pairs.sort(key=lambda t: (str(t[0]), str(t[1])))
    return pairs


REGISTRY_SCHEMA_PAIRS: List[Tuple[Path, Path]] = _discover_registry_schema_pairs()


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
def test_governance_registry_schemas_discovered() -> None:
    """
    Sanity check that we actually discovered at least one registry+schema pair.

    If not, we skip the rest of the tests with a clear message.
    """
    if not REGISTRY_SCHEMA_PAIRS:
        pytest.skip(
            "No governance registry/schema pairs found under "
            f"{SCHEMAS_DIR_REL}/ and {REGISTRIES_DIR_REL}/"
        )


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
@pytest.mark.parametrize(
    "schema_rel,registry_rel",
    REGISTRY_SCHEMA_PAIRS,
    ids=lambda t: f"{t[0]} -> {t[1]}",
)
def test_governance_registries_validate_against_schemas(
    schema_rel: Path, registry_rel: Path
) -> None:
    """
    Validate governance registries like:

      - governance/registries/contracts.json
      - governance/registries/upgrade_paths.json
      - ...

    against their corresponding JSON Schemas under governance/schemas/*.schema.json.

    The convention is: <name>.schema.json ↔ <name>.json
    """
    root = _repo_root()

    schema_path = root / schema_rel
    registry_path = root / registry_rel

    assert schema_path.is_file(), f"Schema file missing: {schema_rel}"
    assert registry_path.is_file(), f"Registry file missing: {registry_rel}"

    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    with registry_path.open("r", encoding="utf-8") as f:
        registry_data = json.load(f)

    Validator = jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
    Validator.check_schema(schema)
    validator = Validator(schema)

    try:
        validator.validate(registry_data)
    except jsonschema_exceptions.ValidationError as exc:  # type: ignore[misc]
        pytest.fail(
            f"Governance registry {registry_rel} failed validation against schema "
            f"{schema_rel}:\n{exc}"
        )
