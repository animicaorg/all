"""
Keyless, SSRF-hardened web access for the Animica edge (coding agent + chat).

No API keys: search via DuckDuckGo's HTML endpoint, fetch via direct HTTP. Runs on
the edge box and only ever does HTTP (never model inference). Every outbound URL is
validated so the AI can't be steered into internal services (the RPC on
127.0.0.1:8545, cloud metadata at 169.254.169.254, private LAN, etc.): the host must
resolve to EXCLUSIVELY public/global IPs, only http(s) is allowed, and every redirect
hop is re-validated.
"""
from __future__ import annotations

import html as _html
import ipaddress
import re
import socket
from typing import Optional
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlparse

import httpx

_UA = "AnimicaWebAccess/1.0 (+https://animica.dev)"
_TIMEOUT = 12.0
_MAX_BYTES = 500_000        # hard cap on a fetched body before extraction


def _host_is_public(host: str) -> bool:
    """True only if EVERY IP `host` resolves to is a global/public address.

    Blocks loopback, private, link-local (incl. 169.254.169.254 metadata),
    reserved, multicast, and unspecified — so a fetch can't reach this box's own
    services or the LAN. Fails closed on any resolution error.
    """
    if not host:
        return False
    # a bare IP literal is checked directly
    try:
        addr = ipaddress.ip_address(host)
        return _ip_ok(addr)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if not _ip_ok(addr):
            return False
    return True


def _ip_ok(addr) -> bool:
    return not (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ) and addr.is_global


def _safe_url(url: str) -> Optional[str]:
    try:
        p = urlparse(url.strip())
    except Exception:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    if not _host_is_public(p.hostname):
        return None
    return url.strip()


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|noscript|template|svg)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = _html.unescape(h)
    h = re.sub(r"[ \t\r\f\v]+", " ", h)
    h = re.sub(r"\n\s*\n\s*\n+", "\n\n", h)
    return h.strip()


def _ddg_unwrap(href: str) -> Optional[str]:
    """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<encoded-target>."""
    if href.startswith("//"):
        href = "https:" + href
    try:
        p = urlparse(_html.unescape(href))
    except Exception:
        return None
    if p.netloc.endswith("duckduckgo.com") and p.path.startswith("/l/"):
        target = parse_qs(p.query).get("uddg", [None])[0]
        return target if target and target.startswith(("http://", "https://")) else None
    if p.scheme in ("http", "https"):
        return _html.unescape(href)
    return None


async def web_search(query: str, *, k: int = 5) -> list[dict]:
    """Keyless web search. Returns [{title, url, snippet}].

    Tries DuckDuckGo's HTML endpoint first; when that is unavailable — datacenter
    IPs get served a 202 "anomaly" page with no results — falls back to the
    Wikipedia search API, which is keyless and answers reliably from server IPs.
    """
    q = (query or "").strip()
    if not q:
        return []
    results = await _ddg_search(q, k=k)
    if results:
        return results
    return await _wikipedia_search(q, k=k)


async def _ddg_search(q: str, *, k: int) -> list[dict]:
    """Scrape DuckDuckGo's HTML results endpoint (keyless, no API key)."""
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True
        ) as c:
            r = await c.get("https://html.duckduckgo.com/html/", params={"q": q})
        text = r.text
    except Exception:
        return []
    links = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.S
    )
    snips = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', text, re.S
    )
    out: list[dict] = []
    for i, (href, title) in enumerate(links):
        real = _ddg_unwrap(href)
        if not real:
            continue
        snippet = _strip_html(snips[i]) if i < len(snips) else ""
        out.append({
            "title": _strip_html(title)[:200],
            "url": real,
            "snippet": snippet[:400],
        })
        if len(out) >= k:
            break
    return out


async def _wikipedia_search(q: str, *, k: int) -> list[dict]:
    """Keyless fallback via the Wikipedia search API (works from datacenter IPs
    where DuckDuckGo is anomaly-blocked). Good for factual/entity grounding."""
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True
        ) as c:
            r = await c.get("https://en.wikipedia.org/w/api.php", params={
                "action": "query", "list": "search", "srsearch": q,
                "format": "json", "srlimit": str(max(1, k)), "srprop": "snippet",
            })
        hits = r.json().get("query", {}).get("search", [])
    except Exception:
        return []
    out: list[dict] = []
    for h in hits:
        title = (h.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title[:200],
            "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe=""),
            "snippet": _strip_html(h.get("snippet") or "")[:400],
        })
        if len(out) >= k:
            break
    return out


async def web_fetch(url: str, *, max_chars: int = 6000) -> dict:
    """Fetch a public web page and return extracted text. SSRF-guarded; every
    redirect hop is re-validated against the public-IP rule."""
    safe = _safe_url(url)
    if not safe:
        return {"url": url, "ok": False,
                "error": "blocked or invalid URL — only public http(s) addresses are allowed"}
    cur = safe
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=False
        ) as c:
            resp = None
            for _ in range(4):
                resp = await c.get(cur)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    nxt = urljoin(cur, loc)
                    s = _safe_url(nxt)
                    if not s:
                        return {"url": nxt, "ok": False,
                                "error": "redirect to a non-public address blocked"}
                    cur = s
                    continue
                break
    except Exception as e:
        return {"url": cur, "ok": False, "error": f"fetch failed: {type(e).__name__}"}
    if resp is None:
        return {"url": cur, "ok": False, "error": "no response"}
    ctype = resp.headers.get("content-type", "")
    raw = resp.content[:_MAX_BYTES]
    body = raw.decode(resp.encoding or "utf-8", "replace")
    title = ""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    if m:
        title = _strip_html(m.group(1))[:200]
    if "html" in ctype or body.lstrip()[:1] == "<":
        body = _strip_html(body)
    return {
        "url": cur,
        "ok": resp.status_code < 400,
        "status": resp.status_code,
        "title": title,
        "text": body[:max_chars],
    }


async def web_context(query: str, *, k: int = 4, fetch_top: int = 1,
                      max_chars: int = 2000) -> dict:
    """Build a compact, cited context block for chat augmentation: search, then
    optionally deep-read the top result. Returns {text, sources:[{title,url}]}."""
    results = await web_search(query, k=k)
    if not results:
        return {"text": "", "sources": []}
    lines = [f"[{i+1}] {r['title']}\n{r['url']}\n{r['snippet']}"
             for i, r in enumerate(results)]
    for r in results[:max(0, fetch_top)]:
        page = await web_fetch(r["url"], max_chars=max_chars)
        if page.get("ok") and page.get("text"):
            lines.append(f"\nExcerpt from {r['url']}:\n{page['text']}")
    return {
        "text": "\n\n".join(lines),
        "sources": [{"title": r["title"], "url": r["url"]} for r in results],
    }
