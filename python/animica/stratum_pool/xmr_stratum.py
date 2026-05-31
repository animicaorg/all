"""Cryptonote pool stratum server — separate TCP port for stock xmrig.

Why a separate port + protocol?

XMRig speaks cryptonote pool protocol (NOT bitcoin-style Stratum-v1)
when mining Monero. Forcing a single xmrig binary to speak Animica's
hashshare protocol AND cryptonote on the same connection would mean
patching every templated worker in xmrig, which is fragile.

Cleanest model:
- Port 3333 (existing): Animica Stratum-v1 hashshare protocol. The
  Animica xmrig fork connects here for SHA3 work.
- Port 3334 (this module): cryptonote pool protocol. STOCK xmrig
  connects here for RandomX work.

Miners run two xmrig processes (one per algo, split threads to taste).
Pool tracks both share streams and uses the miner's payout address
(the Animica `anim1…` bech32) to attribute XMR shares.

Wire format (one JSON object per line, \n-delimited):

  → {"id":1,"jsonrpc":"2.0","method":"login","params":{...}}
  ← {"id":1,"result":{"id":"<sid>","job":{...},"status":"OK"}}
  ← {"jsonrpc":"2.0","method":"job","params":{"job_id":...,"blob":...}}
  → {"id":2,"jsonrpc":"2.0","method":"submit","params":{...}}
  ← {"id":2,"result":{"status":"OK"}}
  → {"id":3,"jsonrpc":"2.0","method":"keepalived","params":{"id":"<sid>"}}
  ← {"id":3,"result":{"status":"KEEPALIVED"}}

This module assumes xmr.py provides the upstream job manager and share
validator; we're just the wire-format adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from animica.stratum_pool.xmr import (
    XmrJob,
    XmrJobManager,
    XmrShareLedger,
    XmrShareValidator,
)


_LOG = logging.getLogger("animica.stratum_pool.xmr_stratum")


@dataclass
class XmrSession:
    session_id: str
    writer: asyncio.StreamWriter
    miner_address: str = ""
    worker: str = ""
    agent: str = ""
    pool_difficulty: int = 10_000
    connected_at: float = field(default_factory=time.time)
    last_share_at: Optional[float] = None
    shares_accepted: int = 0
    shares_rejected: int = 0
    last_job_id: Optional[str] = None
    closed: bool = False


# Animica address sanity check. We accept the bech32m format the rest
# of the chain uses; the pool's payout cron will map the XMR shares
# back to this address to send the miner their cut.
_ANIM_ADDR_RE = re.compile(r"^anim1[0-9a-z]{30,}$")


class XmrStratumServer:
    """Cryptonote pool protocol server, listening on its own port.

    Subscribes to the shared XmrJobManager — whenever a new template
    arrives, broadcasts to every connected session.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        job_manager: XmrJobManager,
        validator: XmrShareValidator,
        ledger: XmrShareLedger,
    ):
        self._host = host
        self._port = port
        self._jm = job_manager
        self._validator = validator
        self._ledger = ledger
        self._sessions: Dict[str, XmrSession] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._jm.subscribe(self._broadcast_job)
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        _LOG.info("XMR cryptonote stratum listening on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        # Close every session writer
        for sess in list(self._sessions.values()):
            try:
                sess.writer.close()
            except Exception:
                pass
        self._sessions.clear()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session_id = secrets.token_hex(8)
        sess = XmrSession(session_id=session_id, writer=writer)
        async with self._lock:
            self._sessions[session_id] = sess
        peer = writer.get_extra_info("peername")
        _LOG.info("xmr session %s connected from %s", session_id, peer)
        try:
            while not sess.closed:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue
                await self._dispatch(sess, msg)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as exc:
            _LOG.warning("xmr session %s error: %s", session_id, exc, exc_info=False)
        finally:
            sess.closed = True
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            async with self._lock:
                self._sessions.pop(session_id, None)
            _LOG.info(
                "xmr session %s closed (accepted=%d rejected=%d)",
                session_id, sess.shares_accepted, sess.shares_rejected,
            )

    async def _dispatch(self, sess: XmrSession, msg: Dict[str, Any]) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "login":
            await self._handle_login(sess, msg_id, params)
        elif method == "submit":
            await self._handle_submit(sess, msg_id, params)
        elif method == "keepalived":
            await self._send(sess, {"id": msg_id, "jsonrpc": "2.0",
                                    "result": {"status": "KEEPALIVED"}})
        else:
            await self._send(sess, {
                "id": msg_id, "jsonrpc": "2.0",
                "error": {"code": -1, "message": f"unknown method: {method}"},
            })

    async def _handle_login(
        self, sess: XmrSession, msg_id: Any, params: Dict[str, Any]
    ) -> None:
        login = str(params.get("login") or "").strip()
        agent = str(params.get("agent") or "")
        # login can be "anim1addr" or "anim1addr.worker_tag"
        if "." in login:
            address, worker = login.split(".", 1)
        else:
            address, worker = login, ""
        if not _ANIM_ADDR_RE.match(address):
            await self._send(sess, {
                "id": msg_id, "jsonrpc": "2.0",
                "error": {
                    "code": -1,
                    "message": "login must be a bech32 anim1… address; XMR shares "
                               "are paid out to a Monero address you register "
                               "separately via the pool portal",
                },
            })
            sess.closed = True
            return
        sess.miner_address = address
        sess.worker = worker
        sess.agent = agent
        job = self._jm.current_job
        job_payload = self._serialize_job_for_session(job, sess) if job else None
        result = {
            "id": sess.session_id,
            "status": "OK",
        }
        if job_payload:
            result["job"] = job_payload
            sess.last_job_id = job_payload.get("job_id")
        await self._send(sess, {"id": msg_id, "jsonrpc": "2.0", "result": result})
        _LOG.info("xmr login: session=%s addr=%s worker=%s agent=%s",
                  sess.session_id, address[:24] + "...", worker, agent[:32])

    async def _handle_submit(
        self, sess: XmrSession, msg_id: Any, params: Dict[str, Any]
    ) -> None:
        # Cryptonote field names: job_id, nonce, result
        job_id = str(params.get("job_id") or "")
        nonce = str(params.get("nonce") or "")
        result_hex = str(params.get("result") or "")
        ok, reason, share = await self._validator.validate(
            miner_id=sess.miner_address,
            job_id=job_id,
            nonce_hex=nonce,
            result_hex=result_hex,
        )
        if not ok or share is None:
            sess.shares_rejected += 1
            await self._send(sess, {
                "id": msg_id, "jsonrpc": "2.0",
                "error": {"code": -1, "message": reason},
            })
            return
        sess.shares_accepted += 1
        sess.last_share_at = time.time()
        await self._ledger.record(share)
        await self._send(sess, {
            "id": msg_id, "jsonrpc": "2.0",
            "result": {"status": "OK"},
        })
        if share.block_solved:
            _LOG.info(
                "🎉 XMR block found via cryptonote port: session=%s miner=%s",
                sess.session_id, sess.miner_address[:24] + "...",
            )

    async def _broadcast_job(self, job: XmrJob) -> None:
        sessions = list(self._sessions.values())
        for sess in sessions:
            if sess.closed or not sess.miner_address:
                continue
            payload = self._serialize_job_for_session(job, sess)
            try:
                await self._send(sess, {
                    "jsonrpc": "2.0",
                    "method": "job",
                    "params": payload,
                })
                sess.last_job_id = payload["job_id"]
            except Exception as exc:
                _LOG.warning("xmr broadcast to %s failed: %s", sess.session_id, exc)
                sess.closed = True

    def _serialize_job_for_session(self, job: XmrJob, sess: XmrSession) -> Dict[str, Any]:
        # XMRig expects target as little-endian uint64 hex. We use the
        # session's effective pool_difficulty (vardiff to be added).
        diff = max(sess.pool_difficulty, 1)
        target_int = (1 << 256) // diff
        # XMRig's target is the high 64 bits of the 256-bit threshold,
        # serialised little-endian (compact form). For modest diffs the
        # 64-bit slice is fine.
        target_64 = (target_int >> 192) & 0xFFFFFFFFFFFFFFFF
        target_hex = target_64.to_bytes(8, "little").hex()
        return {
            "job_id": job.job_id,
            "blob": job.blockhashing_blob.hex(),
            "target": target_hex,
            "seed_hash": job.seed_hash.hex(),
            "height": job.height,
            "algo": "rx/0",
        }

    async def _send(self, sess: XmrSession, msg: Dict[str, Any]) -> None:
        try:
            data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
            sess.writer.write(data)
            await sess.writer.drain()
        except Exception as exc:
            _LOG.warning("xmr send to %s failed: %s", sess.session_id, exc)
            sess.closed = True

    def stats(self) -> Dict[str, Any]:
        return {
            "sessions": len(self._sessions),
            "miners": sorted(set(
                s.miner_address for s in self._sessions.values() if s.miner_address
            )),
        }
