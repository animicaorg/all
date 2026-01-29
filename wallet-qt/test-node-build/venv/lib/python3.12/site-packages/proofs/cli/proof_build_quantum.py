#!/usr/bin/env python3
"""
proofs.cli.proof_build_quantum
==============================
Assemble a QuantumProof envelope from:
  • Provider identity/attestation (X.509 / JSON bundle; optional chain PEM)
  • Trap-circuit outcomes (JSON list with pass/fail counts or per-trap details)
  • Optional circuit description to hash (QASM/JSON bytes)
  • Optional QoS meta (latency ms, queue time, backend/device id)

It emits a canonical ProofEnvelope (CBOR by default, JSON optional) with
a computed nullifier and the correct type_id for the Quantum proof kind.

Examples
--------
# Provider PEM + chain + traps + hash QASM file:
python -m proofs.cli.proof_build_quantum \
  --provider ionq \
  --provider-cert ./fixtures/qpu_ionq_provider.pem \
  --cert-chain ./fixtures/qpu_ionq_chain.pem \
  --traps ./fixtures/trap_receipts.json \
  --circuit ./circuits/ghz_8.qasm \
  --shots 2000 \
  --out ./quantum_proof.cbor \
  --json ./quantum_proof.json

# Pre-built attestation JSON; explicit digest; include QoS fields:
python -m proofs.cli.proof_build_quantum \
  --attest-json ./attest_bundle.json \
  --traps ./fixtures/trap_receipts.json \
  --digest 0x5e3a...cafe \
  --latency-ms 320 \
  --device-id qpu-nova-02 \
  --out ./quantum_proof.cbor
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
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
    name="proof-build-quantum",
    help="Assemble a QuantumProof envelope from provider cert + trap outcomes",
    no_args_is_help=True,
    add_completion=False,
)

# -------------- helpers --------------


def _b64(x: bytes) -> str:
    return base64.b64encode(x).decode("ascii")


def _read_bytes(p: Path) -> bytes:
    return p.read_bytes()


def _sha3_256(b: bytes) -> bytes:
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


def _resolve_quantum_type_id() -> int:
    # Try common names via registry
    for name in ("quantum", "Quantum", "quantum_proof", "QUANTUM_PROOF"):
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
    return 3  # placeholder fallback; real registry provides canonical id


def _encode_body(type_id: int, body: Dict[str, Any]) -> bytes:
    # Prefer canonical encoder
    for fn_name in ("encode_body", "encode_proof_body", "encode_quantum_body"):
        fn = getattr(proofs_cbor, fn_name, None)
        if fn:
            try:
                return fn(type_id, body)  # type: ignore[misc]
            except TypeError:
                try:
                    return fn("quantum", body)  # type: ignore[misc]
                except Exception:
                    pass
            except Exception:
                pass
    # Fallback to cbor2
    try:
        import cbor2  # type: ignore

        return cbor2.dumps(body)
    except Exception as e:
        raise RuntimeError(
            f"Could not CBOR-encode body; install cbor2 or ensure proofs.cbor has encode_body(): {e}"
        )


def _compute_nullifier(type_id: int, body_cbor: bytes) -> bytes:
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
    tag = b"animica:nullifier:quantum:v1"
    return hashlib.sha3_256(tag + type_id.to_bytes(2, "big") + body_cbor).digest()


def _build_attestation_bundle(
    provider: Optional[str],
    provider_cert: Optional[Path],
    cert_chain: Optional[Path],
    attest_json: Optional[Path],
) -> Dict[str, Any]:
    """
    Construct a JSON-ish "attestation" structure that matches
    proofs/schemas/quantum_attestation.schema.json.

    If attest_json is provided, it's used verbatim (must be a JSON object).
    Otherwise, embed PEMs as base64 strings and tag with provider.
    """
    if attest_json:
        obj = _load_json(attest_json)
        if not isinstance(obj, dict):
            raise typer.BadParameter("--attest-json must contain a JSON object")
        # Fill provider if missing (optional)
        if provider and "provider" not in obj:
            obj["provider"] = provider
        return obj

    if not provider_cert:
        raise typer.BadParameter("Provide --provider-cert PEM or --attest-json bundle")

    att: Dict[str, Any] = {
        "provider": provider or "unknown",
        "evidence": {},
    }

    pem_b64 = _b64(_read_bytes(provider_cert))
    att["evidence"]["provider_cert_pem"] = pem_b64

    if cert_chain and cert_chain.exists():
        att["evidence"]["chain_pem"] = _b64(_read_bytes(cert_chain))

    return att


def _load_traps(path: Path) -> Any:
    """
    Load trap outcomes. Expected formats supported:

    A) Compact summary:
      {"total": 100, "passed": 97}

    B) Detailed list:
      [{"trap_id":"t1","shots":64,"passed":63},{"trap_id":"t2","shots":64,"passed":61}, ...]

    The builder computes a summary for convenience; verifiers recompute independently.
    """
    obj = _load_json(path)
    if isinstance(obj, dict) and "total" in obj and "passed" in obj:
        total = int(obj["total"])
        passed = int(obj["passed"])
        if total <= 0 or passed < 0 or passed > total:
            raise typer.BadParameter(
                "Invalid trap summary: passed must be in [0,total]"
            )
        details = obj.get("details") if isinstance(obj.get("details"), list) else None
        return {"summary": {"total": total, "passed": passed}, "details": details or []}

    if isinstance(obj, list):
        total = 0
        passed = 0
        for it in obj:
            if not isinstance(it, dict):
                raise typer.BadParameter("Trap list entries must be objects")
            p = int(it.get("passed", 0))
            s = int(it.get("shots", 0))
            # Interpret "shots" as number of samples and "passed" as count of trap hits
            total += s if s > 0 else 1
            passed += p if s > 0 else (1 if p else 0)
        if total <= 0 or passed < 0 or passed > total:
            raise typer.BadParameter("Computed invalid trap totals from list")
        return {"summary": {"total": total, "passed": passed}, "details": obj}

    raise typer.BadParameter(
        "--traps must be a JSON object with total/passed or a JSON array"
    )


def _digest_from_args(circuit: Optional[Path], digest_hex: Optional[str]) -> bytes:
    if digest_hex:
        return _parse_hex_bytes(digest_hex)
    if not circuit:
        raise typer.BadParameter(
            "Provide --circuit to hash OR --digest with a hex value"
        )
    return _sha3_256(_read_bytes(circuit))


def _maybe_hex_to_bytes(x: Optional[str]) -> Optional[bytes]:
    if x is None:
        return None
    return _parse_hex_bytes(x)


def _save_outputs(
    env: ProofEnvelope, out_path: Optional[Path], json_path: Optional[Path]
) -> None:
    if out_path:
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


def _human_report(
    console: Any, env: ProofEnvelope, traps_summary: Dict[str, int]
) -> None:
    t = Table(title="QuantumProof Envelope", box=box.SIMPLE if box else None)
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

    b = env.body if isinstance(env.body, dict) else {}
    tb = Table(title="Body Summary", box=box.SIMPLE if box else None)
    tb.add_column("Key")
    tb.add_column("Summary", overflow="fold")
    tb.add_row("version", str(b.get("version")))
    att = b.get("attestation", {})
    if isinstance(att, dict):
        tb.add_row("attestation.provider", str(att.get("provider")))
        evid = att.get("evidence", {})
        if isinstance(evid, dict):
            if evid.get("provider_cert_pem"):
                tb.add_row("evidence.provider_cert_pem", "present (PEM)")
            if evid.get("chain_pem"):
                tb.add_row("evidence.chain_pem", "present (PEM)")
    circ = b.get("circuit")
    if isinstance(circ, dict):
        if circ.get("digest"):
            tb.add_row(
                "circuit.digest",
                str(
                    circ.get("digest")
                    if isinstance(circ["digest"], str)
                    else "0x" + bytes(circ["digest"]).hex()
                ),
            )
        if circ.get("shots") is not None:
            tb.add_row("circuit.shots", str(circ.get("shots")))
        if circ.get("format"):
            tb.add_row("circuit.format", str(circ.get("format")))
    tb.add_row("traps.total", str(traps_summary.get("total", 0)))
    tb.add_row("traps.passed", str(traps_summary.get("passed", 0)))
    qos = b.get("qos", {})
    if isinstance(qos, dict):
        for k in ("latency_ms", "queue_ms", "backend", "device_id"):
            if qos.get(k) is not None:
                tb.add_row(f"qos.{k}", str(qos[k]))
    console.print(tb)


# -------------- CLI --------------


@app.callback()
def _meta(
    version: bool = typer.Option(
        False, "--version", "-V", help="Print version and exit", is_eager=True
    )
) -> None:
    if version:
        typer.echo(f"animica-proofs {__version__}")
        raise typer.Exit(0)


@app.command("build")
def build_quantum_proof(
    # Attestation
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="Provider name hint (ionq|rigetti|oxfordq|ibm|aws|google|...)",
    ),
    provider_cert: Optional[Path] = typer.Option(
        None, "--provider-cert", help="Provider identity certificate (PEM)"
    ),
    cert_chain: Optional[Path] = typer.Option(
        None, "--cert-chain", help="Optional PEM chain bundle"
    ),
    attest_json: Optional[Path] = typer.Option(
        None, "--attest-json", help="Pre-built attestation JSON bundle"
    ),
    # Traps
    traps: Path = typer.Option(
        ..., "--traps", help="Trap outcomes JSON (summary or list)"
    ),
    # Circuit description → digest
    circuit: Optional[Path] = typer.Option(
        None, "--circuit", help="Circuit file (QASM/JSON) to hash with sha3-256"
    ),
    digest: Optional[str] = typer.Option(
        None, "--digest", help="Explicit circuit digest (hex)"
    ),
    shots: Optional[int] = typer.Option(
        None, "--shots", help="Number of circuit shots actually executed"
    ),
    circuit_format: Optional[str] = typer.Option(
        None, "--circuit-format", help="freeform hint: qasm|json|cirq|quil|..."
    ),
    # QoS / device meta (optional; non-consensus)
    latency_ms: Optional[int] = typer.Option(
        None, "--latency-ms", help="Observed end-to-end latency in ms"
    ),
    queue_ms: Optional[int] = typer.Option(
        None, "--queue-ms", help="Queue wait time in ms"
    ),
    backend: Optional[str] = typer.Option(
        None, "--backend", help="Backend family (e.g., trapped-ion, superconducting)"
    ),
    device_id: Optional[str] = typer.Option(
        None, "--device-id", help="Provider device id/alias"
    ),
    # Optional linkage
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
    Build a QuantumProof envelope and write it to disk.
    """
    # 1) Attestation bundle
    att = _build_attestation_bundle(
        provider=provider,
        provider_cert=provider_cert,
        cert_chain=cert_chain,
        attest_json=attest_json,
    )

    # 2) Traps
    traps_obj = _load_traps(traps)
    traps_summary = traps_obj["summary"]  # {"total":..., "passed":...}
    traps_details = traps_obj.get("details", [])

    # 3) Circuit digest
    circ_digest = _digest_from_args(circuit, digest)

    # 4) Optional task id
    task_id_bytes = _maybe_hex_to_bytes(task_id)

    # 5) Assemble body (versioned)
    body: Dict[str, Any] = {
        "version": 1,
        "attestation": att,
        "traps": {
            "summary": {
                "total": int(traps_summary["total"]),
                "passed": int(traps_summary["passed"]),
            },
            "details": traps_details,
        },
        "circuit": {
            "digest": circ_digest,  # raw bytes; encoder tags as bstr
            "shots": int(shots) if shots is not None else None,
            "format": circuit_format,
        },
    }
    # Remove None fields from 'circuit'
    body["circuit"] = {k: v for k, v in body["circuit"].items() if v is not None}

    if any(v is not None for v in (latency_ms, queue_ms, backend, device_id)):
        body["qos"] = {}
        if latency_ms is not None:
            body["qos"]["latency_ms"] = int(latency_ms)
        if queue_ms is not None:
            body["qos"]["queue_ms"] = int(queue_ms)
        if backend is not None:
            body["qos"]["backend"] = backend
        if device_id is not None:
            body["qos"]["device_id"] = device_id

    if task_id_bytes is not None:
        body["task_id"] = task_id_bytes
    if job_hint is not None:
        body["meta"] = {"job_hint": job_hint}

    # 6) Encode & nullifier
    type_id = _resolve_quantum_type_id()
    body_cbor = _encode_body(type_id, body)
    nullifier = _compute_nullifier(type_id, body_cbor)

    # 7) Envelope
    env = ProofEnvelope(type_id=type_id, body=body, nullifier=nullifier)  # type: ignore

    # 8) Save
    _save_outputs(env, out, json_out)

    # 9) Print
    if quiet:
        print(
            f"OK type_id={type_id} nullifier=0x{nullifier.hex()} cbor_out={out or '-'} json_out={json_out or '-'}"
        )
        return

    if Console is None:
        print(
            f"QuantumProof built.\n type_id={type_id}\n nullifier=0x{nullifier.hex()}\n out={out}\n json={json_out}"
        )
        return

    console = Console()
    _human_report(console, env, traps_summary)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
