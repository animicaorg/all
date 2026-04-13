from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import click
import typer

from ..address import from_pubkey
from ..contracts.deployer import build_deploy_tx, deploy_package, make_package_bytes
from ..rpc.http import RpcClient
from ..tx import encode as tx_encode
from ..wallet.signer import PQSigner
from .wallet_loader import (
    load_signer_from_wallet_source,
    normalize_alg_name,
    wallet_store_default_path,
)

try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object

app = typer.Typer(help="Deploy contracts and packages", no_args_is_help=True)

__all__ = ["app", "main", "run"]


def _env_default(keys: Sequence[str], default: str) -> str:
    for key in keys:
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return default


def _ctx_value(ctx: typer.Context, name: str) -> Any:
    obj = getattr(ctx, "obj", None)
    if obj is None:
        return None
    return getattr(obj, name, None)


def _resolve_rpc_url(ctx: typer.Context, rpc: Optional[str]) -> str:
    if rpc and rpc.strip():
        return rpc.strip()
    inherited = _ctx_value(ctx, "rpc")
    if isinstance(inherited, str) and inherited.strip():
        return inherited.strip()
    return _env_default(
        ("OMNI_RPC_URL", "OMNI_SDK_RPC_URL", "ANIMICA_RPC_URL"),
        "http://127.0.0.1:8545/rpc",
    )


def _resolve_timeout(ctx: typer.Context, timeout: Optional[float]) -> float:
    if timeout is not None:
        return float(timeout)
    inherited = _ctx_value(ctx, "timeout")
    if inherited is not None:
        return float(inherited)
    return float(_env_default(("OMNI_SDK_HTTP_TIMEOUT",), "3600.0"))


def _auto_detect_chain_id(rpc_url: str, timeout: float) -> Optional[int]:
    try:
        client = RpcClient(rpc_url, timeout=timeout)
        call_fn = getattr(client, "request", None) or getattr(client, "call", None)
        if not callable(call_fn):
            return None
        result = call_fn("chain.getChainId", [])
        if result is not None:
            return int(result)
    except Exception:
        pass
    return None


def _resolve_chain_id(
    ctx: typer.Context,
    chain_id: Optional[int],
    rpc_url: str,
    timeout: float,
) -> int:
    if chain_id is not None:
        return int(chain_id)

    inherited = _ctx_value(ctx, "chain_id")
    if inherited is not None:
        return int(inherited)

    env_chain = os.environ.get("OMNI_CHAIN_ID")
    if env_chain and env_chain.strip():
        return int(env_chain)

    detected = _auto_detect_chain_id(rpc_url, timeout)
    if detected is not None:
        typer.echo(f"Auto-detected chain ID {detected} from node", err=True)
        return int(detected)

    fallback_chain = 2
    typer.echo(
        "WARNING: Could not auto-detect chain ID from node; "
        f"falling back to testnet (chain ID {fallback_chain})",
        err=True,
    )
    return fallback_chain


def _as_output_path(path: Path) -> str:
    return str(path.expanduser().resolve())


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"manifest not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid JSON in manifest: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise typer.BadParameter("manifest must be a JSON object")
    return data


def _load_ir(path: Path) -> bytes:
    try:
        return path.expanduser().read_bytes()
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"ir/code file not found: {path}") from exc


def _normalize_hex(value: str, *, field: str) -> str:
    raw = value.strip()
    if raw.startswith(("0x", "0X")):
        raw = raw[2:]
    if not raw:
        raise typer.BadParameter(f"{field} cannot be empty")
    if len(raw) % 2 != 0:
        raise typer.BadParameter(f"{field} must contain an even number of hex characters")
    try:
        bytes.fromhex(raw)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"{field} must be valid hex") from exc
    return raw


def _parse_seed_hex(seed_input: str) -> bytes:
    raw = _normalize_hex(seed_input, field="--seed-hex")
    seed = bytes.fromhex(raw)
    if len(seed) != 32:
        raise typer.BadParameter(
            f"--seed-hex expects a 32-byte seed (64 hex chars); got {len(seed)} bytes"
        )
    return seed


def _make_signer_from_seed(alg_name: str, seed_hex: str) -> PQSigner:
    seed = _parse_seed_hex(seed_hex)
    try:
        return PQSigner.from_seed(alg_name, seed=seed)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"failed to create signer from seed: {exc}") from exc


def _resolve_nonce(
    rpc: RpcClient,
    sender: str,
    override: Optional[int],
) -> int:
    if override is not None:
        return int(override)
    errors = []
    for params in ([sender, "pending"], [sender]):
        try:
            return int(rpc.request("state.getNonce", params))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"state.getNonce({params!r}) failed: {exc}")
    raise typer.BadParameter(f"failed to fetch sender nonce: {'; '.join(errors)}")


def _resolve_signer_and_sender(
    *,
    seed_hex: Optional[str],
    wallet_store: Optional[Path],
    wallet_file: Optional[Path],
    label: Optional[str],
    wallet_label: Optional[str],
    address: Optional[str],
    keystore: Optional[Path],
    sender: Optional[str],
    alg: Optional[str],
    dry_run: bool,
) -> tuple[Optional[PQSigner], str, str, Optional[str]]:
    has_seed = bool(seed_hex and seed_hex.strip())
    has_wallet_source = bool(
        wallet_store is not None
        or wallet_file is not None
        or label is not None
        or wallet_label is not None
        or address is not None
        or keystore is not None
    )

    if label and wallet_label and label.strip() != wallet_label.strip():
        raise typer.BadParameter("--label and --wallet-label must match when both are provided")
    resolved_label = label or wallet_label

    resolved_wallet_file: Optional[Path] = None
    strict_default_selection = False
    for source_name, source_path in (
        ("--wallet-store", wallet_store),
        ("--wallet-file", wallet_file),
        ("--keystore", keystore),
    ):
        if source_path is None:
            continue
        expanded = source_path.expanduser()
        if resolved_wallet_file is not None and resolved_wallet_file != expanded:
            raise typer.BadParameter(
                "wallet source flags point to different paths; provide one of "
                "--wallet-store, --wallet-file, or --keystore"
            )
        resolved_wallet_file = expanded
        if source_name == "--wallet-store":
            strict_default_selection = True
    if (
        (resolved_label is not None or address is not None)
        and resolved_wallet_file is None
    ):
        resolved_wallet_file = wallet_store_default_path()
        strict_default_selection = True

    if has_seed and has_wallet_source:
        raise typer.BadParameter(
            "choose exactly one signer source: --seed-hex or wallet source flags "
            "(--wallet-store/--wallet-file/--keystore)"
        )

    if has_seed:
        resolved_alg = normalize_alg_name(alg or "dilithium3")
        signer = _make_signer_from_seed(resolved_alg, seed_hex or "")
        sender_addr = signer.address or from_pubkey(
            signer.public_key, alg_id=signer.alg_id, hrp="anim"
        )
        return signer, sender_addr, f"seed:{resolved_alg}", None

    if resolved_wallet_file is not None:
        loaded = load_signer_from_wallet_source(
            path=resolved_wallet_file,
            label=resolved_label,
            address=address,
            alg_override=alg,
            strict_default_selection=strict_default_selection,
        )
        return loaded.signer, loaded.sender, loaded.source, loaded.source_kind

    if sender:
        if not dry_run:
            raise typer.BadParameter(
                "--sender without signer material is only supported in --dry-run mode"
            )
        return None, sender, "sender-override", None

    if dry_run:
        raise typer.BadParameter(
            "missing sender material for --dry-run: pass --sender, --seed-hex, "
            "or --wallet-store/--wallet-file/--keystore"
        )

    raise typer.BadParameter(
        "missing signer material: pass --seed-hex or --wallet-store/--wallet-file/--keystore"
    )


def _extract_contract_address(receipt: Any) -> Optional[str]:
    if not isinstance(receipt, dict):
        return None
    for key in ("contractAddress", "contract_address", "address"):
        value = receipt.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_created_contract_id(receipt: Any) -> Optional[str]:
    if not isinstance(receipt, dict):
        return None
    for key in ("createdContractId", "created_contract_id", "contractId", "contract_id"):
        value = receipt.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_tx_hash(receipt: Any) -> Optional[str]:
    if not isinstance(receipt, dict):
        return None
    for key in ("txHash", "tx_hash", "hash"):
        value = receipt.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _format_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


@app.command("package")
def deploy_package_cmd(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        ...,
        "--manifest",
        "-m",
        help="Path to contract manifest JSON",
    ),
    ir: Path = typer.Option(
        ...,
        "--ir",
        "--code",
        "-i",
        "-c",
        help="Path to compiled contract IR (or source/code blob)",
    ),
    rpc: Optional[str] = typer.Option(
        None,
        "--rpc",
        help="Node HTTP JSON-RPC URL",
        envvar=["OMNI_RPC_URL", "OMNI_SDK_RPC_URL", "ANIMICA_RPC_URL"],
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Expected chain ID",
        envvar="OMNI_CHAIN_ID",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help="HTTP timeout in seconds",
        envvar="OMNI_SDK_HTTP_TIMEOUT",
    ),
    seed_hex: Optional[str] = typer.Option(
        None,
        "--seed-hex",
        help="Signer seed as hex (dev/test only)",
    ),
    wallet_store: Optional[Path] = typer.Option(
        None,
        "--wallet-store",
        help="Path to wallet store (Animica wallets.json or SDK keystore entries)",
    ),
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Backward-compatible wallet/keystore path for signing",
    ),
    wallet_file: Optional[Path] = typer.Option(
        None,
        "--wallet-file",
        help="Deprecated alias for --wallet-store",
    ),
    label: Optional[str] = typer.Option(
        None,
        "--label",
        help="Wallet label within the wallet source",
    ),
    wallet_label: Optional[str] = typer.Option(
        None,
        "--wallet-label",
        help="Deprecated alias for --label",
    ),
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Wallet address within the wallet source",
    ),
    sender: Optional[str] = typer.Option(
        None,
        "--sender",
        help="Sender address override (dry-run only unless signer is also provided)",
    ),
    alg: Optional[str] = typer.Option(
        None,
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
    effective_rpc = _resolve_rpc_url(ctx, rpc)
    effective_timeout = _resolve_timeout(ctx, timeout)
    effective_chain_id = _resolve_chain_id(
        ctx,
        chain_id,
        rpc_url=effective_rpc,
        timeout=effective_timeout,
    )

    manifest_obj = _load_manifest(manifest)
    ir_bytes = _load_ir(ir)

    signer, sender_addr, signer_source, signer_source_kind = _resolve_signer_and_sender(
        seed_hex=seed_hex,
        wallet_store=wallet_store,
        wallet_file=wallet_file,
        label=label,
        wallet_label=wallet_label,
        address=address,
        keystore=keystore,
        sender=sender,
        alg=alg,
        dry_run=dry_run,
    )

    if dry_run:
        nonce_value = int(nonce if nonce is not None else 0)
        package = make_package_bytes(manifest=manifest_obj, code=ir_bytes)
        sign_bytes_len: Optional[int] = None
        tx_build_error: Optional[str] = None
        try:
            tx = build_deploy_tx(
                from_addr=sender_addr,
                chain_id=int(effective_chain_id),
                nonce=nonce_value,
                max_fee=int(max_fee),
                package_bytes=package,
                gas_limit=gas_limit,
            )
            sign_bytes_len = len(tx_encode.sign_bytes(tx))
        except Exception as exc:  # noqa: BLE001
            # Keep dry-run useful even with placeholder sender addresses.
            tx_build_error = str(exc)
        summary = {
            "dryRun": True,
            "sender": sender_addr,
            "rpcUrl": effective_rpc,
            "chainId": int(effective_chain_id),
            "rpc_url": effective_rpc,
            "chain_id": int(effective_chain_id),
            "manifestPath": _as_output_path(manifest),
            "irPath": _as_output_path(ir),
            "manifest_path": _as_output_path(manifest),
            "ir_path": _as_output_path(ir),
            "nonce": nonce_value,
            "packageBytes": len(package),
            "signBytesLen": sign_bytes_len,
            "txBuildError": tx_build_error,
            "signerSource": signer_source,
            "signer_source": signer_source,
            "signer_source_kind": signer_source_kind,
        }
        typer.echo(json.dumps(summary, indent=2))
        return

    if signer is None:
        raise typer.BadParameter("signer is required unless --dry-run is used")

    rpc_client = RpcClient(effective_rpc, timeout=effective_timeout)
    nonce_value = _resolve_nonce(rpc_client, sender_addr, nonce)

    try:
        contract_address, receipt = deploy_package(
            rpc=rpc_client,
            signer=signer,
            manifest=manifest_obj,
            code=ir_bytes,
            chain_id=int(effective_chain_id),
            nonce=nonce_value,
            max_fee=int(max_fee),
            gas_limit=gas_limit,
            await_receipt=wait,
            timeout_s=float(timeout_s),
        )
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(_format_exception(exc)) from exc

    tx_hash = _extract_tx_hash(receipt)
    resolved_contract = contract_address or _extract_contract_address(receipt)
    created_contract_id = _extract_created_contract_id(receipt)

    summary: Dict[str, Any] = {
        "sender": sender_addr,
        "rpcUrl": effective_rpc,
        "chainId": int(effective_chain_id),
        "rpc_url": effective_rpc,
        "chain_id": int(effective_chain_id),
        "txHash": tx_hash,
        "tx_hash": tx_hash,
        "contractAddress": resolved_contract,
        "contract_address": resolved_contract,
        "createdContractId": created_contract_id,
        "manifestPath": _as_output_path(manifest),
        "irPath": _as_output_path(ir),
        "manifest_path": _as_output_path(manifest),
        "ir_path": _as_output_path(ir),
        "status": receipt.get("status") if isinstance(receipt, dict) else None,
        "gasUsed": receipt.get("gasUsed") if isinstance(receipt, dict) else None,
        "blockNumber": receipt.get("blockNumber") if isinstance(receipt, dict) else None,
        "signerSource": signer_source,
        "signer_source": signer_source,
        "signer_source_kind": signer_source_kind,
    }

    if out_dir is not None:
        out_dir = out_dir.expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "deploy_result.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )

    typer.echo(json.dumps(summary, indent=2))


def _rewrite_direct_args(argv: list[str]) -> list[str]:
    if not argv:
        return argv

    first = argv[0]
    if first in {"--help", "-h"}:
        return argv

    # Backward compatibility for callers that still include the historical
    # "package" subcommand in direct module execution.
    if first == "package":
        return argv[1:]

    if first == "help":
        return ["--help", *argv[1:]]

    return argv


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    args = _rewrite_direct_args(args)
    try:
        app(prog_name="omni-sdk-deploy", standalone_mode=False, args=args)
        return 0
    except typer.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return int(getattr(exc, "exit_code", 1))
    except Exception as exc:  # pragma: no cover
        typer.echo(f"error: {_format_exception(exc)}", err=True)
        return 1


def run(argv: Optional[list[str]] = None) -> int:
    return main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
