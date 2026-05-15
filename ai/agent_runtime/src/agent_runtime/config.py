"""Hierarchical YAML + environment config loader.

Design goals:

- **Strict.** Every config file is validated against a known schema as it
  loads. Unknown keys are surfaced as ConfigError. Type mismatches do not
  silently coerce.
- **Hierarchical.** Configs compose via simple includes: a file can declare
  `inherits: ../other.yaml` and the result is a deep merge. The current
  Animica AI stack does not yet need includes but the resolver supports
  them so downstream extensions (e.g. per-tier overrides) stay clean.
- **Env interpolation.** `${ANIMICA_REPO_ROOT}` and `${HOME}` style refs
  are substituted from the process environment after a strict allowlist
  check. Unknown env vars raise ConfigError (no silent empty-string).
- **Placeholder detection.** Base-model ids matching configurable patterns
  are rejected in strict modes. Prevents `lite` runs from masquerading as
  `full`.
- **Snapshotting.** Every run writes a frozen snapshot of the effective
  config under `runs/<run_id>/_pipeline/config.snapshot.yaml`. The snapshot
  is the authoritative record for reproducibility.

Note on imports: this module deliberately depends only on stdlib + PyYAML.
It must not import other agent_runtime modules so it can be re-used
unchanged from `flagship_agent`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from agent_runtime.errors import ConfigError

# --------------------------------------------------------------------------- #
# Repo-root resolution                                                        #
# --------------------------------------------------------------------------- #

_REPO_ROOT_ENV = "ANIMICA_REPO_ROOT"
_REPO_MARKERS = (
    ("python", "animica", "__init__.py"),
    ("ai", "configs", "pipeline.yaml"),
)


def resolve_repo_root(start: Optional[Path] = None) -> Path:
    """Resolve the Animica monorepo root.

    Priority:
      1. ``$ANIMICA_REPO_ROOT`` if set and points at a directory that
         contains any of ``_REPO_MARKERS``.
      2. Walk up from ``start`` (default: this file) until a marker hits.

    Raises ConfigError if neither path resolves.
    """
    override = os.environ.get(_REPO_ROOT_ENV)
    if override:
        candidate = Path(override).expanduser().resolve()
        if any((candidate.joinpath(*m).exists()) for m in _REPO_MARKERS):
            return candidate
        # $ANIMICA_REPO_ROOT is set but doesn't have the source markers.
        # Fall through to the wheel-bundled fallback below instead of raising
        # — pip-only installs commonly export ANIMICA_REPO_ROOT to a workspace
        # dir that's NOT a source checkout, and `animica chat` should still
        # work in that case (configs come from the wheel data).

    here = (start or Path(__file__)).resolve()
    for ancestor in (here, *here.parents):
        if any((ancestor.joinpath(*m).exists()) for m in _REPO_MARKERS):
            return ancestor

    # Pip-installed fallback: the animica wheel bundles `ai/configs/` under
    # `animica/_data/ai/configs/`. Treat the animica package's `_data/` dir
    # as a virtual repo root so `animica chat` and friends work without
    # the source tree on disk. Training stages that genuinely need the
    # source tree (compose files, .env, Dockerfiles) still raise — they
    # check for their specific files explicitly.
    try:
        import animica   # type: ignore
        wheel_root = Path(animica.__file__).resolve().parent / "_data"
        if (wheel_root / "ai" / "configs" / "pipeline.yaml").exists():
            return wheel_root
    except Exception:    # noqa: BLE001
        pass

    raise ConfigError(
        "Could not auto-resolve the Animica repo root.",
        hint=f"Set {_REPO_ROOT_ENV}=/path/to/animica.",
    )


# --------------------------------------------------------------------------- #
# Env interpolation                                                           #
# --------------------------------------------------------------------------- #

# Pattern matching ${VAR} interpolations. We intentionally do not support the
# bash ${VAR:-default} form — defaults belong in the YAML, not in env.
_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# Variables permitted in YAML interpolations. The loader rejects ${X} if X
# is not in this allowlist (defense against accidental leakage of secret
# env vars into config snapshots).
_INTERPOLATION_ALLOWLIST = frozenset({
    "ANIMICA_REPO_ROOT",
    "ANIMICA_NETWORK",
    "ANIMICA_DATA_DIR",
    "HOME",
    "USER",
    "FLAGSHIP_MODE",
    "FLAGSHIP_TIERS",
    "FLAGSHIP_RESUME",
    "FLAGSHIP_STAGES",
    "FLAGSHIP_ALLOW_CPU_REAL",
})


def _interpolate_env(value: str, *, env: Mapping[str, str],
                     where: str) -> str:
    """Substitute ${VAR} occurrences in value from env.

    Unknown vars raise; missing vars raise (no silent empty-string).
    """
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in _INTERPOLATION_ALLOWLIST:
            raise ConfigError(
                f"refused interpolation of disallowed env var ${{{name}}} in "
                f"{where}",
                hint=("add the variable to "
                      "agent_runtime.config._INTERPOLATION_ALLOWLIST if it is "
                      "legitimately needed"),
            )
        if name not in env:
            raise ConfigError(
                f"interpolation ${{{name}}} in {where} requires env var "
                f"{name!r} to be set",
            )
        return env[name]

    return _ENV_PATTERN.sub(repl, value)


def _walk_interpolate(node: Any, *, env: Mapping[str, str],
                      path: str) -> Any:
    if isinstance(node, str):
        return _interpolate_env(node, env=env, where=path)
    if isinstance(node, list):
        return [_walk_interpolate(v, env=env, path=f"{path}[{i}]")
                for i, v in enumerate(node)]
    if isinstance(node, dict):
        return {k: _walk_interpolate(v, env=env, path=f"{path}.{k}")
                for k, v in node.items()}
    return node


# --------------------------------------------------------------------------- #
# YAML loading + include resolution                                            #
# --------------------------------------------------------------------------- #

def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse YAML in {path}: {exc}") from exc


def _deep_merge(base: Any, overlay: Any, *, path: str = "") -> Any:
    """Merge overlay onto base. dict-vs-dict recurses; lists/scalars replace.

    Lists explicitly DO NOT concatenate — assigning a new list overrides the
    base in full. Keeps semantics predictable for operators.
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for k, v in overlay.items():
            if k in out:
                out[k] = _deep_merge(out[k], v, path=f"{path}.{k}")
            else:
                out[k] = v
        return out
    return overlay


def _resolve_inherits(doc: Any, *, anchor: Path, env: Mapping[str, str],
                      visited: Optional[set[Path]] = None) -> Any:
    """If doc declares `inherits: <relative-path>`, recursively merge the
    referenced file under doc."""
    if not isinstance(doc, dict):
        return doc
    if "inherits" not in doc:
        return doc
    inherits = doc.pop("inherits")
    if not isinstance(inherits, (str, list)):
        raise ConfigError(
            f"'inherits' must be a path or list of paths in {anchor}",
        )
    visited = visited or set()
    visited.add(anchor.resolve())
    parents: list[Any] = []
    for spec in ([inherits] if isinstance(inherits, str) else inherits):
        spec_str = _interpolate_env(spec, env=env,
                                    where=f"{anchor}::inherits")
        parent_path = (anchor.parent / spec_str).resolve()
        if parent_path in visited:
            raise ConfigError(
                f"circular inherits in {anchor} -> {parent_path}",
            )
        parent_doc = _load_yaml(parent_path)
        parent_doc = _resolve_inherits(parent_doc, anchor=parent_path,
                                       env=env, visited=visited)
        parents.append(parent_doc)
    merged: Any = {}
    for p in parents:
        merged = _deep_merge(merged, p, path=str(anchor))
    merged = _deep_merge(merged, doc, path=str(anchor))
    return merged


# --------------------------------------------------------------------------- #
# Schema validation                                                            #
# --------------------------------------------------------------------------- #

_REQUIRED_TOP_KEYS = {
    "pipeline.yaml":      {"run", "mode", "stages", "stage_selection",
                           "logging"},
    "training.yaml":      {"base_model", "modes", "cpt", "sft", "honesty"},
    "datasets.yaml":      {"inventory", "classification", "corpora", "dedupe",
                           "splits"},
    "eval.yaml":          {"suites", "gates", "critique", "benchmark"},
    "integration.yaml":   {"aicf", "ipfs", "agent_runtime", "animica_agent",
                           "telemetry"},
    "safety.yaml":        {"secret_scan", "validators", "risk_tiers"},
    "retrieval.yaml":     {"lexical", "dense", "inference", "citations"},
    "graph.yaml":         {"nodes", "edges", "out", "inference_boost"},
    "planning.yaml":      {"views", "planner", "synthesis_budgets"},
    "licenses.yaml":      {"repo_default", "allow", "restrict", "deny",
                           "detection"},
    "experts.yaml":       {"experts", "runtime_routing"},
    "incidents.yaml":     {"packs", "synthesis"},
    "model_catalog.yaml": {"tiers", "train_tiers", "routing", "propagation"},
}


def _validate_top_keys(name: str, doc: Any) -> None:
    if name not in _REQUIRED_TOP_KEYS:
        return
    if not isinstance(doc, dict):
        raise ConfigError(f"{name} must be a YAML mapping at top level")
    required = _REQUIRED_TOP_KEYS[name]
    missing = sorted(required - set(doc))
    if missing:
        raise ConfigError(
            f"{name} is missing required keys: {missing}",
            detail={"required": sorted(required), "present": sorted(doc)},
        )


_PLACEHOLDER_FIELDS = (
    ("training.yaml", ("base_model", "id")),
)


def _check_placeholders(name: str, doc: Any, *, mode: str) -> None:
    """In `full` mode, scan known model-id fields against the rejection
    patterns declared inside training.yaml itself."""
    if mode != "full" or name != "training.yaml":
        return
    try:
        reject_patterns = doc["base_model"]["placeholders_reject"]
        loaded_id = doc["base_model"]["id"]
    except (KeyError, TypeError):
        return
    for pattern in reject_patterns:
        if re.search(pattern, loaded_id):
            raise ConfigError(
                f"base_model.id={loaded_id!r} matches placeholder pattern "
                f"{pattern!r}; refusing strict full mode.",
                hint="Set training.yaml::base_model.id to a real model, or "
                     "set FLAGSHIP_MODE=lite / simulate.",
            )


# --------------------------------------------------------------------------- #
# Public config object                                                         #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Config:
    """Loaded + validated configs, keyed by file name (without directory).

    Access via attribute (`cfg.pipeline`) or dict (`cfg["pipeline.yaml"]`).
    Both return the underlying mapping.
    """

    docs: Mapping[str, Mapping[str, Any]]
    repo_root: Path
    config_dir: Path
    # Effective run mode (simulate / lite / full). Plumbed from FLAGSHIP_MODE
    # (or pipeline.yaml::mode.default).
    mode: str
    # The interpolation environment used at load time; preserved so snapshots
    # show exactly what env state produced the snapshot.
    env_snapshot: Mapping[str, str]

    def __getitem__(self, key: str) -> Mapping[str, Any]:
        return self.docs[key]

    def __getattr__(self, name: str) -> Mapping[str, Any]:
        key = f"{name}.yaml"
        if key in self.docs:
            return self.docs[key]
        raise AttributeError(name)

    def get(self, key: str, default: Any = None) -> Any:
        return self.docs.get(key, default)

    def to_snapshot_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "repo_root": str(self.repo_root),
            "config_dir": str(self.config_dir),
            "mode": self.mode,
            "env_snapshot": dict(self.env_snapshot),
            "docs": {name: doc for name, doc in self.docs.items()},
        }


# --------------------------------------------------------------------------- #
# Load entrypoints                                                            #
# --------------------------------------------------------------------------- #

_DEFAULT_FILES: tuple[str, ...] = (
    "pipeline.yaml",
    "training.yaml",
    "datasets.yaml",
    "eval.yaml",
    "integration.yaml",
    "safety.yaml",
    "retrieval.yaml",
    "graph.yaml",
    "planning.yaml",
    "licenses.yaml",
    "experts.yaml",
    "incidents.yaml",
    "model_catalog.yaml",
)


def _effective_mode(env: Mapping[str, str],
                    pipeline_doc: Mapping[str, Any]) -> str:
    requested = env.get("FLAGSHIP_MODE")
    if not requested:
        try:
            requested = str(pipeline_doc["mode"]["default"])
        except (KeyError, TypeError) as exc:
            raise ConfigError(
                "pipeline.yaml::mode.default missing and FLAGSHIP_MODE unset",
            ) from exc
    if requested not in {"simulate", "lite", "full"}:
        raise ConfigError(
            f"FLAGSHIP_MODE must be one of simulate/lite/full, got {requested!r}",
        )
    return requested


def load_config(config_dir: Optional[Path] = None,
                *,
                files: Sequence[str] = _DEFAULT_FILES,
                env: Optional[Mapping[str, str]] = None,
                strict: bool = True) -> Config:
    """Load all configured YAML files and return a frozen Config.

    Parameters
    ----------
    config_dir : Path, optional
        Directory containing the YAMLs. Defaults to
        ``<repo_root>/ai/configs``.
    files : sequence of str
        File names to load. Each is validated against its known schema.
    env : mapping, optional
        Env used for interpolation. Defaults to ``os.environ``.
    strict : bool
        When true (default), placeholder detection and required-key checks
        raise. Set false only in tests.
    """
    repo_root = resolve_repo_root()
    cfg_dir = config_dir or (repo_root / "ai" / "configs")
    if not cfg_dir.is_dir():
        raise ConfigError(f"config directory not found: {cfg_dir}")
    eff_env = dict(env if env is not None else os.environ)
    # Inject the auto-resolved repo root so YAML interpolations like
    # ${ANIMICA_REPO_ROOT} succeed without the operator having to set the
    # env var explicitly. Operator override (env already populated) wins.
    eff_env.setdefault("ANIMICA_REPO_ROOT", str(repo_root))

    docs: dict[str, Mapping[str, Any]] = {}
    for name in files:
        path = cfg_dir / name
        raw = _load_yaml(path)
        resolved = _resolve_inherits(raw, anchor=path, env=eff_env)
        interpolated = _walk_interpolate(resolved, env=eff_env,
                                         path=str(path))
        if strict:
            _validate_top_keys(name, interpolated)
        docs[name] = interpolated

    mode = _effective_mode(eff_env, docs.get("pipeline.yaml", {}))

    if strict:
        for name in files:
            _check_placeholders(name, docs[name], mode=mode)

    return Config(
        docs=docs,
        repo_root=repo_root,
        config_dir=cfg_dir,
        mode=mode,
        env_snapshot={k: v for k, v in eff_env.items()
                       if k in _INTERPOLATION_ALLOWLIST},
    )


def load_run_config(run_dir: Path, *, config_dir: Optional[Path] = None,
                    env: Optional[Mapping[str, str]] = None) -> Config:
    """Load config and snapshot it under ``<run_dir>/_pipeline/config.snapshot.yaml``.

    The snapshot is what stages should reload from on resume (so a mid-run
    edit to ai/configs/*.yaml does not silently change behavior between
    stages). Callers that already have a snapshot file should pass
    ``config_dir=<run_dir>/_pipeline/configs/`` to load it directly.
    """
    cfg = load_config(config_dir=config_dir, env=env)
    snapshot_dir = run_dir / "_pipeline"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / "config.snapshot.yaml"
    with snapshot_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg.to_snapshot_dict(), fh,
                       sort_keys=False, default_flow_style=False)
    # Also dump as JSON for easier machine reads.
    (snapshot_dir / "config.snapshot.json").write_text(
        json.dumps(cfg.to_snapshot_dict(), indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return cfg


# --------------------------------------------------------------------------- #
# CLI helper                                                                  #
# --------------------------------------------------------------------------- #

def _main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m agent_runtime.config [--check]` — validate without running."""
    argv = list(argv if argv is not None else sys.argv[1:])
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc.render(), file=sys.stderr)
        return 2
    print(f"OK: {len(cfg.docs)} configs loaded from {cfg.config_dir}")
    print(f"    repo_root = {cfg.repo_root}")
    print(f"    mode      = {cfg.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
