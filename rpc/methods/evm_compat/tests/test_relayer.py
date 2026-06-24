"""Node-free unit tests for the custodial EVM<->native relayer.

The funded round-trip + dry-run admission are integration tests (need a running
node); these cover everything that does NOT require node services: EVM decode +
secp256k1 hygiene, the keystore (idempotency, AES-GCM at-rest, plaintext refusal),
nonce/receipt bookkeeping, and the cheap submit() gates that fire before any node
call (chainId/EIP-155, value units + sanity cap, nonce).
"""

from __future__ import annotations

import importlib

import pytest

from eth_account import Account
from eth_utils import to_checksum_address


def _signed(acct, tx) -> str:
    s = acct.sign_transaction(tx)
    raw = s.raw_transaction if hasattr(s, "raw_transaction") else s.rawTransaction
    return "0x" + raw.hex()


# --------------------------------------------------------------------------- #
# decode + secp256k1 hygiene
# --------------------------------------------------------------------------- #
def test_decode_legacy_and_1559():
    from rpc.methods.evm_compat.decode import decode_evm_tx
    acct = Account.from_key("0x" + "22" * 32)
    to = to_checksum_address("0x" + "33" * 20)
    leg = decode_evm_tx(_signed(acct, {"to": to, "value": 1000, "nonce": 0,
                                       "gas": 21000, "gasPrice": 1, "chainId": 149}))
    assert leg["sender"] == acct.address.lower()
    assert leg["chainId"] == 149 and leg["value"] == 1000 and leg["type"] == 0
    t2 = decode_evm_tx(_signed(acct, {"to": to, "value": 5, "nonce": 7, "gas": 21000,
                                      "maxFeePerGas": 2, "maxPriorityFeePerGas": 1,
                                      "chainId": 149, "type": 2}))
    assert t2["sender"] == acct.address.lower()
    assert t2["chainId"] == 149 and t2["nonce"] == 7 and t2["type"] == 2


def test_decode_rejects_pre_eip155():
    from rpc.methods.evm_compat.decode import decode_evm_tx
    acct = Account.from_key("0x" + "22" * 32)
    to = to_checksum_address("0x" + "33" * 20)
    with pytest.raises(ValueError):
        decode_evm_tx(_signed(acct, {"to": to, "value": 1, "nonce": 0,
                                     "gas": 21000, "gasPrice": 1}))  # no chainId -> v 27/28


def test_hygiene_rejects_high_s():
    from rpc.methods.evm_compat.decode import enforce_secp256k1_hygiene, SECP256K1_N
    with pytest.raises(ValueError):
        enforce_secp256k1_hygiene({"type": 2, "r": 1, "s": SECP256K1_N - 1, "v_or_yParity": 0})


# --------------------------------------------------------------------------- #
# keystore / nonce / receipt map (with a KEK, isolated dir)
# --------------------------------------------------------------------------- #
@pytest.fixture
def relayer(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_EVM_RELAYER", "1")
    monkeypatch.setenv("ANIMICA_EVM_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_EVM_RELAYER_KEK", "%064x" % 0xABCD)
    import rpc.methods.evm_compat.relayer as R
    importlib.reload(R)
    R._CONN = None
    R._KEK = None
    R._KEK_RESOLVED = False
    R._AEAD = None
    return R


def test_keystore_idempotent_and_at_rest(relayer):
    R = relayer
    E = "0x" + "a1" * 20
    a1 = R.get_or_create_account(E)
    a2 = R.get_or_create_account(E)
    assert a1.anim1 == a2.anim1
    assert not hasattr(a1, "secret_key")
    sk, pk, alg = R._load_secret(E)
    assert len(sk) == 4032 and len(pk) == 1952 and alg == 0x1003
    row = R._conn().execute("SELECT sk_enc, sk_nonce FROM managed_accounts WHERE evm=?", (E,)).fetchone()
    assert bytes(row[0]) != sk and len(row[1]) == 12      # encrypted at rest


def test_nonce_and_record(relayer):
    R = relayer
    E = "0x" + "a2" * 20
    R.get_or_create_account(E)
    assert R.get_nonce(E) == 0
    assert R._reserve_nonce(E, 0) is True       # atomic CAS 0 -> 1
    assert R.get_nonce(E) == 1
    assert R._reserve_nonce(E, 0) is False       # stale expected loses the CAS
    R._release_nonce(E, 0)                        # roll back 1 -> 0
    assert R.get_nonce(E) == 0
    R.record_submission(evm_hash="0x" + "bb" * 32, native_txid="0x" + "cc" * 32, evm=E,
                        evm_to="0x" + "44" * 20, anim_to="anim1xyz", value=1000,
                        evm_nonce=0, chain_id=149)
    rec = R.lookup_by_evm_hash("0x" + "bb" * 32)
    assert rec and rec["native_txid"] == "0x" + "cc" * 32 and rec["value"] == 1000


def test_plaintext_refused_without_kek(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_EVM_RELAYER", "1")
    monkeypatch.setenv("ANIMICA_EVM_DIR", str(tmp_path / "plain"))
    monkeypatch.delenv("ANIMICA_EVM_RELAYER_KEK", raising=False)
    monkeypatch.delenv("ANIMICA_EVM_RELAYER_ALLOW_PLAINTEXT", raising=False)
    import rpc.methods.evm_compat.relayer as R
    importlib.reload(R)
    R._CONN = None
    R._KEK = None
    R._KEK_RESOLVED = False
    R._AEAD = None
    with pytest.raises(Exception):
        R.get_or_create_account("0x" + "b2" * 20)


# --------------------------------------------------------------------------- #
# submit() gates that fire before any node call
# --------------------------------------------------------------------------- #
def _parsed(E, **over):
    d = dict(sender=E, chainId=149, to="0x" + "44" * 20, data="0x",
             value=1000, nonce=0, raw=b"\x01\x02")
    d.update(over)
    return d


def test_submit_rejects_wrong_chain(relayer):
    R = relayer
    E = "0x" + "c1" * 20
    with pytest.raises(Exception) as ei:
        R.submit("0x0102", E, _parsed(E, chainId=1))
    assert "chainid" in getattr(ei.value, "message", str(ei.value)).lower()


def test_submit_rejects_value_cap(relayer):
    R = relayer
    E = "0x" + "c2" * 20
    with pytest.raises(Exception) as ei:
        R.submit("0x0102", E, _parsed(E, value=10 ** 19))
    assert "cap" in getattr(ei.value, "message", str(ei.value)).lower()


def test_submit_rejects_sender_mismatch(relayer):
    R = relayer
    with pytest.raises(Exception):
        R.submit("0x0102", "0x" + "c3" * 20, _parsed("0x" + "dd" * 20))  # parsed.sender != evm_sender


def test_submit_nonce_gate(relayer):
    R = relayer
    E = "0x" + "c4" * 20
    R.get_or_create_account(E)  # nonce 0 expected
    with pytest.raises(Exception) as ei:
        R.submit("0x0102", E, _parsed(E, nonce=5))   # too high
    assert "too high" in getattr(ei.value, "message", str(ei.value)).lower()
