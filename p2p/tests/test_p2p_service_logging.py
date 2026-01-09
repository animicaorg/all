import asyncio
from types import SimpleNamespace

import pytest

from p2p.node.p2p_service import P2PService
from p2p.wire.encoding import encode_payload


@pytest.mark.asyncio
async def test_p2p_service_logger_and_snapshot_handler(tmp_path):
    service = P2PService(chain_id=1, peerstore_path=tmp_path / "p2p")
    assert service._log is not None

    async def _send_stub(peer, msg_id, payload_obj):
        return None

    service._send = _send_stub  # type: ignore[assignment]

    peer = SimpleNamespace(remote="peer-1")
    payload = encode_payload({"chain_id": 1})
    await service._handle_get_snapshots(peer, payload)
