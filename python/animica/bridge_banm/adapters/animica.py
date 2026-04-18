from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from ..addressing import validate_animica_address


@dataclass(frozen=True)
class AnimicaTxStatus:
    tx_hash: str
    confirmations: int
    included: bool
    block_height: int | None
    raw: dict[str, Any] | None


@dataclass(frozen=True)
class AnimicaDepositObservation:
    tx_hash: str
    from_address: str
    to_address: str
    amount: int
    confirmations: int
    block_height: int | None
    raw: dict[str, Any] | None


@dataclass(frozen=True)
class AnimicaReleaseResult:
    tx_hash: str
    raw_output: str


class AnimicaAdapter:
    def get_balance(self, address: str) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def get_tx_status(self, tx_hash: str) -> AnimicaTxStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def inspect_deposit(
        self,
        *,
        tx_hash: str,
        expected_to: str,
    ) -> AnimicaDepositObservation:  # pragma: no cover - interface
        raise NotImplementedError

    def submit_release(
        self,
        *,
        from_address: str,
        to_address: str,
        amount_base_units: int,
        order_id: str,
    ) -> AnimicaReleaseResult:  # pragma: no cover - interface
        raise NotImplementedError


class AnimicaRpcAdapter(AnimicaAdapter):
    def __init__(self, rpc_url: str):
        self._rpc_url = rpc_url
        self._timeout = httpx.Timeout(20.0)

    def _rpc(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or [],
        }
        response = httpx.post(self._rpc_url, json=payload, timeout=self._timeout)
        response.raise_for_status()
        body = response.json()
        error = body.get("error")
        if error:
            code = error.get("code", "unknown")
            message = error.get("message", "rpc error")
            raise RuntimeError(f"Animica RPC {method} failed ({code}): {message}")
        return body.get("result")

    def get_balance(self, address: str) -> int:
        validate_animica_address(address)
        for method in ("state.getBalance", "state_getBalance"):
            try:
                result = self._rpc(method, [address])
                return int(result)
            except Exception:
                continue
        raise RuntimeError("Animica RPC balance methods unavailable")

    def get_tx_status(self, tx_hash: str) -> AnimicaTxStatus:
        for method in ("tx.getStatus", "tx.getTransactionStatus"):
            try:
                status = self._rpc(method, [tx_hash])
                if isinstance(status, dict):
                    confirmations = int(status.get("confirmations") or 0)
                    included = confirmations > 0 or str(status.get("status") or "").lower() in {
                        "confirmed",
                        "included",
                        "included_block",
                    }
                    block_height = status.get("blockNumber") or status.get("block_height") or status.get("height")
                    return AnimicaTxStatus(
                        tx_hash=tx_hash,
                        confirmations=confirmations,
                        included=bool(included),
                        block_height=int(block_height) if block_height is not None else None,
                        raw=status,
                    )
            except Exception:
                continue
        raise RuntimeError("unable to resolve Animica tx status")

    def inspect_deposit(
        self,
        *,
        tx_hash: str,
        expected_to: str,
    ) -> AnimicaDepositObservation:
        validate_animica_address(expected_to)
        tx = None
        for method in ("tx.getTransactionByHash", "tx_getTransactionByHash", "chain.getTransactionByHash"):
            try:
                tx = self._rpc(method, [tx_hash])
                if tx:
                    break
            except Exception:
                continue
        if not isinstance(tx, dict):
            raise RuntimeError("Animica transaction detail unavailable")

        from_address = str(tx.get("from") or tx.get("sender") or tx.get("fromAddress") or "").strip()
        to_address = str(tx.get("to") or tx.get("recipient") or tx.get("toAddress") or "").strip()
        if not from_address or not to_address:
            raise RuntimeError("Animica transaction missing sender or recipient")
        amount = int(tx.get("value") or tx.get("amount") or 0)
        if amount <= 0:
            raise RuntimeError("Animica deposit amount missing or zero")
        status = self.get_tx_status(tx_hash)
        return AnimicaDepositObservation(
            tx_hash=tx_hash,
            from_address=from_address,
            to_address=to_address,
            amount=amount,
            confirmations=status.confirmations,
            block_height=status.block_height,
            raw=tx,
        )

    def submit_release(
        self,
        *,
        from_address: str,
        to_address: str,
        amount_base_units: int,
        order_id: str,
    ) -> AnimicaReleaseResult:
        validate_animica_address(from_address)
        validate_animica_address(to_address)
        if amount_base_units <= 0:
            raise ValueError("amount_base_units must be > 0")

        cmd = [
            sys.executable,
            "-m",
            "animica.cli.tx",
            "send",
            "--from",
            from_address,
            "--to",
            to_address,
            "--value-nanm",
            str(amount_base_units),
            "--rpc-url",
            self._rpc_url,
            "--allow-remote-rpc",
        ]
        process = subprocess.run(cmd, check=False, capture_output=True, text=True)
        output = (process.stdout or "") + "\n" + (process.stderr or "")
        if process.returncode != 0:
            raise RuntimeError(f"Animica release send failed for {order_id}: {output.strip()}")

        match = re.search(r"0x[a-fA-F0-9]{64}", output)
        if not match:
            # Try JSON-ish output shape
            try:
                payload = json.loads(process.stdout.strip())
                tx_hash = payload.get("tx_hash") or payload.get("hash")
                if isinstance(tx_hash, str) and tx_hash.startswith("0x"):
                    return AnimicaReleaseResult(tx_hash=tx_hash, raw_output=output)
            except Exception:
                pass
            raise RuntimeError(f"Animica release tx hash not found in output: {output.strip()}")

        return AnimicaReleaseResult(tx_hash=match.group(0), raw_output=output)

