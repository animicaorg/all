# -*- coding: utf-8 -*-
"""
Generic Atheris fuzz runner for Python targets.

Usage
-----
# Fuzz a target function that accepts **bytes**
python tests/fuzz/atheris_runner.py \
  --target core.encoding.cbor:decode \
  tests/fuzz/corpus/cbor

# Fuzz a target that accepts atheris.FuzzedDataProvider (fdp)
python tests/fuzz/atheris_runner.py \
  --target proofs.cbor:fuzz_decode_envelope \
  --strategy fdp \
  tests/fuzz/corpus/proofs

You can pass additional Atheris flags after `--` and/or one or more corpus
directories. Unknown flags and corpus paths are forwarded to Atheris.

Environment variables (defaults)
--------------------------------
ANIMICA_FUZZ_TARGET       module:function (same as --target)
ANIMICA_FUZZ_STRATEGY     auto|bytes|fdp      [default: auto]
ANIMICA_FUZZ_MAXLEN       max input len for bytes strategy (int) [default: 4096]
ANIMICA_FUZZ_COVERAGE     1/0 enable python coverage [default: 1]
ANIMICA_FUZZ_IGNORE_EXC   comma-separated exception class names to ignore (e.g. ValueError,json.JSONDecodeError)

Notes
-----
- Import order matters: we call `atheris.instrument_all()` *before* importing
  your target module so that it can be instrumented for coverage.
- Your target function should take exactly one argument:
    • bytes (for parsers/codecs), or
    • atheris.FuzzedDataProvider (fdp) for structured consumption.
- By default (strategy=auto), we inspect the function signature and prefer FDP
  if the parameter name looks like 'fdp' or the annotation mentions it.
"""
from __future__ import annotations

import argparse
import builtins
import importlib
import inspect
import os
import sys
from types import ModuleType
from typing import Callable, Iterable, List, Optional, Tuple, Type

try:
    import atheris  # type: ignore
except Exception as exc:  # pragma: no cover
    sys.stderr.write(
        "[atheris_runner] Atheris is not installed.\n"
        "Install with: pip install atheris && (Python 3.8–3.11 recommended)\n"
        f"Import error: {exc}\n"
    )
    sys.exit(2)

# Instrument as early as possible for better coverage.
atheris.instrument_all()


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


def _resolve_target(t: str) -> Tuple[ModuleType, Callable]:
    """
    Accepts 'pkg.mod:func' or 'pkg.mod.func' and returns (module, function).
    """
    if ":" in t:
        mod_name, func_name = t.split(":", 1)
    else:
        # Split on last dot
        parts = t.rsplit(".", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Invalid --target '{t}'. Use module:function or module.func"
            )
        mod_name, func_name = parts
    mod = importlib.import_module(mod_name)
    func = getattr(mod, func_name, None)
    if not callable(func):
        raise ValueError(f"Target '{t}' is not callable (got {type(func).__name__})")
    return mod, func


def _wants_fdp(func: Callable, strategy: str) -> bool:
    if strategy == "fdp":
        return True
    if strategy == "bytes":
        return False
    # auto: inspect signature
    try:
        sig = inspect.signature(func)
    except Exception:
        return False
    params = list(sig.parameters.values())
    if not params:
        return False
    p0 = params[0]
    name_hint = (p0.name or "").lower()
    if "fdp" in name_hint or "provider" in name_hint:
        return True
    # Check annotation text (avoid importing atheris types here)
    ann = p0.annotation
    txt = ""
    if ann is not inspect._empty:
        try:
            txt = str(ann)
        except Exception:
            txt = ""
    return "FuzzedDataProvider" in txt


def _parse_ignored_excs(spec: str) -> Tuple[Type[BaseException], ...]:
    """
    Resolve a comma-separated list of exception class names to a tuple.
    Names may be bare (ValueError) or qualified (json.JSONDecodeError).
    Unknown names are ignored with a warning.
    """
    out: List[Type[BaseException]] = []
    for raw in (spec or "").split(","):
        name = raw.strip()
        if not name:
            continue
        try:
            if "." in name:
                mod_name, cls_name = name.rsplit(".", 1)
                mod = importlib.import_module(mod_name)
                cls = getattr(mod, cls_name)
            else:
                cls = getattr(builtins, name)
            if isinstance(cls, type) and issubclass(cls, BaseException):
                out.append(cls)
            else:
                sys.stderr.write(
                    f"[atheris_runner] Ignore list entry '{name}' is not an Exception type; skipping.\n"
                )
        except Exception:
            sys.stderr.write(
                f"[atheris_runner] Could not resolve exception '{name}'; skipping.\n"
            )
    return tuple(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shared Atheris fuzzing harness for Animica Python targets",
        add_help=True,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--target",
        default=_env("ANIMICA_FUZZ_TARGET"),
        help="module:function (or module.func)",
    )
    parser.add_argument(
        "--strategy",
        choices=["auto", "bytes", "fdp"],
        default=_env("ANIMICA_FUZZ_STRATEGY", "auto"),
        help="How to call the target function",
    )
    parser.add_argument(
        "--max-input-len",
        type=int,
        default=int(_env("ANIMICA_FUZZ_MAXLEN", "4096") or "4096"),
        help="Max length for bytes strategy (-1 = unlimited)",
    )
    parser.add_argument(
        "--ignore-exc",
        default=_env("ANIMICA_FUZZ_IGNORE_EXC", ""),
        help="Comma-separated exception classes to ignore (e.g. ValueError,json.JSONDecodeError)",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        default=(
            _env("ANIMICA_FUZZ_COVERAGE", "1") not in ("0", "false", "False", "no")
        ),
        help="Enable Atheris Python coverage",
    )
    # All remaining args (including corpus dirs and Atheris flags) are passed through
    args, pass_through = parser.parse_known_args(argv)

    if not args.target:
        parser.error("--target (or ANIMICA_FUZZ_TARGET) is required")

    # Resolve target AFTER instrumentation but BEFORE Setup.
    _, target_func = _resolve_target(args.target)

    wants_fdp = _wants_fdp(target_func, args.strategy)
    ignored = _parse_ignored_excs(args.ignore_exc or "")

    # If no corpus dirs were provided, synthesize a per-target default to avoid
    # Atheris complaining and to allow seed repro with a persistent place.
    has_corpus = any(not s.startswith("-") for s in pass_through)
    if not has_corpus:
        base = os.path.join("tests", "fuzz", "corpus")
        os.makedirs(base, exist_ok=True)
        corpus_dir = os.path.join(base, args.target.replace(":", "."))
        os.makedirs(corpus_dir, exist_ok=True)
        pass_through = [corpus_dir] + pass_through

    # Minimal banner (stderr) to help triage which target is running.
    sys.stderr.write(
        f"[atheris_runner] target={args.target} strategy={'fdp' if wants_fdp else 'bytes'} "
        f"max_len={args.max_input_len} coverage={'on' if args.coverage else 'off'}\n"
    )

    def TestOneInput(data: bytes) -> None:  # noqa: N802 (Atheris naming)
        if wants_fdp:
            fdp = atheris.FuzzedDataProvider(data)
            try:
                target_func(fdp)  # type: ignore[misc]
            except ignored:
                return
        else:
            if args.max_input_len is not None and args.max_input_len >= 0:
                data = data[: args.max_input_len]
            try:
                target_func(data)  # type: ignore[misc]
            except ignored:
                return

    # Atheris expects argv-like list: [prog, <corpus/flags...>]
    atheris_args = [sys.argv[0], *pass_through]
    atheris.Setup(
        atheris_args, TestOneInput, enable_python_coverage=bool(args.coverage)
    )
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
