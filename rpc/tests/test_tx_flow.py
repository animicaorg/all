from __future__ import annotations

import os
import typing as t

import pytest

from rpc.tests import new_test_client, rpc_call

# We rely on the local PQ wrappers and core encoders to build a *real* signed CBOR tx.
# If no PQ backend (liboqs/wasm/pure-python fallback) is available, we skip this test
# rather than fail the suite on environments without PQ crypto.
pytestmark = pytest.mark.anyio


def _choose_working_sig_alg():
    """
    Try dilithium3 first, then sphincs_shake_128s. Return (alg_name, keypair, sign, verify, addr_from_pub).
    Skip the test if neither is available in this environment.
    """
    # Local imports so that environments without these modules can still import the test file.
    from pq.py import keygen as pq_keygen
    from pq.py import sign as pq_sign
    from pq.py import verify as pq_verify
    from pq.py.address import address_from_pubkey
    from pq.py.registry import normalize_alg_name

    candidates = ["dilithium3", "sphincs_shake_128s"]
    # Optional env to force a specific alg while debugging CI
    forced = os.getenv("ANIMICA_TEST_SIG_ALG")
    if forced:
        candidates = [forced]

    last_err: Exception | None = None
    for name in candidates:
        alg = normalize_alg_name(name)
        try:
            kp = pq_keygen.generate(alg)  # (pub, sec) bytes
            msg = b"animica self-check"
            sig = pq_sign.sign(alg, kp.secret_key, msg)
            assert pq_verify.verify(alg, kp.public_key, msg, sig) is True
            # Also check address derivation for this alg
            _ = address_from_pubkey(alg, kp.public_key)
            return alg, kp, pq_sign.sign, pq_verify.verify, address_from_pubkey
        except Exception as e:  # noqa: BLE001 - we genuinely want to try/fallthrough
            last_err = e
            continue

    pytest.skip(f"No working PQ signature backend available (last error: {last_err})")


def _build_signed_transfer_cbor(chain_id: int, from_nonce: int = 0) -> tuple[bytes, str, str]:
    """
    Construct a minimal transfer tx object, sign it (PQ), and CBOR-encode it using core encoders.

    Returns: (cbor_bytes, tx_hash_hex, sender_address)
    """
    # Deferred imports
    from core.types.tx import Tx, Sig
    from core.encoding.canonical import tx_sign_bytes
    from core.encoding.cbor import dumps as cbor_dumps
    from pq.py.utils.hash import sha3_256

    alg, kp, sign_fn, verify_fn, addr_from_pubkey = _choose_working_sig_alg()

    sender = addr_from_pubkey(alg, kp.public_key)
    # A deterministic "to" address derived from the string "recipient"
    to_pub_digest = sha3_256(b"recipient")  # 32 bytes
    # alg id will be inferred inside address encoder; to keep it simple we just reuse sender's alg for a valid bech32m
    to_addr = addr_from_pubkey(alg, to_pub_digest + b"\x00" * max(0, len(kp.public_key) - len(to_pub_digest)))

    # Construct the transaction (aligns with spec/tx_format.cddl and core.types.tx.Tx)
    tx = Tx.transfer(
        chain_id=chain_id,
        nonce=from_nonce,
        from_addr=symbolic_or_bech32(sender=sender),
        to_addr=to_addr,
        value=123456789,      # small nonzero amount
        gas_limit=21000,      # baseline intrinsic
        gas_price=1,          # tiny price for tests
        data=b"",             # no payload for transfer
        access_list=[],       # empty list by default
    )

    # Domain-separated sign-bytes
    sb = tx_sign_bytes(tx)
    sig_bytes = sign_fn(alg, kp.secret_key, sb)
    # Construct signature envelope
    sig_env = Sig(
        alg=alg,
        pub=kp.public_key,
        sig=sig_bytes,
    )
    tx.signatures = [sig_env]

    # Encode CBOR (canonical) and compute tx hash (keccak/sha3 per core.types.tx)
    cbor_tx = cbor_dumps(tx)
    tx_hash_hex = "0x" + tx.hash().hex()
    return cbor_tx, tx_hash_hex, sender


def symbolic_or_bech32(sender: str) -> str:
    """
    Helper to clearly communicate intent in code; for now it's just returning the bech32 address string.
    """
    return sender


@pytest.fixture(scope="function")
def client_and_cfg():
    client, cfg, app = new_test_client()
    return client, cfg


async def test_send_raw_transaction_roundtrip(client_and_cfg):
    client, cfg = client_and_cfg
    # Build a valid signed CBOR transfer
    cbor_tx, exp_tx_hash, sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    raw_hex = "0x" + cbor_tx.hex()

    # 1) Submit
    submit = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert submit["jsonrpc"] == "2.0"
    got_hash = submit["result"]["txHash"]
    assert isinstance(got_hash, str) and got_hash.startswith("0x")
    # Prefer equality when the node computes hash the same way
    assert got_hash == exp_tx_hash

    # 2) The pending pool should expose it by hash
    q = rpc_call(client, "tx.getTransactionByHash", params={"hash": got_hash})
    txv = q["result"]
    assert txv is not None, "submitted tx must be findable by hash while pending"
    assert txv["hash"] == got_hash
    assert txv["from"] == sender
    assert txv["to"] is not None
    assert txv.get("blockNumber") in (None, "pending"), "tx should not be mined in this unit test"

    # 3) Basic state introspection doesn't reflect pending yet (nonce remains 0)
    n = rpc_call(client, "state.getNonce", params={"address": sender})
    assert n["result"] == 0


async def test_rejects_bad_signature(client_and_cfg):
    client, cfg = client_and_cfg
    from core.types.tx import Tx
    from core.encoding.cbor import dumps as cbor_dumps

    # Build an unsigned transfer and attach a bogus signature envelope
    from pq.py.registry import normalize_alg_name
    bad_alg = normalize_alg_name("dilithium3")
    tx = Tx.transfer(
        chain_id=cfg.chain_id,
        nonce=0,
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",  # syntactically valid bech32m example
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        value=1,
        gas_limit=21000,
        gas_price=1,
        data=b"",
        access_list=[],
    )
    # Attach a junk signature
    tx.signatures = [
        Tx.Sig(alg=bad_alg, pub=b"\x01\x02", sig=b"\x03\x04"),
    ]
    raw_hex = "0x" + cbor_dumps(tx).hex()

    res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    # Expect a structured JSON-RPC error for invalid signature (code defined in rpc/errors.py)
    assert "error" in res, "bad signature should be rejected by RPC"
    err = res["error"]
    assert isinstance(err.get("code"), int)
    # A helpful message mentioning signature/verify
    assert any(s in (err.get("message") or "").lower() for s in ("sig", "verify", "invalid"))


async def test_duplicate_submit_returns_same_hash(client_and_cfg):
    client, cfg = client_and_cfg
    cbor_tx, exp_tx_hash, _ = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=1)
    raw_hex = "0x" + cbor_tx.hex()

    r1 = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    r2 = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert r1["result"]["txHash"] == exp_tx_hash
    # Second submit should either be idempotent OK (same hash) or return a "duplicate" error.
    if "result" in r2:
        assert r2["result"]["txHash"] == exp_tx_hash
    else:
        assert "error" in r2
        msg = (r2["error"].get("message") or "").lower()
        assert "duplicate" in msg or "already" in msg


async def test_pending_pool_eviction_policy_smoke(client_and_cfg):
    """
    Submit a handful of txs and verify at least the last one is retrievable.
    This doesn't attempt to exhaust limits; it's a smoke-check that the pending pool indexes work.
    """
    client, cfg = client_and_cfg
    for i in range(3):
        cbor_tx, tx_hash, _ = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=i + 2)
        raw_hex = "0x" + cbor_tx.hex()
        rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
        got = rpc_call(client, "tx.getTransactionByHash", params={"hash": tx_hash})
        assert got["result"] is not None

