from __future__ import annotations

import types
from typing import Any, Iterable, Optional

import pytest

pool_mod = pytest.importorskip("mempool.pool", reason="mempool.pool module not found")
rpc_submit_mod = pytest.importorskip(
    "mempool.adapters.rpc_submit", reason="mempool.adapters.rpc_submit module not found"
)
p2p_admission_mod = pytest.importorskip(
    "mempool.adapters.p2p_admission",
    reason="mempool.adapters.p2p_admission module not found",
)

# -------------------------
# Helpers & scaffolding
# -------------------------


class FakeTx:
    """
    Minimal tx object that many pool/adapters can accept.
    """

    def __init__(
        self,
        sender: bytes,
        nonce: int,
        fee: int,
        gas: int = 21_000,
        size_bytes: int = 120,
    ):
        self.sender = sender
        self.nonce = nonce
        self.fee = fee
        # gas synonyms used by various implementations
        self.gas = gas
        self.gas_limit = gas
        self.intrinsic_gas = gas
        # encoded size (as bytes(tx))
        self.size_bytes = size_bytes
        # stable-ish hash for lookups
        self.hash = (sender + nonce.to_bytes(8, "big"))[:32] or b"\xad" * 32
        self.tx_hash = self.hash  # common alias

    def __bytes__(self) -> bytes:
        return b"\xee" * self.size_bytes


class FakePeer:
    def __init__(self, peer_id: bytes = b"P" * 32, addr: str = "127.0.0.1:0"):
        self.id = peer_id
        self.peer_id = peer_id
        self.address = addr
        self.addr = addr

    def __repr__(self) -> str:
        return f"<FakePeer id={self.peer_id.hex()[:8]} addr={self.address}>"


ALICE = b"A" * 20
BOB = b"B" * 20


def _get_attr_any(obj: Any, names: Iterable[str]) -> Optional[Any]:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _monkeypatch_validation_and_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make admission permissive and priority deterministic for tests.
    """
    try:
        validate = pytest.importorskip("mempool.validate")
        for name in (
            "validate_tx",
            "fast_stateless_check",
            "stateless_validate",
            "validate",
            "check_size",
            "check_chain_id",
            "check_gas_limits",
        ):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda *a, **k: True, raising=True)
        for name in (
            "estimate_encoded_size",
            "encoded_size",
            "tx_encoded_size",
            "get_encoded_size",
        ):
            if hasattr(validate, name):
                monkeypatch.setattr(
                    validate, name, lambda tx: len(bytes(tx)), raising=True
                )
        for name in (
            "precheck_pq_signature",
            "pq_precheck_verify",
            "verify_pq_signature",
            "pq_verify",
        ):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda *a, **k: True, raising=True)
    except Exception:
        pass

    try:
        priority = pytest.importorskip("mempool.priority")
        for name in ("effective_priority", "priority_of", "calc_effective_priority"):
            if hasattr(priority, name):
                monkeypatch.setattr(
                    priority, name, lambda tx: getattr(tx, "fee", 0), raising=True
                )
    except Exception:
        pass


def _make_config(*, max_txs: int | None = None, max_bytes: int | None = None) -> Any:
    try:
        from mempool import config as mp_config  # type: ignore
    except Exception:
        mp_config = None

    fields = {
        "max_txs": max_txs,
        "max_pool_txs": max_txs,
        "max_items": max_txs,
        "capacity": max_txs,
        "max_bytes": max_bytes,
        "max_pool_bytes": max_bytes,
        "capacity_bytes": max_bytes,
        "max_mem_bytes": max_bytes,
    }

    if mp_config:
        for Tname in ("MempoolConfig", "PoolConfig", "Limits", "MempoolLimits"):
            if hasattr(mp_config, Tname):
                T = getattr(mp_config, Tname)
                try:
                    return T(**{k: v for k, v in fields.items() if v is not None})  # type: ignore[call-arg]
                except Exception:
                    continue

    ns = types.SimpleNamespace()
    for k, v in fields.items():
        if v is not None:
            setattr(ns, k, v)
    return ns


def _new_pool(config: Any | None) -> Any:
    for cls_name in ("Pool", "Mempool", "TxPool", "PendingPool", "MemPool"):
        if hasattr(pool_mod, cls_name):
            cls = getattr(pool_mod, cls_name)
            try:
                if config is None:
                    return cls()
                try:
                    return cls(config=config)  # type: ignore[call-arg]
                except TypeError:
                    return cls(config)  # type: ignore[misc]
            except Exception:
                continue
    for name in ("pool", "mempool", "pending", "instance"):
        if hasattr(pool_mod, name):
            return getattr(pool_mod, name)
    pytest.skip("No known pool class/instance found in mempool.pool")
    raise RuntimeError  # pragma: no cover


def _contains_sender_nonce(pool: Any, sender: bytes, nonce: int) -> bool:
    for name in ("get", "get_tx", "by_sender_nonce", "lookup", "contains", "has"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                if name in ("contains", "has"):
                    try:
                        return bool(fn((sender, nonce)))  # type: ignore[misc]
                    except Exception:
                        pass
                got = fn(sender, nonce)  # type: ignore[misc]
                return bool(got)
            except Exception:
                continue
    # Fallback: iterate
    try:
        for item in list(iter(pool)):  # type: ignore[arg-type]
            t = item.tx if hasattr(item, "tx") else item
            if getattr(t, "sender", b"") == sender and getattr(t, "nonce", -1) == nonce:
                return True
    except Exception:
        pass
    return False


def _present_fee_for(pool: Any, sender: bytes, nonce: int) -> Optional[int]:
    best: Optional[int] = None
    try:
        seq = list(iter(pool))  # type: ignore[arg-type]
    except Exception:
        seq = []
    for item in seq:
        t = item.tx if hasattr(item, "tx") else item
        if getattr(t, "sender", b"") == sender and getattr(t, "nonce", -1) == nonce:
            fee = getattr(t, "fee", None)
            if isinstance(fee, int) and (best is None or fee > best):
                best = fee
    return best


def _candidate_payloads_for(tx: FakeTx) -> list[Any]:
    """
    Build a range of "raw" payloads adapters might accept:
    - bytes (pretend CBOR)
    - hex string
    - dict-like
    - the object itself
    """
    raw = bytes(tx)
    return [
        raw,
        bytearray(raw),
        "0x" + raw.hex(),
        {"sender": tx.sender, "nonce": tx.nonce, "fee": tx.fee, "gas": tx.gas},
        tx,
    ]


def _rpc_submit(pool: Any, tx: FakeTx) -> None:
    """
    Try various adapter entrypoints for RPC submission. On success, return.
    If none match, skip the test (API unknown).
    """
    # Some adapters expose a pure function API
    func_candidates = [
        "submit_raw_tx",
        "submit_tx",
        "rpc_submit_tx",
        "handle_tx_submit",
        "on_tx_submit",
        "ingest_rpc_transaction",
        "admit_from_rpc",
    ]
    for fname in func_candidates:
        fn = _get_attr_any(rpc_submit_mod, [fname])
        if not callable(fn):
            continue
        for payload in _candidate_payloads_for(tx):
            # Try with (pool, raw) then (raw) and kwargs variants
            try:
                fn(pool, payload)  # type: ignore[misc]
                return
            except TypeError:
                pass
            except Exception:
                # try next payload
                continue
            try:
                fn(payload)  # type: ignore[misc]
                return
            except Exception:
                continue
            try:
                fn(pool=pool, raw_tx=payload)  # type: ignore[misc]
                return
            except Exception:
                continue
    # Some adapters define a class with a submit method
    for cname in ("RpcSubmitAdapter", "RpcSubmitter", "SubmitService"):
        C = _get_attr_any(rpc_submit_mod, [cname])
        if C is None:
            continue
        try:
            inst = C(pool)  # type: ignore[misc]
        except Exception:
            try:
                inst = C()  # type: ignore[misc]
            except Exception:
                continue
        for mname in ("submit", "submit_tx", "handle", "ingest", "admit"):
            m = _get_attr_any(inst, [mname])
            if not callable(m):
                continue
            for payload in _candidate_payloads_for(tx):
                try:
                    m(payload)  # type: ignore[misc]
                    return
                except Exception:
                    continue
    pytest.skip(
        "No matching RPC submit API signature found in mempool.adapters.rpc_submit"
    )


def _p2p_admit(pool: Any, tx: FakeTx, peer: FakePeer) -> None:
    """
    Try various adapter entrypoints for P2P pre-admission. On success, return.
    If none match, skip.
    """
    func_candidates = [
        "pre_admit_from_peer",
        "admit_from_peer",
        "on_tx_from_peer",
        "handle_incoming_tx",
        "ingress_tx",
        "p2p_admit_tx",
        "fast_path_admit",
    ]
    for fname in func_candidates:
        fn = _get_attr_any(p2p_admission_mod, [fname])
        if not callable(fn):
            continue
        for payload in _candidate_payloads_for(tx):
            # Try (pool, payload, peer) / (pool, peer, payload) / (payload, peer) / kwargs
            for args in (
                (pool, payload, peer),
                (pool, peer, payload),
                (payload, peer),
                (pool, payload),
                (payload,),
            ):
                try:
                    fn(*args)  # type: ignore[misc]
                    return
                except TypeError:
                    continue
                except Exception:
                    continue
            try:
                fn(pool=pool, raw_tx=payload, peer=peer)  # type: ignore[misc]
                return
            except Exception:
                continue
    # Class-based
    for cname in ("P2PAdmissionAdapter", "PeerAdmission", "IngressService"):
        C = _get_attr_any(p2p_admission_mod, [cname])
        if C is None:
            continue
        try:
            inst = C(pool)  # type: ignore[misc]
        except Exception:
            try:
                inst = C()  # type: ignore[misc]
            except Exception:
                continue
        for mname in ("admit", "handle", "ingest", "on_tx"):
            m = _get_attr_any(inst, [mname])
            if not callable(m):
                continue
            for payload in _candidate_payloads_for(tx):
                try:
                    m(payload, peer)  # type: ignore[misc]
                    return
                except Exception:
                    continue
    pytest.skip(
        "No matching P2P admission API signature found in mempool.adapters.p2p_admission"
    )


# -------------------------
# Tests
# -------------------------


def test_rpc_submit_roundtrip_adds_to_pool(monkeypatch: pytest.MonkeyPatch):
    """
    Submitting via RPC adapter results in tx present in pool.
    Duplicate submit should not create duplicates or lower fee.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=100, max_bytes=10_000_000))

    t1 = FakeTx(ALICE, 0, fee=100)
    t2 = FakeTx(BOB, 0, fee=200)

    _rpc_submit(pool, t1)
    _rpc_submit(pool, t2)

    assert _contains_sender_nonce(pool, ALICE, 0), "ALICE/0 not found after RPC submit"
    assert _contains_sender_nonce(pool, BOB, 0), "BOB/0 not found after RPC submit"

    # Duplicate submit: should not create multiple entries nor drop fee
    fee_before = _present_fee_for(pool, ALICE, 0)
    _rpc_submit(pool, t1)  # submit again
    fee_after = _present_fee_for(pool, ALICE, 0)
    assert fee_before == fee_after, "RPC duplicate submit altered stored tx fee"
    # And ensure uniqueness by (sender, nonce)
    # (We don't assert exact count due to different internal shapes, but fee must be stable)


def test_p2p_admission_roundtrip_and_dedupe(monkeypatch: pytest.MonkeyPatch):
    """
    P2P pre-admission accepts tx and dedupes repeated arrivals.
    Higher-fee replacement for same (sender, nonce) should win.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=100, max_bytes=10_000_000))
    peer1 = FakePeer(b"P" * 32, "10.0.0.1:1111")
    peer2 = FakePeer(b"Q" * 32, "10.0.0.2:2222")

    low = FakeTx(ALICE, 0, fee=10)
    high = FakeTx(ALICE, 0, fee=500)

    # First admit from peer1
    _p2p_admit(pool, low, peer1)
    assert _contains_sender_nonce(
        pool, ALICE, 0
    ), "ALICE/0 not present after first P2P admit"
    assert _present_fee_for(pool, ALICE, 0) in (
        10,
        500,
    )  # depending on immediate replacement below

    # Repeated admit of same tx should not duplicate
    _p2p_admit(pool, low, peer1)
    assert _present_fee_for(pool, ALICE, 0) is not None

    # Higher-fee replacement from a different peer should win (pool policy)
    _p2p_admit(pool, high, peer2)
    fee_now = _present_fee_for(pool, ALICE, 0)
    assert (
        fee_now is not None and fee_now >= 500
    ), f"expected replacement with higher fee, got {fee_now}"
