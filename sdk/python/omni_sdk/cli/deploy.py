from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import typer

from ..address import from_pubkey
from ..contracts.deployer import build_deploy_tx, deploy_package, make_package_bytes
from ..rpc.http import RpcClient
from ..tx import encode as tx_encode
from ..wallet.signer import PQSigner

try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object

app = typer.Typer(help="Deploy contracts and packages")

__all__ = ["app"]


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"manifest not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid JSON in manifest: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise typer.BadParameter("manifest must be a JSON object")
    return data


def _load_code(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"code file not found: {path}") from exc


def _parse_seed_hex(seed_hex: Optional[str]) -> bytes:
    resolved = seed_hex or os.environ.get("OMNI_SDK_SEED_HEX")
    if not resolved:
        raise typer.BadParameter(
            "missing seed: pass --seed-hex or set OMNI_SDK_SEED_HEX (dev/test only)"
        )
    try:
        return bytes.fromhex(resolved.strip().removeprefix("0x"))
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter("--seed-hex must be hex (with or without 0x)") from exc


def _resolve_nonce(
    rpc: RpcClient,
    sender: str,
    override: Optional[int],
) -> int:
    if override is not None:
        return int(override)
    try:
        return int(rpc.request("state.getNonce", [sender]))
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"failed to fetch sender nonce: {exc}") from exc


@app.command("package")
def deploy_package_cmd(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        ...,
        "--manifest",
        "-m",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to manifest.json",
    ),
    code: Path = typer.Option(
        ...,
        "--code",
        "-c",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to contract source or IR blob",
    ),
    seed_hex: Optional[str] = typer.Option(
        None,
        "--seed-hex",
        help="Signer seed as hex (dev/test only; fallback OMNI_SDK_SEED_HEX)",
    ),
    sender: Optional[str] = typer.Option(
        None,
        "--sender",
        help="Optional sender address override (dry-run mode only)",
    ),
    alg: str = typer.Option(
        "dilithium3",
        "--alg",
        help="PQ signature algorithm: dilithium3 | sphincs_shake_128s",
    ),
    max_fee: int = typer.Option(1, "--max-fee", help="Transaction max_fee"),
    gas_limit: Optional[int] = typer.Option(None, "--gas-limit"),
    nonce: Optional[int] = typer.Option(None, "--nonce"),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Wait for receipt before returning",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Build deploy transaction locally without submitting to RPC",
    ),
    timeout_s: float = typer.Option(
        120.0,
        "--wait-seconds",
        help="Receipt wait timeout (seconds)",
    ),
    out_dir: Optional[Path] = typer.Option(
        None,
        "--out-dir",
        help="Optional output dir for deploy_result.json",
    ),
) -> None:
    """
    Deploy a manifest+code contract package and print a JSON summary.
    """
    c: Ctx = ctx.obj  # type: ignore[assignment]
    manifest_obj = _load_manifest(manifest)
    code_bytes = _load_code(code)

    signer: Optional[PQSigner]
    if dry_run and sender:
        signer = None
        sender_addr = sender
    else:
        seed = _parse_seed_hex(seed_hex)
        signer = PQSigner.from_seed(alg, seed=seed)
        sender_addr = signer.address or from_pubkey(
            signer.public_key, alg_id=signer.alg_id, hrp="anim"
        )

    if dry_run:
        nonce_value = int(nonce if nonce is not None else 0)
        package = make_package_bytes(manifest=manifest_obj, code=code_bytes)
        tx = build_deploy_tx(
            from_addr=sender_addr,
            chain_id=int(c.chain_id),
            nonce=nonce_value,
            max_fee=int(max_fee),
            package_bytes=package,
            gas_limit=gas_limit,
        )
        sign_bytes = tx_encode.sign_bytes(tx)
        summary = {
            "dryRun": True,
            "sender": sender_addr,
            "nonce": nonce_value,
            "chainId": int(c.chain_id),
            "packageBytes": len(package),
            "signBytesLen": len(sign_bytes),
        }
        typer.echo(json.dumps(summary, indent=2))
        return

    rpc = RpcClient(c.rpc, timeout=c.timeout)
    nonce_value = _resolve_nonce(rpc, sender_addr, nonce)

    if signer is None:
        raise typer.BadParameter("signer is required unless --dry-run is used")

    contract_address, receipt = deploy_package(
        rpc=rpc,
        signer=signer,
        manifest=manifest_obj,
        code=code_bytes,
        chain_id=int(c.chain_id),
        nonce=nonce_value,
        max_fee=int(max_fee),
        gas_limit=gas_limit,
        await_receipt=wait,
        timeout_s=float(timeout_s),
    )

    summary: Dict[str, Any] = {
        "sender": sender_addr,
        "txHash": receipt.get("txHash"),
        "status": receipt.get("status"),
        "gasUsed": receipt.get("gasUsed"),
        "blockNumber": receipt.get("blockNumber"),
        "contractAddress": contract_address or receipt.get("contractAddress"),
    }

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "deploy_result.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )

    typer.echo(json.dumps(summary, indent=2))
