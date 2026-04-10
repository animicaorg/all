from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from ..address import from_pubkey
from ..rpc.http import RpcClient
from ..tx import build as tx_build
from ..tx import encode as tx_encode
from ..tx import send as tx_send
from ..types.abi import decode_return, encode_call, normalize_abi
from ..wallet.signer import PQSigner

try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object

app = typer.Typer(help="Call contract functions (read/write)")

__all__ = ["app"]


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


def _simulate_call(
    rpc: RpcClient,
    *,
    address: str,
    calldata: bytes,
    sender: Optional[str],
) -> Optional[bytes]:
    payload = {"to": address, "data": "0x" + calldata.hex()}
    if sender:
        payload["from"] = sender
    candidates = (
        ("execution.simulateCall", [payload]),
        ("state.call", [payload]),
        ("vm.simulateCall", [address, "0x" + calldata.hex(), sender, None]),
    )
    for method, params in candidates:
        try:
            out = rpc.request(method, params)
        except Exception:
            continue
        if isinstance(out, (bytes, bytearray)):
            return bytes(out)
        if isinstance(out, str) and out.startswith("0x"):
            try:
                return bytes.fromhex(out[2:])
            except Exception:
                return None
    return None


def _make_signer(alg: str, seed_hex: Optional[str]) -> PQSigner:
    seed_input = seed_hex or os.environ.get("OMNI_SDK_SEED_HEX")
    if not seed_input:
        raise typer.BadParameter(
            "missing seed: pass --seed-hex or set OMNI_SDK_SEED_HEX (dev/test only)"
        )
    try:
        seed = bytes.fromhex(seed_input.strip().removeprefix("0x"))
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter("--seed-hex must be hex") from exc
    return PQSigner.from_seed(alg, seed=seed)


def _resolve_nonce(rpc: RpcClient, sender: str, override: Optional[int]) -> int:
    if override is not None:
        return int(override)
    try:
        return int(rpc.request("state.getNonce", [sender]))
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"failed to fetch sender nonce: {exc}") from exc


@app.command("read")
def call_read(
    ctx: typer.Context,
    address: str = typer.Option(..., "--address", "-a"),
    abi: Path = typer.Option(..., "--abi"),
    func: str = typer.Option(..., "--func", "-f"),
    args_json: Optional[str] = typer.Option(None, "--args-json"),
    sender: Optional[str] = typer.Option(None, "--from"),
) -> None:
    """
    Simulate a read-only contract call and decode return values via ABI.
    """
    c: Ctx = ctx.obj  # type: ignore[assignment]
    rpc = RpcClient(c.rpc, timeout=c.timeout)
    abi_obj = _load_abi(abi)
    args_obj = _parse_args_json(args_json)
    args_list = _to_positional_args(abi_obj, func, args_obj)
    calldata = encode_call(abi_obj, func, args_list)
    raw = _simulate_call(rpc, address=address, calldata=calldata, sender=sender)
    if raw is None:
        raise typer.BadParameter(
            "node did not expose a recognized call simulation RPC method"
        )
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
    alg: str = typer.Option("dilithium3", "--alg"),
    max_fee: int = typer.Option(1, "--max-fee"),
    gas_limit: Optional[int] = typer.Option(None, "--gas-limit"),
    nonce: Optional[int] = typer.Option(None, "--nonce"),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
) -> None:
    """
    Build, sign, and send a state-changing contract call.
    """
    c: Ctx = ctx.obj  # type: ignore[assignment]
    rpc = RpcClient(c.rpc, timeout=c.timeout)
    abi_obj = _load_abi(abi)
    args_obj = _parse_args_json(args_json)
    args_list = _to_positional_args(abi_obj, func, args_obj)

    signer = _make_signer(alg, seed_hex)
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")
    nonce_value = _resolve_nonce(rpc, sender, nonce)
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
    sign_bytes = tx_encode.sign_bytes(tx)
    signature = signer.sign(sign_bytes)
    raw = tx_encode.pack_signed(
        tx,
        signature=signature,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    tx_hash = tx_send.submit_raw(rpc, raw)

    summary: Dict[str, Any] = {"txHash": tx_hash, "from": sender, "to": address, "func": func}
    if wait:
        receipt = tx_send.wait_for_receipt(rpc, tx_hash, timeout_s=max(c.timeout, 120.0))
        summary.update(
            {
                "status": receipt.get("status"),
                "gasUsed": receipt.get("gasUsed"),
                "blockNumber": receipt.get("blockNumber"),
            }
        )
    typer.echo(json.dumps(summary, indent=2))
