#!/usr/bin/env python3
"""
proofs.cli.proof_build_hashshare
================================
Build a HashShare proof envelope from a header *template* and a chosen nonce,
then self-verify it using the local verifier.

This is handy for:
 • sanity-checking mining header templates
 • reproducing spec/test_vectors/hashshare.json cases
 • experimenting with the u-draw and D_ratio around Θ

Inputs
------
--header:   Header template (JSON or CBOR). This should be the exact subset the
            verifier binds (roots, Θ/params, chainId, mixSeed/nonce domain pieces
            but *without* the nonce itself if your template format separates it).
--nonce:    Nonce to use (hex or base10). If omitted, a random 8–32 byte nonce
            is generated.
--theta:    Optional Θ (µ-nats) override for local ratio reporting. Verifier does
            not need this; it's for the human report.
--out:      Output CBOR envelope path.
--json:     Also write a JSON view (debug).

Examples
--------
python -m proofs.cli.proof_build_hashshare \
  --header ./mining/fixtures/header_template.json \
  --nonce 0x00000000000000beef \
  --out ./hashshare.cbor --json ./hashshare.json

python -m proofs.cli.proof_build_hashshare \
  --header ./consensus/fixtures/genesis_header.json \
  --out ./hashshare.cbor
"""

from __future__ import annotations
import binascii
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import typer  # type: ignore
except Exception:  # pragma: no cover
    print("This tool requires 'typer'. Try: pip install 'typer[all]'")
    raise

# Pretty output (optional)
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
from proofs.types import ProofEnvelope  # type: ignore
from proofs import cbor as proofs_cbor
from proofs import registry
from proofs import nullifiers as nul

# Optional helpers if present; we fail soft if not.
try:
    from proofs import hashshare as hs_mod  # type: ignore
except Exception:  # pragma: no cover
    hs_mod = None  # type: ignore


app = typer.Typer(
    name="proof-build-hashshare",
    help="Assemble a HashShare proof from a header template + nonce, then verify",
    no_args_is_help=True,
    add_completion=False,
)

# ---------------- util helpers ----------------

def _read_json_or_cbor(path: Path) -> Any:
    data = path.read_bytes()
    # Heuristic: CBOR often starts with 0xa? (map) or 0x8? (array) etc. Try JSON first.
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        pass
    try:
        import cbor2  # type: ignore
        return cbor2.loads(data)
    except Exception as e:
        raise typer.BadParameter(f"Failed to parse {path} as JSON or CBOR: {e}")

def _parse_nonce(value: Optional[str]) -> bytes:
    if value is None:
        # 16-byte random default (domain-agnostic; verifier defines length semantics)
        return os.urandom(16)
    s = value.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    # Allow odd-length hex and base10 int
    try:
        # hex first
        if all(c in "0123456789abcdef" for c in s):
            if len(s) % 2 == 1:
                s = "0" + s
            return binascii.unhexlify(s)
        # else attempt base10
        n = int(value, 10)
        if n < 0:
            raise ValueError("nonce must be non-negative")
        # minimal big-endian length
        out = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
        return out
    except Exception as e:
        raise typer.BadParameter(f"Invalid --nonce value: {e}")

def _resolve_hashshare_type_id() -> int:
    # Ask registry by common names; fallback to 0
    for name in ("hashshare", "HashShare", "hash", "HASH"):
        for fn_name in ("type_id_by_name", "type_id", "name_to_id"):
            fn = getattr(registry, fn_name, None)
            if fn:
                try:
                    tid = fn(name)  # type: ignore
                    if isinstance(tid, int):
                        return tid
                except Exception:
                    pass
    return 0

def _compute_body(header_tmpl: Dict[str, Any], nonce: bytes) -> Dict[str, Any]:
    """
    Prefer an official builder from proofs.hashshare if present; otherwise
    produce a minimal portable body that verifiers can recompute from.
    """
    if hs_mod:
        for cand in ("build_body", "build", "make_body"):
            fn = getattr(hs_mod, cand, None)
            if callable(fn):
                try:
                    body = fn(header_tmpl, nonce)  # type: ignore
                    if isinstance(body, dict):
                        return body
                except Exception:
                    pass
    # Minimal, schema-friendly body; verifier recomputes binding/u-draw internally.
    return {
        "version": 1,
        "header": header_tmpl,   # bound subset; verifier will canonicalize
        "nonce": nonce,          # raw bytes; not hex
    }

def _encode_body(type_id: int, body: Dict[str, Any]) -> bytes:
    # proofs.cbor encoder knows how to encode proof bodies canonically.
    for fn_name in ("encode_body", "encode_proof_body", "encode_hashshare_body"):
        fn = getattr(proofs_cbor, fn_name, None)
        if callable(fn):
            try:
                return fn(type_id, body)  # type: ignore
            except TypeError:
                try:
                    return fn("hashshare", body)  # alt signature
                except Exception:
                    pass
            except Exception:
                pass
    # Fallback: raw CBOR (not consensus-safe but fine for local tooling)
    import cbor2  # type: ignore
    return cbor2.dumps(body)

def _compute_nullifier(type_id: int, body_cbor: bytes) -> bytes:
    for cand in ("compute_nullifier", "nullifier_for_body", "nullifier"):
        fn = getattr(nul, cand, None)
        if callable(fn):
            try:
                return fn(type_id, body_cbor)  # type: ignore
            except Exception:
                pass
    # Domain-separated fallback (must match proofs/nullifiers.py if present)
    import hashlib
    tag = b"animica:nullifier:hashshare:v1"
    return hashlib.sha3_256(tag + type_id.to_bytes(2, "big") + body_cbor).digest()

def _verify_envelope(env: ProofEnvelope) -> Dict[str, Any]:
    """
    Attempt to verify using registry. Return a dict with keys:
      ok: bool
      metrics: dict (if available)
      error: str (if not ok)
    """
    # Try registry helpers in a tolerant order
    for name in ("verify_envelope", "verify", "verify_proof", "verify_env"):
        fn = getattr(registry, name, None)
        if callable(fn):
            try:
                res = fn(env)  # type: ignore
                # Common conventions: either bool/metrics or object with attrs
                if isinstance(res, tuple) and len(res) == 2:
                    ok, metrics = res
                    return {"ok": bool(ok), "metrics": metrics or {}}
                if isinstance(res, dict) and "ok" in res:
                    return {"ok": bool(res["ok"]), "metrics": res.get("metrics", {})}
                if isinstance(res, bool):
                    return {"ok": res, "metrics": {}}
            except Exception as e:
                return {"ok": False, "metrics": {}, "error": f"{name} failed: {e}"}
    # Last resort: call proofs.hashshare.verify() directly if available
    if hs_mod:
        for nm in ("verify", "verify_body", "verify_envelope"):
            fn = getattr(hs_mod, nm, None)
            if callable(fn):
                try:
                    if nm == "verify_body":
                        ok, metrics = fn(env.body)  # type: ignore
                    else:
                        ok, metrics = fn(env)       # type: ignore
                    return {"ok": bool(ok), "metrics": metrics or {}}
                except Exception as e:
                    return {"ok": False, "metrics": {}, "error": f"hashshare.{nm} failed: {e}"}
    return {"ok": False, "metrics": {}, "error": "No verifier available"}

def _save_outputs(env: ProofEnvelope, out_path: Optional[Path], json_path: Optional[Path]) -> None:
    if out_path:
        try:
            data = proofs_cbor.encode_envelope(env)  # type: ignore[attr-defined]
        except Exception:
            import cbor2  # type: ignore
            data = cbor2.dumps({"type_id": env.type_id, "body": env.body, "nullifier": env.nullifier})
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
    if json_path:
        def b2h(b: bytes) -> str:
            return "0x" + b.hex()
        view = {
            "type_id": env.type_id,
            "nullifier": b2h(env.nullifier) if isinstance(env.nullifier, bytes) else env.nullifier,
            "body": env.body,
        }
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(view, indent=2, sort_keys=True), encoding="utf-8")

def _pretty(console: Any, env: ProofEnvelope, verify_res: Dict[str, Any], theta_micro: Optional[int]) -> None:
    t = Table(title="HashShare Envelope", box=box.SIMPLE if box else None)
    t.add_column("Field"); t.add_column("Value", overflow="fold")
    t.add_row("type_id", str(env.type_id))
    t.add_row("nullifier", "0x" + env.nullifier.hex() if isinstance(env.nullifier, bytes) else str(env.nullifier))
    t.add_row("version", str(env.body.get("version") if isinstance(env.body, dict) else "?"))
    if isinstance(env.body, dict):
        header = env.body.get("header")
        nonce = env.body.get("nonce")
        t.add_row("header.present", "yes" if header is not None else "no")
        if isinstance(nonce, (bytes, bytearray)):
            t.add_row("nonce", "0x" + bytes(nonce).hex())
        elif isinstance(nonce, str):
            t.add_row("nonce", nonce)
    console.print(Panel(t, title="Assembled", expand=False))

    ok = verify_res.get("ok", False)
    metrics = verify_res.get("metrics") or {}
    tm = Table(title="Verification", box=box.SIMPLE if box else None)
    tm.add_column("Metric"); tm.add_column("Value", overflow="fold")
    tm.add_row("ok", "✅" if ok else "❌")
    # Common metrics if exposed by verifier
    for k in ("d_ratio", "H_u_micro", "u", "target_micro", "header_hash"):
        if k in metrics:
            v = metrics[k]
            if isinstance(v, (bytes, bytearray)):
                v = "0x" + bytes(v).hex()
            tm.add_row(k, str(v))
    # Human-estimated ratio when Θ provided
    if theta_micro is not None and "H_u_micro" in metrics:
        try:
            Hu = int(metrics["H_u_micro"])
            ratio = Hu / max(1, int(theta_micro))
            tm.add_row("H(u)/Θ (est.)", f"{ratio:.6f}")
        except Exception:
            pass
    console.print(tm)

# ---------------- CLI ----------------

@app.callback()
def _meta(version: bool = typer.Option(False, "--version", "-V", help="Print version and exit", is_eager=True)) -> None:
    if version:
        typer.echo(f"animica-proofs {__version__}")
        raise typer.Exit(0)

@app.command("build")
def build_hashshare(
    header: Path = typer.Option(..., "--header", "-H", help="Header template file (JSON or CBOR)"),
    nonce: Optional[str] = typer.Option(None, "--nonce", "-n", help="Nonce (hex like 0x.. or base10). Omit to randomize."),
    theta_micro: Optional[int] = typer.Option(None, "--theta", help="Optional Θ in µ-nats for reporting only"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Write CBOR envelope"),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Also write JSON view"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="No pretty output"),
) -> None:
    """
    Assemble a HashShare proof body: {version, header, nonce}, wrap in a ProofEnvelope,
    compute a nullifier, verify locally, and write outputs.
    """
    header_obj = _read_json_or_cbor(header)
    if not isinstance(header_obj, dict):
        raise typer.BadParameter("--header must decode to a JSON/CBOR object (map)")

    nonce_b = _parse_nonce(nonce)

    # Build body (prefer official builder if available)
    body = _compute_body(header_obj, nonce_b)

    # Encode & nullify
    type_id = _resolve_hashshare_type_id()
    body_cbor = _encode_body(type_id, body)
    nullifier = _compute_nullifier(type_id, body_cbor)

    env = ProofEnvelope(type_id=type_id, body=body, nullifier=nullifier)  # type: ignore

    # Verify using local registry
    verify_res = _verify_envelope(env)

    # Save artifacts
    _save_outputs(env, out, json_out)

    # Output
    if quiet or Console is None:
        ok = verify_res.get("ok", False)
        ok_s = "OK" if ok else "FAIL"
        print(f"{ok_s} type_id={type_id} nullifier=0x{nullifier.hex()} out={out or '-'} json={json_out or '-'}")
        return

    console = Console()
    _pretty(console, env, verify_res, theta_micro)

def main() -> int:
    app()
    return 0

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
