from __future__ import annotations

"""
Animica • DA • inspect_root
===========================

Decode and print information about an NMT (Namespaced Merkle Tree) commitment.

This tool understands two encodings for a "commitment" blob:

1) Digest-only (32 bytes)
   - Just the NMT root digest. Namespace range cannot be recovered from the
     digest alone; we will only print the hash.

2) Augmented commitment (min_ns || max_ns || root_digest)
   - A compact binary/hex encoding that prepends the namespace range to the
     digest. The namespace ID width is --ns-bytes (default 8).
   - Total size = ns_bytes * 2 + 32.
   - This layout is commonly used for light clients and range proofs.

Input forms accepted:
- Hex strings (with or without 0x)
- A path to a file containing the raw bytes (use --in file.bin)
- Stdin (when --in -)

Examples
--------
# Digest-only hex
python -m da.cli.inspect_root 0x1a2b...dead

# Augmented hex (min||max||digest), print JSON
python -m da.cli.inspect_root 0x0000000000000001_00000000000000ff_abcd... --json

# Read raw bytes from a file and auto-detect augmented vs digest-only
python -m da.cli.inspect_root --in header_da_root.bin

# Explicitly specify namespace width (bytes)
python -m da.cli.inspect_root --in commit.bin --ns-bytes 8 --json
"""

import argparse
import json
import os
import sys
from typing import Optional, Tuple


# Try to learn defaults from the library (if present)
DEFAULT_NS_BYTES = 8
try:
    # Prefer a single source of truth if available
    from da.nmt.namespace import NAMESPACE_ID_BYTES as _LIB_NS_BYTES  # type: ignore
    DEFAULT_NS_BYTES = int(_LIB_NS_BYTES)
except Exception:
    pass


def _is_hex_like(s: str) -> bool:
    t = s.lower().strip().replace("_", "")
    if t.startswith("0x"):
        t = t[2:]
    if len(t) == 0 or len(t) % 2 != 0:
        return False
    try:
        bytes.fromhex(t)
        return True
    except Exception:
        return False


def _read_input_bytes(arg_hex_or_none: Optional[str], in_path: Optional[str]) -> bytes:
    if arg_hex_or_none is not None:
        h = arg_hex_or_none.strip().lower().replace("_", "")
        if h.startswith("0x"):
            h = h[2:]
        return bytes.fromhex(h)
    if in_path is None or in_path == "-":
        return sys.stdin.buffer.read()
    with open(in_path, "rb") as f:
        return f.read()


def _split_augmented(b: bytes, ns_bytes: int) -> Optional[Tuple[int, int, bytes]]:
    """
    If `b` matches the augmented layout (min||max||digest), return (min, max, digest).
    Otherwise return None.
    """
    need = ns_bytes * 2 + 32
    if len(b) != need:
        return None
    min_ns = int.from_bytes(b[0:ns_bytes], "big")
    max_ns = int.from_bytes(b[ns_bytes:2 * ns_bytes], "big")
    digest = b[2 * ns_bytes :]
    return (min_ns, max_ns, digest)


def _fmt_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _fmt_ns(ns: int, ns_bytes: int) -> str:
    return "0x" + ns.to_bytes(ns_bytes, "big").hex()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Animica • DA • inspect NMT commitment (digest-only or augmented)"
    )
    p.add_argument(
        "commitment",
        nargs="?",
        help="hex string (0x… ok) of commitment bytes (digest-only or augmented)",
    )
    p.add_argument(
        "--in",
        dest="in_path",
        help="read commitment bytes from file ('-' for stdin)",
    )
    p.add_argument(
        "--ns-bytes",
        type=int,
        default=DEFAULT_NS_BYTES,
        help=f"namespace id width in bytes (default: {DEFAULT_NS_BYTES})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.commitment is None and args.in_path is None:
        print("error: provide a hex commitment or --in FILE/ -", file=sys.stderr)
        return 2

    if args.commitment is not None and not _is_hex_like(args.commitment):
        # If it's not hex, assume it's a path and read it
        if os.path.exists(args.commitment):
            args.in_path = args.commitment
            args.commitment = None
        else:
            print("error: commitment must be hex or an existing file path", file=sys.stderr)
            return 2

    try:
        raw = _read_input_bytes(args.commitment, args.in_path)
    except Exception as e:
        print(f"error: failed to read commitment bytes: {e}", file=sys.stderr)
        return 2

    ns_bytes = int(args.ns_bytes)
    if ns_bytes <= 0 or ns_bytes > 32:
        print("error: --ns-bytes must be in [1,32]", file=sys.stderr)
        return 2

    # Try to decode as augmented; otherwise treat as digest-only
    decoded = _split_augmented(raw, ns_bytes)
    is_augmented = decoded is not None
    if is_augmented:
        min_ns, max_ns, digest = decoded  # type: ignore[misc]
        summary = {
            "encoding": "augmented",
            "ns_bytes": ns_bytes,
            "min_ns": {
                "hex": _fmt_ns(min_ns, ns_bytes),
                "int": min_ns,
            },
            "max_ns": {
                "hex": _fmt_ns(max_ns, ns_bytes),
                "int": max_ns,
            },
            "range_ok": (min_ns <= max_ns),
            "root_digest": _fmt_hex(digest),
            "size_bytes": len(raw),
        }
    else:
        # digest-only
        digest = raw
        if len(digest) != 32:
            print(
                f"warning: digest-only commitment length is {len(digest)} bytes; expected 32",
                file=sys.stderr,
            )
        summary = {
            "encoding": "digest-only",
            "root_digest": _fmt_hex(digest),
            "size_bytes": len(raw),
            "ns_bytes": ns_bytes,
            "min_ns": None,
            "max_ns": None,
            "range_ok": None,
        }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    # Human-readable output
    print("Animica • DA • NMT Commitment")
    print(f"Encoding   : {summary['encoding']}")
    print(f"Digest     : {summary['root_digest']}")
    if is_augmented:
        print(f"NS bytes   : {summary['ns_bytes']}")
        print(f"Min NS     : {summary['min_ns']['hex']} (int {summary['min_ns']['int']})")
        print(f"Max NS     : {summary['max_ns']['hex']} (int {summary['max_ns']['int']})")
        print(f"Range OK   : {summary['range_ok']}")
    else:
        print("Note       : No namespace range embedded (digest-only).")
    print(f"Size       : {summary['size_bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
