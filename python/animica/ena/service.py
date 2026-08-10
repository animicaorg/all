"""
animica.ena.service
===================

Minimal HTTP API for ENA, exposing sessions, indexes, jobs, receipts, eval
runs, and training runs. Implemented on the standard-library ``http.server``
so ``animica ena serve`` needs no extra dependencies; the handler dispatches
to the same service objects the CLI uses.

Routes
------
GET  /health
GET  /jobs              ?status=&type=
GET  /jobs/<id>
POST /jobs              {type, params}
POST /jobs/<id>/run
POST /jobs/<id>/submit-result   {result, worker_id}  (worker-local jobs)
POST /jobs/<id>/verify
POST /jobs/<id>/receipt
POST /jobs/<id>/export
GET  /indexes
POST /search            {query, mode, index}
GET  /training/runs
GET  /training/runs/<id>
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)
from typing import Any
from urllib.parse import parse_qs, urlparse

# State-changing / economic routes that must be operator-authorized when a token is configured.
# Everything else (worker participation: claim/submit/heartbeat/serve, and read-only GETs) stays
# open so the training network remains permissionless. Gate = env ANIMICA_ENA_API_TOKEN.
_SENSITIVE_ROUTES = frozenset({
    "/pool/create", "/pool/fund/confirm", "/demand/confirm",
    "/pool/aggregate", "/pool/payout", "/feedback",
    # /pool/accrue CREDITS ANM (10 per block, by weight) and advances the height
    # watermark, so it is economic and must be operator-authorized. It was added in
    # 9.5.5 without being listed here, which left it callable by anything that could
    # reach the coordinator.
    "/pool/accrue",
    # /pool/settle BROADCASTS TRANSFERS from the configured payer. Strictly more
    # sensitive than accrue, which only writes a ledger.
    "/pool/settle",
})
_ENA_API_TOKEN_ENV = "ANIMICA_ENA_API_TOKEN"


def _make_handler(facade):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AnimicaENA/0.2"

        def log_message(self, *args):  # quiet by default
            pass

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-headers", "content-type")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # noqa: N802 - CORS preflight
            self._send(204, {})

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                return {}

        def _authorized(self) -> bool:
            """Constant-time bearer-token check. Backward-compatible: if no token is configured,
            the route stays open (matches the tools-approval pattern) — set ANIMICA_ENA_API_TOKEN
            to lock the sensitive/economic routes down. Reachable publicly via pool.animica.org."""
            token = os.environ.get(_ENA_API_TOKEN_ENV, "")
            if not token:
                return True
            hdr = self.headers.get("authorization", "") or ""
            if hdr.lower().startswith("bearer "):
                hdr = hdr[7:].strip()
            return bool(hdr) and hmac.compare_digest(hdr, token)

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                if path == "/health":
                    return self._send(200, {"status": "ok", "service": "ena"})
                if path == "/jobs":
                    return self._send(200, {"jobs": facade.jobs.list(
                        status=q.get("status"), job_type=q.get("type"))})
                if path.startswith("/jobs/"):
                    return self._send(200, facade.jobs.get(path.split("/", 2)[2]))
                if path == "/indexes":
                    return self._send(200, {"indexes": facade.store.list_indexes()})
                if path == "/training/runs":
                    return self._send(200, {"runs": facade.list_runs()})
                if path.startswith("/training/runs/"):
                    return self._send(200, facade.run_status(path.rsplit("/", 1)[1]))
                if path == "/stats":
                    return self._send(200, facade.stats())
                if path == "/datasets":
                    return self._send(200, {"datasets": facade.list_datasets()})
                if path == "/demand/config":
                    return self._send(200, facade.demand.config())
                if path == "/demand/status":
                    jid = q.get("job_id")
                    if not jid:
                        return self._send(400, {"error": "job_id required"})
                    return self._send(200, facade.demand.status(jid))
                if path == "/wallet/poll":
                    rid = q.get("id") or q.get("requestId")
                    if not rid:
                        return self._send(400, {"error": "id required"})
                    return self._send(200, facade.walletconnect.poll(rid))
                if path == "/pool/list":
                    return self._send(200, {"pools": facade.pool.list_pools(
                        status=q.get("status"))})
                if path == "/pool/status":
                    pid = q.get("pool_id")
                    if not pid:
                        return self._send(400, {"error": "pool_id required"})
                    return self._send(200, facade.pool.status(pid))
                if path == "/pool/shard/data":
                    sid = q.get("shard_id")
                    if not sid:
                        return self._send(400, {"error": "shard_id required"})
                    return self._send(200, facade.pool.read_shard_data(sid))
                if path == "/pool/checkpoint":
                    pid = q.get("pool_id")
                    if not pid:
                        return self._send(400, {"error": "pool_id required"})
                    return self._send(200, facade.pool.read_promoted_checkpoint(pid))
                if path == "/pool/eval-data":
                    pid = q.get("pool_id")
                    if not pid:
                        return self._send(400, {"error": "pool_id required"})
                    return self._send(200, facade.pool.read_eval_data(pid))
                if path == "/pool/tools":
                    return self._send(200, {"tools": facade.tools.list(
                        status=q.get("status"))})
                if path == "/pool/synthesis-targets":
                    # Helix targeting: weakest held-out topics + grounding corpus
                    # for worker-local synthesis. Model-free; old coordinators 404.
                    pid = q.get("pool_id")
                    if not pid:
                        return self._send(400, {"error": "pool_id required"})
                    n = q.get("n")
                    pool = facade.pool.get(pid)
                    targets = facade.curriculum.synthesis_targets(
                        pool, max_targets=int(n) if n else 8)
                    return self._send(200, {"targets": targets})
                if path == "/pool/leaderboard":
                    pid = q.get("pool_id")
                    if not pid:
                        return self._send(400, {"error": "pool_id required"})
                    return self._send(200, {"leaderboard": facade.pool.leaderboard(pid)})
                if path == "/pool/payouts":
                    pid = q.get("pool_id")
                    if not pid:
                        return self._send(400, {"error": "pool_id required"})
                    rnd = q.get("round")
                    return self._send(200, {"payouts": facade.pool.payouts(
                        pid, round=int(rnd) if rnd is not None else None)})
                if path == "/pool/models":
                    return self._send(200, {"models": facade.pool.list_models()})
                if path == "/pool/model":
                    mid = q.get("model_id")
                    if not mid:
                        return self._send(400, {"error": "model_id required"})
                    return self._send(200, facade.pool.get_global_model(mid))
                return self._send(404, {"error": "not found", "path": path})
            except Exception as exc:  # noqa: BLE001
                return self._send(400, {"error": str(exc)})

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            body = self._body()
            try:
                if path in _SENSITIVE_ROUTES and not self._authorized():
                    return self._send(401, {"error": f"unauthorized: {path} requires a bearer token "
                                                     f"(set {_ENA_API_TOKEN_ENV} + send Authorization: Bearer …)"})
                if path == "/feedback":
                    return self._send(200, facade.submit_feedback(
                        prompt=body.get("prompt", ""),
                        chosen=body.get("chosen", ""),
                        rejected=body.get("rejected", ""),
                        source=body.get("source"),
                        contributor=body.get("contributor")))
                if path == "/jobs":
                    return self._send(200, facade.jobs.create(
                        body["type"], body.get("params", {}),
                        requester=body.get("requester")))
                if path == "/jobs/claim":
                    claimed = facade.jobs.claim(body["worker_id"], body.get("types"))
                    return self._send(200, claimed or {})
                if path == "/datasets/contribute":
                    return self._send(200, facade.contribute_dataset(
                        name=body.get("name"), kind=body.get("kind", "contributed"),
                        rows=body.get("rows"), url=body.get("url"),
                        curate=body.get("curate", True),
                        contributor=body.get("contributor")))
                if path == "/demand/quote":
                    return self._send(200, facade.demand.quote(
                        body["job_type"], body.get("params", {}),
                        body["reward_anm"], requester=body.get("requester")))
                if path == "/demand/confirm":
                    return self._send(200, facade.demand.confirm(
                        body["job_id"], body["txid"]))
                if path == "/wallet/start":
                    return self._send(200, facade.walletconnect.start(body.get("appOrigin")))
                if path == "/wallet/callback":
                    return self._send(200, facade.walletconnect.callback(
                        body["requestId"], bool(body.get("approved")),
                        body.get("accounts", [])))
                if path == "/search":
                    return self._send(200, {"results": facade.search(
                        body["query"], mode=body.get("mode", "hybrid"),
                        index=body.get("index"))})
                if path.startswith("/jobs/") and path.endswith("/submit-result"):
                    # Worker-local jobs (e.g. synthesize_qa): the worker ran the
                    # model on its own hardware and POSTs the inline result here.
                    # No model runs on the coordinator.
                    return self._send(200, facade.jobs.submit_result(
                        path.split("/")[2], result=body.get("result"),
                        worker_id=body.get("worker_id")))
                if path.startswith("/jobs/") and path.endswith("/run"):
                    return self._send(200, facade.jobs.run(path.split("/")[2]))
                if path.startswith("/jobs/") and path.endswith("/verify"):
                    return self._send(200, facade.jobs.verify(path.split("/")[2]))
                if path.startswith("/jobs/") and path.endswith("/receipt"):
                    return self._send(200, facade.jobs.receipt(path.split("/")[2]))
                if path.startswith("/jobs/") and path.endswith("/export"):
                    return self._send(200, facade.jobs.export_onchain(path.split("/")[2]))
                if path == "/pool/create":
                    return self._send(200, facade.pool.create(
                        body["base_model"], body["dataset"],
                        method=body.get("method", "lora"), name=body.get("name"),
                        num_shards=body.get("num_shards", 4),
                        hyperparameters=body.get("hyperparameters"),
                        reward_split=body.get("reward_split"),
                        eval_gate=body.get("eval_gate"),
                        requester=body.get("requester"),
                        model_id=body.get("model_id"),
                        auto_promote=body.get("auto_promote", True)))
                if path == "/pool/fund/quote":
                    return self._send(200, facade.pool.fund_quote(
                        body["pool_id"], body["reward_anm"],
                        requester=body.get("requester")))
                if path == "/pool/fund/confirm":
                    return self._send(200, facade.pool.fund_confirm(
                        body["pool_id"], body["txid"],
                        requester=body.get("requester")))
                if path == "/pool/claim":
                    claimed = facade.pool.claim_shard(body["pool_id"], body["worker_id"])
                    return self._send(200, {"claimed": claimed})
                if path == "/pool/submit":
                    return self._send(200, facade.pool.submit_shard(
                        body["pool_id"], body["shard_id"],
                        worker_id=body.get("worker_id"), run_id=body.get("run_id"),
                        checkpoint_path=body.get("checkpoint_path"),
                        metrics=body.get("metrics"),
                        miner_address=body.get("miner_address")))
                if path == "/pool/checkpoint/upload":
                    return self._send(200, facade.pool.store_checkpoint_upload(
                        body["pool_id"], body["shard_id"], body["content_b64"]))
                if path == "/pool/served":
                    return self._send(200, facade.pool.record_served(
                        body["pool_id"], body["worker_id"], int(body.get("tokens") or 0),
                        address=body.get("address"), run_id=body.get("run_id"),
                        latency_ms=body.get("latency_ms"),
                        served_round=body.get("served_round")))
                # A node that serves the promoted checkpoint had no way to ANNOUNCE
                # itself: pool.register_server() existed with ZERO callers and no route,
                # so pool_servers stayed empty and nothing could discover a server to
                # route inference to. Without this the trained model is unreachable no
                # matter how many nodes serve it.
                if path == "/pool/server/register":
                    return self._send(200, facade.pool.register_server(
                        body["pool_id"], body["worker_id"], body["endpoint"],
                        address=body.get("address"),
                        metadata=body.get("metadata")))
                if path == "/pool/servers":
                    return self._send(200, {"servers": facade.pool.list_servers(
                        body["pool_id"], status=body.get("status", "active"))})
                # What one address has earned. `animica up` shows this so an operator can
                # see ENA income instead of guessing whether serving pays anything.
                if path == "/pool/earnings":
                    return self._send(200, facade.pool.earnings(
                        body["address"], pool_id=body.get("pool_id")))
                # Credit the per-block emission (10 ANM/block to trainers+servers by
                # weight). Ledger-only: this package can verify money IN but holds no
                # signing key, so it must never claim to have transferred.
                if path == "/pool/accrue":
                    return self._send(200, facade.pool.accrue(
                        body["pool_id"], height=body.get("height")))
                # Pay credited earnings ON CHAIN. Does nothing unless
                # ANIMICA_ENA_SETTLE=1 and ANIMICA_ENA_SETTLE_FROM name a funded payer.
                if path == "/pool/settle":
                    return self._send(200, facade.pool.settle(
                        body["pool_id"], height=body.get("height")))
                if path == "/pool/tools/propose":
                    return self._send(200, facade.tools.propose(
                        body["name"], body.get("description", ""),
                        body.get("parameters") or {}, body["handler_code"],
                        proposer=body.get("proposer")))
                if path in ("/pool/tools/approve", "/pool/tools/reject"):
                    try:
                        if path.endswith("approve"):
                            res = facade.tools.approve(
                                body["name"], approver=body.get("approver", "admin"),
                                admin_token=body.get("admin_token", ""))
                        else:
                            res = facade.tools.reject(
                                body["name"], reason=body.get("reason", ""),
                                admin_token=body.get("admin_token", ""))
                        return self._send(200, res)
                    except PermissionError as exc:
                        return self._send(403, {"error": str(exc)})
                if path == "/pool/heartbeat":
                    return self._send(200, facade.pool.heartbeat(
                        body["pool_id"], body["worker_id"]))
                if path == "/pool/release":
                    return self._send(200, facade.pool.release_shard(
                        body["pool_id"], body["shard_id"],
                        worker_id=body.get("worker_id")))
                if path == "/pool/aggregate":
                    return self._send(200, facade.pool.aggregate(
                        body["pool_id"], eval_score=body.get("eval_score"),
                        min_submitted=body.get("min_submitted")))
                if path == "/pool/payout":
                    return self._send(200, facade.pool.payout(
                        body["pool_id"], round=body.get("round"),
                        cap_nano=body.get("cap_nano"), roles=body.get("roles")))
                return self._send(404, {"error": "not found", "path": path})
            except KeyError as exc:
                return self._send(400, {"error": f"missing field: {exc}"})
            except Exception as exc:  # noqa: BLE001
                return self._send(400, {"error": str(exc)})

    return Handler


def _start_emission(facade) -> None:
    """Background ticker that credits the per-block emission.

    Without this, accrue() is a route nobody calls — which is precisely how the old
    funder-budget payout went unrun for 84 rounds while 394 contributions earned nothing,
    and why miner logs read "earned 0.0 ANM" no matter how much work they did. The
    entitlement is per block, so crediting has to be automatic.

    Safe to run unattended: accrue() is ledger-only (no key, no broadcast), pays exactly
    10 ANM per elapsed block, clamps the elapsed count, and initialises its watermark to
    the current height on first run so it can never emit retroactively.

    Disable with ENA_EMISSION=0; interval via ENA_EMISSION_SECS (default 60, i.e. about
    one chain block).
    """
    import os
    import threading
    import time

    if os.environ.get("ENA_EMISSION", "1") == "0":
        return
    interval = max(30, int(os.environ.get("ENA_EMISSION_SECS", "60")))
    pool = getattr(facade, "pool", None)
    accrue = getattr(pool, "accrue", None)
    if not callable(accrue):
        return

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                for p in pool.list_pools():
                    pid = p.get("pool_id")
                    if not pid:
                        continue
                    try:
                        res = accrue(pid)
                    except Exception as exc:      # noqa: BLE001
                        log.warning("ena emission failed for %s: %s", pid, exc)
                        continue
                    if int(res.get("paid_nano") or 0) > 0:
                        log.info(
                            "ena emission: credited %.6f ANM across %d recipient(s) "
                            "for %d block(s) in %s",
                            int(res["paid_nano"]) / 1e9, len(res.get("entries") or []),
                            int(res.get("blocks") or 0), pid)
                    # Then pay out what is due. settle() is a no-op unless the operator
                    # set ANIMICA_ENA_SETTLE=1 and named a payer, so crediting keeps
                    # working on nodes that never opt into spending.
                    try:
                        st = pool.settle(pid)
                    except Exception as exc:      # noqa: BLE001
                        log.warning("ena settlement failed for %s: %s", pid, exc)
                        st = None
                    if st and int(st.get("paid_nano") or 0) > 0:
                        log.info(
                            "ena settlement: PAID %.6f ANM on chain in %d transfer(s) "
                            "from %s (pool %s)",
                            int(st["paid_nano"]) / 1e9, len(st.get("settled") or []),
                            str(st.get("from"))[:20], pid)
            except Exception as exc:              # noqa: BLE001 — never kill the ticker
                log.warning("ena emission tick failed: %s", exc)

    threading.Thread(target=_loop, name="ena-emission", daemon=True).start()


def _start_pool_sweeper(facade) -> None:
    """Background ticker that unsticks stalled training rounds.

    Periodically calls ``facade.pool.sweep()`` to reopen abandoned shard claims
    and force-aggregate rounds that have stalled (>=1 submitted shard, no live
    claims, no progress for the timeout). Daemon thread — dies with the process.
    Disable with ENA_POOL_SWEEP=0; interval via ENA_POOL_SWEEP_SECS (default 120).
    """
    import os
    import threading
    import time

    if os.environ.get("ENA_POOL_SWEEP", "1") == "0":
        return
    interval = max(15, int(os.environ.get("ENA_POOL_SWEEP_SECS", "120")))
    sweep = getattr(getattr(facade, "pool", None), "sweep", None)
    if not callable(sweep):
        return

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                actions = sweep()
                for a in (actions or []):
                    if a.get("aggregated") or a.get("reclaimed"):
                        print(f"[ena.sweep] {a}")
            except Exception as exc:  # noqa: BLE001 - sweeper must never crash the server
                print(f"[ena.sweep] error: {exc}")

    threading.Thread(target=_loop, name="ena-pool-sweeper", daemon=True).start()
    print(f"[ena] pool sweeper started (every {interval}s)")


class _Coordinator(ThreadingHTTPServer):
    """ThreadingHTTPServer with a listen backlog that survives a real fleet.

    `http.server` defaults request_queue_size to 5. Every connection beyond five
    waiting to be accepted overflows the kernel accept queue, and the client sees
    "connection reset by peer" — which is exactly what workers reported while claiming
    (this box showed 18,514 ListenOverflows). Workers poll on their own cadence and a
    claim burst is normal, so the queue must absorb it.
    """
    request_queue_size = int(os.environ.get("ANIMICA_ENA_LISTEN_BACKLOG", "512") or 512)
    daemon_threads = True
    allow_reuse_address = True


def serve(facade, host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = _Coordinator((host, port), _make_handler(facade))
    print(f"[ena] serving on http://{host}:{port} "
          f"(listen backlog {_Coordinator.request_queue_size})")
    _start_pool_sweeper(facade)
    _start_emission(facade)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        httpd.server_close()
