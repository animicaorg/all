"""Provider descriptors.

A descriptor is the small JSON shape downstream consumers
(animica-agent, studio.animica.org, mining pool web page) read to decide
whether a provider is usable and how to call it.

Two descriptors are exported:

  distributed-aicf  — AICF protocol over the configured RPC endpoint
  local-flagship    — Local exported bundle (when present)

Each descriptor includes the fields the spec requires:
  run_id, base_model, requested_base_model, effective_mode,
  available_for_real_inference, fallback_reasons, bundle_path,
  plus provider-specific fields.

Callers:
  agent_runtime.describe_all()             list all descriptors
  agent_runtime.describe_local_flagship()  the local bundle descriptor
  agent_runtime.describe_distributed()     the AICF descriptor

These are pure functions — they read disk + config, never make network
calls or load torch. Cheap to call on every page render.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent_runtime.config import Config, load_config


@dataclass
class LocalFlagshipDescriptor:
    kind: str = "local-flagship"
    available: bool = False
    reason: str = ""
    run_id: str = ""
    base_model: str = ""
    requested_base_model: str = ""
    effective_mode: str = ""
    available_for_real_inference: bool = False
    fallback_reasons: list[str] = field(default_factory=list)
    tier: str = ""
    bundle_path: str = ""
    ipfs_cid: Optional[str] = None
    bundle_sha256: str = ""
    score: float = 0.0
    verdict: str = ""
    schema: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DistributedAICFDescriptor:
    kind: str = "distributed-aicf"
    available: bool = False
    reason: str = ""
    endpoint: str = ""
    network: str = ""
    treasury_address: str = ""
    default_tier: str = ""
    wallet_address: Optional[str] = None
    wallet_balance_animica: Optional[float] = None
    schema: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Describers                                                                  #
# --------------------------------------------------------------------------- #

def describe_local_flagship(cfg: Optional[Config] = None
                            ) -> LocalFlagshipDescriptor:
    cfg = cfg or load_config()
    root_cfg = cfg.integration["agent_runtime"]["providers"]["local-flagship"]
    bundle_root = cfg.repo_root / root_cfg["bundle_root"]
    pointer = bundle_root / root_cfg["bundle_pointer"]
    desc = LocalFlagshipDescriptor()
    if not (pointer.is_symlink() or pointer.is_dir()):
        desc.reason = f"no_bundle_at:{pointer}"
        return desc
    bundle_dir = pointer.resolve() if pointer.is_symlink() else pointer
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        desc.reason = "bundle_missing_manifest"
        return desc
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        desc.reason = f"manifest_invalid: {exc}"
        return desc
    desc.available = bool(m.get("available_for_real_inference", False))
    desc.run_id = str(m.get("run_id", ""))
    desc.base_model = str(m.get("base_model", ""))
    desc.requested_base_model = str(m.get("requested_base_model", ""))
    desc.effective_mode = str(m.get("effective_mode", ""))
    desc.available_for_real_inference = bool(
        m.get("available_for_real_inference", False))
    desc.fallback_reasons = list(m.get("fallback_reasons", []))
    desc.tier = str(m.get("tier", ""))
    desc.bundle_path = str(bundle_dir)
    desc.ipfs_cid = m.get("ipfs_cid")
    desc.bundle_sha256 = str(m.get("bundle_sha256", ""))
    desc.score = float(m.get("score", 0.0))
    desc.verdict = str(m.get("verdict", ""))
    if not desc.available:
        desc.reason = "bundle_not_marked_real_inference"
    return desc


def describe_distributed(cfg: Optional[Config] = None,
                         *, wallet_path: Optional[str] = None,
                         wallet_balance: Optional[float] = None,
                         wallet_address: Optional[str] = None
                         ) -> DistributedAICFDescriptor:
    cfg = cfg or load_config()
    network = os.environ.get("ANIMICA_NETWORK") or "mainnet"
    endpoint = cfg.integration["aicf"]["endpoint"].get(network) or \
        cfg.integration["aicf"]["endpoint"].get("mainnet", "")
    treasury = cfg.integration["aicf"].get("treasury_address",
                                            "aicf-treasury")
    default_tier = cfg.model_catalog["routing"].get("default_tier",
                                                    "small")
    desc = DistributedAICFDescriptor(
        endpoint=endpoint,
        network=network,
        treasury_address=str(treasury),
        default_tier=str(default_tier),
        wallet_address=wallet_address,
        wallet_balance_animica=wallet_balance,
    )
    if not endpoint:
        desc.reason = "no_endpoint_for_network"
        return desc
    if wallet_balance is not None and wallet_balance <= 0.0:
        desc.reason = "wallet_balance_zero"
        return desc
    desc.available = True
    return desc


def describe_all(cfg: Optional[Config] = None) -> list[dict[str, Any]]:
    cfg = cfg or load_config()
    return [
        describe_distributed(cfg).to_dict(),
        describe_local_flagship(cfg).to_dict(),
    ]


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def _main(argv: Optional[list[str]] = None) -> int:
    """`python -m agent_runtime.descriptors` prints all descriptors as JSON."""
    print(json.dumps(describe_all(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
