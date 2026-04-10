from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.chain.block_import import BlockImporter
from core.genesis.loader import load_genesis
from core.utils.hash import ZERO32
from core.utils.pow import micro_threshold_to_target256


@dataclass
class _DummyBlockDB:
    def get_canonical_head(self):  # noqa: ANN001
        return None

    def get_genesis_hash(self):  # noqa: ANN001
        return None

    def get_canonical_hash(self, _height):  # noqa: ANN001
        return None

    def get_header_by_hash(self, _hash):  # noqa: ANN001
        return None


def test_pow_sanity_accepts_mined_header() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    genesis_path = repo_root / "core" / "genesis" / "genesis.json"
    params, genesis_header = load_genesis(str(genesis_path))

    base = genesis_header.build_child(
        timestamp=int(getattr(genesis_header, "timestamp", 0)) + 1,
        state_root=genesis_header.stateRoot,
        txs_root=ZERO32,
        receipts_root=ZERO32,
        proofs_root=ZERO32,
        da_root=ZERO32,
        theta_micro=int(genesis_header.thetaMicro),
        nonce=0,
        extra=b"",
    )

    target = micro_threshold_to_target256(int(base.thetaMicro))
    mined = None
    for nonce in range(0, 100_000):
        candidate = base.with_nonce(nonce)
        header_hash = candidate.hash()
        if int.from_bytes(header_hash, "big") <= target:
            mined = candidate
            break

    assert mined is not None, "Failed to find a nonce meeting the PoW target"

    importer = BlockImporter(params=params, block_db=_DummyBlockDB())
    pow_error = importer._pow_sanity(
        header=mined, header_hash=mined.hash(), payload=mined.to_obj()
    )
    assert pow_error is None
