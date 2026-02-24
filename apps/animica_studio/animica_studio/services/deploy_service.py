"""DeployService — guided contract deployment pipeline for Animica Studio.

Manages the full deployment lifecycle:
1. Validate + compile
2. Dry-run / simulation (required by default)
3. Build and encode constructor args
4. Submit deploy transaction via RPC
5. Poll for confirmation
6. Persist deployment records

Deployment records are stored as JSON in the Studio app-data dir so they
survive application restarts.

All methods are safe to call from a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from animica_studio.services.vm_toolchain_service import (
    CompileResult,
    SimulateResult,
    VmToolchainError,
    VmToolchainService,
)
from animica_studio.util.paths import app_data_dir

log = logging.getLogger(__name__)

_DEPLOY_RECORDS_FILE = "deployment_records.json"
_TX_POLL_INTERVAL_S = 2.0
_TX_POLL_TIMEOUT_S = 120.0


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class NetworkProfile:
    name: str
    rpc_url: str
    chain_id: int = 0
    explorer_url: str = ""


@dataclass
class ConstructorArg:
    name: str
    type: str
    value: Any = None
    description: str = ""


@dataclass
class DeployRequest:
    project_path: str
    network: NetworkProfile
    signer_address: str
    signer_label: str = ""
    constructor_args: list[ConstructorArg] = field(default_factory=list)
    dry_run: bool = True
    gas_limit: int = 0
    gas_price: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeployRecord:
    id: str
    project_path: str
    artifact_hash: str
    abi_hash: str
    network_name: str
    rpc_url: str
    chain_id: int
    contract_address: str
    tx_hash: str
    block_height: int
    deployed_at: float  # unix timestamp
    constructor_args: list[dict[str, Any]]
    signer_address: str
    signer_label: str
    status: str  # "pending" | "confirmed" | "failed"
    confirmations: int = 0
    explorer_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeployRecord":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            project_path=data.get("project_path", ""),
            artifact_hash=data.get("artifact_hash", ""),
            abi_hash=data.get("abi_hash", ""),
            network_name=data.get("network_name", ""),
            rpc_url=data.get("rpc_url", ""),
            chain_id=data.get("chain_id", 0),
            contract_address=data.get("contract_address", ""),
            tx_hash=data.get("tx_hash", ""),
            block_height=data.get("block_height", 0),
            deployed_at=data.get("deployed_at", 0.0),
            constructor_args=data.get("constructor_args", []),
            signer_address=data.get("signer_address", ""),
            signer_label=data.get("signer_label", ""),
            status=data.get("status", "pending"),
            confirmations=data.get("confirmations", 0),
            explorer_url=data.get("explorer_url", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class DeployResult:
    success: bool
    record: DeployRecord | None = None
    error: str = ""
    dry_run_result: SimulateResult | None = None
    compile_result: CompileResult | None = None


# ---------------------------------------------------------------------------
# ABI arg helpers
# ---------------------------------------------------------------------------


def generate_constructor_args_from_abi(abi: list[dict[str, Any]]) -> list[ConstructorArg]:
    """Auto-generate ConstructorArg list from an ABI definition."""
    for entry in abi:
        if entry.get("type") == "constructor":
            return [
                ConstructorArg(
                    name=inp.get("name", f"arg{i}"),
                    type=inp.get("type", "string"),
                    description=inp.get("description", ""),
                )
                for i, inp in enumerate(entry.get("inputs", []))
            ]
    return []


def encode_constructor_args(
    args: list[ConstructorArg],
) -> list[dict[str, Any]]:
    """Encode constructor args to a serializable list."""
    return [
        {"name": a.name, "type": a.type, "value": a.value}
        for a in args
    ]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DeployService:
    """Orchestrates the full contract deploy pipeline.

    Usage::

        svc = DeployService()
        result = svc.deploy(request)
        if result.success:
            print(result.record.contract_address)
    """

    def __init__(
        self,
        vm: VmToolchainService | None = None,
        records_dir: str | Path | None = None,
    ) -> None:
        self._vm = vm or VmToolchainService()
        self._records_dir = Path(records_dir) if records_dir else app_data_dir() / "deployments"
        self._records_dir.mkdir(parents=True, exist_ok=True)
        self._records_file = self._records_dir / _DEPLOY_RECORDS_FILE

    # ------------------------------------------------------------------
    # Deploy pipeline
    # ------------------------------------------------------------------

    def deploy(self, request: DeployRequest) -> DeployResult:
        """Run the full deploy pipeline.

        Steps:
        1. Validate project
        2. Compile
        3. Dry-run simulation (if request.dry_run is True)
        4. Submit tx
        5. Poll for confirmation
        6. Save deployment record
        """
        # Step 1: Validate
        val = self._vm.validate_project(request.project_path)
        if not val.valid:
            return DeployResult(
                success=False,
                error="Validation failed: " + "; ".join(
                    d.message for d in val.issues if d.severity == "error"
                ),
            )

        # Step 2: Compile
        project_root = Path(request.project_path).resolve()
        entry = self._vm._find_entry_file(project_root)  # noqa: SLF001
        if entry is None:
            return DeployResult(success=False, error="No contract entry file found")

        try:
            compile_result = self._vm.compile_contract(entry)
        except VmToolchainError as exc:
            return DeployResult(success=False, error=str(exc), compile_result=None)

        # Step 3: Dry-run simulation
        dry_run_result: SimulateResult | None = None
        if request.dry_run:
            call_spec = {
                "method": "__deploy__",
                "args": [asdict(a) for a in request.constructor_args],
                "chain_id": request.network.chain_id,
                "caller": request.signer_address,
            }
            dry_run_result = self._vm.simulate_tx(request.project_path, call_spec)
            if not dry_run_result.success:
                diag_msgs = "; ".join(
                    d.message for d in dry_run_result.diagnostics if d.severity in ("error",)
                )
                if diag_msgs and "vm_py not available" not in diag_msgs:
                    return DeployResult(
                        success=False,
                        error=f"Dry-run failed: {diag_msgs}",
                        dry_run_result=dry_run_result,
                        compile_result=compile_result,
                    )

        # Step 4: Submit tx
        tx_hash, contract_address, submit_error = self._submit_deploy_tx(
            request, compile_result
        )
        if submit_error:
            return DeployResult(
                success=False,
                error=submit_error,
                dry_run_result=dry_run_result,
                compile_result=compile_result,
            )

        # Step 5: Compute artifact/abi hashes
        artifact_hash = compile_result.bytecode_hash or self._hash_project(project_root)
        abi_hash = self._hash_abi(project_root)

        # Step 6: Create and save deployment record
        record = DeployRecord(
            id=str(uuid.uuid4()),
            project_path=request.project_path,
            artifact_hash=artifact_hash,
            abi_hash=abi_hash,
            network_name=request.network.name,
            rpc_url=request.network.rpc_url,
            chain_id=request.network.chain_id,
            contract_address=contract_address,
            tx_hash=tx_hash,
            block_height=0,
            deployed_at=time.time(),
            constructor_args=encode_constructor_args(request.constructor_args),
            signer_address=request.signer_address,
            signer_label=request.signer_label,
            status="pending" if tx_hash else "failed",
            explorer_url=request.network.explorer_url,
            metadata=request.metadata,
        )
        self.save_record(record)

        return DeployResult(
            success=bool(tx_hash),
            record=record,
            dry_run_result=dry_run_result,
            compile_result=compile_result,
        )

    # ------------------------------------------------------------------
    # TX submission
    # ------------------------------------------------------------------

    def _submit_deploy_tx(
        self,
        request: DeployRequest,
        compile_result: CompileResult,
    ) -> tuple[str, str, str]:
        """Submit deploy transaction via RPC.

        Returns (tx_hash, contract_address, error_message).
        Empty tx_hash/address on failure.
        """
        if not request.network.rpc_url:
            return "", "", "No RPC URL configured — cannot submit transaction"

        try:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

            client = RpcClient(request.network.rpc_url)

            # Build deploy payload
            payload: dict[str, Any] = {
                "bytecode": compile_result.bytecode_hash,
                "abi": compile_result.abi,
                "constructor_args": encode_constructor_args(request.constructor_args),
                "from": request.signer_address,
                "chain_id": request.network.chain_id,
            }
            if request.gas_limit:
                payload["gas"] = request.gas_limit
            if request.gas_price:
                payload["gas_price"] = request.gas_price

            # Try discovered deploy method
            for method_name in ("deploy_contract", "deploy", "eth_sendTransaction"):
                try:
                    result = client.call(method_name, [payload])
                    if isinstance(result, dict):
                        return (
                            result.get("tx_hash", result.get("hash", "")),
                            result.get("contract_address", result.get("address", "")),
                            "",
                        )
                    if isinstance(result, str) and result.startswith("0x"):
                        return result, "", ""
                except Exception as exc:
                    log.debug("RPC method %s failed: %s", method_name, exc)
                    continue

            return "", "", "No suitable deploy RPC method found on connected node"

        except ImportError:
            return "", "", "RpcClient not available — install animica_studio dependencies"
        except Exception as exc:
            log.exception("TX submission failed")
            return "", "", f"Transaction submission failed: {exc}"

    # ------------------------------------------------------------------
    # TX polling (confirmation tracking)
    # ------------------------------------------------------------------

    def poll_tx_status(
        self,
        tx_hash: str,
        rpc_url: str,
        *,
        timeout_s: float = _TX_POLL_TIMEOUT_S,
        poll_interval_s: float = _TX_POLL_INTERVAL_S,
        required_confirmations: int = 1,
    ) -> dict[str, Any]:
        """Poll RPC until tx is confirmed or times out.

        Returns a dict with:
        - ``status``: "confirmed" | "failed" | "timeout"
        - ``confirmations``: int
        - ``block_height``: int
        - ``error``: str (empty on success)
        """
        if not tx_hash:
            return {"status": "failed", "error": "No tx hash provided", "confirmations": 0, "block_height": 0}

        deadline = time.monotonic() + timeout_s
        confirmations = 0
        block_height = 0

        try:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

            client = RpcClient(rpc_url)

            while time.monotonic() < deadline:
                try:
                    receipt = None
                    for method_name in ("get_receipt", "eth_getTransactionReceipt", "tx_receipt"):
                        try:
                            receipt = client.call(method_name, [tx_hash])
                            if receipt:
                                break
                        except Exception:
                            continue

                    if receipt and isinstance(receipt, dict):
                        block_height = int(receipt.get("block_number", receipt.get("blockNumber", 0)) or 0)
                        confirmations = int(receipt.get("confirmations", 0)) or (1 if block_height else 0)
                        status = receipt.get("status", receipt.get("result", ""))
                        if status in ("0x1", "success", True, 1) or confirmations >= required_confirmations:
                            return {
                                "status": "confirmed",
                                "confirmations": confirmations,
                                "block_height": block_height,
                                "error": "",
                            }
                        if status in ("0x0", "failed", False, 0):
                            return {
                                "status": "failed",
                                "confirmations": 0,
                                "block_height": block_height,
                                "error": "Transaction reverted",
                            }
                except Exception as exc:
                    log.debug("Poll iteration failed: %s", exc)

                time.sleep(poll_interval_s)

            return {
                "status": "timeout",
                "confirmations": confirmations,
                "block_height": block_height,
                "error": f"Timed out after {timeout_s:.0f}s waiting for confirmation",
            }

        except ImportError:
            return {"status": "failed", "error": "RpcClient not available", "confirmations": 0, "block_height": 0}
        except Exception as exc:
            return {"status": "failed", "error": str(exc), "confirmations": 0, "block_height": 0}

    # ------------------------------------------------------------------
    # Deployment record persistence
    # ------------------------------------------------------------------

    def save_record(self, record: DeployRecord) -> None:
        """Persist a deployment record atomically."""
        records = self._load_records_raw()
        # Update or insert
        found = False
        for i, r in enumerate(records):
            if r.get("id") == record.id:
                records[i] = record.to_dict()
                found = True
                break
        if not found:
            records.append(record.to_dict())
        self._save_records_raw(records)

    def update_record_status(
        self,
        record_id: str,
        status: str,
        *,
        confirmations: int = 0,
        block_height: int = 0,
        contract_address: str = "",
    ) -> bool:
        """Update status fields on an existing deployment record."""
        records = self._load_records_raw()
        for r in records:
            if r.get("id") == record_id:
                r["status"] = status
                if confirmations:
                    r["confirmations"] = confirmations
                if block_height:
                    r["block_height"] = block_height
                if contract_address:
                    r["contract_address"] = contract_address
                self._save_records_raw(records)
                return True
        return False

    def list_records(self) -> list[DeployRecord]:
        """Return all deployment records, newest first."""
        raw = self._load_records_raw()
        records = [DeployRecord.from_dict(r) for r in raw]
        return sorted(records, key=lambda r: r.deployed_at, reverse=True)

    def get_record(self, record_id: str) -> DeployRecord | None:
        """Return a single deployment record by ID."""
        for r in self._load_records_raw():
            if r.get("id") == record_id:
                return DeployRecord.from_dict(r)
        return None

    def delete_record(self, record_id: str) -> bool:
        """Delete a deployment record by ID."""
        records = self._load_records_raw()
        new_records = [r for r in records if r.get("id") != record_id]
        if len(new_records) == len(records):
            return False
        self._save_records_raw(new_records)
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_records_raw(self) -> list[dict[str, Any]]:
        if not self._records_file.exists():
            return []
        try:
            data = json.loads(self._records_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception as exc:
            log.warning("Could not load deployment records: %s", exc)
        return []

    def _save_records_raw(self, records: list[dict[str, Any]]) -> None:
        """Atomic write of records list."""
        tmp = self._records_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
            tmp.replace(self._records_file)
        except Exception as exc:
            log.error("Failed to save deployment records: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _hash_project(root: Path) -> str:
        """Compute a deterministic hash of all project source files."""
        h = hashlib.sha256()
        for p in sorted(root.rglob("*.py")):
            try:
                h.update(p.read_bytes())
            except OSError:
                pass
        return h.hexdigest()

    @staticmethod
    def _hash_abi(root: Path) -> str:
        abi_path = root / "abi.json"
        if not abi_path.exists():
            return ""
        try:
            # Canonical hash: sort keys
            data = json.loads(abi_path.read_text(encoding="utf-8"))
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(canonical.encode()).hexdigest()
        except Exception:
            return ""
