from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Iterable, Optional


class GenesisNotFoundError(FileNotFoundError):
    pass


@dataclass(frozen=True)
class GenesisBundle:
    genesis: dict[str, object]
    resolved_path: Optional[Path]
    source: str
    base_dir: Optional[Path]


_GENESIS_CACHE: dict[str, GenesisBundle] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env_genesis_path() -> Optional[Path]:
    env = os.getenv("ANIMICA_GENESIS_PATH") or os.getenv("GENESIS_PATH")
    if not env:
        return None
    return Path(env).expanduser()


def _resolve_relative(path: Path, base: Path) -> Path:
    return (base / path).resolve()


def _candidates_for_relative(path: Path, chain_id: Optional[int]) -> Iterable[Path]:
    repo_root = _repo_root()
    bases = [
        repo_root,
        Path("/app"),
        Path("/etc/animica"),
        Path("/etc/animica/genesis"),
        Path("/config"),
    ]
    for base in bases:
        yield _resolve_relative(path, base)

    if chain_id is not None:
        from core.network_params import get_network_genesis_path

        canonical_path = get_network_genesis_path(chain_id=int(chain_id))
        if canonical_path is not None:
            yield canonical_path

        sample_map = {
            1: repo_root / "genesis" / "genesis.sample.mainnet.json",
            2: repo_root / "genesis" / "genesis.sample.testnet.json",
            1337: repo_root / "genesis" / "genesis.sample.devnet.json",
        }
        sample_path = sample_map.get(int(chain_id))
        if sample_path:
            yield sample_path


def resolve_genesis_path(
    genesis_path: Optional[str | os.PathLike[str]] = None,
    *,
    chain_id: Optional[int] = None,
) -> Path:
    env_path = _env_genesis_path()
    if env_path is not None:
        if env_path.exists():
            return env_path
        raise GenesisNotFoundError(
            f"Genesis file not found at ANIMICA_GENESIS_PATH: {env_path}"
        )

    if genesis_path is not None:
        candidate = Path(genesis_path).expanduser()
        if candidate.is_absolute() and candidate.exists():
            return candidate
        if not candidate.is_absolute():
            for resolved in _candidates_for_relative(candidate, chain_id):
                if resolved.exists():
                    return resolved

    try:
        resource = resources.files("core.genesis").joinpath("genesis.json")
        if resource.is_file():
            with resources.as_file(resource) as res_path:
                if res_path.exists():
                    return res_path
    except Exception:
        pass

    fallback_paths = [
        _repo_root() / "core" / "genesis" / "mainnet.json",
        _repo_root() / "core" / "genesis" / "testnet.json",
        _repo_root() / "core" / "genesis" / "devnet.json",
        _repo_root() / "core" / "genesis" / "genesis.json",
        Path("/app/core/genesis/genesis.json"),
        Path("/etc/animica/genesis/genesis.json"),
        Path("/config/genesis.json"),
    ]
    env_network = (os.getenv("ANIMICA_NETWORK") or "").strip().lower()
    if env_network:
        try:
            from core.network_params import get_network_genesis_path

            canonical = get_network_genesis_path(network_name=env_network)
            if canonical is not None:
                fallback_paths.insert(0, canonical)
        except Exception:
            pass
    if chain_id is not None:
        try:
            from core.network_params import get_network_genesis_path

            canonical = get_network_genesis_path(chain_id=int(chain_id))
            if canonical is not None:
                fallback_paths.insert(0, canonical)
        except Exception:
            pass
        chain_samples = {
            1: _repo_root() / "genesis" / "genesis.sample.mainnet.json",
            2: _repo_root() / "genesis" / "genesis.sample.testnet.json",
            1337: _repo_root() / "genesis" / "genesis.sample.devnet.json",
        }
        sample_path = chain_samples.get(int(chain_id))
        if sample_path:
            fallback_paths.append(sample_path)
    for path in fallback_paths:
        if path.exists():
            return path

    tried = "\n  - ".join(str(p) for p in fallback_paths)
    raise GenesisNotFoundError(
        "Genesis file not found. Set ANIMICA_GENESIS_PATH or provide --genesis.\n"
        f"Tried:\n  - {tried}"
    )


def get_genesis(
    genesis_path: Optional[str | os.PathLike[str]] = None,
    *,
    chain_id: Optional[int] = None,
) -> GenesisBundle:
    resolved = resolve_genesis_path(genesis_path, chain_id=chain_id)
    cache_key = str(resolved)
    cached = _GENESIS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with open(resolved, "r", encoding="utf-8") as f:
        genesis = json.load(f)

    bundle = GenesisBundle(
        genesis=genesis,
        resolved_path=resolved,
        source="file",
        base_dir=resolved.parent,
    )
    _GENESIS_CACHE[cache_key] = bundle
    return bundle


def compute_genesis_sha256(
    genesis_path: Optional[str | os.PathLike[str]] = None,
    *,
    chain_id: Optional[int] = None,
) -> bytes:
    from core.utils.serialization import canonical_dumps

    resolved = resolve_genesis_path(genesis_path, chain_id=chain_id)
    with open(resolved, "r", encoding="utf-8") as f:
        genesis = json.load(f)
    canonical = canonical_dumps(genesis)
    return hashlib.sha256(canonical).digest()


def genesis_tag(
    genesis_path: Optional[str | os.PathLike[str]] = None,
    *,
    chain_id: Optional[int] = None,
    length: int = 8,
) -> str:
    return compute_genesis_sha256(genesis_path, chain_id=chain_id).hex()[:length]
