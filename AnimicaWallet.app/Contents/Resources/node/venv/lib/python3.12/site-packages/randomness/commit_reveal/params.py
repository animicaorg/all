# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
Commit–reveal parameterization.

This module collects the tunables that govern the beacon's commit/reveal
round windows and (optionally) economics hooks like bonding and slashing.

These parameters are *block-time* based: windows are expressed in **blocks**,
not wall-clock seconds.

Typical usage:
    from randomness.commit_reveal.params import CommitRevealParams

    params = CommitRevealParams.default()
    # or load from a dict / network config file:
    params = CommitRevealParams.from_dict({
        "commit_window_blocks": 24,
        "reveal_window_blocks": 24,
        "reveal_grace_blocks": 2,
        "require_bond": True,
        "min_bond_units": 10_000,
        "slash_on_miss": True,
        "slash_ratio_miss": 0.25,
    })

Notes
-----
- Slashing flags only take effect if the execution/economics side wires them in.
  Nodes may set them true in configs but they will be NOOP unless the slashing
  engine is enabled at runtime and the chain policy authorizes it.
- `reveal_grace_blocks` extends the reveal window to allow for small network
  jitter; reveals outside of the *effective* window are invalid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

# Sensible defaults for devnets/smoke-tests.
DEFAULT_COMMIT_WINDOW_BLOCKS = 16
DEFAULT_REVEAL_WINDOW_BLOCKS = 16
DEFAULT_REVEAL_GRACE_BLOCKS = 2


@dataclass(frozen=True, slots=True)
class WindowParams:
    """
    Lightweight wall-clock window parameters used by tests.

    This mirrors :class:`CommitRevealParams` but uses second-based durations and
    an explicit anchor timestamp. Properties expose the names expected by
    :class:`randomness.commit_reveal.round_manager.RoundManager` so the class can
    be passed directly without further adaptation.
    """

    commit_secs: int
    reveal_secs: int
    reveal_grace_secs: int = 0
    round0_start_ts: int = 0

    @property
    def commit_phase_s(self) -> int:  # pragma: no cover - trivial
        return int(self.commit_secs)

    @property
    def reveal_phase_s(self) -> int:  # pragma: no cover - trivial
        return int(self.reveal_secs)

    @property
    def vdf_phase_s(self) -> int:  # pragma: no cover - trivial
        return 0

    @property
    def reveal_grace_s(self) -> int:  # pragma: no cover - trivial
        return int(self.reveal_grace_secs)

    @property
    def round_anchor_s(self) -> int:  # pragma: no cover - trivial
        return int(self.round0_start_ts)


@dataclass(frozen=True, slots=True)
class CommitRevealParams:
    """
    Parameters controlling commit/reveal windows and optional bonding/slashing.

    Attributes
    ----------
    commit_window_blocks : int
        Number of blocks in the **commit** window of a round. Must be >= 1.
    reveal_window_blocks : int
        Number of blocks in the **reveal** window. Must be >= 1.
    reveal_grace_blocks : int
        Extra blocks allowed after the nominal reveal window (>= 0).
    require_bond : bool
        If True, participants must post a bond to be eligible to commit.
    min_bond_units : int
        Minimum bond (chain-specific units). Must be > 0 if require_bond True.
    slash_on_miss : bool
        If True, a participant that *committed* but failed to *reveal* within
        the effective window is eligible for slashing.
    slash_on_bad_reveal : bool
        If True, an *invalid* reveal (wrong preimage/proof) is slashable.
    slash_ratio_miss : float
        Fraction of the bond to slash on a missed reveal. In [0.0, 1.0].
    slash_ratio_bad : float
        Fraction of the bond to slash on a bad reveal. In [0.0, 1.0].
    allow_late_commit : bool
        If True, commits arriving in the first `reveal_grace_blocks` may be
        tolerated on loosely-configured devnets; **never** enable in production.
    """

    commit_window_blocks: int
    reveal_window_blocks: int
    reveal_grace_blocks: int = DEFAULT_REVEAL_GRACE_BLOCKS

    # Bonding / slashing knobs (optional; only active if wired into economics).
    require_bond: bool = False
    min_bond_units: int = 0

    slash_on_miss: bool = False
    slash_on_bad_reveal: bool = True
    slash_ratio_miss: float = 0.50
    slash_ratio_bad: float = 1.00

    # Devnet-quality-of-life (not for production).
    allow_late_commit: bool = False

    # -------------------------
    # Construction helpers
    # -------------------------

    @staticmethod
    def default() -> "CommitRevealParams":
        """Returns a conservative default suitable for local/devnet use."""
        return CommitRevealParams(
            commit_window_blocks=DEFAULT_COMMIT_WINDOW_BLOCKS,
            reveal_window_blocks=DEFAULT_REVEAL_WINDOW_BLOCKS,
            reveal_grace_blocks=DEFAULT_REVEAL_GRACE_BLOCKS,
            require_bond=False,
            min_bond_units=0,
            slash_on_miss=False,
            slash_on_bad_reveal=True,
            slash_ratio_miss=0.50,
            slash_ratio_bad=1.00,
            allow_late_commit=False,
        )

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "CommitRevealParams":
        """
        Build from a plain mapping (e.g., loaded YAML/JSON).

        Unknown keys are ignored; missing keys fall back to defaults.
        """
        base = CommitRevealParams.default()

        def get(key: str, default: Any) -> Any:
            return d.get(key, default)

        obj = CommitRevealParams(
            commit_window_blocks=int(
                get("commit_window_blocks", base.commit_window_blocks)
            ),
            reveal_window_blocks=int(
                get("reveal_window_blocks", base.reveal_window_blocks)
            ),
            reveal_grace_blocks=int(
                get("reveal_grace_blocks", base.reveal_grace_blocks)
            ),
            require_bond=bool(get("require_bond", base.require_bond)),
            min_bond_units=int(get("min_bond_units", base.min_bond_units)),
            slash_on_miss=bool(get("slash_on_miss", base.slash_on_miss)),
            slash_on_bad_reveal=bool(
                get("slash_on_bad_reveal", base.slash_on_bad_reveal)
            ),
            slash_ratio_miss=float(get("slash_ratio_miss", base.slash_ratio_miss)),
            slash_ratio_bad=float(get("slash_ratio_bad", base.slash_ratio_bad)),
            allow_late_commit=bool(get("allow_late_commit", base.allow_late_commit)),
        )
        # Validate eagerly so callers fail-fast on bad config.
        obj._validate()
        return obj

    @staticmethod
    def from_config(cfg: Optional[Any]) -> "CommitRevealParams":
        """
        Build from `randomness.config`-style object if available.

        This is resilient to missing attributes; defaults are applied then any
        present attributes on `cfg` are used to override.

        Expected (optional) attributes on `cfg`:
            - COMMIT_WINDOW_BLOCKS
            - REVEAL_WINDOW_BLOCKS
            - REVEAL_GRACE_BLOCKS
            - REQUIRE_BOND
            - MIN_BOND_UNITS
            - SLASH_ON_MISS
            - SLASH_ON_BAD_REVEAL
            - SLASH_RATIO_MISS
            - SLASH_RATIO_BAD
            - ALLOW_LATE_COMMIT
        """
        base = CommitRevealParams.default()
        if cfg is None:
            return base

        def pick(attr: str, default: Any) -> Any:
            return getattr(cfg, attr, default)

        obj = CommitRevealParams(
            commit_window_blocks=int(
                pick("COMMIT_WINDOW_BLOCKS", base.commit_window_blocks)
            ),
            reveal_window_blocks=int(
                pick("REVEAL_WINDOW_BLOCKS", base.reveal_window_blocks)
            ),
            reveal_grace_blocks=int(
                pick("REVEAL_GRACE_BLOCKS", base.reveal_grace_blocks)
            ),
            require_bond=bool(pick("REQUIRE_BOND", base.require_bond)),
            min_bond_units=int(pick("MIN_BOND_UNITS", base.min_bond_units)),
            slash_on_miss=bool(pick("SLASH_ON_MISS", base.slash_on_miss)),
            slash_on_bad_reveal=bool(
                pick("SLASH_ON_BAD_REVEAL", base.slash_on_bad_reveal)
            ),
            slash_ratio_miss=float(pick("SLASH_RATIO_MISS", base.slash_ratio_miss)),
            slash_ratio_bad=float(pick("SLASH_RATIO_BAD", base.slash_ratio_bad)),
            allow_late_commit=bool(pick("ALLOW_LATE_COMMIT", base.allow_late_commit)),
        )
        obj._validate()
        return obj

    # -------------------------
    # Derived helpers
    # -------------------------

    @property
    def round_length_blocks(self) -> int:
        """Total round length (commit + reveal), *excluding* grace."""
        return self.commit_window_blocks + self.reveal_window_blocks

    @property
    def effective_reveal_end_extension(self) -> int:
        """Number of extra blocks tacked onto the end of the reveal window."""
        return max(0, self.reveal_grace_blocks)

    # -------------------------
    # Validation
    # -------------------------

    def _validate(self) -> None:
        if self.commit_window_blocks < 1:
            raise ValueError("commit_window_blocks must be >= 1")
        if self.reveal_window_blocks < 1:
            raise ValueError("reveal_window_blocks must be >= 1")
        if self.reveal_grace_blocks < 0:
            raise ValueError("reveal_grace_blocks must be >= 0")

        # Bonding invariants.
        if self.require_bond and self.min_bond_units <= 0:
            raise ValueError("min_bond_units must be > 0 when require_bond is True")
        if self.min_bond_units < 0:
            raise ValueError("min_bond_units must be >= 0")

        # Slashing fractions must live in [0, 1].
        for name, val in (
            ("slash_ratio_miss", self.slash_ratio_miss),
            ("slash_ratio_bad", self.slash_ratio_bad),
        ):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0.0, 1.0]")

        # Production safety footgun: discourage late commits.
        if self.allow_late_commit and (self.reveal_grace_blocks == 0):
            raise ValueError("allow_late_commit=True requires reveal_grace_blocks > 0")

    # -------------------------
    # Serialization helpers
    # -------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (JSON/YAML friendly)."""
        return {
            "commit_window_blocks": self.commit_window_blocks,
            "reveal_window_blocks": self.reveal_window_blocks,
            "reveal_grace_blocks": self.reveal_grace_blocks,
            "require_bond": self.require_bond,
            "min_bond_units": self.min_bond_units,
            "slash_on_miss": self.slash_on_miss,
            "slash_on_bad_reveal": self.slash_on_bad_reveal,
            "slash_ratio_miss": self.slash_ratio_miss,
            "slash_ratio_bad": self.slash_ratio_bad,
            "allow_late_commit": self.allow_late_commit,
        }


__all__ = [
    "CommitRevealParams",
    "DEFAULT_COMMIT_WINDOW_BLOCKS",
    "DEFAULT_REVEAL_WINDOW_BLOCKS",
    "DEFAULT_REVEAL_GRACE_BLOCKS",
]
