"""
omni_sdk.cli.call
=================

Typer sub-commands to *read* (simulate) and *write* (send a transaction to)
contract functions defined by a Python-VM ABI.

Quick examples
--------------
Read (simulate, no signing):
    $ omni-sdk call read \
        --address anim1qq... \
        --abi ./examples/counter/manifest.json \
        --func get

Write (send tx, sign with PQ key from seed for dev/test):
    $ omni-sdk call write \
        --address anim1qq... \
        --abi ./examples/counter/manifest.json \
        --func inc \
        --seed-hex 012345... \
        --alg dilithium3 \
        --wait

Arguments can be provided either as JSON (--args-json '[123,"0xdeadbeef"]')
or as repeated --arg key=value pairs (numbers auto-cast; 0x... → bytes).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import typer

from ..address import Address
from ..rpc.http import RpcClient
from ..tx.build import build_call_tx, estimate_call_gas
from ..tx.encode import encode_tx_cbor
from ..tx.send import await_receipt, send_raw_transaction
from ..wallet.signer import Signer

# Prefer to use the high-level Contract client if available.
_ContractClient = None
try:  # lazy/optional
    from ..contracts.client import ContractClient  # type: ignore

    _ContractClient = ContractClient
except Exception:
    pass

# Root context (rpc/chain_id/timeout) from main CLI
try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object  # only for typing

app = typer.Typer(help="Call contract functions (read/write)")

__all__ = ["app"]


# ------------------------------ arg parsing ----------------------------------


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise typer.BadParameter(f"File not found: {path}") from e
    except Exception as e:
        raise typer.BadParameter(f"Invalid JSON in: {path}") from e


def _auto_cast(value: str) -> Any:
    v = value.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.startswith("0x") or v.startswith("0X"):
        try:
            return bytes.fromhex(v[2:])
        except Exception:
            return v  # leave as string if not valid hex
    # int?
    try:
        if v.startswith("+") or v.startswith("-") or v.isdigit():
            return int(v, 10)
    except Exception:
        pass
    # float? (only if explicitly containing a dot)
    if "." in v:
        try:
            return float(v)
        except Exception:
            pass
    return v


def _parse_kv_args(items: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"--arg must be in key=value form, got {item!r}")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            raise typer.BadParameter(f"Invalid empty key in --arg {item!r}")
        out[k] = _auto_cast(v)
    return out


def _resolve_args(
    args_json: Optional[str], args_kv: List[str]
) -> Union[List[Any], Dict[str, Any], None]:
    """
    Merge/resolve args from --args-json and repeated --arg key=value.
    - If JSON is a list → positional args (list).
    - If JSON is an object → keyword args (dict).
    - If both provided, they are combined: list + dict, with kv overlaying keys.
    - If only kv provided → dict.
    """
    parsed: Union[List[Any], Dict[str, Any], None] = None
    if args_json:
        try:
            parsed = json.loads(args_json)
        except Exception as e:
            raise typer.BadParameter(f"--args-json must be valid JSON: {e}") from e
        if not isinstance(parsed, (list, dict)):
            raise typer.BadParameter("--args-json must be a JSON array or object")
    if args_kv:
        kv = _parse_kv_args(args_kv)
        if parsed is None:
            parsed = kv
        elif isinstance(parsed, dict):
            parsed.update(kv)
        else:
            # parsed is a list; kv cannot merge, so error
            raise typer.BadParameter(
                "Cannot mix positional --args-json (array) with named --arg key=value"
            )
    return parsed


def _make_signer(alg: str, seed_hex: Optional[str]) -> Signer:
    if not seed_hex:
        seed_hex = os.environ.get("OMNI_SDK_SEED_HEX")
    if not seed_hex:
        seed_hex = typer.prompt(
            "Enter signer seed as hex (dev/test only!)",
            hide_input=True,
            confirmation_prompt=False,
        )
    try:
        seed = bytes.fromhex(seed_hex.strip().replace("0x", ""))
    except Exception as e:
        raise typer.BadParameter(
            "seed-hex must be raw hex bytes (with or without 0x)"
        ) from e
    return Signer.from_seed(seed, alg=alg)  # type: ignore[attr-defined]


# ------------------------------ read (simulate) -------------------------------


@app.command("read")
def call_read(
    ctx: typer.Context,
    address: str = typer.Option(
        ..., "--address", "-a", help="Contract address (bech32m anim1...)"
    ),
    abi: Path = typer.Option(
        ..., "--abi", help="Path to ABI JSON (or full manifest including ABI)"
    ),
    func: str = typer.Option(..., "--func", "-f", help="Function name to call"),
    args_json: Optional[str] = typer.Option(
        None, "--args-json", help="Arguments as JSON array/object"
    ),
    arg: List[str] = typer.Option([], "--arg", help="Repeated named args: key=value"),
    sender: Optional[str] = typer.Option(
        None, "--from", help="Optional caller address (for view that reads sender)"
    ),
    block: Optional[str] = typer.Option(
        None, "--block", help="Optional block tag/number (if node supports)"
    ),
) -> None:
    """
    Simulate a contract call and print the decoded return value as JSON.
    """
    c: Ctx = ctx.obj  # type: ignore[assignment]
    client = RpcClient(c.rpc, timeout=c.timeout)

    # Load ABI (supports manifest that contains {"abi": [...]})
    abi_obj = _load_json_file(abi)
    if isinstance(abi_obj, dict) and "abi" in abi_obj:
        abi_def = abi_obj["abi"]
    else:
        abi_def = abi_obj

    args_payload = _resolve_args(args_json, arg)

    # Prefer the high-level client if available
    if _ContractClient:
        cc = _ContractClient(client, address=address, abi=abi_def, chain_id=c.chain_id)  # type: ignore
        if isinstance(args_payload, list) or args_payload is None:
            res = cc.read(func, *(args_payload or []), sender=sender, block=block)  # type: ignore[attr-defined]
        elif isinstance(args_payload, dict):
            res = cc.read(func, sender=sender, block=block, **args_payload)  # type: ignore[attr-defined]
        else:
            res = cc.read(func, sender=sender, block=block)  # type: ignore[attr-defined]
        typer.echo(json.dumps(res, indent=2, ensure_ascii=False))
        return

    # Fallback path: encode via ABI and try known simulate RPCs
    try:
        from ..abi.encoding import encode_call  # type: ignore
    except Exception as e:  # pragma: no cover
        raise typer.BadParameter(
            "ABI encoder not available; install omni_sdk.abi"
        ) from e

    call_payload = encode_call(abi_def, func, args_payload)  # returns bytes
    params_candidates = [
        # method, params
        (
            "execution.simulateCall",
            [
                {
                    "to": address,
                    "data": "0x" + call_payload.hex(),
                    "from": sender,
                    "block": block,
                }
            ],
        ),
        (
            "state.call",
            [
                {
                    "to": address,
                    "data": "0x" + call_payload.hex(),
                    "from": sender,
                    "block": block,
                }
            ],
        ),
        ("vm.simulateCall", [address, "0x" + call_payload.hex(), sender, block]),
    ]

    last_err: Optional[Exception] = None
    for method, p in params_candidates:
        try:
            raw = client.call(method, p)
            # Let the node decode, or return hex that we can decode via ABI if necessary.
            # If it's hex-like, try to decode via ABI outputs.
            if isinstance(raw, str) and raw.startswith("0x"):
                try:
                    from ..abi.decoding import decode_return  # type: ignore

                    decoded = decode_return(abi_def, func, bytes.fromhex(raw[2:]))
                    typer.echo(json.dumps(decoded, indent=2, ensure_ascii=False))
                    return
                except Exception:
                    pass
            typer.echo(json.dumps(raw, indent=2, ensure_ascii=False))
            return
        except Exception as e:
            last_err = e
            continue
    raise typer.BadParameter(
        f"Call simulation not supported by node RPC (tried methods); last error: {last_err}"
    )


# ------------------------------ write (send tx) -------------------------------


@app.command("write")
def call_write(
    ctx: typer.Context,
    address: str = typer.Option(
        ..., "--address", "-a", help="Contract address (bech32m anim1...)"
    ),
    abi: Path = typer.Option(
        ..., "--abi", help="Path to ABI JSON (or manifest containing ABI)"
    ),
    func: str = typer.Option(..., "--func", "-f", help="Function name to invoke"),
    args_json: Optional[str] = typer.Option(
        None, "--args-json", help="Arguments as JSON array/object"
    ),
    arg: List[str] = typer.Option([], "--arg", help="Repeated named args: key=value"),
    seed_hex: Optional[str] = typer.Option(
        None,
        "--seed-hex",
        help="Signer seed as hex (dev/test only; can use OMNI_SDK_SEED_HEX)",
    ),
    alg: str = typer.Option(
        "dilithium3",
        "--alg",
        help="PQ signature algorithm: dilithium3 | sphincs_shake_128s",
    ),
    gas_price: Optional[int] = typer.Option(
        None, "--gas-price", help="Optional gas price override"
    ),
    gas_limit: Optional[int] = typer.Option(
        None, "--gas-limit", help="Optional gas limit override"
    ),
    nonce: Optional[int] = typer.Option(
        None, "--nonce", help="Optional sender nonce override"
    ),
    wait: bool = typer.Option(
        True, "--wait/--no-wait", help="Wait for transaction receipt."
    ),
) -> None:
    """
    Build, sign, and send a contract call transaction. Prints JSON summary.
    """
    c: Ctx = ctx.obj  # type: ignore[assignment]
    client = RpcClient(c.rpc, timeout=c.timeout)

    # Load ABI (supports manifest that contains {"abi": [...]})
    abi_obj = _load_json_file(abi)
    abi_def = (
        abi_obj["abi"] if isinstance(abi_obj, dict) and "abi" in abi_obj else abi_obj
    )

    args_payload = _resolve_args(args_json, arg)

    # Build signer and sender address
    signer = _make_signer(alg, seed_hex)
    sender_addr = Address.from_public_key(signer.public_key_bytes(), alg=signer.alg_id).bech32  # type: ignore

    # If available, use the high-level Contract client for convenience
    if _ContractClient:
        cc = _ContractClient(client, address=address, abi=abi_def, chain_id=c.chain_id)  # type: ignore

        # Estimate gas if not provided
        if gas_limit is None:
            try:
                if isinstance(args_payload, list) or args_payload is None:
                    gas_limit = int(cc.estimate_gas(func, *(args_payload or []), sender=sender_addr))  # type: ignore[attr-defined]
                elif isinstance(args_payload, dict):
                    gas_limit = int(cc.estimate_gas(func, sender=sender_addr, **args_payload))  # type: ignore[attr-defined]
                else:
                    gas_limit = int(cc.estimate_gas(func, sender=sender_addr))  # type: ignore[attr-defined]
            except Exception:
                gas_limit = 500_000  # conservative fallback

        if isinstance(args_payload, list) or args_payload is None:
            tx = cc.build_tx(func, *(args_payload or []), sender=sender_addr, gas_price=gas_price, gas_limit=gas_limit, nonce=nonce)  # type: ignore[attr-defined]
        elif isinstance(args_payload, dict):
            tx = cc.build_tx(func, sender=sender_addr, gas_price=gas_price, gas_limit=gas_limit, nonce=nonce, **args_payload)  # type: ignore[attr-defined]
        else:
            tx = cc.build_tx(func, sender=sender_addr, gas_price=gas_price, gas_limit=gas_limit, nonce=nonce)  # type: ignore[attr-defined]

        sig = signer.sign(tx.sign_bytes, domain="tx")
        tx.attach_signature(alg_id=signer.alg_id, signature=sig)
        raw = encode_tx_cbor(tx)
        tx_hash = send_raw_transaction(client, raw)

        receipt: Optional[Dict[str, Any]] = None
        if wait:
            receipt = await_receipt(
                client, tx_hash, timeout_seconds=max(c.timeout, 60.0)
            )

        summary = {
            "txHash": tx_hash,
            "sender": sender_addr,
            "to": address,
            "func": func,
        }
        if receipt:
            summary.update(
                {
                    "status": receipt.get("status"),
                    "blockNumber": receipt.get("blockNumber"),
                    "gasUsed": receipt.get("gasUsed"),
                    "contractAddress": receipt.get("contractAddress"),
                }
            )
        typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    # Fallback: encode ABI, use generic tx builder
    try:
        from ..abi.encoding import encode_call  # type: ignore
    except Exception as e:  # pragma: no cover
        raise typer.BadParameter(
            "ABI encoder not available; install omni_sdk.abi"
        ) from e

    call_data = encode_call(abi_def, func, args_payload)  # bytes

    # Estimate gas if needed
    if gas_limit is None:
        try:
            gas_est = estimate_call_gas(
                client, to=address, data=call_data, sender=sender_addr
            )
            gas_limit = (
                int(gas_est["gasLimit"])
                if isinstance(gas_est, dict) and "gasLimit" in gas_est
                else int(gas_est)
            )
        except Exception:
            gas_limit = 500_000

    # Build tx
    tx = build_call_tx(
        chain_id=c.chain_id,
        sender=sender_addr,
        to=address,
        data=call_data,
        gas_price=gas_price,
        gas_limit=gas_limit,
        nonce=nonce,
    )

    # Sign & submit
    sig = signer.sign(tx.sign_bytes, domain="tx")
    tx.attach_signature(alg_id=signer.alg_id, signature=sig)
    raw = encode_tx_cbor(tx)
    tx_hash = send_raw_transaction(client, raw)

    receipt: Optional[Dict[str, Any]] = None
    if wait:
        receipt = await_receipt(client, tx_hash, timeout_seconds=max(c.timeout, 60.0))

    summary = {"txHash": tx_hash, "sender": sender_addr, "to": address, "func": func}
    if receipt:
        summary.update(
            {
                "status": receipt.get("status"),
                "blockNumber": receipt.get("blockNumber"),
                "gasUsed": receipt.get("gasUsed"),
                "contractAddress": receipt.get("contractAddress"),
            }
        )
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
