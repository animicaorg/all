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


# We are testing **docs-level package manifests** for backward compatibility.
PACKAGE_MANIFEST_SCHEMA_REL = Path("docs/schemas/manifest.schema.json")
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
      - schema_version: "1.0" / "1.1" (string)
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


def _discover_example_paths_with_versions() -> List[Tuple[Path, str, Dict]]:
    """
    Find all docs-level package manifest examples with their schema_version and data.

    Returns a list of tuples:
        (relative_path, schema_version, manifest_data)
    """
    root = _repo_root()
    seen: set[Path] = set()
    results: List[Tuple[Path, str, Dict]] = []

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

            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            sv = data.get("schema_version")
            if not isinstance(sv, str):
                # If it "looks like" a manifest but has no proper schema_version, skip
                # (test_manifest_examples handles validity separately).
                continue

            results.append((rel, sv, data))

    # Deterministic order for stable parametrization
    results.sort(key=lambda t: (t[1], str(t[0])))
    return results


EXAMPLES_WITH_VERSIONS: List[Tuple[Path, str, Dict]] = _discover_example_paths_with_versions()


def _collect_schema_versions() -> List[str]:
    return sorted({sv for _, sv, _ in EXAMPLES_WITH_VERSIONS})


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
def test_manifest_backward_compat_has_multiple_versions() -> None:
    """
    Sanity check that we actually *have* multiple schema_version values.

    If not, this test becomes a no-op and is skipped with a clear message.
    """
    if not EXAMPLES_WITH_VERSIONS:
        pytest.skip("No docs-level package manifest examples found")

    schema_versions = _collect_schema_versions()
    if len(schema_versions) <= 1:
        pytest.skip(
            f"Only one manifest schema_version present ({schema_versions}); "
            "no backward-compat surface to test yet"
        )


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
@pytest.mark.parametrize(
    "rel_path,schema_version,manifest",
    EXAMPLES_WITH_VERSIONS,
    ids=lambda t: f"{t[0]}@{t[1]}",
)
def test_manifest_backward_compat_examples(rel_path: Path, schema_version: str, manifest: Dict) -> None:
    """
    Ensure older manifest schema_version examples still validate under the
    current docs-level package manifest schema, *or* are explicitly marked
    as intentionally invalid for backward-compat reasons.

    Convention for marking expected invalid examples:
      - Add `"x_backward_compat_expected_invalid": true` to the manifest.
      - Optionally add `"x_backward_compat_expected_reason": "<short explanation>"`.

    If that flag is absent, the manifest is expected to validate successfully.
    """
    if not EXAMPLES_WITH_VERSIONS:
        pytest.skip("No docs-level package manifest examples found")

    root = _repo_root()
    schema = _load_package_manifest_schema()

    Validator = jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
    Validator.check_schema(schema)
    validator = Validator(schema)

    target_path = root / rel_path
    assert target_path.is_file(), f"Example package manifest file missing: {rel_path}"

    expected_invalid = bool(manifest.get("x_backward_compat_expected_invalid"))
    expected_reason = manifest.get("x_backward_compat_expected_reason")

    try:
        validator.validate(manifest)
        validation_error = None
    except jsonschema_exceptions.ValidationError as exc:  # type: ignore[misc]
        validation_error = exc

    if expected_invalid:
        # We *expect* this manifest to fail validation under the current schema.
        assert (
            validation_error is not None
        ), f"{rel_path} is marked x_backward_compat_expected_invalid but validated successfully"

        if expected_reason:
            # Optional: check that the reason string is at least mentioned somewhere.
            err_text = str(validation_error)
            assert expected_reason in err_text, (
                f"{rel_path} expected invalid reason '{expected_reason}' "
                f"not found in validation error:\n{err_text}"
            )
    else:
        # Not marked as intentionally invalid → must validate successfully.
        if validation_error is not None:
            pytest.fail(
                f"Manifest {rel_path} (schema_version={schema_version}) "
                f"failed validation unexpectedly:\n{validation_error}"
            )
