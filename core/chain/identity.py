from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ChainIdentity:
    chain_id: int
    genesis_hash: bytes
    fork_id: int
    consensus_id: str
    protocol_version: str


def _parse_fork_id(value: Any) -> int:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v.startswith("0x"):
            return int(v, 16)
        if v.isdigit():
            return int(v)
    raise ValueError(f"Invalid fork_id value: {value!r}")


def derive_fork_id(genesis_hash: bytes, *, explicit: Any = None) -> int:
    if explicit is not None:
        fork_id = _parse_fork_id(explicit)
    else:
        fork_id = zlib.crc32(bytes(genesis_hash)) & 0xFFFFFFFF
    if fork_id < 0 or fork_id > 0xFFFFFFFF:
        raise ValueError(f"fork_id out of range: {fork_id}")
    return int(fork_id)


def consensus_id_from_genesis(genesis: dict[str, Any]) -> str:
    cons = genesis.get("consensus") or {}
    candidate = cons.get("consensusId") or cons.get("consensus_id") or cons.get("id")
    if candidate:
        return str(candidate)
    try:
        from consensus import version as consensus_version

        ver = str(getattr(consensus_version, "__version__", "unknown"))
    except Exception:
        ver = "unknown"
    return f"poies/{ver}"


def protocol_version_from_runtime() -> str:
    try:
        from p2p.protocol import PROTOCOL_MAJOR, PROTOCOL_MINOR

        return f"{int(PROTOCOL_MAJOR)}.{int(PROTOCOL_MINOR)}"
    except Exception:
        return "1.0"


def tx_signing_context(fork_id: int) -> bytes:
    fid = int(fork_id)
    if fid < 0 or fid > 0xFFFFFFFF:
        raise ValueError(f"fork_id out of range: {fid}")
    return b"fork_id:" + fid.to_bytes(4, "big", signed=False)

