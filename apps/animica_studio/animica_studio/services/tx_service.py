from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from animica_studio.services.error_format import safe_str
from animica_studio.services.job_runner import resolve_animica_cli_program_and_env, run_cli_blocking
from animica_studio.storage.config import Config
from animica_studio.models.wallet_models import ANM_BASE_UNITS, ANM_DECIMALS

_TX_HASH_RE = re.compile(r"0x[a-fA-F0-9]{64}")
_ANIM_ADDR_RE = re.compile(r"^anim1[ac-hj-np-z02-9]{10,}$")
_HEX_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass
class TxServiceResult:
    ok: bool
    tx_hash: str | None = None
    error: str | None = None
    details: str | None = None
    command: list[str] | None = None
    stdout: str = ""
    stderr: str = ""


class TxService:
    """Canonical Studio tx send service.

    Canonical pipeline selected from python/animica/cli/tx.py::send:
      1) CLI resolves chain/nonce/fees and builds tx body locally.
      2) CLI signs locally from wallets.json (or explicit keys), with PQ alg_id.
      3) CLI submits via RPC method tx.sendRawTransaction.

    Studio therefore uses the same canonical CLI command instead of reimplementing
    protocol serialization/signing.
    """

    def __init__(self, config: Config, timeout_s: int = 30) -> None:
        self._config = config
        self._timeout_s = timeout_s

    @staticmethod
    def validate_to_address(to_addr: str) -> bool:
        text = (to_addr or "").strip()
        return bool(_ANIM_ADDR_RE.match(text) or _HEX_ADDR_RE.match(text))

    @staticmethod
    def parse_amount(amount_str: str, decimals: int = ANM_DECIMALS) -> int:
        raw = (amount_str or "").strip().upper().replace(",", ".")
        for suffix in (" ANM", "ANM"):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)].strip()
        if not raw:
            raise ValueError("Amount is required")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid amount: {amount_str!r}") from exc
        if value <= 0:
            raise ValueError("Amount must be greater than zero")
        wei = value * Decimal(10 ** decimals)
        if wei != wei.to_integral_value():
            raise ValueError(f"Amount has more than {decimals} decimal places")
        return int(wei)

    @staticmethod
    def _amount_to_anm_string(amount_wei: int) -> str:
        anm = (Decimal(amount_wei) / Decimal(ANM_BASE_UNITS)).normalize()
        return format(anm, "f")

    def send_via_cli(
        self,
        *,
        from_addr: str,
        to_addr: str,
        amount_wei: int,
        rpc_url: str,
        chain_id: int,
    ) -> TxServiceResult:
        start = time.time()
        sub_args = [
            "tx",
            "send",
            "--from",
            from_addr,
            "--to",
            to_addr,
            "--value",
            self._amount_to_anm_string(amount_wei),
            "--rpc-url",
            rpc_url,
            "--chain-id",
            str(chain_id),
        ]
        cmd = sub_args  # kept for result reporting

        try:
            program, base_args, env = resolve_animica_cli_program_and_env(self._config)
            argv = [program, *base_args, *sub_args]
            merged_env = dict(os.environ)
            merged_env.update(env)
            res = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                stdin=subprocess.DEVNULL,
                env=merged_env,
            )
        except TimeoutError as exc:
            return TxServiceResult(ok=False, error=f"Send timed out after {self._timeout_s}s", details=safe_str(exc), command=cmd)
        except Exception as exc:  # noqa: BLE001
            return TxServiceResult(ok=False, error="Failed to execute tx send", details=safe_str(exc), command=cmd)

        out = (res.stdout or "") + "\n" + (res.stderr or "")
        tx_match = _TX_HASH_RE.search(out)
        tx_hash = tx_match.group(0) if tx_match else None

        if res.returncode != 0:
            return TxServiceResult(
                ok=False,
                error=(res.stderr or res.stdout or f"tx send failed (exit {res.returncode})").strip(),
                details=f"exit={res.returncode} elapsed_ms={int((time.time()-start)*1000)}",
                command=cmd,
                stdout=res.stdout or "",
                stderr=res.stderr or "",
            )

        if not tx_hash:
            return TxServiceResult(
                ok=False,
                error="Transaction submitted but no tx hash was found in CLI output",
                details="Ensure animica tx send prints tx hash",
                command=cmd,
                stdout=res.stdout or "",
                stderr=res.stderr or "",
            )

        return TxServiceResult(
            ok=True,
            tx_hash=tx_hash,
            command=cmd,
            stdout=res.stdout or "",
            stderr=res.stderr or "",
        )
