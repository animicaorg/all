from __future__ import annotations

"""
AICF Block-DB adapter
=====================

Purpose
-------
Link AICF *claims* (proof claims resulting in payouts) and *settlements*
(batch settlement events) to specific block heights, so operators can
inspect which block caused which economic side-effects and roll them back
cleanly on reorg.

Design
------
- SQLite, WAL-enabled, single file.
- Two core entities:
  * block_claims(height, job_id, provider_id, payout_id?, amount?, epoch?, tx_hash?)
  * settlements(settlement_id, height, epoch, batch_id, total_amount, payout_count)
    + settlement_items(payout_id -> settlement_id, provider_id, job_id?, amount)
- Idempotent upserts: (height, job_id) unique for claims; payout_id unique for
  settlement_items; settlement_id primary key for settlements.
- Reorg helpers: prune_above(height) deletes claims/settlements above a given height.

This module does *not* depend on other Animica modules; it accepts plain dicts
so it can be used from integration/execution hooks without import cycles.

Example
-------
    bdb = AICFBlockDB("file:aicf_block.db?mode=rwc")
    with bdb.tx():
        bdb.record_proof_claim(
            height=12345,
            job_id="job_abc",
            provider_id="prov_01",
            amount=120_000,
            epoch=7,
            tx_hash="0xdeadbeef",
            payout_id="pay_job_abc",
            meta={"d_ratio": 0.42},
        )

    with bdb.tx():
        bdb.record_settlement(
            height=12346,
            settlement_id="settle_7_001",
            epoch=7,
            batch_id="batch_001",
            total_amount=980_000,
            payouts=[
                {"payout_id": "pay_job_abc", "provider_id": "prov_01", "job_id": "job_abc", "amount": 120_000},
                {"payout_id": "pay_job_def", "provider_id": "prov_02", "job_id": "job_def", "amount": 860_000},
            ],
            meta={"treasury": 50_000, "miner": 20_000},
        )
"""

import contextlib
import json
import os
import sqlite3
import threading
import time
from typing import (Any, Dict, Iterable, Iterator, List, Mapping, Optional,
                    Tuple)

# ---- errors ------------------------------------------------------------------


class BlockDBError(RuntimeError):
    pass


class NotFound(BlockDBError):
    pass


class Conflict(BlockDBError):
    pass


# ---- helpers -----------------------------------------------------------------


def _now_s() -> int:
    return int(time.time())


def _j(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _uj(blob: Optional[bytes]) -> Any:
    return None if not blob else json.loads(blob.decode("utf-8"))


# ---- adapter -----------------------------------------------------------------


class AICFBlockDB:
    """
    Minimal SQLite adapter that links AICF economic artifacts to block heights.
    Thread-safe for light concurrent access via an internal RLock.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str) -> None:
        uri = path.startswith("file:")
        self._db = sqlite3.connect(
            path,
            uri=uri,
            check_same_thread=False,
            isolation_level=None,  # autocommit; explicit BEGIN/COMMIT in tx()
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._apply_pragmas()
        with self.tx():
            self._migrate()

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[None]:
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield
                self._db.execute("COMMIT")
            except Exception:
                try:
                    self._db.execute("ROLLBACK")
                finally:
                    raise

    def _apply_pragmas(self) -> None:
        c = self._db.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA mmap_size=268435456")  # 256 MiB if available
        c.close()

    # -- schema & migrations ---------------------------------------------------

    def _migrate(self) -> None:
        c = self._db.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            )
            """
        )
        # version row
        row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not row:
            c.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )

        c.executescript(
            """
            -- Proof claims referenced by block height
            CREATE TABLE IF NOT EXISTS block_claims (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              height       INTEGER NOT NULL,
              tx_hash      TEXT,
              job_id       TEXT NOT NULL,
              provider_id  TEXT NOT NULL,
              payout_id    TEXT,
              amount       INTEGER,
              epoch        INTEGER,
              claim_json   BLOB NOT NULL,
              created_at   INTEGER NOT NULL,
              UNIQUE(height, job_id) ON CONFLICT REPLACE
            );
            CREATE INDEX IF NOT EXISTS idx_claims_height ON block_claims(height);
            CREATE INDEX IF NOT EXISTS idx_claims_job ON block_claims(job_id);
            CREATE INDEX IF NOT EXISTS idx_claims_provider ON block_claims(provider_id);

            -- Settlement batches recorded at a height
            CREATE TABLE IF NOT EXISTS settlements (
              settlement_id  TEXT PRIMARY KEY,
              height         INTEGER NOT NULL,
              epoch          INTEGER NOT NULL,
              batch_id       TEXT NOT NULL,
              total_amount   INTEGER NOT NULL,
              payout_count   INTEGER NOT NULL,
              details_json   BLOB,
              created_at     INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_settlements_height ON settlements(height);
            CREATE INDEX IF NOT EXISTS idx_settlements_epoch ON settlements(epoch);

            -- Individual payout items within a settlement
            CREATE TABLE IF NOT EXISTS settlement_items (
              payout_id      TEXT PRIMARY KEY,
              settlement_id  TEXT NOT NULL,
              provider_id    TEXT NOT NULL,
              job_id         TEXT,
              amount         INTEGER NOT NULL,
              FOREIGN KEY(settlement_id) REFERENCES settlements(settlement_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_items_provider ON settlement_items(provider_id);
            """
        )
        c.close()

    # ---- claims --------------------------------------------------------------

    def record_proof_claim(
        self,
        *,
        height: int,
        job_id: str,
        provider_id: str,
        tx_hash: Optional[str] = None,
        payout_id: Optional[str] = None,
        amount: Optional[int] = None,
        epoch: Optional[int] = None,
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Idempotently record a proof claim against a block `height`.

        Uniqueness: (height, job_id) → latest row replaces prior (e.g., retries).
        """
        now = _now_s()
        payload = {
            "height": int(height),
            "job_id": job_id,
            "provider_id": provider_id,
            "tx_hash": tx_hash,
            "payout_id": payout_id,
            "amount": int(amount) if amount is not None else None,
            "epoch": int(epoch) if epoch is not None else None,
            "meta": dict(meta or {}),
        }
        self._db.execute(
            """
            INSERT INTO block_claims(height,tx_hash,job_id,provider_id,payout_id,amount,epoch,claim_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(height, job_id) DO UPDATE SET
              tx_hash=excluded.tx_hash,
              provider_id=excluded.provider_id,
              payout_id=excluded.payout_id,
              amount=excluded.amount,
              epoch=excluded.epoch,
              claim_json=excluded.claim_json,
              created_at=excluded.created_at
            """,
            (
                int(height),
                tx_hash,
                job_id,
                provider_id,
                payout_id,
                None if amount is None else int(amount),
                None if epoch is None else int(epoch),
                _j(payload),
                now,
            ),
        )

    def list_claims_at_height(self, height: int) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM block_claims WHERE height=? ORDER BY id ASC", (int(height),)
        ).fetchall()
        return [self._row_claim(r) for r in rows]

    def list_claims_in_range(
        self, start_height: int, end_height: int
    ) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM block_claims WHERE height BETWEEN ? AND ? ORDER BY height ASC, id ASC",
            (int(start_height), int(end_height)),
        ).fetchall()
        return [self._row_claim(r) for r in rows]

    def find_claims_by_job(self, job_id: str) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM block_claims WHERE job_id=? ORDER BY height ASC", (job_id,)
        ).fetchall()
        return [self._row_claim(r) for r in rows]

    # ---- settlements ---------------------------------------------------------

    def record_settlement(
        self,
        *,
        height: int,
        settlement_id: str,
        epoch: int,
        batch_id: str,
        total_amount: int,
        payouts: Iterable[Mapping[str, Any]],
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Record a settlement batch at `height`.

        Each item in `payouts` should include:
          - payout_id: str
          - provider_id: str
          - amount: int
          - job_id: Optional[str]
        """
        now = _now_s()
        payouts_list = [dict(p) for p in payouts]
        self._db.execute(
            """
            INSERT INTO settlements(settlement_id,height,epoch,batch_id,total_amount,payout_count,details_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(settlement_id) DO UPDATE SET
              height=excluded.height,
              epoch=excluded.epoch,
              batch_id=excluded.batch_id,
              total_amount=excluded.total_amount,
              payout_count=excluded.payout_count,
              details_json=excluded.details_json,
              created_at=excluded.created_at
            """,
            (
                settlement_id,
                int(height),
                int(epoch),
                batch_id,
                int(total_amount),
                int(len(payouts_list)),
                _j({"meta": dict(meta or {}), "payouts": payouts_list}),
                now,
            ),
        )
        # Upsert items
        for item in payouts_list:
            self._db.execute(
                """
                INSERT INTO settlement_items(payout_id,settlement_id,provider_id,job_id,amount)
                VALUES(?,?,?,?,?)
                ON CONFLICT(payout_id) DO UPDATE SET
                  settlement_id=excluded.settlement_id,
                  provider_id=excluded.provider_id,
                  job_id=excluded.job_id,
                  amount=excluded.amount
                """,
                (
                    str(item["payout_id"]),
                    settlement_id,
                    str(item["provider_id"]),
                    item.get("job_id"),
                    int(item["amount"]),
                ),
            )

    def get_settlement(self, settlement_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM settlements WHERE settlement_id=?", (settlement_id,)
        ).fetchone()
        if not row:
            raise NotFound(f"settlement {settlement_id} not found")
        out = self._row_settlement(row)
        out["items"] = self.list_settlement_items(settlement_id)
        return out

    def list_settlements_at_height(self, height: int) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM settlements WHERE height=? ORDER BY settlement_id ASC",
            (int(height),),
        ).fetchall()
        return [self._row_settlement(r) for r in rows]

    def list_settlements_by_epoch(self, epoch: int) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM settlements WHERE epoch=? ORDER BY height ASC, settlement_id ASC",
            (int(epoch),),
        ).fetchall()
        return [self._row_settlement(r) for r in rows]

    def list_settlement_items(self, settlement_id: str) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM settlement_items WHERE settlement_id=? ORDER BY payout_id ASC",
            (settlement_id,),
        ).fetchall()
        return [self._row_item(r) for r in rows]

    def find_settlement_by_payout(self, payout_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            """
            SELECT s.* FROM settlement_items i
            JOIN settlements s ON s.settlement_id = i.settlement_id
            WHERE i.payout_id=?
            """,
            (payout_id,),
        ).fetchone()
        if not row:
            raise NotFound(f"payout {payout_id} not linked to a settlement")
        return self._row_settlement(row)

    # ---- reorg helpers -------------------------------------------------------

    def prune_above(self, height: int) -> Tuple[int, int]:
        """
        Delete claims and settlements strictly ABOVE `height`.

        Returns (claims_deleted, settlements_deleted).
        """
        h = int(height)
        claims = self._db.execute(
            "SELECT COUNT(1) AS n FROM block_claims WHERE height>?", (h,)
        ).fetchone()["n"]
        sets = self._db.execute(
            "SELECT COUNT(1) AS n FROM settlements WHERE height>?", (h,)
        ).fetchone()["n"]

        self._db.execute("DELETE FROM block_claims WHERE height>?", (h,))
        # cascade deletes settlement_items
        self._db.execute("DELETE FROM settlements WHERE height>?", (h,))
        return int(claims), int(sets)

    # ---- row decoders --------------------------------------------------------

    def _row_claim(self, row: sqlite3.Row) -> Dict[str, Any]:
        payload = _uj(row["claim_json"]) or {}
        return {
            "id": int(row["id"]),
            "height": int(row["height"]),
            "tx_hash": row["tx_hash"],
            "job_id": row["job_id"],
            "provider_id": row["provider_id"],
            "payout_id": row["payout_id"],
            "amount": None if row["amount"] is None else int(row["amount"]),
            "epoch": None if row["epoch"] is None else int(row["epoch"]),
            "meta": payload.get("meta", {}),
            "created_at": int(row["created_at"]),
        }

    def _row_settlement(self, row: sqlite3.Row) -> Dict[str, Any]:
        details = _uj(row["details_json"]) or {}
        return {
            "settlement_id": row["settlement_id"],
            "height": int(row["height"]),
            "epoch": int(row["epoch"]),
            "batch_id": row["batch_id"],
            "total_amount": int(row["total_amount"]),
            "payout_count": int(row["payout_count"]),
            "meta": details.get("meta", {}),
            "created_at": int(row["created_at"]),
        }

    def _row_item(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "payout_id": row["payout_id"],
            "settlement_id": row["settlement_id"],
            "provider_id": row["provider_id"],
            "job_id": row["job_id"],
            "amount": int(row["amount"]),
        }


# convenience opener via env var
def open_default() -> AICFBlockDB:
    """
    Open using AICF_BLOCK_DB (default: ./aicf_block.db).
    """
    path = os.environ.get("AICF_BLOCK_DB", "aicf_block.db")
    return AICFBlockDB(path)


__all__ = [
    "AICFBlockDB",
    "BlockDBError",
    "NotFound",
    "Conflict",
    "open_default",
]
