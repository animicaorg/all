#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from omni_sdk.address import parse as parse_address
from omni_sdk.contracts.client import ContractClient
from omni_sdk.contracts.deployer import deploy_package
from omni_sdk.rpc.http import RpcClient
from omni_sdk.types.abi import decode_return, encode_call
from omni_sdk.wallet.mnemonic import mnemonic_to_seed
from omni_sdk.wallet.signer import PQSigner

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = ROOT / "contracts" / "standards"
DEPLOYMENTS_DIR = CONTRACTS_DIR / "deployments"
APP_DIR = ROOT / "apps" / "animica-tokens"
APP_SERVER_ENV = APP_DIR / "server" / ".env"
APP_WEB_ENV = APP_DIR / ".env.local"

TOKEN_MANIFEST = CONTRACTS_DIR / "animica_token" / "manifest.json"
TOKEN_CODE = CONTRACTS_DIR / "animica_token" / "contract.py"
PAIR_MANIFEST = CONTRACTS_DIR / "animica_dex_pair" / "manifest.json"
PAIR_CODE = CONTRACTS_DIR / "animica_dex_pair" / "contract.py"
FACTORY_MANIFEST = CONTRACTS_DIR / "animica_dex_factory" / "manifest.json"
FACTORY_CODE = CONTRACTS_DIR / "animica_dex_factory" / "contract.py"
ROUTER_MANIFEST = CONTRACTS_DIR / "animica_dex_router" / "manifest.json"
ROUTER_CODE = CONTRACTS_DIR / "animica_dex_router" / "contract.py"


class ChainOpsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    rpc: RpcClient
    signer: PQSigner
    chain_id: int
    max_fee: int
    gas_limit: int | None


@dataclass(frozen=True)
class StackAddresses:
    factory: str
    router: str


def _json_print(payload: Mapping[str, Any] | list[Any]) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_0x(value: str) -> str:
    v = value.strip()
    if v.startswith(("0x", "0X")):
        return v[2:]
    return v


def _parse_seed_hex(seed_hex: str) -> bytes:
    raw = bytes.fromhex(_strip_0x(seed_hex))
    if len(raw) < 16:
        raise ChainOpsError("seed hex must be at least 16 bytes")
    return raw


def _make_signer(*, seed_hex: str | None, mnemonic: str | None, alg: str) -> PQSigner:
    if seed_hex:
        seed = _parse_seed_hex(seed_hex)
    elif mnemonic:
        seed = mnemonic_to_seed(mnemonic)
    else:
        raise ChainOpsError("either --seed-hex or --mnemonic is required")

    try:
        signer = PQSigner.from_seed(alg, seed=seed)
    except Exception as exc:  # noqa: BLE001
        raise ChainOpsError(f"failed to create signer: {exc}") from exc

    if not signer.address:
        raise ChainOpsError("signer did not produce an address")
    return signer


def _rpc_nonce(rpc: RpcClient, sender: str) -> int:
    errors: list[str] = []
    for params in ([sender, "pending"], [sender]):
        try:
            out = rpc.request("state.getNonce", params)
            return int(out)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"state.getNonce({params!r}) -> {exc}")
    raise ChainOpsError(f"failed to fetch nonce for {sender}: {'; '.join(errors)}")


def _address_to_key(value: str | bytes) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) != 32:
            raise ChainOpsError(f"address key must be 32 bytes, got {len(raw)}")
        return raw

    text = str(value).strip()
    if text == "":
        raise ChainOpsError("empty address")

    if text.startswith(("0x", "0X")):
        raw = bytes.fromhex(_strip_0x(text))
        if len(raw) != 32:
            raise ChainOpsError(f"hex address key must be 32 bytes, got {len(raw)}")
        return raw

    try:
        parsed = parse_address(text)
        pubkey_hash = parsed.get("pubkey_hash")
        if isinstance(pubkey_hash, (bytes, bytearray, memoryview)):
            raw = bytes(pubkey_hash)
            if len(raw) == 32:
                return raw
    except Exception:
        pass

    try:
        from pq.py.address import decode_address

        rec = decode_address(text)
        digest = rec.digest if isinstance(rec.digest, (bytes, bytearray, memoryview)) else bytes(rec.digest)
        raw = bytes(digest)[:32].ljust(32, b"\x00")
        if len(raw) == 32:
            return raw
    except Exception:
        pass

    raise ChainOpsError(f"unable to decode address into 32-byte key: {text}")


def _token_arg(value: str) -> bytes:
    v = value.strip()
    if v.lower() in {"anm", "native", ""}:
        return b""
    return _address_to_key(v)


def _load_abi(path: Path) -> Mapping[str, Any]:
    data = _load_json(path)
    abi = data.get("abi")
    if not isinstance(abi, Mapping):
        raise ChainOpsError(f"manifest missing abi object: {path}")
    return dict(abi)


def _new_session(args: argparse.Namespace) -> Session:
    signer = _make_signer(seed_hex=args.seed_hex, mnemonic=args.mnemonic, alg=args.alg)
    rpc = RpcClient(args.rpc)
    return Session(
        rpc=rpc,
        signer=signer,
        chain_id=int(args.chain_id),
        max_fee=int(args.max_fee),
        gas_limit=int(args.gas_limit) if args.gas_limit is not None else None,
    )


def _send_contract_call(
    session: Session,
    *,
    address: str,
    abi: Mapping[str, Any],
    fn: str,
    call_args: list[Any],
    value: int = 0,
) -> dict[str, Any]:
    client = ContractClient(
        rpc=session.rpc,
        address=address,
        abi=abi,
        chain_id=session.chain_id,
    )
    nonce = _rpc_nonce(session.rpc, session.signer.address or "")
    return client.send(
        fn,
        call_args,
        signer=session.signer,
        nonce=nonce,
        max_fee=session.max_fee,
        gas_limit=session.gas_limit,
        value=int(value),
    )


def _decode_simulated_output(raw: Any) -> bytes:
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith(("0x", "0X")):
            return bytes.fromhex(_strip_0x(text))
    if isinstance(raw, Mapping):
        for key in ("returnData", "return_data", "result", "data", "output"):
            if key in raw:
                return _decode_simulated_output(raw[key])
    raise ChainOpsError(f"unexpected simulated return payload: {raw!r}")


def _simulate_contract_call(
    session: Session,
    *,
    address: str,
    abi: Mapping[str, Any],
    fn: str,
    call_args: list[Any],
) -> Any:
    calldata = bytes(encode_call(abi, fn, call_args))
    payload = {"to": address, "data": "0x" + calldata.hex(), "from": session.signer.address}
    candidates: list[tuple[str, list[Any]]] = [
        ("state.call", [payload]),
        ("execution.simulateCall", [payload]),
        ("state.simulateCall", [payload]),
        ("contracts.simulate", [payload]),
    ]
    errors: list[str] = []
    for method, params in candidates:
        try:
            out = session.rpc.request(method, params)
            raw = _decode_simulated_output(out)
            return decode_return(abi, fn, raw)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{method}: {exc}")
    raise ChainOpsError(f"failed to simulate {fn} on {address}: {'; '.join(errors)}")


def _pair_key_for_tokens(session: Session, stack: StackAddresses, token_a: bytes, token_b: bytes) -> bytes:
    factory_abi = _load_abi(FACTORY_MANIFEST)
    result = _simulate_contract_call(
        session,
        address=stack.factory,
        abi=factory_abi,
        fn="get_pair",
        call_args=[token_a, token_b],
    )
    if isinstance(result, (bytes, bytearray, memoryview)):
        pair = bytes(result)
    elif isinstance(result, str):
        pair = _address_to_key(result)
    else:
        raise ChainOpsError(f"unexpected pair lookup return: {result!r}")

    if pair == b"":
        raise ChainOpsError("pair not found in factory")
    return pair


def _approve_token(
    session: Session,
    *,
    token_address: str,
    spender: bytes,
    amount: int,
) -> dict[str, Any]:
    token_abi = _load_abi(TOKEN_MANIFEST)
    return _send_contract_call(
        session,
        address=token_address,
        abi=token_abi,
        fn="approve",
        call_args=[spender, int(amount)],
    )


def _deploy_contract(
    session: Session,
    *,
    manifest_path: Path,
    code_path: Path,
) -> tuple[str, dict[str, Any]]:
    nonce = _rpc_nonce(session.rpc, session.signer.address or "")
    address, receipt = deploy_package(
        rpc=session.rpc,
        signer=session.signer,
        manifest=manifest_path,
        code=code_path,
        chain_id=session.chain_id,
        from_addr=session.signer.address,
        nonce=nonce,
        max_fee=session.max_fee,
        gas_limit=session.gas_limit,
        await_receipt=True,
    )
    if not address:
        raise ChainOpsError(f"deployment did not return a contract address (manifest={manifest_path})")
    if not isinstance(receipt, dict):
        receipt = {"raw": receipt}
    return str(address), receipt


def _stack_from_env_or_args(args: argparse.Namespace) -> StackAddresses:
    factory = args.factory or os.getenv("ANIMICA_DEX_FACTORY_ADDRESS")
    router = args.router or os.getenv("ANIMICA_DEX_ROUTER_ADDRESS")
    if not factory:
        raise ChainOpsError("missing factory address (use --factory or ANIMICA_DEX_FACTORY_ADDRESS)")
    if not router:
        raise ChainOpsError("missing router address (use --router or ANIMICA_DEX_ROUTER_ADDRESS)")
    return StackAddresses(factory=str(factory), router=str(router))


def _set_env_values(path: Path, updates: Mapping[str, str]) -> None:
    existing: dict[str, str] = {}
    lines: list[str] = []

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k.strip()] = v

    for k, v in updates.items():
        existing[k] = v

    rendered = [f"{k}={existing[k]}" for k in sorted(existing.keys())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def _write_deployment_record(network: str, record: Mapping[str, Any]) -> Path:
    DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DEPLOYMENTS_DIR / f"{network}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def cmd_deploy_stack(args: argparse.Namespace) -> None:
    session = _new_session(args)

    factory_address, factory_deploy_receipt = _deploy_contract(
        session,
        manifest_path=FACTORY_MANIFEST,
        code_path=FACTORY_CODE,
    )

    router_address, router_deploy_receipt = _deploy_contract(
        session,
        manifest_path=ROUTER_MANIFEST,
        code_path=ROUTER_CODE,
    )

    factory_abi = _load_abi(FACTORY_MANIFEST)
    router_abi = _load_abi(ROUTER_MANIFEST)

    owner_addr = args.owner_address or session.signer.address
    fee_recipient = args.fee_recipient or owner_addr

    owner_key = _address_to_key(owner_addr)
    router_key = _address_to_key(router_address)
    factory_key = _address_to_key(factory_address)
    fee_recipient_key = _address_to_key(fee_recipient)

    factory_init_receipt = _send_contract_call(
        session,
        address=factory_address,
        abi=factory_abi,
        fn="init",
        call_args=[
            owner_key,
            router_key,
            int(args.default_fee_bps),
            int(args.launch_fee_anm),
            fee_recipient_key,
        ],
    )

    router_init_receipt = _send_contract_call(
        session,
        address=router_address,
        abi=router_abi,
        fn="init",
        call_args=[owner_key, factory_key],
    )

    env_updates_web = {
        "VITE_ANIMICA_RPC_URL": args.rpc,
        "VITE_ANIMICA_CHAIN_ID": str(args.chain_id),
        "VITE_ANIMICA_DEX_FACTORY_ADDRESS": factory_address,
        "VITE_ANIMICA_DEX_ROUTER_ADDRESS": router_address,
    }
    env_updates_server = {
        "ANIMICA_RPC_URL": args.rpc,
        "ANIMICA_CHAIN_ID": str(args.chain_id),
        "ANIMICA_DEX_FACTORY_ADDRESS": factory_address,
        "ANIMICA_DEX_ROUTER_ADDRESS": router_address,
    }

    _set_env_values(APP_WEB_ENV, env_updates_web)
    _set_env_values(APP_SERVER_ENV, env_updates_server)

    record = {
        "network": args.network,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rpc": args.rpc,
        "chain_id": int(args.chain_id),
        "deployer": session.signer.address,
        "owner": owner_addr,
        "fee_recipient": fee_recipient,
        "default_fee_bps": int(args.default_fee_bps),
        "launch_fee_anm": int(args.launch_fee_anm),
        "contracts": {
            "factory": {
                "address": factory_address,
                "manifest": str(FACTORY_MANIFEST.relative_to(ROOT)),
                "code": str(FACTORY_CODE.relative_to(ROOT)),
                "deploy_receipt": factory_deploy_receipt,
                "init_receipt": factory_init_receipt,
            },
            "router": {
                "address": router_address,
                "manifest": str(ROUTER_MANIFEST.relative_to(ROOT)),
                "code": str(ROUTER_CODE.relative_to(ROOT)),
                "deploy_receipt": router_deploy_receipt,
                "init_receipt": router_init_receipt,
            },
        },
        "env_files": {
            "web": str(APP_WEB_ENV.relative_to(ROOT)),
            "server": str(APP_SERVER_ENV.relative_to(ROOT)),
        },
    }

    deployment_path = _write_deployment_record(args.network, record)

    _json_print(
        {
            "ok": True,
            "factory": factory_address,
            "router": router_address,
            "deployment_record": str(deployment_path),
            "web_env": str(APP_WEB_ENV),
            "server_env": str(APP_SERVER_ENV),
        }
    )


def cmd_launch_token(args: argparse.Namespace) -> None:
    session = _new_session(args)

    token_address, deploy_receipt = _deploy_contract(
        session,
        manifest_path=TOKEN_MANIFEST,
        code_path=TOKEN_CODE,
    )

    token_abi = _load_abi(TOKEN_MANIFEST)

    owner_addr = args.owner_address or session.signer.address
    freeze_authority = args.freeze_authority or ""

    owner_key = _address_to_key(owner_addr)
    freeze_key = b"" if freeze_authority.strip() == "" else _address_to_key(freeze_authority)

    metadata_uri_b = args.metadata_uri.encode("utf-8")

    init_receipt = _send_contract_call(
        session,
        address=token_address,
        abi=token_abi,
        fn="init",
        call_args=[
            args.name.encode("utf-8"),
            args.symbol.encode("utf-8"),
            int(args.decimals),
            owner_key,
            int(args.initial_supply),
            int(args.max_supply),
            bool(args.mintable),
            metadata_uri_b,
            freeze_key,
        ],
    )

    _json_print(
        {
            "ok": True,
            "token": token_address,
            "owner": owner_addr,
            "metadata_uri": args.metadata_uri,
            "deploy_receipt": deploy_receipt,
            "init_receipt": init_receipt,
        }
    )


def cmd_create_pair(args: argparse.Namespace) -> None:
    session = _new_session(args)
    stack = _stack_from_env_or_args(args)

    pair_address, pair_deploy_receipt = _deploy_contract(
        session,
        manifest_path=PAIR_MANIFEST,
        code_path=PAIR_CODE,
    )

    router_abi = _load_abi(ROUTER_MANIFEST)

    token_a = _token_arg(args.token_a)
    token_b = _token_arg(args.token_b)
    pair_key = _address_to_key(pair_address)

    create_receipt = _send_contract_call(
        session,
        address=stack.router,
        abi=router_abi,
        fn="create_pair",
        call_args=[
            pair_key,
            token_a,
            token_b,
            int(args.fee_bps),
            args.metadata_uri.encode("utf-8"),
        ],
        value=int(args.launch_fee_anm),
    )

    _json_print(
        {
            "ok": True,
            "pair": pair_address,
            "factory": stack.factory,
            "router": stack.router,
            "pair_deploy_receipt": pair_deploy_receipt,
            "create_receipt": create_receipt,
        }
    )


def cmd_add_liquidity(args: argparse.Namespace) -> None:
    session = _new_session(args)
    stack = _stack_from_env_or_args(args)
    router_abi = _load_abi(ROUTER_MANIFEST)

    token_a_raw = args.token_a.strip().lower()
    token_b_raw = args.token_b.strip().lower()

    token_a = _token_arg(args.token_a)
    token_b = _token_arg(args.token_b)
    amount_a = int(args.amount_a)
    amount_b = int(args.amount_b)
    pair_key = _pair_key_for_tokens(session, stack, token_a, token_b)
    approvals: list[dict[str, Any]] = []

    if token_a_raw not in {"anm", "native", ""}:
        approvals.append(
            _approve_token(
                session,
                token_address=args.token_a,
                spender=pair_key,
                amount=amount_a,
            )
        )
    if token_b_raw not in {"anm", "native", ""}:
        approvals.append(
            _approve_token(
                session,
                token_address=args.token_b,
                spender=pair_key,
                amount=amount_b,
            )
        )

    value = 0
    if token_a_raw in {"anm", "native", ""}:
        value = amount_a
    elif token_b_raw in {"anm", "native", ""}:
        value = amount_b

    receipt = _send_contract_call(
        session,
        address=stack.router,
        abi=router_abi,
        fn="add_liquidity",
        call_args=[token_a, token_b, amount_a, amount_b, int(args.min_lp), int(args.deadline)],
        value=value,
    )

    _json_print({"ok": True, "router": stack.router, "pair": pair_key.hex(), "approvals": approvals, "receipt": receipt})


def cmd_remove_liquidity(args: argparse.Namespace) -> None:
    session = _new_session(args)
    stack = _stack_from_env_or_args(args)
    router_abi = _load_abi(ROUTER_MANIFEST)

    receipt = _send_contract_call(
        session,
        address=stack.router,
        abi=router_abi,
        fn="remove_liquidity",
        call_args=[
            _token_arg(args.token_a),
            _token_arg(args.token_b),
            int(args.lp_amount),
            int(args.min_amount_a),
            int(args.min_amount_b),
            int(args.deadline),
        ],
    )

    _json_print({"ok": True, "router": stack.router, "receipt": receipt})


def cmd_swap_exact_in(args: argparse.Namespace) -> None:
    session = _new_session(args)
    stack = _stack_from_env_or_args(args)
    router_abi = _load_abi(ROUTER_MANIFEST)

    token_in_raw = args.token_in.strip().lower()
    token_in = _token_arg(args.token_in)
    token_out = _token_arg(args.token_out)
    amount_in = int(args.amount_in)
    approvals: list[dict[str, Any]] = []

    if token_in_raw not in {"anm", "native", ""}:
        pair_key = _pair_key_for_tokens(session, stack, token_in, token_out)
        approvals.append(
            _approve_token(
                session,
                token_address=args.token_in,
                spender=pair_key,
                amount=amount_in,
            )
        )

    value = amount_in if token_in_raw in {"anm", "native", ""} else 0

    to_addr = args.to_address or session.signer.address

    receipt = _send_contract_call(
        session,
        address=stack.router,
        abi=router_abi,
        fn="swap_exact_in",
        call_args=[
            token_in,
            token_out,
            amount_in,
            int(args.min_amount_out),
            _address_to_key(to_addr),
            int(args.deadline),
        ],
        value=value,
    )

    _json_print({"ok": True, "router": stack.router, "approvals": approvals, "receipt": receipt})


def cmd_swap_exact_out(args: argparse.Namespace) -> None:
    session = _new_session(args)
    stack = _stack_from_env_or_args(args)
    router_abi = _load_abi(ROUTER_MANIFEST)

    token_in_raw = args.token_in.strip().lower()
    token_in = _token_arg(args.token_in)
    token_out = _token_arg(args.token_out)
    value = int(args.max_native_in) if token_in_raw in {"anm", "native", ""} else 0
    approvals: list[dict[str, Any]] = []

    if token_in_raw not in {"anm", "native", ""}:
        pair_key = _pair_key_for_tokens(session, stack, token_in, token_out)
        approvals.append(
            _approve_token(
                session,
                token_address=args.token_in,
                spender=pair_key,
                amount=int(args.max_amount_in),
            )
        )

    to_addr = args.to_address or session.signer.address

    receipt = _send_contract_call(
        session,
        address=stack.router,
        abi=router_abi,
        fn="swap_exact_out",
        call_args=[
            token_in,
            token_out,
            int(args.amount_out),
            int(args.max_amount_in),
            _address_to_key(to_addr),
            int(args.deadline),
        ],
        value=value,
    )

    _json_print({"ok": True, "router": stack.router, "approvals": approvals, "receipt": receipt})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Animica token launcher + DEX chain operations")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--rpc", default=os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc"))
        p.add_argument("--chain-id", type=int, default=int(os.getenv("ANIMICA_CHAIN_ID", "1337")))
        p.add_argument("--alg", default=os.getenv("ANIMICA_SIGNER_ALG", "dilithium3"))
        p.add_argument("--seed-hex", default=os.getenv("ANIMICA_DEPLOY_SEED_HEX"))
        p.add_argument("--mnemonic", default=os.getenv("ANIMICA_DEPLOY_MNEMONIC"))
        p.add_argument("--max-fee", type=int, default=int(os.getenv("ANIMICA_MAX_FEE", "1000000")))
        p.add_argument("--gas-limit", type=int, default=(int(os.getenv("ANIMICA_GAS_LIMIT")) if os.getenv("ANIMICA_GAS_LIMIT") else None))

    p_deploy = sub.add_parser("deploy-stack", help="Deploy + init DEX factory and router, then write envs")
    add_common(p_deploy)
    p_deploy.add_argument("--network", default=os.getenv("ANIMICA_NETWORK", "devnet"))
    p_deploy.add_argument("--owner-address", default=os.getenv("ANIMICA_DEX_OWNER"))
    p_deploy.add_argument("--fee-recipient", default=os.getenv("ANIMICA_DEX_FEE_RECIPIENT"))
    p_deploy.add_argument("--default-fee-bps", type=int, default=int(os.getenv("ANIMICA_DEX_DEFAULT_FEE_BPS", "30")))
    p_deploy.add_argument("--launch-fee-anm", type=int, default=int(os.getenv("ANIMICA_DEX_LAUNCH_FEE_ANM", "0")))
    p_deploy.set_defaults(func=cmd_deploy_stack)

    p_launch = sub.add_parser("launch-token", help="Deploy + init Animica token contract")
    add_common(p_launch)
    p_launch.add_argument("--name", required=True)
    p_launch.add_argument("--symbol", required=True)
    p_launch.add_argument("--decimals", type=int, default=18)
    p_launch.add_argument("--initial-supply", type=int, required=True)
    p_launch.add_argument("--max-supply", type=int, required=True)
    p_launch.add_argument("--mintable", action="store_true", default=False)
    p_launch.add_argument("--metadata-uri", required=True)
    p_launch.add_argument("--owner-address", default=os.getenv("ANIMICA_TOKEN_OWNER"))
    p_launch.add_argument("--freeze-authority", default=os.getenv("ANIMICA_TOKEN_FREEZE_AUTHORITY", ""))
    p_launch.set_defaults(func=cmd_launch_token)

    p_pair = sub.add_parser("create-pair", help="Deploy pair contract and register via router")
    add_common(p_pair)
    p_pair.add_argument("--factory", default=os.getenv("ANIMICA_DEX_FACTORY_ADDRESS"))
    p_pair.add_argument("--router", default=os.getenv("ANIMICA_DEX_ROUTER_ADDRESS"))
    p_pair.add_argument("--token-a", required=True, help="token address, or 'ANM'/'native' for native")
    p_pair.add_argument("--token-b", required=True, help="token address, or 'ANM'/'native' for native")
    p_pair.add_argument("--fee-bps", type=int, default=0)
    p_pair.add_argument("--metadata-uri", default="")
    p_pair.add_argument("--launch-fee-anm", type=int, default=int(os.getenv("ANIMICA_DEX_LAUNCH_FEE_ANM", "0")))
    p_pair.set_defaults(func=cmd_create_pair)

    p_add = sub.add_parser("add-liquidity", help="Call router.add_liquidity")
    add_common(p_add)
    p_add.add_argument("--factory", default=os.getenv("ANIMICA_DEX_FACTORY_ADDRESS"))
    p_add.add_argument("--router", default=os.getenv("ANIMICA_DEX_ROUTER_ADDRESS"))
    p_add.add_argument("--token-a", required=True)
    p_add.add_argument("--token-b", required=True)
    p_add.add_argument("--amount-a", type=int, required=True)
    p_add.add_argument("--amount-b", type=int, required=True)
    p_add.add_argument("--min-lp", type=int, default=0)
    p_add.add_argument("--deadline", type=int, default=0)
    p_add.set_defaults(func=cmd_add_liquidity)

    p_remove = sub.add_parser("remove-liquidity", help="Call router.remove_liquidity")
    add_common(p_remove)
    p_remove.add_argument("--factory", default=os.getenv("ANIMICA_DEX_FACTORY_ADDRESS"))
    p_remove.add_argument("--router", default=os.getenv("ANIMICA_DEX_ROUTER_ADDRESS"))
    p_remove.add_argument("--token-a", required=True)
    p_remove.add_argument("--token-b", required=True)
    p_remove.add_argument("--lp-amount", type=int, required=True)
    p_remove.add_argument("--min-amount-a", type=int, default=0)
    p_remove.add_argument("--min-amount-b", type=int, default=0)
    p_remove.add_argument("--deadline", type=int, default=0)
    p_remove.set_defaults(func=cmd_remove_liquidity)

    p_swap_in = sub.add_parser("swap-exact-in", help="Call router.swap_exact_in")
    add_common(p_swap_in)
    p_swap_in.add_argument("--factory", default=os.getenv("ANIMICA_DEX_FACTORY_ADDRESS"))
    p_swap_in.add_argument("--router", default=os.getenv("ANIMICA_DEX_ROUTER_ADDRESS"))
    p_swap_in.add_argument("--token-in", required=True)
    p_swap_in.add_argument("--token-out", required=True)
    p_swap_in.add_argument("--amount-in", type=int, required=True)
    p_swap_in.add_argument("--min-amount-out", type=int, default=0)
    p_swap_in.add_argument("--to-address", default="")
    p_swap_in.add_argument("--deadline", type=int, default=0)
    p_swap_in.set_defaults(func=cmd_swap_exact_in)

    p_swap_out = sub.add_parser("swap-exact-out", help="Call router.swap_exact_out")
    add_common(p_swap_out)
    p_swap_out.add_argument("--factory", default=os.getenv("ANIMICA_DEX_FACTORY_ADDRESS"))
    p_swap_out.add_argument("--router", default=os.getenv("ANIMICA_DEX_ROUTER_ADDRESS"))
    p_swap_out.add_argument("--token-in", required=True)
    p_swap_out.add_argument("--token-out", required=True)
    p_swap_out.add_argument("--amount-out", type=int, required=True)
    p_swap_out.add_argument("--max-amount-in", type=int, required=True)
    p_swap_out.add_argument("--max-native-in", type=int, default=0)
    p_swap_out.add_argument("--to-address", default="")
    p_swap_out.add_argument("--deadline", type=int, default=0)
    p_swap_out.set_defaults(func=cmd_swap_exact_out)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
        return 0
    except ChainOpsError as exc:
        _json_print({"ok": False, "error": str(exc)})
        return 2
    except Exception as exc:  # noqa: BLE001
        _json_print({"ok": False, "error": f"unexpected error: {exc}"})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
