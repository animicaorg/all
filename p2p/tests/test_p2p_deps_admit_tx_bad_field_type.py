from __future__ import annotations

from types import ModuleType
import sys

import p2p.deps as deps_mod
from core.encoding.cbor import dumps as cbor_dumps


class _SvcReject:
    def submit_atomic(self, **kwargs):
        return False, {
            "reason": "bad_field_type",
            "reason_code": "bad_field_type",
            "context": {"field": "nonce", "received_type": "dict"},
        }, kwargs.get("tx_hash_hex")


class _TxMethodsStub:
    _Tx = None

    @staticmethod
    def _decode_tx(raw):
        return {"body": {"nonce": {"nonce": 2}}}, {"tx": {"nonce": {"nonce": 2}}}

    @staticmethod
    def _extract_chain_id(tx_like, obj):
        return 1

    @staticmethod
    def _verify_pq_signature(tx_like, obj, chain_id=1):
        return None

    @staticmethod
    def _get_mempool_service():
        return _SvcReject()

    @staticmethod
    def _pending_put(*_args, **_kwargs):
        return None


def test_p2p_deps_admit_tx_returns_structured_reject_reason(monkeypatch):
    fake_mod = ModuleType("rpc.methods.tx")
    fake_mod._Tx = _TxMethodsStub._Tx
    fake_mod._decode_tx = _TxMethodsStub._decode_tx
    fake_mod._extract_chain_id = _TxMethodsStub._extract_chain_id
    fake_mod._verify_pq_signature = _TxMethodsStub._verify_pq_signature
    fake_mod._get_mempool_service = _TxMethodsStub._get_mempool_service
    fake_mod._pending_put = _TxMethodsStub._pending_put

    rpc_methods_pkg = sys.modules.get("rpc.methods")
    if rpc_methods_pkg is None:
        rpc_methods_pkg = ModuleType("rpc.methods")
        sys.modules["rpc.methods"] = rpc_methods_pkg
    monkeypatch.setitem(sys.modules, "rpc.methods.tx", fake_mod)
    monkeypatch.setattr(rpc_methods_pkg, "tx", fake_mod, raising=False)

    dep = object.__new__(deps_mod.P2PDeps)
    dep.chain_id = 1

    class _Tx:
        def to_cbor(self):
            return cbor_dumps({"tx": {"chainId": 1, "nonce": 1}, "sigs": []})

    ok, reason = deps_mod.P2PDeps.admit_tx(dep, _Tx(), local=False, origin_peer="peer-a")

    assert ok is False
    assert isinstance(reason, str)
    assert reason == "mempool_reject:bad_field_type"
