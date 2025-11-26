"""
Ingest loop for the {{ project_slug }} Indexer Lite.

This module provides a compact, batteries-included block/tx indexer with two
output "sinks":

1) SQLite (default) — durable, queryable local database:
   - File location: <data_dir>/indexer.db (configurable)
   - Tables:
       blocks(number INTEGER PRIMARY KEY, hash TEXT UNIQUE, parent_hash TEXT,
              timestamp INTEGER, raw JSON)
       txs(hash TEXT PRIMARY KEY, block_number INTEGER, "from" TEXT, "to" TEXT,
           value TEXT, nonce INTEGER, input TEXT, raw JSON)
   - Idempotent writes via INSERT OR IGNORE.

2) JSONL — append-only files for lightweight pipelines:
   - <data_dir>/blocks.jsonl
   - <data_dir>/txs.jsonl

The ingest pipeline is designed to be:
- **Robust**: automatic retry/backoff happens in the underlying RPC client.
- **Efficient**: batched range pulls for historical backfill.
- **Tail-friendly**: when caught up with the head, it polls at a small interval
  (configurable) and continues indexing new blocks.

Configuration
-------------
We reuse IndexerConfig from `.config`. Only a few fields are *expected*; the
rest are optional and have sensible defaults if absent:

Required-ish (used by rpc):
- rpc_url: str
- (optional) ws_url: str | None
- http_timeout_s: float
- http_retries: int
- max_batch_size: int
- ws_backoff_initial_s: float
- ws_backoff_max_s: float
- log_level: str

Ingest-related (optional):
- start_block: int (default: 0)
- stop_block: int | None (default: None — unlimited)
- data_dir: str (default: ".")
- sink: Literal["sqlite","jsonl"] (default: "sqlite")
- index_full_txs: bool (default: True)
- tail_poll_interval_s: float (default: 2.0)

CLI
---
$ python -m indexer.ingest run          # Backfill from start_block, then tail forever
$ python -m indexer.ingest backfill --from 0 --to 10_000
$ python -m indexer.ingest tail         # Start at (last indexed + 1) and follow head
$ python -m indexer.ingest one-shot --at 12345

You can also combine environment-driven config via `.config.from_env()`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import pathlib
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union, cast

from .config import IndexerConfig, from_env
from .rpc import JsonRpcClient


Json = Mapping[str, Any]


# ------------------------------ utilities ---------------------------------- #

def _hex_to_int(h: Union[str, int]) -> int:
    if isinstance(h, int):
        return h
    if not isinstance(h, str):
        raise TypeError(f"expected hex-str or int, got {type(h)}")
    return int(h, 16)


def _ensure_dir(p: Union[str, pathlib.Path]) -> pathlib.Path:
    path = pathlib.Path(p).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------- sinks ------------------------------------- #

class Sink:
    async def aclose(self) -> None:  # uniform async close
        return None

    def record_block(self, block: Json) -> None:
        raise NotImplementedError

    def record_txs(self, block_number: int, txs: Iterable[Json]) -> None:
        raise NotImplementedError

    def last_indexed_block(self) -> Optional[int]:
        """
        Return the highest fully-indexed block number, if known.
        """
        return None


class SqliteSink(Sink):
    def __init__(self, db_path: Union[str, pathlib.Path], log: logging.Logger) -> None:
        self.log = log.getChild("sqlite")
        self.db_path = pathlib.Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocks (
                number      INTEGER PRIMARY KEY,
                hash        TEXT UNIQUE,
                parent_hash TEXT,
                timestamp   INTEGER,
                raw         TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS txs (
                hash          TEXT PRIMARY KEY,
                block_number  INTEGER,
                "from"        TEXT,
                "to"          TEXT,
                value         TEXT,
                nonce         INTEGER,
                input         TEXT,
                raw           TEXT
            )
            """
        )
        self.conn.commit()

    def record_block(self, block: Json) -> None:
        number = _hex_to_int(block["number"])
        bhash = cast(str, block.get("hash"))
        parent = cast(str, block.get("parentHash"))
        ts = _hex_to_int(block.get("timestamp", "0x0"))
        raw = json.dumps(block, separators=(",", ":"), sort_keys=True)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO blocks(number, hash, parent_hash, timestamp, raw)
            VALUES (?, ?, ?, ?, ?)
            """,
            (number, bhash, parent, ts, raw),
        )

    def record_txs(self, block_number: int, txs: Iterable[Json]) -> None:
        rows: List[Tuple[Any, ...]] = []
        for tx in txs:
            # When full_txs=False, "tx" may be a hash string. Normalize to dict.
            if isinstance(tx, str):
                rows.append((tx, block_number, None, None, None, None, None, json.dumps({"hash": tx})))
                continue

            t_hash = cast(str, tx.get("hash"))
            t_from = cast(Optional[str], tx.get("from"))
            t_to = cast(Optional[str], tx.get("to"))
            t_val = cast(Optional[str], tx.get("value"))
            t_nonce = _hex_to_int(tx["nonce"]) if "nonce" in tx and isinstance(tx["nonce"], str) else cast(Optional[int], tx.get("nonce"))  # noqa: E501
            t_input = cast(Optional[str], tx.get("input"))
            t_raw = json.dumps(tx, separators=(",", ":"), sort_keys=True)
            rows.append((t_hash, block_number, t_from, t_to, t_val, t_nonce, t_input, t_raw))

        if rows:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO txs(hash, block_number, "from", "to", value, nonce, input, raw)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def commit(self) -> None:
        self.conn.commit()

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.commit()
            self.conn.close()

    def last_indexed_block(self) -> Optional[int]:
        cur = self.conn.cursor()
        row = cur.execute("SELECT MAX(number) FROM blocks").fetchone()
        if not row:
            return None
        return cast(Optional[int], row[0])


class JsonlSink(Sink):
    def __init__(self, dir_path: Union[str, pathlib.Path], log: logging.Logger) -> None:
        self.log = log.getChild("jsonl")
        self.dir = _ensure_dir(dir_path)
        self.blocks_path = self.dir / "blocks.jsonl"
        self.txs_path = self.dir / "txs.jsonl"
        self._last_block: Optional[int] = self._load_last_block_number()
        # Open for append
        self._blocks = open(self.blocks_path, "a", encoding="utf-8")
        self._txs = open(self.txs_path, "a", encoding="utf-8")

    def record_block(self, block: Json) -> None:
        self._blocks.write(json.dumps(block, separators=(",", ":"), sort_keys=True) + "\n")
        try:
            self._last_block = _hex_to_int(block.get("number", 0))
        except Exception:
            # Do not block ingest on bad metadata; resume logic will fall back to config
            pass

    def record_txs(self, block_number: int, txs: Iterable[Json]) -> None:
        for tx in txs:
            if isinstance(tx, str):
                rec = {"hash": tx, "blockNumber": block_number}
            else:
                rec = dict(tx)
                rec["blockNumber"] = block_number
            self._txs.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")

    def commit(self) -> None:
        self._blocks.flush()
        self._txs.flush()
        os.fsync(self._blocks.fileno())
        os.fsync(self._txs.fileno())

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            self._blocks.close()
            self._txs.close()

    def last_indexed_block(self) -> Optional[int]:
        return self._last_block

    def _load_last_block_number(self) -> Optional[int]:
        """
        Read the final JSONL block entry (if any) to resume from the last height.

        We only inspect ``blocks.jsonl`` because it is guaranteed to contain a
        ``number`` field for each recorded block. The method is intentionally
        lightweight and defensive to avoid interfering with ingest if prior
        data is missing or malformed.
        """

        if not self.blocks_path.exists():
            return None

        try:
            with open(self.blocks_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                end = fh.tell()
                if end == 0:
                    return None

                # Read backwards in small chunks until we find a newline to
                # isolate the last complete line.
                buf = b""
                pos = end
                while pos > 0:
                    step = min(1024, pos)
                    pos -= step
                    fh.seek(pos)
                    chunk = fh.read(step)
                    buf = chunk + buf
                    if b"\n" in chunk:
                        break

                lines = buf.strip().split(b"\n")
                if not lines:
                    return None

                rec = json.loads(lines[-1].decode("utf-8"))
                num = rec.get("number") or rec.get("blockNumber") or rec.get("block_number")
                if num is None:
                    return None
                return _hex_to_int(num)
        except Exception:
            return None


# ------------------------------- ingestor ---------------------------------- #

@dataclass
class Ingestor:
    cfg: IndexerConfig
    log: logging.Logger

    def __post_init__(self) -> None:
        self.log.setLevel(getattr(logging, self.cfg.log_level.upper(), logging.INFO))
        # Choose sink
        sink_name = getattr(self.cfg, "sink", "sqlite")
        data_dir = pathlib.Path(getattr(self.cfg, "data_dir", ".")).expanduser()
        if sink_name == "sqlite":
            self.sink: Sink = SqliteSink(data_dir / "indexer.db", self.log)
        elif sink_name == "jsonl":
            self.sink = JsonlSink(data_dir, self.log)
        else:
            raise ValueError(f"unknown sink: {sink_name}")

        self.index_full_txs: bool = bool(getattr(self.cfg, "index_full_txs", True))
        self.max_batch: int = int(getattr(self.cfg, "max_batch_size", 25))
        self.tail_poll_interval_s: float = float(getattr(self.cfg, "tail_poll_interval_s", 2.0))

    # ----------------------------- high-level ops --------------------------- #

    async def run(self) -> None:
        """
        Backfill from cfg.start_block (or last indexed + 1), then tail forever.
        """
        async with JsonRpcClient(self.cfg) as rpc:
            start = self._compute_start_height()
            self.log.info("starting ingest at block %s", start)

            await self._backfill_to_head(rpc, start)
            await self._tail(rpc)

    async def backfill(self, start: int, stop: int) -> None:
        """
        Finite backfill [start, stop], inclusive.
        """
        if stop < start:
            raise ValueError("stop must be >= start")
        async with JsonRpcClient(self.cfg) as rpc:
            await self._ingest_range(rpc, start, stop, commit_each_batch=True)
        await self.sink.aclose()

    async def tail(self) -> None:
        """
        Tail from (last indexed + 1) forever.
        """
        async with JsonRpcClient(self.cfg) as rpc:
            start = self._compute_start_height()
            await self._backfill_to_head(rpc, start)
            await self._tail(rpc)

    async def one_shot(self, at: int) -> None:
        async with JsonRpcClient(self.cfg) as rpc:
            await self._ingest_range(rpc, at, at, commit_each_batch=True)
        await self.sink.aclose()

    # ----------------------------- internals -------------------------------- #

    def _compute_start_height(self) -> int:
        # Prefer persisted progress, fallback to config.start_block (default 0)
        last = self.sink.last_indexed_block()
        if last is not None:
            return last + 1
        return int(getattr(self.cfg, "start_block", 0))

    async def _backfill_to_head(self, rpc: JsonRpcClient, start: int) -> None:
        """
        Backfill from `start` to current head (once).
        """
        head = await rpc.block_number()
        stop_cfg = cast(Optional[int], getattr(self.cfg, "stop_block", None))
        if stop_cfg is not None:
            head = min(head, stop_cfg)
        if head < start:
            return
        await self._ingest_range(rpc, start, head, commit_each_batch=True)

    async def _tail(self, rpc: JsonRpcClient) -> None:
        """
        Keep following the chain head. When caught up, sleep a bit and try again.
        """
        cur = self._compute_start_height()
        stop_cfg = cast(Optional[int], getattr(self.cfg, "stop_block", None))
        while True:
            head = await rpc.block_number()
            if stop_cfg is not None:
                head = min(head, stop_cfg)

            if cur <= head:
                await self._ingest_range(rpc, cur, head, commit_each_batch=True)
                cur = head + 1
            else:
                await asyncio.sleep(self.tail_poll_interval_s)

    async def _ingest_range(self, rpc: JsonRpcClient, start: int, stop: int, *, commit_each_batch: bool) -> None:
        self.log.info("ingest range [%s, %s]", start, stop)
        full_txs = self.index_full_txs

        cur = start
        while cur <= stop:
            chunk_end = min(cur + self.max_batch - 1, stop)
            blocks = cast(List[Json], await rpc.get_block_range(cur, chunk_end, full_txs=full_txs, max_batch=self.max_batch))  # noqa: E501

            # Defensive: some nodes may return null for not-yet-built blocks
            filtered: List[Json] = [b for b in blocks if b]
            for b in filtered:
                try:
                    self._process_block(b, full_txs=full_txs)
                except Exception as e:  # keep going on individual block issues
                    num = b.get("number", "<unknown>")
                    self.log.exception("failed to process block %s: %s", num, e)

            # Persist batch
            if isinstance(self.sink, SqliteSink):
                self.sink.commit()
            elif isinstance(self.sink, JsonlSink):
                self.sink.commit()

            # progress
            cur = chunk_end + 1

            if commit_each_batch:
                # lightweight heartbeat logging
                last_num = _hex_to_int(filtered[-1]["number"]) if filtered else (cur - 1)
                self.log.info("ingested up to block %s", last_num)

    def _process_block(self, block: Json, *, full_txs: bool) -> None:
        self.sink.record_block(block)
        number = _hex_to_int(block["number"])
        txs = block.get("transactions", [])  # could be list[str] when not full
        if isinstance(txs, list) and txs:
            self.sink.record_txs(number, cast(Iterable[Json], txs))


# ------------------------------- CLI entry --------------------------------- #

def _build_ingestor(cfg: Optional[IndexerConfig] = None) -> Ingestor:
    cfg = cfg or from_env()
    log = logging.getLogger("indexer.ingest")
    return Ingestor(cfg=cfg, log=log)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Indexer Lite ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="backfill from start_block then tail forever")

    bf = sub.add_parser("backfill", help="finite backfill [from, to] inclusive")
    bf.add_argument("--from", dest="start", type=int, required=True)
    bf.add_argument("--to", dest="stop", type=int, required=True)

    tail = sub.add_parser("tail", help="tail from last indexed + 1")

    one = sub.add_parser("one-shot", help="ingest a single block by number")
    one.add_argument("--at", dest="at", type=int, required=True)

    p.add_argument("--log-level", default=None, help="override log level (DEBUG, INFO, ...)")
    return p.parse_args(argv)


async def _amain(ns: argparse.Namespace) -> int:
    ing = _build_ingestor()
    if ns.log_level:
        ing.log.setLevel(getattr(logging, ns.log_level.upper(), logging.INFO))

    if ns.cmd == "run":
        await ing.run()
    elif ns.cmd == "backfill":
        await ing.backfill(ns.start, ns.stop)
    elif ns.cmd == "tail":
        await ing.tail()
    elif ns.cmd == "one-shot":
        await ing.one_shot(ns.at)
    else:
        raise SystemExit(f"unknown command: {ns.cmd}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ns = _parse_args(argv)
    try:
        return asyncio.run(_amain(ns))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
