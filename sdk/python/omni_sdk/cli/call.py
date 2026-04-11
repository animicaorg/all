from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

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

app = typer.Typer(help="Call contract functions (read/write)")

__all__ = ["app"]

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


def _rpc_endpoint(ctx_obj: Ctx, override: Optional[str]) -> str:
    if override and override.strip():
        return override.strip()
    for key in ("OMNI_RPC_URL", "OMNI_SDK_RPC_URL", "ANIMICA_RPC_URL"):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return str(getattr(ctx_obj, "rpc", "http://127.0.0.1:8545/rpc"))


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
    c: Ctx = ctx.obj  # type: ignore[assignment]
    rpc_endpoint = _rpc_endpoint(c, rpc)
    rpc_client = RpcClient(rpc_endpoint, timeout=c.timeout)
    abi_obj = _load_abi(abi)
    args_obj = _parse_args_json(args_json)
    args_list = _to_positional_args(abi_obj, func, args_obj)
    calldata = encode_call(abi_obj, func, args_list)
    raw = _simulate_call(rpc_client, address=address, calldata=calldata, sender=sender)
    decoded = decode_return(abi_obj, func, raw)
    typer.echo(json.dumps(decoded, indent=2))


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
    c: Ctx = ctx.obj  # type: ignore[assignment]
    rpc_endpoint = _rpc_endpoint(c, rpc)
    rpc_client = RpcClient(rpc_endpoint, timeout=c.timeout)
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
        chain_id=int(c.chain_id),
        value=0,
    )
    signed = tx_signing.sign_transaction_with_rpc_context(
        tx,
        signer,
        chain_id=int(c.chain_id),
        rpc=rpc_client,
    )
    tx_hash = tx_send.submit_raw(rpc_client, signed.raw_tx)

    summary: Dict[str, Any] = {
        "txHash": tx_hash,
        "from": sender,
        "to": address,
        "func": func,
    }
    if wait:
        receipt = tx_send.wait_for_receipt(
            rpc_client, tx_hash, timeout_s=max(c.timeout, 120.0)
        )
        summary.update(
            {
                "status": receipt.get("status"),
                "gasUsed": receipt.get("gasUsed"),
                "blockNumber": receipt.get("blockNumber"),
            }
        )
    typer.echo(json.dumps(summary, indent=2))
