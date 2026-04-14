from __future__ import annotations

from dataclasses import dataclass, replace

from core.chain.block_import import BlockImporter, ImportErrorCode
from core.genesis.loader import get_genesis, load_chain_params_from_genesis
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams
from mining.hash_search import micro_threshold_to_target256

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


def _make_params(
    *,
    chain_id: int = 1337,
    target_seconds: float = 60.0,
    theta_initial: int = 3_000_000,
    theta_min: int = 1_000_000,
    theta_max: int = 20_000_000,
    window: int = 24,
    ema_alpha: float = 0.35,
) -> ChainParams:
    return ChainParams(
        chain_id=chain_id,
        chain_name="Theta Test Chain",
        genesis_time="2026-01-01T00:00:00Z",
        genesis_hash=ZERO32,
        alg_policy_root=ZERO32,
        poies_policy_root=ZERO32,
        theta_initial=int(theta_initial),
        theta_min=int(theta_min),
        theta_max=int(theta_max),
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(
            window=int(window),
            ema_alpha=float(ema_alpha),
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=float(target_seconds),
            max_bytes=2_000_000,
            max_gas=40_000_000,
            tx_max_bytes=131_072,
            min_gas_price=0,
        ),
    )


def _header(
    *,
    chain_id: int,
    height: int,
    parent_hash: bytes,
    timestamp: int,
    theta_micro: int,
) -> Header:
    return Header(
        v=1,
        chainId=int(chain_id),
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


def _seal_header(header: Header, *, max_nonce: int = 200_000) -> Header:
    target = int(micro_threshold_to_target256(int(header.thetaMicro)))
    for nonce in range(max_nonce):
        candidate = replace(header, nonce=nonce)
        if int.from_bytes(candidate.hash(), "big") <= target:
            return candidate
    raise AssertionError(
        f"failed to find valid nonce under theta={header.thetaMicro} within {max_nonce} attempts"
    )


def test_theta_direction_updates_after_each_canonical_block() -> None:
    params = _make_params(target_seconds=60.0)
    block_db = _InMemoryBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)

    ts = 1_700_000_000
    genesis = _header(
        chain_id=params.chain_id,
        height=0,
        parent_hash=ZERO32,
        timestamp=ts,
        theta_micro=params.theta_initial,
    )
    res0 = importer.import_block(_block(_seal_header(genesis)))
    assert res0.code == ImportErrorCode.ACCEPTED

    theta_for_block_1 = importer.get_current_difficulty()
    block_1 = _header(
        chain_id=params.chain_id,
        height=1,
        parent_hash=res0.block_hash,
        timestamp=ts + 30,  # faster than 60s target -> harder next
        theta_micro=theta_for_block_1,
    )
    res1 = importer.import_block(_block(_seal_header(block_1)))
    assert res1.code == ImportErrorCode.ACCEPTED
    theta_after_fast = importer.get_current_difficulty()
    assert theta_after_fast > theta_for_block_1

    theta_for_block_2 = importer.get_current_difficulty()
    block_2 = _header(
        chain_id=params.chain_id,
        height=2,
        parent_hash=res1.block_hash,
        timestamp=ts + 150,  # slower than 60s target -> easier next
        theta_micro=theta_for_block_2,
    )
    res2 = importer.import_block(_block(_seal_header(block_2)))
    assert res2.code == ImportErrorCode.ACCEPTED
    theta_after_slow = importer.get_current_difficulty()
    assert theta_after_slow < theta_for_block_2


def test_canonical_head_and_next_theta_progress_together() -> None:
    params = _make_params(target_seconds=60.0)
    block_db = _InMemoryBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)

    ts = 1_700_100_000
    genesis = _header(
        chain_id=params.chain_id,
        height=0,
        parent_hash=ZERO32,
        timestamp=ts,
        theta_micro=params.theta_initial,
    )
    res0 = importer.import_block(_block(_seal_header(genesis)))
    assert res0.code == ImportErrorCode.ACCEPTED

    theta_a = importer.get_current_difficulty()
    block_a = _header(
        chain_id=params.chain_id,
        height=1,
        parent_hash=res0.block_hash,
        timestamp=ts + 20,
        theta_micro=theta_a,
    )
    res_a = importer.import_block(_block(_seal_header(block_a)))
    assert res_a.code == ImportErrorCode.ACCEPTED

    head = block_db.get_canonical_head()
    assert head is not None
    head_header = block_db.get_header_by_hash(head[1])
    assert head_header is not None
    assert int(head_header.thetaMicro) == int(theta_a)

    theta_b = importer.get_current_difficulty()
    assert int(theta_b) != int(theta_a)


def test_theta_clamps_do_not_freeze_before_bounds() -> None:
    params = _make_params(
        target_seconds=60.0,
        theta_initial=3_000_000,
        theta_min=2_000_000,
        theta_max=4_000_000,
        window=6,
        ema_alpha=0.6,
    )
    block_db = _InMemoryBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)

    ts = 1_700_200_000
    genesis = _header(
        chain_id=params.chain_id,
        height=0,
        parent_hash=ZERO32,
        timestamp=ts,
        theta_micro=params.theta_initial,
    )
    res = importer.import_block(_block(_seal_header(genesis)))
    assert res.code == ImportErrorCode.ACCEPTED

    parent_hash = res.block_hash
    theta_series: list[int] = [importer.get_current_difficulty()]
    height = 1

    for _ in range(24):
        theta_now = importer.get_current_difficulty()
        ts += 1  # very fast
        blk = _header(
            chain_id=params.chain_id,
            height=height,
            parent_hash=parent_hash,
            timestamp=ts,
            theta_micro=theta_now,
        )
        res = importer.import_block(_block(_seal_header(blk)))
        assert res.code == ImportErrorCode.ACCEPTED
        parent_hash = res.block_hash
        theta_series.append(importer.get_current_difficulty())
        height += 1

    assert max(theta_series) > theta_series[0]
    assert all(v <= params.theta_max for v in theta_series)

    high_point = importer.get_current_difficulty()
    for _ in range(24):
        theta_now = importer.get_current_difficulty()
        ts += 600  # very slow
        blk = _header(
            chain_id=params.chain_id,
            height=height,
            parent_hash=parent_hash,
            timestamp=ts,
            theta_micro=theta_now,
        )
        res = importer.import_block(_block(_seal_header(blk)))
        assert res.code == ImportErrorCode.ACCEPTED
        parent_hash = res.block_hash
        height += 1

    low_point = importer.get_current_difficulty()
    assert low_point < high_point
    assert params.theta_min <= low_point <= params.theta_max


def test_mainnet_and_network_params_target_60_seconds_and_non_frozen_bounds() -> None:
    mainnet_bundle = get_genesis("core/genesis/mainnet.json")
    mainnet_params = load_chain_params_from_genesis(
        mainnet_bundle.genesis, base_dir=mainnet_bundle.base_dir
    )
    assert mainnet_params.block.target_seconds == 60.0
    assert mainnet_params.theta_min < mainnet_params.theta_initial < mainnet_params.theta_max

    for genesis_path in ("core/genesis/testnet.json", "core/genesis/devnet.json"):
        bundle = get_genesis(genesis_path)
        params = load_chain_params_from_genesis(bundle.genesis, base_dir=bundle.base_dir)
        assert params.block.target_seconds == 60.0
