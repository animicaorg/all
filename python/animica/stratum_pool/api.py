from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .package_builder import MinerBundleBuilder
from .portal import MiningPortalService, build_bundle_input
from .metrics import PoolMetrics, RentalConflict

_ANIM1_RE = re.compile(r"^anim1[0-9a-z]{30,}$")
_XMR_RE = re.compile(r"^[48][0-9A-HJ-NP-Za-km-z]{94,105}$")


# --- Accurate Animica NETWORK hashrate -------------------------------------
# The pool's `pool_hashrate` is share-difficulty/window — only the miners
# connected to THIS pool, and noisy. The chain-wide network hashrate is what
# the node computes authoritatively in `chain.getNetworkHashrate`: it sums
# difficulty_to_work(theta_micro) over a recent block window and divides by the
# actual elapsed wall-time between the first and last block header. We fetch
# that from the node and surface it on the stats page. Cached briefly so the
# 30s stats poll doesn't hammer the node (the node also caches 10s).
_NET_HR_TTL_SEC = 10.0
_NET_HR_CACHE: Dict[str, Any] = {"at": 0.0, "key": None, "payload": None}
# The node reports hashrate in "HashShare/sec" = raw SHA3 hashes/sec / 2**32
# (see rpc/hashrate.py:work_to_hashshare_rate). Multiply back out to get a
# human-readable raw H/s figure for the stats page.
_HASHSHARE_TRIALS = 2 ** 32


async def _fetch_network_hashrate(metrics: PoolMetrics, window_blocks: int = 120) -> Dict[str, Any]:
    """Authoritative chain-wide Animica hashrate via the node RPC.

    Returns the node's payload (``hashrate_hsps``, ``window_blocks``,
    ``window_seconds``, ``height_start/end``, ``method``) plus an ``ok`` flag.
    On any failure returns ``{"ok": False, "hashrate_hsps": None, ...}`` so the
    UI can show "—" rather than break.
    """
    now = time.time()
    key = int(window_blocks)
    cached = _NET_HR_CACHE
    if (
        cached["payload"] is not None
        and cached["key"] == key
        and (now - float(cached["at"] or 0.0)) < _NET_HR_TTL_SEC
    ):
        return dict(cached["payload"])

    try:
        from mining.share_submitter import AsyncJsonRpcClient

        rpc_url = str(getattr(metrics.config, "rpc_url", "") or "http://127.0.0.1:8545/rpc")
        client = AsyncJsonRpcClient(rpc_url)
        try:
            res = await client.call(
                "chain.getNetworkHashrate", [int(window_blocks)], timeout_s=8.0
            )
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
        payload = dict(res) if isinstance(res, dict) else {}
        payload["ok"] = payload.get("hashrate_hsps") is not None
    except Exception as exc:
        payload = {
            "ok": False,
            "hashrate_hsps": None,
            "window_blocks": int(window_blocks),
            "error": str(exc),
        }

    _NET_HR_CACHE.update({"at": now, "key": key, "payload": payload})
    return dict(payload)


# --- Projected Monero (RandomX) hashrate estimate (Option B) ----------------
# Animica's PoW is SHA3-256 (fast); Monero is RandomX (slow, memory-hard), so
# the two H/s figures differ by ~4-5 orders of magnitude per CPU thread. We
# can't equate them, but we CAN estimate the RandomX hashrate the same fleet
# would produce, without Monero mining actually running, by:
#   (a) dividing the Animica hashrate by a representative SHA3-per-thread rate
#       to recover an approximate CPU-thread count, then
#   (b) multiplying by a representative RandomX-per-thread rate.
# Both constants are env-calibratable — measure one representative CPU once
# (e.g. `xmrig --bench=1M` for RandomX; the animica miner's per-thread H/s for
# SHA3) and set them so the projection matches reality:
#   ANIMICA_SHA3_HPS_PER_THREAD  default 6e6  (6 MH/s SHA3-256 per thread)
#   ANIMICA_RX_HPS_PER_THREAD    default 800  (0.8 kH/s RandomX per thread)
# Caveat: only meaningful for CPU miners. SHA3 ASIC/GPU contributors inflate
# the Animica hashrate but cannot mine RandomX at all, so this is an UPPER
# bound that assumes an all-CPU fleet.
_DEFAULT_SHA3_HPS_PER_THREAD = 6_000_000.0
_DEFAULT_RX_HPS_PER_THREAD = 800.0


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name) or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _project_monero_hashrate(animica_hps: Optional[float]) -> Dict[str, Any]:
    sha3_pt = _env_float("ANIMICA_SHA3_HPS_PER_THREAD", _DEFAULT_SHA3_HPS_PER_THREAD)
    rx_pt = _env_float("ANIMICA_RX_HPS_PER_THREAD", _DEFAULT_RX_HPS_PER_THREAD)
    hps = max(0.0, float(animica_hps or 0.0))
    est_threads = hps / sha3_pt if sha3_pt > 0 else 0.0
    return {
        "projected_monero_hps": est_threads * rx_pt,
        "animica_hashrate": hps,
        "est_cpu_threads": est_threads,
        "sha3_hps_per_thread": sha3_pt,
        "rx_hps_per_thread": rx_pt,
        "estimate": True,
        "estimate_basis": "per-thread SHA3->RandomX conversion (assumes all-CPU fleet)",
    }


def _rental_secret() -> str:
    return str(os.getenv("POOL_RENTAL_SHARED_SECRET") or "").strip()


async def _require_rental_auth(request: Request, raw_body: bytes) -> None:
    """Authenticate a marketplace → pool rental call via HMAC.

    The caller signs ``method\\npath\\nts\\nbody`` with the shared secret and
    sends ``X-Rental-Sig`` (hex HMAC-SHA256) + ``X-Rental-Ts`` (unix seconds).
    Requests with clock drift > 60s or a bad signature are rejected. Mirrors
    the HMAC-then-constant-time-compare idiom in nowpayments.ts.
    """
    secret = _rental_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="rental API not configured")
    sig = str(request.headers.get("x-rental-sig") or "")
    ts = str(request.headers.get("x-rental-ts") or "")
    if not sig or not ts:
        raise HTTPException(status_code=401, detail="missing rental auth headers")
    try:
        drift = abs(time.time() - float(ts))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="bad rental timestamp")
    if drift > 60:
        raise HTTPException(status_code=401, detail="rental auth timestamp drift")
    message = b"\n".join(
        [
            request.method.encode("utf-8"),
            request.url.path.encode("utf-8"),
            ts.encode("utf-8"),
            raw_body or b"",
        ]
    )
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="bad rental signature")


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

    @app.get("/api/compute/clore-token")
    async def clore_token(worker: str = "", address: str = "", gpu: str = ""):
        """Hand a consenting miner a Clore onboarding config priced for its GPU.

        The 10.2.8+ client calls this only after the machine's operator opted in,
        passing the detected GPU model so the returned config's autoprice is set
        competitively from the live Clore market (a flat price would leave most
        cards idle). Returns {"token": null} when unconfigured — the miner then
        skips enrollment cleanly. See stratum_pool/clore_tokens.py.
        """
        from .clore_tokens import assign_token
        return {"token": assign_token(worker, address, gpu),
                "price_usd_day": None}

    @app.get("/api/compute/status")
    async def clore_status():
        from .clore_tokens import stats
        return stats()

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

    @app.get("/api/pool/network")
    async def pool_network(window_blocks: int = Query(120, ge=2, le=2016)):
        """Accurate Animica network hashrate (raw H/s) + projected Monero rate.

        The primary ``network_hashrate_hps`` is computed by the pool from the
        actual share stream: Σ(per-share expected SHA3 hashes = 2**256 /
        share_target_int) / window. This matches what miners' xmrig reports.

        The node's ``chain.getNetworkHashrate`` is included only as
        ``node_theta_hashrate_hps`` for reference — it derives from on-chain Θ
        (the PoIES acceptance threshold), which understates raw hash power by
        orders of magnitude, so it is NOT the headline figure.
        """
        try:
            ps = metrics.pool_summary()
        except Exception:
            ps = {}
        net_hps = float(ps.get("network_hashrate_hps") or 0.0)
        node = await _fetch_network_hashrate(metrics, window_blocks=window_blocks)
        node_hsps = node.get("hashrate_hsps")
        node_raw = (float(node_hsps) * _HASHSHARE_TRIALS) if node_hsps is not None else None
        return {
            "ok": net_hps > 0.0,
            "network_hashrate_hps": net_hps,
            "source": ps.get("hashrate_source") or "share_work",
            "reported_hashrate_hps": ps.get("reported_hashrate_hps"),
            "reporting_miners": ps.get("reporting_miners"),
            "hashrate_1m": ps.get("hashrate_raw_1m"),
            "hashrate_15m": ps.get("hashrate_raw_15m"),
            "hashrate_1h": ps.get("hashrate_raw_1h"),
            "pool_share_ratio_hps": ps.get("pool_hashrate"),  # legacy ratio/sec
            # Reference only — theta-based, understates raw hash power.
            "node_theta_hashrate_hps": node_raw,
            "node_theta_window_blocks": node.get("window_blocks"),
            "projected_monero": _project_monero_hashrate(net_hps),
        }

    @app.post("/api/pool/hashrate/report")
    async def hashrate_report(payload: Dict[str, Any]):
        """Miner-reported hashrate (Option A).

        The dual-miner polls its local xmrig HTTP API and POSTs its measured
        Animica (SHA3) H/s here every ~30s. The pool sums fresh reports for an
        accurate, smooth network hashrate. Body:
          { "worker": "rig-01", "address": "anim1...", "hps": 8123456.0,
            "algo": "animica" }
        No auth: the worst case is an inflated public hashrate number; it does
        not touch payouts or consensus.
        """
        worker = str(payload.get("worker") or "").strip()
        if not worker:
            raise HTTPException(status_code=400, detail="worker is required")
        try:
            hps = float(payload.get("hps") or 0.0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="hps must be numeric")
        if hps < 0 or hps != hps or hps in (float("inf"), float("-inf")):
            raise HTTPException(status_code=400, detail="hps out of range")
        metrics.record_reported_hashrate(
            worker=worker,
            address=str(payload.get("address") or "") or None,
            hps=hps,
            algo=str(payload.get("algo") or "animica"),
        )
        total, n = metrics.reported_network_hashrate()
        return {"ok": True, "reported_network_hashrate_hps": total, "reporting_miners": n}

    # --- Monero (XMR) dual-mining stats -----------------------------------
    # These endpoints expose the parallel RandomX pool the dual-miner uses.
    # When XMR mining is disabled (ANIMICA_POOL_XMR_ENABLED!=1) they return
    # zeros / empty so the portal UI can show "0 active miners".

    @app.get("/api/pool/xmr/summary")
    async def xmr_summary():
        # Accurate network hashrate + projected Monero, included regardless of
        # whether live XMR mining is enabled (so "projected if all miners
        # dual-mined" shows even before Monero is turned on). Uses the pool's
        # share-work hashrate (real H/s), not the theta-based node figure.
        try:
            _net_hps = float(metrics.pool_summary().get("network_hashrate_hps") or 0.0)
        except Exception:
            _net_hps = 0.0
        _projected = _project_monero_hashrate(_net_hps)
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
                "network_hashrate_hps": _net_hps,
                "projected_monero": _projected,
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
            "network_hashrate_hps": _net_hps,
            "projected_monero": _projected,
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

    # --- Rig-rental redirect engine (marketplace ↔ pool, HMAC-guarded) -----
    # These power pool.animica.org/app. All are server-to-server only and
    # require the shared-secret HMAC signature. Read endpoints expose an
    # opaque rig_id (sha256(worker|address)); the owner payout address is
    # never returned to the public.

    def _claim_worker(address: str, bucket: int) -> str:
        token = hmac.new(
            _rental_secret().encode("utf-8"),
            f"{address}:{bucket}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:16]
        return f"rent-claim-{token}"

    @app.post("/api/rental/assignments")
    async def rental_create(request: Request):
        raw = await request.body()
        await _require_rental_auth(request, raw)
        import json as _json
        try:
            body: Dict[str, Any] = _json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON body")
        rental_id = str(body.get("rental_id") or "").strip()
        owner_worker = str(body.get("owner_worker") or "").strip()
        owner_address = str(body.get("owner_address") or "").strip()
        coins = str(body.get("coins") or "").strip().upper()
        renter_anm = (str(body.get("renter_anm_address") or "").strip() or None)
        renter_xmr_anim = (str(body.get("renter_xmr_anim_address") or "").strip() or None)
        anm_mode = (str(body.get("anm_mode") or "").strip().lower() or None)
        if not rental_id or not owner_worker or not owner_address:
            raise HTTPException(status_code=400, detail="rental_id, owner_worker, owner_address required")
        if not _ANIM1_RE.match(owner_address):
            raise HTTPException(status_code=400, detail="owner_address must be bech32 anim1…")
        if coins not in {"ANM", "XMR", "BOTH"}:
            raise HTTPException(status_code=400, detail="coins must be ANM, XMR, or BOTH")
        if coins in {"ANM", "BOTH"}:
            if not renter_anm or not _ANIM1_RE.match(renter_anm):
                raise HTTPException(status_code=400, detail="renter_anm_address required for ANM rentals")
        if coins in {"XMR", "BOTH"}:
            if not renter_xmr_anim or not _ANIM1_RE.match(renter_xmr_anim):
                raise HTTPException(status_code=400, detail="renter_xmr_anim_address required for XMR rentals")
        try:
            start_ts = float(body.get("start_ts"))
            end_ts = float(body.get("end_ts"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="start_ts and end_ts must be numbers")
        if end_ts <= start_ts:
            raise HTTPException(status_code=400, detail="end_ts must be after start_ts")
        try:
            rec = metrics.upsert_rental_assignment(
                id=rental_id,
                owner_worker=owner_worker,
                owner_address=owner_address,
                coins=coins,
                start_ts=start_ts,
                end_ts=end_ts,
                renter_anm_address=renter_anm,
                renter_xmr_anim_address=renter_xmr_anim,
                anm_mode=anm_mode,
            )
        except RentalConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "rig_id": metrics.rig_id_for(owner_worker, owner_address), "assignment": rec}

    @app.post("/api/rental/assignments/{rental_id}/cancel")
    async def rental_cancel(rental_id: str, request: Request):
        raw = await request.body()
        await _require_rental_auth(request, raw)
        cancelled = metrics.cancel_rental_assignment(rental_id)
        return {"ok": True, "cancelled": bool(cancelled)}

    @app.get("/api/rental/assignments/{rental_id}")
    async def rental_get(rental_id: str, request: Request):
        await _require_rental_auth(request, b"")
        rec = metrics.get_rental_assignment(rental_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="rental not found")
        stats = metrics.rig_stats(
            worker=str(rec["owner_worker"]), address=str(rec["owner_address"])
        )
        window = metrics.rig_window_stats(
            worker=str(rec["owner_worker"]),
            address=str(rec["owner_address"]),
            start_ts=float(rec["start_ts"]),
            end_ts=min(time.time(), float(rec["end_ts"])),
        )
        return {"assignment": rec, "rig": stats, "window": window}

    @app.get("/api/rental/rigs")
    async def rental_rigs(request: Request):
        await _require_rental_auth(request, b"")
        return {"items": metrics.rentable_rigs()}

    @app.get("/api/rental/rigs/{rig_id}/stats")
    async def rental_rig_stats(rig_id: str, request: Request):
        await _require_rental_auth(request, b"")
        stats = metrics.rig_stats(rig_id=rig_id)
        if stats is None:
            raise HTTPException(status_code=404, detail="rig not found or offline")
        return stats

    @app.post("/api/rental/ownership/challenge")
    async def rental_ownership_challenge(request: Request):
        raw = await request.body()
        await _require_rental_auth(request, raw)
        import json as _json
        try:
            body = _json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON body")
        address = str(body.get("address") or "").strip()
        if not _ANIM1_RE.match(address):
            raise HTTPException(status_code=400, detail="address must be bech32 anim1…")
        bucket = int(time.time() // 600)
        return {
            "address": address,
            "claim_worker": _claim_worker(address, bucket),
            "instructions": "Mine for ~2 minutes with --pool-worker set to claim_worker from this address.",
            "expires_ts": (bucket + 2) * 600,
        }

    @app.post("/api/rental/ownership/verify")
    async def rental_ownership_verify(request: Request):
        raw = await request.body()
        await _require_rental_auth(request, raw)
        import json as _json
        try:
            body = _json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON body")
        address = str(body.get("address") or "").strip()
        claim_worker = str(body.get("claim_worker") or "").strip()
        if not _ANIM1_RE.match(address) or not claim_worker:
            raise HTTPException(status_code=400, detail="address and claim_worker required")
        now = time.time()
        bucket = int(now // 600)
        valid_tokens = {_claim_worker(address, bucket), _claim_worker(address, bucket - 1)}
        if claim_worker not in valid_tokens:
            raise HTTPException(status_code=400, detail="claim_worker not recognized for this address")
        window = metrics.rig_window_stats(
            worker=claim_worker, address=address, start_ts=now - 1800, end_ts=now + 1
        )
        proven = int(window.get("active_buckets") or 0) > 0
        return {"ok": True, "proven": proven, "address": address}

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
