"""Self-growing knowledge store for the chat bridge ("teach itself").

Every web lookup the bridge performs and every real answer it serves is written here —
SQLite + FTS5, no model, no training. The next question first RECALLS from this store
(ranked BM25 over fetched page excerpts and prior answers), so the assistant gets better at
the things people actually ask about: a page fetched once is knowledge forever, a good
answer becomes a worked example for the next similar question, and recall costs
milliseconds where a web round-trip costs seconds.

Tables
  docs(url PRIMARY KEY, title, text, fetched_at, hits)   — page excerpts + search snippets
  answers(id, ts, question, answer, sources, model)       — served answers (non-stub only)
  docs_fts / answers_fts                                  — FTS5 shadows (bm25 ranking)

Everything is best-effort: a store failure never fails a chat turn. Location:
``BRIDGE_KNOWLEDGE_DB`` (default ``<bridge dir>/knowledge.db``); ``BRIDGE_KNOWLEDGE=0`` disables.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from typing import Optional

_DB_PATH = os.environ.get("BRIDGE_KNOWLEDGE_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge.db")
_ENABLED = os.environ.get("BRIDGE_KNOWLEDGE", "1") not in ("0", "off", "false")
_MAX_DOC_CHARS = int(os.environ.get("BRIDGE_KNOWLEDGE_DOC_CHARS", "6000"))
_MAX_ANSWER_CHARS = int(os.environ.get("BRIDGE_KNOWLEDGE_ANSWER_CHARS", "4000"))

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_fts_ok = True


def _db() -> Optional[sqlite3.Connection]:
    global _conn, _fts_ok
    if not _ENABLED:
        return None
    if _conn is not None:
        return _conn
    try:
        c = sqlite3.connect(_DB_PATH, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS docs (url TEXT PRIMARY KEY, title TEXT, text TEXT, fetched_at REAL, hits INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS answers (id INTEGER PRIMARY KEY, ts REAL, question TEXT, answer TEXT, sources TEXT, model TEXT)")
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(url UNINDEXED, title, text, content='docs', content_rowid='rowid')")
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS answers_fts USING fts5(question, answer, content='answers', content_rowid='id')")
            c.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
                  INSERT INTO docs_fts(rowid, url, title, text) VALUES (new.rowid, new.url, new.title, new.text); END;
                CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
                  INSERT INTO docs_fts(docs_fts, rowid, url, title, text) VALUES('delete', old.rowid, old.url, old.title, old.text); END;
                CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
                  INSERT INTO docs_fts(docs_fts, rowid, url, title, text) VALUES('delete', old.rowid, old.url, old.title, old.text);
                  INSERT INTO docs_fts(rowid, url, title, text) VALUES (new.rowid, new.url, new.title, new.text); END;
                CREATE TRIGGER IF NOT EXISTS answers_ai AFTER INSERT ON answers BEGIN
                  INSERT INTO answers_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer); END;
                """
            )
        except sqlite3.OperationalError:
            _fts_ok = False
        c.commit()
        _conn = c
        return c
    except Exception:
        return None


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-\.]{1,40}")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "what", "how", "do", "does",
         "can", "i", "you", "me", "my", "it", "this", "that", "with", "about", "tell", "please", "explain", "why"}


def _fts_query(text: str, max_terms: int = 10) -> str:
    terms = []
    for t in _TOKEN.findall(text or ""):
        tl = t.lower().strip(".-_")
        if len(tl) < 2 or tl in _STOP or tl in terms:
            continue
        terms.append(tl)
        if len(terms) >= max_terms:
            break
    # OR query with a phrase-free form; FTS5 needs quoting for tokens with punctuation.
    return " OR ".join('"' + t.replace('"', '') + '"' for t in terms)


def remember_web(query: str, web_ctx: dict) -> None:
    """Store search results + fetched excerpts from a web_context() result."""
    c = _db()
    if c is None or not web_ctx:
        return
    text = web_ctx.get("text") or ""
    sources = web_ctx.get("sources") or []
    excerpts: dict[str, str] = {}
    for m in re.finditer(r"Excerpt from (\S+):\n(.+?)(?=\n\nExcerpt from |\Z)", text, re.S):
        excerpts[m.group(1)] = m.group(2).strip()[:_MAX_DOC_CHARS]
    snippets: dict[str, str] = {}
    for m in re.finditer(r"\[\d+\] (.+?)\n(\S+)\n(.*?)(?=\n\n\[\d+\] |\n\nExcerpt from |\Z)", text, re.S):
        snippets[m.group(2)] = (m.group(1).strip(), m.group(3).strip()[:600])
    with _lock:
        try:
            for s in sources:
                url = (s.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                title = (s.get("title") or snippets.get(url, ("", ""))[0] or "")[:200]
                body = excerpts.get(url) or snippets.get(url, ("", ""))[1]
                if not body:
                    continue
                row = c.execute("SELECT length(text) FROM docs WHERE url=?", (url,)).fetchone()
                if row is None:
                    c.execute("INSERT INTO docs (url, title, text, fetched_at, hits) VALUES (?,?,?,?,0)", (url, title, body, time.time()))
                elif len(body) > (row[0] or 0):
                    c.execute("UPDATE docs SET title=?, text=?, fetched_at=? WHERE url=?", (title, body, time.time(), url))
            c.commit()
        except Exception:
            pass


def remember_answer(question: str, answer: str, sources: Optional[list] = None, model: str = "") -> None:
    c = _db()
    if c is None:
        return
    q = (question or "").strip()
    a = (answer or "").strip()
    if len(q) < 8 or len(a) < 40:
        return
    import json
    with _lock:
        try:
            c.execute("INSERT INTO answers (ts, question, answer, sources, model) VALUES (?,?,?,?,?)",
                      (time.time(), q[:1000], a[:_MAX_ANSWER_CHARS], json.dumps(sources or [])[:2000], model[:80]))
            # keep the table bounded: newest 20k answers
            c.execute("DELETE FROM answers WHERE id < (SELECT MAX(id) FROM answers) - 20000")
            c.commit()
        except Exception:
            pass


def recall(query: str, *, k_docs: int = 3, k_answers: int = 2, max_chars: int = 2400) -> dict:
    """Best prior knowledge for a query: {text, sources, hits}. Empty when nothing relevant."""
    c = _db()
    if c is None or not _fts_ok:
        return {"text": "", "sources": [], "hits": 0}
    fq = _fts_query(query)
    if not fq:
        return {"text": "", "sources": [], "hits": 0}
    lines: list[str] = []
    sources: list[dict] = []
    hits = 0
    with _lock:
        try:
            docs = c.execute(
                "SELECT d.url, d.title, snippet(docs_fts, 2, '', '', '…', 40), bm25(docs_fts) AS r FROM docs_fts "
                "JOIN docs d ON d.rowid = docs_fts.rowid WHERE docs_fts MATCH ? ORDER BY r LIMIT ?", (fq, k_docs)).fetchall()
            ans = c.execute(
                "SELECT a.question, a.answer, bm25(answers_fts) AS r FROM answers_fts JOIN answers a ON a.id = answers_fts.rowid "
                "WHERE answers_fts MATCH ? ORDER BY r LIMIT ?", (fq, k_answers)).fetchall()
            for url, *_ in docs:
                c.execute("UPDATE docs SET hits = hits + 1 WHERE url=?", (url,))
            c.commit()
        except Exception:
            return {"text": "", "sources": [], "hits": 0}
    # bm25 in FTS5 returns NEGATIVE scores (lower = better); only keep reasonably strong hits.
    for url, title, snip, r in docs:
        if r is None or r >= 0:
            continue
        hits += 1
        lines.append(f"[K{hits}] {title or url}\n{url}\n{snip}")
        sources.append({"title": title, "url": url})
    for q, a, r in ans:
        if r is None or r >= 0:
            continue
        hits += 1
        lines.append(f"[K{hits}] Previously answered — Q: {q[:200]}\nA: {a[:700]}")
    text = "\n\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[truncated]"
    return {"text": text, "sources": sources, "hits": hits}


_FACT_FILES = {
    "price": os.environ.get("BRIDGE_FACT_PRICE", "/var/www/animica.dev/anm-price.json"),
    "height": os.environ.get("BRIDGE_FACT_HEIGHT", "/var/www/animica.dev/net-height.json"),
}
_FACT_TRIGGER = re.compile(r"\b(anm|animica|price|market ?cap|exchange|listed|listing|nonkyc|block ?height|chain head|supply|trading|buy anm|sell anm)\b", re.I)


def first_party_facts(query: str) -> str:
    """Verified, machine-read facts from the operator's own feeds (the NonKYC price ticker
    and the node head the sites already display). Weak models hallucinate prices and
    listings; giving them the real numbers — and nothing else — is the only fix."""
    if not _FACT_TRIGGER.search(query or ""):
        return ""
    import json
    lines = []
    try:
        with open(_FACT_FILES["price"], "r", encoding="utf-8") as f:
            pr = json.load(f)
        last = pr.get("last") or pr.get("display")
        if last:
            age = int(time.time() - float(pr.get("ts") or time.time()))
            lines.append(
                f"ANM/USDT price on NonKYC (the ONLY exchange listing ANM): ${float(last):.8f} "
                f"(24h change {pr.get('change_percent', 0)}%, 24h volume {pr.get('base_volume', 0):,.0f} ANM), "
                f"updated {age // 60} min ago. Market: {pr.get('market_url', 'https://nonkyc.io/market/ANM_USDT')}"
            )
    except Exception:
        pass
    try:
        with open(_FACT_FILES["height"], "r", encoding="utf-8") as f:
            hh = json.load(f)
        if hh.get("height"):
            lines.append(f"Animica mainnet (chain id {hh.get('chainId', 1)}) current block height: {hh['height']}. Explorer: https://explorer.animica.org")
    except Exception:
        pass
    if lines:
        lines.append("ANM is NOT listed on MEXC, LBank, Binance, Coinbase, KuCoin, Gate, BitForex or IndoEx; ways to get ANM: mine (pool.animica.org), trade on NonKYC, or accept payments (pay.animica.dev).")
    return "\n".join(lines)


def answer_is_grounded(answer: str, sources: Optional[list]) -> bool:
    """Only remember answers that visibly cite the material they were given: a [n]/[Kn]
    marker or one of the source URLs. Uncited answers from a weak model are the ones most
    likely to be invented — learning them would make the store teach itself errors."""
    a = answer or ""
    if re.search(r"\[K?\d+\]", a):
        return True
    for s in sources or []:
        u = (s.get("url") if isinstance(s, dict) else str(s)) or ""
        if u and u in a:
            return True
    return False


def stats() -> dict:
    c = _db()
    if c is None:
        return {"enabled": False}
    with _lock:
        try:
            nd = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            na = c.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        except Exception:
            return {"enabled": True, "error": "unreadable"}
    return {"enabled": True, "docs": nd, "answers": na, "fts": _fts_ok, "db": _DB_PATH}
