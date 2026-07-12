"""ANM-2026-07 consensus address freeze — height-gated block-validation rule.

Turns the node-local mempool freeze-list (``mempool/address_denylist.py`` — a
per-node ``env``/JSON-file control that only stops the operator's OWN nodes from
ADMITTING or MINING a frozen spend) into a HARD, network-wide CONSENSUS rule: at
and after ``FORK_ADDRESS_FREEZE`` (see ``core.network_params``), block import
rejects any block that contains a non-coinbase transaction whose authoritative
sender OR recipient is a frozen account. The mempool control alone is trivially
bypassable — a stolen / leaked-key address can be drained by mining the spend on
any node that lacks the list, or on the operator's own node while it is offline,
and the resulting block still validates everywhere. This rule makes such a block
invalid for every upgraded node, so frozen funds cannot move on the honest chain.

Design contract (mirrors ``clawback_2026_07.py``):
  * Deterministic — the frozen set is a CODE CONSTANT compiled into pip 7.0.0,
    byte-identical on every node. It is NEVER sourced from env/file (that set is
    per-node and time-varying — fine for a mempool hint, fatal for consensus).
    The reject decision is a pure function of (committed txs, block height, this
    constant); no wall-clock, no state read, no per-node input at scan time.
  * Writes NO state — validation-only. stateRoot is only shadow-enforced, so a
    state-mutating freeze registry could silently fork balances; a reject rule can
    at worst LOUDLY orphan a rule-violating block. Never halts: a block with no
    frozen spend is untouched, and honest upgraded miners exclude frozen spends
    from their templates (``mempool/select.py``), so the honest chain advances.
  * Forward-only — PER-ENTRY activation heights: each address is grandfathered
    below its own height, so adding an address in a later release never
    retroactively invalidates history the network already accepted.

FREEZE-SET AUDIT RULE (safety-critical): every entry MUST be an incident address
(stolen / leaked private key) with NO legitimate activity. NEVER add a treasury /
reward / coinbase-output address — that would false-reject honest blocks, i.e. a
de-facto chain halt. See memory ``project-wallet-export-key-leak``.
"""
from __future__ import annotations

from typing import Dict, Optional

# (32-byte sha3_256(pubkey) account digest as hex, per-entry mainnet activation
# height). Keep in lockstep with the coordinated FORK_ADDRESS_FREEZE height in
# core.network_params for the initial set; later additions may use a higher
# per-entry height so they activate forward-only relative to when they ship.
#
# Entry 1 — the ANM-2026-07 attacker collection address (== clawback_2026_07._THIEF).
# It was already clawed back at height 39,584 (holds ~0 ANM, zero legitimate spend
# activity), so freezing it has zero economic impact: it permanently blocks any
# residual / future re-use of the known-compromised attacker key and exercises the
# consensus machinery live from activation.
_FROZEN_HEX: tuple[tuple[str, int], ...] = (
    ("ce0c733bab5f7c86fe106f4f817892adc952ec6312c8378c6b45ebd230d4870c", 42_000),
    # Entry 2/3 — ANM-2026-07 treasury-scam recipients, cleared to the foundation
    # treasury by clawback_treasury_scam_2026_07 at H=44,444. Frozen from the SAME
    # forward-only height (> head when shipped): a post-recovery lockout so the
    # scam accounts cannot be re-used or re-funded. Neither is a treasury / reward
    # / coinbase address. 0x3e79d533 is a stranded SPHINCS+ (0x1002) wallet that is
    # already unspendable; the freeze additionally blocks inbound re-use.
    ("c12185ebba4d108b5c8c3f969ec858882221693f1bef1d65f78ad63352008948", 44_444),
    ("3e79d53343a4161560daf8553e204c75306bcc92a9d7524029ae2ba5385fc658", 44_444),
    ("9b4da8ae7f7385da48b2fffcf45ad3216834615100185dbb68f4db5b774a8352", 44_444),
)


def _key(raw: bytes) -> bytes:
    """Normalize any account reference to the canonical 32-byte account key.

    Identical to ``mempool.address_denylist._norm`` / ``_decode_address_bytes`` in
    block_import: the alg-stripped ``sha3_256(pubkey)`` digest, right-padded to 32
    bytes. Consensus sender/recipient digests already arrive in this space.
    """
    return bytes(raw)[:32].ljust(32, b"\x00")


# Built once at import — deterministic (no env, no I/O): digest -> activation height.
_FROZEN: Dict[bytes, int] = {
    _key(bytes.fromhex(h)): int(height) for h, height in _FROZEN_HEX
}


def is_frozen(addr: Optional[bytes], *, height: Optional[int] = None) -> bool:
    """True iff ``addr`` is a consensus-frozen account active at ``height``.

    ``height`` is the height of the block being validated; an entry is
    grandfathered below its own per-entry activation height. Pass ``height=None``
    to ignore per-entry gating (used by mempool / block-template pre-gates that
    want to exclude every listed address unconditionally so an honest upgraded
    miner never builds a block its own consensus rule would reject).
    """
    if not addr:
        return False
    try:
        h = _FROZEN.get(_key(addr))
    except Exception:
        return False
    if h is None:
        return False
    if height is None:
        return True
    try:
        return int(height) >= int(h)
    except (TypeError, ValueError):
        return False


def frozen_digests() -> "frozenset[bytes]":
    """All consensus-frozen account digests (32-byte keys) — for the template
    pre-gate and RPC/query surfaces. Per-entry heights are ignored here."""
    return frozenset(_FROZEN.keys())


def frozen_entries() -> "tuple[tuple[str, int], ...]":
    """(hex_digest, activation_height) pairs — for RPC visibility / audit."""
    return tuple((h, int(height)) for h, height in _FROZEN_HEX)
