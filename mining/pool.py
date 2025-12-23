from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .rpc_adapter import RpcTemplateProvider
from .share_submitter import ShareSubmitter, SubmitterConfig
from .stratum_server import StratumJob, StratumServer

log = logging.getLogger("mining.pool")


@dataclass
class PoolConfig:
    rpc_url: str
    listen_host: str = "0.0.0.0"
    listen_port: int = 5333
    share_target: float = 0.01
    proof_type: str = "sha256d"
    p2p_port: int = 30333
    no_p2p: bool = False
    template_interval_sec: float = 1.0


class StratumPool:
    def __init__(self, cfg: PoolConfig) -> None:
        self.cfg = cfg
        self._server = StratumServer(host=cfg.listen_host, port=cfg.listen_port)
        self._tpl_provider = RpcTemplateProvider(cfg.rpc_url, cfg.proof_type)
        self._submitter = ShareSubmitter(SubmitterConfig(rpc_url=cfg.rpc_url))
        self._job_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._server.start()
        await self._server.set_global_difficulty(
            self.cfg.share_target, theta_micro=None
        )
        self._server.set_submit_hook(self._on_submit)
        self._job_task = asyncio.create_task(self._job_loop())
        self._check_p2p_port()

    async def stop(self) -> None:
        if self._job_task:
            self._job_task.cancel()
            await asyncio.gather(self._job_task, return_exceptions=True)
        await self._server.stop()

    async def _job_loop(self) -> None:
        while True:
            tpl = await self._tpl_provider.current_template()
            if tpl:
                job = StratumJob(
                    job_id=str(tpl.get("jobId")),
                    header=tpl.get("header") or {},
                    share_target=self.cfg.share_target,
                    theta_micro=int(tpl.get("thetaMicro") or 0),
                    hints=tpl.get("hints") or {},
                    target=tpl.get("target"),
                    sign_bytes=tpl.get("signBytes"),
                    height=int(tpl.get("height") or 0),
                    parent_hash=tpl.get("parentHash"),
                    parent_height=int(tpl.get("parentHeight") or 0),
                    chain_id=int(tpl.get("chainId") or 0),
                    expires_at=time.time() + 20,
                    proof_type=tpl.get("proofType"),
                )
                await self._server.publish_job(job)
            await asyncio.sleep(self.cfg.template_interval_sec)

    async def _on_submit(
        self,
        _session: Any,
        job: StratumJob,
        params: Dict[str, Any],
        ok: bool,
        _reason: Optional[str],
        is_block: bool,
        _tx_count: int,
    ) -> None:
        if not ok or not is_block:
            return
        nonce_hex = params.get("hashshare", {}).get("nonce") or params.get("nonce")
        try:
            nonce = int(nonce_hex, 16) if isinstance(nonce_hex, str) else int(nonce_hex)
        except Exception:
            return
        header = dict(job.header)
        header["nonce"] = hex(nonce)
        candidate = {"header": header, "txs": [], "proofs": []}
        try:
            self._submitter.submit_block_once(candidate)
        except Exception as e:
            log.warning("candidate block submit failed: %s", e)

    def _check_p2p_port(self) -> None:
        if self.cfg.no_p2p:
            log.info("P2P disabled by configuration.")
            return
        port = self.cfg.p2p_port
        if not _can_bind(port):
            alt = _find_free_port(start=port + 1)
            log.warning(
                "P2P port %s is in use; using RPC-only pool mode (suggest --no-p2p).",
                port,
            )
            if alt is not None:
                log.info("Suggested available P2P port: %s", alt)
            return
        log.info("P2P port %s available; pool running in RPC-only mode.", port)


def _can_bind(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("0.0.0.0", int(port)))
        return True
    except OSError:
        return False


def _find_free_port(start: int) -> Optional[int]:
    for port in range(start, start + 10):
        if _can_bind(port):
            return port
    return None
