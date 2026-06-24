"""net_* and web3_* Ethereum-compatible methods."""

from __future__ import annotations

from rpc.methods import method

from . import formatters as F


def _chain_id() -> int:
    try:
        return int(F.native("chain.getChainId"))
    except Exception:
        return F.from_q((F.native("node.health") or {}).get("chain_id"), 0)


@method("net_version", desc="EVM-compat: network id (decimal string).")
def net_version() -> str:
    return str(_chain_id())


@method("net_listening", desc="EVM-compat: node is listening for connections.")
def net_listening() -> bool:
    return True


@method("net_peerCount", desc="EVM-compat: connected peer count (hex).")
def net_peerCount() -> str:
    return F.q(F.from_q((F.native("node.health") or {}).get("peers_connected"), 0))


@method("web3_clientVersion", desc="EVM-compat: client version string.")
def web3_clientVersion() -> str:
    ver = str((F.native("node.health") or {}).get("version", "3.0.1"))
    return f"Animica/v{ver}/evm-facade"


@method("web3_sha3", desc="EVM-compat: keccak-256 of the given data.")
def web3_sha3(data: str) -> str:
    s = str(data or "0x")
    raw = bytes.fromhex(s[2:] if s.startswith(("0x", "0X")) else s)
    return "0x" + F.keccak256(raw).hex()
