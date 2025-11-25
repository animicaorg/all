#!/usr/bin/env python3
"""
proofs.cli.proof_verify
=======================

Verify Animica proof files (auto-detect type, parse CBOR/JSON), then print:

- Proof kind (type_id + human name)
- Nullifier (hex)
- Verified metrics used by PoIES
- ψ-inputs (the values fed into the PoIES scorer; no caps applied here)

By default prints a human report; use --json for machine-readable output.

Examples:
  python -m proofs.cli.proof_verify fixtures/qpu_provider_cert.json
  python -m proofs.cli.proof_verify ../spec/test_vectors/proofs.json --index 0
  python -m proofs.cli.proof_verify my_hashshare.cbor
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

try:
    import typer  # type: ignore
except Exception:  # pragma: no cover
    typer = None  # type: ignore

try:
    from rich.console import Console  # type: ignore
    from rich.table import Table  # type: ignore
    from rich.panel import Panel  # type: ignore
    from rich import box  # type: ignore
except Exception:  # pragma: no cover
    Console = None  # type: ignore
    Table = None  # type: ignore
    Panel = None  # type: ignore
    box = None  # type: ignore

# Library imports (internal)
from proofs.version import __version__
from proofs import registry
from proofs import cbor as proofs_cbor
from proofs.policy_adapter import metrics_to_psi_inputs  # type: ignore[attr-defined]
from proofs.errors import ProofError  # type: ignore
from proofs.types import ProofEnvelope  # type: ignore

AppLike = Any


def _die(msg: str, code: int = 2) -> None:
    sys.stderr.write(msg.rstrip() + "\n")
    raise SystemExit(code)


def _bytes_to_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _maybe_dataclass_to_dict(x: Any) -> Any:
    if is_dataclass(x):
        return asdict(x)
    if isinstance(x, dict):
        return {k: _maybe_dataclass_to_dict(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [ _maybe_dataclass_to_dict(v) for v in x ]
    return x


def _load_json_or_cbor(path: Path, index: Optional[int]) -> ProofEnvelope:
    """
    Try (in order):
      1) If file suffix looks like .json → parse JSON. If it's a list, select --index.
         If it's a dictionary with a "proof" or "envelope" field, unwrap.
      2) Otherwise, treat as CBOR bytes and decode an envelope.
    """
    data = path.read_bytes()
    tried_json = False
    if path.suffix.lower() in {".json", ".jsn"}:
        tried_json = True
        try:
            obj = json.loads(data.decode("utf-8"))
            # If spec vectors file is an array, allow picking item
            if isinstance(obj, list):
                if index is None:
                    raise ValueError(
                        "JSON file contains a list; provide --index to select an element."
                    )
                try:
                    obj = obj[index]
                except IndexError:
                    raise ValueError(f"--index {index} out of range for list length {len(obj)}")
            # Some vector files wrap as {"envelope": { ... }}
            if isinstance(obj, dict) and "envelope" in obj:
                obj = obj["envelope"]
            # Decode JSON object → dataclass via registry helper
            env = registry.decode_envelope_from_json(obj)  # type: ignore[attr-defined]
            return env
        except Exception as e:
            raise ValueError(f"Failed to parse JSON '{path}': {e}") from e
    # Fall back to CBOR
    try:
        env = proofs_cbor.decode_envelope(data)  # type: ignore[attr-defined]
        return env
    except Exception as e:
        if tried_json:
            raise ValueError(f"Neither JSON nor CBOR decoding succeeded for '{path}': {e}") from e
        else:
            # Maybe the file is actually JSON but had no .json suffix.
            try:
                obj = json.loads(data.decode("utf-8"))
                env = registry.decode_envelope_from_json(obj)  # type: ignore[attr-defined]
                return env
            except Exception:
                raise ValueError(f"Failed to decode file '{path}' as CBOR or JSON: {e}") from e


def _verify_envelope(
    env: ProofEnvelope,
    roots_dir: Optional[Path],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Verify using the dynamic registry and return (metrics_dict, psi_inputs_dict).
    """
    # Some verifiers need vendor roots (TEE/QPU). Allow overriding via --roots-dir.
    if roots_dir is not None:
        os.environ.setdefault("ANIMICA_VENDOR_ROOTS", str(roots_dir))

    # Primary verification
    metrics = registry.verify(env)  # type: ignore[attr-defined]

    # Map to ψ-inputs (no caps/Γ clipping here)
    psi_inputs = metrics_to_psi_inputs(metrics)  # type: ignore

    # Convert dataclasses to plain dicts for output
    return _maybe_dataclass_to_dict(metrics), _maybe_dataclass_to_dict(psi_inputs)


def _human_report(
    console: Any,
    path: Path,
    env: ProofEnvelope,
    kind_name: str,
    metrics: Dict[str, Any],
    psi_inputs: Dict[str, Any],
) -> None:
    # Header panel
    meta = Table.grid(padding=(0, 2))
    meta.add_row("File", str(path))
    meta.add_row("Type", f"{env.type_id} ({kind_name})")
    meta.add_row("Nullifier", _bytes_to_hex(env.nullifier if isinstance(env.nullifier, bytes) else bytes.fromhex(env.nullifier.replace('0x',''))))
    console.print(Panel(meta, title="Proof", expand=False))

    # Metrics table
    t = Table(title="Verified Metrics", box=box.SIMPLE if box else None)
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    for k, v in metrics.items():
        # Flatten nested dicts a bit
        if isinstance(v, dict):
            for sk, sv in v.items():
                t.add_row(f"{k}.{sk}", str(sv))
        else:
            t.add_row(k, str(v))
    console.print(t)

    # ψ-inputs table
    s = Table(title="ψ-inputs (to PoIES)", box=box.SIMPLE if box else None)
    s.add_column("Input")
    s.add_column("Value", justify="right")
    for k, v in psi_inputs.items():
        s.add_row(k, str(v))
    console.print(s)


def _to_jsonable(x: Any) -> Any:
    if isinstance(x, bytes):
        return _bytes_to_hex(x)
    if is_dataclass(x):
        return _maybe_dataclass_to_dict(x)
    if isinstance(x, dict):
        return {k: _to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_to_jsonable(v) for v in x]
    return x


def build_app() -> AppLike:
    if typer is None:  # pragma: no cover
        _die("Typer is required. Try: pip install 'typer[all]' 'rich'")
    app = typer.Typer(
        name="proof-verify",
        help="Verify an Animica proof file and print metrics & ψ-inputs",
        no_args_is_help=True,
        add_completion=False,
    )

    @app.callback()
    def _meta(
        version: bool = typer.Option(
            False, "--version", "-V", help="Print version and exit", is_eager=True
        ),
    ) -> None:
        if version:
            typer.echo(f"animica-proofs {__version__}")
            raise typer.Exit(0)

    @app.command("verify")
    def verify_cmd(
        path: Path = typer.Argument(..., help="Path to proof file (CBOR or JSON)"),
        index: Optional[int] = typer.Option(
            None, "--index", "-i", help="If JSON contains a list, pick element index"
        ),
        json_out: bool = typer.Option(
            False, "--json", help="Print machine-readable JSON result"
        ),
        roots_dir: Optional[Path] = typer.Option(
            None, "--roots-dir", help="Vendor roots directory (overrides env ANIMICA_VENDOR_ROOTS)"
        ),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress human tables"),
    ) -> None:
        """
        Verify a single proof file. Auto-detects format and type.
        """
        try:
            env = _load_json_or_cbor(path, index)
            # Name resolution via registry (for printing)
            kind_name = registry.type_name(env.type_id)  # type: ignore[attr-defined]
            metrics, psi_inputs = _verify_envelope(env, roots_dir)
        except (ValueError, ProofError) as e:
            if json_out:
                print(json.dumps({"ok": False, "error": str(e), "file": str(path)}))
                raise typer.Exit(1)
            _die(f"[verify] {path}: {e}", 1)
            return

        if json_out:
            out = {
                "ok": True,
                "file": str(path),
                "type_id": env.type_id,
                "type_name": kind_name,
                "nullifier": _bytes_to_hex(env.nullifier) if isinstance(env.nullifier, bytes) else env.nullifier,
                "metrics": _to_jsonable(metrics),
                "psi_inputs": _to_jsonable(psi_inputs),
            }
            print(json.dumps(out, indent=2, sort_keys=True))
            return

        if quiet:
            # Minimal success line
            print(f"OK {path} type={env.type_id}({kind_name}) nullifier="
                  f"{_bytes_to_hex(env.nullifier) if isinstance(env.nullifier, bytes) else env.nullifier}")
            return

        if Console is None:  # fallback plain text
            print(f"File:       {path}")
            print(f"Type:       {env.type_id} ({kind_name})")
            print(f"Nullifier:  "
                  f"{_bytes_to_hex(env.nullifier) if isinstance(env.nullifier, bytes) else env.nullifier}")
            print("\n[Metrics]")
            for k, v in metrics.items():
                if isinstance(v, dict):
                    for sk, sv in v.items():
                        print(f"  {k}.{sk}: {sv}")
                else:
                    print(f"  {k}: {v}")
            print("\n[ψ-inputs]")
            for k, v in psi_inputs.items():
                print(f"  {k}: {v}")
            return

        # Pretty report with rich
        console = Console()
        _human_report(console, path, env, kind_name, metrics, psi_inputs)

    return app


def main(argv: Optional[list[str]] = None) -> int:
    app = build_app()
    if app is None:  # pragma: no cover
        return 2
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
