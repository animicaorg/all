# -*- coding: utf-8 -*-
"""
templates.engine.render

High-level rendering helper and richer CLI on top of TemplateEngine.

Why this file?
--------------
`templates/engine/__init__.py` ships the core `TemplateEngine` and a minimal CLI.
This module adds a slightly friendlier interface for ops/dev workflows:

- Load variables from multiple sources with well-defined precedence:
    1) --var key=value (highest)
    2) --vars-file path.json|.env|.yaml (can repeat; later files override earlier)
    3) Environment variables with a prefix (default: TPL_), e.g. TPL_NAME=demo
    4) Template-local defaults (_vars.json) handled by TemplateEngine

- List registered templates from templates/index.json
- Dry-run planning with a neat, aligned table
- Optional placeholder discovery across the template tree
- Tiny Python API: `render(...)` and `plan(...)` convenience wrappers

Zero third-party dependencies. YAML loading is optional (only if PyYAML is available).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

# Import the engine & helpers from the sibling module
from . import TemplateEngine, RenderPlan, RenderAction, discover_placeholders

# ------------------------------- Public API ---------------------------------


def plan(
    template: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    *,
    vars: Optional[Mapping[str, object]] = None,
    overwrite: bool = False,
    include_hidden: bool = False,
    templates_root: str | os.PathLike[str] = "templates",
) -> RenderPlan:
    """
    Compute a dry-run plan describing what would be written.
    """
    engine = TemplateEngine(templates_root)
    return engine.plan(
        template_spec=str(template),
        out_dir=str(out_dir),
        variables=vars or {},
        overwrite=overwrite,
        include_hidden=include_hidden,
    )


def render(
    template: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    *,
    vars: Optional[Mapping[str, object]] = None,
    overwrite: bool = False,
    include_hidden: bool = False,
    templates_root: str | os.PathLike[str] = "templates",
    verbose: bool = True,
) -> RenderPlan:
    """
    Execute rendering using TemplateEngine. Returns the actual plan executed.
    """
    engine = TemplateEngine(templates_root)
    return engine.render(
        template_spec=str(template),
        out_dir=str(out_dir),
        variables=vars or {},
        overwrite=overwrite,
        include_hidden=include_hidden,
        verbose=verbose,
    )


# ------------------------------ CLI Helpers ---------------------------------


def _parse_kv(items: Sequence[str]) -> Dict[str, str]:
    """
    Parse --var key=value parameters (repeatable). Allows empty string values: key=
    """
    m: Dict[str, str] = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"--var expects key=value, got: {it!r}")
        k, v = it.split("=", 1)
        if not k:
            raise SystemExit("--var key cannot be empty")
        m[k] = v
    return m


def _load_vars_file(path: Path) -> Dict[str, str]:
    """
    Load a variables file. Supported formats:
      - .json : a JSON object of string/number/bool values
      - .env  : KEY=VALUE lines, '#' comments allowed
      - .yml/.yaml : if PyYAML available (optional)
      - .txt : same as .env

    Values are converted to strings for deterministic substitution.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Vars file not found: {path}")

    suf = path.suffix.lower()
    if suf == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return {str(k): _coerce_str(v) for k, v in data.items()}

    if suf in (".env", ".txt"):
        out: Dict[str, str] = {}
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                raise ValueError(f"{path}:{i}: expected KEY=VALUE")
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    if suf in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore
        except Exception:
            raise RuntimeError(
                f"{path} looks like YAML but PyYAML is not installed. "
                "Use JSON or .env format instead."
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a mapping at the top level")
        return {str(k): _coerce_str(v) for k, v in data.items()}

    raise ValueError(f"Unsupported vars file type: {path.name}")


def _load_vars_files(paths: Sequence[Path]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for p in paths:
        merged.update(_load_vars_file(p))
    return merged


def _load_vars_env(prefix: Optional[str]) -> Dict[str, str]:
    """
    Collect environment variables with a given prefix, e.g. TPL_NAME=demo → {"NAME": "demo"}.
    If prefix is None or empty string, returns {}.
    """
    if not prefix:
        return {}
    plen = len(prefix)
    out: Dict[str, str] = {}
    for k, v in os.environ.items():
        if k.startswith(prefix):
            key = k[plen:]
            if key:
                out[key] = v
    return out


def _merge_vars(*maps: Mapping[str, object]) -> Dict[str, str]:
    """
    Merge mappings left→right (rightmost wins), converting values to strings.
    """
    out: Dict[str, str] = {}
    for m in maps:
        for k, v in m.items():
            out[k] = _coerce_str(v)
    return out


def _coerce_str(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _print_registry(engine: TemplateEngine) -> None:
    reg = engine.load_registry()
    if not reg:
        print("(no templates/index.json found)")
        return
    print("Registered templates:")
    # Stable order
    for name in sorted(reg.keys()):
        ent = reg[name]
        path = ent.get("path", "")
        desc = ent.get("description", "")
        print(f"  - {name:20}  {path:30}  {desc}")


def _collect_placeholders_in_tree(root: Path, text_exts: Sequence[str]) -> List[str]:
    """
    Iterate files under root, heuristically load UTF-8 text, and gather placeholders.
    """
    names: set[str] = set()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in ("_vars.json", "_ignore.txt"):
            # defaults/ignore are not scanned for placeholders
            continue
        if p.suffix.lower() in text_exts:
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for nm in discover_placeholders(txt):
                names.add(nm)
        else:
            # Try sniffing a small sample
            try:
                sample = p.read_bytes()[:2048]
                sample.decode("utf-8")
                txt = p.read_text(encoding="utf-8")
                for nm in discover_placeholders(txt):
                    names.add(nm)
            except Exception:
                pass
    return sorted(names)


def _print_plan(plan: RenderPlan) -> None:
    writes = [a for a in plan.actions if a.will_write]
    skips = [a for a in plan.actions if not a.will_write]
    # column widths
    reason_w = max((len(a.reason) for a in plan.actions), default=6)
    src_w = min(60, max((len(str(a.src.relative_to(plan.template_root))) for a in plan.actions), default=3))
    print("\nPlan:")
    for a in plan.actions:
        src_rel = str(a.src.relative_to(plan.template_root))
        dst_rel = str(a.dst.relative_to(plan.out_dir))
        print(f"{'[WRITE]' if a.will_write else '[SKIP ]'} {a.reason:<{reason_w}}  {src_rel:<{src_w}}  ->  {dst_rel}")
    print(f"\nSummary: writes={len(writes)}  skips={len(skips)}")


# ------------------------------- CLI Entry ----------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m templates.engine.render",
        description="Render a template folder using simple placeholder substitution.",
    )
    ap.add_argument("--template", "-t", help="Template name (from index.json) or path to template directory")
    ap.add_argument("--out", "-o", help="Destination directory (will be created if missing)")
    ap.add_argument("--var", action="append", default=[], help="Variable assignment key=value (repeatable)")
    ap.add_argument("--vars-file", action="append", default=[], help="Load variables from a file (.json, .env, .yaml) — can repeat")
    ap.add_argument("--env-prefix", default="TPL_", help="Collect env vars with this prefix (e.g. TPL_NAME=demo). Set to '' to disable.")
    ap.add_argument("--overwrite", action="store_true", help="Allow overwriting existing files")
    ap.add_argument("--include-hidden", action="store_true", help="Include dotfiles/directories")
    ap.add_argument("--dry-run", action="store_true", help="Plan only; do not write")
    ap.add_argument("--quiet", action="store_true", help="Less verbose output")

    # Discovery / utilities
    ap.add_argument("--list", action="store_true", help="List templates from templates/index.json and exit")
    ap.add_argument("--placeholders", action="store_true", help="Scan template for placeholders and print them")
    ap.add_argument("--templates-root", default="templates", help="Root directory hosting templates/ (default: ./templates)")

    args = ap.parse_args(argv)

    engine = TemplateEngine(args.templates_root)

    if args.list:
        _print_registry(engine)
        return 0

    if not args.template:
        ap.error("--template is required unless --list is used")
    if not args.out:
        ap.error("--out is required")

    # Collect variables with precedence: env < vars-files < --var
    env_map = _load_vars_env(args.env_prefix) if args.env_prefix is not None else {}
    file_maps = _load_vars_files([Path(p) for p in args.vars_file]) if args.vars_file else {}
    cli_map = _parse_kv(args.var)

    vars_all = _merge_vars(env_map, file_maps, cli_map)

    if args.placeholders:
        # Resolve template path and scan tree
        template_dir = engine.resolve_template_path(args.template)
        names = _collect_placeholders_in_tree(template_dir, engine.text_file_extensions)
        if names:
            print("Placeholders discovered in template:")
            for nm in names:
                v = vars_all.get(nm)
                hint = f" (provided: {v})" if v is not None else ""
                print(f"  - {nm}{hint}")
        else:
            print("(no placeholders found or non-text template)")
        # Not an exit condition; we can still render after showing placeholders

    if args.dry_run:
        plan_obj = engine.plan(
            args.template,
            args.out,
            variables=vars_all,
            overwrite=args.overwrite,
            include_hidden=args.include_hidden,
        )
        _print_plan(plan_obj)
        return 0

    # Render
    plan_obj = engine.render(
        args.template,
        args.out,
        variables=vars_all,
        overwrite=args.overwrite,
        include_hidden=args.include_hidden,
        verbose=not args.quiet,
    )
    if not args.quiet:
        writes = sum(1 for a in plan_obj.actions if a.will_write)
        skips = sum(1 for a in plan_obj.actions if not a.will_write)
        print(f"\nDone. Wrote {writes} files, skipped {skips}.")
    return 0


# ------------------------------ Module Guard --------------------------------

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
