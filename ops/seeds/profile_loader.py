#!/usr/bin/env python3
"""Profile-aware seed loader used by ops/run.sh and tests."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

try:  # Optional dependency used to populate the peer store
    from p2p.cli import peer as peer_cli
except (
    Exception
):  # pragma: no cover - fallback to avoid hard dependency when not installed
    peer_cli = None  # type: ignore

SEED_DIR = Path(__file__).resolve().parent
DEFAULT_HOME = Path(os.environ.get("ANIMICA_HOME", Path.home() / ".animica"))
DEFAULT_STORE = DEFAULT_HOME / "p2p" / "peers.json"


@dataclass
class SeedEntry:
    peer_id: str
    multiaddrs: List[str]


def _first_existing(paths: Iterable[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


def load_profile_file(profile: str, seed_dir: Path = SEED_DIR) -> list[SeedEntry]:
    """Load seeds for a profile from JSON (schema matches bootstrap_nodes.json)."""
    candidates = [seed_dir / f"{profile}.json"]
    if profile == "devnet":
        candidates.append(seed_dir / "bootstrap_nodes.json")

    chosen = _first_existing(candidates)
    if chosen is None:
        return []

    try:
        data = json.loads(chosen.read_text())
    except Exception as exc:  # pragma: no cover - user facing
        raise SystemExit(f"Failed to read seeds file {chosen}: {exc}") from exc

    seeds: list[SeedEntry] = []
    for raw in data.get("seeds", []):
        peer_id = str(raw.get("peer_id") or raw.get("id") or raw.get("name") or "seed")
        addrs = [str(a) for a in raw.get("multiaddrs", []) if a]
        if not addrs:
            continue
        seeds.append(SeedEntry(peer_id=peer_id, multiaddrs=addrs))
    return seeds


def dedupe_multiaddrs(seeds: Iterable[SeedEntry]) -> list[str]:
    seen = set()
    out: list[str] = []
    for seed in seeds:
        for addr in seed.multiaddrs:
            if addr in seen:
                continue
            seen.add(addr)
            out.append(addr)
    return out


def write_peerstore(
    seeds: Iterable[SeedEntry], store_path: Path = DEFAULT_STORE
) -> None:
    if peer_cli is None:
        return
    store = peer_cli.StoreFacade(store_path)
    for seed in seeds:
        for addr in seed.multiaddrs:
            store.ensure_addr(seed.peer_id, addr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--profile",
        required=True,
        choices=["devnet", "testnet", "mainnet"],
        help="Profile to load",
    )
    ap.add_argument(
        "--store", type=Path, default=DEFAULT_STORE, help="Peer store path to populate"
    )
    ap.add_argument(
        "--write-peerstore",
        action="store_true",
        help="Populate the peer store with seeds",
    )
    args = ap.parse_args(argv)

    seeds = load_profile_file(args.profile)
    multiaddrs = dedupe_multiaddrs(seeds)
    if args.write_peerstore and seeds:
        write_peerstore(seeds, args.store)

    # Emit comma-separated list for shell consumption
    if multiaddrs:
        print(",".join(multiaddrs))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI
    sys.exit(main())
