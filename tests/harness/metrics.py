"""
tests.harness.metrics — scrape Prometheus endpoints, compute deltas & rates
============================================================================

Lightweight Prometheus/OpenMetrics text exposition parser for tests. Provides:

- `scrape_metrics(url)` → fetch a snapshot (timestamped)
- `parse_prometheus_text(text)` → parse into typed families and samples
- `compute_deltas(prev, curr)` → per-family deltas (counters, gauges, histograms)
- `rates_from_counters(prev, curr)` → counter → per-second rate
- `reconstruct_histograms(snapshot)` → rebuild logical histograms from *_bucket/_sum/_count series

This module intentionally avoids extra dependencies. It handles the standard
Prometheus text exposition (and most OpenMetrics-compatible outputs) well
enough for test/integration scenarios.

Notes
-----
- Counters are treated as monotonic; resets (negative deltas) are clamped to 0
  by default. You can change with `allow_reset=True`.
- Histograms are cumulative; deltas are computed on bucket counts, sum, count.
- Gauges are not monotonic; we return a signed difference (curr - prev).
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

try:
    import httpx
except Exception as _e:  # pragma: no cover - tests should declare httpx in deps
    httpx = None  # type: ignore


# --------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------

LabelTuple = Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class SampleKey:
    """Unique series identifier: metric name + normalized (sorted) labels."""
    name: str
    labels: LabelTuple = field(default_factory=tuple)

    def __str__(self) -> str:
        if not self.labels:
            return self.name
        inner = ",".join([f'{k}="{v}"' for k, v in self.labels])
        return f"{self.name}{{{inner}}}"


@dataclass
class MetricsSnapshot:
    ts: float
    raw: str
    # Type per *base* metric family (e.g., "http_requests_total" -> "counter",
    # "request_duration_seconds" -> "histogram"). For series with suffixes
    # (_bucket/_sum/_count/_created), the base name is used as the key.
    types: Dict[str, str] = field(default_factory=dict)
    # All series values keyed by full SampleKey (including suffixes)
    series: Dict[SampleKey, float] = field(default_factory=dict)

    def get(self, name: str, **labels: str) -> Optional[float]:
        """Get a series value by exact name+labels (order-insensitive)."""
        key = SampleKey(name=name, labels=normalize_labels(labels))
        return self.series.get(key)


@dataclass
class HistogramFamily:
    """Reconstructed histogram family (for a single base + label set)."""
    base_name: str
    labels: LabelTuple
    buckets: Dict[float, float]  # le -> cumulative count
    count: float
    sum: float

    def bucket_bounds(self) -> List[float]:
        return sorted(self.buckets.keys())


@dataclass
class HistogramDelta:
    dt: float
    base_name: str
    labels: LabelTuple
    bucket_incr: Dict[float, float]  # le -> increment over dt
    count_incr: float
    sum_incr: float

    def bucket_rates(self) -> Dict[float, float]:
        if self.dt <= 0:
            return {le: math.nan for le in self.bucket_incr}
        return {le: v / self.dt for le, v in self.bucket_incr.items()}

    def count_rate(self) -> float:
        return math.nan if self.dt <= 0 else self.count_incr / self.dt

    def sum_rate(self) -> float:
        return math.nan if self.dt <= 0 else self.sum_incr / self.dt


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------

def scrape_metrics(
    url: str,
    timeout: float = 3.0,
    headers: Optional[Mapping[str, str]] = None,
) -> MetricsSnapshot:
    """
    Fetch a Prometheus text exposition endpoint and return a parsed snapshot.
    """
    if httpx is None:
        raise RuntimeError("httpx is required to scrape metrics (not installed)")

    t0 = time.time()
    resp = httpx.get(url, timeout=timeout, headers=headers)
    resp.raise_for_status()
    raw = resp.text
    types, series = parse_prometheus_text(raw)
    return MetricsSnapshot(ts=t0, raw=raw, types=types, series=series)


def parse_prometheus_text(text: str) -> Tuple[Dict[str, str], Dict[SampleKey, float]]:
    """
    Parse Prometheus/OpenMetrics text exposition into:
      - types: base metric name -> type ('counter'|'gauge'|'histogram'|'summary'|'untyped')
      - series: SampleKey -> float value

    This is a pragmatic parser sufficient for tests. It supports:
      - # TYPE <name> <type>
      - # HELP lines (ignored)
      - sample lines: <metric>{k="v",...} <value> [<timestamp>]
      - numeric values: float, inf, +Inf, -Inf, NaN (case-insensitive)
    """
    types: Dict[str, str] = {}
    series: Dict[SampleKey, float] = {}

    # Basic grammar:
    # name := [a-zA-Z_:][a-zA-Z0-9_:]*
    # label := [^"\\]* or escaped
    re_type = re.compile(r"^#\s*TYPE\s+([a-zA-Z_:][a-zA-Z0-9_:]*)\s+(\w+)\s*$")
    re_sample = re.compile(
        r"""^
        (?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)
        (?:\{(?P<labels>.*)\})?
        \s+
        (?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[+-]?(?:Inf|inf)|NaN|nan)
        (?:\s+\d+)?           # optional timestamp (ignored)
        \s*$
        """,
        re.VERBOSE,
    )

    # Robust label splitter (handles quoted values with escapes)
    def _parse_labels(blob: str) -> LabelTuple:
        if not blob:
            return tuple()
        items: List[Tuple[str, str]] = []
        i = 0
        n = len(blob)
        while i < n:
            # skip whitespace and commas
            while i < n and blob[i] in " \t,":
                i += 1
            if i >= n:
                break
            # parse key
            j = i
            while j < n and re.match(r"[a-zA-Z_][a-zA-Z0-9_]*", blob[j:j+1]):
                j += 1
            key = blob[i:j]
            i = j
            # expect =
            while i < n and blob[i] in " \t":
                i += 1
            if i >= n or blob[i] != "=":
                break
            i += 1
            while i < n and blob[i] in " \t":
                i += 1
            # value: quoted
            if i < n and blob[i] == '"':
                i += 1
                val_chars: List[str] = []
                while i < n:
                    ch = blob[i]
                    i += 1
                    if ch == "\\":
                        if i >= n:
                            break
                        esc = blob[i]
                        i += 1
                        if esc == "n":
                            val_chars.append("\n")
                        elif esc == "t":
                            val_chars.append("\t")
                        elif esc == "r":
                            val_chars.append("\r")
                        else:
                            # \" \\ and any other char literal
                            val_chars.append(esc)
                    elif ch == '"':
                        break
                    else:
                        val_chars.append(ch)
                value = "".join(val_chars)
                items.append((key, value))
                # consume trailing spaces/commas
                while i < n and blob[i] in " \t,":
                    i += 1
            else:
                # Unexpected unquoted literal; try to read until comma
                j = i
                while j < n and blob[j] not in ", ":
                    j += 1
                value = blob[i:j]
                i = j
                items.append((key, value))
        items.sort(key=lambda kv: kv[0])
        return tuple(items)

    def _to_float(s: str) -> float:
        s = s.strip()
        if s.lower() == "nan":
            return math.nan
        if s.lower() in ("inf", "+inf"):
            return math.inf
        if s.lower() == "-inf":
            return -math.inf
        return float(s)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = re_type.match(line)
            if m:
                types[m.group(1)] = m.group(2).lower()
            continue
        m = re_sample.match(line)
        if not m:
            # Skip lines we don't understand (e.g., OpenMetrics # EOF)
            continue
        name = m.group("name")
        labels_blob = m.group("labels") or ""
        value = _to_float(m.group("value"))
        labels = _parse_labels(labels_blob)
        series[SampleKey(name, labels)] = value

        # If TYPE for this exact base wasn't provided, try to infer:
        base = base_name(name)
        if base not in types:
            if name.endswith("_bucket") or name.endswith("_sum") or name.endswith("_count"):
                types[base] = "histogram"
            elif name.endswith("_created"):
                # leave unknown unless seen with bucket/sum/count
                types.setdefault(base, "untyped")
            elif name.endswith("_total"):
                types.setdefault(name, "counter")
            else:
                types.setdefault(name, "untyped")

    # Normalize type keys: prefer base family keys even for simple names
    # (If a simple "http_requests_total" appears, base_name==name)
    normalized_types: Dict[str, str] = {}
    for k, v in types.items():
        normalized_types[base_name(k)] = v
    return normalized_types, series


# --------------------------------------------------------------------------------------
# Delta/rate computations
# --------------------------------------------------------------------------------------

def compute_deltas(
    prev: MetricsSnapshot,
    curr: MetricsSnapshot,
    allow_reset: bool = False,
    name_filter: Optional[re.Pattern[str]] = None,
) -> Dict[str, Mapping[SampleKey, float]]:
    """
    Compute deltas for counters & gauges between two snapshots.

    Returns a dict with keys:
      - "counters": SampleKey -> delta (>=0, unless allow_reset=True)
      - "gauges":   SampleKey -> change (signed)
    Histograms are computed separately via `delta_histograms`.
    """
    out_c: Dict[SampleKey, float] = {}
    out_g: Dict[SampleKey, float] = {}

    for key, curr_v in curr.series.items():
        if name_filter and not name_filter.search(key.name):
            continue
        t = family_type(curr, key.name)
        prev_v = prev.series.get(key)
        if prev_v is None or any(map(math.isnan, (prev_v, curr_v))):
            continue
        if t == "counter" or key.name.endswith("_total"):
            d = curr_v - prev_v
            if d < 0 and not allow_reset:
                d = 0.0
            out_c[key] = d
        elif t == "gauge":
            out_g[key] = curr_v - prev_v
        # histograms/summaries handled elsewhere

    return {"counters": out_c, "gauges": out_g}


def rates_from_counters(
    prev: MetricsSnapshot,
    curr: MetricsSnapshot,
    name_filter: Optional[re.Pattern[str]] = None,
    allow_reset: bool = False,
) -> Dict[SampleKey, float]:
    """
    Compute per-second rates for counter series.
    """
    dt = max(curr.ts - prev.ts, 0.0)
    deltas = compute_deltas(prev, curr, allow_reset=allow_reset, name_filter=name_filter)["counters"]
    if dt <= 0:
        return {k: math.nan for k in deltas}
    return {k: v / dt for k, v in deltas.items()}


def reconstruct_histograms(snapshot: MetricsSnapshot) -> Dict[Tuple[str, LabelTuple], HistogramFamily]:
    """
    Reconstruct logical histograms from *_bucket/_sum/_count series.

    Returns a dict keyed by (base_name, labels_without_le).
    """
    families: Dict[Tuple[str, LabelTuple], HistogramFamily] = {}

    for key, val in snapshot.series.items():
        name = key.name
        base = base_name(name)
        t = snapshot.types.get(base, "untyped")
        if t != "histogram":
            continue

        if name.endswith("_bucket"):
            # Remove 'le' from labels to build the family key; keep others
            le_str = label_value(key.labels, "le")
            if le_str is None:
                # malformed bucket; skip
                continue
            try:
                le = float("inf") if le_str == "+Inf" else float(le_str)
            except ValueError:
                # non-numeric le (shouldn't happen); skip
                continue
            fam_labels = tuple((k, v) for (k, v) in key.labels if k != "le")
            fam_key = (base, fam_labels)
            fam = families.get(fam_key)
            if not fam:
                fam = HistogramFamily(base_name=base, labels=fam_labels, buckets={}, count=math.nan, sum=math.nan)
                families[fam_key] = fam
            fam.buckets[le] = val

        elif name.endswith("_count"):
            fam_labels = key.labels
            fam_key = (base, fam_labels)
            fam = families.get(fam_key)
            if not fam:
                families[fam_key] = HistogramFamily(base_name=base, labels=fam_labels, buckets={}, count=val, sum=math.nan)
            else:
                fam.count = val

        elif name.endswith("_sum"):
            fam_labels = key.labels
            fam_key = (base, fam_labels)
            fam = families.get(fam_key)
            if not fam:
                families[fam_key] = HistogramFamily(base_name=base, labels=fam_labels, buckets={}, count=math.nan, sum=val)
            else:
                fam.sum = val

    return families


def delta_histograms(
    prev: MetricsSnapshot,
    curr: MetricsSnapshot,
    name_filter: Optional[re.Pattern[str]] = None,
    allow_reset: bool = False,
) -> List[HistogramDelta]:
    """
    Compute histogram deltas between two snapshots.

    Returns a list of HistogramDelta (bucket increments, count/sum increments).
    Negative increments (resets) are clamped to 0 unless allow_reset=True.
    """
    dt = max(curr.ts - prev.ts, 0.0)
    prev_f = reconstruct_histograms(prev)
    curr_f = reconstruct_histograms(curr)
    results: List[HistogramDelta] = []

    for fam_key, c in curr_f.items():
        base, labels = fam_key
        if name_filter and not name_filter.search(base):
            continue
        p = prev_f.get(fam_key)
        if not p:
            # No previous; skip until we have baseline
            continue

        # Align all bucket bounds present across prev/curr
        bounds = sorted(set(p.buckets.keys()) | set(c.buckets.keys()))
        bucket_incr: Dict[float, float] = {}
        for le in bounds:
            pv = p.buckets.get(le, 0.0)
            cv = c.buckets.get(le, 0.0)
            d = cv - pv
            if d < 0 and not allow_reset:
                d = 0.0
            bucket_incr[le] = d

        count_incr = _delta_nonneg(p.count, c.count, allow_reset=allow_reset)
        sum_incr = _delta_nonneg(p.sum, c.sum, allow_reset=allow_reset)

        results.append(HistogramDelta(
            dt=dt,
            base_name=base,
            labels=labels,
            bucket_incr=bucket_incr,
            count_incr=count_incr,
            sum_incr=sum_incr,
        ))
    return results


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def normalize_labels(labels: Mapping[str, str] | Iterable[Tuple[str, str]]) -> LabelTuple:
    if isinstance(labels, Mapping):
        items = list(labels.items())
    else:
        items = list(labels)
    items.sort(key=lambda kv: kv[0])
    return tuple((str(k), str(v)) for k, v in items)


def base_name(name: str) -> str:
    """Return the 'family' base name for histograms/summaries; else the name itself."""
    for suff in ("_bucket", "_sum", "_count", "_created"):
        if name.endswith(suff):
            return name[: -len(suff)]
    return name


def family_type(snapshot: MetricsSnapshot, series_name: str) -> str:
    """Lookup family type using base name."""
    return snapshot.types.get(base_name(series_name), "untyped")


def label_value(labels: LabelTuple, key: str) -> Optional[str]:
    for k, v in labels:
        if k == key:
            return v
    return None


def _delta_nonneg(prev: float, curr: float, allow_reset: bool = False) -> float:
    if any(map(math.isnan, (prev, curr))):
        return math.nan
    d = curr - prev
    if d < 0 and not allow_reset:
        d = 0.0
    return d


# --------------------------------------------------------------------------------------
# Convenience: scrape-and-compare
# --------------------------------------------------------------------------------------

def scrape_and_compare(
    url: str,
    prev: Optional[MetricsSnapshot],
    timeout: float = 3.0,
    headers: Optional[Mapping[str, str]] = None,
    counter_filter: Optional[re.Pattern[str]] = None,
    histogram_filter: Optional[re.Pattern[str]] = None,
    allow_reset: bool = False,
) -> Tuple[MetricsSnapshot, Dict[str, Mapping[SampleKey, float]], List[HistogramDelta]]:
    """
    One-shot helper: scrape `url`, compute deltas vs `prev`.
    Returns: (curr_snapshot, deltas_map, histogram_deltas)
    """
    curr = scrape_metrics(url, timeout=timeout, headers=headers)
    deltas = compute_deltas(prev, curr, allow_reset=allow_reset, name_filter=counter_filter) if prev else {"counters": {}, "gauges": {}}
    h_deltas = delta_histograms(prev, curr, name_filter=histogram_filter, allow_reset=allow_reset) if prev else []
    return curr, deltas, h_deltas


# --------------------------------------------------------------------------------------
# Pretty-printing (useful for debugging in failing tests)
# --------------------------------------------------------------------------------------

def format_rates(rates: Mapping[SampleKey, float], precision: int = 3) -> str:
    lines = []
    for k, v in sorted(rates.items(), key=lambda kv: str(kv[0])):
        lines.append(f"{k}: {v:.{precision}f}/s")
    return "\n".join(lines)


def format_histogram_delta(h: HistogramDelta, precision: int = 3) -> str:
    parts = [f"{h.base_name}{_labels_str(h.labels)} dt={h.dt:.3f}s"]
    parts.append(f"count+={h.count_incr:.{precision}f} ({h.count_rate():.{precision}f}/s)")
    parts.append(f"sum+={h.sum_incr:.{precision}f} ({h.sum_rate():.{precision}f}/s)")
    for le in sorted(h.bucket_incr.keys()):
        inc = h.bucket_incr[le]
        rate = h.bucket_rates().get(le, math.nan)
        bound = "+Inf" if math.isinf(le) else f"{le:g}"
        parts.append(f"  le={bound}: +{inc:.{precision}f} ({rate:.{precision}f}/s)")
    return "\n".join(parts)


def _labels_str(labels: LabelTuple) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + inner + "}"


# --------------------------------------------------------------------------------------
# Minimal self-test (manual) — invoked only if run directly
# --------------------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    demo = """
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="get",code="200"} 10
http_requests_total{method="get",code="500"} 2
# HELP request_duration_seconds A histogram of request latencies.
# TYPE request_duration_seconds histogram
request_duration_seconds_bucket{le="0.1",route="/"} 3
request_duration_seconds_bucket{le="0.2",route="/"} 5
request_duration_seconds_bucket{le="0.5",route="/"} 6
request_duration_seconds_bucket{le="+Inf",route="/"} 6
request_duration_seconds_sum{route="/"} 0.73
request_duration_seconds_count{route="/"} 6
"""
    t1, s1 = parse_prometheus_text(demo)
    snap1 = MetricsSnapshot(ts=time.time(), raw=demo, types=t1, series=s1)
    time.sleep(0.2)
    demo2 = demo.replace(' 10', ' 13').replace(' 2', ' 2', 1).replace(' 6\n', ' 7\n')
    t2, s2 = parse_prometheus_text(demo2)
    snap2 = MetricsSnapshot(ts=time.time(), raw=demo2, types=t2, series=s2)

    deltas = compute_deltas(snap1, snap2)
    print("Counter deltas/rates:")
    rates = rates_from_counters(snap1, snap2)
    print(format_rates(rates))

    print("\nHistogram delta:")
    for d in delta_histograms(snap1, snap2):
        print(format_histogram_delta(d))
