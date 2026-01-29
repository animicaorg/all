#!/usr/bin/env python3
"""
proofs.cli.proof_build_ai
=========================
Assemble an AIProof envelope from:
  • TEE attestation evidence (SGX | SEV-SNP | Arm CCA | prebuilt JSON bundle)
  • Trap receipts (JSON list)
  • Output digest (sha3-256 of model output OR a provided hex digest)

It emits a canonical ProofEnvelope (CBOR by default, JSON optional) with
a computed nullifier and the correct type_id for the AI proof kind.

Examples
--------
# SGX quote + traps.json + hash the output file:
python -m proofs.cli.proof_build_ai \
  --attest sgx --quote ./fixtures/sgx_quote.bin \
  --traps ./fixtures/trap_receipts.json \
  --out-file ./outputs/sample.txt \
  --out ~/ai_proof.cbor

# Pre-built attestation JSON bundle + explicit hex digest; also write JSON:
python -m proofs.cli.proof_build_ai \
  --attest json --attest-json ./attest_bundle.json \
  --traps ./fixtures/trap_receipts.json \
  --digest 0x7b7f...deadbeef \
  --out ./ai_proof.cbor \
  --json ./ai_proof.json

Notes
-----
• This builder does not *verify* the TEE evidence; it only packages it.
  The on-chain/off-chain verifier in proofs/ will verify it later.
• You can pass ANIMICA_VENDOR_ROOTS to point verifiers at vendor roots later.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import typer  # type: ignore
except Exception:  # pragma: no cover
    print("This tool requires 'typer'. Try: pip install 'typer[all]'")
    raise

# Optional pretty output
try:
    from rich import box  # type: ignore
    from rich.console import Console  # type: ignore
    from rich.panel import Panel  # type: ignore
    from rich.table import Table  # type: ignore
except Exception:  # pragma: no cover
    Console = None  # type: ignore
    Table = None  # type: ignore
    Panel = None  # type: ignore
    box = None  # type: ignore

from proofs import cbor as proofs_cbor
from proofs import nullifiers as nul
from proofs import registry
from proofs.types import ProofEnvelope  # type: ignore
# Animica libs (internal)
from proofs.version import __version__

app = typer.Typer(
    name="proof-build-ai",
    help="Assemble an AIProof envelope from TEE attestation + traps + output digest",
    no_args_is_help=True,
    add_completion=False,
)

# ---------- helpers ----------


def _b64(x: bytes) -> str:
    return base64.b64encode(x).decode("ascii")


def _read_bytes(p: Path) -> bytes:
    return p.read_bytes()


def _sha3_256_bytes(b: bytes) -> bytes:
    # hashlib.sha3_256 is available in Python 3.8+
    return hashlib.sha3_256(b).digest()


def _parse_hex_bytes(s: str) -> bytes:
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise typer.BadParameter(f"Invalid hex digest: {e}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_dataclass(obj: Any) -> bool:
    return is_dataclass(obj)


def _dc_to_dict(x: Any) -> Any:
    if is_dataclass(x):
        return asdict(x)
    if isinstance(x, dict):
        return {k: _dc_to_dict(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_dc_to_dict(v) for v in x]
    return x


def _resolve_ai_type_id() -> int:
    # Try multiple registry APIs for resilience
    for name in ("ai", "AI", "ai_proof", "AI_PROOF"):
        for fn in (
            getattr(registry, "type_id_by_name", None),
            getattr(registry, "type_id", None),
            getattr(registry, "name_to_id", None),
        ):
            if fn is None:
                continue
            try:
                tid = fn(name)  # type: ignore
                if isinstance(tid, int):
                    return tid
            except Exception:
                pass
    # Fallback to a conventional id (should be overridden by real registry)
    return 2  # <- placeholder fallback; registry should supply the canonical id


def _encode_body(type_id: int, body: Dict[str, Any]) -> bytes:
    # Prefer our canonical encoder
    for fn_name in ("encode_body", "encode_proof_body", "encode_ai_body"):
        fn = getattr(proofs_cbor, fn_name, None)
        if fn:
            try:
                # some encoders expect (type_id, body), others (name, body)
                return fn(type_id, body)  # type: ignore[misc]
            except TypeError:
                try:
                    return fn("ai", body)  # type: ignore[misc]
                except Exception:
                    pass
            except Exception:
                pass
    # Last resort: try cbor2 if installed
    try:
        import cbor2  # type: ignore

        return cbor2.dumps(body)
    except Exception as e:
        raise RuntimeError(
            f"Could not CBOR-encode body; install cbor2 or ensure proofs.cbor has encode_body(): {e}"
        )


def _compute_nullifier(type_id: int, body_cbor: bytes) -> bytes:
    # Prefer generic compute(type_id, body_cbor)
    for candidate in ("compute_nullifier", "nullifier_for_body", "nullifier"):
        fn = getattr(nul, candidate, None)
        if fn:
            try:
                return fn(type_id, body_cbor)  # type: ignore[misc]
            except TypeError:
                pass
            except Exception:
                pass
    # Domain-separated fallback (must match proofs/nullifiers.py domain if present)
    tag = b"animica:nullifier:ai:v1"
    return hashlib.sha3_256(tag + type_id.to_bytes(2, "big") + body_cbor).digest()


def _build_attestation_bundle(
    kind: str,
    quote: Optional[Path],
    report: Optional[Path],
    token: Optional[Path],
    evidence_json: Optional[Path],
) -> Dict[str, Any]:
    """
    Construct a JSON-ish "attestation" structure that matches proofs/schemas/ai_attestation.schema.json.
    If evidence_json is supplied, it's used verbatim.
    """
    if evidence_json:
        obj = _load_json(evidence_json)
        if not isinstance(obj, dict):
            raise typer.BadParameter("--attest-json must contain a JSON object")
        return obj

    kind = kind.lower()
    if kind == "sgx":
        if not quote:
            raise typer.BadParameter("--quote is required for --attest sgx")
        return {
            "type": "sgx",
            "quote": _b64(_read_bytes(quote)),
            # Optional aux fields for richer evidence (can be empty; verifiers may fetch from PCS)
            "issuer_chain_pem": None,
            "qe_identity": None,
            "tcb_info": None,
        }
    if kind in ("sev-snp", "sevsnp", "sev_snp"):
        if not report:
            raise typer.BadParameter("--report is required for --attest sev-snp")
        return {
            "type": "sev-snp",
            "report": _b64(_read_bytes(report)),
            "ark_chain_pem": None,
            "ask_chain_pem": None,
        }
    if kind in ("cca", "arm-cca", "arm_cca"):
        if not token:
            raise typer.BadParameter("--token is required for --attest cca")
        # CCA uses a COSE/CBOR token; we carry it as base64
        return {
            "type": "cca",
            "realm_token_cbor": _b64(_read_bytes(token)),
        }
    raise typer.BadParameter(f"Unsupported --attest kind: {kind}")


def _load_traps(path: Path) -> Any:
    """
    traps.json should be a list of receipts like:
      [{"trap_id":"0x..","samples":64,"passed":63,"timestamp":...}, ...]
    The verifier will sanity-check thresholds; builder just packages them.
    """
    obj = _load_json(path)
    if not isinstance(obj, list):
        raise typer.BadParameter("--traps must be a JSON array")
    return obj


def _digest_from_args(out_file: Optional[Path], digest_hex: Optional[str]) -> bytes:
    if digest_hex:
        return _parse_hex_bytes(digest_hex)
    if not out_file:
        raise typer.BadParameter(
            "Provide --out-file to hash OR --digest with a hex value"
        )
    return _sha3_256_bytes(_read_bytes(out_file))


def _maybe_hex_to_bytes(x: Optional[str]) -> Optional[bytes]:
    if x is None:
        return None
    return _parse_hex_bytes(x)


def _save_outputs(
    env: ProofEnvelope,
    out_path: Optional[Path],
    json_path: Optional[Path],
) -> None:
    if out_path:
        # Encode via proofs.cbor if possible, else fallback to cbor2
        try:
            data = proofs_cbor.encode_envelope(env)  # type: ignore[attr-defined]
        except Exception:
            try:
                import cbor2  # type: ignore

                data = cbor2.dumps(
                    {
                        "type_id": env.type_id,
                        "body": env.body,
                        "nullifier": env.nullifier,
                    }
                )
            except Exception as e:
                raise RuntimeError(f"Failed to encode envelope CBOR: {e}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
    if json_path:
        # Make a JSON-safe view
        def b2h(b: bytes) -> str:
            return "0x" + b.hex()

        view = {
            "type_id": env.type_id,
            "body": env.body,
            "nullifier": (
                b2h(env.nullifier)
                if isinstance(env.nullifier, bytes)
                else env.nullifier
            ),
        }
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(view, indent=2, sort_keys=True), encoding="utf-8"
        )


def _human_report(console: Any, env: ProofEnvelope) -> None:
    t = Table(title="AIProof Envelope", box=box.SIMPLE if box else None)
    t.add_column("Field")
    t.add_column("Value", overflow="fold")
    t.add_row("type_id", str(env.type_id))
    nullifier_hex = (
        ("0x" + env.nullifier.hex())
        if isinstance(env.nullifier, bytes)
        else str(env.nullifier)
    )
    t.add_row("nullifier", nullifier_hex)
    console.print(Panel(t, title="Assembled", expand=False))

    # Body summary
    b = env.body if isinstance(env.body, dict) else {}
    tb = Table(title="Body Summary", box=box.SIMPLE if box else None)
    tb.add_column("Key")
    tb.add_column("Summary", overflow="fold")
    tb.add_row("version", str(b.get("version")))
    att = b.get("attestation", {})
    if isinstance(att, dict):
        tb.add_row("attestation.type", str(att.get("type")))
        for key in ("quote", "report", "realm_token_cbor"):
            if att.get(key):
                size = len(att[key]) if isinstance(att[key], str) else len(att[key])
                tb.add_row(f"attestation.{key}", f"present (len={size})")
    traps = b.get("traps", [])
    tb.add_row("traps", f"{len(traps)} receipts")
    od = b.get("output_digest")
    if isinstance(od, (bytes, bytearray)):
        tb.add_row("output_digest", "0x" + bytes(od).hex())
    elif isinstance(od, str):
        tb.add_row("output_digest", od)
    console.print(tb)


# ---------- CLI ----------


@app.callback()
def _meta(
    version: bool = typer.Option(
        False, "--version", "-V", help="Print version and exit", is_eager=True
    ),
) -> None:
    if version:
        typer.echo(f"animica-proofs {__version__}")
        raise typer.Exit(0)


@app.command("build")
def build_ai_proof(
    # Attestation
    attest: str = typer.Option(..., "--attest", help="sgx | sev-snp | cca | json"),
    quote: Optional[Path] = typer.Option(None, "--quote", help="SGX quote (binary)"),
    report: Optional[Path] = typer.Option(
        None, "--report", help="SEV-SNP report (binary)"
    ),
    token: Optional[Path] = typer.Option(
        None, "--token", help="Arm CCA Realm token (CBOR/COSE)"
    ),
    attest_json: Optional[Path] = typer.Option(
        None, "--attest-json", help="Pre-built attestation JSON bundle"
    ),
    # Traps
    traps: Path = typer.Option(..., "--traps", help="Trap receipts JSON file"),
    # Output digest (one of)
    out_file: Optional[Path] = typer.Option(
        None, "--out-file", help="Path to model output to hash with sha3-256"
    ),
    digest: Optional[str] = typer.Option(
        None, "--digest", help="Explicit output digest (hex)"
    ),
    # Optional linkages
    task_id: Optional[str] = typer.Option(
        None, "--task-id", help="Optional task id (hex) for correlation"
    ),
    job_hint: Optional[str] = typer.Option(
        None, "--job-hint", help="Free-form hint (non-consensus)"
    ),
    # Outputs
    out: Optional[Path] = typer.Option(
        None, "--out", "-o", help="Write CBOR envelope to this file"
    ),
    json_out: Optional[Path] = typer.Option(
        None, "--json", help="Also write JSON envelope to this file"
    ),
    # Display
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="No pretty print; just OK line"
    ),
) -> None:
    """
    Build an AIProof envelope and write it to disk.
    """
    # 1) Build attestation bundle
    att_obj = _build_attestation_bundle(
        kind=attest,
        quote=quote,
        report=report,
        token=token,
        evidence_json=attest_json,
    )

    # 2) Load traps
    traps_list = _load_traps(traps)

    # 3) Output digest
    od_bytes = _digest_from_args(out_file, digest)

    # 4) Optional fields
    task_id_bytes = _maybe_hex_to_bytes(task_id)

    # 5) Construct body (versioned)
    body: Dict[str, Any] = {
        "version": 1,
        "attestation": att_obj,
        "traps": traps_list,
        "output_digest": od_bytes,  # raw bytes; CBOR encoder should tag as bstr
    }
    if task_id_bytes is not None:
        body["task_id"] = task_id_bytes
    if job_hint is not None:
        # Non-consensus metadata (ignored by verifiers); useful for provenance
        body["meta"] = {"job_hint": job_hint}

    # 6) Resolve type id & encode body
    type_id = _resolve_ai_type_id()
    body_cbor = _encode_body(type_id, body)

    # 7) Compute nullifier
    nullifier = _compute_nullifier(type_id, body_cbor)

    # 8) Build envelope dataclass
    env = ProofEnvelope(type_id=type_id, body=body, nullifier=nullifier)  # type: ignore

    # 9) Save to disk
    _save_outputs(env, out, json_out)

    # 10) Report
    if quiet:
        print(
            f"OK type_id={type_id} nullifier=0x{nullifier.hex()} cbor_out={out or '-'} json_out={json_out or '-'}"
        )
        return

    if Console is None:
        print(
            f"AIProof built.\n type_id={type_id}\n nullifier=0x{nullifier.hex()}\n out={out}\n json={json_out}"
        )
        return

    console = Console()
    _human_report(console, env)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
