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


# Wallets that may serve inference, same list the x402 capacity gate uses.
# Comma-separated; empty disables the stat rather than reporting a false 0.
_INFERENCE_WALLETS = [
    w.strip() for w in str(os.getenv("ANIMICA_INFERENCE_WORKER_WALLETS", "")).split(",") if w.strip()
]
_SERVING_FRESH_S = 300.0


async def _count_serving_inference_workers(rpc_url: str) -> Dict[str, Any]:
    """How many wallets are ACTUALLY serving inference right now.

    Ground truth is per-wallet ``aicf.workerStatus``. Every other candidate
    over-counts and was verified to do so on this chain:

      * ``aicf.work.listWorkers`` is a stale, different registry — its newest
        heartbeat on mainnet is 89 days old;
      * ``aicf.estimateJobCost.providers`` counts unpruned registration history
        with no freshness filter (210 "providers" while the true count was 0);
      * there is no ``aicf.listServingWorkers``.

    A wallet counts only when registered is true, its heartbeat is within
    300 s (values > 1e12 are milliseconds), and it advertises at least one real
    tier. This mirrors src/capacity.js in the x402 gateway ON PURPOSE: the
    stats page and the capacity gate must not disagree about who is serving.

    Returns ``serving=None`` when no wallet list is configured — an unknown is
    reported as unknown, never as zero.
    """
    if not _INFERENCE_WALLETS:
        return {"serving": None, "configured": 0, "reason": "no_wallets_configured"}
    from mining.share_submitter import AsyncJsonRpcClient

    serving = 0
    serving_wallets: list = []
    now = time.time()
    client = AsyncJsonRpcClient(rpc_url)
    try:
        for wallet in _INFERENCE_WALLETS:
            try:
                res = await client.call(
                    "aicf.workerStatus", {"address": wallet}, timeout_s=5.0
                ) or {}
            except Exception:
                continue  # unreachable counts as NOT serving, never as serving
            if not res.get("registered"):
                continue
            last = res.get("last_seen") or res.get("last_seen_at") or 0
            try:
                last = float(last)
            except (TypeError, ValueError):
                continue
            if last > 1e12:
                last = last / 1000.0
            if not last or (now - last) > _SERVING_FRESH_S:
                continue
            tiers = [t for t in (res.get("tiers") or []) if t and t != "pipeline"]
            if not tiers:
                continue
            serving += 1
            serving_wallets.append(wallet)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
    return {"serving": serving, "configured": len(_INFERENCE_WALLETS),
            "fresh_window_seconds": int(_SERVING_FRESH_S),
            "serving_wallets": serving_wallets}


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

    @app.on_event("startup")
    async def _warm_advisor():
        # Warm the advisor in the background so user requests are fast: (1) load
        # the RAG encoder/index (~12s cold), (2) keep an AI worker warm so the
        # cold-start (30–120s) happens off the user path, not on it.
        import threading
        try:
            from . import advisor
            threading.Thread(target=advisor.warm_rag,
                             name="advisor-rag-warm", daemon=True).start()
            advisor.start_warm()
        except Exception:    # noqa: BLE001 — never block startup
            pass

    @app.get("/summary")
    @app.get("/api/pool/summary")
    async def pool_summary():
        return metrics.pool_summary()

    @app.get("/api/pools")
    @app.get("/api/pool/mps")
    @app.get("/api/mps")
    async def mps_stats():
        """MiningPoolStats-compatible pool stats.

        A stable, flat + nested (cryptonote-nodejs-pool style) JSON that
        MiningPoolStats and similar trackers can poll directly. All values are
        derived live from the same source as /api/pool/summary. Hashrates are
        raw H/s (SHA3-256); difficulty is the work-based expectation
        network_hashrate * target_block_interval_s.
        """
        import os
        from datetime import datetime as _dt
        try:
            ps = metrics.pool_summary()
        except Exception:    # noqa: BLE001
            ps = {}
        try:
            rb = metrics.recent_blocks()
        except Exception:    # noqa: BLE001
            rb = {}
        items = rb.get("items") if isinstance(rb, dict) else None
        last = (items[0] if isinstance(items, list) and items else {}) or {}
        last_ts = None
        iso = last.get("timestamp")
        if iso:
            try:
                last_ts = int(_dt.fromisoformat(str(iso)).timestamp())
            except Exception:    # noqa: BLE001
                last_ts = None
        try:
            reward_anm = int(last.get("reward") or 0) / 1_000_000_000
        except Exception:    # noqa: BLE001
            reward_anm = 0.0
        if reward_anm <= 0:
            reward_anm = 300.0    # current per-block subsidy (fallback)
        net_hps = float(ps.get("network_hashrate_hps") or 0.0)
        pool_hps = float(ps.get("hashrate_raw_1h") or ps.get("network_hashrate_hps") or 0.0)
        height = ps.get("height") or last.get("height") or 0
        miners = int(ps.get("reporting_miners") or 0)
        blocks_found = int(ps.get("blocks_found_total")
                           or (rb.get("blocks_found_total") if isinstance(rb, dict) else 0) or 0)
        interval_s = 60
        difficulty = net_hps * interval_s
        fee = 5.0
        try:
            _mp = os.environ.get("ANIMICA_POOL_MIN_PAYOUT_ANM")
            min_payout = float(_mp) if _mp else None
        except Exception:    # noqa: BLE001
            min_payout = None
        ports = [
            {"port": 3333, "desc": "PPS + sub-block shares", "fee": fee, "tls": False},
            {"port": 3334, "desc": "Solo (95% to finder)", "fee": fee, "tls": False},
        ]
        return {
            # flat top-level (maximum tracker compatibility)
            "coin": "Animica", "symbol": "ANM", "algo": "sha3-256",
            "hashrate": pool_hps, "networkHashrate": net_hps,
            "networkDifficulty": difficulty, "height": height,
            "miners": miners, "workers": miners,
            "lastBlock": last_ts, "totalBlocks": blocks_found,
            "fee": fee, "blockReward": reward_anm, "blockTime": interval_s,
            # nested cryptonote-nodejs-pool style
            "pool": {
                "hashrate": pool_hps, "miners": miners, "workers": miners,
                "totalBlocks": blocks_found, "lastBlockFound": last_ts,
                "lastBlockFoundHeight": last.get("height"),
                "fee": fee, "feeType": "PPS + sub-block shares",
                "minPayout": min_payout, "symbol": "ANM",
            },
            "network": {
                "hashrate": net_hps, "difficulty": difficulty,
                "height": height, "reward": reward_anm, "blockTime": interval_s,
            },
            "config": {
                "coin": "Animica", "symbol": "ANM", "algo": "sha3-256",
                "coinUnits": 1_000_000_000, "coinDifficultyTarget": interval_s,
                "poolHost": "pool.animica.org", "poolFee": fee,
                "paymentScheme": "PPS", "ports": ports,
            },
        }

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

    @app.post("/api/advisor/chat")
    async def advisor_chat(request: Request):
        """Site-wide 'setup advisor' chat: recommends the exact `animica up`
        command for the visitor's hardware, backed by exhaustive RAG over the
        Animica docs and Animica's own AI network, with a deterministic command
        engine as ground truth so it never gives a wrong command. Stateless —
        the widget sends the running history each turn (so it 'remembers')."""
        from starlette.concurrency import run_in_threadpool
        try:
            payload = await request.json()
        except Exception:    # noqa: BLE001 — bad body
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        raw = payload.get("messages") or []
        clean = []
        if isinstance(raw, list):
            for m in raw[-24:]:    # cap history
                if (isinstance(m, dict) and m.get("role") in ("user", "assistant")
                        and m.get("content")):
                    clean.append({"role": str(m["role"]),
                                  "content": str(m["content"])[:4000]})
        hw = payload.get("hardware")
        hw = hw if isinstance(hw, dict) else None
        try:
            from . import advisor
            return await run_in_threadpool(advisor.chat, clean, hw)
        except Exception as exc:    # noqa: BLE001 — never 500 the widget
            return {"reply": "Sorry — I hit an error. You can always run "
                    "`pip install -U animica && animica up --plan` to see what "
                    "your machine would do.",
                    "command": "animica up --plan", "tiers": [],
                    "used_ai": False, "error": str(exc)[:200]}

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
        """Animica hashrate (raw H/s), with its scope and sample size stated.

        The primary ``network_hashrate_hps`` is computed by the pool from the
        actual share stream: Σ(per-share expected SHA3 hashes = 2**256 /
        share_target_int) / window. This matches what miners' xmrig reports.

        SCOPE, measured 2026-08-21 by counting blocks rather than arguing:
        share-work sees only shares submitted here, so it is a POOL figure —
        but this pool found 913 of the 912 heights spanned in 24h, all
        found_by_pool=1, from four named rigs. At ~100% there is no meaningful
        solo or direct mining, pool and network are the same population, and
        ``network_hashrate_hps`` IS the network hashrate. ``hashrate_scope``
        therefore reports ``network_equivalent`` while
        ``pool_block_share_pct`` >= 95, and falls back to ``pool_shares_only``
        the moment that ratio drops.

        That also settles which of the two estimates is wrong: Θ-derived
        ``node_theta_hashrate_hps`` reads ~95x HIGHER (4.3 GH/s vs ~46 MH/s)
        and cannot be reconciled with a pool that finds every block at this
        share rate, so Θ overstates. The original docstring claim that Θ
        "understates raw hash power by orders of magnitude" is backwards, and
        so was an intermediate note here that treated Θ as the chain-wide
        truth. Corroborated independently: the advisor earnings engine is
        calibrated on ~50 MH/s.

        SAMPLE SIZE: ``hashrate_window_samples`` reports accepted-share counts
        per window. At the current rate a 1-minute window is usually empty, and
        an empty window is not 0 H/s — it is no sample. Use
        ``hashrate_confident_window``.
        """
        try:
            ps = metrics.pool_summary()
        except Exception:
            ps = {}
        net_hps = float(ps.get("network_hashrate_hps") or 0.0)
        node = await _fetch_network_hashrate(metrics, window_blocks=window_blocks)
        try:
            machines = metrics.active_machines()
        except Exception:
            machines = {}
        try:
            block_share = metrics.pool_block_share()
        except Exception:
            block_share = {}
        rpc_url = str(getattr(metrics.config, "rpc_url", "") or "http://127.0.0.1:8545/rpc")
        try:
            inference = await _count_serving_inference_workers(rpc_url)
        except Exception:
            inference = {}
        mining_addrs = set(machines.get("address_set") or [])
        serving_addrs = set(inference.get("serving_wallets") or [])
        both = mining_addrs & serving_addrs
        dual = {
            "addresses": len(both),
            "machines": sum(1 for _w, a in (machines.get("worker_addresses") or {}).items()
                            if a in both),
            "mining_only": len(mining_addrs - serving_addrs),
            "inference_only": len(serving_addrs - mining_addrs),
        }
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
            "node_theta_hashrate_hps": node_raw,
            "node_theta_window_blocks": node.get("window_blocks"),
            # --- honesty fields (added after measuring these against the raw
            # share stream and the chain; see the scope note below) ----------
            #
            # SCOPE. `network_hashrate_hps` is Sigma(work)/window over shares
            # submitted TO THIS POOL. It cannot see solo or direct miners, so
            # on a network where most hash power mines direct it is a POOL
            # figure wearing a network label. Callers that need chain-wide
            # should read `node_theta_hashrate_hps`.
            # Scope is DERIVED, not asserted. Share-work sees only this pool's
            # shares, so it is a pool figure — unless the pool finds
            # essentially every block, in which case pool and network are the
            # same population. Measured, not assumed, so the label follows
            # reality the day a real solo miner appears.
            "hashrate_scope": (
                "network_equivalent"
                if (block_share.get("share_pct") or 0) >= 95.0
                else "pool_shares_only"
            ),
            "pool_block_share_pct": block_share.get("share_pct"),
            "pool_blocks_in_window": block_share.get("pool_blocks"),
            "chain_blocks_in_window": block_share.get("chain_blocks"),
            "block_share_window_seconds": block_share.get("window_seconds"),
            # Upper bound (95% conf) on hash power that submits shares to no
            # pool and found no block in the window — the only miner this page
            # cannot see. Miners that DO submit shares are counted whether or
            # not they ever find a block.
            "unseen_hashrate_bound_pct": block_share.get("unseen_hashrate_bound_pct"),
            # SPARSITY. At the current share rate a 1-minute window is usually
            # EMPTY, and an empty window is not 0 H/s — it is no sample. The
            # raw fields still report 0.0 for compatibility; these say whether
            # that 0 means anything.
            "hashrate_window_samples": {
                "m1": ps.get("shares_1m"),
                "m15": ps.get("shares_15m"),
                "h1": ps.get("shares_1h"),
            },
            "hashrate_confident_window": "1h",
            # MACHINES. Individual rigs that submitted an accepted share in the
            # window — proof of work, not socket presence. See
            # PoolMetrics.active_machines for why num_miners/reporting_miners/
            # /api/miners each answer a different question.
            "active_machines": machines.get("machines"),
            "active_machine_addresses": machines.get("addresses"),
            "active_machines_named": machines.get("named"),
            "active_machines_window_seconds": machines.get("window_seconds"),
            # INFERENCE. Wallets actually serving inference right now, from the
            # same primitive the x402 capacity gate uses. None = not configured
            # here, which is reported as unknown rather than as zero.
            "inference_workers_serving": inference.get("serving"),
            "inference_wallets_configured": inference.get("configured"),
            # DUAL ROLE. Operators doing BOTH — mining accepted shares and
            # serving inference. Intersected on the wallet address, because
            # that is the only identity the two sides share: a rig name is
            # local to the pool, a serving worker is identified by its wallet.
            # `machines` counts the rigs belonging to those wallets, so it can
            # exceed `addresses` when one operator runs several rigs.
            "dual_role_addresses": dual["addresses"],
            "dual_role_machines": dual["machines"],
            "mining_only_addresses": dual["mining_only"],
            "inference_only_addresses": dual["inference_only"],
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

    # Monero (XMR) dual-mining routes REMOVED. XMR dual-mining was disabled
    # and monerod removed from this host on 2026-07-16; the endpoints kept
    # answering with zeros and a "projected if all miners dual-mined"
    # figure, which reads as a live feature that does not exist.

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
