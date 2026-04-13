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

try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object

app = typer.Typer(help="Deploy contracts and packages", no_args_is_help=True)

__all__ = ["app", "main", "run"]

ALG_BY_ID = {
    0x1001: "dilithium3",
    0x1002: "sphincs_shake_128s",
}

ALG_ALIASES = {
    "dilithium": "dilithium3",
    "dilithium-3": "dilithium3",
    "dilithium3": "dilithium3",
    "ml-dsa-65": "dilithium3",
    "mldsa65": "dilithium3",
    "sphincs": "sphincs_shake_128s",
    "sphincs+": "sphincs_shake_128s",
    "sphincs+-shake-128s": "sphincs_shake_128s",
    "sphincs_shake_128s": "sphincs_shake_128s",
}


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


def _normalize_alg_name(raw_name: str) -> str:
    normalized = ALG_ALIASES.get(raw_name.strip().lower(), raw_name.strip().lower())
    if normalized not in ("dilithium3", "sphincs_shake_128s"):
        raise typer.BadParameter(
            f"unsupported algorithm '{raw_name}' "
            "(supported: dilithium3, sphincs_shake_128s)"
        )
    return normalized


def _parse_alg_id(raw_value: Any) -> Optional[int]:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if not value:
            return None
        try:
            return int(value, 16) if value.startswith("0x") else int(value)
        except Exception as exc:  # noqa: BLE001
            raise typer.BadParameter(f"invalid wallet alg_id '{raw_value}'") from exc
    try:
        return int(raw_value)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid wallet alg_id '{raw_value}'") from exc


def _wallet_default_path() -> Path:
    env_path = os.getenv("ANIMICA_WALLETS_FILE")
    if env_path and env_path.strip():
        return Path(env_path).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def _wallet_entries(path: Path) -> tuple[list[Dict[str, Any]], Optional[str]]:
    expanded = path.expanduser()
    try:
        raw = json.loads(expanded.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"wallet/keystore file not found: {expanded}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(
            f"invalid JSON in wallet/keystore file {expanded}: {exc}"
        ) from exc

    default_label: Optional[str] = None
    if isinstance(raw, dict) and isinstance(raw.get("wallets"), list):
        entries = raw["wallets"]
        if isinstance(raw.get("default"), str):
            default_label = str(raw["default"])
    elif isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = []
        for label, value in raw.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item.setdefault("label", str(label))
            entries.append(item)
    else:
        raise typer.BadParameter(
            "unsupported wallet/keystore format; expected wallets.json-like object or list"
        )

    normalized = [item for item in entries if isinstance(item, dict)]
    if not normalized:
        raise typer.BadParameter(f"no wallet entries found in {expanded}")
    return normalized, default_label


def _find_wallet_by_label(entries: Sequence[Dict[str, Any]], label: str) -> Dict[str, Any]:
    needle = label.strip().lower()
    if not needle:
        raise typer.BadParameter("--wallet-label cannot be empty")
    for item in entries:
        item_label = str(item.get("label") or "").strip().lower()
        if item_label == needle:
            return item
    raise typer.BadParameter(f"wallet label '{label}' not found")


def _resolve_wallet_entry(path: Path, wallet_label: Optional[str]) -> tuple[Dict[str, Any], str]:
    entries, default_label = _wallet_entries(path)
    if wallet_label is not None:
        entry = _find_wallet_by_label(entries, wallet_label)
        return entry, str(entry.get("label") or wallet_label)

    if default_label:
        try:
            entry = _find_wallet_by_label(entries, default_label)
            return entry, str(entry.get("label") or default_label)
        except typer.BadParameter:
            pass

    if len(entries) == 1:
        only = entries[0]
        label = str(only.get("label") or "default")
        return only, label

    raise typer.BadParameter(
        "wallet/keystore has multiple entries; pass --wallet-label to select one"
    )


def _wallet_alg_name(entry: Dict[str, Any]) -> str:
    name_raw = entry.get("alg_name") or entry.get("algName")
    alg_id = _parse_alg_id(entry.get("alg_id", entry.get("algId", entry.get("alg"))))
    name_from_id = ALG_BY_ID.get(int(alg_id)) if alg_id is not None else None
    if isinstance(name_raw, str) and name_raw.strip():
        normalized_name = _normalize_alg_name(name_raw)
        if name_from_id and name_from_id != normalized_name:
            raise typer.BadParameter(
                "wallet algorithm mismatch: "
                f"alg_id {alg_id} maps to {name_from_id}, but alg_name is {name_raw!r}"
            )
        return normalized_name
    if name_from_id:
        return name_from_id
    raise typer.BadParameter(
        "wallet entry missing supported alg_id/alg_name "
        "(expected dilithium3 or sphincs_shake_128s)"
    )


def _wallet_key_bytes(
    entry: Dict[str, Any],
    *,
    field_name: str,
    aliases: Sequence[str],
) -> bytes:
    for key in aliases:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return bytes.fromhex(_normalize_hex(value, field=f"wallet.{field_name}"))
    raise typer.BadParameter(f"wallet entry missing {field_name}")


def _sender_from_signer(signer: PQSigner) -> str:
    return signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")


def _make_signer_from_seed(alg_name: str, seed_hex: str) -> PQSigner:
    seed = _parse_seed_hex(seed_hex)
    try:
        return PQSigner.from_seed(alg_name, seed=seed)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"failed to create signer from seed: {exc}") from exc


def _make_signer_from_wallet(
    path: Path,
    wallet_label: Optional[str],
    alg_override: Optional[str],
) -> tuple[PQSigner, str]:
    entry, resolved_label = _resolve_wallet_entry(path, wallet_label)
    wallet_alg = _wallet_alg_name(entry)
    if alg_override:
        normalized_override = _normalize_alg_name(alg_override)
        if normalized_override != wallet_alg:
            raise typer.BadParameter(
                f"--alg={normalized_override} does not match wallet algorithm {wallet_alg}"
            )

    public_key = _wallet_key_bytes(
        entry,
        field_name="public_key_hex",
        aliases=("public_key_hex", "publicKeyHex", "pubkey", "pk"),
    )
    secret_key = _wallet_key_bytes(
        entry,
        field_name="secret_key_hex",
        aliases=("secret_key_hex", "secretKeyHex"),
    )

    try:
        signer = PQSigner.from_keypair(
            alg_name=wallet_alg,
            secret_key=secret_key,
            public_key=public_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(
            f"failed to create signer from wallet entry '{resolved_label}': {exc}"
        ) from exc

    wallet_address = str(entry.get("address") or "").strip()
    signer_address = _sender_from_signer(signer)
    if wallet_address and wallet_address.lower() != signer_address.lower():
        raise typer.BadParameter(
            "wallet address mismatch: "
            f"wallet entry '{resolved_label}' has {wallet_address}, "
            f"derived signer address is {signer_address}"
        )

    return signer, resolved_label


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
    wallet_file: Optional[Path],
    wallet_label: Optional[str],
    keystore: Optional[Path],
    sender: Optional[str],
    alg: Optional[str],
    dry_run: bool,
) -> tuple[Optional[PQSigner], str, str]:
    has_seed = bool(seed_hex and seed_hex.strip())
    has_wallet_source = bool(wallet_file is not None or wallet_label is not None or keystore is not None)

    resolved_wallet_file: Optional[Path] = None
    if wallet_file is not None:
        resolved_wallet_file = wallet_file.expanduser()
    if keystore is not None:
        keystore_path = keystore.expanduser()
        if resolved_wallet_file is not None and resolved_wallet_file != keystore_path:
            raise typer.BadParameter(
                "--wallet-file and --keystore point to different paths; provide only one"
            )
        resolved_wallet_file = keystore_path
    if wallet_label is not None and resolved_wallet_file is None:
        resolved_wallet_file = _wallet_default_path()

    if has_seed and has_wallet_source:
        raise typer.BadParameter(
            "choose exactly one signer source: --seed-hex or --keystore/--wallet-file"
        )

    if has_seed:
        resolved_alg = _normalize_alg_name(alg or "dilithium3")
        signer = _make_signer_from_seed(resolved_alg, seed_hex or "")
        sender_addr = _sender_from_signer(signer)
        return signer, sender_addr, f"seed:{resolved_alg}"

    if resolved_wallet_file is not None:
        signer, resolved_label = _make_signer_from_wallet(
            resolved_wallet_file,
            wallet_label=wallet_label,
            alg_override=alg,
        )
        sender_addr = _sender_from_signer(signer)
        return signer, sender_addr, f"wallet:{resolved_label}@{resolved_wallet_file}"

    if sender:
        if not dry_run:
            raise typer.BadParameter(
                "--sender without signer material is only supported in --dry-run mode"
            )
        return None, sender, "sender-override"

    if dry_run:
        raise typer.BadParameter(
            "missing sender material for --dry-run: pass --sender, --seed-hex, "
            "or --keystore/--wallet-file"
        )

    raise typer.BadParameter(
        "missing signer material: pass --seed-hex or --keystore/--wallet-file"
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
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Wallet file path (wallets.json-compatible) used for signing",
    ),
    wallet_file: Optional[Path] = typer.Option(
        None,
        "--wallet-file",
        help="Wallet file path (wallets.json-compatible) used for signing",
    ),
    wallet_label: Optional[str] = typer.Option(
        None,
        "--wallet-label",
        help="Wallet label from the wallet/keystore file",
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

    signer, sender_addr, signer_source = _resolve_signer_and_sender(
        seed_hex=seed_hex,
        wallet_file=wallet_file,
        wallet_label=wallet_label,
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
            "manifestPath": _as_output_path(manifest),
            "irPath": _as_output_path(ir),
            "nonce": nonce_value,
            "packageBytes": len(package),
            "signBytesLen": sign_bytes_len,
            "txBuildError": tx_build_error,
            "signerSource": signer_source,
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
        "txHash": tx_hash,
        "contractAddress": resolved_contract,
        "createdContractId": created_contract_id,
        "manifestPath": _as_output_path(manifest),
        "irPath": _as_output_path(ir),
        "status": receipt.get("status") if isinstance(receipt, dict) else None,
        "gasUsed": receipt.get("gasUsed") if isinstance(receipt, dict) else None,
        "blockNumber": receipt.get("blockNumber") if isinstance(receipt, dict) else None,
        "signerSource": signer_source,
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
