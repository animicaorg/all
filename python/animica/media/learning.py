"""Self-teaching ledger for media rendering (miner side, SQLite, no model training).

Every render writes its outcome here — prompt, engine, camera move, model, seeds, and the
fidelity score the CLIP judge gave the winner. Later decisions read it back:

* `choose_camera`: for a shot whose prompt resembles past shots, prefer the camera move
  that historically scored best for that role (ε-greedy so it keeps exploring).
* `best_seed_hint`: the exact seed that won for an identical compiled prompt, so a repeat
  request starts from a known-good draw instead of a fresh lottery.
* `references_for`: cached reference-image URLs per prompt key (what the web lookup
  returned last time) so repeat prompts don't re-search.
* `stats`: what the operator sees in `animica media stats` — renders, mean fidelity by
  engine/camera, so the learning is inspectable, not magic.

Location: ``$ANIMICA_MEDIA_LEARN_DB`` or ``<ANIMICA_DATA_DIR or ~/.animica>/media-learning.db``.
Disable with ``ANIMICA_MEDIA_LEARN=0``. Everything is best-effort: a ledger failure never
fails a render.
"""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "to", "for", "shot", "wide", "close", "up", "establishing", "is", "are"}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def prompt_key(text: str) -> str:
    return " ".join(sorted(_tokens(text)))[:400]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


class Learner:
    def __init__(self, path: Optional[str] = None, *, epsilon: float = 0.15):
        self.path = path or self._default_path()
        self.epsilon = epsilon
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init()

    @staticmethod
    def _default_path() -> str:
        env = os.environ.get("ANIMICA_MEDIA_LEARN_DB")
        if env:
            return env
        base = os.environ.get("ANIMICA_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".animica")
        return os.path.join(base, "media-learning.db")

    def _init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS renders (
                 id INTEGER PRIMARY KEY, ts REAL, kind TEXT, prompt TEXT, pkey TEXT, role TEXT,
                 engine TEXT, camera TEXT, model TEXT, seed INTEGER, fidelity REAL, meta TEXT)"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS renders_pkey ON renders(pkey)")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS refs (pkey TEXT PRIMARY KEY, ts REAL, urls TEXT)"""
        )
        self._conn.commit()

    # ── writes ───────────────────────────────────────────────────────────────
    def _insert(self, **row) -> None:
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO renders (ts, kind, prompt, pkey, role, engine, camera, model, seed, fidelity, meta) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), row.get("kind"), (row.get("prompt") or "")[:2000], prompt_key(row.get("prompt") or ""),
                     row.get("role"), row.get("engine"), row.get("camera"), row.get("model"), row.get("seed"),
                     row.get("fidelity"), json.dumps(row.get("meta") or {}, default=str)[:4000]),
                )
                self._conn.commit()
            except Exception:
                pass

    def record_image(self, prompt: str, out: dict) -> None:
        self._insert(kind="image", prompt=out.get("prompt") or prompt, engine="image", camera=None,
                     model=out.get("model"), seed=out.get("seed"), fidelity=out.get("fidelity"),
                     meta={k: out.get(k) for k in ("candidates", "rerank", "scores", "steps", "precision", "render_size", "refs_used")})

    def record_video(self, prompt: str, meta: dict) -> None:
        for s in meta.get("shots") or []:
            self._insert(kind="video", prompt=s.get("prompt") or prompt, role=s.get("role"), engine=s.get("engine"),
                         camera=s.get("camera"), model=s.get("model"), seed=s.get("seed"), fidelity=s.get("fidelity"),
                         meta={"seconds": s.get("seconds"), "transition": s.get("transition"), "notes": s.get("notes")})

    def remember_references(self, prompt: str, urls: list[str]) -> None:
        if self._conn is None or not urls:
            return
        with self._lock:
            try:
                self._conn.execute("INSERT OR REPLACE INTO refs (pkey, ts, urls) VALUES (?,?,?)",
                                   (prompt_key(prompt), time.time(), json.dumps(urls[:8])))
                self._conn.commit()
            except Exception:
                pass

    # ── reads ────────────────────────────────────────────────────────────────
    def _similar(self, prompt: str, *, kind: Optional[str] = None, role: Optional[str] = None,
                 min_sim: float = 0.3, limit: int = 400) -> list[tuple[float, sqlite3.Row]]:
        if self._conn is None:
            return []
        toks = _tokens(prompt)
        if not toks:
            return []
        q = "SELECT * FROM renders WHERE fidelity IS NOT NULL"
        args: list = []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if role:
            q += " AND role=?"
            args.append(role)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            try:
                self._conn.row_factory = sqlite3.Row
                rows = self._conn.execute(q, args).fetchall()
            except Exception:
                return []
        out = []
        for r in rows:
            sim = _jaccard(toks, set((r["pkey"] or "").split()))
            if sim >= min_sim:
                out.append((sim, r))
        return out

    def choose_camera(self, role: str, index: int, text: str, seconds: float) -> str:
        """Learned camera move for similar past shots, else the director's default."""
        from .video_director import camera_for, CAMERA_MOVES
        default = camera_for(role, index, text, seconds)
        if random.random() < self.epsilon:
            return default
        hist = self._similar(text, kind="video", role=role)
        if len(hist) < 2:
            return default
        agg: dict[str, list[float]] = {}
        for sim, r in hist:
            if r["camera"] in CAMERA_MOVES and r["fidelity"] is not None:
                agg.setdefault(r["camera"], []).append(float(r["fidelity"]) * (0.5 + sim))
        best = None
        for cam, vals in agg.items():
            if len(vals) >= 2:
                score = sum(vals) / len(vals)
                if best is None or score > best[1]:
                    best = (cam, score)
        return best[0] if best else default

    def best_seed_hint(self, prompt: str) -> Optional[int]:
        """Seed that scored best for this exact compiled prompt (identical key)."""
        if self._conn is None:
            return None
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT seed, fidelity FROM renders WHERE pkey=? AND seed IS NOT NULL AND fidelity IS NOT NULL "
                    "ORDER BY fidelity DESC LIMIT 1", (prompt_key(prompt),)).fetchone()
            except Exception:
                return None
        return int(row[0]) if row else None

    def references_for(self, prompt: str, max_age_s: float = 7 * 86400) -> list[str]:
        if self._conn is None:
            return []
        with self._lock:
            try:
                row = self._conn.execute("SELECT ts, urls FROM refs WHERE pkey=?", (prompt_key(prompt),)).fetchone()
            except Exception:
                return []
        if not row or time.time() - float(row[0]) > max_age_s:
            return []
        try:
            return list(json.loads(row[1]))
        except Exception:
            return []

    def stats(self) -> dict:
        if self._conn is None:
            return {}
        with self._lock:
            try:
                n = self._conn.execute("SELECT COUNT(*) FROM renders").fetchone()[0]
                by_engine = self._conn.execute(
                    "SELECT engine, COUNT(*), AVG(fidelity) FROM renders GROUP BY engine").fetchall()
                by_camera = self._conn.execute(
                    "SELECT camera, COUNT(*), AVG(fidelity) FROM renders WHERE camera IS NOT NULL GROUP BY camera").fetchall()
            except Exception:
                return {}
        return {
            "renders": n,
            "by_engine": {e: {"n": c, "mean_fidelity": round(f, 4) if f is not None else None} for e, c, f in by_engine},
            "by_camera": {e: {"n": c, "mean_fidelity": round(f, 4) if f is not None else None} for e, c, f in by_camera},
            "db": self.path,
        }


_LEARNER: Optional[Learner] = None


def get_learner() -> Optional[Learner]:
    global _LEARNER
    if os.environ.get("ANIMICA_MEDIA_LEARN", "1") in ("0", "off", "false"):
        return None
    if _LEARNER is None:
        try:
            _LEARNER = Learner()
        except Exception:
            return None
    return _LEARNER
