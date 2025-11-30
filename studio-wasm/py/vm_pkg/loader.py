from __future__ import annotations

"""
Loader: manifest + source/IR → validated IR module + code bytes + hash.

This module is used by the WASM/pyodide bridge to:
  * parse and validate a contract manifest (name/version/ABI/metadata)
  * compile Python source to IR (when a full compiler is available)
  * OR accept precompiled IR (object/bytes) for browser-only flows
  * typecheck the IR and compute a deterministic code hash
  * return a self-contained bundle suitable for simulation/execution

Design notes
------------
- "Compilation" in the studio-wasm package is optional. Many browser flows pass
  IR directly (e.g., produced offline by `vm_py`). When a full compiler is
  present (module `vm_py.compiler`), we use it. Otherwise we require IR input.
- IR bytes are encoded using the canonical format from
  `vm_pkg.compiler.encode` (msgpack via msgspec preferred; CBOR fallback).
- The manifest format is intentionally minimal and stable:
    {
      "name": "Counter",
      "version": "1.0.0",
      "abi": {... JSON ABI ...},
      "entry": "init",              # optional; defaults to first function
      "meta": { ... }               # optional metadata
    }

Public API
----------
- load_manifest(obj_or_json) -> dict
- has_compiler() -> bool
- build_from_source(manifest, source:str) -> ContractBundle
- build_from_ir(manifest, ir_obj|ir_bytes) -> ContractBundle
- build_bundle(manifest, *, source=None, ir=None, ir_bytes=None) -> ContractBundle
- ContractBundle: dataclass with .manifest, .abi, .ir, .code, .code_hash_hex
"""

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union, cast

from .compiler import ir as irmod
from .compiler.encode import decode_ir, encode_ir
from .compiler.typecheck import validate_module
from .errors import ValidationError

# ---------------------------- Data model ----------------------------


@dataclass(frozen=True)
class ContractBundle:
    manifest: Dict[str, Any]
    abi: Dict[str, Any]
    ir: irmod.Module
    code: bytes
    code_hash_hex: str

    def to_artifact(self) -> Dict[str, Any]:
        """
        Build a portable JSON-friendly artifact object containing the manifest,
        ABI, code bytes (base64), and hash. Suitable for saving or posting to
        tooling endpoints.
        """
        return {
            "manifest": self.manifest,
            "abi": self.abi,
            "code_b64": base64.b64encode(self.code).decode("ascii"),
            "code_hash": self.code_hash_hex,
        }


# ---------------------------- Manifest ----------------------------


def load_manifest(obj_or_json: Union[str, bytes, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Accept a JSON string/bytes or a dict. Return a normalized manifest dict.
    Raises ValidationError on bad shape.
    """
    if isinstance(obj_or_json, (str, bytes, bytearray)):
        try:
            manifest = cast(Dict[str, Any], json.loads(obj_or_json))
        except Exception as e:
            raise ValidationError(f"manifest JSON parse error: {e}") from e
    elif isinstance(obj_or_json, dict):
        manifest = dict(obj_or_json)
    else:
        raise ValidationError("manifest must be JSON text or dict")

    _validate_manifest_shape(manifest)
    return manifest


def _validate_manifest_shape(m: Dict[str, Any]) -> None:
    def req_str(key: str) -> None:
        v = m.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ValidationError(f"manifest.{key} must be a non-empty string")

    req_str("name")
    req_str("version")

    abi = m.get("abi")
    if not isinstance(abi, dict):
        raise ValidationError("manifest.abi must be an object (JSON ABI)")

    entry = m.get("entry")
    if entry is not None and not (isinstance(entry, str) and entry):
        raise ValidationError("manifest.entry must be a non-empty string when provided")

    meta = m.get("meta", None)
    if meta is not None and not isinstance(meta, dict):
        raise ValidationError("manifest.meta must be an object when provided")


# ---------------------------- Compiler detection ----------------------------


def has_compiler() -> bool:
    """
    Return True if a full Python→IR compiler is available (vm_py).
    """
    try:
        import vm_py.compiler.ast_lower as _  # noqa: F401
        import vm_py.compiler.encode as _enc  # noqa: F401

        return True
    except Exception:
        return False


# ---------------------------- Build helpers ----------------------------


def build_from_source(manifest: Dict[str, Any], source: str) -> ContractBundle:
    """
    Compile Python source to IR using a full compiler when available.
    If not available, raise ValidationError suggesting to pass IR instead.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValidationError("source must be a non-empty Python string")

    if not has_compiler():
        raise ValidationError(
            "Python→IR compiler is not available in this environment. "
            "Provide precompiled IR via build_from_ir(), or include vm_py."
        )

    # Defer import to avoid heavy modules unless needed
    from vm_py.compiler import encode as vm_enc  # type: ignore
    from vm_py.runtime import loader as vm_loader  # type: ignore

    # Use vm_py's loader to compile and typecheck, then re-encode via our codec
    try:
        vm_ir_mod = vm_loader.compile_source_to_ir(
            source
        )  # returns vm_py.compiler.ir.Module
    except Exception as e:
        raise ValidationError(f"source compilation failed: {e}") from e

    # Convert by round-trip through a generic shape (encode->bytes->decode with our decoder)
    try:
        vm_bytes = vm_enc.encode_ir(vm_ir_mod)
        ir_module = decode_ir(vm_bytes)  # vm_pkg.ir.Module
    except Exception as e:
        raise ValidationError(f"IR bridge/convert failed: {e}") from e

    entry_name = _resolve_entry_name(manifest, ir_module)
    ir_module = _with_entry(ir_module, entry_name)
    validate_module(ir_module)

    code = encode_ir(ir_module)
    code_hash = _sha3_256_hex(code)

    abi = cast(Dict[str, Any], manifest["abi"])
    return ContractBundle(
        manifest=manifest, abi=abi, ir=ir_module, code=code, code_hash_hex=code_hash
    )


def build_from_ir(
    manifest: Dict[str, Any], ir_input: Union[bytes, bytearray, Dict[str, Any], list]
) -> ContractBundle:
    """
    Accept already-built IR as bytes (msgpack/CBOR) or as a Python object
    ([domain, entry, functions] list shape). Validate and produce a bundle.
    """
    if isinstance(ir_input, (bytes, bytearray)):
        ir_module = decode_ir(ir_input)
    elif isinstance(ir_input, (dict, list, tuple)):
        # Allow direct object form; re-encode and decode to normalize
        tmp_bytes = _encode_any_ir_object(ir_input)
        ir_module = decode_ir(tmp_bytes)
    else:
        raise ValidationError("ir_input must be bytes or an IR list/dict")

    entry_name = _resolve_entry_name(manifest, ir_module)
    ir_module = _with_entry(ir_module, entry_name)
    validate_module(ir_module)

    code = encode_ir(ir_module)
    code_hash = _sha3_256_hex(code)
    abi = cast(Dict[str, Any], manifest["abi"])
    return ContractBundle(
        manifest=manifest, abi=abi, ir=ir_module, code=code, code_hash_hex=code_hash
    )


def build_bundle(
    manifest: Dict[str, Any],
    *,
    source: Optional[str] = None,
    ir: Optional[Union[bytes, bytearray, Dict[str, Any], list]] = None,
    ir_bytes: Optional[Union[bytes, bytearray]] = None,
) -> ContractBundle:
    """
    Polymorphic entrypoint:
      - if `source` is provided → compile from source (requires vm_py)
      - else if `ir` is provided → use that (object or bytes)
      - else if `ir_bytes` is provided → use that
      - else ValidationError
    """
    if source is not None:
        return build_from_source(manifest, source)
    if ir is not None:
        return build_from_ir(manifest, ir)
    if ir_bytes is not None:
        return build_from_ir(manifest, ir_bytes)
    raise ValidationError("one of {source, ir, ir_bytes} must be provided")


# ---------------------------- Internals ----------------------------


def _resolve_entry_name(manifest: Dict[str, Any], m: irmod.Module) -> str:
    entry = manifest.get("entry")
    if isinstance(entry, str) and entry:
        if entry not in m.functions:
            # Keep this explicit so users can correct manifests early.
            raise ValidationError(f"manifest.entry {entry!r} not found in IR functions")
        return entry
    # Default: preserve existing entry if valid; otherwise pick first function name (sorted)
    if isinstance(m.entry, str) and m.entry in m.functions:
        return m.entry
    if not m.functions:
        raise ValidationError("IR module has no functions")
    return sorted(m.functions.keys())[0]


def _with_entry(m: irmod.Module, entry: str) -> irmod.Module:
    if m.entry == entry:
        return m
    # Rebuild a new module with the desired entry (functions preserved)
    return irmod.Module(functions=dict(m.functions), entry=entry)


def _sha3_256_hex(b: bytes) -> str:
    return hashlib.sha3_256(b).hexdigest()


def _encode_any_ir_object(obj: Union[Dict[str, Any], list, tuple]) -> bytes:
    """
    Accepts a python-obj representation of IR and returns encoded bytes
    by first ensuring the top-level domain header and shape.
    """
    # Fast path: if this already looks like ["animica|ir|v1", ...], delegate through decode/encode
    if isinstance(obj, (list, tuple)) and obj and obj[0] == "animica|ir|v1":
        # Normalize via decode->encode
        mod = _obj_to_module_fast(obj)
        return encode_ir(mod)

    # Try to interpret dict/obj as {"entry":..., "functions":[...]}
    try:
        mod = _coerce_to_module(obj)
        return encode_ir(mod)
    except Exception as e:
        raise ValidationError(f"unsupported IR object shape: {e}") from e


def _obj_to_module_fast(obj: Union[list, tuple]) -> irmod.Module:
    # Let the standard decoder handle complete object validation
    tmp_bytes = json.dumps(obj).encode("utf-8")
    # decode_ir expects bytes in msgpack/CBOR; however dumping JSON and re-parsing
    # via eval is not desirable. Instead, rely on decode_ir's permissive path for
    # repr-like lists (it will eval in a restricted env). To prefer safety, we
    # only take this path for the recognized domain header; otherwise we go through
    # _coerce_to_module which builds from dataclasses.
    return decode_ir(tmp_bytes)


def _coerce_to_module(obj: Union[Dict[str, Any], list, tuple]) -> irmod.Module:
    """
    Build an ir.Module from a looser object form:
      { "entry": "main", "functions": [
          { "name": "main", "params": 0, "body": [ ["PUSH",[1]], ["RET",[]] ] },
          ...
        ] }
    """
    if not isinstance(obj, (dict,)):
        raise ValidationError("IR object must be a dict")

    entry = obj.get("entry")
    funcs = obj.get("functions")
    if not isinstance(entry, str) or not entry:
        raise ValidationError("IR object: 'entry' must be a non-empty string")
    if not isinstance(funcs, list) or not funcs:
        raise ValidationError("IR object: 'functions' must be a non-empty list")

    functions: Dict[str, irmod.Function] = {}
    for f in funcs:
        if not isinstance(f, dict):
            raise ValidationError("IR object: each function must be a dict")
        name = f.get("name")
        params = f.get("params", 0)
        body = f.get("body", [])
        if not isinstance(name, str) or not name:
            raise ValidationError("IR function: 'name' must be a non-empty string")
        if not isinstance(params, int) or params < 0:
            raise ValidationError("IR function: 'params' must be a non-negative int")
        if not isinstance(body, list):
            raise ValidationError("IR function: 'body' must be a list")

        instrs = []
        for ins in body:
            if not (isinstance(ins, (list, tuple)) and len(ins) == 2):
                raise ValidationError("IR instr must be [op, args]")
            op, args = ins
            if not isinstance(op, str):
                raise ValidationError("IR instr op must be string")
            if not isinstance(args, (list, tuple)):
                raise ValidationError("IR instr args must be list/tuple")
            instrs.append(irmod.Instr(op=op, args=tuple(args)))
        if name in functions:
            raise ValidationError(f"duplicate function name {name!r}")
        functions[name] = irmod.Function(name=name, params=params, body=instrs)

    return irmod.Module(functions=functions, entry=entry)


__all__ = [
    "ContractBundle",
    "load_manifest",
    "has_compiler",
    "build_from_source",
    "build_from_ir",
    "build_bundle",
]
