from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .stratum_protocol import (Method, decode_lenpref, decode_lines,
                               encode_lenpref, encode_lines, req_submit,
                               req_subscribe)

try:
    from core.logging import get_logger  # type: ignore
except Exception:  # pragma: no cover

    def get_logger(name: str) -> logging.Logger:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )
        return logging.getLogger(name)


log = get_logger("mining.stratum_client")
JSON = Dict[str, Any]


@dataclass
class SubscribeReply:
    session_id: str
    extranonce1: str
    extranonce2_size: int
    framing: str


class StratumClient:
    """
    Minimal asyncio Stratum client for Animica miners (tests & demos).

    Usage:
        client = StratumClient("127.0.0.1", 23454, agent="demo/0.1", framing="lines")
        await client.connect()
        await client.subscribe()
        await client.authorize(worker="rig1", address="anim1...")
        await client.wait_forever()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 23454,
        agent: str = "animica-stratum-client/0.1",
        framing: str = "lines",  # "lines" | "lenpref"
        loop: Optional[asyncio.AbstractEventLoop] = None,
        tls: bool = False,
        tls_verify: bool = True,
    ) -> None:
        assert framing in ("lines", "lenpref")
        self.host = host
        self.port = int(port)
        self.agent = agent
        self.framing = framing
        self.loop = loop or asyncio.get_event_loop()
        self.tls = bool(tls)
        self.tls_verify = bool(tls_verify)

        self.reader: asyncio.StreamReader
        self.writer: asyncio.StreamWriter

        self._id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._rx_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        # Seconds of *send* inactivity before pushing an application-level
        # `keepalived` frame upstream. TCP keepalive probes are empty ACKs
        # that NAT middleboxes (WinNAT under WSL2, CGNAT, consumer routers)
        # often ignore — they time sessions on upstream *payload*, and a
        # miner only sends payload when it finds a share. Observed in the
        # wild as connections reset like clockwork at ~20 min while the
        # job stream is flowing fine. 0 disables.
        self._keepalive_secs = float(os.environ.get("ANIMICA_STRATUM_KEEPALIVE", "60") or 0)
        self._last_tx_at: float = 0.0
        self._closed = False
        # Stats used to make EOF diagnostics actionable: was the server
        # disconnecting us silently after subscribe (protocol mismatch)
        # or did it actually answer something first?
        self._connect_started_at: float = 0.0
        self._first_byte_at: float = 0.0
        self._frames_received: int = 0
        self._bytes_received: int = 0

        # Session state
        self.session: Optional[SubscribeReply] = None
        self.share_target: Optional[float] = None
        self.theta_micro: Optional[int] = None
        self.last_job: Optional[JSON] = None

        # Callbacks (set by user)
        self.on_notify: Optional[Callable[[JSON], Awaitable[None]]] = None
        self.on_set_difficulty: Optional[Callable[[float, int], Awaitable[None]]] = None

    # ------------- transport -------------

    async def connect(self) -> None:
        self._connect_started_at = time.monotonic()
        ssl_ctx = None
        if self.tls:
            import ssl as _ssl
            ssl_ctx = _ssl.create_default_context()
            if not self.tls_verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl.CERT_NONE
        self.reader, self.writer = await asyncio.open_connection(
            self.host, self.port, ssl=ssl_ctx
        )
        # Aggressive TCP keepalive on the client socket. Without this, Windows
        # clients on long-idle Stratum sessions surface
        # "Semaphore timeout period has expired" (WSAETIMEDOUT) when NAT or
        # firewall state expires between notifies. 30s idle → first probe,
        # 15s between probes, give up after 5 misses.
        try:
            import socket as _sock
            raw = self.writer.get_extra_info("socket")
            if raw is not None:
                raw.setsockopt(_sock.SOL_SOCKET, _sock.SO_KEEPALIVE, 1)
                for opt_name, opt_val in (
                    ("TCP_KEEPIDLE", 30),
                    ("TCP_KEEPINTVL", 15),
                    ("TCP_KEEPCNT", 5),
                ):
                    opt = getattr(_sock, opt_name, None)
                    if opt is not None:
                        try:
                            raw.setsockopt(_sock.IPPROTO_TCP, opt, opt_val)
                        except Exception:
                            pass
        except Exception:
            pass
        self._rx_task = self.loop.create_task(self._rx_loop())
        self._last_tx_at = time.monotonic()
        if self._keepalive_secs > 0:
            self._keepalive_task = self.loop.create_task(self._keepalive_loop())
        log.info(
            f"[client] connected to {self.host}:{self.port} framing={self.framing}"
            f"{' tls' if self.tls else ''}"
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass
        if self._rx_task:
            self._rx_task.cancel()
        if self._keepalive_task:
            self._keepalive_task.cancel()
        # Reject any pending futures with the same hint we logged from the
        # rx loop, so callers (CLI) see *why* the connection closed instead
        # of a generic "connection closed".
        hint = self._eof_hint()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(RuntimeError(f"connection closed: {hint}"))
        self._pending.clear()
        log.info("[client] closed")

    # ------------- JSON-RPC helpers -------------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _send_obj(self, obj: JSON) -> None:
        if self.framing == "lenpref":
            data = encode_lenpref(obj)
        else:
            data = encode_lines(obj)
        self.writer.write(data)
        await self.writer.drain()
        self._last_tx_at = time.monotonic()

    async def _keepalive_loop(self) -> None:
        """Push a `keepalived` request after `_keepalive_secs` of send
        inactivity, mirroring xmrig's --keepalive. The reply (or an error
        from pools that don't know the method) is consumed and discarded —
        the only goal is upstream payload bytes that keep NAT/firewall
        session tracking alive between rare share submissions."""
        check_every = max(min(self._keepalive_secs / 4.0, 15.0), 1.0)
        try:
            while not self._closed:
                await asyncio.sleep(check_every)
                if self._closed:
                    return
                if time.monotonic() - self._last_tx_at < self._keepalive_secs:
                    continue
                req_id = self._next_id()
                fut: asyncio.Future = self.loop.create_future()
                self._pending[req_id] = fut
                try:
                    await self._send_obj(
                        {"jsonrpc": "2.0", "id": req_id, "method": "keepalived", "params": {}}
                    )
                    await asyncio.wait_for(fut, timeout=10.0)
                    log.debug("[client] keepalive acked")
                except asyncio.TimeoutError:
                    # Old pools may simply not answer; the bytes still went out,
                    # which is all the NAT needed.
                    log.debug("[client] keepalive sent (no reply)")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # Send failure means the connection is dead; the rx loop
                    # surfaces that with its own diagnostics.
                    log.debug(f"[client] keepalive send failed: {e}")
                    return
                finally:
                    self._pending.pop(req_id, None)
        except asyncio.CancelledError:  # pragma: no cover
            return

    async def _call(self, method: str, params: JSON) -> JSON:
        req_id = self._next_id()
        obj = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        fut: asyncio.Future = self.loop.create_future()
        self._pending[req_id] = fut
        await self._send_obj(obj)
        return await fut

    # ------------- protocol -------------

    async def subscribe(self) -> SubscribeReply:
        # AICF model-serving is ON by default. Every miner advertises the
        # "standard" tier so the pool's AICF dispatcher can route inference
        # jobs here, and the runtime falls back to a labeled stub on miners
        # that don't have transformers+torch installed — so the protocol
        # round-trip always completes.
        #
        # Override knobs (all optional):
        #   ANIMICA_AICF_TIERS="standard,premium"  — change advertised tiers
        #   ANIMICA_AICF_MODEL="Qwen/..."          — pin a specific HF repo
        #   ANIMICA_AICF_GPU=true                  — hardware hint
        #   ANIMICA_AICF_VRAM_GB=24                — hardware hint
        #   ANIMICA_AICF_DISABLE=1                 — opt out (pure-PoW only)
        import os as _os
        features: dict = {"framing": self.framing}
        if not _os.environ.get("ANIMICA_AICF_DISABLE", "").strip():
            aicf_tiers_env = _os.environ.get("ANIMICA_AICF_TIERS", "").strip()
            tiers_forced = bool(aicf_tiers_env)
            if aicf_tiers_env:
                tiers = [t.strip() for t in aicf_tiers_env.split(",") if t.strip()]
            else:
                # Sensible default: advertise standard tier so chat jobs
                # route to this miner out of the box. Heavier tiers stay
                # opt-in via the env var. Suppressed below when the ML
                # stack is missing so users don't pay for stub replies.
                tiers = ["standard"]
            # Detect whether the ML backend is actually loadable. If it
            # isn't, the engine will fall back to a labeled stub for every
            # claimed job — and the user still pays. Refuse to advertise
            # tiers in that case so the pool routes elsewhere. Operators
            # can override with ANIMICA_AICF_TIERS to keep the legacy
            # behavior (e.g. to take jobs and serve stubs intentionally).
            try:
                import importlib as _il
                _il.import_module("transformers")
                _il.import_module("torch")
                ml_backend = "transformers"
            except Exception as _ml_exc:
                ml_backend = "stub"
                if tiers and not tiers_forced:
                    log.warning(
                        "[client] AICF tiers suppressed: ML stack not "
                        "importable (%s). Run `animica miner setup` to "
                        "install torch/transformers, or set "
                        "ANIMICA_AICF_TIERS to force-advertise anyway.",
                        _ml_exc,
                    )
                    tiers = []
                elif tiers and tiers_forced:
                    log.warning(
                        "[client] AICF tiers %s advertised despite "
                        "missing ML stack (%s) — every claimed job will "
                        "return a stub. Users will pay for empty replies.",
                        tiers, _ml_exc,
                    )
            if tiers:
                hardware: dict = {}
                for key, env in (
                    ("gpu", "ANIMICA_AICF_GPU"),
                    ("vram_gb", "ANIMICA_AICF_VRAM_GB"),
                    ("model", "ANIMICA_AICF_MODEL"),
                ):
                    val = _os.environ.get(env, "").strip()
                    if val:
                        hardware[key] = val
                hardware.setdefault("backend", ml_backend)
                features["aicf"] = {"tiers": tiers, "hardware": hardware}
        req = req_subscribe(
            agent=self.agent, features=features
        )  # uses Method.SUBSCRIBE
        req["id"] = self._next_id()
        fut: asyncio.Future = self.loop.create_future()
        self._pending[req["id"]] = fut
        await self._send_obj(req)
        res = await fut
        result = res.get("result") or {}
        self.session = SubscribeReply(
            session_id=result["sessionId"],
            extranonce1=result["extranonce1"],
            extranonce2_size=int(result["extranonce2Size"]),
            framing=result.get("framing", self.framing),
        )
        # Server may force framing; adopt it
        self.framing = self.session.framing
        log.info(
            f"[client] subscribed session={self.session.session_id} "
            f"ex1={self.session.extranonce1} ex2sz={self.session.extranonce2_size} framing={self.framing}"
        )
        # AICF pre-load: if we advertised AICF tiers, kick off model load
        # in the background NOW so the first inference job doesn't have to
        # wait minutes for a multi-GB HF snapshot to download.
        aicf_feats = features.get("aicf") if isinstance(features, dict) else None
        if isinstance(aicf_feats, dict) and aicf_feats.get("tiers"):
            asyncio.create_task(self._preload_aicf_engine(aicf_feats))
        return self.session

    async def _preload_aicf_engine(self, aicf_features: dict) -> None:
        """Trigger model download + load in the background after subscribe.

        Without this, the first ``mining.aicf.notify`` from the pool kicks
        off a multi-minute model download while a chat user waits at the
        prompt. Doing it once at miner-start hides the latency behind PoW
        share submission. Errors here are non-fatal — the labeled-stub
        fallback still keeps the protocol round-trip working.
        """
        log.info("[client] AICF: pre-loading inference engine in background…")
        try:
            from animica.stratum_pool.aicf_inference import get_engine
        except Exception as exc:
            log.warning(f"[client] aicf preload: engine unavailable: {exc}")
            return
        # Resolve the model the engine will use — match the same logic as
        # the runtime generate() path so we warm the right cache.
        import os as _os
        tier = "standard"
        tiers = aicf_features.get("tiers") or []
        if isinstance(tiers, list) and tiers:
            tier = str(tiers[0])
        # Run the model load on a thread so the asyncio event loop keeps
        # serving PoW notifies and share submits while the download runs.
        # IMPORTANT: cap warmup output at 1 token. Without the cap, the
        # default 64 tokens hold the engine Lock for ~60s on CPU and the
        # FIRST real chat job blocks behind preload's generate() for the
        # full duration — which then blocks behind ITS own generation too,
        # so the user sees a 300s chat timeout even though the model
        # loaded fine.
        try:
            engine = get_engine()
            def _do_warmup() -> None:
                try:
                    res = engine.generate(
                        {"prompt": "hi", "max_output_tokens": 1},
                        tier=tier,
                    )
                    log.info(
                        f"[client] AICF: engine ready backend={res.used_backend} "
                        f"model={res.used_model}"
                    )
                except Exception as exc:
                    log.warning(f"[client] aicf preload generate failed: {exc}")
            await asyncio.to_thread(_do_warmup)
        except Exception as exc:
            log.warning(f"[client] aicf preload failed: {exc}")

    async def authorize(self, worker: str, address: str) -> bool:
        res = await self._call(
            str(Method.AUTHORIZE.value), {"worker": worker, "address": address}
        )
        ok = bool(res.get("result", {}).get("authorized", False))
        log.info(f"[client] authorize worker={worker} address={address} ok={ok}")
        # Stash for mining.aicf.submit, which needs the worker name in
        # its params so the pool can credit the right miner.
        if ok:
            self.worker = worker
            self.address = address
        return ok

    async def get_version(self) -> JSON:
        res = await self._call(str(Method.GET_VERSION.value), {})
        return res.get("result") or {}

    async def submit_share(
        self,
        job_id: str,
        hashshare: JSON,
        proofs: Optional[List[JSON]] = None,
        txs: Optional[List[str]] = None,
        extranonce2: str = "0x00",
    ) -> JSON:
        """
        Submit a share for a given job. `hashshare` is the HashShare envelope/body.
        """
        params: JSON = {
            "worker": self.session.session_id if self.session else "",
            "jobId": job_id,
            "extranonce2": extranonce2,
            "hashshare": hashshare,
        }
        if proofs:
            params["proofs"] = proofs
        if txs:
            params["txs"] = txs

        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": Method.SUBMIT.value,
            "params": params,
        }
        fut: asyncio.Future = self.loop.create_future()
        self._pending[req["id"]] = fut
        await self._send_obj(req)
        res = await fut
        if res.get("error"):
            return res
        result = res.get("result") or {}
        ok = result.get("accepted", False)
        is_block = result.get("isBlock", False)
        reason = result.get("reason")
        log.info(
            f"[client] submit job={job_id} ok={ok} is_block={is_block} reason={reason}"
        )
        flattened = dict(result)
        flattened["result"] = result
        return flattened

    # ------------- RX loop -------------

    async def _rx_loop(self) -> None:
        buf = bytearray()
        try:
            while True:
                chunk = await self.reader.read(65536)
                if not chunk:
                    raise ConnectionResetError("eof")
                if self._first_byte_at == 0.0:
                    self._first_byte_at = time.monotonic()
                self._bytes_received += len(chunk)

                # Decode frames
                objs: List[JSON]
                if self.framing == "lenpref":
                    objs = list(decode_lenpref(bytearray(chunk)))
                else:
                    buf.extend(chunk)
                    objs = list(decode_lines(buf))

                for obj in objs:
                    self._frames_received += 1
                    await self._handle_incoming(obj)
        except asyncio.CancelledError:  # pragma: no cover
            return
        except Exception as e:
            log.warning(f"[client] rx loop error: {e} ({self._eof_hint()})")
        finally:
            await self.close()

    def _eof_hint(self) -> str:
        """Build a human hint about what likely happened when the rx loop exits.

        EOF before any bytes from the server is almost always a
        protocol/transport mismatch — the pool isn't an Animica stratum
        server, or it expects TLS, or it filtered us out before
        responding. Surfacing this directly in the log avoids leaving
        operators to chase a 3-letter error.
        """
        elapsed_ms = int((time.monotonic() - self._connect_started_at) * 1000) if self._connect_started_at else -1
        if self._bytes_received == 0:
            tls_hint = " try stratum+ssl://" if not self.tls else ""
            return (
                f"server closed connection after {elapsed_ms}ms with no response; "
                f"pool likely isn't an Animica stratum endpoint{tls_hint} "
                f"or rejected the {self.framing} framing"
            )
        if self._frames_received == 0:
            return (
                f"server sent {self._bytes_received}B but no complete frame "
                f"({self.framing} framing) — server may speak a different stratum dialect"
            )
        return (
            f"server closed after {self._frames_received} frame(s) and "
            f"{self._bytes_received}B"
        )

    async def _handle_incoming(self, obj: JSON) -> None:
        # Response
        if "id" in obj and (
            obj.get("result") is not None or obj.get("error") is not None
        ):
            rid = obj["id"]
            fut = self._pending.pop(rid, None)
            if fut and not fut.done():
                fut.set_result(obj)
            return

        # Notifications
        method = obj.get("method")
        params = obj.get("params") or {}

        if method == str(Method.SET_DIFFICULTY.value):
            self.share_target = float(params["shareTarget"])
            self.theta_micro = int(params["thetaMicro"])
            log.info(
                f"[client] difficulty shareTarget={self.share_target} thetaMicro={self.theta_micro}"
            )
            if self.on_set_difficulty:
                await self.on_set_difficulty(self.share_target, self.theta_micro)

        elif method == str(Method.NOTIFY.value):
            self.last_job = params
            jid = params.get("jobId")
            log.info(
                f"[client] notify job={jid} clean={params.get('cleanJobs')} shareTarget={params.get('shareTarget')}"
            )
            if self.on_notify:
                await self.on_notify(params)

        elif method == "mining.aicf.notify":
            # Inference job dispatched by the pool. Run in a background
            # task so the reader loop isn't blocked by model generation.
            asyncio.create_task(self._handle_aicf_notify(params))

        else:
            log.debug(f"[client] unhandled message: {obj}")

    async def _handle_aicf_notify(self, params: Dict[str, Any]) -> None:
        """Run inference on the JobSpec and reply with mining.aicf.submit.

        Routes through the shared animica.stratum_pool.aicf_inference
        engine — same code path as the reference miner so behavior is
        consistent regardless of which Stratum client a miner uses.

        Deduplication: the pool re-dispatches the same job every time the
        node-side lease expires (default 60s). Without dedup the miner
        runs inference N times for the same job while the user waits, and
        the inference Lock serializes a real new chat behind a queue of
        stale duplicates. We keep two sets:

          - ``_aicf_inflight_jobs``  : currently being processed
          - ``_aicf_completed_jobs`` : already submitted (bounded LRU)
        """
        job_id = str(params.get("jobId") or "")
        spec = params.get("spec") or {}
        tier = str(params.get("tier") or "standard")
        if not job_id or not isinstance(spec, dict):
            log.warning(f"[client] bad mining.aicf.notify: {params!r}")
            return
        # Lazy-init dedup state. Attribute access guards keep the existing
        # tests and demos that drive _handle_aicf_notify directly from
        # tripping over a missing attribute.
        if not hasattr(self, "_aicf_inflight_jobs"):
            self._aicf_inflight_jobs: set = set()
        if not hasattr(self, "_aicf_completed_jobs"):
            self._aicf_completed_jobs: list = []
        if job_id in self._aicf_inflight_jobs:
            log.info(f"[client] aicf dedup: already processing job={job_id}, skipping")
            return
        if job_id in self._aicf_completed_jobs:
            # Re-send the cached completion so the pool re-clears its
            # in-flight slot even if the original submit was lost.
            log.info(f"[client] aicf dedup: re-acking completed job={job_id}")
            try:
                await self._send_obj({
                    "jsonrpc": "2.0", "id": None,
                    "method": "mining.aicf.submit",
                    "params": {
                        "worker": self.worker or "",
                        "jobId": job_id,
                        "text": "",  # node already has the real result
                        "latencyMs": 0,
                        "attestation": {"backend": "dedup_reack",
                                        "model": ""},
                    },
                })
            except Exception:
                pass
            return
        self._aicf_inflight_jobs.add(job_id)
        try:
            from animica.stratum_pool.aicf_inference import get_engine
        except Exception as exc:
            log.warning(f"[client] aicf inference engine unavailable: {exc}")
            self._aicf_inflight_jobs.discard(job_id)
            return
        engine = get_engine()
        try:
            result = await asyncio.to_thread(engine.generate, spec, tier=tier)
        except Exception as exc:
            log.warning(f"[client] aicf inference failed job={job_id}: {exc}")
            self._aicf_inflight_jobs.discard(job_id)
            return
        log.info(
            f"[client] aicf job done id={job_id} tier={tier} "
            f"backend={result.used_backend} latency={result.latency_ms}ms "
            f"model={result.used_model}"
        )
        try:
            req = {
                "jsonrpc": "2.0",
                "id": None,
                "method": "mining.aicf.submit",
                "params": {
                    "worker": self.worker or "",
                    "jobId": job_id,
                    "text": result.text,
                    "latencyMs": int(result.latency_ms),
                    "attestation": {
                        "backend": result.used_backend,
                        "model": result.used_model,
                    },
                },
            }
            await self._send_obj(req)
        except Exception as exc:
            log.warning(f"[client] failed to send mining.aicf.submit job={job_id}: {exc}")
        finally:
            # Mark complete (regardless of submit outcome — the pool will
            # re-ack via dedup if it's still waiting) and trim the cache.
            self._aicf_inflight_jobs.discard(job_id)
            self._aicf_completed_jobs.append(job_id)
            if len(self._aicf_completed_jobs) > 256:
                self._aicf_completed_jobs[:] = self._aicf_completed_jobs[-256:]

    # ------------- demo helpers -------------

    async def wait_forever(self) -> None:
        while True:
            await asyncio.sleep(3600)


# ------------------------------- CLI demo ----------------------------------


async def _demo_main(argv: List[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Animica Stratum Client (demo)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=23454)
    p.add_argument("--framing", choices=["lines", "lenpref"], default="lines")
    p.add_argument("--worker", default="rig1")
    p.add_argument("--address", default="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3j5kq")
    p.add_argument(
        "--auto-submit",
        action="store_true",
        help="Submit a dummy share on each notify (for server-path tests)",
    )
    args = p.parse_args(argv)

    client = StratumClient(args.host, args.port, framing=args.framing)

    # Example notify handler
    async def on_notify(job: JSON) -> None:
        if not args.auto_submit:
            return
        # Build a trivial/fake HashShare envelope (server should of course reject unless in dev mode)
        fake_hashshare = {
            "nonce": "0x01",
            "body": {
                "headerHash": job["header"].get("parentHash", "0x" + "00" * 32),
                "uDraw": "0x" + "11" * 32,
                "mix": "0x" + "22" * 32,
            },
        }
        await client.submit_share(job["jobId"], fake_hashshare)

    client.on_notify = on_notify

    # Graceful stop
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    await client.connect()
    await client.subscribe()
    await client.authorize(args.worker, args.address)

    ver = await client.get_version()
    log.info(f"[client] server version: {ver}")

    await stop.wait()
    await client.close()
    return 0


def main() -> None:
    try:
        rc = asyncio.run(_demo_main(sys.argv[1:]))
        raise SystemExit(rc)
    except KeyboardInterrupt:  # pragma: no cover
        try:
            asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.05))
        except Exception:
            pass
        raise SystemExit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
