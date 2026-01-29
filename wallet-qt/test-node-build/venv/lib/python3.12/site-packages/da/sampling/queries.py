"""
Animica • DA • Sampling Query Builders

Utilities to construct index query plans for Data Availability Sampling (DAS).
These helpers are *pure* (no I/O) and are suitable for building explicit index
lists that a sampler/retrieval API can fetch proofs for.

Key ideas
---------
- Uniform sampling over the full population of leaves/shares.
- Stratified sampling across namespaces (with optional per-namespace ranges).
- Largest Remainder (Hamilton) method for splitting a total budget across strata.
- Best-effort de-duplication across strata when sampling without replacement.

This module complements:
  - da.sampling.scheduler.SamplingScheduler (which may choose to generate
    indices explicitly or let the sampler pick),
  - da.sampling.probability (to size k for a target p_fail),
  - da.sampling.verifier / light_client (which verify returned proofs).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ------------------------------ Data types ----------------------------------


@dataclass(frozen=True)
class SampleQuery:
    """A batch of indices to query, optionally scoped to a namespace."""

    namespace: Optional[int]
    indices: List[int]


@dataclass(frozen=True)
class QueryPlan:
    """A collection of sample batches to request."""

    batches: List[SampleQuery]

    @property
    def total(self) -> int:
        return sum(len(b.indices) for b in self.batches)

    def as_payloads(self) -> List[Mapping[str, object]]:
        """
        Convert to a JSON-ish list suitable for samplers that accept explicit
        indices. Each payload minimally includes 'indices' and, if set,
        'namespace'.
        """
        out: List[Mapping[str, object]] = []
        for b in self.batches:
            payload: Dict[str, object] = {"indices": list(map(int, b.indices))}
            if b.namespace is not None:
                payload["namespace"] = int(b.namespace)
            out.append(payload)
        return out


__all__ = [
    "SampleQuery",
    "QueryPlan",
    "uniform_indices",
    "uniform_plan",
    "stratified_uniform",
    "stratified_by_ranges",
    "merge_plans",
]

# ------------------------------ Core builders --------------------------------


def uniform_indices(
    population_size: int,
    k: int,
    *,
    seed: Optional[int] = None,
    without_replacement: bool = True,
    exclude: Optional[Iterable[int]] = None,
) -> List[int]:
    """
    Draw k indices uniformly from [0, population_size).

    If without_replacement=True, attempts to avoid duplicates (and anything in
    'exclude'). Falls back to filling any remaining slots deterministically if
    randomness yields too many collisions.

    NOTE: For very large populations, this routine avoids materializing full
    ranges unless necessary.
    """
    n = int(population_size)
    if n <= 0 or k <= 0:
        return []

    rng = random.Random(seed)
    k = int(k)
    out: List[int] = []
    seen = set(int(x) for x in (exclude or [])) if without_replacement else set()

    if without_replacement and k >= n - len(seen):
        # Trivial: take all remaining elements shuffled.
        pool = [i for i in range(n) if i not in seen]
        rng.shuffle(pool)
        return pool[:k]

    # Fast path: rejection sampling with a cap on attempts.
    max_attempts = max(8 * k, 1024)
    attempts = 0
    while len(out) < k and attempts < max_attempts:
        idx = rng.randrange(n)
        attempts += 1
        if without_replacement:
            if idx in seen:
                continue
            seen.add(idx)
        out.append(idx)

    # If we couldn't finish (due to collisions), fill deterministically.
    if len(out) < k and without_replacement:
        deficit = k - len(out)
        # Avoid building the full list if n is huge: scan sequentially with wrap.
        start = rng.randrange(n)
        i = 0
        while deficit > 0 and i < n:
            cand = (start + i) % n
            i += 1
            if cand in seen:
                continue
            seen.add(cand)
            out.append(cand)
            deficit -= 1

    return out


def uniform_plan(
    population_size: int,
    k: int,
    *,
    namespace: Optional[int] = None,
    seed: Optional[int] = None,
    without_replacement: bool = True,
    exclude: Optional[Iterable[int]] = None,
) -> QueryPlan:
    """
    One-batch plan: uniform indices over the full population (optionally tagged
    with a namespace hint).
    """
    return QueryPlan(
        batches=[
            SampleQuery(
                namespace=namespace,
                indices=uniform_indices(
                    population_size,
                    k,
                    seed=seed,
                    without_replacement=without_replacement,
                    exclude=exclude,
                ),
            )
        ]
    )


def stratified_uniform(
    population_size: int,
    k_total: int,
    *,
    namespaces: Sequence[int],
    weights: Optional[Sequence[float]] = None,
    seed: Optional[int] = None,
    without_replacement: bool = True,
    dedupe_across_namespaces: bool = True,
) -> QueryPlan:
    """
    Split k_total across the given namespaces (by weights or equally) and pick
    uniform indices from the *global* population for each namespace.

    This is useful when your retrieval service allows asking for a namespace
    constraint but the global index space is shared. When dedupe_across_namespaces
    is True, we avoid picking the same global index for multiple namespaces.

    If you know the concrete index ranges per namespace, prefer
    stratified_by_ranges() for sharper control.
    """
    if not namespaces:
        return uniform_plan(
            population_size, k_total, seed=seed, without_replacement=without_replacement
        )

    rng = random.Random(seed)
    k_splits = _allocate_counts(k_total, _normalize_weights(namespaces, weights))
    seen_global: set[int] = set()
    batches: List[SampleQuery] = []

    for ns, k in zip(namespaces, k_splits):
        if k <= 0:
            batches.append(SampleQuery(namespace=ns, indices=[]))
            continue
        exclude = (
            seen_global if (without_replacement and dedupe_across_namespaces) else None
        )
        idxs = uniform_indices(
            population_size,
            k,
            seed=rng.randrange(1 << 63),
            without_replacement=without_replacement,
            exclude=exclude,
        )
        if exclude is not None:
            seen_global.update(idxs)
        batches.append(SampleQuery(namespace=ns, indices=idxs))

    return QueryPlan(batches=batches)


def stratified_by_ranges(
    ns_ranges: Mapping[int, Tuple[int, int]],
    k_total: int,
    *,
    weights: Optional[Sequence[float]] = None,
    seed: Optional[int] = None,
    without_replacement: bool = True,
    dedupe_across_namespaces: bool = False,
) -> QueryPlan:
    """
    Stratified sampling where each namespace owns a concrete index range:
      ns_ranges[ns] = (start_inclusive, end_exclusive)  over the *global* index space.

    We split k_total by weights (default: proportional to each range size), then
    sample uniformly *within* each namespace's range. If dedupe_across_namespaces
    is True, we still ensure no global index appears in multiple strata even if
    ranges overlap (unusual but handled).
    """
    if not ns_ranges:
        return QueryPlan(batches=[])

    rng = random.Random(seed)
    # Default weights proportional to range sizes if not provided.
    if weights is None:
        weights = [max(0, end - start) for (_, (start, end)) in ns_ranges.items()]

    namespaces = list(ns_ranges.keys())
    k_splits = _allocate_counts(k_total, _normalize_weights(namespaces, weights))

    seen_global: set[int] = set()
    batches: List[SampleQuery] = []

    for ns, k in zip(namespaces, k_splits):
        start, end = ns_ranges[ns]
        n = max(0, int(end) - int(start))
        if n <= 0 or k <= 0:
            batches.append(SampleQuery(namespace=ns, indices=[]))
            continue

        # Local sampling within [start, end)
        exclude_local: Optional[Iterable[int]] = None
        if without_replacement and dedupe_across_namespaces:
            # Convert seen_global into local indices if within range
            exclude_local = (i for i in seen_global if start <= i < end)

        local_indices = uniform_indices(
            n,
            k,
            seed=rng.randrange(1 << 63),
            without_replacement=without_replacement,
            exclude=((i - start) for i in exclude_local) if exclude_local else None,
        )
        # Map back to global
        global_indices = [start + i for i in local_indices]
        if dedupe_across_namespaces and without_replacement:
            seen_global.update(global_indices)
        batches.append(SampleQuery(namespace=ns, indices=global_indices))

    return QueryPlan(batches=batches)


def merge_plans(plans: Sequence[QueryPlan]) -> QueryPlan:
    """Concatenate multiple plans."""
    batches: List[SampleQuery] = []
    for p in plans:
        batches.extend(p.batches)
    return QueryPlan(batches=batches)


# ------------------------------ Helpers -------------------------------------


def _allocate_counts(total: int, weights: Sequence[float]) -> List[int]:
    """
    Hamilton (Largest Remainder) apportionment: allocate 'total' integer counts
    proportionally to 'weights' while preserving sum == total.
    """
    t = max(0, int(total))
    m = len(weights)
    if m == 0 or t == 0:
        return [0] * m

    w = [max(0.0, float(x)) for x in weights]
    s = sum(w)
    if s <= 0:
        # equal split
        base = [t // m] * m
        for i in range(t % m):
            base[i] += 1
        return base

    quotas = [t * wi / s for wi in w]
    floors = [int(math.floor(q)) for q in quotas]
    remain = t - sum(floors)
    remainders = sorted(
        [(i, quotas[i] - floors[i]) for i in range(m)],
        key=lambda x: x[1],
        reverse=True,
    )
    for j in range(remain):
        floors[remainders[j % m][0]] += 1
    return floors


def _normalize_weights(
    namespaces: Sequence[int], weights: Optional[Sequence[float]]
) -> List[float]:
    if weights is None:
        return [1.0] * len(namespaces)
    if len(weights) != len(namespaces):
        raise ValueError("weights length must match namespaces length")
    return [float(w) for w in weights]
