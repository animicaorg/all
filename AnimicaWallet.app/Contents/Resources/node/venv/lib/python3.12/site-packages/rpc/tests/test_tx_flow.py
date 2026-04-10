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
            kp = pq_keygen.keygen(alg)  # returns SigKeypair with (public_key, secret_key, address)
            msg = b"animica self-check"
            # Use sign_detached which returns a Signature envelope
            sig_env = pq_sign.sign_detached(msg, alg, kp.secret_key, domain="test/self-check")
            # verify_detached expects the Signature envelope
            assert pq_verify.verify_detached(msg, sig_env, kp.public_key) is True
            # Check that address is available (keygen already computes it)
            assert kp.address and isinstance(kp.address, str)
            
            # Return wrapper functions that match the old API for backwards compatibility
            def sign_wrapper(alg_id, sk, msg_bytes):
                sig_env = pq_sign.sign_detached(msg_bytes, alg_id, sk, domain="tx/sign")
                return sig_env.sig  # Extract raw signature bytes
            
            def verify_wrapper(alg_id, pk, msg_bytes, sig_bytes):
                # Reconstruct Signature envelope for verification
                from pq.py.sign import Signature
                from pq.py.registry import ALG_NAME
                sig_env = Signature(
                    alg_id=alg_id if isinstance(alg_id, int) else pq_keygen.keygen(alg_id).alg_id,
                    alg_name=ALG_NAME.get(alg_id, alg_id) if isinstance(alg_id, int) else alg_id,
                    domain="tx/sign",
                    prehash="sha3-512",
                    chain_id=None,
                    context=b"",
                    sig=sig_bytes
                )
                return pq_verify.verify_detached(msg_bytes, sig_env, pk)
            
            return alg, kp, sign_wrapper, verify_wrapper, address_from_pubkey
        except Exception as e:  # noqa: BLE001 - we genuinely want to try/fallthrough
            last_err = e
            continue

    pytest.skip(f"No working PQ signature backend available (last error: {last_err})")


def _build_signed_transfer_cbor(
    chain_id: int, from_nonce: int = 0
) -> tuple[bytes, str, str]:
    """
    Construct a minimal transfer tx object, sign it (PQ), and CBOR-encode it using core encoders.

    Returns: (cbor_bytes, tx_hash_hex, sender_address)
    """
    # Deferred imports
    from core.encoding.canonical import tx_sign_bytes
    from core.encoding.cbor import dumps as cbor_dumps
    from core.types.tx import Sig, Tx
    from pq.py.utils.hash import sha3_256

    alg, kp, sign_fn, verify_fn, addr_from_pubkey_fn = _choose_working_sig_alg()

    sender = kp.address  # Use the address from the SigKeypair directly
    # A deterministic "to" address derived from the string "recipient"
    to_pub_digest = sha3_256(b"recipient")  # 32 bytes
    # Generate a valid public key for recipient (pad to match key length)
    to_pubkey = to_pub_digest + b"\x00" * max(0, len(kp.public_key) - len(to_pub_digest))
    # Use the imported address_from_pubkey with correct argument order
    from pq.py.address import address_from_pubkey
    from pq.py.registry import ALG_ID
    alg_id = ALG_ID[alg] if isinstance(alg, str) else alg
    to_addr = address_from_pubkey(to_pubkey, alg_id)

    # Construct the transaction (aligns with spec/tx_format.cddl and core.types.tx.Tx)
    tx = Tx.transfer(
        chain_id=chain_id,
        nonce=from_nonce,
        from_addr=symbolic_or_bech32(sender=sender),
        to_addr=to_addr,
        value=123456789,  # small nonzero amount
        gas_limit=21000,  # baseline intrinsic
        gas_price=1,  # tiny price for tests
        data=b"",  # no payload for transfer
        access_list=[],  # empty list by default
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
    # Create signed tx (Tx is frozen dataclass)
    from dataclasses import replace
    tx_signed = replace(tx, sigs=(sig_env,))

    # Encode CBOR (canonical) and compute tx hash (keccak/sha3 per core.types.tx)
    cbor_tx = tx_signed.to_cbor()
    tx_hash_hex = "0x" + tx_signed.txid().hex()
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
    cbor_tx, exp_tx_hash, sender = _build_signed_transfer_cbor(
        cfg.chain_id, from_nonce=0
    )
    raw_hex = "0x" + cbor_tx.hex()

    # 1) Submit
    submit = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert submit["jsonrpc"] == "2.0"
    # The result is the tx hash directly, not a dict
    got_hash = submit["result"]
    assert isinstance(got_hash, str) and got_hash.startswith("0x")
    # Prefer equality when the node computes hash the same way
    assert got_hash == exp_tx_hash

    # 2) The pending pool should expose it by hash
    q = rpc_call(client, "tx.getTransactionByHash", params={"txHash": got_hash})
    txv = q["result"]
    assert txv is not None, "submitted tx must be findable by hash while pending"
    assert txv["hash"] == got_hash
    # Regression: pending tx must return bech32m sender address (anim1...), not 0x... hex
    assert txv["from"] == sender, f"expected bech32m sender {sender}, got {txv.get('from')}"
    assert sender.startswith("anim1"), "sender should be bech32m format"
    assert txv["to"] is not None
    assert txv.get("blockNumber") in (
        None,
        "pending",
    ), "tx should not be mined in this unit test"

    # 3) Basic state introspection doesn't reflect pending yet (nonce remains 0)
    n = rpc_call(client, "state.getNonce", params={"address": sender})
    assert n["result"] == 0


async def test_rejects_bad_signature(client_and_cfg):
    client, cfg = client_and_cfg
    from core.encoding.cbor import dumps as cbor_dumps
    from core.types.tx import Tx
    # Build an unsigned transfer and attach a bogus signature envelope
    from pq.py.registry import normalize_alg_name

    bad_alg = normalize_alg_name("dilithium3")
    # Build unsigned transfer
    tx_unsigned = Tx.transfer(
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
    # Create signed tx with a junk signature (using dataclasses.replace since Tx is frozen)
    from dataclasses import replace
    tx = replace(tx_unsigned, sigs=(Tx.Sig(alg=bad_alg, pub=b"\x01\x02", sig=b"\x03\x04"),))
    raw_hex = "0x" + tx.to_cbor().hex()

    res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex}, expect_error=True)
    # Expect a structured JSON-RPC error for invalid signature (code defined in rpc/errors.py)
    err = res["error"]
    assert isinstance(err.get("code"), int)
    # A helpful message mentioning signature/verify
    assert any(
        s in (err.get("message") or "").lower() for s in ("sig", "verify", "invalid")
    )


async def test_duplicate_submit_returns_same_hash(client_and_cfg):
    client, cfg = client_and_cfg
    cbor_tx, exp_tx_hash, _ = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=1)
    raw_hex = "0x" + cbor_tx.hex()

    r1 = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    r2 = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    # The result is the tx hash directly, not a dict
    assert r1["result"] == exp_tx_hash
    # Second submit should either be idempotent OK (same hash) or return a "duplicate" error.
    if "result" in r2:
        assert r2["result"] == exp_tx_hash
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
        cbor_tx, tx_hash, _ = _build_signed_transfer_cbor(
            cfg.chain_id, from_nonce=i + 2
        )
        raw_hex = "0x" + cbor_tx.hex()
        rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
        got = rpc_call(client, "tx.getTransactionByHash", params={"txHash": tx_hash})
        assert got["result"] is not None


async def test_state_get_nonce_with_bech32m_address_regression(client_and_cfg):
    """
    Regression test: state.getNonce should accept bech32m addresses (anim1...)
    and return 0 (not an InternalError) for accounts that don't exist.
    
    This test covers the bug where pending tx flow used bech32m addresses,
    but the RPC nonce service raised an InternalError instead of returning 0.
    """
    client, cfg = client_and_cfg
    # Generate a fresh keypair to get a valid bech32m address
    cbor_tx, tx_hash, sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    
    # Verify the address is bech32m format
    assert sender.startswith("anim1"), f"expected bech32m address, got {sender}"
    
    # Query nonce for this address (which doesn't exist in test state yet)
    result = rpc_call(client, "state.getNonce", params={"address": sender})
    
    # Must return a valid result (not an error) with nonce 0
    assert "result" in result, f"expected result, got {result}"
    assert result["result"] == 0, f"expected nonce 0 for new account, got {result['result']}"
    
    # Also verify state.getBalance works with bech32m addresses
    balance_result = rpc_call(client, "state.getBalance", params={"address": sender})
    assert "result" in balance_result
    assert balance_result["result"] == "0x0", f"expected balance 0x0, got {balance_result['result']}"
