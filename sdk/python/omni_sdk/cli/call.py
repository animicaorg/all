from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import click
import typer

from ..address import from_pubkey
from ..rpc.http import RpcClient
from ..tx import build as tx_build
from ..tx import send as tx_send
from ..tx import signing as tx_signing
from ..types.abi import decode_return, encode_call, normalize_abi
from ..wallet.signer import PQSigner

try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object

app = typer.Typer(help="Call contract functions (read/write)", no_args_is_help=True)

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


def _coerce_abi_for_calls(abi_value: Any) -> Any:
    if not isinstance(abi_value, dict):
        return abi_value
    if {"entries", "functions", "events"} <= set(abi_value.keys()):
        return abi_value
    funcs = abi_value.get("functions", [])
    events = abi_value.get("events", [])
    if not isinstance(funcs, list) or not isinstance(events, list):
        return abi_value
    entries = []
    for fn in funcs:
        if isinstance(fn, dict):
            item = dict(fn)
            item.setdefault("type", "function")
            entries.append(item)
    for ev in events:
        if isinstance(ev, dict):
            item = dict(ev)
            item.setdefault("type", "event")
            entries.append(item)
    return entries


def _load_abi(path: Path) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"ABI file not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid JSON in ABI file {path}: {exc}") from exc
    parsed = data.get("abi", data) if isinstance(data, dict) else data
    return _coerce_abi_for_calls(parsed)


def _parse_args_json(args_json: Optional[str]) -> Any:
    if args_json is None:
        return []
    try:
        return json.loads(args_json)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"--args-json must be valid JSON: {exc}") from exc


def _to_positional_args(abi_obj: Any, fn: str, args_obj: Any) -> List[Any]:
    if args_obj is None:
        return []
    if isinstance(args_obj, list):
        return args_obj
    if not isinstance(args_obj, dict):
        raise typer.BadParameter("--args-json must decode to a JSON array or object")

    normalized = normalize_abi(abi_obj)
    funcs = normalized.get("functions", [])
    match = next((item for item in funcs if item.get("name") == fn), None)
    if not isinstance(match, dict):
        raise typer.BadParameter(
            f"function {fn!r} not present in ABI; cannot map named args"
        )
    inputs = match.get("inputs", [])
    out: List[Any] = []
    for inp in inputs:
        name = inp.get("name")
        if not isinstance(name, str) or not name:
            raise typer.BadParameter(
                f"function {fn!r} has unnamed input; use positional array args"
            )
        if name not in args_obj:
            raise typer.BadParameter(f"missing named argument: {name}")
        out.append(args_obj[name])
    return out


def _rpc_endpoint(ctx_obj: Any, override: Optional[str]) -> str:
    if override and override.strip():
        return override.strip()
    if isinstance(ctx_obj, str) and ctx_obj.strip():
        return ctx_obj.strip()
    inherited = getattr(ctx_obj, "rpc", None)
    if isinstance(inherited, str) and inherited.strip():
        return inherited.strip()
    for key in ("OMNI_RPC_URL", "OMNI_SDK_RPC_URL", "ANIMICA_RPC_URL"):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return "http://127.0.0.1:8545/rpc"


def _ctx_value(ctx: typer.Context, name: str) -> Any:
    obj = getattr(ctx, "obj", None)
    if obj is None:
        return None
    return getattr(obj, name, None)


def _resolve_timeout(ctx: typer.Context, timeout: Optional[float]) -> float:
    if timeout is not None:
        return float(timeout)
    inherited = _ctx_value(ctx, "timeout")
    if inherited is not None:
        return float(inherited)
    raw = os.environ.get("OMNI_SDK_HTTP_TIMEOUT")
    if raw and raw.strip():
        try:
            return float(raw)
        except Exception as exc:  # noqa: BLE001
            raise typer.BadParameter(f"invalid OMNI_SDK_HTTP_TIMEOUT value {raw!r}") from exc
    return 3600.0


def _auto_detect_chain_id(rpc_url: str, timeout: float) -> Optional[int]:
    try:
        client = RpcClient(rpc_url, timeout=timeout)
        call_fn = getattr(client, "request", None) or getattr(client, "call", None)
        if not callable(call_fn):
            return None
        value = call_fn("chain.getChainId", [])
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _resolve_chain_id(
    ctx: typer.Context,
    chain_id: Optional[int],
    *,
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
        try:
            return int(env_chain)
        except Exception as exc:  # noqa: BLE001
            raise typer.BadParameter(f"invalid OMNI_CHAIN_ID value {env_chain!r}") from exc
    detected = _auto_detect_chain_id(rpc_url, timeout)
    if detected is not None:
        return int(detected)
    return 2


def _json_safe(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _emit_json(payload: Dict[str, Any]) -> None:
    typer.echo(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False))


def _simulate_call(
    rpc: RpcClient,
    *,
    address: str,
    calldata: bytes,
    sender: Optional[str],
) -> bytes:
    payload = {"to": address, "data": "0x" + calldata.hex()}
    if sender:
        payload["from"] = sender
    candidates: Sequence[tuple[str, list[Any]]] = (
        ("execution.simulateCall", [payload]),
        ("state.call", [payload]),
        ("vm.simulateCall", [address, "0x" + calldata.hex(), sender, None]),
        ("state.simulateCall", [payload]),
        ("call.simulate", [payload]),
        ("contracts.simulate", [payload]),
    )

    errors: List[str] = []
    for method, params in candidates:
        try:
            out = rpc.request(method, params)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{method}: {exc}")
            continue
        if isinstance(out, (bytes, bytearray)):
            return bytes(out)
        if isinstance(out, str) and out.startswith("0x"):
            try:
                return bytes.fromhex(out[2:])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{method}: invalid hex return ({exc})")
                continue
        if isinstance(out, dict):
            candidates_values = [
                out.get("returnData"),
                out.get("return_data"),
                out.get("result"),
                out.get("data"),
                out.get("output"),
                out.get("raw"),
                out.get("bytes"),
            ]
            nested = out.get("result")
            if isinstance(nested, dict):
                candidates_values.extend(
                    [
                        nested.get("returnData"),
                        nested.get("return_data"),
                        nested.get("data"),
                        nested.get("output"),
                        nested.get("raw"),
                    ]
                )
            for value in candidates_values:
                if isinstance(value, (bytes, bytearray)):
                    return bytes(value)
                if isinstance(value, str) and value.startswith("0x"):
                    try:
                        return bytes.fromhex(value[2:])
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{method}: invalid hex return ({exc})")
                        continue
        errors.append(f"{method}: unsupported response type {type(out).__name__}")

    method_list = ", ".join(method for method, _ in candidates)
    detail = "; ".join(errors[:3]) if errors else "no methods responded"
    raise typer.BadParameter(
        "node did not expose a recognized call simulation RPC method; "
        f"probed methods: {method_list}; details: {detail}"
    )


def _normalize_hex(value: str, *, field: str) -> str:
    raw = value.strip()
    if raw.startswith(("0x", "0X")):
        raw = raw[2:]
    if not raw:
        raise typer.BadParameter(f"{field} cannot be empty")
    if len(raw) % 2 != 0:
        raise typer.BadParameter(
            f"{field} must contain an even number of hex characters"
        )
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


def _wallet_file_path(path_arg: Optional[Path]) -> Path:
    if path_arg is not None:
        return path_arg.expanduser()
    env_path = os.getenv("ANIMICA_WALLETS_FILE")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def _wallet_entries(path: Path) -> List[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"wallet file not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid JSON in wallet file {path}: {exc}") from exc

    if isinstance(raw, dict) and isinstance(raw.get("wallets"), list):
        entries = raw["wallets"]
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
            f"unsupported wallet file format in {path}; expected a wallet object/list"
        )
    return [item for item in entries if isinstance(item, dict)]


def _find_wallet_by_label(path: Path, label: str) -> Dict[str, Any]:
    needle = label.strip().lower()
    if not needle:
        raise typer.BadParameter("--wallet-label cannot be empty")
    for item in _wallet_entries(path):
        item_label = str(item.get("label") or "").strip().lower()
        if item_label == needle:
            return item
    raise typer.BadParameter(f"wallet label '{label}' not found in {path}")


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
    entry: Dict[str, Any], *, field_name: str, aliases: Sequence[str]
) -> bytes:
    for key in aliases:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return bytes.fromhex(_normalize_hex(value, field=f"wallet.{field_name}"))
    raise typer.BadParameter(f"wallet entry missing {field_name}")


def _make_signer_from_seed(alg: str, seed_hex: str) -> PQSigner:
    seed = _parse_seed_hex(seed_hex)
    return PQSigner.from_seed(alg, seed=seed)


def _make_signer_from_wallet(
    wallet_file: Path,
    wallet_label: str,
    alg_override: Optional[str],
) -> PQSigner:
    entry = _find_wallet_by_label(wallet_file, wallet_label)
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
    return PQSigner.from_keypair(
        alg_name=wallet_alg,
        secret_key=secret_key,
        public_key=public_key,
    )


def _resolve_signer(
    *,
    alg: Optional[str],
    seed_hex: Optional[str],
    wallet_file: Optional[Path],
    wallet_label: Optional[str],
) -> PQSigner:
    if wallet_label:
        if seed_hex:
            raise typer.BadParameter(
                "choose exactly one signer source: --seed-hex or --wallet-label"
            )
        resolved_wallet_file = _wallet_file_path(wallet_file)
        return _make_signer_from_wallet(resolved_wallet_file, wallet_label, alg)

    resolved_seed = seed_hex or os.environ.get("OMNI_SDK_SEED_HEX")
    if not resolved_seed:
        raise typer.BadParameter(
            "missing signer material: pass --seed-hex, or --wallet-file/--wallet-label"
        )
    alg_name = _normalize_alg_name(alg or "dilithium3")
    return _make_signer_from_seed(alg_name, resolved_seed)


def _resolve_nonce(rpc: RpcClient, sender: str, override: Optional[int]) -> int:
    if override is not None:
        return int(override)

    errors: List[str] = []
    for params in ([sender, "pending"], [sender]):
        try:
            return int(rpc.request("state.getNonce", params))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"state.getNonce({params!r}) failed: {exc}")
    raise typer.BadParameter(f"failed to fetch sender nonce: {'; '.join(errors)}")


@app.command("read")
def call_read(
    ctx: typer.Context,
    address: str = typer.Option(..., "--address", "-a"),
    abi: Path = typer.Option(..., "--abi"),
    func: str = typer.Option(..., "--func", "-f"),
    args_json: Optional[str] = typer.Option(None, "--args-json"),
    sender: Optional[str] = typer.Option(None, "--from"),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        envvar="OMNI_CHAIN_ID",
        help="Chain ID for output context (auto-detected when omitted).",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        envvar="OMNI_SDK_HTTP_TIMEOUT",
        help="HTTP timeout in seconds.",
    ),
    rpc: Optional[str] = typer.Option(
        None,
        "--rpc",
        envvar=["OMNI_RPC_URL", "OMNI_SDK_RPC_URL", "ANIMICA_RPC_URL"],
        help="Node HTTP JSON-RPC URL override.",
    ),
) -> None:
    """
    Simulate a read-only contract call and decode return values via ABI.
    """
    rpc_endpoint = _rpc_endpoint(_ctx_value(ctx, "rpc"), rpc)
    timeout_value = _resolve_timeout(ctx, timeout)
    chain_id_value = _resolve_chain_id(
        ctx, chain_id, rpc_url=rpc_endpoint, timeout=timeout_value
    )
    rpc_client = RpcClient(rpc_endpoint, timeout=timeout_value)
    abi_obj = _load_abi(abi)
    args_obj = _parse_args_json(args_json)
    args_list = _to_positional_args(abi_obj, func, args_obj)
    calldata = encode_call(abi_obj, func, args_list)
    raw = _simulate_call(rpc_client, address=address, calldata=calldata, sender=sender)
    decoded = decode_return(abi_obj, func, raw)
    summary: Dict[str, Any] = {
        "status": "ok",
        "rpc_url": rpc_endpoint,
        "rpcUrl": rpc_endpoint,
        "chain_id": int(chain_id_value),
        "chainId": int(chain_id_value),
        "address": address,
        "func": func,
        "args": args_list,
        "result": "0x" + raw.hex(),
        "decoded_result": decoded,
        "decodedResult": decoded,
    }
    if sender:
        summary["sender"] = sender
    _emit_json(summary)


@app.command("write")
def call_write(
    ctx: typer.Context,
    address: str = typer.Option(..., "--address", "-a"),
    abi: Path = typer.Option(..., "--abi"),
    func: str = typer.Option(..., "--func", "-f"),
    args_json: Optional[str] = typer.Option(None, "--args-json"),
    seed_hex: Optional[str] = typer.Option(None, "--seed-hex"),
    wallet_file: Optional[Path] = typer.Option(
        None,
        "--wallet-file",
        envvar="ANIMICA_WALLETS_FILE",
        help="Path to wallets.json (used with --wallet-label).",
    ),
    wallet_label: Optional[str] = typer.Option(
        None,
        "--wallet-label",
        help="Wallet label from wallets.json for signing.",
    ),
    alg: Optional[str] = typer.Option(
        None,
        "--alg",
        help="PQ signature algorithm (optional with --wallet-label; inferred from wallet when omitted).",
    ),
    max_fee: int = typer.Option(1, "--max-fee"),
    gas_limit: Optional[int] = typer.Option(None, "--gas-limit"),
    nonce: Optional[int] = typer.Option(None, "--nonce"),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        envvar="OMNI_CHAIN_ID",
        help="Chain ID for transaction signing (auto-detected when omitted).",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        envvar="OMNI_SDK_HTTP_TIMEOUT",
        help="HTTP timeout in seconds.",
    ),
    rpc: Optional[str] = typer.Option(
        None,
        "--rpc",
        envvar=["OMNI_RPC_URL", "OMNI_SDK_RPC_URL", "ANIMICA_RPC_URL"],
        help="Node HTTP JSON-RPC URL override.",
    ),
) -> None:
    """
    Build, sign, and send a state-changing contract call.
    """
    rpc_endpoint = _rpc_endpoint(_ctx_value(ctx, "rpc"), rpc)
    timeout_value = _resolve_timeout(ctx, timeout)
    chain_id_value = _resolve_chain_id(
        ctx, chain_id, rpc_url=rpc_endpoint, timeout=timeout_value
    )
    rpc_client = RpcClient(rpc_endpoint, timeout=timeout_value)
    abi_obj = _load_abi(abi)
    args_obj = _parse_args_json(args_json)
    args_list = _to_positional_args(abi_obj, func, args_obj)

    signer = _resolve_signer(
        alg=alg,
        seed_hex=seed_hex,
        wallet_file=wallet_file,
        wallet_label=wallet_label,
    )
    sender = signer.address or from_pubkey(
        signer.public_key, alg_id=signer.alg_id, hrp="anim"
    )
    nonce_value = _resolve_nonce(rpc_client, sender, nonce)
    calldata = encode_call(abi_obj, func, args_list)

    tx = tx_build.call(
        from_addr=sender,
        to_addr=address,
        data=calldata,
        nonce=nonce_value,
        gas_limit=gas_limit,
        max_fee=int(max_fee),
        chain_id=int(chain_id_value),
        value=0,
    )
    signed = tx_signing.sign_transaction_with_rpc_context(
        tx,
        signer,
        chain_id=int(chain_id_value),
        rpc=rpc_client,
    )
    tx_hash = tx_send.submit_raw(rpc_client, signed.raw_tx)

    receipt: Optional[Dict[str, Any]] = None
    wait_error: Optional[str] = None
    if wait:
        try:
            maybe_receipt = tx_send.wait_for_receipt(
                rpc_client, tx_hash, timeout_s=max(timeout_value, 120.0)
            )
        except Exception as exc:  # noqa: BLE001
            maybe_receipt = None
            wait_error = str(exc)
        if isinstance(maybe_receipt, dict):
            receipt = maybe_receipt
        elif maybe_receipt is not None and wait_error is None:
            wait_error = (
                "unexpected receipt payload type "
                f"{type(maybe_receipt).__name__}; expected object"
            )

    block_number = None
    tx_status = None
    gas_used = None
    if isinstance(receipt, dict):
        block_number = receipt.get("blockNumber", receipt.get("block_number"))
        tx_status = receipt.get("status", receipt.get("tx_status"))
        gas_used = receipt.get("gasUsed", receipt.get("gas_used"))

    summary = {
        "status": "ok",
        "rpc_url": rpc_endpoint,
        "rpcUrl": rpc_endpoint,
        "chain_id": int(chain_id_value),
        "chainId": int(chain_id_value),
        "address": address,
        "func": func,
        "args": args_list,
        "sender": sender,
        "from": sender,
        "to": address,
        "tx_hash": tx_hash,
        "txHash": tx_hash,
        "tx_status": tx_status,
        "txStatus": tx_status,
        "block_number": block_number,
        "blockNumber": block_number,
        "gas_used": gas_used,
        "gasUsed": gas_used,
        "receipt": receipt,
        "wait": bool(wait),
        "waited": bool(wait),
    }
    if wait_error:
        summary["wait_error"] = wait_error
    _emit_json(summary)


def _format_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _rewrite_direct_args(argv: List[str]) -> List[str]:
    if not argv:
        return argv
    first = argv[0]
    if first == "call":
        return argv[1:]
    if first == "help":
        return ["--help", *argv[1:]]
    return argv


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    args = _rewrite_direct_args(args)
    try:
        app(prog_name="omni-sdk-call", standalone_mode=False, args=args)
        return 0
    except typer.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return int(getattr(exc, "exit_code", 1))
    except Exception as exc:  # pragma: no cover
        typer.echo(f"error: {_format_exception(exc)}", err=True)
        return 1


def run(argv: Optional[List[str]] = None) -> int:
    return main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
