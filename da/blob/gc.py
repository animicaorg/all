"""
Animica • DA • Blob GC & Retention

Retention planner and garbage collector for the local DA blob store
(backed by FS + SQLite; see da/blob/store.py).

What this provides
------------------
- **RetentionPolicy**: declarative knobs (keep recent, budget caps, TTL, pins).
- **plan_deletions()**: compute which blobs are eligible for deletion with reasons.
- **execute_plan()**: delete files and DB rows atomically (best-effort on FS).
- **vacuum()**: SQLite VACUUM + optional directory pruning for empty sharded dirs.
- **CLI**: `python -m da.blob.gc --help`

Assumptions
-----------
DB schema matches store.py:

blobs(root BLOB PRIMARY KEY, namespace INTEGER, size_bytes INTEGER, mime TEXT,
      storage_key TEXT UNIQUE, path TEXT, created_at INTEGER,
      data_shards INTEGER, total_shards INTEGER, share_bytes INTEGER)
pins(root BLOB REFERENCES blobs(root) ON DELETE CASCADE, tag TEXT, created_at INTEGER,
     PRIMARY KEY(root, tag))

Notes
-----
- "Pinned" means a row exists in `pins` for that blob (any tag). Pinned blobs are
  always protected when `keep_pinned=True`. You can also protect only specific
  pin tags by setting `protect_tags`.
- All time values are POSIX seconds.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple, Dict

# ----------------------------- Models ------------------------------------- #


@dataclass(frozen=True)
class RetentionPolicy:
    # Protection
    keep_pinned: bool = True
    protect_tags: Optional[Sequence[str]] = None  # if set and keep_pinned=False, protect only these tags
    protect_younger_than_secs: Optional[int] = None  # keep blobs newer than now - X seconds
    # Minimum recency to keep
    keep_recent_global: int = 0  # keep N newest blobs overall
    keep_recent_per_namespace: int = 0  # keep N newest per namespace
    # Budgets (if set, trim oldest eligible until under)
    max_total_bytes: Optional[int] = None
    max_objects: Optional[int] = None
    # Execution
    dry_run: bool = True
    max_delete: int = 1000  # safety cap


@dataclass(frozen=True)
class Candidate:
    root_hex: str
    path: str
    namespace: int
    size_bytes: int
    created_at: int
    reason: str  # short reason (age, budget-bytes, budget-count, excess-recent)


@dataclass
class DeletionPlan:
    candidates: List[Candidate] = field(default_factory=list)
    # snapshot stats before applying plan
    total_objects: int = 0
    total_bytes: int = 0
    protected_objects: int = 0
    protected_bytes: int = 0

    def summarize(self) -> Dict[str, int]:
        plan_bytes = sum(c.size_bytes for c in self.candidates)
        return {
            "total_objects": self.total_objects,
            "total_bytes": self.total_bytes,
            "protected_objects": self.protected_objects,
            "protected_bytes": self.protected_bytes,
            "plan_objects": len(self.candidates),
            "plan_bytes": plan_bytes,
        }


# ----------------------------- Utils -------------------------------------- #


_HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")


def _now() -> int:
    return int(time.time())


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


_SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def parse_size(s: str) -> int:
    """
    Parse size like '10GB', '5GiB', '750MB', '123456' (bytes).
    """
    s = s.strip().lower()
    m = re.match(r"^(\d+)([a-z]+)?$", s)
    if not m:
        raise ValueError(f"invalid size: {s}")
    n = int(m.group(1))
    unit = m.group(2) or "b"
    if unit not in _SIZE_UNITS:
        raise ValueError(f"invalid size unit: {unit}")
    return n * _SIZE_UNITS[unit]


def parse_duration(s: str) -> int:
    """
    Parse duration like '7d', '24h', '15m', '90s'.
    """
    s = s.strip().lower()
    m = re.match(r"^(\d+)([smhdw])$", s)
    if not m:
        raise ValueError(f"invalid duration: {s}")
    n = int(m.group(1))
    unit = m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return n * mult


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


# ----------------------------- Planner ------------------------------------ #


def _base_eligibility_sql(policy: RetentionPolicy) -> Tuple[str, Tuple]:
    """
    Build the SQL selecting all *potentially* deletable rows, tagging which are protected.

    Returns SQL and bound params.
    """
    params: List[object] = []
    now = _now()

    # Build pin-protection expr
    if policy.keep_pinned:
        pin_protect_expr = "COALESCE(p.pin_count, 0) > 0"
    elif policy.protect_tags:
        # Only protect rows that have a pin with one of the tags
        placeholders = ",".join("?" for _ in policy.protect_tags)
        params.extend(policy.protect_tags)
        pin_protect_expr = f"EXISTS (SELECT 1 FROM pins pt WHERE pt.root=b.root AND pt.tag IN ({placeholders}))"
    else:
        pin_protect_expr = "0"  # never protects by pins

    # Age protection
    age_protect_expr = "0"
    if policy.protect_younger_than_secs:
        cutoff = now - int(policy.protect_younger_than_secs)
        params.append(cutoff)
        age_protect_expr = "b.created_at >= ?"

    # Keep recent overall
    keep_recent_overall_cte = ""
    overall_protect_col = "0 AS protect_overall"
    if policy.keep_recent_global > 0:
        keep_recent_overall_cte = """
        , ranked_overall AS (
            SELECT root, ROW_NUMBER() OVER (ORDER BY created_at DESC, root) AS rn_overall
            FROM blobs
        )
        """
        overall_protect_col = f"CASE WHEN ro.rn_overall <= {int(policy.keep_recent_global)} THEN 1 ELSE 0 END AS protect_overall"

    # Keep recent per namespace
    keep_recent_per_ns_cte = ""
    per_ns_join = ""
    per_ns_protect_col = "0 AS protect_per_ns"
    if policy.keep_recent_per_namespace > 0:
        keep_recent_per_ns_cte = """
        , ranked_ns AS (
            SELECT root, namespace,
                   ROW_NUMBER() OVER (PARTITION BY namespace ORDER BY created_at DESC, root) AS rn_ns
            FROM blobs
        )
        """
        per_ns_join = "LEFT JOIN ranked_ns rns ON rns.root=b.root"
        per_ns_protect_col = f"CASE WHEN rns.rn_ns <= {int(policy.keep_recent_per_namespace)} THEN 1 ELSE 0 END AS protect_per_ns"

    sql = f"""
    WITH pin_counts AS (
        SELECT root, COUNT(*) AS pin_count
        FROM pins
        GROUP BY root
    )
    {keep_recent_overall_cte}
    {keep_recent_per_ns_cte}
    SELECT
        b.root,
        b.storage_key,
        b.path,
        b.namespace,
        b.size_bytes,
        b.created_at,
        ({pin_protect_expr}) AS protect_pin,
        ({age_protect_expr}) AS protect_age,
        {overall_protect_col},
        {per_ns_protect_col}
    FROM blobs b
    LEFT JOIN pin_counts p ON p.root=b.root
    {per_ns_join}
    LEFT JOIN ranked_overall ro ON ro.root=b.root
    """
    return sql, tuple(params)


def plan_deletions(db: sqlite3.Connection, policy: RetentionPolicy) -> DeletionPlan:
    """
    Compute a deletion plan given current DB state and policy.
    """
    sql, params = _base_eligibility_sql(policy)
    cur = db.cursor()

    # Totals
    t_objs, t_bytes = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM blobs"
    ).fetchone()
    # Protected snapshot (pins only, for a coarse idea; detailed protections handled below)
    p_objs, p_bytes = cur.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(b.size_bytes),0)
        FROM blobs b
        WHERE EXISTS (SELECT 1 FROM pins p WHERE p.root=b.root)
        """
    ).fetchone()

    # Candidates (unfiltered by budgets yet)
    rows = list(cur.execute(sql, params))
    candidates: List[Candidate] = []

    # First pass: mark eligibility by protection flags
    for root_b, skey, path, ns, size_b, created, protect_pin, protect_age, protect_overall, protect_per_ns in rows:
        # Skip hard protections
        if protect_pin or protect_age or protect_overall or protect_per_ns:
            continue
        # If not keeping pinned but protect_tags is set, the pin flag above is computed accordingly.
        # At this point, a row is *eligible* for deletion.
        candidates.append(
            Candidate(
                root_hex=_hex(bytes(root_b)),
                path=path,
                namespace=int(ns),
                size_bytes=int(size_b),
                created_at=int(created),
                reason="eligible",
            )
        )

    # Sort oldest-first for budget trimming
    candidates.sort(key=lambda c: (c.created_at, c.root_hex))

    # Budget trimming: if caps set, cut the list down to what is required to satisfy caps.
    plan: List[Candidate] = []
    if policy.max_total_bytes is None and policy.max_objects is None:
        # No budgets: keep everything eligible (subject to max_delete in execution)
        plan = candidates
    else:
        # Figure current usage and derive required frees
        need_free_bytes = 0
        need_free_count = 0
        if policy.max_total_bytes is not None and t_bytes > policy.max_total_bytes:
            need_free_bytes = int(t_bytes - policy.max_total_bytes)
        if policy.max_objects is not None and t_objs > policy.max_objects:
            need_free_count = int(t_objs - policy.max_objects)

        freed_bytes = 0
        freed_count = 0
        for c in candidates:
            take = False
            # If either budget requires freeing, select until both satisfied
            if need_free_bytes > 0 and freed_bytes < need_free_bytes:
                take = True
            if need_free_count > 0 and freed_count < need_free_count:
                take = True
            if take:
                # Reason specialization (purely cosmetic)
                reason = []
                if need_free_bytes > 0:
                    reason.append("budget-bytes")
                if need_free_count > 0:
                    reason.append("budget-count")
                plan.append(Candidate(**{**c.__dict__, "reason": ",".join(reason) or c.reason}))
                freed_bytes += c.size_bytes
                freed_count += 1
            if (need_free_bytes <= 0 or freed_bytes >= need_free_bytes) and (
                need_free_count <= 0 or freed_count >= need_free_count
            ):
                break

    return DeletionPlan(
        candidates=plan,
        total_objects=int(t_objs),
        total_bytes=int(t_bytes),
        protected_objects=int(p_objs),
        protected_bytes=int(p_bytes),
    )


# ----------------------------- Execution ---------------------------------- #


def execute_plan(
    db: sqlite3.Connection,
    objects_root: str,
    plan: DeletionPlan,
    *,
    max_delete: Optional[int] = None,
    dry_run: bool = True,
    prune_empty_dirs: bool = True,
) -> List[str]:
    """
    Apply a deletion plan: remove files, then delete DB rows in a transaction.
    Returns list of root_hex removed (or that would be removed in dry-run).
    """
    removed: List[str] = []
    to_delete = plan.candidates[: (max_delete or len(plan.candidates))]

    if not to_delete:
        return removed

    # File removals are best-effort; DB deletions happen after.
    for c in to_delete:
        if dry_run:
            removed.append(c.root_hex)
            continue
        # Remove file
        with contextlib.suppress(FileNotFoundError):
            os.remove(c.path)
        removed.append(c.root_hex)

    if not dry_run:
        # Delete rows within a transaction
        db.execute("BEGIN IMMEDIATE")
        try:
            q = "DELETE FROM blobs WHERE root=?"
            for c in to_delete:
                rb = bytes.fromhex(c.root_hex[2:] if c.root_hex.startswith("0x") else c.root_hex)
                db.execute(q, (sqlite3.Binary(rb),))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

        if prune_empty_dirs:
            _prune_dirs(objects_root, [c.path for c in to_delete])

    return removed


def vacuum(db: sqlite3.Connection, *, wal_checkpoint: bool = True) -> None:
    """
    Reclaim DB space. Optionally checkpoint WAL first for best effect.
    """
    if wal_checkpoint:
        with contextlib.suppress(sqlite3.DatabaseError):
            db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    db.execute("VACUUM;")
    # Some platforms benefit from a page_size reset; we keep defaults for portability.


def _prune_dirs(objects_root: str, file_paths: Sequence[str], *, max_up: int = 4) -> None:
    """
    Remove empty shard directories up to `objects_root`.
    """
    objects_root = os.path.abspath(objects_root)
    for fp in file_paths:
        d = os.path.dirname(os.path.abspath(fp))
        hops = 0
        while hops < max_up and d.startswith(objects_root):
            try:
                os.rmdir(d)
            except OSError:
                break  # not empty or cannot remove
            d = os.path.dirname(d)
            hops += 1


# ----------------------------- CLI ---------------------------------------- #


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Animica DA Blob GC")
    p.add_argument("--root", required=True, help="Path to the blob store root directory")
    p.add_argument("--db", help="Path to SQLite DB (defaults to <root>/db.sqlite)")
    p.add_argument("--dry-run", action="store_true", default=False, help="Compute and print plan only")
    p.add_argument("--list-only", action="store_true", help="Only list candidates (no deletion)")
    p.add_argument("--max-delete", type=int, default=1000, help="Max objects to delete in one run")

    # Protection knobs
    p.add_argument("--no-keep-pinned", action="store_true", help="Do not protect pinned blobs")
    p.add_argument(
        "--protect-tags",
        type=str,
        help="Comma-separated pin tags to protect (used when --no-keep-pinned is set)",
    )
    p.add_argument(
        "--protect-younger",
        type=str,
        help="Protect blobs younger than duration (e.g. '7d', '24h', '30m')",
    )
    p.add_argument("--keep-recent-global", type=int, default=0, help="Keep N newest blobs overall")
    p.add_argument(
        "--keep-recent-per-ns", type=int, default=0, help="Keep N newest blobs per namespace"
    )

    # Budgets
    p.add_argument("--max-total-bytes", type=str, help="Budget cap, e.g. '100GiB'")
    p.add_argument("--max-objects", type=int, help="Budget cap on total object count")

    # Vacuum & filesystem
    p.add_argument("--vacuum", action="store_true", help="Run SQLite VACUUM after deletion")
    p.add_argument(
        "--no-prune-dirs", action="store_true", help="Do not prune empty sharded directories"
    )

    return p


def _policy_from_args(args: argparse.Namespace) -> RetentionPolicy:
    return RetentionPolicy(
        keep_pinned=not args.no_keep_pinned,
        protect_tags=[t.strip() for t in args.protect_tags.split(",")] if args.protect_tags else None,
        protect_younger_than_secs=parse_duration(args.protect_younger) if args.protect_younger else None,
        keep_recent_global=int(args.keep_recent_global or 0),
        keep_recent_per_namespace=int(args.keep_recent_per_ns or 0),
        max_total_bytes=parse_size(args.max_total_bytes) if args.max_total_bytes else None,
        max_objects=int(args.max_objects) if args.max_objects is not None else None,
        dry_run=bool(args.dry_run or args.list_only),
        max_delete=int(args.max_delete or 1000),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = _build_arg_parser()
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    db_path = args.db or os.path.join(root, "db.sqlite")
    objects_root = os.path.join(root, "objects")

    if not os.path.exists(db_path):
        print(f"[gc] error: db not found at {db_path}", file=sys.stderr)
        return 2

    db = _connect(db_path)
    policy = _policy_from_args(args)

    plan = plan_deletions(db, policy)
    summary = plan.summarize()
    print("[gc] snapshot:", summary)

    # Show plan
    for c in plan.candidates[: policy.max_delete]:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(c.created_at))
        print(f"[gc] del {c.root_hex} ns={c.namespace} size={c.size_bytes}B at={ts} reason={c.reason}")

    if args.list_only:
        return 0

    removed = execute_plan(
        db,
        objects_root=objects_root,
        plan=plan,
        max_delete=policy.max_delete,
        dry_run=policy.dry_run,
        prune_empty_dirs=not args.no_prune_dirs,
    )
    print(f"[gc] removed {len(removed)} object(s){' (dry-run)' if policy.dry_run else ''}")

    if args.vacuum and not policy.dry_run:
        print("[gc] vacuuming database…")
        vacuum(db)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
