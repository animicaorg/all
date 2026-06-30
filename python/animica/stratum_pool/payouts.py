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
            for marker in (
                "too low", "too high", "mismatch", "already used", "invalid",
                # Animica uses a nonce-less execution model (the sender nonce is
                # never persisted/incremented), but the mempool still rejects
                # non-contiguous nonces as a "nonce gap". Treat that as a nonce
                # error so the submit path re-resolves the mempool-aware nonce and
                # retries instead of burning attempts on a stale value.
                "gap", "nonce_gap",
            )
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

    @staticmethod
    def _retry_fee_for_rebroadcast(*, base_fee: int, retry_count: int, attempt: int) -> int:
        # Ensure replacement tx body changes on every rebroadcast attempt.
        floor = max(1, int(base_fee))
        bump = max(1, int(retry_count) + int(attempt) + 1)
        return floor + bump

    @staticmethod
    def _next_nonce_after_retry(
        *,
        tx_nonce: Optional[int],
        sender_pending_nonce: Optional[int],
    ) -> Optional[int]:
        if tx_nonce is None and sender_pending_nonce is None:
            return None
        if tx_nonce is None:
            return int(sender_pending_nonce) if sender_pending_nonce is not None else None
        if sender_pending_nonce is None:
            return int(tx_nonce)
        return max(int(tx_nonce), int(sender_pending_nonce))

    def _reconcile_submitted_payouts(
        self,
        *,
        rpc: Any,
        tx_send: Any,
        tx_build: Any,
        sign_transaction_with_rpc_context: Any,
    ) -> None:
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

        signer_sender: Optional[str] = None
        signer_obj: Any = None
        signer_error: Optional[Exception] = None
        pending_nonce_cache: Optional[int] = None
        nonce_cache_valid_at: float = 0.0

        def resolve_signer_once() -> tuple[Optional[str], Any]:
            nonlocal signer_sender, signer_obj, signer_error
            if signer_sender is not None and signer_obj is not None:
                return signer_sender, signer_obj
            if signer_error is not None:
                raise signer_error
            try:
                resolved = self._resolve_signer()
                signer_sender = str(getattr(resolved, "sender", "") or "").strip()
                signer_obj = getattr(resolved, "signer", None)
                if not signer_sender or signer_obj is None:
                    raise RuntimeError("payout signer unavailable")
                return signer_sender, signer_obj
            except Exception as exc:  # noqa: BLE001
                signer_error = exc
                raise

        def refresh_pending_nonce(sender: str, *, force: bool = False) -> int:
            nonlocal pending_nonce_cache, nonce_cache_valid_at
            if (
                not force
                and pending_nonce_cache is not None
                and (time.time() - nonce_cache_valid_at) <= 1.0
            ):
                return int(pending_nonce_cache)
            pending_nonce_cache = self._resolve_nonce(rpc, sender)
            nonce_cache_valid_at = time.time()
            return int(pending_nonce_cache)

        for item in pending:
            tx_hash = self._normalize_tx_hash(item.get("tx_hash"))
            if not tx_hash:
                continue
            address = str(item.get("address") or "").strip()
            amount = int(item.get("amount") or 0)
            retry_count = max(0, int(item.get("retry_count") or 0))
            item_nonce_raw = item.get("nonce")
            item_nonce = int(item_nonce_raw) if item_nonce_raw is not None else None
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

            if not address or amount <= 0:
                if callable(mark_dropped):
                    try:
                        mark_dropped(
                            tx_hash=tx_hash,
                            error=f"payout tx {tx_hash} dropped ({state}) and payout metadata is incomplete",
                            release_credit=True,
                        )
                    except Exception:
                        pass
                continue

            try:
                sender, signer = resolve_signer_once()
                if sender is None or signer is None:
                    raise RuntimeError("payout signer unavailable")
            except Exception as exc:  # noqa: BLE001
                if callable(record_retry):
                    try:
                        record_retry(
                            tx_hash=tx_hash,
                            error=f"rebroadcast signer unavailable: {exc}",
                            next_retry_ts=now + (self._retry_backoff_seconds * max(1, retry_count + 1)),
                        )
                    except Exception:
                        pass
                continue

            try:
                chain_pending_nonce = refresh_pending_nonce(sender)
            except Exception as exc:  # noqa: BLE001
                if callable(record_retry):
                    try:
                        record_retry(
                            tx_hash=tx_hash,
                            error=f"rebroadcast nonce lookup failed: {exc}",
                            next_retry_ts=now + (self._retry_backoff_seconds * max(1, retry_count + 1)),
                        )
                    except Exception:
                        pass
                continue

            if item_nonce is not None and chain_pending_nonce > item_nonce:
                # Nonce is already consumed/pending by another tx; do not generate
                # another resend for this payout slot.
                if callable(record_retry):
                    try:
                        record_retry(
                            tx_hash=tx_hash,
                            error=(
                                "rebroadcast skipped: nonce already advanced "
                                f"(chain_pending_nonce={chain_pending_nonce}, tx_nonce={item_nonce})"
                            ),
                            next_retry_ts=now + (self._retry_backoff_seconds * max(2, retry_count + 1)),
                        )
                    except Exception:
                        pass
                continue

            resend_nonce = self._next_nonce_after_retry(
                tx_nonce=item_nonce,
                sender_pending_nonce=chain_pending_nonce,
            )
            if resend_nonce is None:
                if callable(record_retry):
                    try:
                        record_retry(
                            tx_hash=tx_hash,
                            error="rebroadcast skipped: unable to resolve resend nonce",
                            next_retry_ts=now + (self._retry_backoff_seconds * max(1, retry_count + 1)),
                        )
                    except Exception:
                        pass
                continue

            try:
                rechecked = self._read_tx_status(rpc, tx_hash)
            except Exception as exc:  # noqa: BLE001
                if callable(record_retry):
                    try:
                        record_retry(
                            tx_hash=tx_hash,
                            error=f"rebroadcast precheck failed: {exc}",
                            next_retry_ts=now + (self._retry_backoff_seconds * max(1, retry_count + 1)),
                        )
                    except Exception:
                        pass
                continue

            if self._tx_is_confirmed(rechecked):
                if callable(mark_confirmed):
                    try:
                        mark_confirmed(
                            tx_hash=tx_hash,
                            details={
                                "state": rechecked.get("state"),
                                "status": rechecked.get("status"),
                                "reason": "rebroadcast_precheck_confirmed",
                            },
                        )
                    except Exception:
                        pass
                continue
            if self._tx_is_pending(rechecked) or not self._tx_is_dropped(rechecked):
                if callable(record_retry):
                    try:
                        record_retry(
                            tx_hash=tx_hash,
                            error=(
                                "rebroadcast skipped: tx no longer dropped "
                                f"(state={rechecked.get('state')}, status={rechecked.get('status')})"
                            ),
                            next_retry_ts=now + (self._retry_backoff_seconds * max(1, retry_count + 1)),
                        )
                    except Exception:
                        pass
                continue

            try:
                rebroadcast_hash = ""
                rebroadcast_raw_tx: Optional[str] = None
                rebroadcast_nonce = int(resend_nonce)
                for attempt in range(3):
                    retry_fee = self._retry_fee_for_rebroadcast(
                        base_fee=self._max_fee,
                        retry_count=retry_count,
                        attempt=attempt,
                    )
                    tx_obj = tx_build.transfer(
                        from_addr=sender,
                        to_addr=address,
                        amount=amount,
                        nonce=rebroadcast_nonce,
                        gas_limit=None,
                        max_fee=retry_fee,
                        chain_id=int(self._config.chain_id),
                    )
                    signed = sign_transaction_with_rpc_context(
                        tx_obj,
                        signer,
                        chain_id=int(self._config.chain_id),
                        rpc=rpc,
                    )
                    rebroadcast_raw_tx = str(getattr(signed, "raw_tx", "") or "")
                    if not rebroadcast_raw_tx:
                        raise RuntimeError("rebroadcast signing produced empty raw tx")
                    candidate_hash = self._normalize_tx_hash(
                        tx_send.submit_raw(rpc, signed.raw_tx)
                    )
                    if not candidate_hash:
                        raise RuntimeError("rebroadcast did not return transaction hash")
                    if candidate_hash != tx_hash:
                        rebroadcast_hash = candidate_hash
                        break
                if not rebroadcast_hash:
                    raise RuntimeError(
                        f"rebroadcast returned existing tx hash {tx_hash}; replacement was not unique"
                    )
                if callable(record_rebroadcast):
                    try:
                        record_rebroadcast(
                            tx_hash=tx_hash,
                            new_tx_hash=rebroadcast_hash,
                            raw_tx=rebroadcast_raw_tx,
                            nonce=rebroadcast_nonce,
                            next_retry_ts=now + self._retry_backoff_seconds,
                            details={
                                "state": state,
                                "retry_count": retry_count + 1,
                                "resend_nonce": rebroadcast_nonce,
                            },
                        )
                    except Exception:
                        pass
                pending_nonce_cache = rebroadcast_nonce + 1
                nonce_cache_valid_at = time.time()
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
            self._reconcile_submitted_payouts(
                rpc=rpc,
                tx_send=tx_send,
                tx_build=tx_build,
                sign_transaction_with_rpc_context=sign_transaction_with_rpc_context,
            )

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

            # Nonce-less execution model: the chain never persists/increments the
            # sender's account nonce (state.getNonce is always 0), yet the mempool
            # enforces strict sequential nonces (expected = highest-pending-nonce
            # + 1, or the committed 0 when the sender has nothing pending).
            # Resolving the nonce ONCE per cycle and locally incrementing
            # (0,1,2,...) produced `nonce_gap` rejections whenever a block mined a
            # payout mid-burst and dropped the pending count. So resolve the
            # mempool-aware nonce FRESH for every payout (and on every retry) — each
            # tx then carries exactly the nonce the mempool currently expects, and
            # the gap-aware retry below recovers from the residual submit/mine race.
            for item in due:
                address = str(item.get("address") or "").strip()
                amount = int(item.get("amount") or 0)
                if not address or amount <= 0:
                    continue

                current_nonce: Optional[int] = None
                # Unique per-payout fee -> distinct tx hash so a repeat (address,
                # amount) payout actually executes. The nonce-less chain rejects
                # re-applying an already-included hash (block_import "duplicate tx
                # apply attempt rejected"), so two identical payouts otherwise
                # silently no-op the 2nd one while the ledger still marks it paid.
                # Computed ONCE per payout (stable across retries). max_fee is a
                # cap; the salt is sub-0.01 ANM.
                payout_fee = int(self._max_fee) + (int(time.time() * 1_000_000) % 5_000_000)
                try:
                    tx_hash = None
                    signed_raw_tx: Optional[str] = None
                    attempt = 0
                    while attempt < self._retry_attempts:
                        attempt += 1
                        try:
                            # Re-resolve every attempt so a retry after a nonce gap
                            # (or any transient failure) uses the current expected
                            # nonce rather than a stale one.
                            current_nonce = self._resolve_nonce(rpc, resolved.sender)
                            tx_obj = tx_build.transfer(
                                from_addr=resolved.sender,
                                to_addr=address,
                                amount=amount,
                                nonce=current_nonce,
                                gas_limit=None,
                                max_fee=payout_fee,
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
                        nonce=current_nonce if current_nonce is not None else 0,
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
                            "nonce": current_nonce,
                            "error": str(exc),
                        },
                    )
                    if "insufficient" in str(exc).lower():
                        break
        return sent
