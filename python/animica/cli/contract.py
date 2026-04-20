from __future__ import annotations

import ast
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer

from animica.contracts import abi_utils
from animica.contracts import artifacts as artifact_store
from animica.contracts import deployments as deployment_store
from animica.contracts import wallet_utils
from animica.contracts.rpc_utils import (
    RpcAdapter,
    get_tx_status,
    resolve_chain_id,
    resolve_network_hint,
    resolve_rpc_url,
    wait_for_receipt,
)

app = typer.Typer(
    help="Compile, deploy, inspect, and interact with smart contracts.",
    no_args_is_help=True,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(_json_safe(payload), indent=2, sort_keys=True, ensure_ascii=False))


def _text_or_json(*, payload: dict[str, Any], json_output: bool, lines: list[str]) -> None:
    if json_output:
        _emit_json(payload)
        return
    for line in lines:
        typer.echo(line)


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"{label} file not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid JSON in {label} file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise typer.BadParameter(f"{label} file must contain a JSON object: {path}")
    return data


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


def _is_hex_address(value: str) -> bool:
    text = value.strip()
    if not text.startswith(("0x", "0X")):
        return False
    raw = text[2:]
    if len(raw) != 64:
        return False
    try:
        bytes.fromhex(raw)
    except Exception:
        return False
    return True


def _is_hex_tx_hash(value: str) -> bool:
    text = value.strip()
    if not text.startswith(("0x", "0X")):
        return False
    raw = text[2:]
    if len(raw) != 64:
        return False
    try:
        bytes.fromhex(raw)
    except Exception:
        return False
    return True


def _is_address_like(value: str) -> bool:
    text = value.strip()
    return wallet_utils.is_animica_address(text) or _is_hex_address(text)


def _resolve_sender_hint(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if _is_address_like(text):
        return text
    return wallet_utils.resolve_wallet_address(text)


def _resolve_nonce(rpc: RpcAdapter, sender: str, override: Optional[int]) -> int:
    if override is not None:
        return int(override)
    errors: list[str] = []
    for params in ([sender, "pending"], [sender]):
        try:
            return int(rpc.request("state.getNonce", params))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"state.getNonce({params!r}) failed: {exc}")
    raise typer.BadParameter(f"failed to fetch sender nonce: {'; '.join(errors)}")


def _first_int(d: dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        parsed = _coerce_int(d.get(key))
        if parsed is not None:
            return parsed
    return None


def _first_str(d: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = d.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _extract_contract_address(receipt: Optional[dict[str, Any]]) -> Optional[str]:
    if not isinstance(receipt, dict):
        return None
    for key in (
        "contractAddress",
        "contract_address",
        "createdAddress",
        "created_address",
        "address",
    ):
        value = receipt.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    logs = receipt.get("logs")
    if isinstance(logs, list):
        for log in logs:
            if not isinstance(log, dict):
                continue
            for key in (
                "contractAddress",
                "contract_address",
                "createdAddress",
                "created_address",
                "address",
            ):
                value = log.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _fetch_receipt_once(rpc: RpcAdapter, tx_hash: str) -> Optional[dict[str, Any]]:
    for method in ("tx.getTransactionReceipt", "tx.getReceipt", "tx.getInstantReceipt"):
        try:
            result = rpc.request(method, [tx_hash])
        except Exception:
            continue
        if isinstance(result, dict) and result:
            return result
    return None


def _poll_transaction_confirmation(
    rpc: RpcAdapter,
    tx_hash: str,
    *,
    wait_timeout_secs: float,
    poll_interval_secs: float,
) -> dict[str, Any]:
    timeout = max(0.1, float(wait_timeout_secs))
    interval = max(0.1, float(poll_interval_secs))
    deadline = time.monotonic() + timeout

    last_status: Optional[str] = None
    last_block_height: Optional[int] = None

    while True:
        receipt = _fetch_receipt_once(rpc, tx_hash)
        if isinstance(receipt, dict) and receipt:
            tx_status = _first_str(receipt, "status", "tx_status")
            block_height = _first_int(receipt, "blockNumber", "block_height", "height")

            if tx_status is None or block_height is None:
                status_view = get_tx_status(rpc, tx_hash)
                if isinstance(status_view, dict):
                    if tx_status is None:
                        tx_status = _first_str(status_view, "status", "state", "tx_status")
                    if block_height is None:
                        block_height = _first_int(
                            status_view,
                            "included_height",
                            "blockNumber",
                            "block_height",
                            "height",
                        )

            return {
                "receipt": receipt,
                "tx_status": tx_status,
                "block_height": block_height,
                "confirmed": True,
                "success": _tx_status_is_success(tx_status),
                "timed_out": False,
            }

        status_view = get_tx_status(rpc, tx_hash)
        if isinstance(status_view, dict):
            status_candidate = _first_str(status_view, "status", "state", "tx_status")
            if status_candidate is not None:
                last_status = status_candidate
            block_candidate = _first_int(status_view, "included_height", "blockNumber", "block_height", "height")
            if block_candidate is not None:
                last_block_height = block_candidate

            success = _tx_status_is_success(last_status)
            if success is not None:
                return {
                    "receipt": None,
                    "tx_status": last_status,
                    "block_height": last_block_height,
                    "confirmed": True,
                    "success": success,
                    "timed_out": False,
                }

        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    receipt_after_timeout = _fetch_receipt_once(rpc, tx_hash)
    if isinstance(receipt_after_timeout, dict) and receipt_after_timeout:
        tx_status = _first_str(receipt_after_timeout, "status", "tx_status") or last_status
        block_height = (
            _first_int(receipt_after_timeout, "blockNumber", "block_height", "height") or last_block_height
        )
        return {
            "receipt": receipt_after_timeout,
            "tx_status": tx_status,
            "block_height": block_height,
            "confirmed": True,
            "success": _tx_status_is_success(tx_status),
            "timed_out": False,
        }

    return {
        "receipt": None,
        "tx_status": last_status,
        "block_height": last_block_height,
        "confirmed": False,
        "success": None,
        "timed_out": True,
    }


def _derive_contract_address(
    tx_obj: Any,
    *,
    tx_hash: str,
    sender: str,
    chain_id: int,
    receipt: Optional[dict[str, Any]],
) -> Optional[str]:
    try:
        from core.utils.deploy_metadata import build_python_vm_package_deploy_metadata
    except Exception:
        return None

    try:
        derived = build_python_vm_package_deploy_metadata(
            tx_obj,
            tx_hash=tx_hash,
            sender=sender,
            block_hash=(receipt or {}).get("blockHash") if isinstance(receipt, dict) else None,
            block_number=_first_int(receipt or {}, "blockNumber", "block_height", "height"),
            status=(receipt or {}).get("status") if isinstance(receipt, dict) else None,
            chain_id=chain_id,
        )
    except Exception:
        return None

    if isinstance(derived, dict):
        value = derived.get("contractAddress")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _deployment_lookup_key(rpc_url: Optional[str]) -> tuple[Optional[str], Optional[int], Optional[str]]:
    if rpc_url is None:
        return None, None, resolve_network_hint()
    adapter = RpcAdapter(rpc_url)
    chain_id = resolve_chain_id(adapter, None)
    network = resolve_network_hint()
    return deployment_store.network_key(chain_id=chain_id, network=network), chain_id, network


def _resolve_deployment_context(
    *,
    rpc: Optional[str],
    network_override: Optional[str],
    chain_id_override: Optional[int],
) -> tuple[str, Optional[int], Optional[str], str]:
    rpc_url = resolve_rpc_url(rpc)
    network_name = (network_override or "").strip() or resolve_network_hint()
    chain_id: Optional[int] = int(chain_id_override) if chain_id_override is not None else None
    if chain_id is None:
        try:
            chain_id = resolve_chain_id(RpcAdapter(rpc_url), None)
        except Exception:
            chain_id = None
    deployment_key = deployment_store.network_key(chain_id=chain_id, network=network_name)
    return deployment_key, chain_id, network_name, rpc_url


def _resolve_contract_identifier(
    identifier: str,
    *,
    preferred_key: Optional[str] = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    text = identifier.strip()
    if not text:
        raise typer.BadParameter("contract identifier cannot be empty")
    if _is_address_like(text):
        return text, None

    if preferred_key:
        found = deployment_store.resolve_deployment(text, key=preferred_key)
        if isinstance(found, dict):
            address = str(found.get("address") or "").strip()
            if address:
                return address, found

    found = deployment_store.resolve_deployment(text, key=None)
    if isinstance(found, dict):
        address = str(found.get("address") or "").strip()
        if address:
            return address, found

    if preferred_key:
        raise typer.BadParameter(
            f"contract '{identifier}' not found in saved deployments for '{preferred_key}'"
        )
    raise typer.BadParameter(f"contract '{identifier}' not found in saved deployments")


def _load_abi_and_path(
    *,
    abi_path_opt: Optional[Path],
    deployment_record: Optional[dict[str, Any]],
    manifest_obj: Optional[dict[str, Any]],
) -> tuple[Optional[Any], Optional[Path]]:
    if abi_path_opt is not None:
        resolved = abi_path_opt.expanduser().resolve()
        return abi_utils.load_abi_document(resolved), resolved

    if isinstance(deployment_record, dict):
        stored = deployment_record.get("abi_path")
        if isinstance(stored, str) and stored.strip():
            path = Path(stored).expanduser().resolve()
            if path.exists():
                return abi_utils.load_abi_document(path), path

    if isinstance(manifest_obj, dict):
        abi_obj = manifest_obj.get("abi")
        if isinstance(abi_obj, (dict, list)):
            return abi_utils.coerce_abi(abi_obj), None

    return None, None


def _coerce_result_bytes(value: Any) -> Optional[bytes]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str) and value.startswith(("0x", "0X")):
        try:
            return bytes.fromhex(value[2:])
        except Exception:
            return None
    if isinstance(value, dict):
        candidates: list[Any] = [
            value.get("returnData"),
            value.get("return_data"),
            value.get("result"),
            value.get("data"),
            value.get("output"),
            value.get("raw"),
            value.get("bytes"),
        ]
        nested = value.get("result")
        if isinstance(nested, dict):
            candidates.extend(
                [
                    nested.get("returnData"),
                    nested.get("return_data"),
                    nested.get("data"),
                    nested.get("output"),
                    nested.get("raw"),
                ]
            )
        for item in candidates:
            out = _coerce_result_bytes(item)
            if out is not None:
                return out
    return None


def _simulate_call(
    rpc: RpcAdapter,
    *,
    contract_address: str,
    calldata: bytes,
    sender: Optional[str],
) -> tuple[str, Any, Optional[bytes]]:
    payload = {"to": contract_address, "data": "0x" + calldata.hex()}
    if sender:
        payload["from"] = sender

    candidates: list[tuple[str, list[Any]]] = [
        ("state.call", [payload]),
        ("execution.simulateCall", [payload]),
        ("vm.simulateCall", [contract_address, payload["data"], sender, None]),
        ("state.simulateCall", [payload]),
        ("call.simulate", [payload]),
        ("contracts.simulate", [payload]),
    ]

    errors: list[str] = []
    for method_name, params in candidates:
        try:
            result = rpc.request(method_name, params)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{method_name}: {exc}")
            continue
        return method_name, result, _coerce_result_bytes(result)

    method_list = ", ".join(name for name, _ in candidates)
    detail = "; ".join(errors[:4]) if errors else "no methods responded"
    raise RuntimeError(
        "node did not expose a recognized call simulation RPC method; "
        f"probed methods: {method_list}; details: {detail}"
    )


def _simulate_call_with_fallback(
    *,
    rpc_url: str,
    rpc: RpcAdapter,
    contract_address: str,
    calldata: bytes,
    sender: Optional[str],
    abi_path: Optional[Path],
    abi_obj: Optional[Any],
    method: Optional[str],
    args: Optional[list[Any]],
) -> tuple[str, Any, Optional[bytes], Optional[Any], Optional[dict[str, Any]]]:
    try:
        rpc_method, raw_result, raw_bytes = _simulate_call(
            rpc,
            contract_address=contract_address,
            calldata=calldata,
            sender=sender,
        )
        return rpc_method, raw_result, raw_bytes, None, None
    except RuntimeError as exc:
        if abi_path is None or abi_obj is None or method is None:
            raise typer.BadParameter(str(exc)) from exc

        try:
            from omni_sdk.cli import call as sdk_call_cli
            from omni_sdk.rpc.http import RpcClient

            replay_raw, replay_decoded, replay_meta = sdk_call_cli._simulate_call_local_replay(  # type: ignore[attr-defined]
                RpcClient(rpc_url, timeout=30.0),
                address=contract_address,
                abi_path=abi_path,
                abi_obj=abi_obj,
                func=method,
                args=list(args or []),
            )
        except Exception as replay_exc:  # noqa: BLE001
            raise typer.BadParameter(f"{exc}; local replay fallback failed: {replay_exc}") from replay_exc

        replay_hex = "0x" + replay_raw.hex() if isinstance(replay_raw, (bytes, bytearray)) else replay_raw
        return "sdk_local_replay", replay_hex, replay_raw, replay_decoded, replay_meta


def _method_hint(abi_obj: Optional[Any], method: str) -> str:
    if abi_obj is None:
        return method
    mutability = (abi_utils.state_mutability(abi_obj, method) or "").strip().lower()
    if mutability:
        return f"{method} ({mutability})"
    return method


def _load_manifest_from_path(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _default_manifest_for_contract(
    *,
    name: str,
    abi_obj: Optional[Any],
    source_path: Optional[Path],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "manifestVersion": 1,
        "language": "python-vm",
    }
    if source_path is not None:
        manifest["source"] = source_path.name
        manifest["entry"] = source_path.name
    if isinstance(abi_obj, (dict, list)):
        manifest["abi"] = abi_obj
    return manifest


def _coerce_abi_for_inspection(abi_obj: Optional[Any]) -> Optional[Any]:
    if abi_obj is None:
        return None
    try:
        return abi_utils.coerce_abi(abi_obj)
    except Exception:
        return abi_obj


def _extract_callable_functions(abi_obj: Optional[Any]) -> list[str]:
    if abi_obj is None:
        return []
    coerced = _coerce_abi_for_inspection(abi_obj)
    names: set[str] = set()
    try:
        normalized = abi_utils.normalize_abi(coerced)
        fn_map = normalized.get("functions", {})
        if isinstance(fn_map, dict):
            for key in fn_map.keys():
                if isinstance(key, str) and key.strip():
                    names.add(key.strip())
    except Exception:
        pass

    if isinstance(coerced, dict):
        funcs = coerced.get("functions")
        if isinstance(funcs, list):
            for item in funcs:
                if isinstance(item, dict):
                    name = item.get("name")
                    if isinstance(name, str) and name.strip():
                        names.add(name.strip())
        entries = coerced.get("entries")
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").strip().lower() != "function":
                    continue
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    names.add(name.strip())
    elif isinstance(coerced, list):
        for item in coerced:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "function":
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    return sorted(names)


def _discover_source_callable_functions(source_path: Path) -> list[str]:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except Exception:
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            name = str(node.name or "").strip()
            if not name or name.startswith("_"):
                continue
            names.append(name)
    return sorted(set(names))


def _tx_status_is_success(status: Optional[str]) -> Optional[bool]:
    if status is None:
        return None
    text = status.strip().lower()
    if not text:
        return None
    if text in {"success", "succeeded", "ok", "applied", "included", "1", "true"}:
        return True
    if text in {"failed", "failure", "error", "revert", "reverted", "0", "false"}:
        return False
    return None


def _parse_constructor_args(
    *,
    args_json: Optional[str],
    constructor_args_json: Optional[str],
    abi_obj: Optional[Any],
) -> tuple[list[Any], Optional[str], Optional[bytes]]:
    if args_json and constructor_args_json and args_json.strip() != constructor_args_json.strip():
        raise typer.BadParameter("--args and --constructor-args conflict; pass one or matching values")

    raw = constructor_args_json if constructor_args_json is not None else args_json
    if raw is None:
        return [], None, None

    args_obj = abi_utils.parse_args_json(raw, option_name="--constructor-args")
    if abi_obj is None:
        raise typer.BadParameter("constructor args were provided but no ABI is available; pass --abi")

    constructor_method = abi_utils.pick_constructor_method(abi_obj)
    if constructor_method is None:
        raise typer.BadParameter(
            "constructor args were provided, but ABI has no constructor-like method "
            "(expected one of: constructor, __init__, init)"
        )

    args_list = abi_utils.to_positional_args(abi_obj, constructor_method, args_obj)
    _, calldata = abi_utils.encode_constructor_args(abi_obj, args_list)
    return args_list, constructor_method, calldata


@app.command("compile")
def contract_compile(
    source: Path = typer.Argument(..., help="Contract source file path."),
    out: Optional[Path] = typer.Option(None, "--out", help="Compiled artifact output path."),
    abi_out: Optional[Path] = typer.Option(None, "--abi-out", help="Optional ABI JSON output path."),
    manifest_out: Optional[Path] = typer.Option(
        None,
        "--manifest-out",
        help="Optional manifest/metadata JSON output path.",
    ),
    optimize: bool = typer.Option(True, "--optimize/--no-optimize", help="Enable optimizer hint."),
    format_: str = typer.Option("auto", "--format", help="Output format: auto|json|text."),
    project_root: Optional[Path] = typer.Option(None, "--project-root", help="Optional project root."),
    entrypoint: Optional[str] = typer.Option(None, "--entrypoint", help="Optional contract entrypoint name."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing output files."),
) -> None:
    src = source.expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise typer.BadParameter(f"source file not found: {source}")

    ir_bytes, compiler_meta = artifact_store.compile_source_to_ir_bytes(
        src,
        entrypoint=entrypoint,
        optimize=optimize,
    )
    code_hash = artifact_store.sha3_256_hex(ir_bytes)

    manifest_src_path = artifact_store.resolve_manifest_for_source(src)
    contract_name = artifact_store.detect_contract_name(src, manifest_src_path)
    artifact_path = out.expanduser().resolve() if out is not None else artifact_store.default_artifact_path(contract_name, code_hash)
    artifact_path = artifact_store.write_artifact_bytes(artifact_path, ir_bytes, overwrite=overwrite)

    manifest_src_obj = _load_manifest_from_path(manifest_src_path)
    abi_obj = artifact_store.load_abi_from_manifest(manifest_src_path)
    if abi_obj is None:
        abi_obj = artifact_store.generate_abi_from_source(src)

    abi_path: Optional[Path] = None
    if abi_out is not None:
        if abi_obj is None:
            raise typer.BadParameter("--abi-out was provided but ABI could not be inferred from source/manifest")
        abi_path = artifact_store.write_json_file(
            abi_out.expanduser().resolve(), abi_obj, overwrite=overwrite
        )
    elif abi_obj is not None:
        abi_path = artifact_store.write_json_file(
            artifact_store.default_abi_path(contract_name, code_hash),
            abi_obj,
            overwrite=overwrite,
        )

    manifest_written_path: Optional[Path] = None
    manifest_for_record = manifest_src_path
    if manifest_out is not None:
        payload = manifest_src_obj or _default_manifest_for_contract(
            name=contract_name,
            abi_obj=abi_obj,
            source_path=src,
        )
        payload.setdefault("compiler", {})
        if isinstance(payload.get("compiler"), dict):
            payload["compiler"] = {
                **payload.get("compiler", {}),
                "compile_command": "animica contract compile",
                "optimize": bool(optimize),
                "entrypoint": entrypoint,
            }
        manifest_written_path = artifact_store.write_json_file(
            manifest_out.expanduser().resolve(),
            payload,
            overwrite=overwrite,
        )
        manifest_for_record = manifest_written_path

    artifact_record = artifact_store.infer_artifact_metadata(
        artifact_path=artifact_path,
        source_path=src,
        code_hash=code_hash,
        contract_name=contract_name,
        abi_path=abi_path,
        manifest_path=manifest_for_record,
        compiler_meta=compiler_meta,
        optimize=optimize,
        project_root=project_root,
        entrypoint=entrypoint,
    )
    artifact_store.save_artifact_record(artifact_record)

    payload = {
        "status": "ok",
        "contract_name": contract_name,
        "source_path": str(src),
        "artifact_path": str(artifact_path),
        "abi_path": str(abi_path) if abi_path else None,
        "manifest_path": str(manifest_written_path or manifest_src_path) if (manifest_written_path or manifest_src_path) else None,
        "code_hash": code_hash,
        "compiler_meta": compiler_meta,
        "optimize": bool(optimize),
        "entrypoint": entrypoint,
    }

    render_json = format_.strip().lower() == "json"
    if format_.strip().lower() not in {"auto", "json", "text"}:
        raise typer.BadParameter("--format must be one of: auto, json, text")

    _text_or_json(
        payload=payload,
        json_output=render_json,
        lines=[
            "Compile succeeded",
            f"Contract:     {contract_name}",
            f"Artifact:     {artifact_path}",
            f"Code hash:    {code_hash}",
            f"ABI:          {abi_path or 'not produced'}",
            f"Manifest:     {manifest_written_path or manifest_src_path or 'not produced'}",
        ],
    )


@app.command("deploy")
def contract_deploy(
    artifact_or_source: str = typer.Argument(..., help="Path to source (.py) or compiled artifact."),
    from_: Optional[str] = typer.Option(None, "--from", help="Wallet label or sender address."),
    label: Optional[str] = typer.Option(None, "--label", help="Wallet label convenience alias."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL."),
    gas_limit: Optional[int] = typer.Option(None, "--gas-limit", help="Gas limit override."),
    gas_price: int = typer.Option(1, "--gas-price", help="Gas price / max fee."),
    value: int = typer.Option(0, "--value", help="Value to attach (base units)."),
    args_json: Optional[str] = typer.Option(None, "--args", help="Constructor args JSON array/object."),
    constructor_args_json: Optional[str] = typer.Option(
        None,
        "--constructor-args",
        help="Constructor args JSON array/object.",
    ),
    abi: Optional[Path] = typer.Option(None, "--abi", help="Path to ABI JSON."),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Optional nonce override."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for receipt confirmation."),
    wait_timeout_secs: int = typer.Option(
        60,
        "--wait-timeout-secs",
        help="Maximum seconds to wait for confirmation when --wait is enabled.",
    ),
    poll_interval_secs: float = typer.Option(
        1.0,
        "--poll-interval-secs",
        help="Polling interval in seconds while waiting for confirmation.",
    ),
    save: bool = typer.Option(False, "--save", help="Persist deployment metadata locally."),
    name: Optional[str] = typer.Option(None, "--name", help="Saved deployment alias."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    try:
        from omni_sdk.contracts.deployer import build_deploy_tx, make_package_bytes
        from omni_sdk.rpc.http import RpcClient
        from omni_sdk.tx import send as tx_send
        from omni_sdk.tx.signing import resolve_signing_context, sign_transaction_for_submission
    except Exception as exc:  # pragma: no cover - dependency gate
        raise typer.BadParameter(f"contract deploy dependencies unavailable: {exc}") from exc

    rpc_url = resolve_rpc_url(rpc)
    rpc_adapter = RpcAdapter(rpc_url)
    chain_id = resolve_chain_id(rpc_adapter, None)
    network_name = resolve_network_hint()
    deployment_key = deployment_store.network_key(chain_id=chain_id, network=network_name)

    if wait_timeout_secs <= 0:
        raise typer.BadParameter("--wait-timeout-secs must be greater than zero")
    if poll_interval_secs <= 0:
        raise typer.BadParameter("--poll-interval-secs must be greater than zero")

    signer_resolution = wallet_utils.resolve_signer(
        from_value=from_,
        label=label,
        wallet_file=None,
        alg_override=None,
    )
    signer = signer_resolution.signer
    sender = signer_resolution.sender

    source_path: Optional[Path] = None
    artifact_path: Optional[Path] = None
    artifact_meta: Optional[dict[str, Any]] = None
    manifest_path: Optional[Path] = None
    manifest_obj: Optional[dict[str, Any]] = None
    abi_obj: Optional[Any] = None
    abi_path: Optional[Path] = None

    input_path = Path(artifact_or_source).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        raise typer.BadParameter(f"artifact or source file not found: {artifact_or_source}")

    if input_path.suffix.lower() == ".py":
        source_path = input_path
        try:
            ir_bytes, _compiler_meta = artifact_store.compile_source_to_ir_bytes(source_path)
        except Exception as exc:  # noqa: BLE001
            raise typer.BadParameter(f"contract compile failed before deployment: {exc}") from exc
        code_hash = artifact_store.sha3_256_hex(ir_bytes)
        manifest_path = artifact_store.resolve_manifest_for_source(source_path)
        contract_name = artifact_store.detect_contract_name(source_path, manifest_path)
        manifest_obj = _load_manifest_from_path(manifest_path)
        if manifest_obj is None:
            manifest_obj = _default_manifest_for_contract(
                name=contract_name,
                abi_obj=None,
                source_path=source_path,
            )
        artifact_bytes = ir_bytes
    else:
        artifact_path = input_path
        artifact_bytes, artifact_meta = artifact_store.load_artifact_file(artifact_path)
        code_hash = artifact_store.sha3_256_hex(artifact_bytes)
        contract_name = artifact_path.stem

        manifest_path = artifact_store.discover_sidecar_manifest(artifact_path)
        if manifest_path is not None:
            manifest_obj = _load_manifest_from_path(manifest_path)
        if manifest_obj is None and isinstance(artifact_meta, dict):
            embedded_manifest = artifact_meta.get("manifest")
            if isinstance(embedded_manifest, dict):
                manifest_obj = embedded_manifest
        if manifest_obj is None:
            manifest_obj = _default_manifest_for_contract(
                name=contract_name,
                abi_obj=None,
                source_path=None,
            )

    discovered_abi_path: Optional[Path] = None
    if artifact_path is not None:
        discovered_abi_path = artifact_store.discover_sidecar_abi(artifact_path)
    if discovered_abi_path is None and source_path is not None and manifest_path is not None:
        manifest_abi = artifact_store.load_abi_from_manifest(manifest_path)
        if isinstance(manifest_abi, (dict, list)):
            abi_obj = manifest_abi

    if abi_obj is None:
        deployment_for_abi = artifact_store.find_artifact_by_path(artifact_path) if artifact_path else None
        abi_obj, abi_path = _load_abi_and_path(
            abi_path_opt=(abi if abi is not None else discovered_abi_path),
            deployment_record=deployment_for_abi,
            manifest_obj=manifest_obj,
        )
    else:
        abi_obj, abi_path = _load_abi_and_path(
            abi_path_opt=abi,
            deployment_record=None,
            manifest_obj=manifest_obj,
        )

    if source_path is not None:
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except Exception:
            source_text = None
        if isinstance(source_text, str) and source_text.strip():
            # Ensure deployments from source carry executable inline source metadata
            # so node-side simulation/call can run from chain state alone.
            manifest_obj["source"] = source_text
            manifest_obj.setdefault("source_path", source_path.name)

    constructor_args, constructor_method, constructor_calldata = _parse_constructor_args(
        args_json=args_json,
        constructor_args_json=constructor_args_json,
        abi_obj=abi_obj,
    )
    if constructor_calldata is not None:
        deploy_block = manifest_obj.setdefault("deploy", {})
        if isinstance(deploy_block, dict):
            deploy_block["constructorMethod"] = constructor_method
            deploy_block["constructorArgs"] = constructor_args
            deploy_block["constructorCalldata"] = "0x" + constructor_calldata.hex()

    try:
        package_bytes = make_package_bytes(manifest=manifest_obj, code=artifact_bytes)
        if nonce is None:
            try:
                head = rpc_adapter.request("chain.getHead", [])
                head_height = _first_int(head if isinstance(head, dict) else {}, "height", "number", "headHeight")
            except Exception:
                head_height = None
            valid_after = int(head_height or 0)
            valid_until = int(valid_after + 120)
            salt = os.urandom(16)
            tx_obj = build_deploy_tx(
                from_addr=sender,
                chain_id=chain_id,
                max_fee=int(gas_price),
                package_bytes=package_bytes,
                manifest_obj=manifest_obj,
                code_bytes=artifact_bytes,
                gas_limit=gas_limit,
                valid_after=valid_after,
                valid_until=valid_until,
                salt=salt,
            )
        else:
            tx_obj = build_deploy_tx(
                from_addr=sender,
                chain_id=chain_id,
                nonce=int(nonce),
                max_fee=int(gas_price),
                package_bytes=package_bytes,
                manifest_obj=manifest_obj,
                code_bytes=artifact_bytes,
                gas_limit=gas_limit,
            )

        if int(value) != 0:
            tx_obj["value"] = int(value)

        rpc_client = RpcClient(rpc_url, timeout=30.0)
        context = resolve_signing_context(rpc_client, chain_id=int(chain_id))
        signed = sign_transaction_for_submission(tx_obj, signer, context=context)
        tx_hash = tx_send.submit_raw(rpc_client, signed.raw_tx)
    except typer.BadParameter:
        raise
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"contract deploy failed: {exc}") from exc

    if not json_output:
        typer.echo("Deploy submitted")
        typer.echo(f"Submitted tx hash: {tx_hash}")
        if wait:
            typer.echo(
                "Waiting for confirmation "
                f"(timeout={wait_timeout_secs}s, poll={poll_interval_secs:.2f}s)..."
            )

    receipt: Optional[dict[str, Any]] = None
    tx_status: Optional[str] = None
    confirmed = False
    success: Optional[bool] = None
    timed_out = False
    block_height: Optional[int] = None

    if wait:
        polling_rpc_timeout = max(1.0, min(5.0, float(wait_timeout_secs)))
        poll_result = _poll_transaction_confirmation(
            RpcAdapter(rpc_url, timeout=polling_rpc_timeout),
            tx_hash,
            wait_timeout_secs=float(wait_timeout_secs),
            poll_interval_secs=float(poll_interval_secs),
        )
        receipt = poll_result.get("receipt")
        tx_status = poll_result.get("tx_status")
        block_height = poll_result.get("block_height")
        confirmed = bool(poll_result.get("confirmed"))
        success = poll_result.get("success")
        timed_out = bool(poll_result.get("timed_out"))

    contract_address = _extract_contract_address(receipt)
    if contract_address is None:
        contract_address = _derive_contract_address(
            tx_obj,
            tx_hash=tx_hash,
            sender=sender,
            chain_id=chain_id,
            receipt=receipt,
        )

    block_height = block_height or _first_int(receipt or {}, "blockNumber", "block_height", "height")
    gas_used = _first_int(receipt or {}, "gasUsed", "gas_used")
    tx_status = tx_status or _first_str(receipt or {}, "status", "tx_status")
    if tx_status is None:
        status_view = get_tx_status(rpc_adapter, tx_hash)
        if isinstance(status_view, dict):
            tx_status = _first_str(status_view, "status", "state")
            if block_height is None:
                block_height = _first_int(status_view, "included_height", "height")
    if success is None:
        success = _tx_status_is_success(tx_status)

    persisted_artifact_path: Optional[Path] = artifact_path
    persisted_abi_path: Optional[Path] = abi_path
    persisted_manifest_path: Optional[Path] = manifest_path
    deployment_name = (name or contract_name).strip() or contract_name
    saved = False
    save_warning: Optional[str] = None
    fatal_save_error: Optional[str] = None

    if save:
        if not wait:
            save_warning = (
                "--save was requested with --no-wait, so deployment was not saved yet. "
                f"Confirm first with `animica contract receipt {tx_hash} --wait --wait-timeout-secs {wait_timeout_secs}` "
                "then save with "
                f"`animica contract save-deployment --name {deployment_name} --address <contract_address> --tx-hash {tx_hash}`."
            )
        elif not confirmed:
            if contract_address:
                save_warning = (
                    "--save was requested, but deployment was not confirmed before timeout; "
                    "save was skipped pending confirmation."
                )
            else:
                save_warning = (
                    "--save was requested, but deployment was not confirmed before timeout and "
                    "contract address is still unknown; save was skipped pending confirmation."
                )
        elif success is False:
            save_warning = (
                f"--save was requested, but deployment status is {tx_status or 'FAILED'}; "
                "save was skipped."
            )
        else:
            try:
                if not contract_address:
                    raise RuntimeError("contract address could not be resolved from receipt/metadata")

                if persisted_artifact_path is None:
                    persisted_artifact_path = artifact_store.default_artifact_path(contract_name, code_hash)
                    if not persisted_artifact_path.exists():
                        artifact_store.write_artifact_bytes(
                            persisted_artifact_path,
                            artifact_bytes,
                            overwrite=False,
                        )

                if persisted_abi_path is None and abi_obj is not None:
                    persisted_abi_path = artifact_store.default_abi_path(contract_name, code_hash)
                    if not persisted_abi_path.exists():
                        artifact_store.write_json_file(persisted_abi_path, abi_obj, overwrite=False)

                if persisted_manifest_path is None:
                    persisted_manifest_path = artifact_store.default_manifest_path(contract_name, code_hash)
                    if not persisted_manifest_path.exists():
                        artifact_store.write_json_file(persisted_manifest_path, manifest_obj, overwrite=False)

                artifact_store.save_artifact_record(
                    artifact_store.infer_artifact_metadata(
                        artifact_path=persisted_artifact_path,
                        source_path=source_path,
                        code_hash=code_hash,
                        contract_name=contract_name,
                        abi_path=persisted_abi_path,
                        manifest_path=persisted_manifest_path,
                        compiler_meta={"compiled_during": "deploy"} if source_path is not None else {},
                        optimize=True,
                    )
                )

                deployment_store.save_deployment_record(
                    {
                        "name": deployment_name,
                        "address": contract_address,
                        "chain_id": chain_id,
                        "network": network_name,
                        "tx_hash": tx_hash,
                        "block_height": block_height,
                        "deployer": sender,
                        "abi_path": str(persisted_abi_path) if persisted_abi_path else None,
                        "artifact_path": str(persisted_artifact_path) if persisted_artifact_path else None,
                        "manifest_path": str(persisted_manifest_path) if persisted_manifest_path else None,
                        "code_hash": code_hash,
                        "rpc_url": rpc_url,
                        "created_at": _utc_now_iso(),
                        "constructor_args": constructor_args,
                        "constructor_method": constructor_method,
                        "constructor_calldata": ("0x" + constructor_calldata.hex()) if constructor_calldata else None,
                    },
                    key=deployment_key,
                )
                saved = True
            except Exception as exc:  # noqa: BLE001
                fatal_save_error = str(exc)

    if save and fatal_save_error:
        raise typer.BadParameter(
            f"deployment submitted (tx_hash={tx_hash}) but save failed: {fatal_save_error}"
        )

    if wait:
        if confirmed:
            if success is True:
                confirmation_state = "confirmed (success)"
                message = "deployment confirmed successfully"
            elif success is False:
                confirmation_state = f"confirmed (failure: {tx_status or 'FAILED'})"
                message = f"deployment confirmed with failure status: {tx_status or 'FAILED'}"
            else:
                confirmation_state = "confirmed"
                message = "deployment confirmed"
        else:
            confirmation_state = f"not confirmed before timeout ({wait_timeout_secs}s)"
            message = "deployment submitted but not confirmed before timeout"
    else:
        confirmation_state = "not requested (--no-wait)"
        message = "deployment submitted without waiting for confirmation"

    if save and saved:
        message += f"; deployment alias '{deployment_name}' saved"
    elif save and save_warning:
        message += f"; {save_warning}"

    payload = {
        "status": "ok",
        "rpc_url": rpc_url,
        "chain_id": chain_id,
        "network": network_name,
        "deployment_key": deployment_key,
        "submitted": True,
        "confirmed": bool(confirmed),
        "success": success,
        "tx_hash": tx_hash,
        "contract_address": contract_address,
        "deployment_name": deployment_name,
        "block_height": block_height,
        "gas_used": gas_used,
        "tx_status": tx_status,
        "waited": bool(wait),
        "wait_timeout_secs": int(wait_timeout_secs),
        "poll_interval_secs": float(poll_interval_secs),
        "timed_out": bool(timed_out),
        "wait_error": "timeout" if timed_out else None,
        "save_requested": bool(save),
        "saved": bool(saved),
        "save_warning": save_warning,
        "name": deployment_name,
        "deployer": sender,
        "artifact_path": str(persisted_artifact_path) if persisted_artifact_path else None,
        "abi_path": str(persisted_abi_path) if persisted_abi_path else None,
        "manifest_path": str(persisted_manifest_path) if persisted_manifest_path else None,
        "code_hash": code_hash,
        "constructor_method": constructor_method,
        "constructor_args": constructor_args,
        "constructor_calldata": ("0x" + constructor_calldata.hex()) if constructor_calldata else None,
        "receipt": receipt,
        "message": message,
    }

    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[
            "Deployment result",
            f"Submitted tx hash: {tx_hash}",
            f"Confirmation:      {confirmation_state}",
            f"Contract address:  {contract_address or 'pending'}",
            f"Alias saved:       {'yes' if saved else 'no'}",
            f"Alias name:        {deployment_name}",
            f"Message:           {message}",
        ],
    )


@app.command("receipt")
def contract_receipt(
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for confirmation."),
    wait_timeout_secs: int = typer.Option(
        60,
        "--wait-timeout-secs",
        help="Maximum seconds to wait for confirmation when --wait is enabled.",
    ),
    poll_interval_secs: float = typer.Option(
        1.0,
        "--poll-interval-secs",
        help="Polling interval in seconds while waiting for confirmation.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    tx_hash_value = tx_hash.strip()
    if not _is_hex_tx_hash(tx_hash_value):
        raise typer.BadParameter(f"invalid tx hash format: {tx_hash}")
    if wait_timeout_secs <= 0:
        raise typer.BadParameter("--wait-timeout-secs must be greater than zero")
    if poll_interval_secs <= 0:
        raise typer.BadParameter("--poll-interval-secs must be greater than zero")

    rpc_url = resolve_rpc_url(rpc)
    rpc_adapter = RpcAdapter(rpc_url)

    receipt: Optional[dict[str, Any]] = None
    tx_status: Optional[str] = None
    block_height: Optional[int] = None
    success: Optional[bool] = None
    confirmed = False
    timed_out = False

    if wait:
        poll_result = _poll_transaction_confirmation(
            RpcAdapter(rpc_url, timeout=max(1.0, min(5.0, float(wait_timeout_secs)))),
            tx_hash_value,
            wait_timeout_secs=float(wait_timeout_secs),
            poll_interval_secs=float(poll_interval_secs),
        )
        receipt = poll_result.get("receipt")
        tx_status = poll_result.get("tx_status")
        block_height = poll_result.get("block_height")
        success = poll_result.get("success")
        confirmed = bool(poll_result.get("confirmed"))
        timed_out = bool(poll_result.get("timed_out"))
    else:
        receipt = _fetch_receipt_once(rpc_adapter, tx_hash_value)
        tx_status = _first_str(receipt or {}, "status", "tx_status")
        block_height = _first_int(receipt or {}, "blockNumber", "block_height", "height")
        if tx_status is None:
            status_view = get_tx_status(rpc_adapter, tx_hash_value)
            if isinstance(status_view, dict):
                tx_status = _first_str(status_view, "status", "state", "tx_status")
                if block_height is None:
                    block_height = _first_int(status_view, "included_height", "blockNumber", "block_height", "height")
        success = _tx_status_is_success(tx_status)
        confirmed = bool(receipt) or (success is not None)

    gas_used = _first_int(receipt or {}, "gasUsed", "gas_used")
    contract_address = _extract_contract_address(receipt)

    if confirmed:
        if success is True:
            message = "transaction confirmed successfully"
            confirmation_state = "confirmed (success)"
        elif success is False:
            message = f"transaction confirmed with failure status: {tx_status or 'FAILED'}"
            confirmation_state = f"confirmed (failure: {tx_status or 'FAILED'})"
        else:
            message = "transaction appears confirmed, but outcome status is unknown"
            confirmation_state = "confirmed (unknown outcome)"
    else:
        if wait and timed_out:
            message = "transaction not confirmed before timeout"
            confirmation_state = f"not confirmed before timeout ({wait_timeout_secs}s)"
        else:
            message = "transaction not confirmed yet"
            confirmation_state = "not confirmed"

    payload = {
        "status": "ok",
        "rpc_url": rpc_url,
        "tx_hash": tx_hash_value,
        "submitted": True,
        "confirmed": bool(confirmed),
        "success": success,
        "tx_status": tx_status,
        "contract_address": contract_address,
        "block_height": block_height,
        "gas_used": gas_used,
        "waited": bool(wait),
        "wait_timeout_secs": int(wait_timeout_secs),
        "poll_interval_secs": float(poll_interval_secs),
        "timed_out": bool(timed_out),
        "receipt": receipt,
        "message": message,
    }

    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[
            "Transaction receipt status",
            f"Tx hash:           {tx_hash_value}",
            f"Confirmation:      {confirmation_state}",
            f"Contract address:  {contract_address or 'pending'}",
            f"Block height:      {block_height if block_height is not None else 'pending'}",
            f"Status:            {tx_status or 'unknown'}",
            f"Message:           {message}",
        ],
    )


@app.command("save-deployment")
def contract_save_deployment(
    name: str = typer.Option(..., "--name", help="Deployment alias to save."),
    address: str = typer.Option(..., "--address", help="Deployed contract address."),
    abi: Optional[Path] = typer.Option(None, "--abi", help="Optional ABI JSON path."),
    tx_hash: Optional[str] = typer.Option(None, "--tx-hash", help="Optional deployment transaction hash."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL for deployment scoping."),
    network: Optional[str] = typer.Option(None, "--network", help="Explicit deployment network label."),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", help="Explicit chain ID for deployment scoping."),
    artifact_path: Optional[Path] = typer.Option(None, "--artifact-path", help="Optional artifact path."),
    manifest_path: Optional[Path] = typer.Option(None, "--manifest-path", help="Optional manifest path."),
    code_hash: Optional[str] = typer.Option(None, "--code-hash", help="Optional contract code hash."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    deployment_name = name.strip()
    if not deployment_name:
        raise typer.BadParameter("--name cannot be empty")

    contract_address = address.strip()
    if not _is_address_like(contract_address):
        raise typer.BadParameter(f"invalid contract address: {address}")

    tx_hash_value: Optional[str] = None
    if tx_hash is not None and tx_hash.strip():
        tx_hash_value = tx_hash.strip()
        if not _is_hex_tx_hash(tx_hash_value):
            raise typer.BadParameter(f"invalid tx hash format: {tx_hash}")

    abi_path: Optional[Path] = None
    if abi is not None:
        abi_path = abi.expanduser().resolve()
        _ = abi_utils.load_abi_document(abi_path)

    artifact_resolved: Optional[Path] = None
    if artifact_path is not None:
        artifact_resolved = artifact_path.expanduser().resolve()
        if not artifact_resolved.exists() or not artifact_resolved.is_file():
            raise typer.BadParameter(f"artifact file not found: {artifact_path}")

    manifest_resolved: Optional[Path] = None
    if manifest_path is not None:
        manifest_resolved = manifest_path.expanduser().resolve()
        if not manifest_resolved.exists() or not manifest_resolved.is_file():
            raise typer.BadParameter(f"manifest file not found: {manifest_path}")
        _ = _load_json(manifest_resolved, label="manifest")

    deployment_key, resolved_chain_id, resolved_network, rpc_url = _resolve_deployment_context(
        rpc=rpc,
        network_override=network,
        chain_id_override=chain_id,
    )

    deployment_store.save_deployment_record(
        {
            "name": deployment_name,
            "address": contract_address,
            "chain_id": resolved_chain_id,
            "network": resolved_network,
            "tx_hash": tx_hash_value,
            "abi_path": str(abi_path) if abi_path else None,
            "artifact_path": str(artifact_resolved) if artifact_resolved else None,
            "manifest_path": str(manifest_resolved) if manifest_resolved else None,
            "code_hash": code_hash,
            "rpc_url": rpc_url,
            "created_at": _utc_now_iso(),
        },
        key=deployment_key,
    )

    payload = {
        "status": "ok",
        "saved": True,
        "deployment_name": deployment_name,
        "address": contract_address,
        "tx_hash": tx_hash_value,
        "abi_path": str(abi_path) if abi_path else None,
        "artifact_path": str(artifact_resolved) if artifact_resolved else None,
        "manifest_path": str(manifest_resolved) if manifest_resolved else None,
        "code_hash": code_hash,
        "network": resolved_network,
        "chain_id": resolved_chain_id,
        "deployment_key": deployment_key,
        "rpc_url": rpc_url,
        "message": f"saved deployment alias '{deployment_name}'",
    }

    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[
            "Deployment alias saved",
            f"Alias:            {deployment_name}",
            f"Address:          {contract_address}",
            f"Deployment key:   {deployment_key}",
            f"Tx hash:          {tx_hash_value or 'not provided'}",
            f"ABI path:         {abi_path or 'not provided'}",
        ],
    )


@app.command("call")
def contract_call(
    contract: str = typer.Argument(..., help="Saved deployment name or contract address."),
    method: str = typer.Argument(..., help="ABI method name (or calldata hex with --raw and no ABI)."),
    args_json: Optional[str] = typer.Option(None, "--args", help="Method args JSON array/object."),
    abi: Optional[Path] = typer.Option(None, "--abi", help="Path to ABI JSON."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL."),
    from_: Optional[str] = typer.Option(None, "--from", help="Optional sender context (label or address)."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    raw: bool = typer.Option(False, "--raw", help="Treat method argument as raw calldata hex when ABI is missing."),
) -> None:
    rpc_url = resolve_rpc_url(rpc)
    lookup_key, chain_id, _network_name = _deployment_lookup_key(rpc_url)
    contract_address, deployment_record = _resolve_contract_identifier(contract, preferred_key=lookup_key)
    manifest_obj = None
    if isinstance(deployment_record, dict):
        raw_manifest_path = deployment_record.get("manifest_path")
        if isinstance(raw_manifest_path, str) and raw_manifest_path.strip():
            manifest_obj = _load_manifest_from_path(Path(raw_manifest_path))

    abi_obj, abi_path = _load_abi_and_path(
        abi_path_opt=abi,
        deployment_record=deployment_record,
        manifest_obj=manifest_obj,
    )

    sender = _resolve_sender_hint(from_)
    rpc_adapter = RpcAdapter(rpc_url)

    args_list: list[Any] = []
    replay_decoded: Optional[Any] = None
    replay_meta: Optional[dict[str, Any]] = None
    if abi_obj is None:
        if not raw:
            raise typer.BadParameter(
                "ABI is required to encode method calls. Pass --abi, use a saved deployment with abi_path, or use --raw."
            )
        calldata = abi_utils.parse_hex_bytes(method, field="method/calldata")
        method_name = "<raw>"
    else:
        method_name = method
        parsed_args = abi_utils.parse_args_json(args_json, option_name="--args")
        args_list = abi_utils.to_positional_args(abi_obj, method_name, parsed_args)
        calldata = abi_utils.encode_calldata(abi_obj, method_name, args_list)

    rpc_method, raw_result, raw_bytes, replay_decoded, replay_meta = _simulate_call_with_fallback(
        rpc_url=rpc_url,
        rpc=rpc_adapter,
        contract_address=contract_address,
        calldata=calldata,
        sender=sender,
        abi_path=abi_path,
        abi_obj=abi_obj,
        method=(None if abi_obj is None else method_name),
        args=args_list,
    )

    decoded = replay_decoded
    if decoded is None and abi_obj is not None and not raw and raw_bytes is not None:
        try:
            decoded = abi_utils.decode_method_result(abi_obj, method_name, raw_bytes)
        except Exception:
            decoded = None

    payload = {
        "status": "ok",
        "rpc_url": rpc_url,
        "chain_id": chain_id,
        "contract": contract,
        "address": contract_address,
        "method": method_name,
        "args": args_list,
        "from": sender,
        "calldata": "0x" + calldata.hex(),
        "rpc_method": rpc_method,
        "raw_result": raw_result,
        "raw_result_hex": ("0x" + raw_bytes.hex()) if raw_bytes is not None else None,
        "decoded_result": decoded,
        "replay": replay_meta,
    }

    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[
            f"Call result for {contract_address}",
            f"Method:   {_method_hint(abi_obj, method_name)}",
            f"RPC:      {rpc_method}",
            f"Calldata: 0x{calldata.hex()}",
            f"Decoded:  {decoded!r}" if decoded is not None else f"Raw:      {raw_result!r}",
        ],
    )


@app.command("send")
def contract_send(
    contract: str = typer.Argument(..., help="Saved deployment name or contract address."),
    method: str = typer.Argument(..., help="ABI method name."),
    from_: Optional[str] = typer.Option(None, "--from", help="Wallet label or sender address."),
    label: Optional[str] = typer.Option(None, "--label", help="Wallet label convenience alias."),
    args_json: Optional[str] = typer.Option(None, "--args", help="Method args JSON array/object."),
    abi: Optional[Path] = typer.Option(None, "--abi", help="Path to ABI JSON."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL."),
    gas_limit: Optional[int] = typer.Option(None, "--gas-limit", help="Gas limit override."),
    gas_price: int = typer.Option(1, "--gas-price", help="Gas price / max fee."),
    value: int = typer.Option(0, "--value", help="Value to attach (base units)."),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Optional nonce override."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for confirmation receipt."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    try:
        from omni_sdk.rpc.http import RpcClient
        from omni_sdk.tx import build as tx_build
        from omni_sdk.tx import send as tx_send
        from omni_sdk.tx.signing import sign_transaction_with_rpc_context
    except Exception as exc:  # pragma: no cover - dependency gate
        raise typer.BadParameter(f"contract send dependencies unavailable: {exc}") from exc

    rpc_url = resolve_rpc_url(rpc)
    rpc_adapter = RpcAdapter(rpc_url)
    chain_id = resolve_chain_id(rpc_adapter, None)
    network_name = resolve_network_hint()
    deployment_key = deployment_store.network_key(chain_id=chain_id, network=network_name)

    contract_address, deployment_record = _resolve_contract_identifier(contract, preferred_key=deployment_key)

    manifest_obj = None
    if isinstance(deployment_record, dict):
        raw_manifest_path = deployment_record.get("manifest_path")
        if isinstance(raw_manifest_path, str) and raw_manifest_path.strip():
            manifest_obj = _load_manifest_from_path(Path(raw_manifest_path))

    abi_obj, _abi_path = _load_abi_and_path(
        abi_path_opt=abi,
        deployment_record=deployment_record,
        manifest_obj=manifest_obj,
    )
    if abi_obj is None:
        raise typer.BadParameter(
            "ABI is required for contract send. Pass --abi or deploy with --save so abi_path is recorded."
        )

    parsed_args = abi_utils.parse_args_json(args_json, option_name="--args")
    args_list = abi_utils.to_positional_args(abi_obj, method, parsed_args)
    calldata = abi_utils.encode_calldata(abi_obj, method, args_list)

    signer_resolution = wallet_utils.resolve_signer(
        from_value=from_,
        label=label,
        wallet_file=None,
        alg_override=None,
    )
    signer = signer_resolution.signer
    sender = signer_resolution.sender
    if getattr(signer, "address", None) in (None, ""):
        try:
            setattr(signer, "address", sender)
        except Exception:
            pass

    try:
        resolved_nonce = _resolve_nonce(rpc_adapter, sender, nonce)
        resolved_gas_limit = (
            int(gas_limit)
            if gas_limit is not None
            else tx_build.suggest_gas_limit("call", calldata_len=len(calldata))
        )
        tx_obj = tx_build.call(
            from_addr=sender,
            to_addr=contract_address,
            data=calldata,
            nonce=resolved_nonce,
            gas_limit=resolved_gas_limit,
            max_fee=int(gas_price),
            chain_id=chain_id,
            value=int(value),
        )

        rpc_client = RpcClient(rpc_url, timeout=30.0)
        signed = sign_transaction_with_rpc_context(
            tx_obj,
            signer,
            chain_id=int(chain_id),
            rpc=rpc_client,
        )
        tx_hash = tx_send.submit_raw(rpc_client, signed.raw_tx)
    except typer.BadParameter:
        raise
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"contract send failed: {exc}") from exc

    receipt: Optional[dict[str, Any]] = None
    wait_error: Optional[str] = None
    if wait:
        try:
            receipt = wait_for_receipt(rpc_adapter, tx_hash, timeout_s=120.0, poll_interval_s=0.5)
        except Exception as exc:  # noqa: BLE001
            wait_error = str(exc)

    tx_status = _first_str(receipt or {}, "status", "tx_status")
    block_height = _first_int(receipt or {}, "blockNumber", "block_height", "height")
    gas_used = _first_int(receipt or {}, "gasUsed", "gas_used")
    if tx_status is None:
        status_view = get_tx_status(rpc_adapter, tx_hash)
        if isinstance(status_view, dict):
            tx_status = _first_str(status_view, "status", "state")
            if block_height is None:
                block_height = _first_int(status_view, "included_height", "height")

    payload = {
        "status": "ok",
        "rpc_url": rpc_url,
        "chain_id": chain_id,
        "deployment_key": deployment_key,
        "contract": contract,
        "address": contract_address,
        "method": method,
        "args": args_list,
        "from": sender,
        "gas_limit": resolved_gas_limit,
        "gas_price": int(gas_price),
        "value": int(value),
        "nonce": resolved_nonce,
        "tx_hash": tx_hash,
        "tx_status": tx_status,
        "block_height": block_height,
        "gas_used": gas_used,
        "waited": bool(wait),
        "wait_error": wait_error,
        "receipt": receipt,
    }

    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[
            "Contract transaction submitted",
            f"Tx hash:      {tx_hash}",
            f"Contract:     {contract_address}",
            f"Method:       {_method_hint(abi_obj, method)}",
            f"Block height: {block_height if block_height is not None else 'pending'}",
            f"Gas used:     {gas_used if gas_used is not None else 'unknown'}",
            f"Status:       {tx_status or ('pending' if not wait else 'unknown')}",
        ],
    )


@app.command("inspect")
def contract_inspect(
    artifact_or_deployment: str = typer.Argument(..., help="Artifact path or saved deployment name/address."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL (for network-scoped lookup)."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    path = Path(artifact_or_deployment).expanduser().resolve()
    if path.exists() and path.is_file() and path.suffix.lower() == ".py":
        source_path = path
        manifest_path = artifact_store.resolve_manifest_for_source(source_path)
        manifest_obj = _load_manifest_from_path(manifest_path) if manifest_path else None

        abi_obj: Optional[Any] = None
        abi_path = artifact_store.infer_abi_path_for_source(source_path, manifest_path)
        if abi_path is not None:
            try:
                abi_obj = abi_utils.load_abi_document(abi_path)
            except Exception:
                abi_obj = None
        if abi_obj is None and manifest_path is not None:
            manifest_abi = artifact_store.load_abi_from_manifest(manifest_path)
            if isinstance(manifest_abi, (dict, list)):
                abi_obj = manifest_abi
        if abi_obj is None:
            generated = artifact_store.generate_abi_from_source(source_path)
            if isinstance(generated, (dict, list)):
                abi_obj = generated

        functions = _extract_callable_functions(abi_obj)
        if not functions:
            functions = _discover_source_callable_functions(source_path)

        compile_ready = False
        compile_error: Optional[str] = None
        compiler_meta: Optional[dict[str, Any]] = None
        code_hash: Optional[str] = None
        size_bytes: Optional[int] = None
        try:
            compiled_bytes, compiler_meta = artifact_store.compile_source_to_ir_bytes(source_path)
            compile_ready = True
            code_hash = artifact_store.sha3_256_hex(compiled_bytes)
            size_bytes = len(compiled_bytes)
        except Exception as exc:  # noqa: BLE001
            compile_error = str(exc)

        deployable = bool(compile_ready and functions)
        payload = {
            "status": "ok",
            "kind": "source",
            "path": str(source_path),
            "manifest_path": str(manifest_path) if manifest_path else None,
            "abi_path": str(abi_path) if abi_path else None,
            "contract_name": (
                (manifest_obj or {}).get("name")
                or artifact_store.detect_contract_name(source_path, manifest_path)
            ),
            "functions": functions,
            "compile_ready": compile_ready,
            "deployable": deployable,
            "compile_error": compile_error,
            "compiler_meta": compiler_meta,
            "compiled_code_hash": code_hash,
            "compiled_size_bytes": size_bytes,
        }
        lines = [
            "Source inspection",
            f"Path:         {source_path}",
            f"Manifest:     {manifest_path or 'not found'}",
            f"ABI:          {abi_path or 'inferred from manifest/source'}",
            f"Functions:    {', '.join(functions) if functions else 'none detected'}",
            f"Compile:      {'ready' if compile_ready else 'not ready'}",
            f"Deployable:   {'yes' if deployable else 'no'}",
        ]
        if compile_error:
            lines.append(f"Compile error: {compile_error}")

        _text_or_json(
            payload=payload,
            json_output=json_output,
            lines=lines,
        )
        return

    if path.exists() and path.is_file():
        artifact_bytes, artifact_meta = artifact_store.load_artifact_file(path)
        code_hash = artifact_store.sha3_256_hex(artifact_bytes)
        saved = artifact_store.find_artifact_by_path(path)
        sidecar_abi = artifact_store.discover_sidecar_abi(path)
        sidecar_manifest = artifact_store.discover_sidecar_manifest(path)
        manifest_obj = _load_manifest_from_path(sidecar_manifest) if sidecar_manifest else None
        abi_obj: Optional[Any] = None
        abi_path: Optional[Path] = sidecar_abi
        if abi_path is not None:
            abi_obj = abi_utils.load_abi_document(abi_path)
        else:
            if isinstance(saved, dict):
                saved_abi = saved.get("abi_path")
                if isinstance(saved_abi, str) and saved_abi.strip():
                    candidate = Path(saved_abi).expanduser().resolve()
                    if candidate.exists() and candidate.is_file():
                        abi_path = candidate
                        abi_obj = abi_utils.load_abi_document(candidate)
            if abi_obj is None and sidecar_manifest is not None:
                manifest_abi = artifact_store.load_abi_from_manifest(sidecar_manifest)
                if isinstance(manifest_abi, (dict, list)):
                    abi_obj = manifest_abi

        functions = _extract_callable_functions(abi_obj)
        if not functions and isinstance(saved, dict):
            raw_source = saved.get("source_path")
            if isinstance(raw_source, str) and raw_source.strip():
                functions = _discover_source_callable_functions(Path(raw_source))

        payload = {
            "status": "ok",
            "kind": "artifact",
            "path": str(path),
            "code_hash": code_hash,
            "size_bytes": len(artifact_bytes),
            "saved_record": saved,
            "artifact_meta": artifact_meta,
            "abi_path": str(abi_path) if abi_path else None,
            "manifest_path": str(sidecar_manifest) if sidecar_manifest else None,
            "contract_name": (
                (saved or {}).get("name")
                or (manifest_obj or {}).get("name")
                or path.stem
            ),
            "functions": functions,
        }
        _text_or_json(
            payload=payload,
            json_output=json_output,
            lines=[
                "Artifact inspection",
                f"Path:         {path}",
                f"Code hash:    {code_hash}",
                f"Size:         {len(artifact_bytes)} bytes",
                f"ABI:          {abi_path or 'not found'}",
                f"Manifest:     {sidecar_manifest or 'not found'}",
                f"Functions:    {', '.join(functions) if functions else 'none detected'}",
            ],
        )
        return

    rpc_url = resolve_rpc_url(rpc)
    lookup_key, _, _ = _deployment_lookup_key(rpc_url)
    address, record = _resolve_contract_identifier(artifact_or_deployment, preferred_key=lookup_key)
    functions: list[str] = []
    manifest_path: Optional[str] = None
    if isinstance(record, dict):
        raw_manifest_path = record.get("manifest_path")
        if isinstance(raw_manifest_path, str) and raw_manifest_path.strip():
            manifest_path = raw_manifest_path.strip()
        deployment_abi_obj, _deployment_abi_path = _load_abi_and_path(
            abi_path_opt=None,
            deployment_record=record,
            manifest_obj=_load_manifest_from_path(Path(manifest_path)) if manifest_path else None,
        )
        functions = _extract_callable_functions(deployment_abi_obj)

    payload = {
        "status": "ok",
        "kind": "deployment",
        "query": artifact_or_deployment,
        "address": address,
        "record": record,
        "functions": functions,
    }
    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[
            "Deployment inspection",
            f"Name:         {(record or {}).get('name') or artifact_or_deployment}",
            f"Address:      {address}",
            f"Tx hash:      {(record or {}).get('tx_hash') or 'unknown'}",
            f"Deployer:     {(record or {}).get('deployer') or 'unknown'}",
            f"Artifact:     {(record or {}).get('artifact_path') or 'unknown'}",
            f"ABI:          {(record or {}).get('abi_path') or 'unknown'}",
            f"Functions:    {', '.join(functions) if functions else 'none detected'}",
        ],
    )


@app.command("address")
def contract_address(
    name: str = typer.Argument(..., help="Saved deployment alias or address."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL (for network-scoped lookup)."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    if _is_address_like(name):
        payload = {"status": "ok", "name": name, "address": name, "record": None}
        _text_or_json(
            payload=payload,
            json_output=json_output,
            lines=[f"{name} -> {name}"],
        )
        return

    lookup_key = None
    if rpc is not None:
        rpc_url = resolve_rpc_url(rpc)
        lookup_key, _, _ = _deployment_lookup_key(rpc_url)
    record = deployment_store.resolve_deployment(name, key=lookup_key) or deployment_store.resolve_deployment(name, key=None)
    if not isinstance(record, dict):
        raise typer.BadParameter(f"deployment alias not found: {name}")
    address = str(record.get("address") or "").strip()
    if not address:
        raise typer.BadParameter(f"deployment alias exists but has no address: {name}")

    payload = {"status": "ok", "name": name, "address": address, "record": record}
    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[f"{name} -> {address}"],
    )


@app.command("estimate-gas")
def contract_estimate_gas(
    contract: str = typer.Argument(..., help="Saved deployment name or contract address."),
    method: str = typer.Argument(..., help="ABI method name."),
    args_json: Optional[str] = typer.Option(None, "--args", help="Method args JSON array/object."),
    abi: Optional[Path] = typer.Option(None, "--abi", help="Path to ABI JSON."),
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL."),
    from_: Optional[str] = typer.Option(None, "--from", help="Optional sender context (label or address)."),
    value: int = typer.Option(0, "--value", help="Value to attach (base units)."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    try:
        from omni_sdk.tx import build as tx_build
    except Exception as exc:  # pragma: no cover - dependency gate
        raise typer.BadParameter(f"gas estimation dependencies unavailable: {exc}") from exc

    rpc_url = resolve_rpc_url(rpc)
    lookup_key, chain_id, _network_name = _deployment_lookup_key(rpc_url)
    contract_address, deployment_record = _resolve_contract_identifier(contract, preferred_key=lookup_key)

    manifest_obj = None
    if isinstance(deployment_record, dict):
        raw_manifest_path = deployment_record.get("manifest_path")
        if isinstance(raw_manifest_path, str) and raw_manifest_path.strip():
            manifest_obj = _load_manifest_from_path(Path(raw_manifest_path))

    abi_obj, _abi_path = _load_abi_and_path(
        abi_path_opt=abi,
        deployment_record=deployment_record,
        manifest_obj=manifest_obj,
    )
    if abi_obj is None:
        raise typer.BadParameter("ABI is required for gas estimation; pass --abi or use saved deployment metadata")

    parsed_args = abi_utils.parse_args_json(args_json, option_name="--args")
    args_list = abi_utils.to_positional_args(abi_obj, method, parsed_args)
    calldata = abi_utils.encode_calldata(abi_obj, method, args_list)
    estimated = tx_build.suggest_gas_limit("call", calldata_len=len(calldata))

    sender = _resolve_sender_hint(from_)

    payload = {
        "status": "ok",
        "strategy": "local_intrinsic_plus_safety",
        "contract": contract,
        "address": contract_address,
        "method": method,
        "args": args_list,
        "from": sender,
        "value": int(value),
        "chain_id": chain_id,
        "calldata_size": len(calldata),
        "estimated_gas_limit": int(estimated),
    }
    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[
            f"Estimated gas limit: {estimated}",
            f"Method: {_method_hint(abi_obj, method)}",
            f"Calldata bytes: {len(calldata)}",
        ],
    )


@app.command("encode-calldata")
def contract_encode_calldata(
    method: str = typer.Argument(..., help="ABI method name."),
    args_json: Optional[str] = typer.Option(None, "--args", help="Method args JSON array/object."),
    abi: Path = typer.Option(..., "--abi", help="Path to ABI JSON."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    abi_obj = abi_utils.load_abi_document(abi.expanduser().resolve())
    parsed_args = abi_utils.parse_args_json(args_json, option_name="--args")
    args_list = abi_utils.to_positional_args(abi_obj, method, parsed_args)
    calldata = abi_utils.encode_calldata(abi_obj, method, args_list)
    payload = {
        "status": "ok",
        "method": method,
        "args": args_list,
        "calldata": "0x" + calldata.hex(),
    }
    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[f"0x{calldata.hex()}"],
    )


@app.command("decode-result")
def contract_decode_result(
    method: str = typer.Argument(..., help="ABI method name."),
    data: str = typer.Option(..., "--data", help="Raw result bytes as 0x-prefixed hex."),
    abi: Path = typer.Option(..., "--abi", help="Path to ABI JSON."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    abi_obj = abi_utils.load_abi_document(abi.expanduser().resolve())
    raw = abi_utils.parse_hex_bytes(data, field="--data")
    decoded = abi_utils.decode_method_result(abi_obj, method, raw)
    payload = {
        "status": "ok",
        "method": method,
        "raw": "0x" + raw.hex(),
        "decoded": decoded,
    }
    _text_or_json(
        payload=payload,
        json_output=json_output,
        lines=[f"{decoded!r}"],
    )


@app.command("list-artifacts")
def contract_list_artifacts(
    rpc: Optional[str] = typer.Option(None, "--rpc", help="RPC endpoint URL for deployment scoping."),
    network: Optional[str] = typer.Option(None, "--network", help="Explicit deployment network key."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    artifacts = artifact_store.list_saved_artifacts()

    deployment_key: Optional[str] = None
    if network and network.strip():
        deployment_key = deployment_store.network_key(network=network.strip(), chain_id=None)
    elif rpc is not None:
        rpc_url = resolve_rpc_url(rpc)
        deployment_key, _, _ = _deployment_lookup_key(rpc_url)

    deployments = deployment_store.list_deployments(key=deployment_key)
    payload = {
        "status": "ok",
        "deployment_key": deployment_key,
        "artifact_count": len(artifacts),
        "deployment_count": len(deployments),
        "artifacts": artifacts,
        "deployments": deployments,
    }

    if json_output:
        _emit_json(payload)
        return

    typer.echo(
        f"Artifacts: {len(artifacts)} | Deployments: {len(deployments)}"
        + (f" ({deployment_key})" if deployment_key else "")
    )
    if artifacts:
        typer.echo("Artifacts:")
        for item in artifacts[:50]:
            typer.echo(
                f"  - {item.get('name') or Path(str(item.get('artifact_path') or '')).stem} :: "
                f"{item.get('artifact_path')} :: {item.get('code_hash')}"
            )
    if deployments:
        typer.echo("Deployments:")
        for item in deployments[:50]:
            typer.echo(
                f"  - {item.get('name') or '<unnamed>'} :: {item.get('address')} :: "
                f"tx={item.get('tx_hash')} :: net={item.get('network')}"
            )
