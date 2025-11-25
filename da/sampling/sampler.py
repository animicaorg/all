"""
Animica • DA • Data Availability Sampler

A pragmatic DAS client that:
  - Builds a random sampling plan (uniform by default)
  - Batches requests to a DA Retrieval API
  - Retries with exponential backoff on transient failures
  - Verifies returned proofs against the commitment (when a verifier is available)
  - Produces a compact report including failed indices and an estimated p_fail

Transport is pluggable. If you pass a custom `fetch_proof` callable, the
sampler will use it. Otherwise it will try an HTTP client against a base_url.

HTTP API (expected; matches retrieval layer in da/retrieval/api.py):
  GET  /da/proof?commitment=0x...&indices=12,55,901
  or
  POST /da/proof   {"commitment":"0x...","indices":[12,55,901]}

Return value should be a JSON-compatible object containing enough material for
verifier to check samples (branches, roots, indices). Exact keys are delegated
to `da.sampling.verifier.verify_samples`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Any
import time
import math
import json
import binascii
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Optional HTTP deps
try:  # pragma: no cover - exercised in integration tests
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None

# Lazy imports for planning / verification / probability
def _lazy(module: str, attr: str) -> Any:  # pragma: no cover - trivial
    import importlib
    return getattr(importlib.import_module(module), attr)

# ----------------------------- Config & Types ------------------------------

HexStr = str
FetchProofFn = Callable[[HexStr, Sequence[int]], Mapping[str, Any]]
VerifyFn = Callable[[bytes, Mapping[str, Any]], Any]  # shape normalized later


@dataclass(frozen=True)
class SamplerConfig:
    base_url: Optional[str] = None
    timeout_s: float = 5.0
    max_retries: int = 3
    backoff_initial_s: float = 0.25
    backoff_factor: float = 2.0
    batch_size: int = 64               # indices per proof request
    max_in_flight: int = 4             # concurrency
    user_agent: str = "animica-da-sampler/1.0"
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass
class SamplerStats:
    requests: int = 0
    retries: int = 0
    bytes_rx: int = 0
    elapsed_s: float = 0.0


@dataclass
class SamplerResult:
    commitment: bytes
    selected_indices: List[int]
    ok_indices: List[int]
    bad_indices: List[int]
    errors: Dict[int, str]                      # index -> error string (fetch/verify)
    stats: SamplerStats
    p_fail_estimate: Optional[float] = None     # upper bound estimate given sample_count

    def ok_ratio(self) -> float:
        if not self.selected_indices:
            return 0.0
        return len(self.ok_indices) / float(len(self.selected_indices))


# ----------------------------- HTTP Transport ------------------------------

class _HttpDAClient:
    """
    Minimal HTTP client for DA proofs. Supports GET-with-query and POST-with-JSON.
    """

    def __init__(self, cfg: SamplerConfig) -> None:
        if cfg.base_url is None:
            raise ValueError("base_url is required for HTTP transport")
        self.base = cfg.base_url.rstrip("/")
        self.timeout = cfg.timeout_s
        self.headers = {"User-Agent": cfg.user_agent, "Accept": "application/json"}
        self.headers.update(cfg.headers or {})
        self._session = None
        if requests is not None:  # pragma: no cover
            self._session = requests.Session()

    def _build_url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _do(self, method: str, path: str, *, params=None, json_body=None) -> Tuple[int, bytes]:
        if requests is None:
            # urllib fallback (stdlib only)
            import urllib.request
            import urllib.parse

            url = self._build_url(path)
            if params:
                q = urllib.parse.urlencode(params, doseq=True)
                url = f"{url}?{q}"
            req = urllib.request.Request(url, method=method)
            for k, v in self.headers.items():
                req.add_header(k, v)
            if json_body is not None:
                body = json.dumps(json_body).encode("utf-8")
                req.add_header("Content-Type", "application/json")
            else:
                body = None
            with urllib.request.urlopen(req, data=body, timeout=self.timeout) as resp:
                return resp.getcode(), resp.read()

        # requests path (preferred)
        sess = self._session or requests
        if method == "GET":
            r = sess.get(self._build_url(path), params=params, headers=self.headers, timeout=self.timeout)
        elif method == "POST":
            r = sess.post(self._build_url(path), json=json_body, headers=self.headers, timeout=self.timeout)
        else:
            raise ValueError("unsupported method")
        return int(r.status_code), bytes(r.content)

    # --- public ---

    def fetch_proof(self, commitment_hex: HexStr, indices: Sequence[int]) -> Mapping[str, Any]:
        """
        Try GET first, then POST. Returns parsed JSON (raises on HTTP errors).
        """
        # GET
        params = {"commitment": commitment_hex, "indices": ",".join(str(i) for i in indices)}
        code, data = self._do("GET", "/da/proof", params=params, json_body=None)
        if code == 200:
            try:
                return json.loads(data.decode("utf-8"))
            except Exception as e:
                raise RuntimeError(f"invalid JSON in GET /da/proof response: {e}") from e

        # Fallback to POST
        body = {"commitment": commitment_hex, "indices": list(map(int, indices))}
        code, data = self._do("POST", "/da/proof", params=None, json_body=body)
        if code != 200:
            # Try to extract error string if present
            msg = data.decode("utf-8", errors="ignore")
            raise RuntimeError(f"DA proof fetch failed (HTTP {code}): {msg}")
        try:
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"invalid JSON in POST /da/proof response: {e}") from e


# ------------------------------ Sampler ------------------------------------

def _to_hex(b: bytes) -> str:
    return "0x" + binascii.hexlify(b).decode("ascii")


def _chunks(seq: Sequence[int], n: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class DataAvailabilitySampler:
    """
    High-level sampler that coordinates planning → fetch → verify.

    Parameters
    ----------
    config : SamplerConfig
        Configuration for batching, retries, timeouts, and base URL.
    fetch_proof : Optional[FetchProofFn]
        Custom proof fetcher. If None, uses HTTP client built from `config.base_url`.
    verify_fn : Optional[VerifyFn]
        Optional verifier override. If None, uses `da.sampling.verifier.verify_samples`.

    Notes
    -----
    - `verify_fn(commitment_bytes, proof_obj)` may return:
        * (ok_indices: List[int], bad_indices: List[int])
        * {"ok_indices":[...], "bad_indices":[...]}
        * True/False (then all indices in the batch are considered ok/bad)
    """

    def __init__(
        self,
        config: SamplerConfig,
        fetch_proof: Optional[FetchProofFn] = None,
        verify_fn: Optional[VerifyFn] = None,
    ) -> None:
        self.cfg = config
        if fetch_proof is None:
            self._client = _HttpDAClient(config)
            self._fetch: FetchProofFn = self._client.fetch_proof
        else:
            self._client = None
            self._fetch = fetch_proof
        if verify_fn is None:
            # Lazy resolve default verifier
            def _default_verify(commitment: bytes, proof: Mapping[str, Any]) -> Any:
                vf = _lazy("da.sampling.verifier", "verify_samples")
                return vf(commitment, proof)
            self._verify = _default_verify
        else:
            self._verify = verify_fn
        self._lock = threading.Lock()

    # ------------------------- public API -------------------------

    def run(
        self,
        *,
        commitment: bytes,
        population_size: int,
        sample_count: int,
        seed: Optional[int] = None,
        indices: Optional[Sequence[int]] = None,
    ) -> SamplerResult:
        """
        Execute a sampling round for `commitment`.

        If `indices` is not provided, a uniform plan of `sample_count` indices is generated.
        """
        t0 = time.time()
        sel = list(map(int, indices)) if indices is not None else self._plan(population_size, sample_count, seed)
        stats = SamplerStats()

        ok: List[int] = []
        bad: List[int] = []
        errors: Dict[int, str] = {}

        commitment_hex = _to_hex(commitment)

        # Fetch+verify in batches with limited concurrency
        batches = list(_chunks(sel, max(1, int(self.cfg.batch_size))))
        with ThreadPoolExecutor(max_workers=max(1, int(self.cfg.max_in_flight))) as tp:
            futs = [tp.submit(self._fetch_and_verify_batch, commitment, commitment_hex, batch, stats) for batch in batches]
            for fut in as_completed(futs):
                b_ok, b_bad, b_errs = fut.result()
                ok.extend(b_ok)
                bad.extend(b_bad)
                errors.update(b_errs)

        stats.elapsed_s = max(0.0, time.time() - t0)

        # Estimate p_fail upper bound with simple (1 - f)^{n} style bound:
        p_fail_est = self._estimate_p_fail(population_size=population_size, total_samples=len(sel), bad=len(bad))

        return SamplerResult(
            commitment=commitment,
            selected_indices=sel,
            ok_indices=sorted(ok),
            bad_indices=sorted(bad),
            errors=errors,
            stats=stats,
            p_fail_estimate=p_fail_est,
        )

    # ----------------------- internals -----------------------

    def _plan(self, population_size: int, sample_count: int, seed: Optional[int]) -> List[int]:
        plan_uniform = _lazy("da.sampling.queries", "plan_uniform")
        return list(map(int, plan_uniform(population_size=population_size, sample_count=sample_count, seed=seed)))

    def _fetch_and_verify_batch(
        self,
        commitment: bytes,
        commitment_hex: HexStr,
        indices: Sequence[int],
        stats: SamplerStats,
    ) -> Tuple[List[int], List[int], Dict[int, str]]:
        """
        Fetch one batch with retries, then verify. Returns (ok, bad, errs).
        """
        payload: Optional[Mapping[str, Any]] = None
        last_err: Optional[BaseException] = None
        attempt = 0
        while attempt <= int(self.cfg.max_retries):
            try:
                with self._lock:
                    stats.requests += 1
                payload = self._fetch(commitment_hex, indices)
                # Stats: rough size
                b = _maybe_len_bytes(payload)
                if b is not None:
                    with self._lock:
                        stats.bytes_rx += b
                last_err = None
                break
            except Exception as e:
                last_err = e
                if attempt >= int(self.cfg.max_retries):
                    break
                # backoff
                sleep = self.cfg.backoff_initial_s * (self.cfg.backoff_factor ** attempt)
                time.sleep(sleep)
                with self._lock:
                    stats.retries += 1
                attempt += 1

        if payload is None:
            # mark all indices in this batch as failed fetch
            msg = str(last_err) if last_err else "unknown fetch error"
            return [], list(map(int, indices)), {int(i): f"fetch: {msg}" for i in indices}

        # Verify; normalize result shape
        try:
            vr = self._verify(commitment, payload)
            ok_idx, bad_idx = _normalize_verify_result(vr, indices)
            return list(ok_idx), list(bad_idx), {}
        except Exception as e:
            # If the verifier explodes, count the whole batch as bad with shared error
            msg = f"verify: {e}"
            return [], list(map(int, indices)), {int(i): msg for i in indices}

    def _estimate_p_fail(self, *, population_size: int, total_samples: int, bad: int) -> Optional[float]:
        """
        A conservative bound: if no bad samples observed, use standard (1 - f)^n bound
        rearranged to solve for p_fail under worst-case fraction of corruption.

        If some bad samples are observed, return 1.0 (unavailable).
        """
        if total_samples <= 0:
            return None
        if bad > 0:
            return 1.0
        try:
            # Use package math if available for a better bound
            pf = _lazy("da.sampling.probability", "estimate_p_fail_upper")
            return float(pf(population_size=population_size, sample_count=total_samples))
        except Exception:
            # Simple heuristic: assume >=1 share corrupt among population; prob miss after n samples
            # p_fail <= ((population_size - 1) / population_size) ** total_samples
            if population_size <= 1:
                return 0.0
            base = (population_size - 1) / float(population_size)
            return float(base ** total_samples)


# ------------------------- helpers -------------------------

def _normalize_verify_result(v: Any, batch_indices: Sequence[int]) -> Tuple[List[int], List[int]]:
    """
    Accepts multiple shapes from verify function and returns (ok_indices, bad_indices).
    """
    if isinstance(v, tuple) and len(v) == 2:
        a, b = v
        return list(map(int, a)), list(map(int, b))
    if isinstance(v, Mapping):
        ok = v.get("ok_indices") or v.get("ok") or []
        bad = v.get("bad_indices") or v.get("bad") or []
        return list(map(int, ok)), list(map(int, bad))
    if isinstance(v, bool):
        if v:
            return list(map(int, batch_indices)), []
        else:
            return [], list(map(int, batch_indices))
    # Fallback: treat unexpected truthy as all-ok
    return list(map(int, batch_indices)), []


def _maybe_len_bytes(obj: Any) -> Optional[int]:
    try:
        # coarse estimate via JSON dump length
        s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        return len(s.encode("utf-8"))
    except Exception:
        return None


__all__ = [
    "SamplerConfig",
    "SamplerStats",
    "SamplerResult",
    "DataAvailabilitySampler",
]
