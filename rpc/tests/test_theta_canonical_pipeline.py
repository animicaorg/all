from __future__ import annotations

import copy
from dataclasses import dataclass, replace

import pytest

from core.chain.block_import import BlockImporter, ImportErrorCode
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams
from mining.hash_search import micro_threshold_to_target256
from rpc.methods import chain as chain_methods
from rpc.methods import miner as miner_methods

ZERO32 = b"\x00" * 32


@dataclass
class _InMemoryBlockDB:
    headers: dict[bytes, Header]
    blocks: dict[bytes, Block]
    canonical: dict[int, bytes]
    head: tuple[int, bytes] | None
    canonical_height: int

    def __init__(self) -> None:
        self.headers = {}
        self.blocks = {}
        self.canonical = {}
        self.head = None
        self.canonical_height = 0

    def get_header_by_hash(self, block_hash: bytes) -> Header | None:
        return self.headers.get(bytes(block_hash))

    def get_block_by_hash(self, block_hash: bytes) -> Block | None:
        return self.blocks.get(bytes(block_hash))

    def put_header(self, *args):
        if len(args) >= 3:
            _height, block_hash, header = args[:3]
            h = bytes(block_hash)
            self.headers[h] = header
            return h
        header = args[0]
        h = header.hash()
        self.headers[h] = header
        return h

    def put_block(self, *args):
        if len(args) >= 2:
            block_hash, block = args[:2]
            h = bytes(block_hash)
            self.blocks[h] = block
            self.headers[h] = block.header
            return h
        block = args[0]
        h = block.header.hash()
        self.blocks[h] = block
        self.headers[h] = block.header
        return h

    def get_canonical_head(self):
        return self.head

    def set_head(self, height: int, block_hash: bytes, **_kwargs) -> None:
        self.head = (int(height), bytes(block_hash))

    def set_canonical(self, height: int, block_hash: bytes, **_kwargs) -> None:
        self.canonical[int(height)] = bytes(block_hash)

    def set_canonical_head(self, height: int, block_hash: bytes, **_kwargs) -> None:
        self.set_canonical(height, block_hash, allow_overwrite=True)
        self.set_head(height, block_hash, allow_reorg=True)

    def get_canonical_hash(self, height: int) -> bytes | None:
        return self.canonical.get(int(height))

    def get_canonical_height(self) -> int:
        return int(self.canonical_height)

    def set_canonical_height(self, height: int) -> None:
        self.canonical_height = int(height)


def _make_params() -> ChainParams:
    return ChainParams(
        chain_id=1337,
        chain_name="RPC Theta Test",
        genesis_time="2026-01-01T00:00:00Z",
        genesis_hash=ZERO32,
        alg_policy_root=ZERO32,
        poies_policy_root=ZERO32,
        theta_initial=2_000_000,
        theta_min=500_000,
        theta_max=20_000_000,
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(
            window=8,
            ema_alpha=0.5,
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=60.0,
            max_bytes=2_000_000,
            max_gas=40_000_000,
            tx_max_bytes=131_072,
            min_gas_price=0,
        ),
    )


def _header(
    *,
    height: int,
    parent_hash: bytes,
    timestamp: int,
    theta_micro: int,
) -> Header:
    return Header(
        v=1,
        chainId=1337,
        height=int(height),
        parentHash=bytes(parent_hash),
        timestamp=int(timestamp),
        stateRoot=ZERO32,
        txsRoot=ZERO32,
        receiptsRoot=ZERO32,
        proofsRoot=ZERO32,
        daRoot=ZERO32,
        mixSeed=ZERO32,
        poiesPolicyRoot=ZERO32,
        pqAlgPolicyRoot=ZERO32,
        thetaMicro=int(theta_micro),
        nonce=0,
        extra=b"",
    )


def _block(header: Header) -> Block:
    return Block(header=header, txs=(), proofs=(), receipts=None)


def _seal(header: Header, *, max_nonce: int = 200_000) -> Header:
    target = int(micro_threshold_to_target256(int(header.thetaMicro)))
    for nonce in range(max_nonce):
        candidate = replace(header, nonce=nonce)
        if int.from_bytes(candidate.hash(), "big") <= target:
            return candidate
    raise AssertionError(
        f"failed to find valid nonce under theta={header.thetaMicro} within {max_nonce} attempts"
    )


class _FakeCfg:
    chain_id = 1337
    genesis_path = "core/genesis/devnet.json"


class _FakeCtx:
    def __init__(self, block_db: _InMemoryBlockDB):
        self.cfg = _FakeCfg()
        self.block_db = block_db
        self.state_db = None
        self.tx_index = None
        self.params = {"monetary": {"issuance": {"target_block_interval_ms": 60000}}}

    def get_head(self):
        head = self.block_db.get_canonical_head()
        if head is None:
            return {"height": 0, "hash": None, "header": None}
        height, block_hash = head
        return {
            "height": int(height),
            "hash": "0x" + block_hash.hex(),
            "header": self.block_db.get_header_by_hash(block_hash),
        }


def _snapshot_miner_globals() -> dict[str, dict]:
    with miner_methods._HEAD_RW_LOCK:
        with miner_methods._TEMPLATE_CACHE_LOCK:
            return {
                "job_cache": dict(miner_methods._JOB_CACHE),
                "template_cache": dict(miner_methods._TEMPLATE_CACHE),
                "local_head": dict(miner_methods._LOCAL_HEAD),
                "head_state": dict(miner_methods._HEAD_STATE),
                "mining_state": copy.deepcopy(miner_methods._MINING_STATE),
            }


def _restore_miner_globals(snapshot: dict[str, dict]) -> None:
    with miner_methods._HEAD_RW_LOCK:
        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE.update(snapshot["job_cache"])
        miner_methods._LOCAL_HEAD.clear()
        miner_methods._LOCAL_HEAD.update(snapshot["local_head"])
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update(snapshot["head_state"])
        miner_methods._MINING_STATE.clear()
        miner_methods._MINING_STATE.update(snapshot["mining_state"])
        with miner_methods._TEMPLATE_CACHE_LOCK:
            miner_methods._TEMPLATE_CACHE.clear()
            miner_methods._TEMPLATE_CACHE.update(snapshot["template_cache"])


def test_get_work_surfaces_updated_canonical_theta_after_block_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.chain.block_import as block_import_mod

    params = _make_params()
    block_db = _InMemoryBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    ctx = _FakeCtx(block_db)

    ts = 1_700_300_000
    genesis = _seal(
        _header(height=0, parent_hash=ZERO32, timestamp=ts, theta_micro=params.theta_initial)
    )
    res0 = importer.import_block(_block(genesis))
    assert res0.code == ImportErrorCode.ACCEPTED

    snapshot = _snapshot_miner_globals()
    try:
        monkeypatch.setattr(miner_methods, "_ctx", lambda: ctx)
        monkeypatch.setattr(miner_methods, "_mining_gate", lambda **_kw: (True, None))
        monkeypatch.setattr(
            block_import_mod, "_load_chain_params_for_import", lambda _gp: params
        )
        monkeypatch.setattr(
            block_import_mod, "_get_importer", lambda *_args, **_kwargs: importer
        )

        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update(
            {"height": None, "hash": None, "theta": None, "generation": 0}
        )

        work0 = miner_methods.miner_get_work()
        theta0 = int(work0["thetaMicro"])
        assert theta0 == importer.get_current_difficulty()

        block1 = _seal(
            _header(
                height=1,
                parent_hash=res0.block_hash,
                timestamp=ts + 20,  # fast block -> theta should increase next
                theta_micro=theta0,
            )
        )
        res1 = importer.import_block(_block(block1))
        assert res1.code == ImportErrorCode.ACCEPTED

        work1 = miner_methods.miner_get_work()
        theta1 = int(work1["thetaMicro"])
        assert theta1 == importer.get_current_difficulty()
        assert theta1 > theta0
    finally:
        _restore_miner_globals(snapshot)


def test_chain_get_head_surfaces_updated_canonical_theta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _make_params()
    block_db = _InMemoryBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    ctx = _FakeCtx(block_db)

    ts = 1_700_400_000
    genesis = _seal(
        _header(height=0, parent_hash=ZERO32, timestamp=ts, theta_micro=params.theta_initial)
    )
    res0 = importer.import_block(_block(genesis))
    assert res0.code == ImportErrorCode.ACCEPTED

    theta0 = importer.get_current_difficulty()
    block1 = _seal(
        _header(
            height=1,
            parent_hash=res0.block_hash,
            timestamp=ts + 20,
            theta_micro=theta0,
        )
    )
    res1 = importer.import_block(_block(block1))
    assert res1.code == ImportErrorCode.ACCEPTED

    monkeypatch.setattr(chain_methods.deps, "get_head", ctx.get_head)
    monkeypatch.setattr(chain_methods.deps, "get_chain_id", lambda: params.chain_id)

    head = chain_methods.chain_get_head()
    assert int(head["height"]) == 1
    assert int(head["thetaMicro"]) == int(theta0)

    theta1 = importer.get_current_difficulty()
    block2 = _seal(
        _header(
            height=2,
            parent_hash=res1.block_hash,
            timestamp=ts + 180,
            theta_micro=theta1,
        )
    )
    res2 = importer.import_block(_block(block2))
    assert res2.code == ImportErrorCode.ACCEPTED

    head2 = chain_methods.chain_get_head()
    assert int(head2["height"]) == 2
    assert int(head2["thetaMicro"]) == int(theta1)


def test_theta_change_invalidates_caches_even_when_head_hash_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ThetaOnlyCtx:
        def __init__(self):
            self.theta = 1_000_000

        def get_head(self):
            return {
                "height": 5,
                "hash": "0x" + ("ab" * 32),
                "header": {"thetaMicro": int(self.theta)},
            }

    ctx = _ThetaOnlyCtx()
    snapshot = _snapshot_miner_globals()
    try:
        monkeypatch.setattr(miner_methods, "_ctx", lambda: ctx)
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update(
            {
                "height": 5,
                "hash": "0x" + ("ab" * 32),
                "theta": 1_000_000,
                "generation": 9,
            }
        )
        miner_methods._JOB_CACHE["j1"] = {"height": 6}
        with miner_methods._TEMPLATE_CACHE_LOCK:
            miner_methods._TEMPLATE_CACHE["t1"] = {"parent_hash": "0x" + ("ab" * 32)}

        same = miner_methods._current_head_snapshot()
        assert int(same["generation"]) == 9
        assert "j1" in miner_methods._JOB_CACHE

        ctx.theta = 1_250_000
        moved = miner_methods._current_head_snapshot()
        assert int(moved["generation"]) == 10
        assert miner_methods._JOB_CACHE == {}
        with miner_methods._TEMPLATE_CACHE_LOCK:
            assert miner_methods._TEMPLATE_CACHE == {}
    finally:
        _restore_miner_globals(snapshot)


def test_target_block_time_helper_uses_60_seconds_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Ctx:
        params = {"monetary": {"issuance": {"target_block_interval_ms": 60000}}}

    monkeypatch.setattr(miner_methods, "_ctx", lambda: _Ctx())
    assert miner_methods._target_block_time_s() == 60.0


def test_timestamp_bounds_reads_timestamp_from_dict_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(miner_methods.time, "time", lambda: 1_100.0)
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MAX_FUTURE_SECONDS", "5")

    timestamp_min, _timestamp_max, _candidate = miner_methods._timestamp_bounds(
        {"timestamp": 1_000}
    )
    assert timestamp_min == 1_000


def test_stale_head_interval_triggers_once_per_target_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._MINING_STATE.clear()
        miner_methods._MINING_STATE.update(
            {
                "last_block_time": None,
                "block_times": [],
                "theta_state": None,
                "adjustment_enabled": True,
                "last_network_height": 100,
                "last_network_timestamp": 1_000,
                "stale_head_hash": None,
                "stale_head_bucket": 0,
            }
        )

        monkeypatch.setattr(miner_methods, "_target_block_time_s", lambda default=60.0: 60.0)
        monkeypatch.setattr(miner_methods.time, "time", lambda: 1_100.0)

        dt1 = miner_methods._stale_head_interval("0xabc", 1_000)
        assert dt1 is not None
        assert dt1[0] == pytest.approx(100.0)
        assert dt1[1] == 1

        monkeypatch.setattr(miner_methods.time, "time", lambda: 1_119.0)
        assert miner_methods._stale_head_interval("0xabc", 1_000) is None

        monkeypatch.setattr(miner_methods.time, "time", lambda: 1_125.0)
        dt2 = miner_methods._stale_head_interval("0xabc", 1_000)
        assert dt2 is not None
        assert dt2[0] == pytest.approx(125.0)
        assert dt2[1] == 1

        monkeypatch.setattr(miner_methods.time, "time", lambda: 1_130.0)
        assert miner_methods._stale_head_interval("0xdef", 1_090) is None

        monkeypatch.setattr(miner_methods.time, "time", lambda: 1_151.0)
        dt3 = miner_methods._stale_head_interval("0xdef", 1_090)
        assert dt3 is not None
        assert dt3[0] == pytest.approx(61.0)
        assert dt3[1] == 1
    finally:
        _restore_miner_globals(snapshot)


def test_stale_head_interval_reports_catch_up_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._MINING_STATE.clear()
        miner_methods._MINING_STATE.update(
            {
                "last_block_time": None,
                "block_times": [],
                "theta_state": None,
                "adjustment_enabled": True,
                "last_network_height": 100,
                "last_network_timestamp": 1_000,
                "stale_head_hash": "0xabc",
                "stale_head_bucket": 1,
            }
        )

        monkeypatch.setattr(miner_methods, "_target_block_time_s", lambda default=60.0: 60.0)
        monkeypatch.setattr(miner_methods.time, "time", lambda: 1_245.0)

        dt = miner_methods._stale_head_interval("0xabc", 1_000)
        assert dt is not None
        assert dt[0] == pytest.approx(245.0)
        assert dt[1] == 3
    finally:
        _restore_miner_globals(snapshot)


def test_network_block_interval_uses_mean_dt_per_step() -> None:
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._MINING_STATE.clear()
        miner_methods._MINING_STATE.update(
            {
                "last_block_time": None,
                "block_times": [],
                "theta_state": None,
                "adjustment_enabled": True,
                "last_network_height": 100,
                "last_network_timestamp": 1_000,
                "stale_head_hash": "0xabc",
                "stale_head_bucket": 2,
            }
        )

        dt = miner_methods._network_block_interval(103, 1_180)
        assert dt is not None
        # 180 seconds over 3 blocks should be modeled as 60s per step with 3 steps.
        assert dt[0] == pytest.approx(60.0)
        assert dt[1] == 3
    finally:
        _restore_miner_globals(snapshot)
