"""Gateway client for the 9.0.0 GPU-Studio kinds — streamed large-file transport.

The classic media kinds move bytes inline as base64 JSON. The studio kinds move real
files (videos, songs, .blend scenes, rendered frame zips), so this client streams:

  * download_input(url)          — claim payloads carry input_urls; fetch to a temp file
  * post_progress(job_id, pct)   — heartbeat that also EXTENDS the job lease server-side
  * post_result_file(job_id, …)  — raw-binary upload of a big artifact (no base64 blowup)

Everything is stdlib urllib (matching miner.py), fail-closed, and bounded: downloads
enforce a byte cap as they stream, uploads send Content-Length from the real file size.
Progress posts are best-effort (a lost heartbeat must never fail a render); input
downloads and result posts raise MediaError on any failure.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from .base import MediaError

# Studio inputs are capped server-side at 512 MB; give the client the same ceiling
# (+ a little slack for filesystem overhead) so a lying Content-Length can't fill disk.
MAX_DOWNLOAD_BYTES = 550 * 1024 * 1024
_CHUNK = 1024 * 256


class GatewayClient:
    """Bearer-authenticated helper bound to one gateway + miner token."""

    def __init__(self, gateway: str, token: str, *, timeout: float = 120.0):
        self.base = gateway.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ── internals ────────────────────────────────────────────────────────────
    def _request(self, url: str, *, data=None, method: str = "GET",
                 headers: Optional[dict] = None, timeout: Optional[float] = None):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=timeout or self.timeout)  # nosec B310

    def abs_url(self, url: str) -> str:
        return url if url.startswith("http") else f"{self.base}{url}"

    # ── inputs ───────────────────────────────────────────────────────────────
    def download_input(self, url: str, dest_path: str, *,
                       max_bytes: int = MAX_DOWNLOAD_BYTES) -> int:
        """Stream an input file to dest_path. Returns bytes written.

        Raises MediaError on HTTP failure, empty body, or byte-cap overflow (the
        partial file is removed — never leave a truncated input for a render)."""
        total = 0
        try:
            with self._request(self.abs_url(url)) as r, open(dest_path, "wb") as f:
                while True:
                    chunk = r.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaError(f"input exceeds {max_bytes} bytes")
                    f.write(chunk)
        except MediaError:
            try:
                os.unlink(dest_path)
            except OSError:
                pass
            raise
        except Exception as e:
            try:
                os.unlink(dest_path)
            except OSError:
                pass
            raise MediaError(f"input download failed: {e}") from e
        if total == 0:
            try:
                os.unlink(dest_path)
            except OSError:
                pass
            raise MediaError("input download was empty")
        return total

    # ── progress ─────────────────────────────────────────────────────────────
    def post_progress(self, job_id: str, pct: float, note: str = "") -> None:
        """Best-effort progress heartbeat (also extends the server-side lease)."""
        body = json.dumps({"job_id": job_id, "pct": max(0.0, min(100.0, float(pct))),
                           "note": (note or "")[:200]}).encode()
        try:
            with self._request(f"{self.base}/api/mkt/v1/media/miner/progress",
                               data=body, method="POST",
                               headers={"Content-Type": "application/json"},
                               timeout=15):
                pass
        except Exception:
            pass  # heartbeats must never kill a render

    def progress_fn(self, job_id: str, *, start: float = 0.0, span: float = 100.0):
        """A progress callable mapping a stage's local 0..100 onto [start, start+span]."""
        def _p(pct: float, note: str = "") -> None:
            self.post_progress(job_id, start + (span * max(0.0, min(100.0, pct)) / 100.0), note)
        return _p

    # ── results ──────────────────────────────────────────────────────────────
    def post_result_file(self, job_id: str, path: str, *, mime: str, sha3: str,
                         meta: Optional[dict] = None) -> Any:
        """Stream a finished artifact from disk. Raises MediaError unless the
        gateway acknowledges with HTTP 200."""
        size = os.path.getsize(path)
        if size <= 0:
            raise MediaError("refusing to post an empty artifact")
        meta_b64 = base64.b64encode(
            json.dumps(meta or {}, separators=(",", ":"))[:2000].encode()
        ).decode("ascii")
        url = f"{self.base}/api/mkt/v1/media/miner/result-file?job_id={job_id}"
        f = open(path, "rb")
        try:
            req = urllib.request.Request(url, data=f, method="POST")
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("Content-Type", "application/octet-stream")
            req.add_header("Content-Length", str(size))
            req.add_header("x-anm-mime", mime)
            req.add_header("x-anm-sha3", sha3)
            req.add_header("x-anm-meta", meta_b64)
            with urllib.request.urlopen(req, timeout=max(self.timeout, 600)) as r:  # nosec B310
                code = r.getcode()
                body = r.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            raise MediaError(f"result upload rejected ({e.code}): {detail}") from e
        except Exception as e:
            raise MediaError(f"result upload failed: {e}") from e
        finally:
            f.close()
        if code != 200:
            raise MediaError(f"result upload returned {code}")
        try:
            return json.loads(body.decode()) if body else None
        except Exception:
            return None
