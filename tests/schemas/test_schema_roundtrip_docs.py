import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

try:
    import jsonschema
    from jsonschema import exceptions as jsonschema_exceptions
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]
    jsonschema_exceptions = None  # type: ignore[assignment]


DOCS_REL = Path("docs")
# Directories where JSON Schemas are expected to live
SCHEMA_DIRS = [
    Path("docs/schemas"),
    Path("governance/schemas"),
]

# Regex to find schema paths referenced inside docs:
# Examples that should be matched:
#   - docs/schemas/abi.schema.json
#   - docs/schemas/manifest.schema.json
#   - schemas/ai_attestation.schema.json
#   - governance/schemas/contracts.schema.json
SCHEMA_PATH_PATTERN = re.compile(
    r"(?P<path>(?:docs/|governance/)?schemas/[A-Za-z0-9_\-./]+\.schema\.json)"
)

# Schema files that are currently known to be malformed / WIP.
# We treat these as expected failures (xfail) rather than hard errors.
KNOWN_BROKEN_SCHEMAS: Set[Path] = {
    Path("docs/schemas/manifest.schema.json"),
}

# Schema refs that are mentioned in docs but deliberately not implemented yet.
# These will be reported as xfail instead of hard failing the suite.
KNOWN_MISSING_SCHEMA_REFS: Set[Path] = {
    Path("docs/schemas/ai_attestation.schema.json"),
    Path("docs/schemas/quantum_attestation.schema.json"),
    Path("docs/schemas/retrieval_api.schema.json"),
    Path("docs/schemas/syscalls_abi.schema.json"),
    Path("docs/schemas/zk_verify.schema.json"),
}


def _repo_root() -> Path:
    """
    Resolve the repository root assuming this file lives under tests/schemas/.
    """
    return Path(__file__).resolve().parents[2]


def _iter_doc_files() -> List[Path]:
    """
    Yield all text-like documentation files under docs/ that might contain
    schema references.

    We include:
      - .md
      - .mdx
      - .markdown
      - .txt
      - .yaml / .yml (for config-style docs that still reference schemas)
    """
    root = _repo_root()
    docs_root = root / DOCS_REL

    if not docs_root.is_dir():
        raise AssertionError(
            f"Docs directory not found at {DOCS_REL!s} (resolved: {docs_root})"
        )

    exts = {".md", ".mdx", ".markdown", ".txt", ".yaml", ".yml"}
    results: List[Path] = []

    for path in docs_root.rglob("*"):
        if path.is_file() and path.suffix in exts:
            results.append(path)

    return results


def _discover_schema_refs_in_docs() -> Set[Path]:
    """
    Scan docs for schema path references and return a set of repo-relative paths.
    """
    root = _repo_root()
    refs: Set[Path] = set()

    for doc_path in _iter_doc_files():
        try:
            text = doc_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # In case of odd encodings, just skip this file
            continue

        for match in SCHEMA_PATH_PATTERN.finditer(text):
            raw = match.group("path")
            # Normalize things like "./docs/schemas/..." if they ever appear
            normalized = raw.lstrip("./")

            # Special case: "schemas/..." without prefix is treated as "docs/schemas/..."
            if normalized.startswith("schemas/"):
                normalized = "docs/" + normalized

            ref_path = root / normalized

            try:
                # Store as repo-relative for nicer test output
                rel = ref_path.relative_to(root)
            except ValueError:
                # If it somehow escapes the repo root, keep absolute
                rel = ref_path

            refs.add(rel)

    return refs


def _discover_all_schema_files() -> List[Path]:
    """
    Find all *.schema.json files under the known schema directories.

    Returns repo-relative paths.
    """
    root = _repo_root()
    results: List[Path] = []

    for rel_dir in SCHEMA_DIRS:
        schema_dir = root / rel_dir
        if not schema_dir.is_dir():
            # Not fatal; just skip missing schema dirs
            continue

        for path in schema_dir.rglob("*.schema.json"):
            if path.is_file():
                results.append(path.relative_to(root))

    # Deterministic order
    results.sort()
    return results


SCHEMA_REFS_IN_DOCS: Set[Path] = _discover_schema_refs_in_docs()
ALL_SCHEMA_FILES: List[Path] = _discover_all_schema_files()


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
def test_all_schema_refs_in_docs_exist_on_disk() -> None:
    """
    Ensure all JSON Schema paths referenced in docs actually exist on disk.

    This catches broken links like pointing to docs/schemas/foo.schema.json
    that was renamed or deleted.

    For a small set of known-not-yet-implemented schemas, we report xfail
    instead of hard failing the suite.
    """
    root = _repo_root()

    if not SCHEMA_REFS_IN_DOCS:
        pytest.skip("No schema references found in docs (schemas/* .schema.json)")

    missing: List[Tuple[Path, List[Path]]] = []

    for rel_path in sorted(SCHEMA_REFS_IN_DOCS, key=str):
        # Primary candidate is the path as-normalized
        candidates: List[Path] = [root / rel_path]

        exists = any(p.is_file() for p in candidates)

        if not exists:
            missing.append((rel_path, candidates))

    if not missing:
        return

    # Partition into expected vs unexpected missing schema refs
    unexpected: List[Tuple[Path, List[Path]]] = []
    expected: List[Tuple[Path, List[Path]]] = []

    for rel, candidates in missing:
        if rel in KNOWN_MISSING_SCHEMA_REFS:
            expected.append((rel, candidates))
        else:
            unexpected.append((rel, candidates))

    if unexpected:
        lines = ["The following schema paths are referenced in docs but missing:"]
        for rel, candidates in unexpected:
            lines.append(f"  - {rel}")
            for c in candidates:
                lines.append(f"      tried: {c}")
        pytest.fail("\n".join(lines))

    # Only expected missing schemas → soft failure (xfail)
    lines = [
        "Some schema paths referenced in docs are known-not-yet-implemented "
        "(soft failure, please add these schemas when ready):"
    ]
    for rel, candidates in expected:
        lines.append(f"  - {rel}")
        for c in candidates:
            lines.append(f"      tried: {c}")
    pytest.xfail("\n".join(lines))


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
def test_all_schema_files_are_well_formed_and_checkable() -> None:
    """
    Ensure all discovered *.schema.json files under docs/governance schemas
    are:

      - Valid JSON
      - Pass jsonschema's check_schema (no broken $ref syntax)

    This does not guarantee external $refs resolve across files at runtime,
    but it catches obvious internal structural and ref errors.

    Known-broken schemas are reported as xfail rather than hard failures,
    so they stay visible but do not block the suite.
    """
    root = _repo_root()

    if not ALL_SCHEMA_FILES:
        pytest.skip(
            "No *.schema.json files found under configured schema dirs "
            f"{[str(d) for d in SCHEMA_DIRS]}"
        )

    unexpected_errors: Dict[Path, Exception] = {}
    expected_errors: Dict[Path, Exception] = {}

    for rel_path in ALL_SCHEMA_FILES:
        path = root / rel_path

        try:
            raw = path.read_text(encoding="utf-8")
            schema = json.loads(raw)
        except Exception as exc:
            if rel_path in KNOWN_BROKEN_SCHEMAS:
                expected_errors[rel_path] = exc
            else:
                unexpected_errors[rel_path] = exc
            continue

        try:
            Validator = jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
            Validator.check_schema(schema)
        except Exception as exc:
            if rel_path in KNOWN_BROKEN_SCHEMAS:
                expected_errors[rel_path] = exc
            else:
                unexpected_errors[rel_path] = exc

    if unexpected_errors:
        lines = ["Some schema files failed to load or check_schema:"]
        for rel_path, exc in unexpected_errors.items():
            lines.append(f"  - {rel_path}: {type(exc).__name__}: {exc}")
        pytest.fail("\n".join(lines))

    if expected_errors:
        lines = ["Some schema files are known-broken/WIP and failed check_schema:"]
        for rel_path, exc in expected_errors.items():
            lines.append(f"  - {rel_path}: {type(exc).__name__}: {exc}")
        pytest.xfail("\n".join(lines))


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
def test_all_schemas_referenced_somewhere() -> None:
    """
    Soft check: every schema file under docs/governance schemas should be
    referenced at least once in the docs, otherwise it might be dead/unused.

    This is a soft check: we *warn* via xfail if there are unused schemas
    rather than failing the build, to avoid blocking intentional additions.
    """
    if not ALL_SCHEMA_FILES:
        pytest.skip(
            "No *.schema.json files found under configured schema dirs "
            f"{[str(d) for d in SCHEMA_DIRS]}"
        )

    if not SCHEMA_REFS_IN_DOCS:
        pytest.skip("No schema references found in docs")

    unused = sorted(
        set(ALL_SCHEMA_FILES) - set(SCHEMA_REFS_IN_DOCS),
        key=str,
    )

    if unused:
        pytest.xfail(
            "Some schema files are not referenced in docs (could be OK but "
            "worth reviewing):\n" + "\n".join(f"  - {p}" for p in unused)
        )
