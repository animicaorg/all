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
from ..types import abi as abi_codec
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


def _load_abi_document(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"ABI file not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid JSON in ABI file {path}: {exc}") from exc


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
        ("state.call", [payload]),
        ("execution.simulateCall", [payload]),
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


def _normalize_address_for_match(value: Any) -> Optional[str]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex().lower()
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text.startswith(("0x", "0X")):
        try:
            raw = bytes.fromhex(text[2:])
        except Exception:
            return text
        return "0x" + raw.hex()
    if text.startswith("anim"):
        return text
    try:
        raw = bytes.fromhex(text)
    except Exception:
        return text
    return "0x" + raw.hex()


def _decode_call_data(abi_obj: Any, data: bytes) -> Optional[tuple[str, List[Any]]]:
    if len(data) < 8:
        return None
    normalized = normalize_abi(abi_obj)
    functions = normalized.get("functions", {})
    if not isinstance(functions, dict):
        return None
    selector = data[:8]
    for fn_name, fn_def in functions.items():
        if not isinstance(fn_name, str) or not isinstance(fn_def, dict):
            continue
        try:
            fn_selector = abi_codec.function_selector(fn_def)
        except Exception:
            continue
        if fn_selector != selector:
            continue

        inputs = fn_def.get("inputs", [])
        if not isinstance(inputs, list):
            return None
        try:
            arg_count, offset = abi_codec._uvarint_decode(data, 8)  # type: ignore[attr-defined]
        except Exception:
            return None
        if arg_count != len(inputs):
            return None

        values: List[Any] = []
        for param in inputs:
            if not isinstance(param, dict):
                return None
            typ = param.get("type")
            if not isinstance(typ, str):
                return None
            try:
                desc = abi_codec._parse_type(typ)  # type: ignore[attr-defined]
                value, offset = abi_codec._decode_desc(data, desc, offset)  # type: ignore[attr-defined]
            except Exception:
                return None
            values.append(value)

        if offset != len(data):
            return None
        return fn_name, values
    return None


def _encode_return_data(abi_obj: Any, fn_name: str, value: Any) -> Optional[bytes]:
    normalized = normalize_abi(abi_obj)
    fn_def = normalized.get("functions", {}).get(fn_name)
    if not isinstance(fn_def, dict):
        return None
    outputs = fn_def.get("outputs", [])
    if not isinstance(outputs, list):
        return None
    if len(outputs) == 0:
        return b""

    encoded: List[bytes] = []
    encoded.append(abi_codec._uvarint_encode(len(outputs)))  # type: ignore[attr-defined]

    if len(outputs) == 1:
        values = [value]
    else:
        if not isinstance(value, (list, tuple)) or len(value) != len(outputs):
            return None
        values = list(value)

    for out_item, out_value in zip(outputs, values):
        if not isinstance(out_item, dict):
            return None
        typ = out_item.get("type")
        if not isinstance(typ, str):
            return None
        try:
            desc = abi_codec._parse_type(typ)  # type: ignore[attr-defined]
            encoded.append(abi_codec._encode_desc(out_value, desc))  # type: ignore[attr-defined]
        except Exception:
            return None
    return b"".join(encoded)


def _chain_head_height(rpc: RpcClient) -> int:
    out = rpc.request("chain.getHead", [])
    if not isinstance(out, dict):
        raise RuntimeError("chain.getHead did not return an object")
    for key in ("height", "number", "headHeight"):
        if key in out and out[key] is not None:
            return int(out[key])
    raise RuntimeError("chain.getHead response did not include a height")


def _coerce_payload_kind(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 16) if text.startswith(("0x", "0X")) else int(text)
        except Exception:
            return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_data_bytes(value: Any) -> Optional[bytes]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return b""
        if text.startswith(("0x", "0X")):
            text = text[2:]
        if len(text) % 2 != 0:
            text = "0" + text
        try:
            return bytes.fromhex(text)
        except Exception:
            return None
    return None


def _coerce_bytes_loose(value: Any) -> Optional[bytes]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return b""
        if text.startswith(("0x", "0X")):
            text = text[2:]
            if len(text) % 2 != 0:
                text = "0" + text
            try:
                return bytes.fromhex(text)
            except Exception:
                return None
        # Accept plain JSON text payloads too.
        return value.encode("utf-8")
    return None


def _extract_tx_body(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return None
    tx_candidate = entry.get("tx")
    if isinstance(tx_candidate, dict):
        return tx_candidate
    return entry


def _derive_contract_address_from_deploy_tx(tx_obj: Any) -> Optional[str]:
    try:
        from core.utils.deploy_metadata import build_python_vm_package_deploy_metadata

        meta = build_python_vm_package_deploy_metadata(tx_obj)
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    addr = meta.get("contractAddress") or meta.get("createdAddress")
    if not isinstance(addr, str) or not addr.strip():
        return None
    return _normalize_address_for_match(addr)


def _manifest_from_deploy_tx(tx_obj: Any) -> Optional[dict[str, Any]]:
    if not isinstance(tx_obj, dict):
        return None
    payload = tx_obj.get("payload")
    if not isinstance(payload, dict):
        return None
    if _coerce_payload_kind(payload.get("t")) != 1:
        return None
    payload_v = payload.get("v")
    if not isinstance(payload_v, dict):
        return None
    manifest_raw = payload_v.get("manifest")
    manifest_bytes = _coerce_bytes_loose(manifest_raw)
    if manifest_bytes is None:
        return None
    try:
        decoded = json.loads(manifest_bytes.decode("utf-8"))
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _find_deploy_manifest_for_address(
    rpc: RpcClient,
    *,
    address: str,
    head_height: int,
) -> Optional[dict[str, Any]]:
    target = _normalize_address_for_match(address)
    if target is None:
        return None

    for height in range(max(0, int(head_height)), -1, -1):
        try:
            block = rpc.request("chain.getBlockByHeight", [height, True, True])
        except Exception:
            try:
                block = rpc.request("chain.getBlockByHeight", [height, False, False])
            except Exception:
                continue
        if not isinstance(block, dict):
            continue

        tx_entries = block.get("transactions", block.get("txs", []))
        if not isinstance(tx_entries, list):
            continue
        for entry in tx_entries:
            tx_obj = _extract_tx_body(entry)
            if not isinstance(tx_obj, dict):
                continue
            derived = _derive_contract_address_from_deploy_tx(tx_obj)
            if derived != target:
                continue
            manifest = _manifest_from_deploy_tx(tx_obj)
            if isinstance(manifest, dict):
                return manifest
    return None


def _extract_call_destination_and_data(tx_obj: Any) -> Optional[tuple[Any, bytes]]:
    if not isinstance(tx_obj, dict):
        return None

    to_value: Any = tx_obj.get("to")
    data_value: Any = tx_obj.get("data")
    payload = tx_obj.get("payload")
    if isinstance(payload, dict):
        payload_kind = _coerce_payload_kind(payload.get("t"))
        payload_value = payload.get("v")
        if isinstance(payload_value, dict):
            if payload_kind in (0, 2, 3):
                to_value = payload_value.get("to", to_value)
                data_value = payload_value.get("data", data_value)
            elif to_value is None:
                to_value = payload_value.get("to")

    data_bytes = _coerce_data_bytes(data_value)
    if data_bytes is None:
        return None
    if to_value is None:
        return None
    return to_value, data_bytes


def _iter_block_call_payloads(
    rpc: RpcClient,
    *,
    block_hash: str,
) -> List[tuple[Any, bytes]]:
    payloads: List[tuple[Any, bytes]] = []
    try:
        raw_block = rpc.request("debug.getRawBlock", [block_hash])
    except Exception:
        return payloads
    if not isinstance(raw_block, dict):
        return payloads

    raw_block_hex = raw_block.get("blockCbor", raw_block.get("block_cbor"))
    if not isinstance(raw_block_hex, str):
        return payloads
    block_hex = raw_block_hex[2:] if raw_block_hex.startswith(("0x", "0X")) else raw_block_hex
    if not block_hex:
        return payloads
    try:
        raw_bytes = bytes.fromhex(block_hex)
    except Exception:
        return payloads

    try:
        import cbor2

        block_obj = cbor2.loads(raw_bytes)
    except Exception:
        return payloads
    if not isinstance(block_obj, dict):
        return payloads

    tx_entries = block_obj.get("txs", [])
    if not isinstance(tx_entries, list):
        return payloads
    for entry in tx_entries:
        tx_body = None
        if isinstance(entry, dict):
            tx_candidate = entry.get("tx")
            tx_body = tx_candidate if isinstance(tx_candidate, dict) else entry
        parsed = _extract_call_destination_and_data(tx_body)
        if parsed is not None:
            payloads.append(parsed)
    return payloads


def _iter_confirmed_call_payloads(
    rpc: RpcClient,
    *,
    head_height: int,
) -> List[tuple[Any, bytes]]:
    payloads: List[tuple[Any, bytes]] = []
    for height in range(max(0, int(head_height)) + 1):
        try:
            block = rpc.request("chain.getBlockByHeight", [height, True, True])
        except Exception:
            try:
                block = rpc.request("chain.getBlockByHeight", [height, False, False])
            except Exception:
                continue
        if not isinstance(block, dict):
            continue

        tx_list = block.get("transactions", block.get("txs", []))
        block_payloads = []
        if isinstance(tx_list, list):
            for tx_entry in tx_list:
                parsed = _extract_call_destination_and_data(tx_entry)
                if parsed is not None:
                    block_payloads.append(parsed)

        if block_payloads:
            payloads.extend(block_payloads)
            continue

        block_hash = block.get("hash")
        if isinstance(block_hash, str) and block_hash:
            payloads.extend(_iter_block_call_payloads(rpc, block_hash=block_hash))
    return payloads


def _simulate_call_local_replay(
    rpc: RpcClient,
    *,
    address: str,
    abi_path: Path,
    abi_obj: Any,
    func: str,
    args: List[Any],
) -> tuple[Optional[bytes], Any, Dict[str, Any]]:
    manifest_path = abi_path.expanduser().resolve()
    manifest_doc = _load_abi_document(abi_path)
    if not isinstance(manifest_doc, dict):
        raise RuntimeError(
            "local replay fallback requires --abi to point to a contract manifest object"
        )
    replay_manifest: dict[str, Any] = dict(manifest_doc)
    manifest_input: Any = dict(replay_manifest)
    manifest_source = "abi_path"

    try:
        from stdlib import storage as stdlib_storage  # type: ignore
        from vm_py.runtime.loader import run_call as vm_run_call  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "local replay fallback requires vm_py runtime modules (stdlib, vm_py.runtime.loader)"
        ) from exc

    if callable(getattr(stdlib_storage, "reset_backend", None)):
        stdlib_storage.reset_backend()

    target = _normalize_address_for_match(address)
    if target is None:
        raise RuntimeError(f"invalid contract address for replay: {address!r}")

    head_height = _chain_head_height(rpc)

    # If --abi points to an ABI-only document, recover a richer manifest from chain
    # deployment data so replay can still execute from source metadata.
    if not any(key in replay_manifest for key in ("source", "entry", "sources", "code", "path")):
        recovered = _find_deploy_manifest_for_address(
            rpc,
            address=address,
            head_height=head_height,
        )
        if isinstance(recovered, dict):
            replay_manifest = recovered
            manifest_input = dict(replay_manifest)
            manifest_source = "onchain_deploy_manifest"
        else:
            raise RuntimeError(
                "local replay fallback could not recover manifest source metadata "
                "(expected source/entry/sources or deploy-manifest on chain)"
            )
    elif any(key in replay_manifest for key in ("source", "entry", "sources", "code", "path")):
        # Prefer path input when --abi is itself a manifest file so relative source
        # paths resolve against the manifest directory.
        manifest_input = str(manifest_path)

    if not any(key in replay_manifest for key in ("source", "entry", "sources", "code", "path")):
        raise RuntimeError(
            "local replay fallback requires manifest source metadata "
            "(source/entry/sources/code/path)"
        )

    call_payloads = _iter_confirmed_call_payloads(rpc, head_height=head_height)
    replayed_calls = 0

    for tx_to_raw, calldata in call_payloads:
        tx_to = _normalize_address_for_match(tx_to_raw)
        if tx_to != target:
            continue
        decoded = _decode_call_data(abi_obj, calldata)
        if decoded is None:
            continue
        tx_fn, tx_args = decoded
        try:
            vm_run_call(manifest_input, tx_fn, tx_args)
            replayed_calls += 1
        except Exception:
            continue

    call_out = vm_run_call(manifest_input, func, list(args))
    decoded_result = call_out.get("result") if isinstance(call_out, dict) else call_out
    raw_result = _encode_return_data(abi_obj, func, decoded_result)
    meta = {
        "source": "sdk_local_replay",
        "head_height": head_height,
        "replayed_calls": replayed_calls,
        "manifest_source": manifest_source,
    }
    return raw_result, decoded_result, meta


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


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 16) if text.startswith(("0x", "0X")) else int(text)
        except Exception:
            return None
    try:
        return int(value)
    except Exception:
        return None


def _infer_chain_height(rpc: RpcClient) -> Optional[int]:
    for method in ("chain.getHead", "chain.getInfo", "node.status", "chain.getStatus"):
        try:
            out = rpc.request(method, [])
        except Exception:
            continue
        if isinstance(out, dict):
            for key in (
                "height",
                "head_height",
                "best_height",
                "block_height",
                "bestHeight",
                "headHeight",
                "blockHeight",
                "number",
            ):
                value = _coerce_int(out.get(key))
                if value is not None:
                    return value
            head = out.get("head")
            if isinstance(head, dict):
                for key in ("height", "number", "head_height", "headHeight"):
                    value = _coerce_int(head.get(key))
                    if value is not None:
                        return value
        else:
            value = _coerce_int(out)
            if value is not None:
                return value
    return None


def _resolve_validity_window_from_rpc(
    rpc: RpcClient,
    *,
    ttl_blocks: int = 120,
) -> tuple[int, int]:
    height = _infer_chain_height(rpc)
    if height is None:
        raise typer.BadParameter(
            "could not infer chain height for call tx validity window"
        )
    valid_after = int(height)
    valid_until = int(valid_after + max(1, int(ttl_blocks)))
    if valid_until <= valid_after:
        valid_until = valid_after + 1
    return valid_after, valid_until


def _address_to_account_key(value: str | bytes | bytearray | memoryview) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) != 32:
            raise typer.BadParameter(
                f"address/account key must be 32 bytes, got {len(raw)}"
            )
        return raw
    if not isinstance(value, str):
        raise typer.BadParameter(
            f"address/account key must be string or 32-byte value, got {type(value).__name__}"
        )
    text = value.strip()
    if text.startswith(("0x", "0X")):
        raw = bytes.fromhex(text[2:])
        if len(raw) != 32:
            raise typer.BadParameter(
                f"hex account key must be 32 bytes, got {len(raw)}"
            )
        return raw
    try:
        from pq.py.address import decode_address

        record = decode_address(text)
        digest = bytes(record.digest) if isinstance(record.digest, list) else bytes(record.digest)
        raw = digest[:32].ljust(32, b"\x00")
        if len(raw) == 32:
            return raw
    except Exception:
        pass
    try:
        from ..address import parse as parse_address

        parsed = parse_address(text)
        pubkey_hash = parsed.get("pubkey_hash")
        if isinstance(pubkey_hash, (bytes, bytearray, memoryview)):
            raw = bytes(pubkey_hash)
            if len(raw) == 32:
                return raw
    except Exception as exc:
        raise typer.BadParameter(f"invalid address {value!r}: {exc}") from exc
    raise typer.BadParameter(f"invalid address/account key: {value!r}")


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
    raw: Optional[bytes] = None
    replay_meta: Optional[Dict[str, Any]] = None
    simulation_source = "rpc_simulation"
    try:
        raw = _simulate_call(rpc_client, address=address, calldata=calldata, sender=sender)
        decoded = decode_return(abi_obj, func, raw)
    except typer.BadParameter as exc:
        if "node did not expose a recognized call simulation RPC method" not in str(exc):
            raise
        try:
            raw, decoded, replay_meta = _simulate_call_local_replay(
                rpc_client,
                address=address,
                abi_path=abi,
                abi_obj=abi_obj,
                func=func,
                args=args_list,
            )
        except Exception as replay_exc:
            raise typer.BadParameter(
                f"{exc}; local replay fallback failed: {replay_exc}"
            ) from replay_exc
        simulation_source = "sdk_local_replay"
    summary: Dict[str, Any] = {
        "status": "ok",
        "rpc_url": rpc_endpoint,
        "rpcUrl": rpc_endpoint,
        "chain_id": int(chain_id_value),
        "chainId": int(chain_id_value),
        "address": address,
        "func": func,
        "args": args_list,
        "result": ("0x" + raw.hex()) if isinstance(raw, (bytes, bytearray)) else None,
        "decoded_result": decoded,
        "decodedResult": decoded,
        "simulation_source": simulation_source,
        "simulationSource": simulation_source,
    }
    if replay_meta:
        summary["replay"] = replay_meta
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
    calldata = encode_call(abi_obj, func, args_list)
    valid_after, valid_until = _resolve_validity_window_from_rpc(rpc_client)
    resolved_gas_limit = (
        int(gas_limit)
        if gas_limit is not None
        else tx_build.suggest_gas_limit("call", calldata_len=len(calldata))
    )
    tx: Dict[str, Any] = {
        "v": 2,
        "chainId": int(chain_id_value),
        "from": _address_to_account_key(sender),
        "gas": {"price": int(max_fee), "limit": int(resolved_gas_limit)},
        "payload": {
            "t": 2,
            "v": {
                "to": _address_to_account_key(address),
                "data": bytes(calldata),
            },
        },
        "accessList": [],
        "validAfter": int(valid_after),
        "validUntil": int(valid_until),
        "salt": os.urandom(16),
    }
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
        "tx_kind": "call",
        "txKind": "call",
        "valid_after": int(valid_after),
        "validAfter": int(valid_after),
        "valid_until": int(valid_until),
        "validUntil": int(valid_until),
        "gas_limit": int(resolved_gas_limit),
        "gasLimit": int(resolved_gas_limit),
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
