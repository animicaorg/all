from __future__ import annotations

import sqlite3
from pathlib import Path

from core.types.header import Header
from core.utils.pow import micro_threshold_to_target256


def _load_mainnet_genesis_header() -> tuple[Header, str]:
    repo_root = Path(__file__).resolve().parents[3]
    db_path = repo_root / "mainnet" / "chain.db"
    if not db_path.exists():
        raise AssertionError(f"Missing mainnet chain DB fixture at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("select k, v from kv where substr(k,1,1)=?", (b"\x10",))
        row = cur.fetchone()
        if row is None:
            raise AssertionError("No header entries found in mainnet chain DB")
        key, raw = row
        header = Header.from_cbor(raw)
        return header, key[1:].hex()
    finally:
        conn.close()


def test_mainnet_genesis_pow_vector() -> None:
    header, key_hash = _load_mainnet_genesis_header()

    assert key_hash == "1d964197f0def34f190cdfea52a6bed997b9e0f14d8173d0a5e4e4ae2ae3b474"
    assert int(header.height) == 0
    assert int(header.thetaMicro) == 1_000_000

    target = micro_threshold_to_target256(int(header.thetaMicro))
    assert (
        hex(target)
        == "0x5e2d58d8b3bcdf1abadec7829054f90dda9805aab56c77333024b9d0a507daed"
    )

    header_hash_int = int.from_bytes(bytes(header.hash()), "big")
    assert header_hash_int <= target
