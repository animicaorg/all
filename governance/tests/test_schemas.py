# pytest: governance/tests/test_schemas.py
"""
jsonschema validates all schemas & examples.

This suite checks:
1) All JSON Schemas in governance/schemas are themselves valid
   against the Draft 2020-12 metaschema.
2) All example artifacts (Markdown proposals with YAML front-matter,
   ballots, tallies) validate against the appropriate schema.

Run:
  pytest -q governance/tests/test_schemas.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

import pytest

try:
    import yaml  # PyYAML
except Exception as e:  # pragma: no cover
    pytest.skip("PyYAML not installed (pip install pyyaml)", allow_module_level=True)

try:
    from jsonschema import Draft202012Validator
except Exception as e:  # pragma: no cover
    pytest.skip(
        "jsonschema not installed (pip install jsonschema)", allow_module_level=True
    )


ROOT = Path(__file__).resolve().parents[2]  # repo root
SCHEMAS_DIR = ROOT / "governance" / "schemas"
EXAMPLES_DIR = ROOT / "governance" / "examples"

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*$", re.DOTALL | re.MULTILINE)

# Map proposal 'type' (from front-matter) to schema filename
SCHEMA_NAME_MAP: Dict[str, str] = {
    "upgrade": "upgrade.schema.json",
    "param_change": "param_change.schema.json",
    "params": "param_change.schema.json",
    "pq_rotation": "pq_rotation.schema.json",
    "pq": "pq_rotation.schema.json",
    "ballot": "ballot.schema.json",
    "tally": "tally.schema.json",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(name: str) -> Dict[str, Any]:
    p = SCHEMAS_DIR / name
    assert p.exists(), f"Schema not found: {p}"
    return read_json(p)


def metaschema_check(schema: Dict[str, Any]) -> None:
    # Raises jsonschema.exceptions.SchemaError if invalid
    Draft202012Validator.check_schema(schema)


def validator_for(schema: Dict[str, Any]) -> Draft202012Validator:
    # base_uri allows local $ref resolution relative to SCHEMAS_DIR
    base_uri = f"file://{SCHEMAS_DIR.as_posix()}/"
    return Draft202012Validator(
        schema, resolver=Draft202012Validator.RESOLVER.resolver.bind(base_uri)
    )


def extract_front_matter(md_path: Path) -> Dict[str, Any]:
    text = md_path.read_text(encoding="utf-8")
    m = FRONT_MATTER_RE.search(text)
    assert m, f"No YAML front-matter found in {md_path}"
    return yaml.safe_load(m.group(1)) or {}


def discover_schema_files() -> Iterable[Path]:
    return sorted(SCHEMAS_DIR.glob("*.schema.json"))


def discover_markdown_examples() -> Iterable[Path]:
    # All .md under examples/ (including nested) that contain YAML front-matter
    return sorted(EXAMPLES_DIR.rglob("*.md"))


def test_all_schemas_are_valid_metaschema() -> None:
    schema_files = list(discover_schema_files())
    assert schema_files, f"No schemas found in {SCHEMAS_DIR}"
    for sf in schema_files:
        schema = read_json(sf)
        metaschema_check(schema)  # will raise if invalid


@pytest.mark.parametrize("md_file", discover_markdown_examples())
def test_markdown_examples_validate(md_file: Path) -> None:
    # Load payload from YAML front-matter and pick schema by 'type'
    payload = extract_front_matter(md_file)

    # Accept both top-level and nested 'proposal' objects
    p = payload
    if "proposal" in payload and isinstance(payload["proposal"], dict):
        p = payload["proposal"]

    t = str(p.get("type", "")).strip().lower()
    assert t, f"'type' missing in front-matter of {md_file}"
    schema_name = SCHEMA_NAME_MAP.get(t)
    assert schema_name, f"Unknown proposal type '{t}' in {md_file}"

    schema = load_schema(schema_name)
    validator = validator_for(schema)

    errors = sorted(
        validator.iter_errors(p if t not in ("ballot", "tally") else payload),
        key=lambda e: (list(e.path), e.message),
    )
    assert not errors, "Schema errors:\n" + "\n".join(
        f"- {md_file}:{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
        for e in errors
    )


def test_ballot_example_validates() -> None:
    ballot_path = EXAMPLES_DIR / "ballots" / "sample_ballot.json"
    assert ballot_path.exists(), f"Missing example ballot: {ballot_path}"
    ballot = read_json(ballot_path)
    schema = load_schema("ballot.schema.json")
    validator = validator_for(schema)
    errors = list(validator.iter_errors(ballot))
    assert not errors, "Ballot schema errors:\n" + "\n".join(
        f"- {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors
    )


def test_tally_example_validates() -> None:
    tally_path = EXAMPLES_DIR / "tallies" / "sample_tally.json"
    assert tally_path.exists(), f"Missing example tally: {tally_path}"
    tally = read_json(tally_path)
    schema = load_schema("tally.schema.json")
    validator = validator_for(schema)
    errors = list(validator.iter_errors(tally))
    assert not errors, "Tally schema errors:\n" + "\n".join(
        f"- {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors
    )
