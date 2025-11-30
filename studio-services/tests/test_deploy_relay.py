from __future__ import annotations

import binascii
from dataclasses import dataclass
from typing import Any, Optional

import pytest

# Notes:
# - We monkeypatch the NodeRPC adapter used by the deploy service so no real
#   network calls happen.
# - The API is exercised end-to-end via the ASGI test client.
# - The request body is intentionally minimal and uses hex-encoded CBOR bytes.
#
# Expected deploy response shape (tolerant to extra fields):
#   {
#     "txHash": "0x...",
#     "receipt": { ... }    # only when waiting for receipt
#   }

HEX_TX = "0xa10102"  # tiny CBOR-like bytes; just a placeholder for tests
RAW_TX = binascii.unhexlify(HEX_TX[2:])


@dataclass
class _CallLog:
    sent_raw: Optional[bytes] = None
    waited_for_receipt: bool = False


class FakeNodeRPC:
    """
    Test double for studio_services.adapters.node_rpc.NodeRPC.

    It mimics the small subset of behavior the deploy service relies on:
    - async context manager support
    - send_raw_tx(raw_tx: bytes) -> str
    - get_transaction_receipt(tx_hash: str) -> dict[str, Any]
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._log = kwargs.pop("_log")
        self.rpc_url = kwargs.get("rpc_url", "http://example.invalid")
        self.chain_id = kwargs.get("chain_id", 1)

    async def __aenter__(self) -> "FakeNodeRPC":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def send_raw_tx(self, raw_tx: bytes) -> str:
        self._log.sent_raw = raw_tx
        # Deterministic fake hash (64 'de' nybbles)
        return "0x" + ("de" * 32)

    async def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any]:
        self._log.waited_for_receipt = True
        # Minimal but realistic receipt stub
        return {
            "status": 1,
            "gasUsed": "0x4d2",  # 1234
            "blockNumber": "0x1",
            "transactionHash": tx_hash,
            "logs": [],
        }


@pytest.mark.asyncio
async def test_deploy_relay_sends_raw_tx(aclient, monkeypatch):
    # Patch NodeRPC with our fake
    call_log = _CallLog()
    try:
        import studio_services.adapters.node_rpc as node_rpc  # type: ignore
    except Exception as e:  # pragma: no cover - test environment mismatch
        pytest.skip(f"node_rpc adapter not importable: {e}")

    monkeypatch.setattr(
        node_rpc,
        "NodeRPC",
        lambda *a, **kw: FakeNodeRPC(*a, _log=call_log, **kw),
        raising=True,
    )

    # Fire request (no receipt wait)
    payload = {
        "tx": HEX_TX,
        "wait_for_receipt": False,
    }
    resp = await aclient.post("/deploy", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert "txHash" in data and isinstance(data["txHash"], str)
    assert data["txHash"].startswith("0x")
    # Service should *not* include receipt when wait_for_receipt is False
    assert "receipt" not in data or data["receipt"] is None

    # Verify our fake saw the exact bytes
    assert call_log.sent_raw == RAW_TX
    assert call_log.waited_for_receipt is False


@pytest.mark.asyncio
async def test_deploy_relay_waits_for_receipt(aclient, monkeypatch):
    call_log = _CallLog()
    try:
        import studio_services.adapters.node_rpc as node_rpc  # type: ignore
    except Exception as e:  # pragma: no cover
        pytest.skip(f"node_rpc adapter not importable: {e}")

    monkeypatch.setattr(
        node_rpc,
        "NodeRPC",
        lambda *a, **kw: FakeNodeRPC(*a, _log=call_log, **kw),
        raising=True,
    )

    payload = {
        "tx": HEX_TX,
        "wait_for_receipt": True,
    }
    resp = await aclient.post("/deploy", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "txHash" in data and isinstance(data["txHash"], str)
    # When waiting, a receipt should be present and match our stub
    assert "receipt" in data and isinstance(data["receipt"], dict)
    assert data["receipt"].get("transactionHash") == data["txHash"]

    assert call_log.sent_raw == RAW_TX
    assert call_log.waited_for_receipt is True


@pytest.mark.asyncio
async def test_deploy_rejects_bad_tx_encoding(aclient):
    # Malformed hex should be rejected by pydantic validation or service parsing
    payloads = [
        {"tx": "0xzz", "wait_for_receipt": False},
        {"tx": "", "wait_for_receipt": False},
        {"wait_for_receipt": False},  # missing tx
    ]
    for p in payloads:
        resp = await aclient.post("/deploy", json=p)
        # Either 400 (service-level) or 422 (validation) are acceptable for bad input
        assert resp.status_code in (400, 422)
