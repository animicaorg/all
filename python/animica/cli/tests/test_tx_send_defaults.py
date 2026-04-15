from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from typer.testing import CliRunner

from animica.cli import tx


runner = CliRunner()


def _patch_signing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        tx,
        "pq_sign_tx",
        lambda *_args, **_kwargs: SimpleNamespace(alg_id=4098, sig=b"\x01" * 64),
    )
    monkeypatch.setattr(
        tx,
        "pq_verify_tx",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True),
    )


def _invoke_send() -> object:
    return runner.invoke(
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


def test_send_defaults_to_pending_nonce_source(monkeypatch) -> None:  # noqa: ANN001
    calls: list[str] = []
    nonces: list[int] = []
    original_build_tx_body = tx._build_tx_body

    def recording_build_tx_body(*args, **kwargs):  # noqa: ANN001
        nonces.append(int(kwargs["nonce"]))
        return original_build_tx_body(*args, **kwargs)

    def fake_rpc(_url: str, method: str, params):  # noqa: ANN001
        calls.append(method)
        if method == "sync.getStatus":
            return {"synchronized": True, "head_height": 7}
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        if method == "chain.getHead":
            return {"height": 7}
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            return 12
        if method in {"state.getNonce", "tx.getTransactionCount", "state.getTransactionCount"}:
            return 5
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        if method == "tx.sendRawTransaction":
            return "0xhash"
        if method == "mempool.getStatus":
            return {"hash": params[0], "known": True, "state": "pending", "reason": None}
        return None

    tx._NONCE_CACHE.clear()
    monkeypatch.setattr(tx, "_rpc", fake_rpc)
    monkeypatch.setattr(
        tx,
        "_load_wallet_entry",
        lambda _addr: {"public_key_hex": "11" * 32, "secret_key_hex": "22" * 32},
    )
    monkeypatch.setattr(tx, "_nonce_lock", lambda _addr: nullcontext())
    monkeypatch.setattr(tx, "_build_tx_body", recording_build_tx_body)
    _patch_signing(monkeypatch)

    result = _invoke_send()
    assert result.exit_code == 0, result.output
    assert nonces == [12]
    assert "state.getNonce" not in calls


def test_send_validity_defaults_use_chain_head_not_sync_hint(monkeypatch) -> None:  # noqa: ANN001
    validity_windows: list[tuple[int, int]] = []
    original_build_tx_body = tx._build_tx_body

    def recording_build_tx_body(*args, **kwargs):  # noqa: ANN001
        validity_windows.append((int(kwargs["valid_after"]), int(kwargs["valid_until"])))
        return original_build_tx_body(*args, **kwargs)

    def fake_rpc(_url: str, method: str, params):  # noqa: ANN001
        if method == "sync.getStatus":
            return {"synchronized": True, "head_height": 5000}
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        if method == "chain.getHead":
            return {"height": 2}
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            return 1
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        if method == "tx.sendRawTransaction":
            return "0xhash"
        if method == "mempool.getStatus":
            return {"hash": params[0], "known": True, "state": "pending", "reason": None}
        return None

    tx._NONCE_CACHE.clear()
    monkeypatch.setattr(tx, "_rpc", fake_rpc)
    monkeypatch.setattr(
        tx,
        "_load_wallet_entry",
        lambda _addr: {"public_key_hex": "11" * 32, "secret_key_hex": "22" * 32},
    )
    monkeypatch.setattr(tx, "_nonce_lock", lambda _addr: nullcontext())
    monkeypatch.setattr(tx, "_build_tx_body", recording_build_tx_body)
    _patch_signing(monkeypatch)

    result = _invoke_send()
    assert result.exit_code == 0, result.output
    assert validity_windows == [(2, 2 + tx.DEFAULT_TX_TTL_BLOCKS)]
