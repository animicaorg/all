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
        self._drop_grace_seconds = max(
            0.0, float(os.getenv("ANIMICA_POOL_PAYOUT_DROP_GRACE_SECONDS", "30.0"))
        )
        self._max_pending_reconcile = max(
            1, int(os.getenv("ANIMICA_POOL_PAYOUT_PENDING_LIMIT", "1000"))
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

    @staticmethod
    def _normalize_tx_hash(tx_hash: object) -> str:
        text = str(tx_hash or "").strip().lower()
        if text and not text.startswith("0x"):
            text = "0x" + text
        return text

    def _record_payout_sent(
        self,
        *,
        address: str,
        amount: int,
        tx_hash: str,
        raw_tx: Optional[str],
        nonce: int,
    ) -> int:
        try:
            return int(
                self._metrics.record_payout_sent(
                    address=address,
                    amount=amount,
                    tx_hash=tx_hash,
                    raw_tx=raw_tx,
                    nonce=nonce,
                )
            )
        except TypeError:
            return int(
                self._metrics.record_payout_sent(
                    address=address,
                    amount=amount,
                    tx_hash=tx_hash,
                )
            )

    def _read_tx_status(self, rpc: Any, tx_hash: str) -> dict[str, object]:
        normalized = self._normalize_tx_hash(tx_hash)
        if not normalized:
            return {"hash": tx_hash, "status": "not_found", "state": "not_found"}
        methods = ("tx.getStatus", "tx.getTransactionStatus")
        last_error: Optional[Exception] = None
        for method in methods:
            try:
                status = rpc.request(method, [normalized])
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            if not isinstance(status, dict):
                continue
            payload = dict(status)
            payload.setdefault("hash", normalized)
            return payload
        if last_error is not None:
            raise RuntimeError(f"tx status lookup failed for {normalized}: {last_error}") from last_error
        return {"hash": normalized, "status": "not_found", "state": "not_found"}

    @staticmethod
    def _tx_is_confirmed(status: dict[str, object]) -> bool:
        state = str(status.get("state") or "").strip().lower()
        token = str(status.get("status") or "").strip().lower()
        return state in {"included_block", "finalized"} or token in {"confirmed", "finalized"}

    @staticmethod
    def _tx_is_pending(status: dict[str, object]) -> bool:
        state = str(status.get("state") or "").strip().lower()
        token = str(status.get("status") or "").strip().lower()
        return state in {"pending_mempool"} or token in {"pending"}

    @staticmethod
    def _tx_is_dropped(status: dict[str, object]) -> bool:
        state = str(status.get("state") or "").strip().lower()
        token = str(status.get("status") or "").strip().lower()
        dropped = {"evicted", "rejected", "reorged_out", "not_found"}
        return state in dropped or token in dropped

    def _reconcile_submitted_payouts(self, *, rpc: Any, tx_send: Any) -> None:
        pending_getter = getattr(self._metrics, "pending_payout_submissions", None)
        if not callable(pending_getter):
            return
        try:
            pending = list(
                pending_getter(limit=self._max_pending_reconcile) or []
            )
        except Exception:
            return
        if not pending:
            return

        mark_confirmed = getattr(self._metrics, "mark_payout_confirmed", None)
        record_rebroadcast = getattr(self._metrics, "record_payout_rebroadcast", None)
        record_retry = getattr(self._metrics, "record_payout_retry", None)
        mark_dropped = getattr(self._metrics, "mark_payout_dropped", None)
        now = time.time()

        for item in pending:
            tx_hash = self._normalize_tx_hash(item.get("tx_hash"))
            if not tx_hash:
                continue
            address = str(item.get("address") or "").strip()
            amount = int(item.get("amount") or 0)
            retry_count = max(0, int(item.get("retry_count") or 0))
            next_retry_ts_obj = item.get("next_retry_ts")
            next_retry_ts = float(next_retry_ts_obj) if next_retry_ts_obj is not None else None
            if next_retry_ts is not None and now < next_retry_ts:
                continue

            try:
                status = self._read_tx_status(rpc, tx_hash)
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "pool_payout_status_lookup_failed",
                    extra={"tx_hash": tx_hash, "address": address, "error": str(exc)},
                )
                continue

            if self._tx_is_confirmed(status):
                if callable(mark_confirmed):
                    try:
                        mark_confirmed(
                            tx_hash=tx_hash,
                            details={
                                "state": status.get("state"),
                                "status": status.get("status"),
                                "confirmations": status.get("confirmations"),
                                "included_height": status.get("included_height"),
                            },
                        )
                    except Exception:
                        pass
                continue

            if self._tx_is_pending(status):
                continue
            if not self._tx_is_dropped(status):
                continue

            submitted_ts_obj = item.get("timestamp")
            submitted_ts = float(submitted_ts_obj or 0.0)
            age = max(0.0, now - submitted_ts) if submitted_ts > 0 else self._drop_grace_seconds
            state = str(status.get("state") or status.get("status") or "unknown").lower()
            if state in {"evicted", "not_found"} and age < self._drop_grace_seconds:
                continue

            raw_tx = str(item.get("raw_tx") or "").strip()
            if not raw_tx:
                if callable(mark_dropped):
                    try:
                        mark_dropped(
                            tx_hash=tx_hash,
                            error=f"payout tx {tx_hash} dropped ({state}) and raw tx is unavailable",
                            release_credit=True,
                        )
                    except Exception:
                        pass
                continue

            try:
                rebroadcast_hash = self._normalize_tx_hash(tx_send.submit_raw(rpc, raw_tx))
                if not rebroadcast_hash:
                    raise RuntimeError("rebroadcast did not return transaction hash")
                if callable(record_rebroadcast):
                    try:
                        record_rebroadcast(
                            tx_hash=tx_hash,
                            new_tx_hash=rebroadcast_hash,
                            raw_tx=raw_tx,
                            nonce=item.get("nonce"),
                            next_retry_ts=now + self._retry_backoff_seconds,
                            details={"state": state},
                        )
                    except Exception:
                        pass
                self._log.info(
                    "pool_payout_rebroadcast",
                    extra={
                        "address": address,
                        "amount": amount,
                        "previous_tx_hash": tx_hash,
                        "tx_hash": rebroadcast_hash,
                        "retry_count": retry_count + 1,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                if self._is_nonce_error(exc):
                    try:
                        status_after_nonce_error = self._read_tx_status(rpc, tx_hash)
                    except Exception:
                        status_after_nonce_error = {}
                    if (
                        isinstance(status_after_nonce_error, dict)
                        and self._tx_is_confirmed(status_after_nonce_error)
                    ):
                        if callable(mark_confirmed):
                            try:
                                mark_confirmed(
                                    tx_hash=tx_hash,
                                    details={
                                        "state": status_after_nonce_error.get("state"),
                                        "status": status_after_nonce_error.get("status"),
                                        "reason": "nonce_error_recheck_confirmed",
                                    },
                                )
                            except Exception:
                                pass
                        continue

                if self._is_retryable_submit_error(exc):
                    if callable(record_retry):
                        try:
                            record_retry(
                                tx_hash=tx_hash,
                                error=f"rebroadcast failed: {exc}",
                                next_retry_ts=now + (self._retry_backoff_seconds * max(1, retry_count + 1)),
                            )
                        except Exception:
                            pass
                    continue

                if callable(mark_dropped):
                    try:
                        mark_dropped(
                            tx_hash=tx_hash,
                            error=f"rebroadcast non-retryable failure: {exc}",
                            release_credit=True,
                        )
                    except Exception:
                        pass

    def _process_once(self) -> int:
        if self._interval <= 0:
            return 0

        from omni_sdk.rpc.http import RpcClient
        from omni_sdk.tx import build as tx_build
        from omni_sdk.tx import send as tx_send
        from omni_sdk.tx.signing import sign_transaction_with_rpc_context

        sent = 0
        with RpcClient(self._config.rpc_url, timeout=float(self._config.rpc_timeout)) as rpc:
            self._reconcile_submitted_payouts(rpc=rpc, tx_send=tx_send)

            payout_budget = self._resolve_payout_budget()
            if payout_budget <= 0:
                return sent
            due = self._metrics.payout_due_addresses(
                min_amount=self._min_amount,
                limit=self._max_recipients,
                max_total_amount=payout_budget,
            )
            if not due:
                return sent

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

            nonce = self._resolve_nonce(rpc, resolved.sender)
            for item in due:
                address = str(item.get("address") or "").strip()
                amount = int(item.get("amount") or 0)
                if not address or amount <= 0:
                    continue

                try:
                    current_nonce = nonce
                    tx_hash = None
                    signed_raw_tx: Optional[str] = None
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
                            signed_raw_tx = str(signed.raw_tx)
                            tx_hash = self._normalize_tx_hash(
                                tx_send.submit_raw(rpc, signed.raw_tx)
                            )
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

                    applied = self._record_payout_sent(
                        address=address,
                        amount=amount,
                        tx_hash=tx_hash,
                        raw_tx=signed_raw_tx,
                        nonce=current_nonce,
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
