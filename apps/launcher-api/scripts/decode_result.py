#!/usr/bin/env python3
"""
Companion to encode_calldata.py: decodes the raw return bytes from a Python-VM
contract view call into a JSON value the launcher-api can pass back to the
browser.

Usage:
    decode_result.py <manifest.json> <method> <raw_hex>

`raw_hex` may be 0x-prefixed or bare hex. Outputs a single line of JSON on
stdout. Bytes values are emitted as 0x-prefixed hex; ints pass through.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "sdk" / "python"))
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))

from animica.contracts.abi_utils import decode_method_result  # noqa: E402


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


def _jsonable(value):
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, int):
        # Emit as decimal string for big ints so JS BigInt handling stays
        # exact through the JSON wire (Number loses precision past 2^53).
        return str(value) if value.bit_length() > 53 else value
    return value


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    manifest_path, method, raw_hex = argv[1], argv[2], argv[3]
    text = raw_hex.strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    raw = bytes.fromhex(text) if text else b""

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

    decoded = decode_method_result(abi_flat, method, raw)
    sys.stdout.write(json.dumps(_jsonable(decoded)))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
