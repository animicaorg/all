from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.encoding.cbor import dumps as cbor_dumps
from core.types.tx import Tx
from core.utils.tx import normalize_tx_envelope
from mempool.tx_hash import tx_hash_hex
from rpc.mempool_service import MempoolService
from mempool.errors import AdmissionError


@dataclass
class _DummyEntry:
    tx: object
    meta: object
    tx_hash: bytes
    raw: bytes


class _DummyIndex:
    def __init__(self) -> None:
        self._by_hash: dict[bytes, _DummyEntry] = {}
        self._by_sender: dict[str, list[_DummyEntry]] = {}

    def get_by_sender(self, sender_hex: str) -> list[_DummyEntry]:
        return list(self._by_sender.get(sender_hex, []))

    def get(self, tx_hash: bytes) -> _DummyEntry | None:
        return self._by_hash.get(bytes(tx_hash))

    def all_items(self):
        return list(self._by_hash.items())


class _DummyPool:
    def __init__(self):
        self._by_hash: dict[bytes, _DummyEntry] = {}
        self.index = _DummyIndex()

    def __len__(self):
        return len(self._by_hash)

    def get(self, tx_hash):
        return self._by_hash.get(bytes(tx_hash))

    def add(self, pool_tx, meta, is_local=True):
        tx_hash = bytes(pool_tx.tx_hash)
        entry = _DummyEntry(tx=pool_tx, meta=meta, tx_hash=tx_hash, raw=bytes(pool_tx.raw))
        self._by_hash[tx_hash] = entry
        self.index._by_hash[tx_hash] = entry
        self.index._by_sender.setdefault(str(meta.sender), []).append(entry)


class _RejectingService(MempoolService):
    def submit(self, **kwargs):  # type: ignore[override]
        raise AdmissionError("bad signature", context={"reason": "invalid_signature"})


class _AcceptingService(MempoolService):
    def submit(self, **kwargs):  # type: ignore[override]
        return kwargs.get("tx_hash_hex") or "0x" + "11" * 32

    def has_hash(self, _tx_hash_hex: str) -> bool:
        return True


class _SeenTxIndex:
    def exists(self, _tx_hash: bytes) -> bool:
        return True


class _LegacyUnsignedSeenTxIndex:
    def __init__(self, seen_hash: bytes) -> None:
        self.seen_hash = bytes(seen_hash)
        self.calls: list[bytes] = []

    def exists(self, tx_hash: bytes) -> bool:
        hashed = bytes(tx_hash)
        self.calls.append(hashed)
        return hashed == self.seen_hash


def _replay_candidate_envelope() -> tuple[dict[str, object], bytes, str, str]:
    sender_hex = "0x" + "11" * 32
    tx = {
        "body": {
            "chainId": 1,
            "from": sender_hex,
            "to": "0x" + "22" * 32,
            "nonce": 0,
            "value": 1,
            "gasLimit": 21_000,
            "maxFee": 1,
            "data": b"",
        },
        "sigs": [],
    }
    raw = cbor_dumps(tx)
    return tx, raw, tx_hash_hex(raw), sender_hex


def test_submit_atomic_reject_payload_is_typed() -> None:
    svc = _RejectingService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    ok, reject, txh = svc.submit_atomic(tx={}, raw=b"\x80", tx_hash_hex="0x" + "22" * 32)
    assert ok is False
    assert txh == "0x" + "22" * 32
    assert isinstance(reject, dict)
    assert reject["reason"] == "invalid_signature"
    assert "hint" in reject


def test_submit_atomic_simulate_accepts_without_insert() -> None:
    svc = _AcceptingService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    ok, reject, _ = svc.submit_atomic(tx={}, raw=b"\x80", tx_hash_hex="0x" + "33" * 32, simulate=True)
    assert ok is True
    assert reject is None


def test_submit_atomic_stale_recent_cache_is_not_false_replay() -> None:
    svc = MempoolService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
        # This test exercises the stale-recent-cache replay path, not the
        # ANM-H10 signature gate. Unsigned fixtures would otherwise be
        # rejected as invalid_signature before the replay logic runs, so
        # disable the sig check via the production kill-switch.
        verify_signatures=False,
    )
    tx, raw, txh, sender_hex = _replay_candidate_envelope()
    svc._recent_txids[txh] = 1_000_000

    ok, reject, got_hash = svc.submit_atomic(tx=tx, raw=raw, tx_hash_hex=txh)
    assert ok is True
    assert got_hash == txh
    assert reject is None
    assert svc.has_hash(txh) is True
    assert txh in svc._recent_txids


def test_submit_atomic_replay_from_tx_index_is_typed() -> None:
    svc = MempoolService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=_SeenTxIndex(),
        persist_enabled=False,
        # Exercises the tx_index replay path, not the ANM-H10 signature gate;
        # disable the sig check so the unsigned fixture reaches replay logic.
        verify_signatures=False,
    )
    tx, raw, txh, sender_hex = _replay_candidate_envelope()

    ok, reject, got_hash = svc.submit_atomic(tx=tx, raw=raw, tx_hash_hex=txh)
    assert ok is False
    assert got_hash == txh
    assert isinstance(reject, dict)
    assert reject["reason"] != "internal_error"
    assert reject["reason_code"] == "replay"
    assert reject["context"]["tx_hash"] == txh
    assert reject["context"]["sender"] == sender_hex
    assert "trace_id" not in reject["context"]


def test_quarantined_hash_is_rejected_without_pool_insert() -> None:
    svc = MempoolService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    tx, raw, txh, _sender_hex = _replay_candidate_envelope()

    outcome = svc.quarantine_hashes([txh], reason="duplicate_canonical", permanent=True)
    assert outcome["quarantined"] == 1

    ok, reject, got_hash = svc.submit_atomic(tx=tx, raw=raw, tx_hash_hex=txh)
    assert ok is False
    assert got_hash == txh
    assert isinstance(reject, dict)
    assert reject["reason_code"] == "tx_quarantined"


def test_submit_atomic_replay_from_legacy_unsigned_tx_index_is_typed() -> None:
    tx, raw, txh, sender_hex = _replay_candidate_envelope()
    normalized = normalize_tx_envelope(raw)
    tx_obj = Tx.from_obj(
        {"tx": normalized.get("tx"), "sigs": normalized.get("sigs", [])}
    )
    signed_hash = bytes.fromhex(txh[2:])
    unsigned_hash = bytes(tx_obj.unsigned_hash())
    assert signed_hash != unsigned_hash

    tx_index = _LegacyUnsignedSeenTxIndex(unsigned_hash)
    svc = MempoolService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=tx_index,
        persist_enabled=False,
        # Exercises the legacy unsigned-hash replay path, not the ANM-H10
        # signature gate; disable the sig check so the unsigned fixture
        # reaches the replay logic.
        verify_signatures=False,
    )

    ok, reject, got_hash = svc.submit_atomic(tx=tx, raw=raw, tx_hash_hex=txh)
    assert ok is False
    assert got_hash == txh
    assert isinstance(reject, dict)
    assert reject["reason_code"] == "replay"
    assert reject["context"]["tx_hash"] == txh
    assert reject["context"]["sender"] == sender_hex
    assert reject["context"]["replay_source"] == "tx_index_unsigned_hash"
    assert any(call == unsigned_hash for call in tx_index.calls)


def test_submit_atomic_stale_recent_cache_ignores_replay_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rpc.mempool_service as mempool_service

    class BrokenReplay(Exception):
        pass

    monkeypatch.setattr(mempool_service, "Replay", BrokenReplay)
    svc = MempoolService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
        # Exercises the stale-recent-cache path (must not touch the Replay
        # constructor), not the ANM-H10 signature gate; disable the sig
        # check so the unsigned fixture reaches the recent-cache logic.
        verify_signatures=False,
    )
    tx, raw, txh, sender_hex = _replay_candidate_envelope()
    svc._recent_txids[txh] = 1_000_000

    ok, reject, got_hash = svc.submit_atomic(tx=tx, raw=raw, tx_hash_hex=txh)
    assert ok is True
    assert got_hash == txh
    assert reject is None
    assert svc.has_hash(txh) is True


class _TypeErrorService(MempoolService):
    def submit(self, **kwargs):  # type: ignore[override]
        raise TypeError("'<' not supported between instances of 'dict' and 'int'")


def test_submit_atomic_internal_error_has_trace_id_and_reason_code() -> None:
    svc = _TypeErrorService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    ok, reject, _ = svc.submit_atomic(tx={"body": {"nonce": {"bad": 1}}}, raw=b"\x80", tx_hash_hex="0x" + "44" * 32)
    assert ok is False
    assert isinstance(reject, dict)
    assert reject["reason"] == "internal_error"
    assert reject["reason_code"] == "internal_error"
    assert reject["context"]["error_class"] == "TypeError"
    assert isinstance(reject["context"].get("trace_id"), str)
    assert reject["context"].get("error_message")


def test_submit_atomic_bad_field_type_is_structured() -> None:
    svc = _RejectingService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    err = AdmissionError(
        "tx envelope normalization failed",
        context={
            "reason": "bad_field_type",
            "field": "nonce",
            "received_type": "dict",
            "received_keys": ["unexpected"],
        },
    )

    class _Svc(_RejectingService):
        def submit(self, **kwargs):  # type: ignore[override]
            raise err

    svc = _Svc(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    ok, reject, _ = svc.submit_atomic(tx={"body": {"nonce": {"unexpected": 1}}}, raw=bytes([0x80]), tx_hash_hex="0x" + "55" * 32)
    assert ok is False
    assert isinstance(reject, dict)
    assert reject["reason_code"] == "bad_field_type"
    assert reject["context"]["field"] == "nonce"
    assert reject["context"]["received_type"] == "dict"


class _NonceDictService(MempoolService):
    def submit(self, **kwargs):  # type: ignore[override]
        tx = kwargs.get("tx") or {}
        _ = int(((tx.get("body") or {}).get("nonce")))  # reproduces historical crash
        return "0x" + "66" * 32


def test_submit_atomic_nonce_dict_is_bad_field_type_not_internal_error() -> None:
    svc = _NonceDictService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    ok, reject, _ = svc.submit_atomic(
        tx={"body": {"nonce": {"nonce": 7}}},
        raw=bytes([0x80]),
        tx_hash_hex="0x" + "66" * 32,
    )
    assert ok is False
    assert isinstance(reject, dict)
    assert reject["reason_code"] == "bad_field_type"
    assert reject["context"]["field"] == "nonce"


class _FeeReservedDictService(MempoolService):
    def submit(self, **kwargs):  # type: ignore[override]
        tx = kwargs.get("tx") or {}
        _ = int(((tx.get("body") or {}).get("fee_reserved")))  # reproduces historical crash
        return "0x" + "77" * 32


def test_submit_atomic_fee_reserved_dict_is_bad_field_type_not_internal_error() -> None:
    svc = _FeeReservedDictService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    ok, reject, _ = svc.submit_atomic(
        tx={"body": {"fee_reserved": {"amount": 1}}},
        raw=bytes([0x80]),
        tx_hash_hex="0x" + "77" * 32,
    )
    assert ok is False
    assert isinstance(reject, dict)
    assert reject["reason_code"] == "bad_field_type"
    assert reject["context"]["field"] == "fee_reserved"
