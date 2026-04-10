from __future__ import annotations

from coretx.crypto import SchemeInfo
from coretx.signing import verify_tx_signature
from coretx.types import TxAuth, TxBody, TxEnvelope, TxId, TxKind


def _mk_env(scheme_id: int, pk_len: int, sig_len: int) -> TxEnvelope:
    body = TxBody(
        version=1,
        chain_id=1,
        nonce=1,
        from_addr=b"\x11" * 32,
        to_addr=b"\x22" * 32,
        value=1,
        fee=1,
        gas_limit=1,
        data=b"",
        memo="",
        timestamp=1,
        kind=TxKind.TRANSFER,
    )
    auth = TxAuth(
        scheme_id=scheme_id,
        pubkey_bytes=b"p" * pk_len,
        signature_bytes=b"s" * sig_len,
        prehash_id=0,
    )
    return TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))


def test_unknown_scheme_id_unambiguous_inference_succeeds(monkeypatch):
    from coretx import crypto

    schemes = {
        7: SchemeInfo(
            scheme_id=7,
            name="alpha",
            sign_func=None,
            verify_func=lambda _m, _s, _p: True,
            pubkey_lengths=(77,),
            signature_lengths=(88,),
            enabled=True,
        )
    }
    monkeypatch.setattr(crypto, "_SCHEMES", schemes)

    env = _mk_env(9999, 77, 88)
    result = verify_tx_signature(env)
    assert result.ok is True


def test_unknown_scheme_id_ambiguous_inference_fails(monkeypatch):
    from coretx import crypto

    schemes = {
        7: SchemeInfo(7, "alpha", None, lambda _m, _s, _p: True, (77,), (88,), enabled=True),
        8: SchemeInfo(8, "beta", None, lambda _m, _s, _p: True, (77,), (88,), enabled=True),
    }
    monkeypatch.setattr(crypto, "_SCHEMES", schemes)

    env = _mk_env(9999, 77, 88)
    result = verify_tx_signature(env)
    assert result.ok is False
    assert result.reason == "scheme_unsupported"
    assert result.diagnostics["supported"] == [{"id": 7, "name": "alpha"}, {"id": 8, "name": "beta"}]


def test_scheme_disabled_by_policy_error(monkeypatch):
    from coretx import crypto

    schemes = {
        7: SchemeInfo(7, "alpha", None, lambda _m, _s, _p: True, (77,), (88,), enabled=False, reason_if_disabled="disabled_by_policy"),
    }
    monkeypatch.setattr(crypto, "_SCHEMES", schemes)

    env = _mk_env(7, 77, 88)
    result = verify_tx_signature(env)
    assert result.ok is False
    assert result.reason == "scheme_disabled_by_policy"
