from pathlib import Path
from typing import Any, Dict, List

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


MODULE_OWNERS_REL = Path("governance/registries/module_owners.yaml")


def _repo_root() -> Path:
    """
    Resolve the repository root assuming this file lives under tests/schemas/.
    """
    return Path(__file__).resolve().parents[2]


def _load_module_owners() -> Dict[str, Any]:
    root = _repo_root()
    path = root / MODULE_OWNERS_REL
    if not path.is_file():
        raise AssertionError(
            f"module_owners.yaml not found at {MODULE_OWNERS_REL!s} (resolved: {path})"
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise AssertionError(
            f"Expected module_owners.yaml to parse as mapping, got {type(data)!r}"
        )
    return data


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_module_owners_basic_shape() -> None:
    """
    Basic structural sanity:

      - Top-level keys exist: version, defaults, modules.
      - 'defaults' is a mapping.
      - 'modules' is a non-empty list of mappings.
    """
    data = _load_module_owners()

    for key in ("version", "defaults", "modules"):
        assert key in data, f"Missing top-level key {key!r} in module_owners.yaml"

    assert isinstance(
        data["defaults"], dict
    ), f"'defaults' must be a mapping, got {type(data['defaults'])!r}"

    modules = data["modules"]
    assert isinstance(
        modules, list
    ), f"'modules' must be a list, got {type(modules)!r}"
    assert modules, "'modules' list must not be empty"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_module_owners_defaults_required_fields() -> None:
    """
    Validate `defaults` section:

      - reviewers_min: int >= 1
      - approvers_min: int >= 1
    """
    data = _load_module_owners()
    defaults = data["defaults"]

    for key in ("reviewers_min", "approvers_min"):
        assert key in defaults, f"'defaults' is missing required key {key!r}"
        value = defaults[key]
        assert isinstance(
            value, int
        ), f"'defaults.{key}' must be int, got {type(value)!r}"
        assert value >= 1, f"'defaults.{key}' must be >= 1, got {value}"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_module_entries_have_required_fields_and_paths() -> None:
    """
    Each module entry must have:

      - id: non-empty string
      - name: non-empty string
      - paths: non-empty list of non-empty strings
      - maintainers: non-empty list of non-empty strings
      - reviewers: non-empty list of non-empty strings
      - backups: non-empty list of non-empty strings
    """
    data = _load_module_owners()
    modules = data["modules"]

    def _assert_str_list(name: str, value: Any) -> List[str]:
        assert isinstance(value, list), f"'{name}' must be a list, got {type(value)!r}"
        assert value, f"'{name}' list must not be empty"
        for idx, item in enumerate(value):
            assert isinstance(
                item, str
            ), f"'{name}[{idx}]' must be a string, got {type(item)!r}"
            assert item.strip(), f"'{name}[{idx}]' must not be empty/whitespace"
        return value  # type: ignore[return-value]

    for idx, module in enumerate(modules):
        assert isinstance(
            module, dict
        ), f"modules[{idx}] must be a mapping, got {type(module)!r}"

        for key in ("id", "name", "paths", "maintainers", "reviewers", "backups"):
            assert (
                key in module
            ), f"modules[{idx}] is missing required key {key!r}"

        mid = module["id"]
        name = module["name"]
        assert isinstance(mid, str) and mid.strip(), f"modules[{idx}].id must be a non-empty string"
        assert isinstance(name, str) and name.strip(), f"modules[{idx}].name must be a non-empty string"

        paths = _assert_str_list(f"modules[{idx}].paths", module["paths"])
        _assert_str_list(f"modules[{idx}].maintainers", module["maintainers"])
        _assert_str_list(f"modules[{idx}].reviewers", module["reviewers"])
        _assert_str_list(f"modules[{idx}].backups", module["backups"])

        # Path pattern sanity: no obvious junk; must not contain whitespace.
        for p in paths:
            assert " " not in p, f"modules[{idx}].paths entry {p!r} must not contain spaces"
            assert p, f"modules[{idx}].paths contains empty string"
            # Common convention: module patterns should include at least '/' or '**'
            assert (
                "/" in p
            ), f"modules[{idx}].paths entry {p!r} should look like a path pattern (contain '/')"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_codeowners_shape_and_owners_required_if_present() -> None:
    """
    If the optional 'codeowners' synthesis block is present, validate that:

      - It is a list of mappings.
      - Each entry has:
          * pattern: non-empty string (preferably starting with '/')
          * owners: non-empty list of non-empty strings
    """
    data = _load_module_owners()

    if "codeowners" not in data:
        pytest.skip("'codeowners' block not present in module_owners.yaml")

    codeowners = data["codeowners"]
    assert isinstance(
        codeowners, list
    ), f"'codeowners' must be a list, got {type(codeowners)!r}"
    assert codeowners, "'codeowners' list must not be empty"

    for idx, entry in enumerate(codeowners):
        assert isinstance(
            entry, dict
        ), f"codeowners[{idx}] must be a mapping, got {type(entry)!r}"
        for key in ("pattern", "owners"):
            assert (
                key in entry
            ), f"codeowners[{idx}] is missing required key {key!r}"

        pattern = entry["pattern"]
        owners = entry["owners"]

        assert isinstance(
            pattern, str
        ), f"codeowners[{idx}].pattern must be a string, got {type(pattern)!r}"
        assert pattern.strip(), f"codeowners[{idx}].pattern must not be empty/whitespace"
        # Convention: CODEOWNERS patterns normally start with '/'
        assert pattern.startswith(
            "/"
        ), f"codeowners[{idx}].pattern {pattern!r} should start with '/'"

        assert isinstance(
            owners, list
        ), f"codeowners[{idx}].owners must be a list, got {type(owners)!r}"
        assert owners, f"codeowners[{idx}].owners list must not be empty"
        for j, owner in enumerate(owners):
            assert isinstance(
                owner, str
            ), f"codeowners[{idx}].owners[{j}] must be a string, got {type(owner)!r}"
            assert owner.strip(), (
                f"codeowners[{idx}].owners[{j}] must not be empty/whitespace"
            )
