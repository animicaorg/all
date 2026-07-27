"""
Publish + host (SERVE mode) for the Animica Internet app.

Publish: take a single self-contained HTML file (or an entry HTML that inlines its assets),
verify it is <= 2 MB, and push it to a name the user owns. The site is then reachable at
<name>.anm from any Animica Internet client — no server to run.

Host: optionally act as a hosting node — announce/heartbeat CIDs (yours and others') to earn
hosting-reward IOUs and improve .anm replication. This is what "serve to the Animica Internet
from a connected machine" means: the app pins content and heartbeats it.
"""

from __future__ import annotations

import os

from .config import MAX_CONTENT_BYTES
from .resolver import compute_cid


class PublishError(RuntimeError):
    pass


def load_site_html(path: str) -> str:
    """Load a self-contained HTML site from a file (or a folder's index.html). Enforces the
    2 MB single-object limit up front with a clear message."""
    if os.path.isdir(path):
        idx = os.path.join(path, "index.html")
        if not os.path.exists(idx):
            raise PublishError(f"no index.html in {path}")
        path = idx
    if not path.lower().endswith((".html", ".htm")):
        raise PublishError("a .anm site is one self-contained HTML file (inline your CSS/JS/images)")
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > MAX_CONTENT_BYTES:
        raise PublishError(f"site is {len(data):,} bytes; the limit is {MAX_CONTENT_BYTES:,} "
                           f"(2 MB). Inline and minify assets, or split content.")
    return data.decode("utf-8", errors="replace")


def publish_site(reg, name: str, html: str) -> dict:
    """Publish HTML to `name` (caller must be logged-in owner). Returns the publish result
    incl. the CID and gateway URL."""
    if len(html.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise PublishError(f"site exceeds {MAX_CONTENT_BYTES:,} bytes (2 MB)")
    try:
        res = reg.publish(name, html)
    except Exception as e:  # noqa: BLE001
        raise PublishError(f"publish failed: {e}") from e
    return res


def local_cid(html: str) -> str:
    """The CID this HTML will get once published — lets the UI show it before uploading."""
    return compute_cid(html.encode("utf-8"))
