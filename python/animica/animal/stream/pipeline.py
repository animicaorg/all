"""The stream orchestrator.

Drives one lockstep loop — brain → audio mixer → behavior → scene render — and pumps
raw video + PCM audio through two FIFOs into a single ffmpeg process that encodes once
and (live) tees to the YouTube RTMP ingest AND a segmented 1-hour recording, or
(preview) writes a local MP4. One loop for both A/V keeps lip-sync frame-exact.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional

from .audio import AudioMixer
from .behavior import BehaviorEngine
from .contract import AnimalState, Character, SceneContext, SpeechItem, StreamConfig, clamp
from .scene import Scene
from .voice import Voice

LineProvider = Callable[[SceneContext], Optional[SpeechItem]]


class StreamPipeline:
    def __init__(self, cfg: StreamConfig, char: Character, *,
                 net_provider: Callable[[], dict] = None,
                 line_provider: LineProvider = None,
                 log=print):
        self.cfg = cfg
        self.char = char
        self.scene = Scene(cfg, char)
        self.behavior = BehaviorEngine(cfg, char, self.scene.stage_bounds(), cfg.seed)
        self.voice = Voice(cfg, char)
        self.mixer = AudioMixer(cfg, char)
        self.net_provider = net_provider or (lambda: {})
        self.line_provider = line_provider
        self.log = log
        self.state = AnimalState(emotion=char.default_emotion)
        self.ctx = SceneContext(title=cfg.channel_name)
        self.ctx.now_playing = "" if cfg.music == "off" else "lofi beats to mine ANM to"
        self._stop = threading.Event()
        self._started = 0.0
        self._last_net = 0.0
        self._last_think = 0.0

    def stop(self):
        self._stop.set()

    # ── main entry ───────────────────────────────────────────────────────────
    def run(self, max_seconds: float = 0.0) -> int:
        """Run until stopped (or max_seconds for preview). Returns ffmpeg's exit code."""
        try:
            from animica.media.base import resolve_ffmpeg
        except Exception as e:                       # pragma: no cover
            self.log(f"[stream] ffmpeg resolver unavailable: {e}")
            return 2
        ff = resolve_ffmpeg()
        if not ff:
            self.log("[stream] no ffmpeg (install ffmpeg or imageio-ffmpeg)")
            return 2

        tmp = tempfile.mkdtemp(prefix="anm-stream-")
        vfifo, afifo = os.path.join(tmp, "v.raw"), os.path.join(tmp, "a.raw")
        os.mkfifo(vfifo)
        os.mkfifo(afifo)
        cmd = self._ffmpeg_cmd(ff, vfifo, afifo, max_seconds)
        self.log("[stream] " + " ".join(self._redact(cmd)))
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        # Feed each FIFO from its own thread via a bounded queue. This decouples frame
        # production from the FIFO open handshake: ffmpeg probes input-0 (video) before
        # it opens input-1 (audio), so a single thread that opens both write ends in
        # order deadlocks. Two writer threads + queues avoid that and give live pacing.
        vq: "queue.Queue" = queue.Queue(maxsize=8)
        aq: "queue.Queue" = queue.Queue(maxsize=240)
        tv = threading.Thread(target=self._writer, args=(vfifo, vq), daemon=True)
        ta = threading.Thread(target=self._writer, args=(afifo, aq), daemon=True)
        tv.start()
        ta.start()
        try:
            self._started = time.monotonic()
            self._loop(vq, aq, proc, max_seconds)
        finally:
            for q in (vq, aq):
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            tv.join(timeout=5)
            ta.join(timeout=5)
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)
        return proc.returncode or 0

    def _writer(self, path: str, q: "queue.Queue"):
        try:
            f = open(path, "wb", buffering=0)
        except Exception as e:                       # pragma: no cover
            self.log(f"[stream] writer open failed {path}: {e}")
            return
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                try:
                    f.write(item)
                except BrokenPipeError:
                    break
        finally:
            try:
                f.close()
            except Exception:
                pass

    # ── the lockstep loop ────────────────────────────────────────────────────
    def _loop(self, vq, aq, proc, max_seconds: float):
        cfg = self.cfg
        fps = cfg.fps
        dt = 1.0 / fps
        spf_exact = cfg.audio_rate / fps
        acc = 0.0
        frame = 0
        live = not cfg.preview_path
        prev_meta = None
        wall0 = time.monotonic()

        while not self._stop.is_set():
            if proc.poll() is not None:
                self.log(f"[stream] ffmpeg exited early rc={proc.returncode}")
                break
            t = frame * dt
            if max_seconds and t >= max_seconds:
                break

            # brain: at most ~every 0.4s decide whether to say something new
            self._maybe_speak(t)

            # audio chunk (advances voice → drives lip-sync + subtitle transitions)
            acc += spf_exact
            n = int(acc)
            acc -= n
            meta_before = self.mixer.current_meta
            pcm = self.mixer.read(n)
            meta_now = self.mixer.current_meta
            if meta_now is not meta_before:
                if meta_now:
                    self.ctx.subtitle = meta_now.get("text", "")
                    self.behavior.set_speaking(True, meta_now.get("emotion", ""), meta_now.get("behavior", ""))
                else:
                    self.ctx.subtitle = ""
                    self.behavior.set_speaking(False)

            self.behavior.tick(dt, self.state, self.mixer.last_voice_rms)
            self._update_ctx(t)

            img = self.scene.render(self.state, self.ctx, t)
            try:
                vq.put(img.tobytes(), timeout=10)
                aq.put(pcm, timeout=10)
            except queue.Full:
                self.log("[stream] encoder stalled (queue full) — dropping frame")
            frame += 1

            if live:  # pace to real time so we stream at ~1x
                target = wall0 + frame * dt
                slack = target - time.monotonic()
                if slack > 0:
                    time.sleep(min(slack, 0.5))

    # ── brain / context ──────────────────────────────────────────────────────
    def _maybe_speak(self, t: float):
        if self.line_provider is None:
            return
        # don't stack lines: only ask for a new one when the mixer is nearly idle
        if self.mixer.backlog > 1 or (t - self._last_think) < 0.4:
            return
        self._last_think = t
        try:
            item = self.line_provider(self.ctx)
        except Exception as e:
            self.log(f"[stream] line_provider error: {e}")
            item = None
        if item and item.text:
            samples, _ = self.voice.synth(item.text)
            self.mixer.enqueue(samples, {"text": item.text, "emotion": item.emotion,
                                         "behavior": item.behavior, "source": item.source})

    def _update_ctx(self, t: float):
        self.ctx.uptime_s = time.monotonic() - self._started
        if (t - self._last_net) > 5.0 or not self.ctx.net_stats:
            self._last_net = t
            try:
                self.ctx.net_stats = self.net_provider() or self.ctx.net_stats
            except Exception:
                pass
        self.ctx.daytime = (t / 3600.0) % 1.0

    # ── ffmpeg command ───────────────────────────────────────────────────────
    def _ffmpeg_cmd(self, ff: str, vfifo: str, afifo: str, max_seconds: float):
        c = self.cfg
        v = [
            ff, "-hide_banner", "-loglevel", "warning", "-y",
            # analyzeduration/probesize 0/32: raw inputs need no probing; without this
            # ffmpeg tries to buffer a ~5s analyze window of raw video and deadlocks.
            "-thread_queue_size", "512", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{c.width}x{c.height}", "-r", str(c.fps),
            "-analyzeduration", "0", "-probesize", "32", "-i", vfifo,
            "-thread_queue_size", "512", "-f", "s16le", "-ar", str(c.audio_rate), "-ac", "2",
            "-analyzeduration", "0", "-probesize", "32", "-i", afifo,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-b:v", f"{c.bitrate_k}k", "-maxrate", f"{c.bitrate_k}k",
            "-bufsize", f"{2 * c.bitrate_k}k", "-g", str(c.fps * 2),
            "-c:a", "aac", "-b:a", "160k", "-ar", str(c.audio_rate),
        ]
        if c.preview_path:
            if max_seconds:
                v += ["-t", str(max_seconds)]
            v += ["-movflags", "+faststart", c.preview_path]
            return v
        # LIVE: encode once, tee to RTMP + a segmented 1-hour recording for VOD upload
        outs = []
        if c.rtmp_url:
            outs.append(f"[f=flv]{c.rtmp_url}")
        if c.record_dir:
            os.makedirs(c.record_dir, exist_ok=True)
            seg = os.path.join(c.record_dir, "seg_%05d.mp4")
            outs.append(f"[f=segment:segment_time={c.segment_seconds}:reset_timestamps=1:segment_format=mp4]{seg}")
        v += ["-f", "tee", "|".join(outs)]
        return v

    @staticmethod
    def _redact(cmd):
        out = []
        for a in cmd:
            out.append(a.split("/live2/")[0] + "/live2/****" if "rtmp" in a and "/live2/" in a else a)
        return out
