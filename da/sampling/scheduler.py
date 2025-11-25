"""
Animica • DA • Sampling Scheduler

Policy-driven, periodic Data Availability Sampling (DAS) runner for nodes or L1
contracts that want continuous assurance that posted blobs are actually
available. This module coordinates:

  - obtaining the current DA commitment (NMT root) + population size,
  - requesting random samples/proofs from a DA retrieval service,
  - verifying those samples, and
  - deciding availability against a target failure probability.

It is intentionally backend-agnostic. You inject:
  • a "head provider" that returns (commitment, population_size, height),
  • a "sampler" that fetches proofs for random indices, and
  • an optional "report handler" to log/emit metrics/notify RPC.

No network I/O or global state is assumed here beyond what the injected
sampler uses.

Quick sketch
------------
from da.sampling.scheduler import (
    SamplingPolicy, SamplingScheduler, HeadInfo,
)

async def get_head() -> HeadInfo:
    # Return the latest header’s DA root & population (number of leaves/shares).
    root = bytes.fromhex("...")   # read from your core DB/header
    return HeadInfo(commitment=root, population_size=65536, height=1234)

# Build a sampler using your DA retrieval API client.
from da.sampling.sampler import Sampler
sampler = Sampler(base_url="http://127.0.0.1:8549")

async def on_report(report, head):
    print(head.height, "available?" , report.available, "p_fail≈", report.p_fail_estimate, report.reasons)

policy = SamplingPolicy(target_p_fail=1e-9, per_block_samples=64, max_concurrency=2)
sched = SamplingScheduler(get_head_fn=get_head, sampler=sampler, on_report=on_report, policy=policy)
await sched.run_forever(poll_interval_sec=5.0)

Notes
-----
- This module leans on:
    da.sampling.light_client.light_verify
    da.sampling.probability.estimate_p_fail_upper (via light_client)
- The concrete Sampler interface is duck-typed. See DefaultSamplerAdapter below.
- All timing/backoff/jitter is handled locally; you may wrap this in your
  service supervisor for production restarts.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable

# ------------------------------ Types & Policy ------------------------------

@dataclass(frozen=True)
class HeadInfo:
    commitment: bytes
    population_size: int
    height: int


@dataclass(frozen=True)
class SamplingPolicy:
    """
    Tuning knobs for the sampling scheduler.

    - target_p_fail: desired failure probability bound for light verification.
    - per_block_samples: how many unique samples to attempt per head (total across namespaces).
    - namespaces: optional list of namespaces to stratify samples across. If provided,
      per-block sample budget is partitioned across them as evenly as possible.
    - max_concurrency: cap concurrent in-flight sampling tasks per head.
    - require_no_bad: fail availability if any provided sample fails verification.
    - min_ok_ratio: require at least this fraction of returned samples to verify.
    - without_replacement: avoid re-sampling the same indices within a head (best-effort).
    """
    target_p_fail: float = 1e-9
    per_block_samples: int = 64
    namespaces: Optional[Sequence[int]] = None
    max_concurrency: int = 4
    require_no_bad: bool = True
    min_ok_ratio: float = 1.0
    without_replacement: bool = True


# ------------------------------ Sampler Protocol ----------------------------

@runtime_checkable
class SamplerLike(Protocol):
    """
    Minimal duck-typed surface expected by the scheduler. Any object with a
    compatible coroutine or sync method will work. The adapter below tries a
    few common names and call signatures.

    Expected to return a JSON-like payload acceptable by
    da.sampling.verifier.verify_samples / light_client.light_verify.
    """
    # Common async form:
    async def sample_batch(  # type: ignore[empty-body]
        self,
        *,
        commitment: bytes,
        population_size: int,
        sample_count: int,
        namespace: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> Mapping[str, Any]:
        ...


class DefaultSamplerAdapter:
    """
    Wrap a sampler instance with various possible method names/signatures into
    a uniform async interface for the scheduler.
    """
    def __init__(self, sampler: Any):
        self._sampler = sampler

    async def request_samples(
        self,
        *,
        commitment: bytes,
        population_size: int,
        sample_count: int,
        namespace: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> Mapping[str, Any]:
        s = self._sampler

        # Try common async method names first
        for name in ("sample_batch", "request_samples", "get_samples"):
            fn = getattr(s, name, None)
            if fn and asyncio.iscoroutinefunction(fn):
                return await fn(
                    commitment=commitment,
                    population_size=int(population_size),
                    sample_count=int(sample_count),
                    namespace=namespace,
                    timeout_sec=timeout_sec,
                )

        # Try sync methods; run in default executor
        loop = asyncio.get_running_loop()
        for name in ("sample_batch", "request_samples", "get_samples"):
            fn = getattr(s, name, None)
            if callable(fn):
                return await loop.run_in_executor(
                    None,
                    lambda: fn(
                        commitment=commitment,
                        population_size=int(population_size),
                        sample_count=int(sample_count),
                        namespace=namespace,
                        timeout_sec=timeout_sec,
                    ),
                )

        raise RuntimeError("Sampler object does not expose a compatible sampling method")


# ------------------------------ Scheduler -----------------------------------

ReportHandler = Callable[[Any, HeadInfo], Union[None, Awaitable[None]]]
HeadProvider = Callable[[], Awaitable[HeadInfo]]


class SamplingScheduler:
    """
    Periodically samples DA availability for the current head, verifies, and
    emits a report via the provided callback.

    Lifecycle:
      - run_forever() periodically polls get_head_fn
      - on each new height, schedule up to policy.max_concurrency sample tasks
      - aggregate light-verify results and pass to on_report
    """
    def __init__(
        self,
        *,
        get_head_fn: HeadProvider,
        sampler: Any,
        on_report: Optional[ReportHandler] = None,
        policy: Optional[SamplingPolicy] = None,
        log: Optional[Callable[[str], None]] = None,
    ):
        self._get_head = get_head_fn
        self._sampler = DefaultSamplerAdapter(sampler)
        self._on_report = on_report or (lambda report, head: None)
        self._policy = policy or SamplingPolicy()
        self._log = log or (lambda msg: None)

        self._last_height_reported: Optional[int] = None
        self._random = random.Random()

        # Best-effort dedupe of indices per head (index set per namespace)
        self._seen_indices: dict[Tuple[int, Optional[int]], set[int]] = {}

    # -------------------------- public runners --------------------------

    async def run_forever(self, *, poll_interval_sec: float = 5.0, jitter_frac: float = 0.15) -> None:
        """
        Polls for the latest head and runs sampling when a new height is observed.
        Continues indefinitely until cancelled.
        """
        backoff = Backoff(min_sec=poll_interval_sec, max_sec=max(poll_interval_sec * 8, 30.0))
        while True:
            try:
                head = await self._get_head()
            except Exception as e:
                self._log(f"[da.sampling.scheduler] get_head failed: {e!r}")
                await asyncio.sleep(backoff.next())
                continue

            # reset dedupe guard when head changes
            if self._last_height_reported != head.height:
                self._seen_indices.clear()

            try:
                await self._sample_head(head)
                self._last_height_reported = head.height
                backoff.reset()
            except Exception as e:
                self._log(f"[da.sampling.scheduler] sample_head failed at h={head.height}: {e!r}")

            # sleep with jitter
            base = poll_interval_sec
            jitter = base * jitter_frac
            await asyncio.sleep(max(0.2, base + self._random.uniform(-jitter, +jitter)))

    # -------------------------- core logic -----------------------------

    async def _sample_head(self, head: HeadInfo) -> None:
        """
        Schedule sampling tasks for this head based on policy, await them, then
        aggregate+verify and emit report.
        """
        names = list(self._policy.namespaces) if self._policy.namespaces else [None]
        per_ns_budget = max(1, self._policy.per_block_samples // max(1, len(names)))

        tasks: List[asyncio.Task] = []
        sem = asyncio.Semaphore(self._policy.max_concurrency)

        async def worker(ns: Optional[int]):
            async with sem:
                payload = await self._request_samples_for_head(head, ns, per_ns_budget)
                return ns, payload

        for ns in names:
            tasks.append(asyncio.create_task(worker(ns)))

        # Gather payloads; ignore failures per-namespace (will be treated as empty)
        ns_payloads: List[Tuple[Optional[int], Mapping[str, Any]]] = []
        for t in tasks:
            try:
                ns, payload = await t
                ns_payloads.append((ns, payload))
            except Exception as e:
                self._log(f"[da.sampling.scheduler] namespace task failed: {e!r}")

        # Verify all batches with the light client
        verify = _lazy("da.sampling.light_client", "light_verify")
        LightVerifyConfig = _lazy("da.sampling.light_client", "LightVerifyConfig")

        report = verify(
            commitment=head.commitment,
            population_size=head.population_size,
            sample_payloads=[p for _, p in ns_payloads if p],
            config=LightVerifyConfig(
                target_p_fail=self._policy.target_p_fail,
                without_replacement=self._policy.without_replacement,
                require_no_bad=self._policy.require_no_bad,
                min_ok_ratio=self._policy.min_ok_ratio,
                require_min_unique=1,
            ),
        )

        await _maybe_await(self._on_report(report, head))

    async def _request_samples_for_head(
        self,
        head: HeadInfo,
        namespace: Optional[int],
        budget: int,
    ) -> Mapping[str, Any]:
        """
        Request a batch of unique indices (best-effort) for a given namespace.
        """
        # Deduplicate indices within this head/namespace if possible
        key = (head.height, namespace)
        seen = self._seen_indices.setdefault(key, set())

        # If the sampler supports generating random indices itself, pass only the budget.
        # Otherwise, provide explicit indices that avoid 'seen'.
        indices: Optional[List[int]] = None

        # Try to ask probability module for a better budget to hit target p_fail
        budget = max(1, int(budget))
        try:
            needed = _lazy("da.sampling.probability", "required_samples_for_target")(
                population_size=head.population_size,
                target_p_fail=self._policy.target_p_fail,
                assumed_corrupt_fraction=None,
                without_replacement=self._policy.without_replacement,
            )
            budget = max(budget, int(needed))
        except Exception:
            pass  # fall back to configured budget

        # If we choose explicit indices, sample without replacement avoiding previously seen ones.
        # Keep it simple and uniform over [0, population_size).
        make_explicit = True
        try:
            # If the sampler exposes "supports_explicit_indices", respect it.
            if hasattr(self._sampler, "_sampler") and getattr(self._sampler._sampler, "supports_explicit_indices", False):
                make_explicit = True
        except Exception:
            pass

        if make_explicit:
            # Guard against pathological budgets
            max_pop = max(1, int(head.population_size))
            take = min(budget, max_pop)
            indices = []
            # avoid infinite loop if seen is huge: cap attempts
            attempts_left = take * 10
            while len(indices) < take and attempts_left > 0:
                idx = self._randbelow(max_pop)
                attempts_left -= 1
                if idx in seen:
                    continue
                seen.add(idx)
                indices.append(idx)

        # Dispatch to sampler
        payload = await self._sampler.request_samples(
            commitment=head.commitment,
            population_size=head.population_size,
            sample_count=budget,
            namespace=namespace,
            timeout_sec=10.0,
        )

        # If payload didn't include indices and we generated them, attach for verifiers that need it.
        if indices and "indices" not in payload:
            payload = {**payload, "indices": indices}

        # Attach namespace hint if missing (useful for codecs)
        if namespace is not None and "namespace" not in payload:
            payload = {**payload, "namespace": namespace}

        return payload

    # -------------------------- helpers --------------------------------

    def _randbelow(self, n: int) -> int:
        return self._random.randrange(n)


# ------------------------------ Backoff -------------------------------------

class Backoff:
    """
    Simple decorrelated jitter backoff (bounded).
    """
    def __init__(self, *, min_sec: float = 1.0, max_sec: float = 30.0):
        self.min = float(min_sec)
        self.max = float(max_sec)
        self.cur = self.min

    def reset(self) -> None:
        self.cur = self.min

    def next(self) -> float:
        # "Decorrelated jitter" (AWS architecture blog): cur = min(max, random(min, cur * 3))
        rnd = random.random()
        self.cur = min(self.max, max(self.min, rnd * (self.cur * 3.0)))
        return self.cur


# ------------------------------ small utils ---------------------------------

def _lazy(module: str, attr: str):
    import importlib
    m = importlib.import_module(module)
    try:
        return getattr(m, attr)
    except AttributeError as e:  # pragma: no cover
        raise RuntimeError(f"Expected attribute '{attr}' in module '{module}'") from e


async def _maybe_await(x: Union[None, Awaitable[None]]) -> None:
    if x is None:
        return
    await x


__all__ = [
    "HeadInfo",
    "SamplingPolicy",
    "SamplerLike",
    "SamplingScheduler",
]
