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
import json
import time
import uuid
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from animica_studio.models.profile_models import RpcProfile
from animica_studio.models.wallet_models import (
    Account,
    BalanceState,
    PendingTx,
    format_amount,
)
from animica_studio.services.balance_service import BalanceService
from animica_studio.services.cli_capabilities import get_cli_ops
from animica_studio.services.cli_ops import CliOperation
from animica_studio.services.error_format import format_rpc_error, safe_str
from animica_studio.services.job_runner import JobHandle, JobRunner, resolve_animica_cli_program_and_env
from animica_studio.services.signer_service import SignerService, SigningNotAvailableError
from animica_studio.services.tx_builder import estimate_fee
from animica_studio.storage.config import Config, save_config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_MAX_PENDING_TXS = 100  # keep only the last N pending/sent txs; older entries are trimmed on save
_BALANCE_CONCURRENCY = 4
_DEFAULT_DECIMALS = 18
_WALLET_LABEL_RE = re.compile(r"^[A-Za-z0-9 _-]{1,32}$")
_WALLET_ADDRESS_RE = re.compile(r"Address:\s*(anim1[ac-hj-np-z02-9]{10,})")
_TX_HASH_RE = re.compile(r"0x[a-fA-F0-9]{64}")
_ANM_BALANCE_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*ANM", re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_SCHEME_ALIASES = {
    "dilithium3": "dilithium3",
    "sphincs128s": "sphincs_shake_128s",
    "sphincs_shake_128s": "sphincs_shake_128s",
}
_SCHEME_LABELS = {
    "dilithium3": "Dilithium3",
    "sphincs_shake_128s": "SPHINCS+ 128s",
}


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
        self._balance_service = BalanceService()

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

        scheme = _SCHEME_ALIASES.get(sig_scheme.strip().lower())
        if scheme is None:
            raise ValueError("Signature scheme must be dilithium3 or sphincs_shake_128s")
        return clean_label, scheme

    def build_create_wallet_args(
        self,
        label: str,
        sig_scheme: str = "dilithium3",
        *,
        allow_insecure_fallback: bool = False,
    ) -> tuple[list[str], str, str]:
        """Build CLI args for wallet creation and return normalized values."""
        clean_label, scheme = self.validate_wallet_create_request(label, sig_scheme)
        ops = get_cli_ops(self._config)
        args = ops.build(
            CliOperation.WALLET_CREATE,
            {
                "label": clean_label,
                "alg": scheme,
                "allow_insecure_fallback": allow_insecure_fallback,
            },
        )
        return args, clean_label, scheme

    def parse_created_wallet_address(self, stdout: str) -> str:
        """Extract created wallet address from legacy CLI output."""
        match = _WALLET_ADDRESS_RE.search(stdout)
        if not match:
            raise RuntimeError("Wallet was created but Studio could not read the new address from CLI output.")
        return match.group(1)

    def _wallet_store_path(self) -> str:
        return os.environ.get("ANIMICA_WALLETS_FILE") or str((Path.home() / ".animica" / "wallets.json"))

    def _load_wallet_store_addresses(self) -> set[str]:
        path = self._wallet_store_path()
        try:
            with open(path, encoding="utf-8") as f:
                parsed = json.load(f)
            wallets = parsed.get("wallets") if isinstance(parsed, dict) else []
            if not isinstance(wallets, list):
                return set()
            return {str(w.get("address", "")).strip() for w in wallets if isinstance(w, dict) and w.get("address")}
        except FileNotFoundError:
            return set()
        except Exception as exc:  # noqa: BLE001
            log.warning("WalletService: failed to load wallet store path=%s error=%s", path, exc)
            return set()

    def resolve_created_wallet_address(self, known_addresses: set[str]) -> str:
        current = self._load_wallet_store_addresses()
        new_addresses = current - known_addresses
        if len(new_addresses) == 1:
            return next(iter(new_addresses))
        if len(new_addresses) > 1:
            return sorted(new_addresses)[-1]
        raise RuntimeError("Wallet was created but Studio could not resolve a new address from wallet store.")

    def start_create_wallet(
        self,
        runner: JobRunner,
        label: str,
        sig_scheme: str = "dilithium3",
        *,
        allow_insecure_fallback: bool = False,
        timeout_s: int = 15,
    ) -> tuple[JobHandle, str, str, set[str]]:
        wallet_args, clean_label, scheme = self.build_create_wallet_args(
            label,
            sig_scheme,
            allow_insecure_fallback=allow_insecure_fallback,
        )
        if "--label" not in wallet_args:
            raise RuntimeError("wallet create command is missing required --label flag")
        program, base_args, resolved_env = resolve_animica_cli_program_and_env(self._config)
        argv = [program, *base_args, *wallet_args]
        log.info("WalletService: resolved CLI path=%s", program)
        log.info("WalletService: create wallet argv=%r", argv)
        known_addresses = self._load_wallet_store_addresses()
        job = runner.run_cli(argv, env=resolved_env, timeout_s=timeout_s)
        return job, clean_label, scheme, known_addresses

    def store_created_wallet(self, label: str, address: str, sig_scheme: str) -> Account:
        """Persist a wallet record from validated CLI creation output."""
        account = Account(label=label, address=address, sig_scheme=sig_scheme)
        raw: list[dict[str, Any]] = self._config.accounts  # type: ignore[attr-defined]
        raw.append(account.to_dict())
        save_config(self._config)
        return account

    def create_wallet(
        self,
        label: str,
        sig_scheme: str = "dilithium3",
        *,
        allow_insecure_fallback: bool = False,
    ) -> Account:
        """Create a new wallet via Animica CLI and persist it."""
        wallet_args, clean_label, scheme = self.build_create_wallet_args(
            label,
            sig_scheme,
            allow_insecure_fallback=allow_insecure_fallback,
        )
        if "--label" not in wallet_args:
            raise RuntimeError("wallet create command is missing required --label flag")
        try:
            program, base_args, resolved_env = resolve_animica_cli_program_and_env(self._config)
        except FileNotFoundError as exc:
            raise RuntimeError(str(exc)) from exc

        cmd = [program, *base_args, *wallet_args]
        started = time.perf_counter()
        log.info("WalletService: create wallet requested label=%s scheme=%s argv=%r", clean_label, scheme, cmd)

        known_addresses = self._load_wallet_store_addresses()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
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

        address = self.resolve_created_wallet_address(known_addresses)
        account = self.store_created_wallet(clean_label, address, scheme)
        elapsed = int((time.perf_counter() - started) * 1000)
        log.info("WalletService: create wallet success label=%s scheme=%s address=%s duration_ms=%s", clean_label, scheme, address, elapsed)
        return account

    @staticmethod
    def scheme_label(sig_scheme: str) -> str:
        """Return a friendly display label for a stored wallet signature scheme ID."""
        raw = (sig_scheme or "").strip().lower()
        if not raw or raw == "unknown":
            return "Unknown"
        clean = _SCHEME_ALIASES.get(raw, raw)
        return _SCHEME_LABELS.get(clean, sig_scheme)

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
        self._balance_service.clear()

    def get_cached_balance(self, address: str) -> BalanceState | None:
        """Return cached balance for *address*, or None.

        The cache is keyed by address only. Call :meth:`clear_balance_cache`
        when the active profile changes to prevent stale cross-profile balances.
        """
        return self._balances.get(address)

    def fetch_balance(self, address: str, rpc_url: str, profile: RpcProfile | None = None) -> BalanceState:
        """Fetch balance for a single *address* using RPC with explorer fallback."""
        effective_profile = profile or RpcProfile.make_default_remote()
        if rpc_url:
            effective_profile.rpc_url = rpc_url
        state = self._balance_service.get_balance(address, effective_profile, self._decimals())
        self._balances[address] = state
        return state

    def _parse_wallet_show_balance(self, output: str) -> tuple[int, str]:
        """Extract raw + formatted balance from ``animica wallet show`` output."""
        text = (output or "").replace("\x00", "").strip()
        if not text:
            raise RuntimeError("wallet show returned no output")

        parsed: dict[str, Any] | None = None
        try:
            candidate = text
            if not candidate.startswith("{"):
                match = _JSON_OBJECT_RE.search(candidate)
                if match:
                    candidate = match.group(0)
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                parsed = loaded
        except json.JSONDecodeError:
            parsed = None

        if parsed:
            raw_int: int | None = None
            for key in ("balance_confirmed", "balance", "available_balance"):
                maybe_raw = parsed.get(key)
                if isinstance(maybe_raw, str):
                    maybe_raw = maybe_raw.strip()
                    if maybe_raw.startswith("0x"):
                        try:
                            raw_int = int(maybe_raw, 16)
                            break
                        except ValueError:
                            continue
                    try:
                        raw_int = int(maybe_raw)
                        break
                    except ValueError:
                        continue
                if isinstance(maybe_raw, (int, float)):
                    raw_int = int(maybe_raw)
                    break

            if raw_int is not None:
                formatted = str(
                    parsed.get("balance_confirmed_formatted")
                    or parsed.get("balance_formatted")
                    or format_amount(raw_int, self._decimals())
                ).strip()
                return raw_int, formatted

            formatted = str(
                parsed.get("balance_confirmed_formatted")
                or parsed.get("balance_formatted")
                or ""
            ).strip()
            if formatted and "ANM" in formatted.upper():
                return 0, formatted

        match = _ANM_BALANCE_RE.search(text)
        if match:
            amount = match.group(1)
            return 0, f"{amount} ANM"

        raise RuntimeError("Unable to parse ANM balance from wallet show output")

    def refresh_all_balances(
        self,
        rpc_url: str,
        cancel: CancelToken | None = None,
        profile: RpcProfile | None = None,
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
                pool.submit(self.fetch_balance, acc.address, rpc_url, profile): acc.address
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
        # Keep fee/nonce metadata for history UI; CLI derives the canonical tx details.
        nonce = 0
        _gas_limit = gas_limit or 21_000
        _gas_price = gas_price_wei or 10 ** 9
        fee = estimate_fee(_gas_limit, _gas_price)

        # Decimal ANM value (no scientific notation) for: animica tx send --value
        amount_anm = (Decimal(amount_wei) / Decimal(10 ** 18)).normalize()
        amount_arg = format(amount_anm, "f")

        ptx = PendingTx(
            from_addr=from_addr,
            to_addr=to_addr,
            amount_wei=amount_wei,
            nonce=nonce,
            fee=fee,
            memo=memo,
            raw_tx_hex="",
            status="SENT",
            created_ts=time.time(),
            updated_ts=time.time(),
        )

        try:
            program, base_args, resolved_env = resolve_animica_cli_program_and_env(self._config)
            # Keep the send invocation aligned with the expected wallet UX flow:
            # clicking "Send" should fire the same CLI command users run manually.
            cmd = [
                program,
                *base_args,
                "tx",
                "send",
                "--from",
                from_addr,
                "--to",
                to_addr,
                "--value",
                amount_arg,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **resolved_env} if resolved_env else None,
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "Unknown CLI error").strip()
                raise RuntimeError(details)

            output = "\n".join([result.stdout or "", result.stderr or ""])
            match = _TX_HASH_RE.search(output)
            ptx.tx_hash = match.group(0) if match else None
            ptx.status = "PENDING"
            log.info("WalletService: submitted tx via CLI hash=%s", ptx.tx_hash or "(not reported)")
        except Exception as exc:  # noqa: BLE001
            ptx.status = "FAILED"
            ptx.error = format_rpc_error(exc)
            log.error("WalletService: tx send CLI failed: %s", exc)

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
        return ""
