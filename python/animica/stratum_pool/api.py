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

    # --- Monero (XMR) dual-mining stats -----------------------------------
    # These endpoints expose the parallel RandomX pool the dual-miner uses.
    # When XMR mining is disabled (ANIMICA_POOL_XMR_ENABLED!=1) they return
    # zeros / empty so the portal UI can show "0 active miners".

    @app.get("/api/pool/xmr/summary")
    async def xmr_summary():
        ref = getattr(metrics, "xmr_handles", lambda: None)()
        if not ref:
            return {
                "enabled": False,
                "monerod_height": 0,
                "monerod_target": 0,
                "monerod_synced": False,
                "active_miners": 0,
                "blocks_found": 0,
                "current_seed_hash": None,
                "current_height": 0,
                "pool_fee_address": None,
                "pool_fee_bps": 500,
            }
        ledger = ref.get("ledger")
        jm = ref.get("job_manager")
        cn = ref.get("cryptonote_server")
        monerod_info: Dict[str, Any] = {}
        client = ref.get("client")
        if client is not None:
            try:
                monerod_info = await client.get_info()
            except Exception:
                monerod_info = {}
        stats_l = await ledger.stats() if ledger else {}
        cn_stats = cn.stats() if cn else {"sessions": 0, "miners": []}
        cur_job = jm.current_job if jm else None
        return {
            "enabled": True,
            "monerod_height": int(monerod_info.get("height") or 0),
            "monerod_target": int(monerod_info.get("target_height") or 0),
            "monerod_synced": bool(monerod_info.get("synchronized") or False),
            "active_miners": int(cn_stats.get("sessions", 0)),
            "miner_addresses": cn_stats.get("miners", []),
            "blocks_found": int(stats_l.get("blocks_found", 0)),
            "current_seed_hash": (cur_job.seed_hash.hex() if cur_job else None),
            "current_height": int(cur_job.height) if cur_job else 0,
            "pool_fee_address": ref.get("pool_fee_address"),
            "pool_fee_bps": 500,
            "ledger": stats_l,
        }

    @app.get("/api/pool/xmr/blocks")
    async def xmr_blocks(limit: int = Query(50, ge=1, le=500)):
        """List recent XMR blocks the pool found (most recent first)."""
        try:
            from animica.stratum_pool.xmr_payouts import DEFAULT_LEDGER_PATH
            from pathlib import Path
            entries: list = []
            path = Path(DEFAULT_LEDGER_PATH)
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            import json as _json
                            entries.append(_json.loads(line))
                        except Exception:
                            continue
            entries.sort(
                key=lambda e: e.get("monero_block_height", 0), reverse=True
            )
            return {"blocks": entries[:limit], "total": len(entries)}
        except Exception as exc:
            return {"blocks": [], "total": 0, "error": str(exc)}

    @app.post("/api/pool/xmr/register")
    async def xmr_register(payload: Dict[str, Any]):
        """Register a miner's payout preference.

        Body:
          {
            "animica_address": "anim1...",
            "payout_currency": "xmr" | "anm",
            "xmr_address": "4... or 8..."   # required when currency=xmr
          }

        No on-chain signature required for now — the registration is
        only USED at payout time, and the worst case is that a malicious
        actor redirects another miner's earnings to themselves. We'll
        add bech32 signature verification once `animica wallet sign-msg`
        is wired up.
        """
        from animica.stratum_pool.xmr_payouts import (
            load_registry, save_registry,
        )
        import re as _re

        anim1 = str(payload.get("animica_address") or "").strip()
        ccy = str(payload.get("payout_currency") or "xmr").strip().lower()
        xmr = str(payload.get("xmr_address") or "").strip()

        if not _re.match(r"^anim1[0-9a-z]{30,}$", anim1):
            raise HTTPException(status_code=400,
                                detail="animica_address must be bech32 anim1…")
        if ccy not in ("xmr", "anm"):
            raise HTTPException(status_code=400,
                                detail="payout_currency must be 'xmr' or 'anm'")
        if ccy == "xmr":
            # Monero addresses are 95 chars (standard) or 106 (integrated)
            if not (_re.match(r"^[48][0-9A-HJ-NP-Za-km-z]{94,105}$", xmr)):
                raise HTTPException(
                    status_code=400,
                    detail="xmr_address must be a Monero primary/integrated address",
                )

        reg = load_registry()
        reg[anim1] = {
            "xmr_address": xmr if ccy == "xmr" else "",
            "payout_currency": ccy,
        }
        save_registry(reg)
        return {
            "ok": True,
            "animica_address": anim1,
            "payout_currency": ccy,
            "registered_count": len(reg),
        }

    @app.get("/api/pool/xmr/miner/{anim1_address}")
    async def xmr_miner(anim1_address: str):
        """Aggregated XMR earnings for a single miner address."""
        from animica.stratum_pool.xmr_payouts import (
            DEFAULT_LEDGER_PATH, _load_jsonl, load_registry,
        )
        entries = _load_jsonl(DEFAULT_LEDGER_PATH)
        registry = load_registry()
        owed_atomic = 0
        paid_atomic = 0
        block_count = 0
        for e in entries:
            credits = e.get("miner_credits_atomic", {})
            if anim1_address in credits:
                amt = int(credits[anim1_address])
                if anim1_address in e.get("paid_anim1", []):
                    paid_atomic += amt
                else:
                    owed_atomic += amt
                block_count += 1
        return {
            "anim1_address": anim1_address,
            "owed_atomic": owed_atomic,
            "paid_atomic": paid_atomic,
            "owed_xmr": owed_atomic / 1e12,
            "paid_xmr": paid_atomic / 1e12,
            "block_count_contributed_to": block_count,
            "registered_xmr_address": registry.get(anim1_address),
        }

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
