import pytest

from p2p.node.p2p_service import P2PService


class _MockBlockDB:
    def get_genesis_hash(self) -> bytes:
        return b"\x11" * 32


class _DepsRaise:
    block_db = _MockBlockDB()
    async def admit_tx(self, _raw, *_args):
        raise TypeError("broken compare")


class _DepsReason:
    block_db = _MockBlockDB()
    async def admit_tx(self, _raw, *_args):
        return False, "pq_verify"


@pytest.mark.asyncio
async def test_admit_tx_result_internal_errors_map_to_trace_id() -> None:
    svc = P2PService(listen_addrs=[], seeds=[], chain_id=1337, deps=_DepsRaise())
    ok, reason = await svc._admit_tx_result(b"abc", local=False, origin_peer="peer-a")
    assert ok is False
    assert isinstance(reason, str)
    assert reason.startswith("internal_error:trace_id=")


@pytest.mark.asyncio
async def test_admit_tx_result_preserves_pq_verify_reason() -> None:
    svc = P2PService(listen_addrs=[], seeds=[], chain_id=1337, deps=_DepsReason())
    ok, reason = await svc._admit_tx_result(b"abc", local=False, origin_peer="peer-a")
    assert ok is False
    assert reason == "pq_verify"
