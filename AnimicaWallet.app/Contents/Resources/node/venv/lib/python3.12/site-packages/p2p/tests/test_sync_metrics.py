import pytest

from p2p.node.p2p_service import P2PService


@pytest.mark.asyncio
async def test_debug_status_includes_sync_metrics(tmp_path) -> None:
    service = P2PService(chain_id=1, peerstore_path=tmp_path / "p2p")
    result = await service.debug_status()
    assert "sync_metrics" in result
    metrics = result["sync_metrics"]
    assert metrics["download_inflight_blocks"] == 0
    assert metrics["download_queue_depth"] == 0
    assert "verify_ms_per_block" in metrics
