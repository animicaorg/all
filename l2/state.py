"""Authenticated L2 account state.

State commitment = a binary Sparse Merkle Tree (SMT) of depth 256 keyed by the
32-byte account address, leaves = ``sha3_256(LEAF_TAG || addr || account_bytes)``.
An SMT is chosen over a plain Merkle tree because it gives compact
**non-membership** proofs (needed for forced exits and for proving "this account
never received funds") and deterministic roots independent of insertion order —
two nodes replaying the same batch get identical roots regardless of scheduling.

Optimizations that keep it practical:

* **Default-subtree short-circuit.** Empty subtrees hash to a precomputed
  per-level constant, so a tree with N non-empty leaves costs O(N·256) hashing at
  most, and in practice far less because sibling defaults are cached.
* **In-memory node map** with copy-on-write per batch: a batch computes its new
  root against a snapshot without mutating committed state until commit.
* Deterministic: no map-iteration-order dependence anywhere in root computation.

Accounts hold ``balance`` (nanos, non-negative int), ``nonce`` (monotonic), and an
optional 32-byte ``metadata`` commitment (e.g. an escrow-set root) so richer state
can be added later without changing the leaf layout.

Every ANM quantity is a Python ``int`` in nanos. Floating point is never used.
The money invariant — total L2 balance == credited deposits − finalized
withdrawals − burned fees-to-treasury — is checked by :mod:`l2.bridge`; this
module guarantees the narrower local invariant that no balance is ever negative.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from .constants import ADDR_LEN, HASH_LEN

LEAF_TAG = b"animica.l2.smt.leaf"
NODE_TAG = b"animica.l2.smt.node"
TREE_DEPTH = 256
ZERO32 = b"\x00" * HASH_LEN


def _h(*parts: bytes) -> bytes:
    m = hashlib.sha3_256()
    for p in parts:
        m.update(p)
    return m.digest()


# Precompute the hash of an all-empty subtree at each level. default_hashes[0] is
# the empty-leaf value; default_hashes[i] = H(NODE_TAG || d[i-1] || d[i-1]).
def _compute_default_hashes() -> List[bytes]:
    d = [ZERO32]
    for _ in range(TREE_DEPTH):
        d.append(_h(NODE_TAG, d[-1], d[-1]))
    return d


DEFAULT_HASHES: List[bytes] = _compute_default_hashes()
# The root of a completely empty tree.
EMPTY_ROOT: bytes = DEFAULT_HASHES[TREE_DEPTH]


# ── account model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Account:
    balance: int = 0
    nonce: int = 0
    metadata: bytes = ZERO32  # 32B commitment slot for future extensions

    def encode(self) -> bytes:
        # Fixed 32B metadata + varint-free fixed-width balance/nonce so the leaf
        # value is unambiguous. Balance/nonce as 16-byte big-endian (128-bit;
        # far above any real value, and MAX_AMOUNT is 96-bit).
        if self.balance < 0:
            raise ValueError("negative balance")
        if self.nonce < 0:
            raise ValueError("negative nonce")
        return (
            self.balance.to_bytes(16, "big")
            + self.nonce.to_bytes(16, "big")
            + (self.metadata if self.metadata else ZERO32)
        )

    @staticmethod
    def decode(b: bytes) -> "Account":
        if len(b) != 64:
            raise ValueError("bad account encoding length")
        return Account(
            balance=int.from_bytes(b[0:16], "big"),
            nonce=int.from_bytes(b[16:32], "big"),
            metadata=b[32:64],
        )

    def is_empty(self) -> bool:
        return self.balance == 0 and self.nonce == 0 and self.metadata == ZERO32


EMPTY_ACCOUNT = Account()


def _leaf_hash(addr: bytes, acct: Account) -> bytes:
    if acct.is_empty():
        return ZERO32  # empty accounts are indistinguishable from absent ones
    return _h(LEAF_TAG, addr, acct.encode())


def _bit(addr: bytes, level: int) -> int:
    """Bit of the address at tree level (0 = most significant, root side)."""
    byte = addr[level // 8]
    return (byte >> (7 - (level % 8))) & 1


# ── the tree ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MembershipProof:
    """Proof that ``addr`` maps to ``account`` (or to the empty account, for a
    non-membership proof) under ``root``. ``siblings`` is top-down, length 256."""

    addr: bytes
    account: Account
    siblings: List[bytes]

    def verify(self, root: bytes) -> bool:
        return verify_proof(root, self.addr, self.account, self.siblings)


def verify_proof(
    root: bytes, addr: bytes, account: Account, siblings: List[bytes]
) -> bool:
    if len(addr) != ADDR_LEN or len(siblings) != TREE_DEPTH:
        return False
    node = _leaf_hash(addr, account)
    # Recompute from the leaf up to the root using the address bits.
    for level in range(TREE_DEPTH - 1, -1, -1):
        sib = siblings[level]
        if _bit(addr, level) == 0:
            node = _h(NODE_TAG, node, sib)
        else:
            node = _h(NODE_TAG, sib, node)
    return node == root


class StateTree:
    """In-memory SMT with copy-on-write batch application.

    Node storage is a dict keyed by (level, path_prefix_bits_as_bytes) is
    overkill here; instead we store the *populated* leaves and recompute internal
    nodes lazily against DEFAULT_HASHES. For the account counts an L2 sees this is
    both simple and correct; a production deployment can swap in a persistent
    node store behind the same interface (see :mod:`l2.store`).
    """

    def __init__(self, accounts: Optional[Dict[bytes, Account]] = None) -> None:
        self._acct: Dict[bytes, Account] = dict(accounts or {})
        self._root_cache: Optional[bytes] = None

    # ── reads ──
    def get(self, addr: bytes) -> Account:
        return self._acct.get(addr, EMPTY_ACCOUNT)

    def accounts(self) -> Dict[bytes, Account]:
        return dict(self._acct)

    def num_accounts(self) -> int:
        return sum(1 for a in self._acct.values() if not a.is_empty())

    # ── writes (invalidate root cache) ──
    def set(self, addr: bytes, acct: Account) -> None:
        if len(addr) != ADDR_LEN:
            raise ValueError("bad address length")
        if acct.is_empty():
            self._acct.pop(addr, None)
        else:
            self._acct[addr] = acct
        self._root_cache = None

    # ── root ──
    def _leaf_nodes(self) -> Dict[int, bytes]:
        leaves: Dict[int, bytes] = {}
        for addr, acct in self._acct.items():
            lh = _leaf_hash(addr, acct)
            if lh != ZERO32:
                leaves[int.from_bytes(addr, "big")] = lh
        return leaves

    @staticmethod
    def _collapse(level_nodes: Dict[int, bytes], level: int) -> Dict[int, bytes]:
        """Combine every node at ``level`` (256..1) with its sibling into the
        parent level. A missing sibling is the level's default hash. Iterating
        sorted keys makes this deterministic; the parity check assigns left/right
        unambiguously regardless of which sibling is visited first."""
        default = DEFAULT_HASHES[TREE_DEPTH - level]
        parent: Dict[int, bytes] = {}
        for path in sorted(level_nodes):
            pp = path >> 1
            if pp in parent:
                continue  # sibling already produced this parent
            node = level_nodes[path]
            sib = level_nodes.get(path ^ 1, default)
            if path & 1 == 0:
                left, right = node, sib
            else:
                left, right = sib, node
            parent[pp] = _h(NODE_TAG, left, right)
        return parent

    def root(self) -> bytes:
        if self._root_cache is not None:
            return self._root_cache
        level_nodes = self._leaf_nodes()
        level = TREE_DEPTH
        while level > 0:
            level_nodes = self._collapse(level_nodes, level)
            level -= 1
        root = level_nodes.get(0, EMPTY_ROOT)
        self._root_cache = root
        return root

    def prove(self, addr: bytes) -> MembershipProof:
        """Membership (or non-membership) proof for ``addr``. Siblings recompute
        to :meth:`root` via :func:`verify_proof`."""
        if len(addr) != ADDR_LEN:
            raise ValueError("bad address length")
        level_nodes = self._leaf_nodes()
        target = int.from_bytes(addr, "big")
        siblings: List[bytes] = [ZERO32] * TREE_DEPTH
        path = target
        level = TREE_DEPTH
        while level > 0:
            default = DEFAULT_HASHES[TREE_DEPTH - level]
            siblings[level - 1] = level_nodes.get(path ^ 1, default)
            level_nodes = self._collapse(level_nodes, level)
            path >>= 1
            level -= 1
        return MembershipProof(addr=addr, account=self.get(addr), siblings=siblings)

    def clone(self) -> "StateTree":
        return StateTree(self._acct)


@dataclass
class StateSnapshot:
    """Immutable view used to apply a batch without mutating committed state."""

    accounts: Dict[bytes, Account]

    def tree(self) -> StateTree:
        return StateTree(self.accounts)
