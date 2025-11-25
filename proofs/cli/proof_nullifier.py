#!/usr/bin/env python3
"""
proofs.cli.proof_nullifier
==========================

Compute and display the *nullifier* for a given proof **body** (or a full
proof **envelope**). This uses the exact same canonical CBOR encoding and
domain separation as the on-chain verifier (proofs/nullifiers.py), so the
result should match consensus calculations.

Usage
-----
# Body only (JSON or CBOR), specify type by name or id
python -m proofs.cli.proof_nullifier --in ai_body.json --type ai
python -m proofs.cli.proof_nullifier --in hashshare_body.cbor --type-id 1

# Full envelope (detect type_id, recompute body hash & nullifier)
python -m proofs.cli.proof_nullifier --in proof_envelope.cbor --envelope

# Raw hex only
python -m proofs.cli.proof_nullifier --in body.json --type quantum --hex

Inputs
------
--in         Path to JSON/CBOR file (or "-" for stdin).
--envelope   Treat input as a full envelope {type_id, body, nullifier?}.
--type       Proof type name (hashshare|ai|quantum|storage|vdf) if body-only.
--type-id    Proof type id (integer) if body-only.
--hex        Print only 0x<hex> of the nullifier (no extra text).
--out        Optional file to write binary nullifier (32 bytes).
"""

from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import typer  # pip install 'typer[all]'

# Pretty printing (optional)
try:
    from rich.console import Console  # type: ignore
    from rich.table import Table      # type: ignore
    from rich.panel import Panel      # type: ignore
    from rich import box              # type: ignore
except Exception:  # pragma: no cover
    Console = None  # type: ignore
    Table = None    # type: ignore
    Panel = None    # type: ignore
    box = None      # type: ignore

# Animica libs
from proofs.version import __version__
from proofs import registry
from proofs import cbor as proofs_cbor
from proofs import nullifiers as nul

# Fallback CBOR
try:
    import cbor2  # type: ignore
except Exception as e:  # pragma: no cover
    cbor2 = None  # type: ignore

app = typer.Typer(no_args_is_help=True, add_completion=False)


# ----------------- helpers -----------------

def _read_json_or_cbor(path: str) -> Any:
    data: bytes
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(path).read_bytes()

    # Try JSON first
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        pass

    # Then CBOR
    if cbor2 is None:
        raise typer.BadParameter("Input seems CBOR, but 'cbor2' is not installed. pip install cbor2")
    try:
        return cbor2.loads(data)
    except Exception as e:
        raise typer.BadParameter(f"Failed to parse {path!r} as JSON or CBOR: {e}")


def _resolve_type_id(type_name: Optional[str], type_id: Optional[int], maybe_env: Optional[dict]) -> int:
    # Envelope path
    if isinstance(maybe_env, dict) and "type_id" in maybe_env and isinstance(maybe_env["type_id"], int):
        return int(maybe_env["type_id"])
    # Explicit id
    if type_id is not None:
        return int(type_id)
    # By name via registry
    if type_name:
        name = type_name.strip().lower()
        for fn_name in ("type_id_by_name", "type_id", "name_to_id"):
            fn = getattr(registry, fn_name, None)
            if callable(fn):
                try:
                    tid = fn(name)  # type: ignore
                    if isinstance(tid, int):
                        return tid
                except Exception:
                    pass
        raise typer.BadParameter(f"Unknown proof type name: {type_name!r}")
    raise typer.BadParameter("Must provide --type or --type-id when not using --envelope")


def _extract_body(obj: Any, envelope: bool) -> Dict[str, Any]:
    if envelope:
        if not isinstance(obj, dict) or "body" not in obj:
            raise typer.BadParameter("--envelope was set, but input does not look like an envelope")
        body = obj.get("body")
        if not isinstance(body, dict):
            raise typer.BadParameter("Envelope.body must be a JSON/CBOR object (map)")
        return body
    if not isinstance(obj, dict):
        raise typer.BadParameter("Body input must decode to a JSON/CBOR object (map)")
    return obj


def _encode_body_cbor(type_id: int, body: Dict[str, Any]) -> bytes:
    # Prefer canonical encoder from proofs.cbor
    for fn_name in ("encode_body", "encode_proof_body", "encode_hashshare_body"):
        fn = getattr(proofs_cbor, fn_name, None)
        if callable(fn):
            try:
                # Most implementations accept (type_id, body)
                return fn(type_id, body)  # type: ignore
            except TypeError:
                # Some older prototypes accepted a string name first
                return fn("hashshare", body)  # type: ignore
            except Exception:
                pass
    if cbor2 is None:
        raise typer.BadParameter("CBOR encoder not available; install cbor2")
    return cbor2.dumps(body)


def _compute_nullifier(type_id: int, body_cbor: bytes) -> bytes:
    # Official implementation
    for fn_name in ("compute_nullifier", "nullifier_for_body", "nullifier"):
        fn = getattr(nul, fn_name, None)
        if callable(fn):
            return fn(type_id, body_cbor)  # type: ignore
    # Hard fallback (must mirror proofs/nullifiers.py tag!)
    import hashlib
    tag = b"animica:nullifier:generic:v1"
    return hashlib.sha3_256(tag + type_id.to_bytes(2, "big") + body_cbor).digest()


def _print_result(nul_bytes: bytes, meta: Dict[str, Any], hex_only: bool) -> None:
    h = "0x" + nul_bytes.hex()
    if hex_only:
        print(h)
        return
    if Console is None:
        print(f"nullifier={h}")
        for k, v in meta.items():
            print(f"{k}: {v}")
        return
    console = Console()
    t = Table(title="Proof Nullifier", box=box.SIMPLE if box else None)
    t.add_column("Field"); t.add_column("Value", overflow="fold")
    t.add_row("nullifier", h)
    for k, v in meta.items():
        t.add_row(k, str(v))
    console.print(Panel(t, title="animica-proofs", expand=False))


# ----------------- CLI -----------------

@app.callback()
def _meta(version: bool = typer.Option(False, "--version", "-V", help="Print version and exit", is_eager=True)) -> None:
    if version:
        typer.echo(f"animica-proofs {__version__}")
        raise typer.Exit(0)


@app.command("compute")
def compute(
    in_path: str = typer.Option(..., "--in", "-i", help="Input file path (JSON/CBOR) or '-' for stdin"),
    envelope: bool = typer.Option(False, "--envelope", "-e", help="Treat input as a full envelope"),
    type: Optional[str] = typer.Option(None, "--type", "-t", help="Proof type name if body-only (e.g., hashshare, ai, quantum, storage, vdf)"),
    type_id: Optional[int] = typer.Option(None, "--type-id", help="Proof type id if body-only"),
    hex_only: bool = typer.Option(False, "--hex", help="Print only the 0x-hex nullifier"),
    out_bin: Optional[Path] = typer.Option(None, "--out", help="Write raw 32-byte nullifier to file"),
) -> None:
    """
    Compute a proof nullifier using the canonical body CBOR and domain separation.
    """
    obj = _read_json_or_cbor(in_path)
    tid = _resolve_type_id(type, type_id, obj if envelope else None)
    body = _extract_body(obj, envelope=envelope)
    body_cbor = _encode_body_cbor(tid, body)
    n = _compute_nullifier(tid, body_cbor)

    if out_bin:
        out_bin.parent.mkdir(parents=True, exist_ok=True)
        out_bin.write_bytes(n)

    meta = {
        "type_id": tid,
        "input_kind": "envelope" if envelope else "body",
        "body_cbor_len": len(body_cbor),
    }
    _print_result(n, meta, hex_only)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
