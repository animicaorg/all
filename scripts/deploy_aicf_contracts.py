#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = ROOT / "python"
DEFAULT_ENV_FILE = ROOT / "apps" / "aicf-api" / ".env"
DEFAULT_BUILD_DIR = ROOT / "contracts" / "build" / "aicf"
DEFAULT_RPC_URL = "http://127.0.0.1:8545/rpc"

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


@dataclass(frozen=True)
class ContractTarget:
    env_key: str
    package_name: str


CONTRACT_TARGETS: tuple[ContractTarget, ...] = (
    ContractTarget("AICF_PROJECT_BALANCE_CONTRACT", "aicf_project_balance"),
    ContractTarget("AICF_JOB_ESCROW_CONTRACT", "aicf_job_escrow"),
    ContractTarget("AICF_REWARDS_CONTRACT", "aicf_rewards"),
    ContractTarget("AICF_PROVIDER_REGISTRY_CONTRACT", "aicf_provider_registry"),
    ContractTarget("AICF_STAKE_MANAGER_CONTRACT", "aicf_stake_manager"),
    ContractTarget("AICF_DISPUTE_MANAGER_CONTRACT", "aicf_dispute_manager"),
)


class DeployScriptError(RuntimeError):
    pass


def _prepend_sys_path(path: Path) -> None:
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def _repo_pythonpath() -> str:
    entries: list[str] = []
    seen: set[str] = set()
    for candidate in (PYTHON_ROOT, ROOT):
        if not candidate.exists():
            continue
        item = str(candidate.resolve())
        if item not in seen:
            entries.append(item)
            seen.add(item)
    for existing in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        text = existing.strip()
        if text and text not in seen:
            entries.append(text)
            seen.add(text)
    return os.pathsep.join(entries)


_prepend_sys_path(PYTHON_ROOT)
_prepend_sys_path(ROOT)


def _coerce_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            raise DeployScriptError(f"{field} is empty")
        try:
            if text.startswith(("0x", "0X")):
                return int(text, 16)
            return int(text)
        except Exception as exc:  # noqa: BLE001
            raise DeployScriptError(f"{field} is not an integer: {value}") from exc
    try:
        return int(value)
    except Exception as exc:  # noqa: BLE001
        raise DeployScriptError(f"{field} is not an integer: {value}") from exc


def _reexec_into_venv_if_needed() -> None:
    """Re-exec into repo .venv python when runtime deps are missing."""
    if os.environ.get("AICF_DEPLOY_NO_REEXEC") == "1":
        return

    try:
        import omni_sdk  # noqa: F401
        import vm_py  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.is_file():
        return

    venv_root = (ROOT / ".venv").resolve()
    current_prefix = Path(sys.prefix).resolve()
    if current_prefix == venv_root:
        return

    env = dict(os.environ)
    env["AICF_DEPLOY_NO_REEXEC"] = "1"
    env["PYTHONPATH"] = _repo_pythonpath()
    os.execve(str(venv_python), [str(venv_python), *sys.argv], env)


_reexec_into_venv_if_needed()

from omni_sdk.contracts.deployer import deploy_package
from omni_sdk.rpc.http import RpcClient
from omni_sdk.wallet.mnemonic import mnemonic_to_seed
from omni_sdk.wallet.signer import PQSigner


def _strip_0x(value: str) -> str:
    text = value.strip()
    if text.startswith(("0x", "0X")):
        return text[2:]
    return text


def _normalize_hex(value: str, *, field: str) -> str:
    raw = _strip_0x(value)
    if raw == "":
        raise DeployScriptError(f"{field} must not be empty")
    if len(raw) % 2 != 0:
        raise DeployScriptError(f"{field} must contain an even number of hex chars")
    try:
        bytes.fromhex(raw)
    except Exception as exc:  # noqa: BLE001
        raise DeployScriptError(f"{field} is not valid hex: {exc}") from exc
    return raw


def _parse_seed_hex(seed_hex: str) -> bytes:
    seed = bytes.fromhex(_normalize_hex(seed_hex, field="seed hex"))
    if len(seed) < 16:
        raise DeployScriptError("seed hex must decode to at least 16 bytes")
    return seed


def _normalize_alg_name(raw_name: str) -> str:
    normalized = ALG_ALIASES.get(raw_name.strip().lower(), raw_name.strip().lower())
    if normalized not in ("dilithium3", "sphincs_shake_128s"):
        raise DeployScriptError(
            f"unsupported algorithm '{raw_name}' (supported: dilithium3, sphincs_shake_128s)"
        )
    return normalized


def _parse_alg_id(raw_value: Any) -> int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if value == "":
            return None
        try:
            return int(value, 16) if value.startswith("0x") else int(value)
        except Exception as exc:  # noqa: BLE001
            raise DeployScriptError(f"invalid wallet alg_id: {raw_value}") from exc
    try:
        return int(raw_value)
    except Exception as exc:  # noqa: BLE001
        raise DeployScriptError(f"invalid wallet alg_id: {raw_value}") from exc


def _wallet_file_path(path_arg: Path | None) -> Path:
    if path_arg is not None:
        return path_arg.expanduser()
    env_path = os.environ.get("ANIMICA_WALLETS_FILE")
    if env_path and env_path.strip():
        return Path(env_path).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def _wallet_entries(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeployScriptError(f"wallet file not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise DeployScriptError(f"invalid JSON in wallet file {path}: {exc}") from exc

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
        raise DeployScriptError(
            f"unsupported wallet file format in {path}; expected an object with 'wallets' list"
        )

    return [item for item in entries if isinstance(item, dict)]


def _find_wallet_by_label(path: Path, label: str) -> dict[str, Any]:
    needle = label.strip().lower()
    if needle == "":
        raise DeployScriptError("--wallet-label must not be empty")
    for item in _wallet_entries(path):
        item_label = str(item.get("label") or "").strip().lower()
        if item_label == needle:
            return item
    raise DeployScriptError(f"wallet label '{label}' not found in {path}")


def _wallet_alg_name(entry: Mapping[str, Any]) -> str:
    name_raw = entry.get("alg_name") or entry.get("algName")
    alg_id = _parse_alg_id(entry.get("alg_id", entry.get("algId", entry.get("alg"))))
    name_from_id = ALG_BY_ID.get(int(alg_id)) if alg_id is not None else None
    if isinstance(name_raw, str) and name_raw.strip():
        normalized_name = _normalize_alg_name(name_raw)
        if name_from_id and name_from_id != normalized_name:
            raise DeployScriptError(
                "wallet algorithm mismatch: "
                f"alg_id {alg_id} maps to {name_from_id}, but alg_name is {name_raw!r}"
            )
        return normalized_name
    if name_from_id:
        return name_from_id
    raise DeployScriptError(
        "wallet entry missing supported alg_id/alg_name "
        "(expected dilithium3 or sphincs_shake_128s)"
    )


def _wallet_key_bytes(
    entry: Mapping[str, Any], *, field_name: str, aliases: tuple[str, ...]
) -> bytes:
    for key in aliases:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return bytes.fromhex(_normalize_hex(value, field=f"wallet.{field_name}"))
    raise DeployScriptError(f"wallet entry missing {field_name}")


def _make_signer_from_wallet(*, wallet_file: Path | None, wallet_label: str) -> PQSigner:
    path = _wallet_file_path(wallet_file)
    entry = _find_wallet_by_label(path, wallet_label)
    alg_name = _wallet_alg_name(entry)
    public_key = _wallet_key_bytes(
        entry,
        field_name="public_key_hex",
        aliases=("public_key_hex", "publicKeyHex", "pubkey", "pk"),
    )
    secret_key = _wallet_key_bytes(
        entry,
        field_name="secret_key_hex",
        aliases=("secret_key_hex", "secretKeyHex", "private_key_enc"),
    )

    try:
        signer = PQSigner.from_keypair(
            alg_name=alg_name,
            secret_key=secret_key,
            public_key=public_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise DeployScriptError(
            f"failed to create signer from wallet '{wallet_label}': {exc}"
        ) from exc

    wallet_address = str(entry.get("address") or "").strip()
    if wallet_address == "":
        raise DeployScriptError(
            f"wallet '{wallet_label}' in {path} is missing address; cannot verify signer"
        )
    if not signer.address:
        raise DeployScriptError("wallet signer does not expose an address")
    if signer.address.lower() != wallet_address.lower():
        raise DeployScriptError(
            "wallet address mismatch: "
            f"wallet '{wallet_label}' has {wallet_address}, derived signer address is {signer.address}"
        )

    wallet_alg_id = _parse_alg_id(entry.get("alg_id", entry.get("algId", entry.get("alg"))))
    if wallet_alg_id is not None and int(wallet_alg_id) != int(signer.alg_id):
        raise DeployScriptError(
            "wallet algorithm ID mismatch: "
            f"wallet '{wallet_label}' has alg_id={wallet_alg_id}, derived signer has alg_id={signer.alg_id}"
        )
    return signer


def _make_signer(
    *,
    seed_hex: str | None,
    mnemonic: str | None,
    wallet_label: str | None,
    wallet_file: Path | None,
    alg: str,
) -> PQSigner:
    selected_sources = int(bool(seed_hex)) + int(bool(mnemonic)) + int(bool(wallet_label))
    if selected_sources != 1:
        raise DeployScriptError(
            "use exactly one signer source: --wallet-label, --seed-hex, or --mnemonic"
        )

    if wallet_label:
        return _make_signer_from_wallet(wallet_file=wallet_file, wallet_label=wallet_label)

    if seed_hex:
        seed = _parse_seed_hex(seed_hex)
    else:
        assert mnemonic is not None
        m = mnemonic.strip()
        if m == "":
            raise DeployScriptError("mnemonic must not be empty")
        seed = mnemonic_to_seed(m)

    try:
        signer = PQSigner.from_seed(alg, seed=seed)
    except Exception as exc:  # noqa: BLE001
        raise DeployScriptError(f"failed to create signer ({alg}): {exc}") from exc

    if not signer.address:
        raise DeployScriptError("signer does not expose an address")
    return signer


def _resolve_chain_id(rpc: RpcClient, requested_chain_id: int | None) -> int:
    if requested_chain_id is not None:
        return _coerce_int(requested_chain_id, field="--chain-id")

    env_chain_id = os.environ.get("OMNI_CHAIN_ID")
    if env_chain_id and env_chain_id.strip():
        return _coerce_int(env_chain_id.strip(), field="OMNI_CHAIN_ID")

    try:
        return _coerce_int(rpc.request("chain.getChainId", []), field="chain.getChainId")
    except Exception:
        pass

    try:
        params = rpc.request("chain.getParams", [])
        if isinstance(params, Mapping):
            direct = params.get("chainId")
            nested = (params.get("chain") or {}).get("id") if isinstance(params.get("chain"), Mapping) else None
            candidate = direct if direct is not None else nested
            if candidate is not None:
                return _coerce_int(candidate, field="chain.getParams")
    except Exception:
        pass

    raise DeployScriptError("could not resolve chain id; pass --chain-id explicitly")


def _resolve_nonce(rpc: RpcClient, sender: str) -> int:
    errors: list[str] = []
    for params in ([sender, "pending"], [sender]):
        try:
            value = rpc.request("state.getNonce", params)
            return _coerce_int(value, field=f"state.getNonce({params!r})")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"state.getNonce({params!r}) -> {exc}")
    raise DeployScriptError(f"failed to fetch nonce for {sender}: {'; '.join(errors)}")


def _extract_contract_address(receipt: Any, returned_address: str | None) -> str | None:
    if returned_address:
        return str(returned_address)
    if not isinstance(receipt, Mapping):
        return None
    for key in ("contractAddress", "contract_address", "createdAddress", "created_address", "address"):
        value = receipt.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_tx_hash(receipt: Any) -> str | None:
    if not isinstance(receipt, Mapping):
        return None
    for key in ("txHash", "tx_hash", "hash"):
        value = receipt.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _compile_to_ir(manifest_path: Path, ir_path: Path) -> None:
    ir_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "vm_py.cli.compile",
        "--manifest",
        str(manifest_path),
        "--out",
        str(ir_path),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = _repo_pythonpath()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        details = stderr if stderr else stdout
        raise DeployScriptError(f"compile failed for {manifest_path}: {details}")

    if not ir_path.is_file() or ir_path.stat().st_size == 0:
        raise DeployScriptError(f"compile produced no IR file: {ir_path}")


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise DeployScriptError(f"failed to parse manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DeployScriptError(f"manifest is not a JSON object: {path}")
    return payload


def _backup_env_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)
    return backup


def _set_env_values(path: Path, updates: Mapping[str, str]) -> None:
    key_line = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    updated_lines: list[str] = []
    seen_keys: set[str] = set()
    for line in existing_lines:
        match = key_line.match(line)
        if not match:
            updated_lines.append(line)
            continue

        key = match.group(1)
        if key in updates:
            updated_lines.append(f"{key}={updates[key]}")
            seen_keys.add(key)
        else:
            updated_lines.append(line)

    for key, value in updates.items():
        if key not in seen_keys:
            updated_lines.append(f"{key}={value}")

    rendered = "\n".join(updated_lines).rstrip("\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def _deploy_one(
    *,
    rpc: RpcClient,
    signer: PQSigner,
    chain_id: int,
    max_fee: int,
    gas_limit: int | None,
    wait_seconds: float,
    build_dir: Path,
    target: ContractTarget,
) -> dict[str, Any]:
    manifest_path = ROOT / "contracts" / "packages" / target.package_name / "manifest.json"
    if not manifest_path.is_file():
        raise DeployScriptError(f"manifest not found: {manifest_path}")

    ir_path = build_dir / target.package_name / f"{target.package_name}.ir"
    _compile_to_ir(manifest_path, ir_path)
    manifest = _load_manifest(manifest_path)

    sender = signer.address or ""
    if sender == "":
        raise DeployScriptError("signer address is empty")

    nonce = _resolve_nonce(rpc, sender)

    contract_address, receipt = deploy_package(
        rpc=rpc,
        signer=signer,
        manifest=manifest,
        code=ir_path,
        chain_id=int(chain_id),
        from_addr=sender,
        nonce=int(nonce),
        max_fee=int(max_fee),
        gas_limit=gas_limit,
        await_receipt=True,
        timeout_s=float(wait_seconds),
    )

    resolved_address = _extract_contract_address(receipt, contract_address)
    if not resolved_address:
        raise DeployScriptError(
            f"deployment completed but no contract address returned ({target.package_name})"
        )

    return {
        "envKey": target.env_key,
        "package": target.package_name,
        "manifestPath": str(manifest_path),
        "irPath": str(ir_path),
        "address": resolved_address,
        "txHash": _extract_tx_hash(receipt),
        "receiptStatus": receipt.get("status") if isinstance(receipt, Mapping) else None,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy AICF contracts and inject addresses into apps/aicf-api .env",
    )
    parser.add_argument(
        "--rpc",
        default=os.environ.get("OMNI_RPC_URL", DEFAULT_RPC_URL),
        help=f"JSON-RPC URL (default: {DEFAULT_RPC_URL} or OMNI_RPC_URL)",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=None,
        help="Chain ID (if omitted, auto-detect from RPC or OMNI_CHAIN_ID)",
    )
    parser.add_argument(
        "--alg",
        default="dilithium3",
        help=(
            "Signer algorithm for --seed-hex/--mnemonic: "
            "dilithium3 or sphincs_shake_128s (default: dilithium3)"
        ),
    )
    signer_group = parser.add_mutually_exclusive_group(required=True)
    signer_group.add_argument(
        "--wallet-label",
        default=None,
        help="Use signer from wallet label in wallets.json",
    )
    signer_group.add_argument("--seed-hex", default=None, help="Signer seed hex")
    signer_group.add_argument("--mnemonic", default=None, help="Signer mnemonic words")
    parser.add_argument(
        "--wallet-file",
        type=Path,
        default=None,
        help=(
            "Path to wallets.json used with --wallet-label "
            "(default: $ANIMICA_WALLETS_FILE or ~/.animica/wallets.json)"
        ),
    )
    parser.add_argument("--max-fee", type=int, default=1, help="Deploy transaction max_fee")
    parser.add_argument("--gas-limit", type=int, default=None, help="Optional deploy gas limit")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=180.0,
        help="Receipt wait timeout per deploy (default: 180)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"API env file path (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help=f"IR output directory (default: {DEFAULT_BUILD_DIR})",
    )
    parser.add_argument(
        "--no-env-backup",
        action="store_true",
        help="Skip creating a timestamped backup of the env file",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print only JSON summary (useful for automation)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        signer = _make_signer(
            seed_hex=args.seed_hex,
            mnemonic=args.mnemonic,
            wallet_label=args.wallet_label,
            wallet_file=args.wallet_file,
            alg=args.alg,
        )
        rpc = RpcClient(args.rpc)
        chain_id = _resolve_chain_id(rpc, args.chain_id)

        deployments: list[dict[str, Any]] = []
        env_updates: dict[str, str] = {}
        for target in CONTRACT_TARGETS:
            if not args.print_json:
                print(f"[aicf-deploy] deploying {target.package_name} ...", file=sys.stderr)
            deployed = _deploy_one(
                rpc=rpc,
                signer=signer,
                chain_id=chain_id,
                max_fee=args.max_fee,
                gas_limit=args.gas_limit,
                wait_seconds=args.wait_seconds,
                build_dir=args.build_dir,
                target=target,
            )
            deployments.append(deployed)
            env_updates[target.env_key] = deployed["address"]

        backup_path = None if args.no_env_backup else _backup_env_file(args.env_file)
        _set_env_values(args.env_file, env_updates)

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rpc": args.rpc,
            "chainId": chain_id,
            "deployer": signer.address,
            "envFile": str(args.env_file.resolve()),
            "envBackup": str(backup_path.resolve()) if backup_path else None,
            "deployments": deployments,
        }

        if args.print_json:
            print(json.dumps(summary, sort_keys=True, indent=2))
            return 0

        print("[aicf-deploy] done")
        print(f"[aicf-deploy] api env updated: {args.env_file}")
        if backup_path:
            print(f"[aicf-deploy] backup written: {backup_path}")
        print("[aicf-deploy] deployed addresses:")
        for item in deployments:
            print(f"  - {item['envKey']}={item['address']}")
        return 0
    except DeployScriptError as exc:
        print(f"[aicf-deploy] ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[aicf-deploy] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
