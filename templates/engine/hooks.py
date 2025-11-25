# -*- coding: utf-8 -*-
"""
templates.engine.hooks

A lightweight, dependency-free hook system to customize template rendering.

This module defines a small Hooks interface (class with overridable methods),
a RenderContext dataclass, and loader utilities that discover hook providers
from several places in deterministic order:

  1) Template-local file: <template_dir>/_hooks.py
  2) Repo-global file:    <templates_root>/hooks.py
  3) Environment list:    TPL_HOOKS="pkg.module[:attr],other.module[:attr]"

Each discovered provider can expose one or more callables with these names:
    - before_render(ctx) -> Mapping[str, object] | None
    - after_render(ctx) -> None
    - include_file(ctx, rel_path) -> bool
    - map_destination(ctx, rel_path) -> str | os.PathLike
    - before_write(ctx, rel_path, content: bytes) -> bytes
    - on_conflict(ctx, rel_path, existing: bytes, new: bytes)
          -> ("overwrite" | "skip" | "rename", bytes | None, Optional[str])
      Returns a decision, optionally transformed bytes (for overwrite), and an
      optional new relative path (for rename).
    - after_write(ctx, rel_path) -> None

You can also subclass Hooks and implement the same method names.

The engine (templates.engine.render) is expected to call these methods
around its normal flow. This module does not import the renderer to avoid
cycles.

Example template-local _hooks.py:

    from pathlib import Path

    def before_render(ctx):
        # Inject a derived value
        name = ctx.variables.get("NAME", "app")
        return {"APP_SLUG": name.lower().replace(" ", "-")}

    def include_file(ctx, rel_path: Path) -> bool:
        # Only include Dockerfile if DOCKER=true
        if rel_path.name == "Dockerfile":
            return ctx.variables.get("DOCKER", "false").lower() == "true"
        return True

    def map_destination(ctx, rel_path: Path):
        # Rename __name__ placeholders on disk
        return Path(str(rel_path).replace("__name__", ctx.variables["APP_SLUG"]))

    def on_conflict(ctx, rel_path, existing: bytes, new: bytes):
        # Never overwrite README.md automatically
        if rel_path.name.lower().startswith("readme"):
            return ("rename", new, str(rel_path) + ".new")
        return ("overwrite", new, None)

"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RenderContext:
    """
    Immutable context shared with hooks.

    Attributes:
        templates_root: Root directory that contains the templates/ tree.
        template_dir:   The directory of the specific template being rendered.
        output_dir:     Destination directory where files will be written.
        variables:      Final (stringified) variables after merging & schema application.
    """
    templates_root: Path
    template_dir: Path
    output_dir: Path
    variables: Mapping[str, str]

    def with_variables(self, updates: Mapping[str, Any]) -> "RenderContext":
        """Return a shallow copy with variables updated (values coerced to str)."""
        new_vars: Dict[str, str] = dict(self.variables)
        for k, v in updates.items():
            new_vars[str(k)] = _coerce_str(v)
        return replace(self, variables=new_vars)


class HookError(RuntimeError):
    """Raised when a hook cannot be loaded or fails at runtime."""


# --------------------------------------------------------------------------- #
# Hooks interface
# --------------------------------------------------------------------------- #


class Hooks:
    """
    Base hook provider. Subclass and override methods as needed.
    All methods are optional; defaults are no-ops/pass-through.
    """

    # ---- Render lifecycle -------------------------------------------------- #

    def before_render(self, ctx: RenderContext) -> Optional[Mapping[str, Any]]:
        """
        Called once before any files are processed.
        Return a mapping of variable updates to be merged in (stringified later),
        or None to leave variables unchanged.
        """
        return None

    def after_render(self, ctx: RenderContext) -> None:
        """Called once after all files have been processed."""
        return None

    # ---- File selection & mapping ----------------------------------------- #

    def include_file(self, ctx: RenderContext, rel_path: Path) -> bool:
        """
        Decide whether to include a file (relative path under the template).
        Return False to skip generating this file.
        """
        return True

    def map_destination(self, ctx: RenderContext, rel_path: Path) -> Path:
        """
        Map the template-relative path to a destination-relative path.
        Can be used for placeholder renames (e.g., __name__ → project slug).
        """
        return rel_path

    # ---- Write flow -------------------------------------------------------- #

    def before_write(self, ctx: RenderContext, rel_path: Path, content: bytes) -> bytes:
        """
        Called immediately before content is written to disk.
        May transform content (e.g., header injection, license banner).
        Must return bytes.
        """
        return content

    def on_conflict(
        self,
        ctx: RenderContext,
        rel_path: Path,
        existing: bytes,
        new: bytes,
    ) -> Tuple[str, Optional[bytes], Optional[str]]:
        """
        Handle file conflicts when destination already exists.

        Returns a tuple:
            (decision, data, new_rel_path)
        Where:
            decision ∈ {"overwrite", "skip", "rename"}
            data     : bytes to write when decision == "overwrite" (or None to use `new`)
            new_rel_path: relative path string for "rename" decision (under output_dir)

        Default: "overwrite" using `new`.
        """
        return ("overwrite", None, None)

    def after_write(self, ctx: RenderContext, rel_path: Path) -> None:
        """Called after a file has been successfully written."""
        return None


# --------------------------------------------------------------------------- #
# Composite hooks (chain multiple providers)
# --------------------------------------------------------------------------- #


class CompositeHooks(Hooks):
    """
    Fan-out to multiple Hook providers in order. For write/content/conflict
    decisions, later providers see the results of earlier ones.
    """

    def __init__(self, providers: Sequence[Hooks]) -> None:
        self._providers: List[Hooks] = list(providers)

    def before_render(self, ctx: RenderContext) -> Optional[Mapping[str, Any]]:
        merged: Dict[str, Any] = {}
        for p in self._providers:
            upd = p.before_render(ctx)
            if upd:
                for k, v in upd.items():
                    merged[str(k)] = v
        return merged or None

    def after_render(self, ctx: RenderContext) -> None:
        for p in self._providers:
            p.after_render(ctx)

    def include_file(self, ctx: RenderContext, rel_path: Path) -> bool:
        for p in self._providers:
            if not p.include_file(ctx, rel_path):
                return False
        return True

    def map_destination(self, ctx: RenderContext, rel_path: Path) -> Path:
        out = rel_path
        for p in self._providers:
            out = Path(p.map_destination(ctx, Path(out)))
        return out

    def before_write(self, ctx: RenderContext, rel_path: Path, content: bytes) -> bytes:
        out = content
        for p in self._providers:
            out = p.before_write(ctx, rel_path, out)
            _assert_bytes(out, "before_write")
        return out

    def on_conflict(
        self,
        ctx: RenderContext,
        rel_path: Path,
        existing: bytes,
        new: bytes,
    ) -> Tuple[str, Optional[bytes], Optional[str]]:
        """
        Apply conflict policies from providers; the first non-default decision wins.
        If all providers return default ("overwrite", None, None), we overwrite.
        """
        for p in self._providers:
            decision, data, new_rel = p.on_conflict(ctx, rel_path, existing, new)
            if (decision, data, new_rel) != ("overwrite", None, None):
                return decision, data, new_rel
        return ("overwrite", None, None)

    def after_write(self, ctx: RenderContext, rel_path: Path) -> None:
        for p in self._providers:
            p.after_write(ctx, rel_path)


# --------------------------------------------------------------------------- #
# Dynamic module-backed hooks
# --------------------------------------------------------------------------- #


class _ModuleHooks(Hooks):
    """
    Wrap a plain Python module that exposes top-level functions with hook names.
    Only the functions present are used.
    """

    def __init__(self, mod: Any, label: str) -> None:
        self._mod = mod
        self._label = label

        # Bind functions if they exist. We fetch attributes once for speed.
        self._fn_before_render = _get_callable(mod, "before_render")
        self._fn_after_render = _get_callable(mod, "after_render")
        self._fn_include_file = _get_callable(mod, "include_file")
        self._fn_map_destination = _get_callable(mod, "map_destination")
        self._fn_before_write = _get_callable(mod, "before_write")
        self._fn_on_conflict = _get_callable(mod, "on_conflict")
        self._fn_after_write = _get_callable(mod, "after_write")

    def before_render(self, ctx: RenderContext) -> Optional[Mapping[str, Any]]:
        fn = self._fn_before_render
        return None if fn is None else _call(fn, ctx)

    def after_render(self, ctx: RenderContext) -> None:
        fn = self._fn_after_render
        if fn:
            _call(fn, ctx)

    def include_file(self, ctx: RenderContext, rel_path: Path) -> bool:
        fn = self._fn_include_file
        return True if fn is None else bool(_call(fn, ctx, rel_path))

    def map_destination(self, ctx: RenderContext, rel_path: Path) -> Path:
        fn = self._fn_map_destination
        if fn is None:
            return rel_path
        result = _call(fn, ctx, rel_path)
        return Path(result)

    def before_write(self, ctx: RenderContext, rel_path: Path, content: bytes) -> bytes:
        fn = self._fn_before_write
        if fn is None:
            return content
        out = _call(fn, ctx, rel_path, content)
        _assert_bytes(out, f"{self._label}.before_write")
        return out

    def on_conflict(
        self,
        ctx: RenderContext,
        rel_path: Path,
        existing: bytes,
        new: bytes,
    ) -> Tuple[str, Optional[bytes], Optional[str]]:
        fn = self._fn_on_conflict
        if fn is None:
            return ("overwrite", None, None)
        result = _call(fn, ctx, rel_path, existing, new)
        # Normalize result
        if not isinstance(result, (tuple, list)) or not (1 <= len(result) <= 3):
            raise HookError(
                f"{self._label}.on_conflict must return (decision[, data[, new_rel]]), got: {result!r}"
            )
        decision = str(result[0])
        data = None
        new_rel = None
        if len(result) >= 2 and result[1] is not None:
            data = result[1]
            _assert_bytes(data, f"{self._label}.on_conflict")
        if len(result) == 3 and result[2] is not None:
            new_rel = str(result[2])
        if decision not in ("overwrite", "skip", "rename"):
            raise HookError(f"{self._label}.on_conflict returned invalid decision: {decision!r}")
        return decision, data, new_rel

    def after_write(self, ctx: RenderContext, rel_path: Path) -> None:
        fn = self._fn_after_write
        if fn:
            _call(fn, ctx, rel_path)


# --------------------------------------------------------------------------- #
# Loading & discovery
# --------------------------------------------------------------------------- #


def load_hooks(
    *,
    templates_root: Union[str, Path],
    template_dir: Union[str, Path],
    env_var: str = "TPL_HOOKS",
) -> Hooks:
    """
    Discover and load hook providers.

    Order (first to last):
        1) <template_dir>/_hooks.py
        2) <templates_root>/hooks.py
        3) Modules listed in the env var `env_var` (comma-separated),
           each item like "pkg.mod[:attr]". If :attr is omitted, the module
           object is used; if provided, the attribute value is used.

    Returns a CompositeHooks (or plain Hooks if only one).
    """
    providers: List[Hooks] = []

    troot = Path(templates_root).resolve()
    tdir = Path(template_dir).resolve()

    # 1) template-local
    tpl_hooks_path = tdir / "_hooks.py"
    if tpl_hooks_path.is_file():
        providers.append(_load_from_file(tpl_hooks_path, label=str(tpl_hooks_path)))

    # 2) repo-global
    repo_hooks_path = troot / "hooks.py"
    if repo_hooks_path.is_file():
        providers.append(_load_from_file(repo_hooks_path, label=str(repo_hooks_path)))

    # 3) env var
    extra = os.environ.get(env_var, "").strip()
    if extra:
        for ref in _split_items(extra):
            providers.append(_load_from_ref(ref))

    if not providers:
        return Hooks()
    if len(providers) == 1:
        return providers[0]
    return CompositeHooks(providers)


# --------------------------------------------------------------------------- #
# Utilities for glob-style filtering (optional helpers for hook authors)
# --------------------------------------------------------------------------- #


def build_glob_filter(
    *,
    include: Optional[Sequence[str]] = None,
    exclude: Optional[Sequence[str]] = None,
) -> Callable[[Path], bool]:
    """
    Return a predicate (Path -> bool) that matches basenames or posix paths
    against fnmatch-style patterns. If `include` is given, a file must match
    at least one include; if `exclude` is given, files matching any exclude
    are rejected.

    Example:

        only_src = build_glob_filter(include=["src/**", "pyproject.toml"],
                                     exclude=["**/*.tmp", "**/__pycache__/**"])

        def include_file(ctx, rel):
            return only_src(rel)
    """
    import fnmatch

    inc = list(include or [])
    exc = list(exclude or [])

    def _predicate(rel: Path) -> bool:
        p = rel.as_posix()
        name = rel.name

        if inc:
            matched = any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(name, pat) for pat in inc)
            if not matched:
                return False
        if exc:
            if any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(name, pat) for pat in exc):
                return False
        return True

    return _predicate


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _coerce_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _assert_bytes(b: Any, where: str) -> None:
    if not isinstance(b, (bytes, bytearray)):
        raise HookError(f"{where} must return bytes, got {type(b).__name__}")


def _get_callable(obj: Any, name: str) -> Optional[Callable[..., Any]]:
    fn = getattr(obj, name, None)
    if fn is None:
        return None
    if not callable(fn):
        return None
    return fn


def _split_items(spec: str) -> List[str]:
    return [s.strip() for s in spec.split(",") if s.strip()]


def _load_from_ref(ref: str) -> Hooks:
    """
    Load a module or attribute reference "pkg.mod[:attr]".
    If attr is provided and resolves to a Hooks subclass or a module exposing
    hook functions, it will be wrapped accordingly.
    """
    mod_name, attr = (ref.split(":", 1) + [None])[:2]
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        raise HookError(f"Failed to import module '{mod_name}' from '{ref}': {e}") from e

    target = mod if not attr else getattr(mod, attr, None)
    if target is None:
        raise HookError(f"Attribute '{attr}' not found in module '{mod_name}'")

    if isinstance(target, Hooks):
        return target
    if inspect.isclass(target) and issubclass(target, Hooks):
        try:
            return target()  # type: ignore[call-arg]
        except Exception as e:
            raise HookError(f"Failed to instantiate Hooks subclass '{target}': {e}") from e
    if inspect.ismodule(target):
        return _ModuleHooks(target, label=ref)
    if inspect.isfunction(target) or inspect.ismethod(target):
        # Single function is not enough; treat module instead
        return _ModuleHooks(mod, label=mod_name)
    # Fallback: treat module-level functions
    return _ModuleHooks(mod, label=mod_name)


def _load_from_file(path: Path, *, label: str) -> Hooks:
    """
    Load hooks from an arbitrary python file path (no package install required).
    """
    try:
        spec = importlib.util.spec_from_file_location(f"_tpl_hook_{abs(hash(path))}", path)
        if spec is None or spec.loader is None:
            raise HookError(f"spec_from_file_location failed for {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # ensure importable within the file
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    except Exception as e:
        raise HookError(f"Failed to load hooks from {path}: {e}") from e
    return _ModuleHooks(mod, label=label)


# --------------------------------------------------------------------------- #
# Public exports
# --------------------------------------------------------------------------- #

__all__ = [
    "RenderContext",
    "HookError",
    "Hooks",
    "CompositeHooks",
    "build_glob_filter",
    "load_hooks",
]
