from __future__ import annotations

from contextlib import nullcontext
from typer.testing import CliRunner

from animica.cli import tx


runner = CliRunner()


def test_send_retries_on_nonce_too_low(monkeypatch) -> None:
    nonces: list[int] = []
    send_calls = 0
    nonce_calls = 0
    original_build_tx_body = tx._build_tx_body

    def recording_build_tx_body(*args, **kwargs):  # noqa: ANN001
        nonces.append(int(kwargs["nonce"]))
        return original_build_tx_body(*args, **kwargs)

    def fake_rpc(_url: str, method: str, params):  # noqa: ANN001
        nonlocal send_calls, nonce_calls
        if method == "sync.getStatus":
            return {"synchronized": True}
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            nonce_calls += 1
            return 18 if nonce_calls == 1 else 19
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        if method == "tx.sendRawTransaction":
            send_calls += 1
            return f"0xhash{send_calls}"
        if method == "mempool.getStatus":
            if send_calls == 1:
                return {
                    "hash": params[0],
                    "known": True,
                    "state": "evicted",
                    "reason": "nonce_too_low",
                    "details": {"expected": 19, "got": 18},
                }
            return {"hash": params[0], "known": True, "state": "pending", "reason": None}
        return None

    class DummySig:
        alg_id = 1
        sig = b"\x01" * 64

    monkeypatch.setattr(tx, "_rpc", fake_rpc)
    monkeypatch.setattr(tx, "_load_wallet_entry", lambda _addr: {"public_key_hex": "11" * 32, "secret_key_hex": "22" * 32})
    monkeypatch.setattr(tx, "build_sign_bytes", lambda *_args, **_kwargs: b"signbytes")
    monkeypatch.setattr(tx, "pq_sign_detached", lambda *_args, **_kwargs: DummySig())
    monkeypatch.setattr(tx, "verify_detached", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tx, "_nonce_lock", lambda _addr: nullcontext())
    monkeypatch.setattr(tx, "_build_tx_body", recording_build_tx_body)

    result = runner.invoke(
        tx.app,
        [
            "send",
            "--from",
            "0x" + "11" * 32,
            "--to",
            "0x" + "22" * 32,
            "--value-nanm",
            "1",
            "--rpc-url",
            "http://node",
        ],
    )

    assert result.exit_code == 0, result.output
    # Updated to match new output format that includes reason
    assert "nonce mismatch" in result.output and "retrying with nonce=19" in result.output
    assert nonces == [18, 19]


def test_send_retries_with_advancing_pending_nonce(monkeypatch) -> None:
    """
    Test that CLI uses the correct nonce when the mempool's pending nonce
    advances between the first rejection and the retry.
    
    Scenario:
    1. CLI attempts nonce 63, gets rejected with "expected 64"
    2. Between rejection and retry, another transaction enters mempool with nonce 64
    3. When CLI fetches fresh pending nonce for retry, it now gets 65 (not 64)
    4. CLI should use max(expected=64, fresh_pending=65) = 65, not chase with 64
    """
    nonces: list[int] = []
    send_calls = 0
    nonce_calls = 0
    original_build_tx_body = tx._build_tx_body

    def recording_build_tx_body(*args, **kwargs):  # noqa: ANN001
        nonces.append(int(kwargs["nonce"]))
        return original_build_tx_body(*args, **kwargs)

    def fake_rpc(_url: str, method: str, params):  # noqa: ANN001
        nonlocal send_calls, nonce_calls
        if method == "sync.getStatus":
            return {"synchronized": True}
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            nonce_calls += 1
            # First call returns 63 (initial), second call returns 65 (mempool advanced)
            if nonce_calls == 1:
                return 63
            else:
                # Simulate mempool advancing (another tx with nonce 64 was accepted)
                return 65
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        if method == "tx.sendRawTransaction":
            send_calls += 1
            nonce_value = nonces[-1] if nonces else -1

            # First submission with nonce 63 fails with "expected 64"
            if send_calls == 1:
                from animica.cli.tx import RpcError
                raise RpcError(
                    code=-32014,
                    message="nonce too low: expected 64, got 63",
                    data={
                        "mempoolError": {
                            "code": 1005,
                            "reason": "nonce_too_low",
                            "message": "nonce too low",
                            "context": {
                                "expected_nonce": 64,
                                "got_nonce": 63,
                            },
                        }
                    },
                )
            
            # Second submission should use nonce 65 (max of expected=64, fresh=65)
            return f"0xhash{send_calls}"
        if method == "mempool.getStatus":
            # Second submission succeeds
            return {"hash": params[0], "known": True, "state": "pending", "reason": None}
        return None

    class DummySig:
        alg_id = 1
        sig = b"\x01" * 64

    monkeypatch.setattr(tx, "_rpc", fake_rpc)
    monkeypatch.setattr(tx, "_load_wallet_entry", lambda _addr: {"public_key_hex": "11" * 32, "secret_key_hex": "22" * 32})
    monkeypatch.setattr(tx, "build_sign_bytes", lambda *_args, **_kwargs: b"signbytes")
    monkeypatch.setattr(tx, "pq_sign_detached", lambda *_args, **_kwargs: DummySig())
    monkeypatch.setattr(tx, "verify_detached", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tx, "_nonce_lock", lambda _addr: nullcontext())
    monkeypatch.setattr(tx, "_build_tx_body", recording_build_tx_body)
    
    # Clear nonce cache to ensure clean test
    tx._NONCE_CACHE.clear()

    result = runner.invoke(
        tx.app,
        [
            "send",
            "--from",
            "0x" + "11" * 32,
            "--to",
            "0x" + "22" * 32,
            "--value-nanm",
            "1",
            "--rpc-url",
            "http://node",
        ],
    )

    assert result.exit_code == 0, result.output
    # Should retry with nonce 65 (max of expected=64, fresh_pending=65)
    assert nonces == [63, 65], f"Expected nonces [63, 65] but got {nonces}"
    # Verify the retry message is present
    assert "nonce mismatch" in result.output or "retrying" in result.output.lower()


def test_send_no_off_by_one_chase(monkeypatch) -> None:
    """
    Test that repeated nonce_too_low errors don't cause off-by-one chasing.
    
    This tests the scenario where:
    1. Cache has a stale value (62)
    2. CLI correctly fetches fresh nonce (64) instead of using cached+1 (63)
    3. First submission uses correct nonce 64 and succeeds
    
    With the fix, the cache is properly managed so we don't use stale cached+1 values.
    """
    nonces: list[int] = []
    send_calls = 0
    nonce_calls = 0
    original_build_tx_body = tx._build_tx_body

    def recording_build_tx_body(*args, **kwargs):  # noqa: ANN001
        nonces.append(int(kwargs["nonce"]))
        return original_build_tx_body(*args, **kwargs)

    def fake_rpc(_url: str, method: str, params):  # noqa: ANN001
        nonlocal send_calls, nonce_calls
        if method == "sync.getStatus":
            return {"synchronized": True}
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            nonce_calls += 1
            # Always return the correct next nonce: 64
            return 64
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        if method == "tx.sendRawTransaction":
            send_calls += 1
            nonce_value = nonces[-1] if nonces else -1

            # First submission with wrong nonce fails
            if nonce_value < 64:
                from animica.cli.tx import RpcError
                raise RpcError(
                    code=-32014,
                    message="nonce too low",
                    data={
                        "mempoolError": {
                            "reason": "nonce_too_low",
                            "context": {
                                "expected_nonce": 64,
                                "got_nonce": nonce_value,
                            },
                        }
                    },
                )
            
            # Correct nonce succeeds
            return f"0xhash{send_calls}"
        if method == "mempool.getStatus":
            # After using nonce 64, it should be in mempool
            return {"hash": params[0], "known": True, "state": "pending", "reason": None}
        return None

    class DummySig:
        alg_id = 1
        sig = b"\x01" * 64

    monkeypatch.setattr(tx, "_rpc", fake_rpc)
    monkeypatch.setattr(tx, "_load_wallet_entry", lambda _addr: {"public_key_hex": "11" * 32, "secret_key_hex": "22" * 32})
    monkeypatch.setattr(tx, "build_sign_bytes", lambda *_args, **_kwargs: b"signbytes")
    monkeypatch.setattr(tx, "pq_sign_detached", lambda *_args, **_kwargs: DummySig())
    monkeypatch.setattr(tx, "verify_detached", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tx, "_nonce_lock", lambda _addr: nullcontext())
    monkeypatch.setattr(tx, "_build_tx_body", recording_build_tx_body)
    
    # Pre-populate cache with stale value to test that it's properly handled
    tx._NONCE_CACHE[("http://node", "0x" + "11" * 32)] = 62

    result = runner.invoke(
        tx.app,
        [
            "send",
            "--from",
            "0x" + "11" * 32,
            "--to",
            "0x" + "22" * 32,
            "--value-nanm",
            "1",
            "--rpc-url",
            "http://node",
        ],
    )

    assert result.exit_code == 0, result.output
    # With the fix, CLI correctly uses fresh nonce 64 (not stale cached+1=63)
    assert nonces == [64], f"Expected nonces [64] (no retry needed) but got {nonces}"
    # Should succeed on first try without retries
    assert len(nonces) == 1, f"Should succeed on first try, but got {len(nonces)} attempts"
