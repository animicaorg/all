#!/usr/bin/env python3
"""
chains/scripts/check_endpoints.py — probe RPC/Explorer endpoints and write a report.

What it does
------------
• Reads chain JSONs (from --chains args or registry.json)
• For each chain:
    - HTTP RPC endpoints (rpc.http[]): send HTTP HEAD (fallback to GET) and record status/latency
    - WS RPC endpoints   (rpc.ws[]):   open a WebSocket, send a ping, await pong, record latency
    - Explorer URLs      (explorers[]): HTTP HEAD/GET check
• Writes a JSON report (default: chains/reports/endpoints_report.json)
• Prints a human summary to stdout and returns non-zero if any checks fail (unless --no-fail)

Dependencies
------------
• Standard library only for HTTP checks.
• Optional: 'websockets' package for WS checks. Install with:
      pip install websockets
  If unavailable, WS checks will be skipped with a warning.

Usage
-----
python chains/scripts/check_endpoints.py
python chains/scripts/check_endpoints.py --chains chains/animica.testnet.json chains/animica.localnet.json
python chains/scripts/check_endpoints.py --timeout 5 --retries 1 --out chains/reports/report.json
python chains/scripts/check_endpoints.py --no-fail
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "chains" / "registry.json"
DEFAULT_OUT = ROOT / "chains" / "reports" / "endpoints_report.json"

# Optional websocket support
try:
    import websockets  # type: ignore
    from websockets.exceptions import WebSocketException  # type: ignore

    _HAS_WS = True
except Exception:
    websockets = None  # type: ignore
    WebSocketException = Exception  # type: ignore
    _HAS_WS = False


def read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def discover_from_registry(registry_path: Path) -> List[Path]:
    chains: List[Path] = []
    if not registry_path.exists():
        return chains
    reg = read_json(registry_path)
    for e in reg.get("entries", []):
        path = ROOT / e.get("path", "")
        if path.exists():
            chains.append(path)
    return chains


def http_probe(
    url: str, timeout: float, retries: int
) -> Tuple[bool, Optional[int], float, Optional[str]]:
    """
    Try HTTP HEAD, fall back to GET if method not allowed.
    Returns: (ok, status_code, latency_ms, error)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return (False, None, 0.0, f"unsupported scheme: {parsed.scheme}")

    last_err: Optional[str] = None
    status: Optional[int] = None
    t0 = time.perf_counter()

    for attempt in range(retries + 1):
        try:
            # HEAD first
            req = Request(
                url, method="HEAD", headers={"User-Agent": "animica-endpoint-check/1.0"}
            )
            ctx = None
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
            with urlopen(req, timeout=timeout, context=ctx) as resp:  # type: ignore[arg-type]
                status = getattr(resp, "status", 200)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                return (200 <= status < 400, status, latency_ms, None)
        except Exception as e1:
            last_err = f"HEAD: {e1!s}"
            # Try GET just for liveness
            try:
                req = Request(
                    url,
                    method="GET",
                    headers={"User-Agent": "animica-endpoint-check/1.0"},
                )
                ctx = None
                if parsed.scheme == "https":
                    ctx = ssl.create_default_context()
                with urlopen(req, timeout=timeout, context=ctx) as resp:  # type: ignore[arg-type]
                    status = getattr(resp, "status", 200)
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    return (200 <= status < 400, status, latency_ms, None)
            except Exception as e2:
                last_err = f"GET: {e2!s}"
                if attempt < retries:
                    continue
                latency_ms = (time.perf_counter() - t0) * 1000.0
                return (False, status, latency_ms, last_err)

    latency_ms = (time.perf_counter() - t0) * 1000.0
    return (False, status, latency_ms, last_err)


async def ws_probe_one(url: str, timeout: float) -> Tuple[bool, float, Optional[str]]:
    """
    Connect to a ws(s) URL, send ping, await pong.
    Returns: (ok, latency_ms, error)
    """
    if not _HAS_WS:
        return (False, 0.0, "websockets package not installed")
    parsed = urlparse(url)
    if parsed.scheme not in ("ws", "wss"):
        return (False, 0.0, f"unsupported scheme: {parsed.scheme}")

    t0 = time.perf_counter()
    try:
        # Suppress certificate verification override — use defaults for security.
        async with websockets.connect(url, open_timeout=timeout, close_timeout=timeout) as ws:  # type: ignore[attr-defined]
            await ws.ping()
            # Many servers auto-pong; give them the same timeout window
            await asyncio.wait_for(asyncio.sleep(0), timeout=timeout)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return (True, latency_ms, None)
    except (asyncio.TimeoutError, WebSocketException, Exception) as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return (False, latency_ms, str(e))


async def ws_probe(urls: List[str], timeout: float) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for u in urls:
        ok, latency_ms, err = await ws_probe_one(u, timeout)
        results.append(
            {
                "url": u,
                "ok": ok,
                "latency_ms": round(latency_ms, 2),
                "error": None if ok else err,
            }
        )
    return results


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Probe RPC/Explorer endpoints for Animica chains."
    )
    ap.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY), help="Path to registry.json"
    )
    ap.add_argument(
        "--chains",
        nargs="*",
        default=[],
        help="Explicit chain JSON paths (overrides registry)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-request timeout seconds (default 5.0)",
    )
    ap.add_argument("--retries", type=int, default=0, help="HTTP retries (default 0)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output report JSON path")
    ap.add_argument(
        "--no-fail", action="store_true", help="Always exit 0 even if some checks fail"
    )
    args = ap.parse_args(argv)

    # Collect chain files
    chain_paths: List[Path]
    if args.chains:
        chain_paths = [Path(p) for p in args.chains]
    else:
        chain_paths = discover_from_registry(Path(args.registry))

    if not chain_paths:
        print("[warn] no chain files provided or found; nothing to check")
        return 0

    # Prepare report
    report: Dict[str, Any] = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "timeoutSec": args.timeout,
        "retries": args.retries,
        "websocketSupport": _HAS_WS,
        "chains": [],
    }

    any_fail = False

    for cp in chain_paths:
        try:
            chain = read_json(cp)
        except Exception as e:
            any_fail = True
            report["chains"].append(
                {
                    "file": str(cp),
                    "error": f"invalid JSON: {e}",
                }
            )
            continue

        name = chain.get("name", cp.stem)
        rpc_http: List[str] = chain.get("rpc", {}).get("http", []) or []
        rpc_ws: List[str] = chain.get("rpc", {}).get("ws", []) or []
        explorers: List[Dict[str, str]] = chain.get("explorers", []) or []

        print(f"[info] checking {name} ({cp})")
        http_results: List[Dict[str, Any]] = []
        for u in rpc_http:
            ok, status, latency_ms, err = http_probe(u, args.timeout, args.retries)
            http_results.append(
                {
                    "url": u,
                    "ok": ok,
                    "status": status,
                    "latency_ms": round(latency_ms, 2),
                    "error": None if ok else err,
                }
            )
            if not ok:
                any_fail = True

        exp_results: List[Dict[str, Any]] = []
        for e in explorers:
            url = e.get("url")
            if not url:
                continue
            ok, status, latency_ms, err = http_probe(url, args.timeout, args.retries)
            exp_results.append(
                {
                    "name": e.get("name", ""),
                    "url": url,
                    "ok": ok,
                    "status": status,
                    "latency_ms": round(latency_ms, 2),
                    "error": None if ok else err,
                }
            )
            if not ok:
                any_fail = True

        ws_results: List[Dict[str, Any]] = []
        if rpc_ws:
            if _HAS_WS:
                ws_results = asyncio.run(ws_probe(rpc_ws, args.timeout))
                if any(not r["ok"] for r in ws_results):
                    any_fail = True
            else:
                any_fail = True
                ws_results = [
                    {
                        "url": u,
                        "ok": False,
                        "latency_ms": 0.0,
                        "error": "websockets package not installed",
                    }
                    for u in rpc_ws
                ]

        report["chains"].append(
            {
                "file": str(cp),
                "name": name,
                "rpc": {
                    "http": http_results,
                    "ws": ws_results,
                },
                "explorers": exp_results,
            }
        )

    # Ensure dest dir and write
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[done] wrote {out_path}")

    # Human summary
    def _summary_row(ok: bool) -> str:
        return "OK  " if ok else "FAIL"

    for ch in report["chains"]:
        print(f"\n== {ch.get('name','(unknown)')} ==")
        for r in ch["rpc"]["http"]:
            print(
                f"{_summary_row(r['ok'])} HTTP {r['status'] or '-':>3} {r['latency_ms']:>7.2f} ms  {r['url']}{'' if r['ok'] else f'  # {r['error']}'}"
            )
        for r in ch["rpc"]["ws"]:
            print(
                f"{_summary_row(r['ok'])}  WS  {'-':>3} {r['latency_ms']:>7.2f} ms  {r['url']}{'' if r['ok'] else f'  # {r['error']}'}"
            )
        for r in ch["explorers"]:
            print(
                f"{_summary_row(r['ok'])} EXPL {r['status'] or '-':>3} {r['latency_ms']:>7.2f} ms  {r['url']}"
            )

    if any_fail and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
