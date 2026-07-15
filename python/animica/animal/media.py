"""Media generation via the Animica media QUEUE (dispatch-only — no model runs here). We submit a
job, poll until a GPU miner renders it, and get the bytes back. For TikTok/Shorts we generate a
vertical clip AND a custom music track, then mux them locally with ffmpeg (muxing is not inference,
same allowance as the i2v Ken-Burns path)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from typing import Optional, Tuple

from .config import AnimalConfig


def _post(url: str, payload: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
        return json.loads(r.read().decode())


class MediaResult:
    def __init__(self, kind: str, data: bytes, mime: str, job_id: str, meta: dict):
        self.kind = kind
        self.data = data
        self.mime = mime
        self.job_id = job_id
        self.meta = meta or {}

    def ext(self) -> str:
        m = (self.mime or "").lower()
        if "mp4" in m or "video" in m:
            return "mp4"
        if "png" in m:
            return "png"
        if "jpeg" in m or "jpg" in m:
            return "jpg"
        if "mpeg" in m or "mp3" in m:
            return "mp3"
        if "wav" in m:
            return "wav"
        return "bin"


def submit(cfg: AnimalConfig, kind: str, prompt: str, params: Optional[dict] = None) -> Optional[str]:
    """Enqueue a media job; returns job_id (or None)."""
    try:
        resp = _post(cfg.media_jobs_url, {"kind": kind, "prompt": prompt, "params": params or {}})
        return resp.get("job_id")
    except Exception:
        return None


def wait(cfg: AnimalConfig, job_id: str, kind: str, timeout_secs: int = 900, poll_secs: int = 6) -> Optional[MediaResult]:
    """Poll a job until DONE (a miner picked it up) or timeout. Returns bytes or None. A queued job
    with no miner online simply keeps waiting — it goes through eventually."""
    url = f"{cfg.media_jobs_url}/{job_id}"
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        try:
            j = _get(url)
        except Exception:
            time.sleep(poll_secs)
            continue
        status = j.get("status")
        if status == "DONE" and j.get("result"):
            res = j["result"]
            b64 = res.get("b64") or ""
            try:
                data = base64.b64decode(b64)
            except Exception:
                return None
            return MediaResult(kind, data, res.get("mime") or "", job_id, res.get("meta") or {})
        if status in ("FAILED", "EXPIRED"):
            return None
        time.sleep(poll_secs)
    return None


def generate(cfg: AnimalConfig, kind: str, prompt: str, params: Optional[dict] = None, timeout_secs: int = 900) -> Optional[MediaResult]:
    jid = submit(cfg, kind, prompt, params)
    if not jid:
        return None
    return wait(cfg, jid, kind, timeout_secs=timeout_secs)


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def mux_video_music(video: MediaResult, music: MediaResult) -> Optional[bytes]:
    """Replace/attach the music track onto the video. Local ffmpeg mux — no model involved."""
    if not has_ffmpeg():
        return None
    with tempfile.TemporaryDirectory() as td:
        vp = os.path.join(td, f"v.{video.ext()}")
        ap = os.path.join(td, f"a.{music.ext()}")
        op = os.path.join(td, "out.mp4")
        with open(vp, "wb") as f:
            f.write(video.data)
        with open(ap, "wb") as f:
            f.write(music.data)
        cmd = [
            "ffmpeg", "-y", "-i", vp, "-i", ap,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
            "-movflags", "+faststart", op,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            with open(op, "rb") as f:
                return f.read()
        except Exception:
            return None


def save_preview(cfg: AnimalConfig, data: bytes, ext: str, slug: str) -> str:
    path = os.path.join(cfg.preview_dir(), f"{slug}.{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path
