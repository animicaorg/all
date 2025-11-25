# -*- coding: utf-8 -*-
"""
Lightweight template engine for this repo's `templates/` directory.

Goals
-----
- Zero third-party deps (stdlib only).
- Predictable, deterministic rendering (stable file ordering).
- Safe by default (no overwrite unless explicitly requested).
- Flexible placeholders:
    - {{var}}          — common mustache-like
    - __VAR__          — screaming snake style
    - ${var} / $var    — shell-like (only ${..} is substituted inside text)

Features
--------
- Template discovery via templates/index.json (optional).
- Variable validation against templates/schemas/variables.schema.json (optional, shallow).
- Render file *paths* and *contents* with placeholder substitution.
- Binary-safe: non-UTF8 files are copied verbatim without substitution.
- Dry-run planning (see what would be written).
- Basic CLI: `python -m templates.engine --template <path|name> --out ./dst --var key=val`

Notes
-----
This module avoids heavy templating. If later you need Jinja2-like logic, add a separate
renderer module and keep this one backward compatible for simple, fast scaffolding.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "TemplateEngine",
    "RenderPlan",
    "RenderAction",
    "discover_placeholders",
    "substitute_placeholders",
]

RE_MUSTACHE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
RE_DOLLAR_BRACED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
RE_SCREAMING = re.compile(r"__([A-Z][A-Z0-9_]*)__")


@dataclass(frozen=True)
class RenderAction:
    src: Path
    dst: Path
    will_write: bool
    reason: str  # "create", "overwrite", "skip-exists", "skip-ignored", "noop-binary", etc.


@dataclass(frozen=True)
class RenderPlan:
    template_root: Path
    out_dir: Path
    actions: Tuple[RenderAction, ...]


class TemplateEngine:
    """
    A minimal renderer for project scaffolding.

    Directory layout conventions (recommended but not required):
      templates/
        index.json                        # optional registry
        schemas/variables.schema.json     # optional shallow validation
        <category>/<name>/                # template folders
          files...                        # arbitrary files with placeholders
          _ignore.txt                     # optional ignore globs (one per line)
          _vars.json                      # optional default vars for this template
    """

    def __init__(
        self,
        templates_root: Path | str = "templates",
        *,
        ignore_dirs: Sequence[str] = (".git", ".svn", ".hg", "node_modules", "__pycache__"),
        text_file_extensions: Sequence[str] = (
            ".md",
            ".txt",
            ".py",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cddl",
            ".c",
            ".h",
            ".rs",
            ".ts",
            ".tsx",
            ".js",
            ".mjs",
            ".jsx",
            ".css",
            ".scss",
            ".env",
            ".sh",
            ".service",
            ".j2",
        ),
    ) -> None:
        self.root = Path(templates_root).resolve()
        self.ignore_dirs = set(ignore_dirs)
        self.text_file_extensions = tuple(text_file_extensions)

    # ---------- Discovery & validation -------------------------------------

    def load_registry(self) -> Dict[str, Dict[str, str]]:
        """
        Load optional templates/index.json: { "templates": [{ "name": "...", "path": "..." }, ...] }
        Returns mapping name -> entry dict.
        """
        idx = self.root / "index.json"
        if not idx.is_file():
            return {}
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"Failed to parse {idx}: {exc}") from exc
        entries = {}
        for ent in data.get("templates", []):
            name = ent.get("name")
            if not isinstance(name, str):
                continue
            entries[name] = ent
        return entries

    def resolve_template_path(self, spec: str | os.PathLike[str]) -> Path:
        """
        Resolve `spec` either as a name in index.json or as a relative/absolute path.
        """
        p = Path(spec)
        if p.exists():
            return p.resolve()

        reg = self.load_registry()
        if spec in reg:
            rel = reg[spec].get("path") or spec
            return (self.root / rel).resolve()

        # Try common layouts: templates/<spec> or templates/*/<spec>
        direct = (self.root / spec).resolve()
        if direct.exists():
            return direct
        for sub in self.root.glob("*/"):
            cand = (sub / spec).resolve()
            if cand.exists():
                return cand

        raise FileNotFoundError(f"Template not found: {spec!r} (checked path and registry)")

    def load_template_defaults(self, template_dir: Path) -> Dict[str, str]:
        defaults: Dict[str, str] = {}
        f = template_dir / "_vars.json"
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(k, str) and isinstance(v, (str, int, float, bool)):
                            defaults[k] = str(v)
            except Exception:
                pass
        return defaults

    def _load_variables_schema(self) -> Optional[Dict[str, object]]:
        schema = self.root / "schemas" / "variables.schema.json"
        if schema.is_file():
            try:
                return json.loads(schema.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def validate_variables(self, variables: Mapping[str, str]) -> None:
        """
        Shallow validation against templates/schemas/variables.schema.json if present.
        Supports only 'required' and 'properties' with 'type' in {'string','number','boolean','integer'}.
        """
        schema = self._load_variables_schema()
        if not schema:
            return

        required = schema.get("required") or []
        if isinstance(required, list):
            for key in required:
                if key not in variables:
                    raise ValueError(f"Missing required variable: {key}")

        props = schema.get("properties") or {}
        if isinstance(props, dict):
            for k, rule in props.items():
                if k not in variables:
                    continue
                v = variables[k]
                if isinstance(rule, dict):
                    t = rule.get("type")
                    if t == "string" and not isinstance(v, str):
                        raise TypeError(f"Variable {k} must be string")
                    if t == "number" and not _is_number_like(v):
                        raise TypeError(f"Variable {k} must be number-like")
                    if t == "integer" and not _is_int_like(v):
                        raise TypeError(f"Variable {k} must be integer-like")
                    if t == "boolean" and not _is_bool_like(v):
                        raise TypeError(f"Variable {k} must be boolean-like")

    # ---------- Rendering ---------------------------------------------------

    def plan(
        self,
        template_spec: str | os.PathLike[str],
        out_dir: str | os.PathLike[str],
        variables: Mapping[str, object] | None = None,
        *,
        overwrite: bool = False,
        include_hidden: bool = False,
    ) -> RenderPlan:
        """
        Create a dry-run plan describing what would be written.
        """
        template_dir = self.resolve_template_path(template_spec)
        out = Path(out_dir).resolve()
        vars_all = {}
        vars_all.update({k: str(v) for k, v in self.load_template_defaults(template_dir).items()})
        if variables:
            vars_all.update({k: _coerce_var(v) for k, v in variables.items()})
        self.validate_variables(vars_all)

        ignores = self._load_ignores(template_dir)

        actions: List[RenderAction] = []
        for src in sorted(_iter_files(template_dir), key=lambda p: str(p)):
            # skip control files
            rel = src.relative_to(template_dir)
            if rel.parts and rel.parts[0].startswith("."):
                if not include_hidden:
                    actions.append(RenderAction(src, out / rel, False, "skip-hidden"))
                    continue
            if rel.name in ("_vars.json", "_ignore.txt"):
                actions.append(RenderAction(src, out / rel, False, "skip-control"))
                continue
            if self._is_ignored(rel, ignores):
                actions.append(RenderAction(src, out / rel, False, "skip-ignored"))
                continue

            # substitute in relative path (each part)
            dst_rel = Path("/".join(substitute_placeholders(part, vars_all) for part in rel.parts))
            dst = out / dst_rel

            if dst.exists() and not overwrite:
                actions.append(RenderAction(src, dst, False, "skip-exists"))
                continue

            # Decide if content substitution will occur (only for likely text files)
            reason = "create" if not dst.exists() else "overwrite"
            actions.append(RenderAction(src, dst, True, reason))

        return RenderPlan(template_root=template_dir, out_dir=out, actions=tuple(actions))

    def render(
        self,
        template_spec: str | os.PathLike[str],
        out_dir: str | os.PathLike[str],
        variables: Mapping[str, object] | None = None,
        *,
        overwrite: bool = False,
        include_hidden: bool = False,
        verbose: bool = True,
    ) -> RenderPlan:
        """
        Execute rendering according to a computed plan. Returns the actual plan executed.
        """
        plan = self.plan(
            template_spec,
            out_dir,
            variables=variables,
            overwrite=overwrite,
            include_hidden=include_hidden,
        )
        for act in plan.actions:
            if not act.will_write:
                if verbose:
                    print(f"[SKIP] {act.reason:12}  {act.src} -> {act.dst}")
                continue

            act.dst.parent.mkdir(parents=True, exist_ok=True)
            # Copy with substitution if text-like, else raw bytes
            if self._is_text_like(act.src):
                try:
                    content = act.src.read_text(encoding="utf-8")
                    rendered = substitute_placeholders(content, _vars_to_str_map(variables, self.load_template_defaults(plan.template_root)))
                    act.dst.write_text(rendered, encoding="utf-8", newline="\n")
                    if verbose:
                        print(f"[WRITE] {act.reason:12}  {act.dst} (text)")
                except UnicodeDecodeError:
                    # fall back to binary copy
                    act.dst.write_bytes(act.src.read_bytes())
                    if verbose:
                        print(f"[WRITE] {act.reason:12}  {act.dst} (binary-fallback)")
            else:
                act.dst.write_bytes(act.src.read_bytes())
                if verbose:
                    print(f"[WRITE] {act.reason:12}  {act.dst} (binary)")
        return plan

    # ---------- Internals ---------------------------------------------------

    def _is_text_like(self, path: Path) -> bool:
        if path.suffix.lower() in self.text_file_extensions:
            return True
        # Heuristic: try a small decode sniff
        try:
            sample = path.read_bytes()[:2048]
            sample.decode("utf-8")
            return True
        except Exception:
            return False

    def _load_ignores(self, template_dir: Path) -> Tuple[str, ...]:
        f = template_dir / "_ignore.txt"
        if not f.is_file():
            return tuple()
        lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()]
        return tuple(ln for ln in lines if ln and not ln.startswith("#"))

    def _is_ignored(self, rel: Path, ignores: Tuple[str, ...]) -> bool:
        s = str(rel).replace("\\", "/")
        for pat in ignores:
            # very small glob: suffix/prefix/contains
            if pat.endswith("/**"):
                base = pat[:-3]
                if s.startswith(base):
                    return True
            if pat.startswith("**/"):
                base = pat[3:]
                if s.endswith(base):
                    return True
            if "*" in pat:
                # translate '*' to '.*' for quick regex match
                rx = "^" + re.escape(pat).replace(r"\*", ".*") + "$"
                if re.match(rx, s):
                    return True
            if pat == s:
                return True
        return False


# ---------- Standalone helpers ----------------------------------------------

def substitute_placeholders(text: str, variables: Mapping[str, object]) -> str:
    """
    Replace {{var}}, ${var}, and __VAR__ placeholders with stringified values.
    Unknown placeholders are left intact (so templates remain readable).
    """
    # normalize mapping (case-sensitive for {{var}} and ${var}}, UPPER for __VAR__)
    strmap: Dict[str, str] = {k: str(v) for k, v in variables.items()}

    def _mustache_sub(m: re.Match[str]) -> str:
        key = m.group(1)
        return strmap.get(key, m.group(0))

    def _dollar_sub(m: re.Match[str]) -> str:
        key = m.group(1)
        return strmap.get(key, m.group(0))

    def _screaming_sub(m: re.Match[str]) -> str:
        key = m.group(1)
        return strmap.get(key, m.group(0))

    text = RE_MUSTACHE.sub(_mustache_sub, text)
    text = RE_DOLLAR_BRACED.sub(_dollar_sub, text)
    # For __VAR__, lookup prefers exact key first, then UPPER fallback
    def _screaming_sub_with_fallback(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in strmap:
            return strmap[key]
        if key.lower() in strmap:
            return strmap[key.lower()]
        return _screaming_sub(m)

    text = RE_SCREAMING.sub(_screaming_sub_with_fallback, text)
    return text


def discover_placeholders(text: str) -> List[str]:
    """
    Return list of placeholder variable names discovered in `text`.
    """
    names = set()
    for r in RE_MUSTACHE.findall(text):
        names.add(r)
    for r in RE_DOLLAR_BRACED.findall(text):
        names.add(r)
    for r in RE_SCREAMING.findall(text):
        names.add(r)
    return sorted(names)


def _iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def _is_number_like(v: object) -> bool:
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v))
        return True
    except Exception:
        return False


def _is_int_like(v: object) -> bool:
    if isinstance(v, int):
        return True
    try:
        int(str(v), 10)
        return True
    except Exception:
        return False


def _is_bool_like(v: object) -> bool:
    if isinstance(v, bool):
        return True
    s = str(v).strip().lower()
    return s in ("true", "false", "1", "0", "yes", "no", "on", "off")


def _coerce_var(v: object) -> str:
    # Normalize to string for stable substitution
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _vars_to_str_map(user_vars: Optional[Mapping[str, object]], defaults: Mapping[str, str]) -> Dict[str, str]:
    merged = dict(defaults)
    if user_vars:
        for k, v in user_vars.items():
            merged[k] = _coerce_var(v)
    # also add UPPERCASE mirror for __VAR__ convenience
    mirrored = dict(merged)
    for k, v in merged.items():
        mirrored[k.upper()] = v
    return mirrored


# ---------- CLI --------------------------------------------------------------

def _parse_vars(items: Sequence[str]) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"--var expects key=value, got: {it!r}")
        k, v = it.split("=", 1)
        m[k] = v
    return m


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m templates.engine",
        description="Render a template directory with simple placeholder substitution.",
    )
    ap.add_argument("--template", required=True, help="Template name (from index.json) or path to directory")
    ap.add_argument("--out", required=True, help="Destination directory")
    ap.add_argument("--var", action="append", default=[], help="Variable assignment key=value (repeatable)")
    ap.add_argument("--overwrite", action="store_true", help="Allow overwriting existing files")
    ap.add_argument("--include-hidden", action="store_true", help="Include dotfiles/directories")
    ap.add_argument("--dry-run", action="store_true", help="Plan only; do not write")
    ap.add_argument("--quiet", action="store_true", help="Less verbose output")
    args = ap.parse_args(argv)

    engine = TemplateEngine()
    vars_map = _parse_vars(args.var)

    if args.dry_run:
        plan = engine.plan(args.template, args.out, vars_map, overwrite=args.overwrite, include_hidden=args.include_hidden)
        for a in plan.actions:
            print(f"{'[WRITE]' if a.will_write else '[SKIP ]'} {a.reason:12}  {a.src} -> {a.dst}")
        print(f"\nPlanned {sum(1 for a in plan.actions if a.will_write)} writes, {sum(1 for a in plan.actions if not a.will_write)} skips.")
        return 0

    plan = engine.render(
        args.template,
        args.out,
        vars_map,
        overwrite=args.overwrite,
        include_hidden=args.include_hidden,
        verbose=not args.quiet,
    )
    writes = sum(1 for a in plan.actions if a.will_write)
    skips = sum(1 for a in plan.actions if not a.will_write)
    if not args.quiet:
        print(f"\nDone. Wrote {writes} files, skipped {skips}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
