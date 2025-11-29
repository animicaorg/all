"""Minimal Stratum v1 (SHA-256) debug client.

This tool helps sanity check the ASIC-facing Stratum listener without
needing physical hardware. It performs the standard subscribe/authorize
handshake and prints any notifications that arrive.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any, List


log = logging.getLogger("stratum_debug")


async def _read_json(reader: asyncio.StreamReader) -> Any:
    line = await reader.readline()
    if not line:
        raise ConnectionError("connection closed")
    return json.loads(line.decode())


async def run_client(host: str, port: int, worker: str, password: str) -> None:
    reader, writer = await asyncio.open_connection(host, port)
    peer = writer.get_extra_info("peername")
    log.info("connected to %s", peer)

    subscribe = {"id": 1, "method": "mining.subscribe", "params": [worker]}
    writer.write((json.dumps(subscribe) + "\n").encode())
    await writer.drain()

    sub_res = await _read_json(reader)
    log.info("subscribe result: %s", sub_res)

    authorize = {"id": 2, "method": "mining.authorize", "params": [worker, password]}
    writer.write((json.dumps(authorize) + "\n").encode())
    await writer.drain()
    auth_res = await _read_json(reader)
    log.info("authorize result: %s", auth_res)

    async def listener() -> None:
        while True:
            msg = await _read_json(reader)
            log.info("RX: %s", msg)

    listener_task = asyncio.create_task(listener())

    try:
        await listener_task
    except asyncio.CancelledError:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Stratum v1 debug client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--worker", default="debug.worker")
    parser.add_argument("--password", default="x")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run_client(args.host, args.port, args.worker, args.password))
    except KeyboardInterrupt:
        log.info("stopping")


if __name__ == "__main__":  # pragma: no cover
    main()
