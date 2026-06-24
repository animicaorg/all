"""The ANM billing leg — pricing a function run and escrowing payment.

v1 reuses the *proven* chat-bridge money path: sign a value transfer to the AICF
treasury, broadcast it, and hand the ``payment_tx_hash`` to the broker, which
admits the job only once the transfer has landed on-chain. Payout to the worker
is computed by the existing AICF split math at settlement.

Pricing bridges Modal-style resource-seconds into AICF "units". The default
schedule is intentionally conservative and lives in one place so it's easy to
tune or replace with ``aicf.estimateJobCost`` (preferred when a node is up).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import StudioConfig, anm_to_nanos, nanos_to_anm
from .errors import BillingError, InsufficientFundsError

# Coarse per-second prices in ANM. Real quotes come from aicf.estimateJobCost;
# these are the offline fallback so a quote is always available.
PRICE_CPU_SEC_ANM = 0.0001
PRICE_GPU_SEC_ANM = 0.01
PRICE_MEM_GB_SEC_ANM = 0.00002
MIN_PRICE_ANM = 0.0001


@dataclass
class Quote:
    cost_nanos: int
    cost_anm: float
    source: str  # "broker" | "local-estimate"
    units: int = 0


def units_for_function(cpu_s: float, gpu_s: float = 0.0, mem_gb_s: float = 0.0) -> int:
    """Map resource-seconds to integer AICF units (1 unit ~= 1 CPU-second-eq)."""
    units = cpu_s + gpu_s * 100.0 + mem_gb_s * 0.2
    return max(1, int(round(units)))


def local_quote(*, est_seconds: float, gpu: bool, mem_gb: float) -> Quote:
    cpu_s = est_seconds if not gpu else 0.0
    gpu_s = est_seconds if gpu else 0.0
    anm = (
        cpu_s * PRICE_CPU_SEC_ANM
        + gpu_s * PRICE_GPU_SEC_ANM
        + mem_gb * est_seconds * PRICE_MEM_GB_SEC_ANM
    )
    anm = max(anm, MIN_PRICE_ANM)
    return Quote(
        cost_nanos=anm_to_nanos(anm),
        cost_anm=anm,
        source="local-estimate",
        units=units_for_function(cpu_s, gpu_s, mem_gb * est_seconds),
    )


def quote_for(function, client=None) -> Quote:
    """Best-available quote for one invocation of ``function``."""
    gpu = bool(function.gpu)
    mem_gb = _parse_mem_gb(function.memory)
    est_seconds = float(function.timeout or 60) * 0.25  # assume ~quarter of budget
    if client is not None:
        try:
            spec = {
                "class": "function_compute",
                "est_seconds": est_seconds,
                "gpu": gpu,
                "mem_gb": mem_gb,
            }
            res = client.estimate_cost(spec)
            cost_anm = float(res.get("cost_animica", 0.0))
            if cost_anm > 0:
                return Quote(anm_to_nanos(cost_anm), cost_anm, "broker", int(res.get("units", 0)))
        except Exception:
            pass  # fall back to local estimate
    return local_quote(est_seconds=est_seconds, gpu=gpu, mem_gb=mem_gb)


def escrow(config: StudioConfig, quote: Quote, *, signer=None) -> str:
    """Escrow ``quote`` ANM to the treasury; return the payment tx hash.

    Uses ``omni_sdk`` for PQ signing + submission. Raises a clear ``BillingError``
    if the wallet/SDK isn't available so callers can fall back to ``billing='none'``
    in dev rather than failing opaquely.
    """
    if quote.cost_anm > config.max_price_anm:
        raise BillingError(
            f"quote {quote.cost_anm:.6f} ANM exceeds max_price_anm={config.max_price_anm}; "
            f"raise it or use a smaller image/timeout"
        )
    try:
        from omni_sdk.rpc.http import HttpClient  # type: ignore
        from omni_sdk.tx import build as tx_build  # type: ignore
        from omni_sdk.tx import send as tx_send  # type: ignore
        from omni_sdk.tx import signing as tx_signing  # type: ignore
        from omni_sdk.wallet.signer import PQSigner  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional
        raise BillingError(
            "on-chain escrow needs omni-sdk (`pip install omni-sdk`) and a wallet. "
            "For local dev set billing='none' or ANIMICA_STUDIO_MODE=local."
        ) from exc

    if signer is None:
        raise BillingError(
            "no wallet signer configured. Set ANIMICA_STUDIO_WALLET or pass a signer."
        )

    rpc = HttpClient(url=config.rpc_url)
    sender = signer.address
    balance = int(rpc.call("state.getBalance", [sender]))
    if balance < quote.cost_nanos:
        raise InsufficientFundsError(
            f"balance {nanos_to_anm(balance):.4f} ANM < quote {quote.cost_anm:.4f} ANM"
        )
    nonce = int(rpc.call("state.getNonce", [sender]))
    tx = tx_build.transfer(
        from_addr=sender,
        to_addr=config.treasury,
        amount=quote.cost_nanos,
        nonce=nonce,
        gas_limit=21000,
        max_fee=2_000_000,
        chain_id=config.chain_id,
    )
    signed = tx_signing.sign_transaction_with_rpc_context(tx, signer, chain_id=config.chain_id, rpc=rpc)
    tx_hash = tx_send.submit_raw(rpc, signed.raw_tx)
    return tx_hash


def _parse_mem_gb(memory) -> float:
    if memory is None:
        return 0.5
    if isinstance(memory, (int, float)):
        return float(memory) / 1024.0 if memory > 64 else float(memory)  # MB heuristic
    s = str(memory).strip().lower()
    try:
        if s.endswith("gb") or s.endswith("g"):
            return float(s.rstrip("gb"))
        if s.endswith("mb") or s.endswith("m"):
            return float(s.rstrip("mb")) / 1024.0
        return float(s)
    except ValueError:
        return 0.5
