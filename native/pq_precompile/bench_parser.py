#!/usr/bin/env python3
"""Parse bench output lines and compute a suggested gas formula.

Usage: cat bench_output.txt | python bench_parser.py
It reads lines that look like: PQ_BENCH_JSON: { ... }
and performs a simple linear regression on p95_ms vs size to estimate fixed + per-byte costs.
"""
from __future__ import annotations

import json
import math
import sys
from typing import List


def parse_lines(lines: List[str]):
    records = []
    for ln in lines:
        ln = ln.strip()
        if "PQ_BENCH_JSON:" in ln:
            j = ln.split("PQ_BENCH_JSON:", 1)[1].strip()
            try:
                obj = json.loads(j)
                records.append(obj)
            except Exception:
                continue
    return records


def linear_regression(xs, ys):
    n = len(xs)
    if n == 0:
        return (0.0, 0.0)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return (sy / n, 0.0)
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n
    return (a, b)


def suggest_gas(records, ms_to_gas=1000):
    # Use p95_ms vs size
    xs = [r["size"] for r in records]
    ys = [r["p95_ms"] for r in records]
    a_ms, b_ms = linear_regression(xs, ys)
    # map ms to gas units with factor
    a_gas = math.ceil(a_ms * ms_to_gas)
    b_gas = b_ms * ms_to_gas
    # gas formula: GAS = BASE + PER_BYTE * size
    suggestion = {
        "ms_to_gas_factor": ms_to_gas,
        "a_ms": a_ms,
        "b_ms_per_byte": b_ms,
        "GAS_BASE": a_gas,
        "GAS_PER_BYTE": int(math.ceil(b_gas)),
    }
    return suggestion


def main():
    data = sys.stdin.read().splitlines()
    records = parse_lines(data)
    if not records:
        print("No bench JSON records found on stdin (look for PQ_BENCH_JSON lines).")
        sys.exit(2)
    print("Parsed records:")
    print(json.dumps(records, indent=2))
    suggestion = suggest_gas(records, ms_to_gas=1000)
    print("\nSuggested gas formula (first-pass):")
    print(json.dumps(suggestion, indent=2))
    print(
        "\nInterpretation: GAS = GAS_BASE + GAS_PER_BYTE * message_length\nAdjust ms_to_gas factor based on desired gas-per-ms mapping."
    )


if __name__ == "__main__":
    main()
