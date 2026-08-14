#!/usr/bin/env python3
"""deploy_preview.py — preview what Animica Deploy would learn about a website.

Usage:  python3 deploy_preview.py [https://your-site.example]

Animica Deploy (https://animica.org/deploy) is a $20 one-time website+AI
product. Its landing-page preview endpoint is free and keyless:

    POST https://animica.org/deploy/api/preview
    body: {"url": "https://your-site.example"}   (http/https, public host)
    →  {"siteName", "origin", "pagesDiscovered", "topics",
        "samplePages": [{"url", "title"}...], "normalizedUrl"}

Notes for agents:
  * Plain JSON response (not SSE). The server crawls up to 5 pages of the
    target at 1 req/s, so a call can take ~25 s.
  * Rate-limited per IP per hour and charged on attempt — call sparingly.
  * 400 = the target site was unreachable/blocked; 429 = rate-limited;
    502 = the preview service itself is busy. Nothing here spends money —
    checkout is a separate PayPal flow this script never touches.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://animica.org/deploy/api/preview"
TIMEOUT = 40  # server-side crawl budget is 25 s


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    print(f"POST {ENDPOINT}")
    print(f"     {{\"url\": \"{url}\"}}  (crawling target — may take ~25s)")

    body = json.dumps({"url": url}).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            preview = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("message", "")
        except Exception:
            detail = ""
        if e.code == 400:
            print(f"preview failed: the target site could not be crawled. {detail}")
            return 0
        if e.code == 429:
            print(f"rate-limited: {detail or 'try again later.'}")
            return 0
        print(f"error: HTTP {e.code} {detail}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"error: cannot reach {ENDPOINT}: {e}", file=sys.stderr)
        return 1

    print("\npreview result")
    print(f"  site name  : {preview.get('siteName')}")
    print(f"  origin     : {preview.get('origin')}")
    print(f"  pages seen : {preview.get('pagesDiscovered')}")
    print(f"  topics     : {', '.join(preview.get('topics') or []) or '(none detected)'}")
    for p in preview.get("samplePages") or []:
        print(f"  page       : {p.get('title')!r} — {p.get('url')}")
    print(f"  normalized : {preview.get('normalizedUrl')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
