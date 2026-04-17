from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from animica.contracts import wallet_utils

from .config import PoolConfig
from .metrics import PoolMetrics


class PoolPayoutScheduler:
    """Periodic on-chain payout runner for credited pool balances."""

    def __init__(
        self,
        *,
        config: PoolConfig,
        metrics: PoolMetrics,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._config = config
        self._metrics = metrics
        self._log = logger or logging.getLogger("animica.stratum_pool.payouts")
        self._interval = max(0.0, float(config.payout_interval_seconds or 0.0))
        self._min_amount = max(1, int(config.payout_min_amount or 1))
        self._wallet_selector = str(
            config.payout_wallet or config.pool_address or ""
        ).strip()
        self._max_fee = max(1, int(os.getenv("ANIMICA_POOL_PAYOUT_MAX_FEE", "1")))
        self._max_recipients = max(
            1, int(os.getenv("ANIMICA_POOL_PAYOUT_MAX_RECIPIENTS", "100"))
        )
        self._retry_attempts = max(
            1, int(os.getenv("ANIMICA_POOL_PAYOUT_RETRY_ATTEMPTS", "3"))
        )
        self._retry_backoff_seconds = max(
            0.0, float(os.getenv("ANIMICA_POOL_PAYOUT_RETRY_BACKOFF_SECONDS", "2.0"))
        )
        self._signer_resolution: Any = None

    async def run(self) -> None:
        if self._interval <= 0:
            self._metrics.set_next_payout_at(None)
            return
        next_run_at = time.time() + self._interval
        self._metrics.set_next_payout_at(next_run_at)
        while True:
            wait_seconds = max(0.0, next_run_at - time.time())
            await asyncio.sleep(wait_seconds)
            payout_count = 0
            run_error: Optional[str] = None
            try:
                payout_count = await asyncio.to_thread(self._process_once)
            except Exception as exc:  # noqa: BLE001
                run_error = str(exc)
                self._log.warning("pool_payout_cycle_failed", exc_info=exc)
            self._metrics.record_payout_cycle(
                ts=time.time(),
                count=payout_count,
                error=run_error,
            )
            next_run_at = time.time() + self._interval
            self._metrics.set_next_payout_at(next_run_at)

    def _resolve_signer(self) -> Any:
        if self._signer_resolution is not None:
            return self._signer_resolution
        if not self._wallet_selector:
            raise RuntimeError(
                "pool payout wallet is not configured (set ANIMICA_POOL_PAYOUT_WALLET or --payout-wallet)"
            )
        resolved = wallet_utils.resolve_signer(
            from_value=self._wallet_selector,
            label=None,
            wallet_file=None,
            alg_override=None,
        )
        signer = resolved.signer
        if getattr(signer, "address", None) in (None, ""):
            try:
                setattr(signer, "address", resolved.sender)
            except Exception:
                pass
        self._signer_resolution = resolved
        return resolved

    @staticmethod
    def _resolve_nonce(rpc: Any, sender: str) -> int:
        errors: list[str] = []
        for params in ([sender, "pending"], [sender]):
            try:
                return int(rpc.request("state.getNonce", params))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"state.getNonce({params!r}) failed: {exc}")
        raise RuntimeError("; ".join(errors))

    def _resolve_payout_budget(self) -> int:
        # Prefer cumulative mined-minus-paid budget so failed sends remain
        # retryable even when no fresh blocks were found in the latest interval.
        budget_getter = getattr(self._metrics, "payout_available_budget", None)
        if callable(budget_getter):
            try:
                return max(0, int(budget_getter()))
            except Exception:
                pass
        return max(
            0,
            int(
                self._metrics.mined_reward_in_window(
                    window_seconds=self._interval
                )
            ),
        )

    @staticmethod
    def _is_retryable_submit_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        if not message:
            return True
        non_retryable_markers = (
            "insufficient",
            "invalid address",
            "bad address",
            "malformed address",
            "amount must be positive",
            "amount too low",
            "wallet locked",
        )
        return not any(marker in message for marker in non_retryable_markers)

    @staticmethod
    def _is_nonce_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        if "nonce" not in message:
            return False
        return any(
            marker in message
            for marker in ("too low", "too high", "mismatch", "already used", "invalid")
        )

    def _process_once(self) -> int:
        if self._interval <= 0:
            return 0
        payout_budget = self._resolve_payout_budget()
        if payout_budget <= 0:
            return 0
        due = self._metrics.payout_due_addresses(
            min_amount=self._min_amount,
            limit=self._max_recipients,
            max_total_amount=payout_budget,
        )
        if not due:
            return 0

        try:
            resolved = self._resolve_signer()
        except Exception as exc:  # noqa: BLE001
            # Make failure visible in accounting + dashboard while keeping pool alive.
            self._metrics.record_payout_failed(
                address="",
                amount=0,
                error=f"payout signer unavailable: {exc}",
            )
            raise RuntimeError(f"payout signer unavailable: {exc}") from exc

        from omni_sdk.rpc.http import RpcClient
        from omni_sdk.tx import build as tx_build
        from omni_sdk.tx import send as tx_send
        from omni_sdk.tx.signing import sign_transaction_with_rpc_context

        sent = 0
        with RpcClient(self._config.rpc_url, timeout=float(self._config.rpc_timeout)) as rpc:
            nonce = self._resolve_nonce(rpc, resolved.sender)
            for item in due:
                address = str(item.get("address") or "").strip()
                amount = int(item.get("amount") or 0)
                if not address or amount <= 0:
                    continue

                try:
                    current_nonce = nonce
                    tx_hash = None
                    attempt = 0
                    while attempt < self._retry_attempts:
                        attempt += 1
                        try:
                            tx_obj = tx_build.transfer(
                                from_addr=resolved.sender,
                                to_addr=address,
                                amount=amount,
                                nonce=current_nonce,
                                gas_limit=None,
                                max_fee=self._max_fee,
                                chain_id=int(self._config.chain_id),
                            )
                            signed = sign_transaction_with_rpc_context(
                                tx_obj,
                                resolved.signer,
                                chain_id=int(self._config.chain_id),
                                rpc=rpc,
                            )
                            tx_hash = tx_send.submit_raw(rpc, signed.raw_tx)
                            break
                        except Exception as submit_exc:  # noqa: BLE001
                            if attempt >= self._retry_attempts or not self._is_retryable_submit_error(submit_exc):
                                raise
                            if self._is_nonce_error(submit_exc):
                                try:
                                    current_nonce = self._resolve_nonce(rpc, resolved.sender)
                                except Exception:
                                    pass
                            self._log.warning(
                                "pool_payout_submission_retry",
                                extra={
                                    "address": address,
                                    "amount": amount,
                                    "attempt": attempt,
                                    "max_attempts": self._retry_attempts,
                                    "nonce": current_nonce,
                                    "error": str(submit_exc),
                                },
                            )
                            backoff = self._retry_backoff_seconds * attempt
                            if backoff > 0:
                                time.sleep(backoff)
                    if tx_hash is None:
                        raise RuntimeError("payout submission did not return transaction hash")

                    applied = self._metrics.record_payout_sent(
                        address=address,
                        amount=amount,
                        tx_hash=tx_hash,
                    )
                    self._log.info(
                        "pool_payout_submitted",
                        extra={
                            "address": address,
                            "amount": int(applied),
                            "tx_hash": tx_hash,
                            "nonce": current_nonce,
                        },
                    )
                    nonce = current_nonce + 1
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    self._metrics.record_payout_failed(
                        address=address,
                        amount=amount,
                        error=str(exc),
                    )
                    self._log.warning(
                        "pool_payout_submission_failed",
                        extra={
                            "address": address,
                            "amount": amount,
                            "nonce": nonce,
                            "error": str(exc),
                        },
                    )
                    # Recover nonce for the next candidate after a failed submit.
                    try:
                        nonce = self._resolve_nonce(rpc, resolved.sender)
                    except Exception:
                        pass
                    if "insufficient" in str(exc).lower():
                        break
        return sent
