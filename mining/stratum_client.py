from __future__ import annotations

import asyncio
import json
import logging
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
    ) -> None:
        assert framing in ("lines", "lenpref")
        self.host = host
        self.port = int(port)
        self.agent = agent
        self.framing = framing
        self.loop = loop or asyncio.get_event_loop()

        self.reader: asyncio.StreamReader
        self.writer: asyncio.StreamWriter

        self._id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = False

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
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self._rx_task = self.loop.create_task(self._rx_loop())
        log.info(
            f"[client] connected to {self.host}:{self.port} framing={self.framing}"
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
        # Reject any pending futures
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(RuntimeError("connection closed"))
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

    async def _call(self, method: str, params: JSON) -> JSON:
        req_id = self._next_id()
        obj = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        fut: asyncio.Future = self.loop.create_future()
        self._pending[req_id] = fut
        await self._send_obj(obj)
        return await fut

    # ------------- protocol -------------

    async def subscribe(self) -> SubscribeReply:
        req = req_subscribe(
            agent=self.agent, features={"framing": self.framing}
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
        return self.session

    async def authorize(self, worker: str, address: str) -> bool:
        res = await self._call(
            str(Method.AUTHORIZE.value), {"worker": worker, "address": address}
        )
        ok = bool(res.get("result", {}).get("authorized", False))
        log.info(f"[client] authorize worker={worker} address={address} ok={ok}")
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
        result = res.get("result") or {}
        ok = result.get("accepted", False)
        is_block = result.get("isBlock", False)
        reason = result.get("reason")
        log.info(
            f"[client] submit job={job_id} ok={ok} is_block={is_block} reason={reason}"
        )
        return result

    # ------------- RX loop -------------

    async def _rx_loop(self) -> None:
        buf = bytearray()
        try:
            while True:
                chunk = await self.reader.read(65536)
                if not chunk:
                    raise ConnectionResetError("eof")

                # Decode frames
                objs: List[JSON]
                if self.framing == "lenpref":
                    objs = list(decode_lenpref(bytearray(chunk)))
                else:
                    buf.extend(chunk)
                    objs = list(decode_lines(buf))

                for obj in objs:
                    await self._handle_incoming(obj)
        except asyncio.CancelledError:  # pragma: no cover
            return
        except Exception as e:
            log.warning(f"[client] rx loop error: {e}")
        finally:
            await self.close()

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

        else:
            log.debug(f"[client] unhandled message: {obj}")

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
