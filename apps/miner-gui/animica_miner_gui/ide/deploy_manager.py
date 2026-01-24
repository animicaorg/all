"""Deployment manager for Animica IDE deploy flow."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from animica_miner_gui.backend.rpc_client import RPCClient, RPCError
from animica_miner_gui.ide.toolchain.builder import BuildResult, build_contract

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalletEntry:
    label: str
    address: str
    alg_name: str
    public_key_hex: str
    secret_key_hex: str


@dataclass(frozen=True)
class DeploymentOptions:
    from_address: str
    network: str
    gas_limit: Optional[int]
    max_fee: int
    explorer_url: Optional[str]


@dataclass(frozen=True)
class DeploymentResult:
    success: bool
    message: str
    tx_hash: Optional[str] = None
    receipt: Optional[dict] = None
    contract_address: Optional[str] = None
    block_height: Optional[int] = None


class DeploymentError(RuntimeError):
    pass


def load_wallet_entries(wallet_path: Path) -> List[WalletEntry]:
    if not wallet_path.exists():
        return []
    data = json.loads(wallet_path.read_text(encoding="utf-8"))
    entries = data.get("wallets") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    wallets: List[WalletEntry] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address")
        public_key_hex = entry.get("public_key_hex")
        secret_key_hex = entry.get("secret_key_hex")
        if not address or not public_key_hex or not secret_key_hex:
            continue
        wallets.append(
            WalletEntry(
                label=str(entry.get("label") or ""),
                address=str(address),
                alg_name=str(entry.get("alg_name") or "dilithium3"),
                public_key_hex=str(public_key_hex),
                secret_key_hex=str(secret_key_hex),
            )
        )
    return wallets


def _wallet_entry_for_address(entries: Iterable[WalletEntry], address: str) -> WalletEntry:
    for entry in entries:
        if entry.address == address:
            return entry
    raise DeploymentError(f"Wallet entry not found for address {address}.")


def _hex_to_bytes(value: str) -> bytes:
    v = value.strip()
    if v.startswith("0x"):
        v = v[2:]
    return bytes.fromhex(v)


class DeploymentManager:
    """Orchestrates contract deployment via local RPC."""

    def __init__(
        self,
        rpc_client: RPCClient,
        *,
        workspace: Path,
        wallet_path: Path,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.rpc_client = rpc_client
        self.workspace = workspace
        self.wallet_path = wallet_path
        self.on_progress = on_progress

    def _emit(self, message: str) -> None:
        if self.on_progress:
            self.on_progress(message)

    def discover_rpc_methods(self) -> List[str]:
        try:
            methods = self.rpc_client.get_rpc_methods()
        except RPCError as exc:
            raise DeploymentError(f"Failed to fetch RPC methods: {exc}") from exc
        if not methods:
            raise DeploymentError("RPC did not expose available methods (rpc.methods).")
        if not isinstance(methods, list):
            raise DeploymentError("RPC methods response was not a list.")
        return [str(m) for m in methods]

    def resolve_deploy_method(self, methods: List[str]) -> str:
        candidates = [
            "tx.sendRawTransaction",
            "tx_sendRawTransaction",
        ]
        for name in candidates:
            if name in methods:
                return name
        available = ", ".join(sorted(methods))
        raise DeploymentError(
            "No supported deploy RPC method found. "
            "Expected one of: tx.sendRawTransaction / tx_sendRawTransaction. "
            f"Available methods: {available}"
        )

    def resolve_receipt_method(self, methods: List[str]) -> Optional[str]:
        for name in ("tx.getTransactionReceipt", "tx_getTransactionReceipt"):
            if name in methods:
                return name
        return None

    def send_raw_transaction(self, raw_hex: str) -> str:
        methods = self.discover_rpc_methods()
        method = self.resolve_deploy_method(methods)
        try:
            result = self.rpc_client._call(method, [raw_hex])
        except RPCError as exc:
            if exc.code == -32601 or "-32601" in str(exc) or "Method not found" in str(exc):
                available = ", ".join(sorted(methods))
                raise DeploymentError(
                    "Deploy RPC method not found on node. "
                    f"Available methods: {available}"
                ) from exc
            raise
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            for key in ("txHash", "hash", "tx_hash"):
                value = result.get(key)
                if isinstance(value, str):
                    return value
        return ""

    def poll_for_receipt(
        self,
        tx_hash: str,
        *,
        timeout_s: float = 120.0,
        poll_interval_s: float = 1.0,
    ) -> Optional[dict]:
        methods = self.discover_rpc_methods()
        receipt_method = self.resolve_receipt_method(methods)
        if receipt_method is None:
            self._emit("Receipt RPC method unavailable; skipping confirmation.")
            return None
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                receipt = self.rpc_client._call(receipt_method, [tx_hash])
            except RPCError as exc:
                logger.debug("Receipt poll failed: %s", exc)
                receipt = None
            if receipt:
                return receipt if isinstance(receipt, dict) else {"receipt": receipt}
            time.sleep(poll_interval_s)
        return None

    def deploy(self, options: DeploymentOptions) -> DeploymentResult:
        self._emit("Building contract artifacts…")
        build_result: BuildResult = build_contract(self.workspace)
        if not build_result.success or not build_result.artifacts or not build_result.manifest:
            return DeploymentResult(False, build_result.message)

        try:
            wallets = load_wallet_entries(self.wallet_path)
            wallet_entry = _wallet_entry_for_address(wallets, options.from_address)
        except DeploymentError as exc:
            return DeploymentResult(False, str(exc))

        try:
            from omni_sdk.contracts.deployer import build_deploy_tx, make_package_bytes
            from omni_sdk.tx import encode as tx_encode
            from omni_sdk.wallet.signer import create_signer_from_keypair
        except Exception as exc:
            return DeploymentResult(False, f"SDK unavailable for deployment: {exc}")

        chain_id = self.rpc_client.get_chain_id()
        if chain_id is None:
            return DeploymentResult(False, "Unable to determine chain ID from node.")

        nonce = self.rpc_client.get_nonce(options.from_address)
        if nonce is None:
            return DeploymentResult(False, "Unable to determine nonce for sender.")

        try:
            package_bytes = make_package_bytes(
                manifest=build_result.manifest,
                code=build_result.artifacts.contract_path.read_bytes(),
            )
        except Exception as exc:
            return DeploymentResult(False, f"Failed to package deploy payload: {exc}")

        gas_limit = options.gas_limit
        max_fee = options.max_fee
        if max_fee <= 0:
            return DeploymentResult(False, "Max fee must be greater than zero.")

        try:
            tx_obj = build_deploy_tx(
                from_addr=options.from_address,
                chain_id=chain_id,
                nonce=nonce,
                max_fee=max_fee,
                package_bytes=package_bytes,
                gas_limit=gas_limit,
            )
            signer = create_signer_from_keypair(
                wallet_entry.alg_name,
                _hex_to_bytes(wallet_entry.secret_key_hex),
                _hex_to_bytes(wallet_entry.public_key_hex),
            )
            sign_bytes = tx_encode.sign_bytes(tx_obj)
            signature = signer.sign_tx(sign_bytes, chain_id)
            raw = tx_encode.pack_signed(
                tx_obj,
                signature=signature,
                alg_id=signer.alg_id,
                public_key=signer.public_key,
            )
            raw_hex = "0x" + raw.hex()
            computed_hash = tx_encode.tx_hash_hex(raw)
        except Exception as exc:
            return DeploymentResult(False, f"Failed to build deploy transaction: {exc}")

        self._emit("Submitting deploy transaction…")
        try:
            tx_hash = self.send_raw_transaction(raw_hex) or computed_hash
        except DeploymentError as exc:
            return DeploymentResult(False, str(exc))
        except RPCError as exc:
            return DeploymentResult(False, f"RPC error while sending deploy tx: {exc}")

        self._emit("Waiting for receipt confirmation…")
        receipt = self.poll_for_receipt(tx_hash)
        contract_address = None
        block_height = None
        if isinstance(receipt, dict):
            contract_address = receipt.get("contractAddress") or receipt.get("contract_address") or receipt.get("address")
            block_height = receipt.get("blockNumber") or receipt.get("block_height") or receipt.get("height")

        self._persist_deployment(
            options=options,
            tx_hash=tx_hash,
            receipt=receipt,
            contract_address=contract_address,
            block_height=block_height,
            build_result=build_result,
        )

        msg = "Deploy submitted"
        if receipt:
            msg = "Deploy confirmed"
        return DeploymentResult(
            True,
            msg,
            tx_hash=tx_hash,
            receipt=receipt,
            contract_address=contract_address,
            block_height=block_height,
        )

    def _persist_deployment(
        self,
        *,
        options: DeploymentOptions,
        tx_hash: str,
        receipt: Optional[dict],
        contract_address: Optional[str],
        block_height: Optional[int],
        build_result: BuildResult,
    ) -> None:
        deployments_path = self.workspace / ".animica_deployments.json"
        payload: Dict[str, object] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "network": options.network,
            "from": options.from_address,
            "txHash": tx_hash,
            "contractAddress": contract_address,
            "blockHeight": block_height,
            "codeHash": build_result.artifacts.code_hash if build_result.artifacts else None,
            "manifest": build_result.manifest,
            "receipt": receipt,
        }

        store: Dict[str, object] = {"deployments": []}
        if deployments_path.exists():
            try:
                store = json.loads(deployments_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                store = {"deployments": []}
        deployments = store.get("deployments") if isinstance(store, dict) else None
        if not isinstance(deployments, list):
            deployments = []
        deployments.append(payload)
        store = {"deployments": deployments}
        deployments_path.write_text(json.dumps(store, indent=2), encoding="utf-8")
