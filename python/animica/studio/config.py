"""Runtime configuration for Animica Studio.

All knobs are environment-driven so the SDK behaves identically whether it's
imported from a notebook, the ``animica studio`` CLI, or a provider worker.

Resolution order for every value: explicit argument > environment > default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# The mainnet AICF treasury — the on-chain sink that escrowed ANM is sent to
# before a job is admitted (the proven chat-bridge payment leg). Overridable so
# testnets / local devnets point elsewhere.
DEFAULT_TREASURY = (
    "anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt"
)
DEFAULT_RPC_URL = "http://127.0.0.1:8545"
DEFAULT_CHAIN_ID = 1
DEFAULT_SANDBOX_URL = "http://127.0.0.1:8004"

# ANM has 9 decimal places on Animica; 1 ANM == 1e9 "nanos" (smallest unit).
NANOS_PER_ANM = 1_000_000_000


class Mode:
    """How `.remote()` is satisfied."""

    #: Run in a local sandbox subprocess on this machine. No chain, no payment.
    #: This is what makes the SDK demoable with zero infrastructure.
    LOCAL = "local"
    #: Escrow ANM and dispatch to the AICF broker / provider fleet.
    REMOTE = "remote"
    #: Use REMOTE when a node RPC is reachable and a wallet is configured,
    #: otherwise fall back to LOCAL (the friendly default for `animica studio run`).
    AUTO = "auto"


@dataclass
class StudioConfig:
    """Resolved Studio settings."""

    mode: str = Mode.AUTO
    rpc_url: str = DEFAULT_RPC_URL
    chain_id: int = DEFAULT_CHAIN_ID
    treasury: str = DEFAULT_TREASURY
    sandbox_url: str = DEFAULT_SANDBOX_URL
    #: Path/name of the wallet used to escrow ANM (resolved by omni_sdk / animica wallet).
    wallet: Optional[str] = None
    #: Default billing rail for functions that don't specify one.
    billing: str = "onchain"  # "onchain" | "credits" | "none"
    #: Per-call price ceiling in ANM; a call quoted above this aborts before paying.
    max_price_anm: float = 100.0
    #: Default function timeout (seconds) when a function doesn't set one.
    default_timeout_s: int = 300
    #: Hard cap on local-mode concurrency for `.map()`.
    local_concurrency: int = field(default_factory=lambda: min(32, (os.cpu_count() or 4)))

    @classmethod
    def from_env(cls, **overrides) -> "StudioConfig":
        def pick(key_env: str, default, cast=str):
            val = os.environ.get(key_env)
            return cast(val) if val not in (None, "") else default

        cfg = cls(
            mode=pick("ANIMICA_STUDIO_MODE", Mode.AUTO),
            rpc_url=pick("ANIMICA_RPC_URL", DEFAULT_RPC_URL),
            chain_id=pick("ANIMICA_CHAIN_ID", DEFAULT_CHAIN_ID, int),
            treasury=pick("ANIMICA_AICF_TREASURY_ADDRESS", DEFAULT_TREASURY),
            sandbox_url=pick("ANIMICA_STUDIO_SANDBOX_URL", DEFAULT_SANDBOX_URL),
            wallet=os.environ.get("ANIMICA_STUDIO_WALLET") or os.environ.get("ANIMICA_WALLET"),
            billing=pick("ANIMICA_STUDIO_BILLING", "onchain"),
            max_price_anm=pick("ANIMICA_STUDIO_MAX_PRICE_ANM", 100.0, float),
            default_timeout_s=pick("ANIMICA_STUDIO_TIMEOUT_S", 300, int),
        )
        for k, v in overrides.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


def anm_to_nanos(anm: float) -> int:
    """Convert a human ANM amount to integer nanos (smallest unit)."""
    return int(round(anm * NANOS_PER_ANM))


def nanos_to_anm(nanos: int) -> float:
    """Convert integer nanos back to a human ANM amount."""
    return nanos / NANOS_PER_ANM
