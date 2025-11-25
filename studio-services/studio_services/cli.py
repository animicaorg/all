"""
Admin CLI for Animica Studio Services.

Utilities:
  - migrate           : apply DB migrations / initialize schema
  - create-api-key    : generate and store an API key (for auth middleware)
  - list-api-keys     : list stored API keys (redacted)
  - revoke-api-key    : revoke (soft delete) an API key by id
  - queue-stats       : print verification queue stats
  - backfill          : run best-effort backfill jobs (indexes, digests)
  - gc                : garbage-collect old artifacts / stale records

Usage:
  python -m studio_services.cli <command> [options]
"""

from __future__ import annotations

import asyncio
import binascii
import os
import secrets
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

import typer

from .config import Config, load_config
from .logging import get_logger  # type: ignore[attr-defined]  # logger helper expected in module
from .storage import sqlite as storage_sqlite
from .storage import fs as storage_fs
from .tasks import queue as tasks_queue

app = typer.Typer(add_completion=False, help="Animica Studio Services — Admin CLI")
log = get_logger(__name__) if hasattr(sys.modules.get(__name__), "get_logger") else None  # fallback later


@dataclass
class AppCtx:
    cfg: Config
    db: object | None = None


_ctx: AppCtx | None = None


async def _open_db(cfg: Config):
    """
    Open or initialize the SQLite DB using storage_sqlite helpers.
    Compatible with both async/sync implementations.
    """
    if hasattr(storage_sqlite, "init_db"):
        maybe = storage_sqlite.init_db(cfg)
        return await maybe if asyncio.iscoroutine(maybe) else maybe
    if hasattr(storage_sqlite, "open_db"):
        maybe = storage_sqlite.open_db(cfg)
        return await maybe if asyncio.iscoroutine(maybe) else maybe
    raise RuntimeError("storage_sqlite has neither init_db nor open_db")


async def _close_db(db: object):
    if hasattr(storage_sqlite, "close_db"):
        maybe = storage_sqlite.close_db(db)
        if asyncio.iscoroutine(maybe):
            await maybe
    elif hasattr(db, "close"):
        maybe = db.close()
        if asyncio.iscoroutine(maybe):
            await maybe


def _ensure_logger():
    global log
    if log is None:
        class _Dummy:
            def info(self, *a, **k): print(*a)
            def warning(self, *a, **k): print(*a, file=sys.stderr)
            def error(self, *a, **k): print(*a, file=sys.stderr)
        log = _Dummy()


def _ctx_or_init(config_path: Optional[str] = None) -> AppCtx:
    global _ctx
    if _ctx is not None:
        return _ctx
    cfg = load_config(config_path=config_path) if "config_path" in load_config.__code__.co_varnames else load_config()
    _ctx = AppCtx(cfg=cfg, db=None)
    return _ctx


@app.callback()
def main(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to env/config file (optional)"),
):
    """
    Shared options for all subcommands.
    """
    _ensure_logger()
    _ctx_or_init(config)


@app.command("migrate")
def migrate():
    """
    Apply DB migrations or initialize schema.
    """
    ctx = _ctx_or_init()
    async def _run():
        db = await _open_db(ctx.cfg)
        ctx.db = db
        # Prefer an explicit migrate function if present.
        if hasattr(storage_sqlite, "migrate"):
            maybe = storage_sqlite.migrate(db, ctx.cfg)
            if asyncio.iscoroutine(maybe):
                await maybe
        elif hasattr(storage_sqlite, "apply_schema"):
            maybe = storage_sqlite.apply_schema(db, ctx.cfg)
            if asyncio.iscoroutine(maybe):
                await maybe
        print("✅ Migrations applied.")
        await _close_db(db)

    asyncio.run(_run())


def _random_api_key(n_bytes: int = 32) -> str:
    return binascii.hexlify(secrets.token_bytes(n_bytes)).decode()


@app.command("create-api-key")
def create_api_key(
    name: str = typer.Option("default", "--name", "-n", help="Label for this key"),
    scopes: Optional[str] = typer.Option(None, "--scopes", "-s", help="CSV of scopes (optional)"),
    print_only: bool = typer.Option(False, "--print-only", help="Do not persist; just print a random key"),
):
    """
    Generate and store an API key in the database (or print without storing).
    """
    key = _random_api_key()
    if print_only:
        print(key)
        return

    ctx = _ctx_or_init()

    async def _run():
        db = await _open_db(ctx.cfg)
        ctx.db = db

        # Try dedicated helper if provided by storage module; otherwise fallback to generic SQL.
        if hasattr(storage_sqlite, "insert_api_key"):
            maybe = storage_sqlite.insert_api_key(db, key=key, name=name, scopes=scopes)
            if asyncio.iscoroutine(maybe):
                await maybe
        else:
            # Best-effort generic SQL (assumes sqlite3-like connection)
            import sqlite3  # type: ignore
            if isinstance(db, sqlite3.Connection):
                cur = db.cursor()
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS api_keys(
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           name TEXT NOT NULL,
                           key TEXT NOT NULL UNIQUE,
                           scopes TEXT,
                           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           revoked_at TIMESTAMP
                       );"""
                )
                cur.execute("INSERT INTO api_keys(name, key, scopes) VALUES (?, ?, ?);", (name, key, scopes))
                db.commit()
            else:
                raise RuntimeError("DB helper for API keys not available and DB is not sqlite3.Connection")

        print("✅ API key created:")
        print(key)
        await _close_db(db)

    asyncio.run(_run())


@app.command("list-api-keys")
def list_api_keys():
    """
    List stored API keys (redacted), if the storage helper is available.
    """
    ctx = _ctx_or_init()

    async def _run():
        db = await _open_db(ctx.cfg)
        ctx.db = db

        rows: Sequence[tuple] = []
        if hasattr(storage_sqlite, "list_api_keys"):
            maybe = storage_sqlite.list_api_keys(db)
            rows = await maybe if asyncio.iscoroutine(maybe) else maybe
        else:
            try:
                import sqlite3  # type: ignore
                if isinstance(db, sqlite3.Connection):
                    cur = db.cursor()
                    cur.execute("SELECT id, name, key, scopes, created_at, revoked_at FROM api_keys ORDER BY id;")
                    rows = cur.fetchall()
            except Exception:
                pass

        if not rows:
            print("No API keys found.")
        else:
            for r in rows:
                # Attempt to map common shapes. Indexing is best-effort.
                rid = r[0]
                rname = r[1]
                rkey = r[2]
                rscopes = r[3] if len(r) > 3 else None
                rcreated = r[4] if len(r) > 4 else None
                rrevoked = r[5] if len(r) > 5 else None
                red = (rkey[:6] + "…" + rkey[-4:]) if isinstance(rkey, str) and len(rkey) > 10 else "redacted"
                status = "revoked" if rrevoked else "active"
                print(f"[{rid}] {rname:<16} {red:<16} scopes={rscopes or '-'} created={rcreated} {status}")

        await _close_db(db)

    asyncio.run(_run())


@app.command("revoke-api-key")
def revoke_api_key(
    key_id: int = typer.Argument(..., help="Numeric id of the API key to revoke"),
):
    """
    Revoke (soft delete) an API key by id.
    """
    ctx = _ctx_or_init()

    async def _run():
        db = await _open_db(ctx.cfg)
        ctx.db = db

        ok = False
        if hasattr(storage_sqlite, "revoke_api_key"):
            maybe = storage_sqlite.revoke_api_key(db, key_id=key_id)
            ok = await maybe if asyncio.iscoroutine(maybe) else bool(maybe)
        else:
            try:
                import sqlite3  # type: ignore
                if isinstance(db, sqlite3.Connection):
                    cur = db.cursor()
                    cur.execute(
                        "UPDATE api_keys SET revoked_at=CURRENT_TIMESTAMP WHERE id=? AND revoked_at IS NULL;",
                        (key_id,),
                    )
                    ok = cur.rowcount > 0
                    db.commit()
            except Exception:
                ok = False

        if ok:
            print(f"✅ Revoked API key id={key_id}")
        else:
            print(f"⚠️  Could not revoke API key id={key_id} (not found?)", file=sys.stderr)
            sys.exit(1)

        await _close_db(db)

    asyncio.run(_run())


@app.command("queue-stats")
def queue_stats():
    """
    Print verification job queue statistics.
    """
    ctx = _ctx_or_init()

    async def _run():
        db = await _open_db(ctx.cfg)
        ctx.db = db

        stats = {}
        if hasattr(tasks_queue, "stats"):
            maybe = tasks_queue.stats(db)
            stats = await maybe if asyncio.iscoroutine(maybe) else maybe
        else:
            # Fallback heuristic for a 'queue' table
            try:
                import sqlite3  # type: ignore
                if isinstance(db, sqlite3.Connection):
                    cur = db.cursor()
                    cur.execute("SELECT COUNT(*) FROM queue WHERE status='pending';")
                    pending = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM queue WHERE status='active';")
                    active = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM queue WHERE status='failed';")
                    failed = cur.fetchone()[0]
                    stats = {"pending": pending, "active": active, "failed": failed}
            except Exception:
                stats = {}

        if not stats:
            print("No queue stats available.")
        else:
            for k, v in stats.items():
                print(f"{k:>8}: {v}")

        await _close_db(db)

    asyncio.run(_run())


@app.command("backfill")
def backfill(
    artifacts: bool = typer.Option(True, help="Backfill artifacts metadata/hashes if missing"),
    verifications: bool = typer.Option(True, help="Recompute verification digests if missing"),
    dry_run: bool = typer.Option(False, help="Scan only; do not write changes"),
):
    """
    Best-effort backfill jobs to recompute missing indexes/digests.
    """
    ctx = _ctx_or_init()

    async def _run():
        db = await _open_db(ctx.cfg)
        ctx.db = db

        changed = 0

        # Prefer dedicated helpers if present in storage/adapters modules.
        if artifacts and hasattr(storage_fs, "backfill_artifacts"):
            maybe = storage_fs.backfill_artifacts(db, ctx.cfg, dry_run=dry_run)
            changed += await maybe if asyncio.iscoroutine(maybe) else int(maybe or 0)
        if verifications and hasattr(storage_sqlite, "backfill_verifications"):
            maybe = storage_sqlite.backfill_verifications(db, ctx.cfg, dry_run=dry_run)
            changed += await maybe if asyncio.iscoroutine(maybe) else int(maybe or 0)

        print(f"✅ Backfill complete. Changes: {changed} (dry_run={dry_run})")
        await _close_db(db)

    asyncio.run(_run())


@app.command("gc")
def gc(
    days: int = typer.Option(30, "--days", "-d", help="Delete orphaned artifacts older than N days"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan only; do not delete"),
):
    """
    Garbage-collect orphaned artifacts and stale rows.
    """
    ctx = _ctx_or_init()

    async def _run():
        db = await _open_db(ctx.cfg)
        ctx.db = db

        removed = 0
        # Prefer helpers if exposed by storage layers
        if hasattr(storage_fs, "gc"):
            maybe = storage_fs.gc(db, ctx.cfg, older_than_days=days, dry_run=dry_run)
            removed += await maybe if asyncio.iscoroutine(maybe) else int(maybe or 0)
        if hasattr(storage_sqlite, "gc"):
            maybe = storage_sqlite.gc(db, ctx.cfg, older_than_days=days, dry_run=dry_run)
            removed += await maybe if asyncio.iscoroutine(maybe) else int(maybe or 0)

        print(f"✅ GC complete. Removed: {removed} (dry_run={dry_run}, days={days})")
        await _close_db(db)

    asyncio.run(_run())


def _entry():
    # Allow: python -m studio_services.cli
    app()


if __name__ == "__main__":
    _entry()
