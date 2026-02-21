"""Wallet service for Animica Studio.

Responsibilities:
- Load / save accounts and pending transactions from/to Config.
- Fetch balances per address via RPC (with fallbacks).
- Fetch pending nonce via RPC.
- Build, sign, and submit transactions.
- Receipt polling for pending txs.
- Cancellation support for refresh cycles.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from animica_studio.models.wallet_models import (
    Account,
    BalanceState,
    PendingTx,
    format_amount,
)
from animica_studio.services.error_format import format_rpc_error, safe_str
from animica_studio.services.job_runner import resolve_animica_cli_program_and_env
from animica_studio.services.signer_service import SignerService, SigningNotAvailableError
from animica_studio.services.tx_builder import (
    build_transfer_tx,
    encode_to_cbor_hex,
    estimate_fee,
)
from animica_studio.storage.config import Config, save_config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_MAX_PENDING_TXS = 100  # keep only the last N pending/sent txs; older entries are trimmed on save
_BALANCE_CONCURRENCY = 4
_DEFAULT_DECIMALS = 18
_WALLET_LABEL_RE = re.compile(r"^[A-Za-z0-9 _-]{1,32}$")
_WALLET_ADDRESS_RE = re.compile(r"Address:\s*(anim1[ac-hj-np-z02-9]{10,})")


class WalletService:
    """High-level wallet operations backed by :class:`~animica_studio.storage.config.Config`.

    Parameters
    ----------
    config:
        The application configuration object.
    signer:
        Optional :class:`~animica_studio.services.signer_service.SignerService`.
        Created automatically if not provided.
    """

    def __init__(
        self,
        config: Config,
        signer: SignerService | None = None,
    ) -> None:
        self._config = config
        self._signer = signer or SignerService()
        # In-memory balance cache keyed by address
        self._balances: dict[str, BalanceState] = {}

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    def list_accounts(self) -> list[Account]:
        """Return all saved accounts."""
        raw: list[dict[str, Any]] = self._config.accounts  # type: ignore[attr-defined]
        return [Account.from_dict(d) for d in raw]

    def get_account(self, account_id: str) -> Account | None:
        """Return account by ID, or None."""
        for acc in self.list_accounts():
            if acc.id == account_id:
                return acc
        return None

    def add_account(self, label: str, address: str) -> Account:
        """Add a new account.

        Raises
        ------
        ValueError
            If the address is already tracked or is empty.
        """
        if not address:
            raise ValueError("Address must not be empty")
        for acc in self.list_accounts():
            if acc.address == address:
                raise ValueError(f"Address {address!r} is already tracked")
        acc = Account(label=label, address=address)
        raw: list[dict[str, Any]] = self._config.accounts  # type: ignore[attr-defined]
        raw.append(acc.to_dict())
        save_config(self._config)
        log.info("WalletService: added account %s (%s)", label, address)
        return acc

    def validate_wallet_create_request(self, label: str, sig_scheme: str = "dilithium3") -> tuple[str, str]:
        """Validate and normalize wallet creation inputs."""
        clean_label = label.strip()
        if not _WALLET_LABEL_RE.match(clean_label):
            raise ValueError(
                "Wallet label must be 1–32 chars and use only letters, numbers, spaces, '_' or '-'."
            )

        scheme = sig_scheme.strip().lower()
        if scheme not in {"dilithium3", "sphincs128s"}:
            raise ValueError("Signature scheme must be dilithium3 or sphincs128s")
        return clean_label, scheme

    def build_create_wallet_args(self, label: str, sig_scheme: str = "dilithium3") -> tuple[list[str], str, str]:
        """Build CLI args for wallet creation and return normalized values."""
        clean_label, scheme = self.validate_wallet_create_request(label, sig_scheme)
        return [
            "wallet",
            "create",
            "--label",
            clean_label,
            "--sig-scheme",
            scheme,
            "--allow-insecure-fallback",
        ], clean_label, scheme

    def parse_created_wallet_address(self, stdout: str) -> str:
        """Extract created wallet address from CLI output."""
        match = _WALLET_ADDRESS_RE.search(stdout)
        if not match:
            raise RuntimeError("Wallet was created but Studio could not read the new address from CLI output.")
        return match.group(1)

    def store_created_wallet(self, label: str, address: str, sig_scheme: str) -> Account:
        """Persist a wallet record from validated CLI creation output."""
        account = Account(label=label, address=address, sig_scheme=sig_scheme)
        raw: list[dict[str, Any]] = self._config.accounts  # type: ignore[attr-defined]
        raw.append(account.to_dict())
        save_config(self._config)
        return account

    def create_wallet(self, label: str, sig_scheme: str = "dilithium3") -> Account:
        """Create a new wallet via Animica CLI and persist it."""
        wallet_args, clean_label, scheme = self.build_create_wallet_args(label, sig_scheme)
        try:
            program, base_args, resolved_env = resolve_animica_cli_program_and_env(self._config)
        except FileNotFoundError as exc:
            raise RuntimeError(str(exc)) from exc

        cmd = [program, *base_args, *wallet_args]
        started = time.perf_counter()
        log.info("WalletService: create wallet requested label=%s scheme=%s argv=%r", clean_label, scheme, cmd)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **resolved_env} if resolved_env else None,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("WalletService: create wallet failed to invoke CLI: %s", exc)
            raise RuntimeError(f"Failed to start wallet CLI: {safe_str(exc)}") from exc

        if result.returncode != 0:
            details = (result.stderr or result.stdout or "Unknown CLI error").strip()
            elapsed = int((time.perf_counter() - started) * 1000)
            log.warning("WalletService: create wallet CLI failed label=%s scheme=%s duration_ms=%s error=%s", clean_label, scheme, elapsed, details)
            raise RuntimeError(details)

        address = self.parse_created_wallet_address(result.stdout)
        account = self.store_created_wallet(clean_label, address, scheme)
        elapsed = int((time.perf_counter() - started) * 1000)
        log.info("WalletService: create wallet success label=%s scheme=%s address=%s duration_ms=%s", clean_label, scheme, address, elapsed)
        return account

    def remove_account(self, account_id: str) -> bool:
        """Remove account by ID.  Returns True if removed."""
        raw: list[dict[str, Any]] = self._config.accounts  # type: ignore[attr-defined]
        before = len(raw)
        raw[:] = [d for d in raw if d.get("id") != account_id]
        if len(raw) < before:
            # Also clean up in-memory balance for that address
            removed = [d for d in self._config.accounts if d.get("id") == account_id]  # type: ignore[attr-defined]
            for d in removed:
                self._balances.pop(d.get("address", ""), None)
            save_config(self._config)
            log.info("WalletService: removed account %s", account_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Balance management
    # ------------------------------------------------------------------

    def clear_balance_cache(self) -> None:
        """Clear the in-memory balance cache.

        Should be called when the active profile changes to prevent stale
        balances from a different RPC endpoint being shown.
        """
        self._balances.clear()

    def get_cached_balance(self, address: str) -> BalanceState | None:
        """Return cached balance for *address*, or None.

        The cache is keyed by address only. Call :meth:`clear_balance_cache`
        when the active profile changes to prevent stale cross-profile balances.
        """
        return self._balances.get(address)

    def fetch_balance(self, address: str, rpc_url: str) -> BalanceState:
        """Fetch balance for a single *address* from the RPC.

        Returns a :class:`BalanceState` with either a value or an error.
        Never raises.
        """
        from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

        try:
            with RpcClient(rpc_url) as c:
                raw = c.get_balance(address)
            decimals = self._decimals()
            state = BalanceState(
                address=address,
                balance_wei=raw,
                formatted=format_amount(raw, decimals),
                updated_ts=time.time(),
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            state = BalanceState(
                address=address,
                balance_wei=0,
                formatted="—",
                updated_ts=time.time(),
                error=format_rpc_error(exc),
            )
            log.warning("WalletService: balance fetch error for %s: %s", address, exc)

        # Store keyed by address — never aliased
        self._balances[address] = state
        return state

    def refresh_all_balances(
        self,
        rpc_url: str,
        cancel: CancelToken | None = None,
    ) -> dict[str, BalanceState]:
        """Fetch balances for all accounts concurrently.

        Parameters
        ----------
        rpc_url:
            The RPC endpoint URL.
        cancel:
            Optional cancellation token.  If cancelled mid-flight, returns
            whatever partial results have been collected.

        Returns a mapping of ``address → BalanceState``.
        """
        accounts = self.list_accounts()
        results: dict[str, BalanceState] = {}

        if not accounts:
            return results

        # Use a thread pool capped at _BALANCE_CONCURRENCY
        with ThreadPoolExecutor(max_workers=_BALANCE_CONCURRENCY) as pool:
            futures = {
                pool.submit(self.fetch_balance, acc.address, rpc_url): acc.address
                for acc in accounts
            }
            for fut in as_completed(futures):
                if cancel and cancel.is_cancelled:
                    log.debug("WalletService: refresh_all_balances cancelled")
                    break
                addr = futures[fut]
                try:
                    results[addr] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    results[addr] = BalanceState(
                        address=addr,
                        error=safe_str(exc),
                        updated_ts=time.time(),
                    )

        return results

    # ------------------------------------------------------------------
    # Nonce
    # ------------------------------------------------------------------

    def fetch_nonce(self, address: str, rpc_url: str) -> int:
        """Fetch the pending nonce for *address* from RPC.

        Raises
        ------
        Exception
            On any RPC error (caller must handle).
        """
        from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

        with RpcClient(rpc_url) as c:
            return c.get_pending_nonce(address)

    # ------------------------------------------------------------------
    # Send flow
    # ------------------------------------------------------------------

    def build_and_send(
        self,
        *,
        rpc_url: str,
        chain_id: int,
        from_addr: str,
        to_addr: str,
        amount_wei: int,
        memo: str | None = None,
        gas_limit: int | None = None,
        gas_price_wei: int | None = None,
    ) -> PendingTx:
        """Build, sign, and submit a transfer transaction.

        Returns a :class:`PendingTx` with status ``SENT`` and the tx hash,
        or status ``FAILED`` with an error message.

        Raises
        ------
        SigningNotAvailableError
            If this is a watch-only account.
        ValueError
            If the transaction cannot be built.
        """
        from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

        # 1. Fetch nonce
        nonce = self.fetch_nonce(from_addr, rpc_url)

        # 2. Estimate fee / gas params
        _gas_limit = gas_limit or 21_000
        _gas_price = gas_price_wei or 10 ** 9
        fee = estimate_fee(_gas_limit, _gas_price)

        # 3. Build tx dict
        tx_dict = build_transfer_tx(
            chain_id=chain_id,
            from_addr=from_addr,
            to_addr=to_addr,
            value_wei=amount_wei,
            nonce=nonce,
            gas_limit=_gas_limit,
            gas_price_wei=_gas_price,
            memo=memo,
        )

        # 4. Sign
        signed_tx = self._signer.sign_tx(tx_dict, from_addr)

        # 5. Encode to hex
        raw_tx_hex = encode_to_cbor_hex(signed_tx)

        # 6. Build PendingTx record
        ptx = PendingTx(
            from_addr=from_addr,
            to_addr=to_addr,
            amount_wei=amount_wei,
            nonce=nonce,
            fee=fee,
            memo=memo,
            raw_tx_hex=raw_tx_hex,
            status="SENT",
            created_ts=time.time(),
            updated_ts=time.time(),
        )

        # 7. Submit
        try:
            with RpcClient(rpc_url) as c:
                tx_hash = c.send_raw_tx(raw_tx_hex)
            ptx.tx_hash = tx_hash
            ptx.status = "PENDING"
            log.info("WalletService: submitted tx %s", tx_hash)
        except Exception as exc:  # noqa: BLE001
            ptx.status = "FAILED"
            ptx.error = format_rpc_error(exc)
            log.error("WalletService: tx submit failed: %s", exc)

        # 8. Persist
        self._save_pending_tx(ptx)
        return ptx

    # ------------------------------------------------------------------
    # Pending tx management
    # ------------------------------------------------------------------

    def list_pending_txs(self, from_addr: str | None = None) -> list[PendingTx]:
        """Return pending txs, optionally filtered by sender address."""
        raw: list[dict[str, Any]] = self._config.pending_txs  # type: ignore[attr-defined]
        txs = [PendingTx.from_dict(d) for d in raw]
        if from_addr:
            txs = [t for t in txs if t.from_addr == from_addr]
        # Newest first
        txs.sort(key=lambda t: t.created_ts, reverse=True)
        return txs

    def _save_pending_tx(self, ptx: PendingTx) -> None:
        raw: list[dict[str, Any]] = self._config.pending_txs  # type: ignore[attr-defined]
        # Update if already tracked (same local_id)
        for i, d in enumerate(raw):
            if d.get("local_id") == ptx.local_id:
                raw[i] = ptx.to_dict()
                save_config(self._config)
                return
        raw.append(ptx.to_dict())
        # Trim to last N
        if len(raw) > _MAX_PENDING_TXS:
            raw[:] = raw[-_MAX_PENDING_TXS:]
        save_config(self._config)

    def poll_receipt(self, ptx: PendingTx, rpc_url: str) -> PendingTx:
        """Check receipt for a PENDING tx.  Updates status in place and persists."""
        if not ptx.tx_hash or ptx.status not in ("PENDING", "SENT"):
            return ptx

        from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

        try:
            with RpcClient(rpc_url) as c:
                result = c.call("tx_getTransactionReceipt", [ptx.tx_hash])
            if result and isinstance(result, dict):
                status_raw = result.get("status", result.get("success"))
                if status_raw is True or status_raw == 1 or status_raw == "0x1":
                    ptx.status = "CONFIRMED"
                elif status_raw is False or status_raw == 0 or status_raw == "0x0":
                    ptx.status = "FAILED"
                    ptx.error = safe_str(result.get("error") or "Transaction reverted")
                ptx.updated_ts = time.time()
                self._save_pending_tx(ptx)
        except Exception as exc:  # noqa: BLE001
            log.debug("WalletService: receipt poll error for %s: %s", ptx.tx_hash, exc)

        return ptx

    # ------------------------------------------------------------------
    # Wallet settings helpers
    # ------------------------------------------------------------------

    def _decimals(self) -> int:
        ws = getattr(self._config, "wallet_settings", {}) or {}
        if isinstance(ws, dict):
            return int(ws.get("decimals", _DEFAULT_DECIMALS))
        return _DEFAULT_DECIMALS

    def explorer_url_for_tx(self, tx_hash: str) -> str:
        return f"{self._explorer_base()}/tx/{tx_hash}"

    def explorer_url_for_address(self, address: str) -> str:
        return f"{self._explorer_base()}/address/{address}"

    def _explorer_base(self) -> str:
        ws = getattr(self._config, "wallet_settings", {}) or {}
        if isinstance(ws, dict):
            url = ws.get("explorer_base_url", "")
            if url:
                return url.rstrip("/")
        return "https://animica.org/explorer"
