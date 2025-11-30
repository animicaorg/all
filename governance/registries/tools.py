"""Utility helpers for governance registries and templates."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Set, Tuple

MODULE_OWNERS_PATH = Path("governance/registries/module_owners.yaml")
UPGRADE_PATHS_PATH = Path("governance/registries/upgrade_paths.json")
CONTRACTS_PATH = Path("governance/registries/contracts.json")


@dataclass(frozen=True)
class Module:
    module_id: str
    paths: Tuple[str, ...]
    maintainers: Tuple[str, ...]
    reviewers: Tuple[str, ...]
    backups: Tuple[str, ...]


def _load_yaml(path: Path) -> Mapping:
    import yaml  # PyYAML is an existing optional dependency

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_module_owners(path: Path = MODULE_OWNERS_PATH) -> List[Module]:
    data = _load_yaml(path)
    modules: List[Module] = []
    for entry in data.get("modules", []):
        modules.append(
            Module(
                module_id=str(entry.get("id")),
                paths=tuple(str(p) for p in entry.get("paths", []) if p),
                maintainers=tuple(str(p) for p in entry.get("maintainers", []) if p),
                reviewers=tuple(str(p) for p in entry.get("reviewers", []) if p),
                backups=tuple(str(p) for p in entry.get("backups", []) if p),
            )
        )
    return modules


def match_modules_for_paths(
    changed_paths: Iterable[str],
    modules: Iterable[Module] | None = None,
) -> Dict[str, Set[str]]:
    """Return mapping of repo paths → matching module ids.

    A path may belong to multiple modules if patterns overlap.
    """

    modules = list(modules or load_module_owners())
    matches: Dict[str, Set[str]] = {}
    for raw_path in changed_paths:
        path = raw_path.strip("/\n")
        for mod in modules:
            if any(fnmatch.fnmatch(path, pat) for pat in mod.paths):
                matches.setdefault(path, set()).add(mod.module_id)
    return matches


@dataclass
class ReviewPlan:
    matches: Dict[str, Set[str]]
    reviewers: Set[str]
    maintainers: Set[str]
    backups: Set[str]
    reviewers_min: int
    approvers_min: int


def compute_review_plan(
    changed_paths: Iterable[str],
    registry_path: Path = MODULE_OWNERS_PATH,
) -> ReviewPlan:
    """Compute a simple review plan for the provided diff.

    The plan aggregates maintainers/reviewers/backups for all matched modules and
    carries the default reviewer/approver thresholds for convenience.
    """

    data = _load_yaml(registry_path)
    modules = load_module_owners(registry_path)
    defaults: Mapping[str, object] = data.get("defaults", {})  # type: ignore[assignment]

    matches = match_modules_for_paths(changed_paths, modules)
    touched_ids = {mid for module_ids in matches.values() for mid in module_ids}

    maint: Set[str] = set()
    revs: Set[str] = set()
    backs: Set[str] = set()
    module_lookup: MutableMapping[str, Module] = {m.module_id: m for m in modules}
    for mid in touched_ids:
        mod = module_lookup.get(mid)
        if not mod:
            continue
        maint.update(mod.maintainers)
        revs.update(mod.reviewers)
        backs.update(mod.backups)

    reviewers_min = int(defaults.get("reviewers_min", 1))
    approvers_min = int(defaults.get("approvers_min", 1))

    return ReviewPlan(
        matches=matches,
        reviewers=revs,
        maintainers=maint,
        backups=backs,
        reviewers_min=reviewers_min,
        approvers_min=approvers_min,
    )


def load_upgrade_graph(path: Path = UPGRADE_PATHS_PATH) -> List[Tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    edges: List[Tuple[str, str]] = []
    graph = data.get("graph", {})
    for track_edges in graph.values():
        for edge in track_edges:
            src = str(edge.get("from"))
            dst = str(edge.get("to"))
            if src and dst:
                edges.append((src, dst))
    return edges


def validate_upgrade_graph(path: Path = UPGRADE_PATHS_PATH) -> None:
    edges = load_upgrade_graph(path)
    seen: Set[Tuple[str, str]] = set()
    adj: Dict[str, Set[str]] = {}
    for src, dst in edges:
        if (src, dst) in seen:
            raise ValueError(f"duplicate upgrade edge {src}→{dst}")
        seen.add((src, dst))
        adj.setdefault(src, set()).add(dst)

    visiting: Set[str] = set()
    visited: Set[str] = set()

    def dfs(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            raise ValueError(f"cycle detected at {node}")
        visiting.add(node)
        for nxt in adj.get(node, set()):
            dfs(nxt)
        visiting.remove(node)
        visited.add(node)

    for start in list(adj):
        dfs(start)


def validate_contract_registry(path: Path = CONTRACTS_PATH) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    networks: Mapping[str, Mapping] = data.get("networks", {})  # type: ignore[assignment]

    for net, info in networks.items():
        contracts: Mapping[str, Mapping] = info.get("contracts", {})  # type: ignore[assignment]
        ids: Set[str] = set()
        addrs: Set[str] = set()
        for name, meta in contracts.items():
            cid = str(meta.get("id", "")).strip()
            addr_block = meta.get("address", {})
            hex_addr = None
            if isinstance(addr_block, Mapping):
                hex_addr = addr_block.get("hex")
            elif isinstance(addr_block, str):
                hex_addr = addr_block
            hex_addr = (hex_addr or "").strip()

            if not cid:
                raise ValueError(f"{net}:{name} missing contract id")
            if cid in ids:
                raise ValueError(f"{net} has duplicate id {cid}")
            ids.add(cid)

            if not hex_addr:
                raise ValueError(f"{net}:{cid} missing hex address")
            if hex_addr in addrs:
                raise ValueError(f"{net} reuses address {hex_addr}")
            addrs.add(hex_addr)
