#!/usr/bin/env python3
"""
Animica mempool flush tool

Drain mempool entries (best-effort) and emit them as JSON to stdout, and/or
write a canonical CBOR snapshot to a file for offline debugging.

Examples:
  # Try RPC first (prefers non-standard 'mempool.drain', falls back to 'mempool.inspect')
  python -m mempool.cli.flush --rpc http://127.0.0.1:8645/rpc --limit 200 --cbor-out /tmp/mempool.cbor

  # Read from a JSON file and just CBOR-encode it canonically
  python -m mempool.cli.flush --input snapshot.json --cbor-out /tmp/snapshot.cbor

  # Pipe JSON through and pretty-print canonically
  cat snapshot.json | python -m mempool.cli.flush --input -

Notes:
- The JSON-RPC methods used here are DEBUG/OPTIONAL:
    * mempool.drain(params?)  -> list[entry]
    * mempool.inspect()       -> list[entry] or {"entries":[...]}
- If the node doesn't implement these, use --input/- to transform local JSON.
- CBOR encoding uses canonical ordering (if 'cbor2' is available). If not, the
  tool prints a helpful hint and exits non-zero when --cbor-out is requested.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import sys
import typing as t
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Optional CBOR backend
try:
    import cbor2  # type: ignore
except Exception:  # pragma: no cover
    cbor2 = None  # type: ignore[assignment]

Json = t.Union[dict, list, str, int, float, bool, None]


def _canonicalize(obj: Json) -> Json:
    """
    Produce a JSON structure with dictionaries sorted by key, recursively.
    This is helpful for reproducible JSON and for CBOR canonical encoding.
    """
    if isinstance(obj, dict):
        return {k: _canonicalize(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_canonicalize(x) for x in obj]
    return obj


def _read_json_input(path: str) -> Json:
    if path == "-":
        return json.loads(sys.stdin.read())
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _flatten_entries(blob: Json) -> list[dict]:
    if isinstance(blob, list):
        return [x for x in blob if isinstance(x, dict)]
    if isinstance(blob, dict):
        if "entries" in blob and isinstance(blob["entries"], list):
            return [x for x in blob["entries"] if isinstance(x, dict)]
        # Sometimes keyed by hashes
        vals = [v for v in blob.values() if isinstance(v, (dict, list))]
        if vals and all(isinstance(v, dict) for v in vals):
            return list(t.cast(dict, blob).values())
    return []


def _rpc_call(url: str, method: str, params: list | dict | None = None) -> Json:
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise SystemExit(f"RPC HTTP error {e.code}: {e.reason}") from e
    except URLError as e:
        raise SystemExit(f"RPC connection error: {e.reason}") from e
    except Exception as e:
        raise SystemExit(f"RPC error: {e}") from e

    if "error" in payload and payload["error"] is not None:
        code = payload["error"].get("code")
        msg = payload["error"].get("message")
        raise SystemExit(f"RPC returned error {code}: {msg}")
    return payload.get("result")


def _fetch_from_rpc(
    url: str,
    limit: int | None,
    ready_only: bool,
    gas_limit: int | None,
    bytes_limit: int | None,
) -> list[dict]:
    # Prefer a purpose-built drain endpoint if present.
    params = {
        "limit": limit,
        "ready_only": ready_only,
        "gas_limit": gas_limit,
        "bytes_limit": bytes_limit,
    }
    # Strip Nones to be polite
    params = {k: v for k, v in params.items() if v is not None}
    try:
        result = _rpc_call(url, "mempool.drain", [params])
        if isinstance(result, dict):
            entries = _flatten_entries(result)
        elif isinstance(result, list):
            entries = [x for x in result if isinstance(x, dict)]
        else:
            entries = []
        if entries:
            return entries
    except SystemExit:
        # Surface later only if inspect also fails.
        pass
    except Exception:
        pass

    # Fallback: inspect (non-destructive)
    result = _rpc_call(url, "mempool.inspect", [])
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    if isinstance(result, dict):
        return _flatten_entries(result)
    return []


def _write_cbor(path: str, obj: Json) -> None:
    if cbor2 is None:
        raise SystemExit(
            "cbor2 is not available. Install with:\n  pip install cbor2\n"
            "Or omit --cbor-out to print JSON to stdout."
        )
    # Use canonical CBOR: sort keys and set canonical=True if available
    canonical = _canonicalize(obj)
    try:
        with open(path, "wb") as f:
            # cbor2 canonical encoding: default sort keys behavior; ensure floats preserved
            # cbor2 doesn't expose a canonical flag; with pre-sorted dicts it's deterministic.
            cbor2.dump(canonical, f)
    except Exception as e:
        raise SystemExit(f"Failed to write CBOR to {path}: {e}") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Drain Animica mempool entries to stdout (JSON) and/or CBOR file."
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--rpc", help="JSON-RPC endpoint (tries mempool.drain → mempool.inspect)"
    )
    src.add_argument(
        "-i", "--input", help="Read entries from JSON file or '-' for stdin"
    )

    ap.add_argument(
        "--limit", type=int, default=None, help="Max number of entries to fetch via RPC"
    )
    ap.add_argument(
        "--ready-only",
        action="store_true",
        help="Ask RPC to return only 'ready' entries (if supported)",
    )
    ap.add_argument(
        "--gas-limit",
        type=int,
        default=None,
        help="Optional gas budget hint for RPC drain",
    )
    ap.add_argument(
        "--bytes-limit",
        type=int,
        default=None,
        help="Optional byte budget hint for RPC drain",
    )

    ap.add_argument("--cbor-out", help="Path to write canonical CBOR snapshot")
    ap.add_argument(
        "--json-out", help="Optional path to also write canonical JSON (pretty)"
    )

    ap.add_argument(
        "--no-stdout",
        action="store_true",
        help="Do not print JSON to stdout (useful with --cbor-out)",
    )
    args = ap.parse_args(argv)

    # Load entries
    if args.rpc:
        entries = _fetch_from_rpc(
            args.rpc,
            limit=args.limit,
            ready_only=bool(args.ready_only),
            gas_limit=args.gas_limit,
            bytes_limit=args.bytes_limit,
        )
    else:
        blob = _read_json_input(args.input)
        entries = _flatten_entries(blob)

    # Canonicalize for determinism
    payload: Json = {"entries": _canonicalize(entries)}

    # Write CBOR if requested
    if args.cbor_out:
        _write_cbor(args.cbor_out, payload)
        print(f"[+] Wrote canonical CBOR snapshot to {args.cbor_out}", file=sys.stderr)

    # Write JSON file if requested
    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            print(
                f"[+] Wrote canonical JSON snapshot to {args.json_out}", file=sys.stderr
            )
        except Exception as e:
            raise SystemExit(f"Failed to write JSON to {args.json_out}: {e}") from e

    # Print to stdout unless suppressed
    if not args.no - stdout:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
