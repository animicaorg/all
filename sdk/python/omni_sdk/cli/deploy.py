"""
omni_sdk.cli.deploy
===================

Typer sub-commands to deploy a Python-VM contract package to an Animica node.

Typical usage
-------------
    $ omni-sdk deploy package \
        --manifest ./examples/counter/manifest.json \
        --code ./examples/counter/contract.py \
        --seed-hex 0123456789abcdef... \
        --alg dilithium3 \
        --wait

This command:
1) Loads the manifest JSON and contract source,
2) Builds a deploy transaction (estimating gas if needed),
3) Signs it with a PQ signer (Dilithium3 or SPHINCS+),
4) Submits it via JSON-RPC and optionally waits for the receipt.

The command inherits global flags from the root CLI (see `omni-sdk --help`):
- --rpc / OMNI_SDK_RPC_URL
- --chain-id / OMNI_CHAIN_ID
- --timeout / OMNI_SDK_HTTP_TIMEOUT
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import typer

from ..rpc.http import RpcClient  # required
from ..tx.send import await_receipt, send_raw_transaction
from ..tx.encode import encode_tx_cbor  # CBOR encoder for raw tx submission
from ..tx.build import build_deploy_tx, estimate_deploy_gas  # helpers for deploy construction
from ..wallet.signer import Signer  # PQ signer interface
from ..address import Address

# Optional: content-addressed artifact writes
try:
    from ..filestore import ensure_dir, atomic_write, write_blob_ca
except Exception:  # pragma: no cover
    ensure_dir = None
    atomic_write = None
    write_blob_ca = None

# For access to root context (rpc/chain_id/timeout) set by main callback.
try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object  # fallback for type hints only

app = typer.Typer(help="Deploy contracts and packages")

__all__ = ["app"]


# ------------------------------ helpers --------------------------------------


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise typer.BadParameter(f"Manifest not found: {path}") from e
    try:
        return json.loads(text)
    except Exception as e:
        raise typer.BadParameter(f"Manifest is not valid JSON: {path}") from e


def _load_code(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as e:
        raise typer.BadParameter(f"Code file not found: {path}") from e


def _make_signer(alg: str, seed_hex: Optional[str]) -> Signer:
    if not seed_hex:
        # Allow env var as a safer default than interactive prompt for automation
        seed_hex = os.environ.get("OMNI_SDK_SEED_HEX")
    if not seed_hex:
        seed_hex = typer.prompt("Enter signer seed as hex (dev/test only!)", hide_input=True, confirmation_prompt=False)
    try:
        seed = bytes.fromhex(seed_hex.strip().replace("0x", ""))
    except Exception as e:
        raise typer.BadParameter("seed-hex must be raw hex bytes (with or without 0x prefix)") from e
    return Signer.from_seed(seed, alg=alg)  # type: ignore[attr-defined]


def _store_artifacts(out_dir: Optional[Path], *, tx_cbor: bytes, receipt: Optional[Dict[str, Any]] = None) -> None:
    if not out_dir:
        return
    if ensure_dir is None or atomic_write is None:
        # filestore not available; write minimal files
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "tx.cbor").write_bytes(tx_cbor)
        if receipt is not None:
            (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return

    ensure_dir(out_dir)
    atomic_write(out_dir / "tx.cbor", tx_cbor)
    if receipt is not None:
        atomic_write(out_dir / "receipt.json", json.dumps(receipt, indent=2).encode("utf-8"))
    # Also store CBOR under a content-addressed path for reproducibility (best-effort)
    try:
        write_blob_ca(out_dir / "ca", tx_cbor, algo="sha3-256", ext="cbor")
    except Exception:
        pass  # optional


def _summarize(receipt: Optional[Dict[str, Any]], tx_hash: str) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"txHash": tx_hash}
    if receipt:
        summary["status"] = receipt.get("status")
        summary["blockNumber"] = receipt.get("blockNumber")
        summary["gasUsed"] = receipt.get("gasUsed")
        if "contractAddress" in receipt and receipt["contractAddress"]:
            summary["contractAddress"] = receipt["contractAddress"]
    return summary


# ------------------------------ CLI command ----------------------------------


@app.command("package")
def deploy_package(
    ctx: typer.Context,
    manifest: Path = typer.Option(..., "--manifest", "-m", exists=True, file_okay=True, dir_okay=False, readable=True,
                                  help="Path to manifest.json"),
    code: Path = typer.Option(..., "--code", "-c", exists=True, file_okay=True, dir_okay=False, readable=True,
                              help="Path to contract source (e.g., contract.py) or IR bytes"),
    seed_hex: Optional[str] = typer.Option(None, "--seed-hex", help="Signer seed as hex (dev/test only; can use OMNI_SDK_SEED_HEX)"),
    alg: str = typer.Option("dilithium3", "--alg", help="PQ signature algorithm: dilithium3 | sphincs_shake_128s"),
    gas_price: Optional[int] = typer.Option(None, "--gas-price", help="Optional gas price override"),
    gas_limit: Optional[int] = typer.Option(None, "--gas-limit", help="Optional gas limit override"),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Optional sender nonce override"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for transaction receipt."),
    out_dir: Optional[Path] = typer.Option(None, "--out-dir", help="Directory to store tx/receipt artifacts"),
) -> None:
    """
    Deploy a contract package (manifest + code). Prints a JSON summary with
    txHash and (when available) contractAddress.
    """
    # Resolve environment from root callback
    c: Ctx = ctx.obj  # type: ignore[assignment]
    client = RpcClient(c.rpc, timeout=c.timeout)

    # Load inputs
    manifest_obj = _load_manifest(manifest)
    code_bytes = _load_code(code)

    # Build signer
    signer = _make_signer(alg, seed_hex)
    sender_addr = Address.from_public_key(signer.public_key_bytes(), alg=signer.alg_id).bech32  # type: ignore

    # Construct deploy tx (estimate gas if not provided)
    if gas_limit is None:
        try:
            gas_est = estimate_deploy_gas(client, manifest_obj, code_bytes, sender=sender_addr)
            gas_limit = int(gas_est["gasLimit"]) if isinstance(gas_est, dict) and "gasLimit" in gas_est else int(gas_est)
        except Exception:
            # Fallback to a conservative default if estimate path is unavailable
            gas_limit = 1_000_000

    tx = build_deploy_tx(
        chain_id=c.chain_id,
        sender=sender_addr,
        manifest=manifest_obj,
        code=code_bytes,
        gas_price=gas_price,
        gas_limit=gas_limit,
        nonce=nonce,
    )

    # Sign & encode
    sign_bytes = tx.sign_bytes  # provided by build_deploy_tx dataclass
    sig = signer.sign(sign_bytes, domain="tx")  # domain-separated signing
    tx.attach_signature(alg_id=signer.alg_id, signature=sig)  # mutate/return self
    raw = encode_tx_cbor(tx)

    # Submit
    tx_hash = send_raw_transaction(client, raw)

    receipt: Optional[Dict[str, Any]] = None
    if wait:
        receipt = await_receipt(client, tx_hash, timeout_seconds=max(c.timeout, 60.0))

    # Persist artifacts if requested
    _store_artifacts(out_dir, tx_cbor=raw, receipt=receipt)

    # Print summary JSON
    typer.echo(json.dumps(_summarize(receipt, tx_hash), indent=2))


# ------------------------------ module end -----------------------------------

