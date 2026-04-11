from __future__ import annotations

from core.encoding.cbor import dumps as cbor_dumps
from mempool.tx_hash import tx_hash_hex
from rpc.mempool_service import MempoolService
from mempool.errors import AdmissionError


class _DummyPool:
    def __len__(self):
        return 0

    def get(self, _):
        return None


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


def test_submit_atomic_replay_from_recent_cache_is_typed() -> None:
    svc = MempoolService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        persist_enabled=False,
    )
    tx, raw, txh, sender_hex = _replay_candidate_envelope()
    svc._recent_txids[txh] = 1_000_000

    ok, reject, got_hash = svc.submit_atomic(tx=tx, raw=raw, tx_hash_hex=txh)
    assert ok is False
    assert got_hash == txh
    assert isinstance(reject, dict)
    assert reject["reason"] != "internal_error"
    assert reject["reason_code"] == "replay"
    assert reject["context"]["tx_hash"] == txh
    assert reject["context"]["sender"] == sender_hex
    assert "trace_id" not in reject["context"]


def test_submit_atomic_replay_from_tx_index_is_typed() -> None:
    svc = MempoolService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=_SeenTxIndex(),
        persist_enabled=False,
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
