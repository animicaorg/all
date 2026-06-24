"""In-sandbox bootstrap that actually runs a packed function call.

This file is **deliberately self-contained** — it imports only the stdlib (plus
``cloudpickle`` *iff* a pickle-mode call needs it). That lets a runner ship its
source verbatim into a sandbox that doesn't have the ``animica`` package
installed, and run it as::

    python _wrapper.py /path/to/call_spec.json
    # or
    ANIMICA_STUDIO_CALL=<base64(json spec)> python _wrapper.py

On success it prints one line::

    __ANIMICA_STUDIO_RESULT__<base64(json({"kind": ..., "value": ...}))>

On failure::

    __ANIMICA_STUDIO_ERROR__<base64(json({"message": ..., "traceback": ...}))>

Everything the function prints to stdout/stderr is preserved; the runner parses
only the final sentinel line.
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import os
import sys
import traceback
import types

RESULT_SENTINEL = "__ANIMICA_STUDIO_RESULT__"
ERROR_SENTINEL = "__ANIMICA_STUDIO_ERROR__"


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _load_spec() -> dict:
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", ""):
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            return json.load(fh)
    env = os.environ.get("ANIMICA_STUDIO_CALL")
    if env:
        return json.loads(_b64d(env).decode("utf-8"))
    raise SystemExit("no call spec: pass a path argv[1] or set ANIMICA_STUDIO_CALL")


def _module_from_source(source: str, filename: str | None) -> types.ModuleType:
    name = "__animica_studio_target__"
    mod = types.ModuleType(name)
    mod.__file__ = filename or "<studio-target>"
    # Set __name__ so the user's `if __name__ == "__main__":` guard does NOT
    # fire — we only want their top-level defs, not their script body.
    sys.modules[name] = mod
    code = compile(source, mod.__file__, "exec")
    exec(code, mod.__dict__)
    return mod


def _resolve_ref(entrypoint: str, source_b64: str | None, filename: str | None):
    module_name, _, qualname = entrypoint.partition(":")
    if source_b64:
        mod = _module_from_source(_b64d(source_b64).decode("utf-8"), filename)
    else:
        mod = importlib.import_module(module_name)
    obj = mod
    for part in qualname.split("."):
        if part == "<locals>":
            raise RuntimeError(
                f"cannot resolve closure target {entrypoint!r} in ref mode; "
                f"use serialization='pickle'"
            )
        obj = getattr(obj, part)
    # If the resolved object is a Studio @function wrapper (re-created when the
    # module is imported), unwrap to the raw callable it guards. Duck-typed so
    # this file stays dependency-free.
    if getattr(obj, "__animica_studio_function__", False):
        obj = getattr(obj, "fn", obj)
    return obj


def _pack_result(value):
    try:
        json.dumps(value)
        return {"kind": "json", "value": value}
    except (TypeError, ValueError):
        try:
            import cloudpickle  # type: ignore
            return {"kind": "pickle", "value": _b64e(cloudpickle.dumps(value))}
        except Exception:
            return {"kind": "repr", "value": repr(value)}


def main() -> int:
    try:
        spec = _load_spec()
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(ERROR_SENTINEL + _b64e(json.dumps(
            {"message": f"spec load failed: {exc}", "traceback": traceback.format_exc()}
        ).encode("utf-8")) + "\n")
        return 2

    try:
        mode = spec.get("mode", "ref")
        if mode == "pickle":
            import cloudpickle  # type: ignore
            fn, args, kwargs = cloudpickle.loads(_b64d(spec["pickle_b64"]))
        else:
            fn = _resolve_ref(spec["entrypoint"], spec.get("source_b64"), spec.get("source_filename"))
            args = tuple(spec.get("args", []))
            kwargs = dict(spec.get("kwargs", {}))

        result = fn(*args, **kwargs)
        packed = _pack_result(result)
        sys.stdout.flush()
        sys.stdout.write(RESULT_SENTINEL + _b64e(json.dumps(packed).encode("utf-8")) + "\n")
        sys.stdout.flush()
        return 0
    except Exception as exc:  # noqa: BLE001 - report any function failure faithfully
        sys.stdout.flush()
        sys.stdout.write(ERROR_SENTINEL + _b64e(json.dumps(
            {"message": str(exc), "traceback": traceback.format_exc()}
        ).encode("utf-8")) + "\n")
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
