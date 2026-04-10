from __future__ import annotations

"""
AICF Treasury — Minting policy
------------------------------

Mints a configured slice of new units to the AICF treasury either:
  • per-block (fixed amount each block), and/or
  • per-epoch (fixed amount on the first block of each epoch, with optional decay and cap)

Design goals:
  • Pure Python, deterministic integer arithmetic (no floats).
  • Storage-agnostic: this module keeps a small tracker that callers can persist
    via `dump()` / `load()` alongside the wider node state.
  • Idempotent per block: calling `on_block(height)` at most once per height minting
    avoids double-minting; epoch mints are guarded by `last_epoch_minted`.

This module *credits* the internal AICF treasury account inside `TreasuryState`
(using a special ProviderId, by default "aicf:treasury"). Higher-level settlement
code later debits from that account to pay providers according to proofs and
split rules.

Typical use
~~~~~~~~~~~
>>> state = TreasuryState()
>>> cfg = MintConfig(per_block=1000, epoch_length=1024, per_epoch=0)
>>> minter = AICFMinter(cfg, state)
>>> minted = minter.on_block(height=12345)  # returns int amount minted this block
>>> snapshot = minter.dump()  # persist alongside node state

"""

from dataclasses import asdict, dataclass
from typing import Dict, Optional

from aicf.aitypes.provider import ProviderId  # type: ignore
from aicf.treasury.state import TreasuryState

# Optional import: use canonical epoch helper if present
try:  # pragma: no cover - trivial import fallback
    from aicf.economics.epochs import \
        epoch_index_for_height as _epoch_for_height  # type: ignore
except Exception:  # pragma: no cover - fallback

    def _epoch_for_height(height: int, epoch_len: int) -> int:
        if epoch_len <= 0:
            raise ValueError("epoch_len must be > 0")
        if height < 0:
            raise ValueError("height must be >= 0")
        return height // epoch_len


PPM = 1_000_000  # parts-per-million for integer decay math


@dataclass
class MintConfig:
    """
    Minting configuration.

    All amounts are in base units (integers).
    - per_block: minted every block, if > 0
    - per_epoch: minted at the first block of each epoch, if > 0
    - epoch_length: number of blocks per epoch (required if per_epoch > 0)
    - start_height: activation height (inclusive)
    - end_height: optional deactivation height (inclusive); None = no end
    - decay_ppm: optional epoch-over-epoch decay on the *per_epoch* amount.
                 For example, 100_000 PPM (10%) decays each epoch by 10%.
    - cap_per_epoch: optional cap for total minted within a single epoch.
                     Useful when combining per_block and per_epoch.
    - treasury_provider_id: destination account inside TreasuryState
    """

    per_block: int = 0
    per_epoch: int = 0
    epoch_length: int = 0
    start_height: int = 0
    end_height: Optional[int] = None
    decay_ppm: int = 0
    cap_per_epoch: Optional[int] = None
    treasury_provider_id: ProviderId = ProviderId("aicf:treasury")

    def validate(self) -> None:
        if self.per_block < 0 or self.per_epoch < 0:
            raise ValueError("per_block/per_epoch must be non-negative")
        if self.per_epoch > 0 and self.epoch_length <= 0:
            raise ValueError("epoch_length must be > 0 when per_epoch > 0")
        if not (0 <= self.decay_ppm <= PPM):
            raise ValueError("decay_ppm must be in [0, 1_000_000]")


@dataclass
class MintTracker:
    """
    Small piece of state to ensure idempotent minting across blocks/epochs.

    - last_height_processed: last height passed to on_block (monotonic, optional)
    - last_epoch_minted: epoch index where the per-epoch mint was last emitted
    - minted_in_epoch: total minted so far in the current epoch (for caps)
    """

    last_height_processed: Optional[int] = None
    last_epoch_minted: Optional[int] = None
    minted_in_epoch: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "MintTracker":
        return MintTracker(
            last_height_processed=d.get("last_height_processed"),
            last_epoch_minted=d.get("last_epoch_minted"),
            minted_in_epoch=int(d.get("minted_in_epoch", 0)),
        )


class AICFMinter:
    """
    Applies the configured minting policy to credit the AICF treasury account.

    Call `on_block(height)` once per imported block in height order.
    The method returns the integer amount minted at that block (0 if none).
    """

    __slots__ = ("cfg", "treasury", "_t")

    def __init__(
        self,
        cfg: MintConfig,
        treasury: TreasuryState,
        tracker: Optional[MintTracker] = None,
    ) -> None:
        cfg.validate()
        self.cfg = cfg
        self.treasury = treasury
        self._t = tracker or MintTracker()

    # --- persistence for tracker ---

    def dump(self) -> Dict:
        return self._t.to_dict()

    def load(self, data: Dict) -> None:
        self._t = MintTracker.from_dict(data)

    # --- core ---

    def on_block(self, height: int) -> int:
        """
        Process minting for a single block at `height`.

        Returns the amount minted at this height. Credits the treasury account
        in the same call (side-effect via TreasuryState).

        Idempotency: If called multiple times for the same height in sequence,
        the function will only mint once. If called out of order (non-monotonic),
        a ValueError is raised.
        """
        if height < 0:
            raise ValueError("height must be >= 0")

        # enforce monotonicity to keep tracker sane
        if (
            self._t.last_height_processed is not None
            and height < self._t.last_height_processed
        ):
            raise ValueError(
                f"on_block called out of order: got {height}, last={self._t.last_height_processed}"
            )

        if height < self.cfg.start_height:
            self._t.last_height_processed = height
            return 0
        if self.cfg.end_height is not None and height > self.cfg.end_height:
            self._t.last_height_processed = height
            return 0

        minted = 0

        # Epoch accounting (for per-epoch mint and caps)
        epoch_idx = (
            _epoch_for_height(height - self.cfg.start_height, self.cfg.epoch_length)
            if self.cfg.epoch_length
            else 0
        )
        first_block_of_epoch = False
        if self.cfg.epoch_length:
            # Detect epoch boundary vs tracker
            prev_epoch = _epoch_for_height(
                (self._t.last_height_processed or (height - 1)) - self.cfg.start_height,
                self.cfg.epoch_length,
            )
            first_block_of_epoch = epoch_idx != prev_epoch
            if first_block_of_epoch:
                self._t.minted_in_epoch = 0

        # 1) Per-epoch mint (only once per epoch)
        if self.cfg.per_epoch > 0 and self.cfg.epoch_length > 0:
            if self._t.last_epoch_minted != epoch_idx:
                epoch_amount = self._per_epoch_amount_after_decay(epoch_idx)
                epoch_amount = self._apply_epoch_cap(epoch_amount)
                if epoch_amount > 0:
                    self._credit_treasury(
                        epoch_amount, height, reason=f"mint-epoch-{epoch_idx}"
                    )
                    minted += epoch_amount
                    self._t.last_epoch_minted = epoch_idx
                    self._t.minted_in_epoch += epoch_amount

        # 2) Per-block mint (every block, but respect optional epoch cap)
        if self.cfg.per_block > 0:
            blk_amount = self.cfg.per_block
            blk_amount = self._apply_epoch_cap(blk_amount)
            if blk_amount > 0:
                self._credit_treasury(blk_amount, height, reason="mint-block")
                minted += blk_amount
                self._t.minted_in_epoch += blk_amount

        self._t.last_height_processed = height
        return minted

    # --- helpers ---

    def _credit_treasury(self, amount: int, height: int, *, reason: str) -> None:
        self.treasury.credit(
            self.cfg.treasury_provider_id, amount, height=height, reason=reason
        )

    def _per_epoch_amount_after_decay(self, epoch_idx: int) -> int:
        """Apply integer decay on the per-epoch amount using PPM; epoch_idx starts at 0."""
        if self.cfg.decay_ppm <= 0:
            return self.cfg.per_epoch
        # amount * (1 - d/PPM) ** epoch_idx  using integer math
        keep_ppm = PPM - self.cfg.decay_ppm
        num = self.cfg.per_epoch * pow(keep_ppm, epoch_idx)
        den = pow(PPM, epoch_idx)
        return num // den

    def _apply_epoch_cap(self, want: int) -> int:
        """Clamp the amount to respect cap_per_epoch across combined mints."""
        cap = self.cfg.cap_per_epoch
        if cap is None:
            return want
        remaining = cap - self._t.minted_in_epoch
        if remaining <= 0:
            return 0
        return want if want <= remaining else remaining
