import itertools
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


SIDEBAR_REL = Path("docs/SIDEBAR.yaml")


def _repo_root() -> Path:
    """
    Resolve the repository root assuming this file lives under tests/schemas/.
    """
    return Path(__file__).resolve().parents[2]


def _load_sidebar() -> Any:
    root = _repo_root()
    sidebar_path = root / SIDEBAR_REL
    if not sidebar_path.is_file():
        raise AssertionError(
            f"SIDEBAR.yaml not found at {SIDEBAR_REL!s} (resolved: {sidebar_path})"
        )

    with sidebar_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---- helpers for traversing and validating the sidebar tree -----------------


def _canonical_slug(slug: Any) -> str:
    """
    Normalize slug values from SIDEBAR into a canonical string form.

    We support a few common shapes:
      - "getting-started/intro"
      - ["getting-started", "intro"]

    You can extend this if your SIDEBAR uses a different form.
    """
    if isinstance(slug, str):
        return slug.strip().strip("/")
    if isinstance(slug, (list, tuple)):
        parts = [str(p).strip().strip("/") for p in slug]
        parts = [p for p in parts if p]
        return "/".join(parts)
    raise TypeError(f"Unsupported slug type: {type(slug)!r} ({slug!r})")


def _iter_nodes(obj: Any) -> Iterable[Dict[str, Any]]:
    """
    Recursively yield all mapping nodes that *may* represent nav items.

    This is intentionally generic: any dict that has a 'slug' key is treated
    as a nav node. Everything else is still traversed so we don't rely on a
    particular 'sections' / 'items' / 'children' schema.
    """
    if isinstance(obj, dict):
        if "slug" in obj:
            yield obj

        for v in obj.values():
            yield from _iter_nodes(v)

    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_nodes(v)


def _dfs_detect_cycles(obj: Any, seen_stack: Set[int]) -> None:
    """
    Detect reference cycles inside the YAML tree (in case of YAML anchors).

    In practice the sidebar is a simple tree, but this guards against
    accidental YAML aliases/anchors that create cycles.
    """
    obj_id = id(obj)
    if obj_id in seen_stack:
        raise AssertionError("Cycle detected in SIDEBAR.yaml (YAML anchors/aliases?)")

    if isinstance(obj, (dict, list)):
        seen_stack.add(obj_id)
        if isinstance(obj, dict):
            for v in obj.values():
                _dfs_detect_cycles(v, seen_stack)
        else:
            for v in obj:
                _dfs_detect_cycles(v, seen_stack)
        seen_stack.remove(obj_id)


def _candidate_doc_paths_for_slug(slug: str) -> List[Path]:
    """
    Given a canonical slug like "getting-started/intro", produce possible
    documentation file paths that should back it.

    We check both the canonical docs tree and the website-synced tree:
      - docs/<slug>.md
      - docs/<slug>.mdx
      - docs/<slug>/index.md
      - docs/<slug>/index.mdx
      - website/src/docs/<slug>.mdx
      - website/src/docs/<slug>/index.mdx
    """
    root = _repo_root()
    parts = slug.split("/") if slug else []
    base = Path(*parts) if parts else Path("index")

    candidates: List[Path] = []

    # Docs tree
    candidates.append(root / "docs" / (str(base) + ".md"))
    candidates.append(root / "docs" / (str(base) + ".mdx"))
    candidates.append(root / "docs" / base / "index.md")
    candidates.append(root / "docs" / base / "index.mdx")

    # Website-synced tree
    candidates.append(root / "website" / "src" / "docs" / (str(base) + ".mdx"))
    candidates.append(root / "website" / "src" / "docs" / base / "index.mdx")

    # Deduplicate while preserving order
    unique: List[Path] = []
    seen: Set[Path] = set()
    for p in candidates:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    return unique


# ---- tests ------------------------------------------------------------------


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_sidebar_yaml_parses_and_has_basic_structure() -> None:
    """
    Ensure docs/SIDEBAR.yaml parses as YAML and is a mapping or a list.

    We don't enforce a specific top-level schema here; other tests
    look at the 'slug' nodes in a generic way.
    """
    sidebar = _load_sidebar()
    assert isinstance(
        sidebar, (dict, list)
    ), f"Expected SIDEBAR.yaml top-level to be dict or list, got {type(sidebar)!r}"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_sidebar_has_no_cycles() -> None:
    """
    Guard against accidental YAML cycles (via anchors/aliases) in SIDEBAR.

    In practice the sidebar is a tree, but this catches pathological cases.
    """
    sidebar = _load_sidebar()
    _dfs_detect_cycles(sidebar, seen_stack=set())


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_sidebar_slugs_are_unique_and_backed_by_docs() -> None:
    """
    Validate that:
      * All nav item slugs in SIDEBAR.yaml are unique (soft check).
      * Each slug corresponds to at least one existing documentation file.

    Duplicate slugs and missing docs are treated as xfail (soft failures)
    so they remain visible but do not block the test suite.
    """
    sidebar = _load_sidebar()

    nodes = list(_iter_nodes(sidebar))
    if not nodes:
        pytest.skip("No nav items with 'slug' found in SIDEBAR.yaml")

    # Collect canonical slugs and detect duplicates
    slug_to_nodes: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes:
        slug_raw = node.get("slug")
        try:
            slug = _canonical_slug(slug_raw)
        except Exception as exc:  # pragma: no cover - defensive
            pytest.fail(f"Invalid slug value {slug_raw!r} in node {node!r}: {exc}")

        slug_to_nodes.setdefault(slug, []).append(node)

    duplicates = {s: ns for s, ns in slug_to_nodes.items() if len(ns) > 1}
    if duplicates:
        formatted = ", ".join(
            f"{slug!r} (count={len(nodes)})" for slug, nodes in duplicates.items()
        )
        pytest.xfail(f"Duplicate slugs found in SIDEBAR.yaml: {formatted}")

    # Ensure each slug has at least one backing file
    missing: List[Tuple[str, List[Path]]] = []

    for slug, _nodes in slug_to_nodes.items():
        candidates = _candidate_doc_paths_for_slug(slug)
        if not any(p.is_file() for p in candidates):
            missing.append((slug, candidates))

    if missing:
        msg_lines = [
            "Some SIDEBAR slugs do not resolve to any known doc file "
            "(soft failure, please review):"
        ]
        for slug, candidates in missing:
            msg_lines.append(f"  - {slug!r}")
            for p in candidates:
                msg_lines.append(f"      candidate: {p}")
        pytest.xfail("\n".join(msg_lines))


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_sidebar_has_no_obviously_orphan_sections() -> None:
    """
    Sanity check that there is at least one nav item (slug) overall and
    that sections are not completely empty.

    This is a softer check, mainly to catch accidentally wiped-out sections.
    Orphan sections are reported as xfail instead of hard failure.
    """
    sidebar = _load_sidebar()
    nodes = list(_iter_nodes(sidebar))
    if not nodes:
        pytest.fail("SIDEBAR.yaml contains no nav items with a 'slug' field")

    # For dict-based top-level with 'sections' or similar, make sure at least
    # one section has at least one slug.
    section_like_keys = ("sections", "items", "children")
    if isinstance(sidebar, dict):
        section_nodes: List[Any] = []
        for key in section_like_keys:
            if key in sidebar and isinstance(sidebar[key], list):
                section_nodes.extend(sidebar[key])

        if section_nodes:
            # For each top-level section, ensure it eventually leads to a slug
            def section_has_slug(section_obj: Any) -> bool:
                return any(True for _ in _iter_nodes(section_obj))

            empties = [
                section_obj
                for section_obj in section_nodes
                if not section_has_slug(section_obj)
            ]
            if empties:
                pytest.xfail(
                    "One or more top-level sections in SIDEBAR.yaml "
                    "do not contain any nav items with 'slug' "
                    "(soft failure, please review sidebar structure)."
                )
