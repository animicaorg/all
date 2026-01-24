from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.chain.head import finalize_genesis
from core.errors import GenesisMismatchError
from core.genesis.loader import load_and_init_genesis, load_genesis


def _write_genesis(path: Path, *, genesis_time: str) -> None:
    genesis = {
        "meta": {"version": 1, "description": "test genesis"},
        "chainId": 1337,
        "network": "animica-devnet",
        "genesisTime": genesis_time,
        "unit": {"symbol": "ANM", "decimals": 9},
        "paramsRef": {"path": "spec/params.yaml"},
        "economics": {"premineTotal": "100"},
        "alloc": [{"address": "system:test", "nonce": 0, "balance": "100"}],
        "consensus": {"initialThetaMicro": 1_000_000, "gammaCapMicro": 2_000_000},
    }
    path.write_text(json.dumps(genesis), encoding="utf-8")


def test_finalize_genesis_rejects_mismatched_db(tmp_path: Path) -> None:
    db_path = tmp_path / "chain.db"
    genesis_a = tmp_path / "genesis_a.json"
    genesis_b = tmp_path / "genesis_b.json"

    _write_genesis(genesis_a, genesis_time="2026-05-01T00:00:00Z")
    _write_genesis(genesis_b, genesis_time="2026-05-02T00:00:00Z")

    result = load_and_init_genesis(
        str(genesis_a),
        f"sqlite:///{db_path}",
        override_chain_id=1337,
        log=False,
    )

    params_b, header_b = load_genesis(str(genesis_b))
    with pytest.raises(GenesisMismatchError):
        finalize_genesis(
            result["blocks"],
            params_b,
            header_b,
            genesis_path=str(genesis_b),
        )
