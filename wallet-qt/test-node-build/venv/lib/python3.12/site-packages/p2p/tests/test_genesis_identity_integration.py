import json
from pathlib import Path

import pytest

from core.genesis.loader import compute_genesis_identity, load_and_init_genesis
from p2p.deps import P2PDeps
from p2p.errors import P2PError

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _write_genesis_copy(tmp_path: Path, name: str, *, chain_id: int) -> Path:
    base = json.loads(GENESIS_PATH.read_text(encoding="utf-8"))
    base["chainId"] = chain_id
    out = tmp_path / name
    out.write_text(json.dumps(base, indent=2), encoding="utf-8")
    return out


def _write_genesis_with_extra_alloc(tmp_path: Path, name: str, *, chain_id: int) -> Path:
    base = json.loads(GENESIS_PATH.read_text(encoding="utf-8"))
    base["chainId"] = chain_id
    alloc = list(base.get("alloc", []))
    alloc.append({"address": "system:genesis-extra", "balance": 1, "nonce": 0})
    base["alloc"] = alloc
    out = tmp_path / name
    out.write_text(json.dumps(base, indent=2), encoding="utf-8")
    return out


def test_p2p_deps_accepts_matching_genesis(tmp_path: Path) -> None:
    genesis_path = _write_genesis_copy(tmp_path, "genesis.devnet.json", chain_id=1337)
    identity = compute_genesis_identity(str(genesis_path))

    db_path = tmp_path / "chain.db"
    db_uri = f"sqlite:///{db_path}"
    load_and_init_genesis(str(genesis_path), db_uri, log=False)

    deps = P2PDeps.open(db_uri, str(genesis_path))
    assert deps.expected_genesis_hash == identity.genesis_block_hash
    assert deps.db_genesis_hash == identity.genesis_block_hash


def test_p2p_deps_detects_genesis_mismatch(tmp_path: Path) -> None:
    genesis_a = _write_genesis_copy(tmp_path, "genesis.devnet.json", chain_id=1337)
    genesis_b = _write_genesis_with_extra_alloc(
        tmp_path, "genesis.devnet.alt.json", chain_id=1337
    )

    db_path = tmp_path / "mismatch.db"
    db_uri = f"sqlite:///{db_path}"
    load_and_init_genesis(str(genesis_a), db_uri, log=False)

    with pytest.raises(P2PError, match="GENESIS_MISMATCH"):
        P2PDeps.open(db_uri, str(genesis_b))
