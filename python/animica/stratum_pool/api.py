from __future__ import annotations

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
