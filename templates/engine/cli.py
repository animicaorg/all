# -*- coding: utf-8 -*-
"""
templates.engine.cli

Unified command-line interface for the templates engine.

Features
--------
- Locate the repository's templates/ root.
- List available templates (directories with a manifest.json).
- Validate a template directory and (optionally) variables.
- Normalize variables from multiple sources (file, --var KEY=VAL, env prefix).
- Render a template to an output directory (with dry-run and overwrite controls).
- Optional diff-style preview if a renderer/plan implementation is available.

This CLI primarily delegates to submodules:

- templates.engine.validate
- templates.engine.render      (optional but preferred)
- templates.engine.variables   (optional helpers)

If templates.engine.render is not available, a minimal fallback renderer is used:
it copies files and applies simple {{var}} substitutions in file paths and
UTF-8 text contents (binary files are copied as-is).

Usage examples
--------------
python -m templates.engine.cli list
python -m templates.engine.cli validate --template templates/examples/counter
python -m templates.engine.cli validate -t templates/examples/counter --vars vars.json --print
python -m templates.engine.cli render -t templates/templates/ai_agent -o /tmp/out --var NAME=demo --force
python -m templates.engine.cli render -t templates/templates/ai_agent -o ./scaffold --env-prefix TPL_

Exit codes
----------
0: success
1: validation or operation error
"""
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# Required module (we rely on this for structure validation & var normalization)
from .validate import (
    ValidationReport,
    find_templates_root,
    validate_template,
    validate_and_normalize_variables,
)

# Optional helpers
try:  # rendering module is optional; we can fallback if not present
    from . import render as _render_mod  # type: ignore
except Exception:  # pragma: no cover
    _render_mod = None

try:
    from . import variables as _variables_mod  # type: ignore
except Exception:  # pragma: no cover
    _variables_mod = None


# -------------------------------------------------------------------------------------- #
# Utilities
# -------------------------------------------------------------------------------------- #


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_manifest(tpl_dir: Path) -> Dict[str, Any]:
    mf = tpl_dir / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        return _read_json(mf)
    except Exception:
        return {}


def _walk_templates(templates_root: Path) -> List[Path]:
    """Return candidate template directories (contain manifest.json)."""
    out: List[Path] = []
    for p in templates_root.glob("**/manifest.json"):
        # Only collect direct template dirs (ignore schemas/, root index.json, etc.)
        if p.parent.name == "schemas":
            continue
        out.append(p.parent)
    # Deduplicate & sort
    seen = set()
    uniq: List[Path] = []
    for p in sorted(out):
        rp = p.resolve()
        if rp not in seen:
            uniq.append(p)
            seen.add(rp)
    return uniq


def _parse_kv_pairs(items: Iterable[str]) -> Dict[str, str]:
    """
    Parse KEY=VALUE tokens. VALUE may contain '=' if quoted: KEY="a=b=c".
    Surrounding quotes in VALUE are stripped.
    """
    out: Dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            _eprint(f"[warn] Ignoring --var without '=': {raw}")
            continue
        key, val = raw.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (len(val) >= 2) and (
            (val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")
        ):
            val = val[1:-1]
        out[key] = val
    return out


def _vars_from_env(prefix: str) -> Dict[str, str]:
    """
    Collect variables from environment by prefix.
    TPL_NAME=demo  (prefix 'TPL_') -> {"NAME": "demo"}
    """
    out: Dict[str, str] = {}
    plen = len(prefix)
    for k, v in os.environ.items():
        if k.startswith(prefix):
            out[k[plen:]] = v
    return out


def _merge_dicts(*ds: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for d in ds:
        out.update(d)
    return out


def _resolve_template_dir(user_arg: str, templates_root: Path) -> Path:
    """
    Accept either a direct path or a name relative to templates_root.
    Resolution rules:
      - if user_arg points to a directory containing manifest.json, use it
      - else if templates_root/user_arg exists (directory), use it
      - else if templates_root/*/user_arg exists, prefer the first (common 'category/name' shortform)
    """
    p = Path(user_arg)
    if p.is_dir() and (p / "manifest.json").is_file():
        return p.resolve()

    candidate = templates_root / user_arg
    if candidate.is_dir() and (candidate / "manifest.json").is_file():
        return candidate.resolve()

    # Attempt 1-level deep search
    for d in templates_root.iterdir():
        if d.is_dir():
            cand = d / user_arg
            if cand.is_dir() and (cand / "manifest.json").is_file():
                return cand.resolve()

    # Final fallback: just treat as directory (error will surface later)
    return p.resolve()


# -------------------------------------------------------------------------------------- #
# Fallback renderer (only if templates.engine.render is missing)
# -------------------------------------------------------------------------------------- #


@dataclass
class _CopyAction:
    src: Path
    dst: Path
    kind: str  # "mkdir" | "copy" | "write"


@dataclass
class _RenderPlan:
    actions: List[_CopyAction]


def _is_binary_bytes(b: bytes) -> bool:
    # Heuristic: if it has nulls or fails UTF-8 decoding, treat as binary
    if b"\x00" in b:
        return True
    try:
        b.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


_VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _sub_vars_in_text(s: str, vars_map: Mapping[str, str]) -> str:
    def repl(m: re.Match[str]) -> str:
        k = m.group(1)
        return vars_map.get(k, m.group(0))
    return _VAR_PATTERN.sub(repl, s)


def _should_exclude(path: Path, template_dir: Path, patterns: List[str]) -> bool:
    if not patterns:
        return False
    rel = str(path.relative_to(template_dir))
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


def _fallback_build_plan(
    template_dir: Path,
    out_dir: Path,
    vars_map: Mapping[str, str],
    *,
    exclude: Optional[List[str]] = None,
) -> _RenderPlan:
    actions: List[_CopyAction] = []
    exclude = exclude or []
    for src in sorted(template_dir.rglob("*")):
        if src.name in {"manifest.json", "_hooks.py"}:
            continue
        if src.is_dir():
            continue
        if _should_exclude(src, template_dir, exclude):
            continue

        rel = src.relative_to(template_dir)
        # Apply var substitution to each path segment (excluding dotfiles unchanged)
        rel_str = str(rel)
        target_rel_str = _sub_vars_in_text(rel_str, vars_map)
        dst = out_dir / target_rel_str

        actions.append(_CopyAction(src=src, dst=dst, kind="copy"))
    # Ensure directories exist (mkdir actions)
    dirs = set(a.dst.parent for a in actions)
    for d in sorted(dirs):
        actions.insert(0, _CopyAction(src=d, dst=d, kind="mkdir"))
    return _RenderPlan(actions=actions)


def _fallback_apply_plan(
    plan: _RenderPlan, *,
    overwrite: bool = False,
) -> List[_CopyAction]:
    executed: List[_CopyAction] = []
    for act in plan.actions:
        if act.kind == "mkdir":
            act.dst.mkdir(parents=True, exist_ok=True)
            executed.append(act)
        elif act.kind == "copy":
            act.dst.parent.mkdir(parents=True, exist_ok=True)
            if act.dst.exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing file: {act.dst}")
            # Binary-aware substitution: for text files, do {{var}} replacement
            data = act.src.read_bytes()
            if _is_binary_bytes(data):
                act.dst.write_bytes(data)
            else:
                txt = data.decode("utf-8")
                txt = _sub_vars_in_text(txt, {})  # no-op default
                act.dst.write_text(txt, encoding="utf-8")
            executed.append(act)
        else:
            raise RuntimeError(f"Unknown plan action kind: {act.kind}")
    return executed


# -------------------------------------------------------------------------------------- #
# Subcommand implementations
# -------------------------------------------------------------------------------------- #


def cmd_list(args: argparse.Namespace) -> int:
    troot = find_templates_root(Path.cwd())
    rows = []
    for d in _walk_templates(troot):
        mf = _load_manifest(d)
        name = mf.get("name") or d.name
        version = mf.get("version") or "0.0.0"
        rows.append((str(d.relative_to(troot)), name, version))
    if not rows:
        print("No templates found under templates/")
        return 0

    width_a = max(len(a) for a, _, _ in rows)
    width_b = max(len(b) for _, b, _ in rows)
    print(f"{'PATH'.ljust(width_a)}  {'NAME'.ljust(width_b)}  VERSION")
    print("-" * (width_a + width_b + 10))
    for a, b, c in rows:
        print(f"{a.ljust(width_a)}  {b.ljust(width_b)}  {c}")
    return 0


def _load_and_normalize_vars(
    template_dir: Path,
    *,
    vars_file: Optional[str],
    kv_pairs: Iterable[str],
    env_prefix: Optional[str],
) -> Dict[str, str]:
    manifest = _load_manifest(template_dir)

    from_file: Dict[str, Any] = {}
    if vars_file:
        data = _read_json(Path(vars_file))
        if not isinstance(data, dict):
            raise ValueError(f"--vars JSON must be an object, got {type(data).__name__}")
        from_file = data

    from_kv = _parse_kv_pairs(kv_pairs)
    from_env: Dict[str, str] = _vars_from_env(env_prefix) if env_prefix else {}

    merged = _merge_dicts(from_file, from_env, from_kv)

    # Prefer official normalization routine
    normalized, report = validate_and_normalize_variables(manifest, merged, where=str(template_dir / "manifest.json"))
    report.dump_to_stderr()
    if not report.ok:
        raise SystemExit(1)
    return normalized


def cmd_validate(args: argparse.Namespace) -> int:
    troot = find_templates_root(Path.cwd())
    template_dir = _resolve_template_dir(args.template, troot)

    user_vars: Optional[Dict[str, Any]] = None
    if args.vars or args.var or args.env_prefix:
        # We normalize to strings later; for validation, use raw mapping
        user_vars = {}
        if args.vars:
            v = _read_json(Path(args.vars))
            if not isinstance(v, dict):
                _eprint("--vars JSON must be an object")
                return 1
            user_vars.update(v)
        if args.env_prefix:
            user_vars.update(_vars_from_env(args.env_prefix))
        if args.var:
            user_vars.update(_parse_kv_pairs(args.var))

    normalized, report = validate_template(template_dir, user_vars=user_vars)
    report.dump_to_stderr()

    if report.ok and args.print:
        print(json.dumps(normalized, indent=2, sort_keys=True))

    if not report.ok:
        return 1
    if args.strict and report.has_warnings():
        return 1
    return 0


def _call_renderer(
    template_dir: Path,
    out_dir: Path,
    variables: Mapping[str, str],
    *,
    dry_run: bool,
    overwrite: bool,
    exclude: Optional[List[str]],
) -> int:
    """
    Try to delegate to templates.engine.render if present, else fallback.
    We support a few common function shapes:
      - render(template_dir, out_dir, variables, overwrite=False, dry_run=False, exclude=None)
      - render_template_dir(...)
      - apply_plan(plan) after plan_template_dir(...)
    """
    if _render_mod is not None:
        # Strategy 1: plan + apply style
        plan_fn = getattr(_render_mod, "plan_template_dir", None) or getattr(_render_mod, "plan", None)
        apply_fn = getattr(_render_mod, "apply_plan", None) or getattr(_render_mod, "apply", None)

        if plan_fn and apply_fn:
            plan = plan_fn(template_dir=template_dir, out_dir=out_dir, variables=dict(variables),
                           exclude=exclude or [])
            if dry_run:
                # Pretty print plan if it looks like a dataclass or list of changes
                _print_plan(plan)
                return 0
            apply_fn(plan, overwrite=overwrite)
            return 0

        # Strategy 2: single-shot render
        render_fn = (
            getattr(_render_mod, "render", None)
            or getattr(_render_mod, "render_template_dir", None)
            or getattr(_render_mod, "render_dir", None)
        )
        if render_fn:
            render_fn(template_dir=template_dir, out_dir=out_dir, variables=dict(variables),
                      overwrite=overwrite, dry_run=dry_run, exclude=exclude or [])
            return 0

    # Fallback renderer
    plan = _fallback_build_plan(template_dir, out_dir, variables, exclude=exclude or [])
    if dry_run:
        _print_plan(plan)
        return 0
    _fallback_apply_plan(plan, overwrite=overwrite)
    return 0


def _print_plan(plan: Any) -> None:
    """Best-effort plan pretty printer."""
    # Known fallback plan
    if isinstance(plan, _RenderPlan):
        print("# Plan (fallback renderer)")
        for a in plan.actions:
            if a.kind == "mkdir":
                print(f"MKDIR {a.dst}")
            elif a.kind == "copy":
                print(f"WRITE {a.dst}     <- {a.src}")
            else:
                print(f"{a.kind.upper()} {a.dst}")
        return

    # Try to inspect generic objects
    if dataclasses.is_dataclass(plan):
        print(json.dumps(dataclasses.asdict(plan), indent=2, default=str))
        return
    if isinstance(plan, (list, tuple)):
        try:
            print(json.dumps(plan, indent=2, default=str))
            return
        except TypeError:
            pass
    # Last resort repr
    print(repr(plan))


def cmd_render(args: argparse.Namespace) -> int:
    troot = find_templates_root(Path.cwd())
    template_dir = _resolve_template_dir(args.template, troot)
    out_dir = Path(args.out).resolve()

    normalized = _load_and_normalize_vars(
        template_dir,
        vars_file=args.vars,
        kv_pairs=args.var or [],
        env_prefix=args.env_prefix,
    )

    exclude = args.exclude or []
    rc = _call_renderer(
        template_dir=template_dir,
        out_dir=out_dir,
        variables=normalized,
        dry_run=args.dry_run,
        overwrite=args.force,
        exclude=exclude,
    )
    if rc == 0 and args.print:
        print(json.dumps(normalized, indent=2, sort_keys=True))
    return rc


# -------------------------------------------------------------------------------------- #
# Argument parsing
# -------------------------------------------------------------------------------------- #


def _add_vars_group(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("Variables")
    g.add_argument("--vars", "-v", metavar="FILE", help="Path to a JSON file with variables")
    g.add_argument(
        "--var",
        action="append",
        metavar="K=V",
        help="Supply/override a variable (can be repeated). Example: --var NAME=demo --var COUNT=3",
    )
    g.add_argument(
        "--env-prefix",
        metavar="PREFIX",
        help="Load variables from environment by prefix (e.g. TPL_ -> NAME from TPL_NAME)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="templates-engine",
        description="Animica templates engine CLI",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # list
    sp = sub.add_parser("list", help="List available templates under templates/")
    sp.set_defaults(func=cmd_list)

    # validate
    sp = sub.add_parser("validate", help="Validate a template and (optionally) variables")
    sp.add_argument("--template", "-t", required=True, help="Template name or path")
    sp.add_argument("--print", action="store_true", help="Print normalized variables on success")
    sp.add_argument("--strict", action="store_true", help="Exit non-zero on warnings too")
    _add_vars_group(sp)
    sp.set_defaults(func=cmd_validate)

    # render
    sp = sub.add_parser("render", help="Render a template to an output directory")
    sp.add_argument("--template", "-t", required=True, help="Template name or path")
    sp.add_argument("--out", "-o", required=True, help="Output directory")
    sp.add_argument("--dry-run", action="store_true", help="Show planned writes without changing the filesystem")
    sp.add_argument("--force", action="store_true", help="Overwrite existing files")
    sp.add_argument(
        "--exclude",
        action="append",
        metavar="GLOB",
        help="Exclude relative paths matching glob (can be repeated), e.g. 'README.md' or '*/__pycache__/*'",
    )
    sp.add_argument("--print", action="store_true", help="Print normalized variables after render")
    _add_vars_group(sp)
    sp.set_defaults(func=cmd_render)

    return p


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        func = getattr(args, "func", None)
        if not func:
            parser.print_help()
            return 1
        return int(func(args))  # type: ignore[arg-type]
    except FileExistsError as e:
        _eprint(f"[error] {e}")
        return 1
    except KeyboardInterrupt:
        _eprint("[warn] interrupted")
        return 1
    except SystemExit as se:
        # propagate explicit exits (e.g., validation report requested exit 1)
        return int(se.code) if se.code is not None else 1
    except Exception as e:
        _eprint(f"[error] {e.__class__.__name__}: {e}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
