"""ANM-C03: deterministic state/txs commitment roots."""
import collections

from core.chain.state_commit import compute_block_txs_root, compute_state_root
from core.encoding.cbor import cbor_dumps
from core.utils.hash import ZERO32


class _Acc:
    def __init__(self, bal, code_hash=None):
        self.balance = bal
        self.code_hash = code_hash

    def to_cbor(self):
        m = {"balance": int(self.balance)}
        if self.code_hash is not None:
            m["code_hash"] = bytes(self.code_hash)
        return cbor_dumps(m)


class _DB:
    def __init__(self, accts):
        self._a = accts

    def iter_accounts(self):
        return iter(self._a.items())


def test_state_root_is_order_independent():
    a = {b"\x01" * 32: _Acc(100), b"\x02" * 32: _Acc(200), b"\x03" * 32: _Acc(0)}
    r1 = compute_state_root(_DB(a))
    a_rev = collections.OrderedDict(reversed(list(a.items())))
    r2 = compute_state_root(_DB(a_rev))
    assert r1 == r2
    assert r1 != ZERO32


def test_state_root_changes_with_any_balance():
    r1 = compute_state_root(_DB({b"\x01" * 32: _Acc(100)}))
    r2 = compute_state_root(_DB({b"\x01" * 32: _Acc(101)}))
    assert r1 != r2


def test_empty_state_root_is_zero():
    assert compute_state_root(_DB({})) == ZERO32


def test_txs_root_empty_is_zero():
    class _B:
        txs = []

    assert compute_block_txs_root(_B()) == ZERO32


def test_txs_root_import_gate_grandfathers_and_catches():
    import types

    from core.chain import block_import as bi

    blk = types.SimpleNamespace(header=types.SimpleNamespace(txsRoot=b"\x11" * 32), txs=[])
    blk0 = types.SimpleNamespace(header=types.SimpleNamespace(txsRoot=ZERO32), txs=[])
    # Grandfathered below the mainnet activation height (37000).
    assert bi._verify_block_txs_root_gated(blk, 36999, 1) is None
    # At/after H: committed 0x11.. but computed ZERO32 (empty txs) -> mismatch.
    assert (bi._verify_block_txs_root_gated(blk, 37000, 1) or "").startswith("txs_root_mismatch")
    # A block that commits a zero txsRoot self-gates (never false-rejected).
    assert bi._verify_block_txs_root_gated(blk0, 37000, 1) is None
    # Unknown chain -> never enforced (forward-safe).
    assert bi._verify_block_txs_root_gated(blk, 10**9, 999) is None


def test_proofs_root_import_gate():
    """ANM-C04 slice: proofsRoot commitment is verified on the same gate."""
    import types

    from core.chain import block_import as bi

    # Commits proofsRoot 0x22.. but proofs_root() computes 0x33.. -> mismatch at H.
    blk = types.SimpleNamespace(
        header=types.SimpleNamespace(txsRoot=ZERO32, proofsRoot=b"\x22" * 32),
        txs=[],
        proofs_root=lambda: b"\x33" * 32,
    )
    assert bi._verify_block_txs_root_gated(blk, 36999, 1) is None  # grandfathered
    assert (bi._verify_block_txs_root_gated(blk, 37000, 1) or "").startswith("proofs_root_mismatch")

    # Matching proofsRoot -> accepted.
    blk_ok = types.SimpleNamespace(
        header=types.SimpleNamespace(txsRoot=ZERO32, proofsRoot=b"\x44" * 32),
        txs=[],
        proofs_root=lambda: b"\x44" * 32,
    )
    assert bi._verify_block_txs_root_gated(blk_ok, 37000, 1) is None
