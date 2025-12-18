from __future__ import annotations

"""Network metadata RPC methods."""

import typing as t

from rpc import deps
from rpc.config import resolve_chain_id
from rpc.methods import method


def _fallback_seeds_from_network(chain_id: int | None) -> list[str]:
    network_name = {1: "mainnet", 2: "testnet", 1337: "devnet"}.get(chain_id, "mainnet")
    try:
        from animica.seeds import get_seed_nodes

        return list(get_seed_nodes(network_name))
    except Exception:
        return []


@method("net.getBootstrapSeeds", desc="Return canonical bootstrap seeds for the active network")
def net_get_bootstrap_seeds() -> dict[str, t.Any]:
    seeds: list[str] = []
    try:
        from p2p.config import load_config

        cfg = load_config()
        seeds = list(getattr(cfg, "seeds", []) or [])
    except Exception:
        seeds = []

    if not seeds:
        try:
            cid = resolve_chain_id()
        except Exception:
            try:
                cid = deps.get_ctx().cfg.chain_id  # type: ignore[attr-defined]
            except Exception:
                cid = None
        seeds = _fallback_seeds_from_network(cid)

    ttl = 120
    return {
        "seeds": seeds[:32],
        "ttl": ttl,
        "chainId": resolve_chain_id(),
    }


__all__ = ["net_get_bootstrap_seeds"]
