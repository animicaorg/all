#!/usr/bin/env python3
"""AICF provider worker reference runtime.

This worker is intentionally lightweight and dependency-free so that the bundle can
run on Linux, Windows, and Python-only environments.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

VERSION = "0.2.0"
DEFAULT_CONFIG = {
    "api_base_url": "https://aicf.animica.org",
    "provider_id": "provider-demo",
    "provider_token": "replace-with-provider-token",
    "node_id": "node-gpu-01",
    "payout_address": "anm1replacewithwallet",
    "heartbeat_interval_seconds": 8,
    "benchmark_seconds": 10,
    "log_dir": "logs",
    "labels": ["gpu", "provider"],
    "capabilities": {
        "runtime": "llm",
        "model_families": ["aicf-chat-1", "aicf-embed-1"],
        "region": "unset"
    }
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def append_log(log_path: Path, message: str) -> None:
    line = f"[{utc_now()}] {message}\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    print(line.strip())


def detect_gpus() -> List[Dict[str, str]]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return []

    cmd = [
        nvidia_smi,
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader"
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=4)
    except Exception:
        return []

    detected: List[Dict[str, str]] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        parts = [part.strip() for part in raw.split(",")]
        name = parts[0] if len(parts) > 0 else "unknown"
        memory = parts[1] if len(parts) > 1 else "unknown"
        driver = parts[2] if len(parts) > 2 else "unknown"
        detected.append({"name": name, "memory": memory, "driver": driver})
    return detected


def synthetic_cpu_score(seconds: int) -> float:
    started = time.time()
    loops = 0
    digest = hashlib.sha256()
    while time.time() - started < seconds:
        payload = f"aicf-bench-{loops}-{random.random()}".encode("utf-8")
        digest.update(payload)
        loops += 1
    elapsed = max(0.001, time.time() - started)
    return round(loops / elapsed, 2)


def benchmark(config: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    gpu_info = detect_gpus()
    bench_seconds = int(config.get("benchmark_seconds", 10))
    cpu_score = synthetic_cpu_score(bench_seconds)

    gpu_score = 0.0
    for gpu in gpu_info:
        mem_raw = gpu.get("memory", "0 MiB").split()[0]
        try:
            mem_mib = float(mem_raw)
        except ValueError:
            mem_mib = 0.0
        gpu_score += max(1.0, mem_mib / 512.0)

    benchmark_score = round(cpu_score * 0.002 + gpu_score, 2)

    payload = {
        "worker_version": VERSION,
        "generated_at": utc_now(),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpus": os.cpu_count() or 0,
        "gpus": gpu_info,
        "scores": {
            "cpu_hash_iterations_per_sec": cpu_score,
            "gpu_capacity_score": round(gpu_score, 2),
            "benchmark_score": benchmark_score,
        },
        "config_file": str(config_path),
    }

    log_dir = Path(config.get("log_dir", "logs"))
    save_json(log_dir / "benchmark-last.json", payload)
    return payload


def post_json(url: str, payload: Dict[str, Any], token: str) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return {"ok": True}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        return {"ok": False, "http_status": exc.code, "error": body}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def heartbeat_payload(config: Dict[str, Any], benchmark_data: Dict[str, Any]) -> Dict[str, Any]:
    gpu_count = len(benchmark_data.get("gpus", []))
    benchmark_score = benchmark_data.get("scores", {}).get("benchmark_score", 0)
    return {
        "providerId": config.get("provider_id"),
        "nodeId": config.get("node_id"),
        "wallet": config.get("payout_address"),
        "workerVersion": VERSION,
        "timestamp": utc_now(),
        "hardware": {
            "cpus": benchmark_data.get("cpus", os.cpu_count() or 0),
            "gpus": benchmark_data.get("gpus", []),
            "gpuCount": gpu_count,
        },
        "runtime": {
            "benchmarkScore": benchmark_score,
            "labels": config.get("labels", []),
            "capabilities": config.get("capabilities", {}),
            "status": "ready",
        },
    }


def worker_loop(config: Dict[str, Any], config_path: Path) -> None:
    log_path = Path(config.get("log_dir", "logs")) / "provider-worker.log"
    benchmark_file = Path(config.get("log_dir", "logs")) / "benchmark-last.json"

    if benchmark_file.exists():
        with benchmark_file.open("r", encoding="utf-8") as handle:
            benchmark_data = json.load(handle)
    else:
        benchmark_data = benchmark(config, config_path)

    base_url = str(config.get("api_base_url", "")).rstrip("/")
    token = str(config.get("provider_token", ""))
    endpoint = f"{base_url}/provider/worker/heartbeat"
    interval = max(2, int(config.get("heartbeat_interval_seconds", 8)))

    append_log(log_path, f"worker start version={VERSION} endpoint={endpoint} interval={interval}s")

    while True:
        payload = heartbeat_payload(config, benchmark_data)
        result = post_json(endpoint, payload, token)
        if result.get("ok") is False:
            append_log(log_path, f"heartbeat failed: {result}")
        else:
            append_log(log_path, "heartbeat accepted")
        time.sleep(interval)


def command_init_config(args: argparse.Namespace) -> int:
    target = Path(args.config)
    if target.exists() and not args.force:
        print(f"Config already exists: {target}. Use --force to overwrite.")
        return 1
    save_json(target, DEFAULT_CONFIG)
    print(f"Wrote config template to {target}")
    return 0


def command_benchmark(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    payload = benchmark(config, config_path)
    print(json.dumps(payload, indent=2))
    return 0


def command_health(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    status_url = str(config.get("api_base_url", "")).rstrip("/") + "/status"

    request = urllib.request.Request(status_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            raw = response.read().decode("utf-8")
        print(f"health ok: {status_url}")
        print(raw[:4000])
        return 0
    except Exception as exc:
        print(f"health failed: {exc}")
        return 1


def command_start(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    worker_loop(config, config_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aicf-provider-worker", description="AICF provider worker")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init-config", help="write example config")
    init_cmd.add_argument("--config", default="provider.config.json")
    init_cmd.add_argument("--force", action="store_true")
    init_cmd.set_defaults(func=command_init_config)

    bench_cmd = sub.add_parser("benchmark", help="run benchmark and hardware detection")
    bench_cmd.add_argument("--config", default="provider.config.json")
    bench_cmd.set_defaults(func=command_benchmark)

    health_cmd = sub.add_parser("health", help="check API health endpoint")
    health_cmd.add_argument("--config", default="provider.config.json")
    health_cmd.set_defaults(func=command_health)

    start_cmd = sub.add_parser("start", help="start provider worker heartbeat loop")
    start_cmd.add_argument("--config", default="provider.config.json")
    start_cmd.set_defaults(func=command_start)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
