from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .package_builder import MinerBundleBuilder
from .portal import MiningPortalService, build_bundle_input
from .metrics import PoolMetrics


def create_app(metrics: PoolMetrics) -> FastAPI:
    app = FastAPI(title="Animica Stratum Pool API", version="0.1.0")
    portal = MiningPortalService(metrics.config, metrics)
    bundle_builder = MinerBundleBuilder()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/summary")
    @app.get("/api/pool/summary")
    async def pool_summary():
        return metrics.pool_summary()

    @app.get("/miners")
    @app.get("/api/miners")
    async def list_miners(page: int = 1, page_size: int = 50):
        data = metrics.miners()
        start = max(page - 1, 0) * page_size
        end = start + page_size
        items = data["items"][start:end]
        return {"items": items, "total": data["total"]}

    @app.get("/miners/{worker_id}")
    @app.get("/api/miners/{worker_id}")
    async def miner_detail(worker_id: str):
        data = metrics.miner_detail(worker_id)
        if not data:
            raise HTTPException(status_code=404, detail="worker not found")
        return data

    @app.get("/blocks")
    @app.get("/api/blocks/recent")
    async def recent_blocks():
        return metrics.recent_blocks()

    @app.get("/api/pool/accounting")
    async def pool_accounting():
        return metrics.accounting_summary()

    @app.get("/api/pool/accounting/ledger")
    async def pool_accounting_ledger(limit: int = Query(100, ge=1, le=500)):
        return metrics.accounting_ledger(limit=limit)

    @app.get("/healthz")
    async def health():
        return metrics.health()

    # --- pool-web (pool.animica.org) compatibility surface ----------------
    # The static site at /pool-web fetches /v1/pool/status and /v1/pool/stats
    # against this API. Keep these endpoints stable and CORS-friendly.

    def _iso_to_epoch(ts_value: Any) -> int:
        if not ts_value:
            return 0
        if isinstance(ts_value, (int, float)):
            return int(ts_value)
        if isinstance(ts_value, str):
            normalized = ts_value.replace("Z", "+00:00")
            try:
                return int(datetime.fromisoformat(normalized).timestamp())
            except ValueError:
                return 0
        return 0

    @app.get("/v1/pool/status")
    async def pool_status_v1(request: Request):
        resolved = portal.resolve(request)
        summary = metrics.pool_summary()
        health_payload = metrics.health()
        payout = metrics.payout_status()
        return {
            "host": resolved.public_host,
            "port": int(resolved.public_port),
            "stratum_url": resolved.stratum_url,
            "connected_miners": int(summary.get("num_miners") or 0),
            "network": resolved.network or str(summary.get("network") or ""),
            "synced": bool(
                resolved.pool_enabled
                and str(health_payload.get("status") or "").lower() == "ok"
            ),
            # Payout schedule — surfaced so the website can show a
            # countdown to the next sweep. countdown_seconds is the live
            # server-clock remaining; next_payout_at is ISO for clients
            # that prefer to compute their own offset against local time.
            "payouts_enabled": bool(payout.get("payouts_enabled")),
            "payout_interval_seconds": payout.get("payout_interval_seconds"),
            "payout_countdown_seconds": payout.get("payout_countdown_seconds"),
            "next_payout_at": payout.get("next_payout_at"),
            "last_payout_at": payout.get("last_payout_at"),
        }

    @app.get("/v1/pool/stats")
    async def pool_stats_v1(limit: int = Query(8, ge=1, le=50)):
        summary = metrics.pool_summary()
        blocks_payload = metrics.recent_blocks()
        recent: list[dict[str, Any]] = []
        for blk in (blocks_payload.get("items") or [])[:limit]:
            recent.append(
                {
                    "height": blk.get("height"),
                    "ts": _iso_to_epoch(blk.get("timestamp")),
                    "miner": blk.get("worker") or blk.get("address") or "",
                    "reward": blk.get("reward"),
                    "tx_count": blk.get("tx_count"),
                }
            )
        return {
            "hashrate": float(summary.get("pool_hashrate") or 0.0),
            "hashrate_1m": float(summary.get("hashrate_1m") or 0.0),
            "hashrate_15m": float(summary.get("hashrate_15m") or 0.0),
            "hashrate_1h": float(summary.get("hashrate_1h") or 0.0),
            "miners": int(summary.get("num_miners") or 0),
            "blocks_found_total": int(summary.get("blocks_found_total") or 0),
            "recent_blocks": recent,
            "last_update": summary.get("last_update"),
        }

    @app.get("/api/mining/config", name="mining_config")
    async def mining_config(request: Request):
        return portal.config_payload(request)

    @app.get("/api/mining/status", name="mining_status")
    async def mining_status(request: Request):
        return portal.status_payload(request)

    @app.get("/api/mining/generate", name="mining_generate")
    async def mining_generate(
        request: Request,
        address: str = "",
        worker: str = "",
        threads: int = Query(0, ge=0, le=256),
    ):
        return portal.generated_payload(
            request,
            address=address or None,
            worker=worker or None,
            threads=threads or None,
        )

    @app.get("/api/mining/downloads", name="mining_downloads_manifest")
    async def mining_downloads_manifest(request: Request):
        resolved = portal.resolve(request)
        entries = []
        for platform, label in (
            ("windows", "Windows"),
            ("macos", "macOS"),
            ("linux", "Ubuntu / Linux"),
        ):
            artifact = bundle_builder.build(resolved, platform, build_bundle_input())
            entries.append(
                {
                    "platform": platform,
                    "label": label,
                    "filename": artifact.filename,
                    "version": artifact.version,
                    "launcher": artifact.launcher,
                    "entrypoint": artifact.entrypoint,
                    "includes_executable": artifact.includes_executable,
                    "requires_python": artifact.requires_python,
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes,
                    "url": str(request.url_for("download_miner_bundle", platform=platform)),
                    "notes": (
                        "Starter bundle with launcher + config. "
                        + (
                            "Includes a standalone miner executable."
                            if artifact.includes_executable
                            else "Falls back to Python script miner when executable is unavailable."
                        )
                    ),
                }
            )
        return {
            "network": resolved.network,
            "endpoint": resolved.stratum_url,
            "items": entries,
        }

    @app.get("/api/mining/downloads/{platform}", name="download_miner_bundle")
    async def download_miner_bundle(
        request: Request,
        platform: str,
        address: str = "",
        worker: str = "",
        threads: int = Query(0, ge=0, le=256),
    ):
        resolved = portal.resolve(request)
        bundle = build_bundle_input(
            address=address or None,
            worker=worker or None,
            threads=threads or None,
        )
        try:
            artifact = bundle_builder.build(resolved, platform, bundle)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=artifact.path.read_bytes(),
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            },
        )

    return app
