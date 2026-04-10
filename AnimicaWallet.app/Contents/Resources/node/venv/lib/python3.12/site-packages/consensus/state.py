"""
In-memory consensus state (tests & simulations)
===============================================

This module intentionally keeps *no* persistence concerns. It offers a compact
state container that other consensus tests/simulations can instantiate, poke,
and inspect without bringing up the full node stack.

What it tracks
--------------
- Chain/consensus parameters relevant to retargeting.
- Current Θ (theta) in "micro-units" (see consensus.types) as an integer.
- Tip (height, hash, timestamp) and a tiny header index for parent linkage.
- A rolling window of timestamps + an EMA of Δt for stable retargeting.
- A best-effort nullifier set (for tests that want to simulate reuse).
- Policy roots (alg-policy, PoIES policy) as bytes, for quick equality checks.

What it *doesn't* do
--------------------
- It does not perform full header validation. Use consensus.validator for that.
- It does not store blocks/transactions or perform fork choice beyond "set_tip".

Fixed-point & determinism
-------------------------
All arithmetic is integer-only (ppm / SCALE) and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .window import (PPM, SCALE, WindowSpec, apply_ratio_int, clamp_retgt_ppm,
                     diffs_from_timestamps, ema_interval_update,
                     ema_interval_value, lambda_from_ema_interval,
                     observed_lambda_from_timestamps, retarget_ratio_ppm)

# ------------------------------ Type shorthands ------------------------------


@dataclass(frozen=True)
class Tip:
    height: int
    hash: bytes
    timestamp: int  # seconds since epoch (monotone non-decreasing per chain rules)


@dataclass(frozen=True)
class PolicyRoots:
    alg_policy_root: (
        bytes  # root for post-quantum alg-policy (see spec/alg_policy.schema.json)
    )
    poies_policy_root: bytes  # root for PoIES policy (see spec/poies_policy.yaml)


@dataclass(frozen=True)
class RetargetParams:
    """
    Retargeting control parameters.

    - target_interval_sec: desired mean inter-block interval.
    - up_ppm / down_ppm: asymmetric clamps on multiplicative step per retarget.
      Example: up=1_050_000 (allow +5%), down=950_000 (allow -5%).
    - ema_shift: EMA window = 2^ema_shift samples (integer-only filter).
    - window: a sampling window for "snapshot" estimators (harmonic/mean).
    """

    target_interval_sec: int = 10
    up_ppm: int = 1_050_000
    down_ppm: int = 950_000
    ema_shift: int = 8
    window: WindowSpec = WindowSpec(size_blocks=60, include_tip=True)


@dataclass
class InMemoryConsensusState:
    """
    Small consensus state snapshot for tests/sims.

    theta_micro:
        Acceptance threshold Θ in "micro-units" (an integer). The exact
        interpretation depends on the math in consensus.math and scorer, but
        for retargeting we treat Θ as *proportional* to difficulty: higher Θ
        → harder to cross acceptance.

    chain_id:
        CAIP-2 chain id integer (animica:1 → 1, etc.)

    policy_roots:
        Expected policy roots (alg-policy, PoIES). Tests can mutate these to
        simulate upgrades.

    Notes on timestamps:
        We maintain *all* seen timestamps since genesis in order (for simplicity)
        and also track an EMA accumulator for Δt to reduce sensitivity to
        outliers and small reorgs in synthetic tests.
    """

    chain_id: int
    theta_micro: int
    policy_roots: PolicyRoots
    retarget: RetargetParams = field(default_factory=RetargetParams)

    # Tip & header index (minimal: only what's needed for sims)
    tip: Tip = field(init=False)
    headers: Dict[bytes, Tip] = field(default_factory=dict)  # hash -> Tip
    parents: Dict[bytes, bytes] = field(
        default_factory=dict
    )  # child_hash -> parent_hash

    # Timing / retarget stats
    timestamps: List[int] = field(default_factory=list)  # append-only (tests are small)
    ema_dt_scaled: int = 0  # EMA accumulator (scaled by 2^ema_shift)

    # Nullifiers for tests (value -> first_seen_height)
    nullifiers: Dict[bytes, int] = field(default_factory=dict)

    # Genesis initializer is provided separately for clarity
    def init_from_genesis(self, genesis_hash: bytes, genesis_timestamp: int) -> None:
        """
        Initialize the in-memory state with a given genesis header.
        """
        self.tip = Tip(
            height=0, hash=bytes(genesis_hash), timestamp=int(genesis_timestamp)
        )
        self.headers[self.tip.hash] = self.tip
        self.timestamps = [self.tip.timestamp]
        # Initialize EMA with an arbitrary "neutral" Δt equal to target interval
        # so the first updates converge smoothly.
        self.ema_dt_scaled = (
            self.retarget.target_interval_sec << self.retarget.ema_shift
        )

    # ------------------------------ Read-only views ------------------------------

    @property
    def theta(self) -> int:
        """Current Θ (micro-units)."""
        return self.theta_micro

    @property
    def head(self) -> Tip:
        """Current tip header view."""
        return self.tip

    def get_header(self, h: bytes) -> Optional[Tip]:
        """Return Tip-like view for a known header hash (or None)."""
        return self.headers.get(h)

    # ------------------------------- Nullifiers ---------------------------------

    def seen_nullifier(self, n: bytes) -> bool:
        """Whether a nullifier value has ever been recorded."""
        return n in self.nullifiers

    def record_nullifier(self, n: bytes, height: Optional[int] = None) -> None:
        """
        Record a nullifier as seen at given height (defaults to current tip+1
        for convenience when called during candidate evaluation).
        """
        if height is None:
            height = self.tip.height + 1
        if n not in self.nullifiers:
            self.nullifiers[n] = int(height)

    # ----------------------------- Tip / headers API -----------------------------

    def _update_time_stats_on_accept(self, timestamp_sec: int) -> None:
        """
        Update timestamp history and Δt EMA when a new block is *accepted* onto
        the canonical head (no fork-choice in this tiny helper).
        """
        ts = int(timestamp_sec)
        if self.timestamps and ts <= self.timestamps[-1]:
            # Enforce monotone increase in tests to avoid zero/negative Δt.
            ts = self.timestamps[-1] + 1
        self.timestamps.append(ts)

        # Update EMA with the latest Δt
        if len(self.timestamps) >= 2:
            dt = ts - self.timestamps[-2]
            if dt <= 0:
                dt = 1
            self.ema_dt_scaled = ema_interval_update(
                self.ema_dt_scaled, dt, self.retarget.ema_shift
            )

    def _retarget_theta(self) -> None:
        """
        Retarget Θ using the EMA of Δt (primary) with a harmonic snapshot
        fallback over the configured window. All math is integer-only.

        Relation:
            If observed interval > target → lower Θ (easier) to speed up blocks.
            If observed interval < target → raise Θ (harder) to slow down blocks.
        """
        # Prefer EMA (stable), fall back to window harmonic if EMA isn't primed yet.
        dt_ema = ema_interval_value(self.ema_dt_scaled, self.retarget.ema_shift)
        if dt_ema <= 0:
            # Fallback: compute from recent timestamps in the configured window
            w = self.retarget.window.size_blocks
            if len(self.timestamps) >= 2:
                ts_slice = self.timestamps[-(w + 1) :]
                dts = diffs_from_timestamps(ts_slice)
                observed = max(1, int(sum(dts) // max(1, len(dts))))
            else:
                observed = self.retarget.target_interval_sec
        else:
            observed = dt_ema

        ratio_ppm = retarget_ratio_ppm(self.retarget.target_interval_sec, observed)
        ratio_ppm = clamp_retgt_ppm(
            ratio_ppm, self.retarget.up_ppm, self.retarget.down_ppm
        )
        self.theta_micro = apply_ratio_int(self.theta_micro, ratio_ppm)

    def accept_header(
        self,
        header_hash: bytes,
        parent_hash: bytes,
        timestamp_sec: int,
    ) -> Tip:
        """
        Append-accept a header extending the current tip (no fork choice).
        Returns the new Tip.

        NOTE: For richer scenarios (forks, validation), pair this with
        consensus.validator + consensus.fork_choice in higher-level tests.
        """
        # Minimal linkage check against the current tip
        if parent_hash != self.tip.hash:
            raise ValueError(
                "accept_header: parent does not match current tip (no fork handling here)"
            )

        new_height = self.tip.height + 1
        new_tip = Tip(
            height=new_height, hash=bytes(header_hash), timestamp=int(timestamp_sec)
        )

        # Update indices
        self.headers[new_tip.hash] = new_tip
        self.parents[new_tip.hash] = parent_hash

        # Update time stats & retarget
        self._update_time_stats_on_accept(new_tip.timestamp)
        self._retarget_theta()

        # Advance tip
        self.tip = new_tip
        return new_tip

    # ------------------------------- Diagnostics --------------------------------

    def observed_lambda(self) -> int:
        """
        Current observed λ (blocks/sec) in SCALE fixed-point, derived from the
        EMA accumulator when available, otherwise from a harmonic snapshot of
        the last `window.size_blocks` intervals.
        """
        dt_ema = ema_interval_value(self.ema_dt_scaled, self.retarget.ema_shift)
        if dt_ema > 0:
            return lambda_from_ema_interval(self.ema_dt_scaled, self.retarget.ema_shift)
        # Snapshot fallback
        if len(self.timestamps) < 2:
            return 0
        ts_slice = self.timestamps[-(self.retarget.window.size_blocks + 1) :]
        return observed_lambda_from_timestamps(ts_slice, method="harmonic")

    def snapshot(self) -> dict:
        """
        Small JSON-ish snapshot for assertions in tests.
        """
        return {
            "chainId": self.chain_id,
            "thetaMicro": self.theta_micro,
            "tip": {
                "height": self.tip.height,
                "hash": self.tip.hash.hex(),
                "timestamp": self.tip.timestamp,
            },
            "timestamps": self.timestamps[-10:],  # last 10 for readability
            "emaDt": ema_interval_value(self.ema_dt_scaled, self.retarget.ema_shift),
            "lambda": self.observed_lambda() / SCALE if SCALE else 0.0,
            "policyRoots": {
                "algPolicyRoot": self.policy_roots.alg_policy_root.hex(),
                "poiesPolicyRoot": self.policy_roots.poies_policy_root.hex(),
            },
            "retarget": {
                "targetIntervalSec": self.retarget.target_interval_sec,
                "upPpm": self.retarget.up_ppm,
                "downPpm": self.retarget.down_ppm,
                "emaShift": self.retarget.ema_shift,
                "windowSize": self.retarget.window.size_blocks,
            },
            "nullifiersCount": len(self.nullifiers),
        }


# -------------------------------- Self-test ----------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Tiny smoke demo
    roots = PolicyRoots(alg_policy_root=b"\xaa" * 32, poies_policy_root=b"\xbb" * 32)
    st = InMemoryConsensusState(chain_id=1, theta_micro=5_000_000, policy_roots=roots)
    st.init_from_genesis(genesis_hash=b"\x00" * 32, genesis_timestamp=1_700_000_000)

    # Synthesize ~10s blocks with jitter
    import random

    ts = st.tip.timestamp
    rng = random.Random(42)
    for _ in range(30):
        ts += max(5, int(10 + rng.uniform(-2.5, 2.5)))
        hh = bytes([_ % 256]) * 32
        st.accept_header(hh, st.head.hash, ts)
        if (_ + 1) % 10 == 0:
            snap = st.snapshot()
            print(
                f"After {st.head.height} blocks: Θ={snap['thetaMicro']}, λ≈{snap['lambda']:.4f} blk/s, Δt_ema={snap['emaDt']}s"
            )
