#!/usr/bin/env python3
"""
Standalone CLI: encode a Python-VM contract method call into raw calldata bytes.

Reads a manifest.json, a method name, and a positional-args JSON array; emits
hex (no 0x prefix) on stdout. Used by the launcher-api to encode DEX init
calls (`factory.init`, `router.init`) into kind=2 tx `data` payloads that the
wallet then signs and broadcasts.

Usage:
    encode_calldata.py <manifest.json> <method> <args_json>

The manifest format used by animica contracts wraps ABI entries inside
`{events:[...], functions:[...]}`; omni_sdk's encoder expects a flat list of
`{type:"function", ...}` entries, so we flatten before calling encode_call.

Bytes args may be passed as hex strings ("0x..." or "...") or as plain
strings (left as utf-8). Ints pass through.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make in-repo packages importable regardless of the caller's CWD.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "sdk" / "python"))
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))

from animica.contracts.abi_utils import encode_calldata  # noqa: E402


def _flatten_abi(abi_section: dict) -> list:
    flat: list = []
    for fn in abi_section.get("functions", []) or []:
        entry = dict(fn)
        entry.setdefault("type", "function")
        flat.append(entry)
    for ev in abi_section.get("events", []) or []:
        entry = dict(ev)
        entry.setdefault("type", "event")
        flat.append(entry)
    return flat


def _coerce_arg(value, spec_type: str):
    t = (spec_type or "").lower()
    if t == "bytes":
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str):
            text = value
            if text.startswith(("0x", "0X")):
                text = text[2:]
            # If the string is plain hex, decode it; otherwise treat as utf-8.
            try:
                return bytes.fromhex(text)
            except ValueError:
                return value.encode("utf-8")
        raise SystemExit(f"bad bytes arg: {value!r}")
    if t in ("int", "uint", "uint256", "int256"):
        if isinstance(value, str) and value.startswith(("0x", "0X")):
            return int(value, 16)
        return int(value)
    return value


def _coerce_args(abi_flat: list, method: str, args: list) -> list:
    fn = next(
        (f for f in abi_flat if f.get("type") == "function" and f.get("name") == method),
        None,
    )
    if fn is None:
        # Let encode_calldata raise a useful error.
        return args
    inputs = fn.get("inputs", []) or []
    if len(inputs) != len(args):
        raise SystemExit(
            f"arg count mismatch for {method}: expected {len(inputs)}, got {len(args)}"
        )
    return [_coerce_arg(arg, inp.get("type", "")) for arg, inp in zip(args, inputs)]


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    manifest_path, method, args_json = argv[1], argv[2], argv[3]
    manifest = json.loads(Path(manifest_path).read_text())
    abi_section = manifest.get("abi", manifest)
    if isinstance(abi_section, dict) and (
        "functions" in abi_section or "events" in abi_section
    ):
        abi_flat = _flatten_abi(abi_section)
    elif isinstance(abi_section, list):
        abi_flat = abi_section
    else:
        raise SystemExit("manifest.abi is not a list or {functions,events} object")

    args = json.loads(args_json)
    if not isinstance(args, list):
        raise SystemExit("args must be a JSON array")

    coerced = _coerce_args(abi_flat, method, args)
    raw = encode_calldata(abi_flat, method, coerced)
    sys.stdout.write(raw.hex())
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
