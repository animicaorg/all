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
        self._bg = None            # background worker: all network/LLM/chat I/O lives here (LIVE only)
        self._bg_last_net = -1e9   # -inf so the first net-stats fetch / first line fire immediately
        self._bg_last_think = -1e9
        self._pv_last_net = -1e9   # preview runs the brain inline on the video timeline instead
        self._pv_last_think = -1e9

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
            # LIVE: all network/LLM/chat I/O runs on a background thread, never on the render
            # loop, so a slow node RPC or LLM call can never stall frame production (which would
            # make YouTube report "video output low" as the stream falls behind real time).
            # PREVIEW: there is no real-time pacing, so the render loop drains the audio timeline
            # far faster than wall clock — a wall-clock brain thread would leave previews nearly
            # silent. So previews run the brain INLINE on the video timeline (see _inline_brain).
            if not self.cfg.preview_path:
                self._bg = threading.Thread(target=self._bg_loop, name="anm-stream-brain", daemon=True)
                self._bg.start()
            self._loop(vq, aq, proc, max_seconds)
        finally:
            self._stop.set()                     # also releases the background worker
            if self._bg is not None:
                self._bg.join(timeout=5)
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

            # LIVE: the brain runs on the background worker (never block the render loop).
            # PREVIEW: run it inline on the video timeline so speech is present + deterministic.
            if not live:
                self._inline_brain(t)

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
    def _bg_loop(self):
        """Background worker for everything that can block on I/O: overlay net-stats,
        reading live chat, the LLM line generation and TTS synthesis. Keeping all of it
        off the render loop is what lets the stream hold real time — the render thread
        only ever touches CPU-local work (render + audio mix + queue put).

        Enqueue into the mixer is safe from here: AudioMixer._queue is a plain list, and
        list.append (enqueue) vs list.pop(0) (read, on the render thread) are each atomic
        under the GIL; _cur/_vp playback state is only ever touched by read()."""
        while not self._stop.is_set():
            now = time.monotonic()
            # 1) overlay/brain grounding stats — may block for seconds on a slow node RPC.
            # Gate on TIME ONLY: if the fetch keeps failing (net_stats stays empty) we must NOT
            # busy-retry every tick, or a down node gets hammered ~10x/sec with no backoff.
            if now - self._bg_last_net > 5.0:
                self._bg_last_net = now
                try:
                    ns = self.net_provider()
                    if ns:
                        self.ctx.net_stats = ns
                except Exception:
                    pass
            # 2) let the brain speak when the mixer is nearly idle; the (blocking) chat
            #    read + LLM completion + TTS all happen HERE, not on the render loop.
            if (self.line_provider is not None and self.mixer.backlog <= 1
                    and now - self._bg_last_think >= 0.4):
                self._bg_last_think = now
                try:
                    item = self.line_provider(self.ctx)
                except Exception as e:
                    self.log(f"[stream] line_provider error: {e}")
                    item = None
                if item and item.text:
                    try:
                        samples, _ = self.voice.synth(item.text)
                        self.mixer.enqueue(samples, {"text": item.text, "emotion": item.emotion,
                                                     "behavior": item.behavior, "source": item.source})
                    except Exception as e:
                        self.log(f"[stream] synth error: {e}")
            self._stop.wait(0.1)

    def _inline_brain(self, t: float):
        """Preview-only: run net-stats + brain/TTS synchronously on the VIDEO timeline (t is
        video-seconds). Previews render as fast as the CPU allows, so pacing speech by wall
        clock (the live _bg_loop) would leave them near-silent; keying off t keeps voice and
        subtitles landing at the right frames. Blocking here is fine — previews aren't live."""
        if t - self._pv_last_net > 5.0:
            self._pv_last_net = t
            try:
                ns = self.net_provider()
                if ns:
                    self.ctx.net_stats = ns
            except Exception:
                pass
        if (self.line_provider is not None and self.mixer.backlog <= 1
                and t - self._pv_last_think >= 0.4):
            self._pv_last_think = t
            try:
                item = self.line_provider(self.ctx)
            except Exception as e:
                self.log(f"[stream] line_provider error: {e}")
                item = None
            if item and item.text:
                try:
                    samples, _ = self.voice.synth(item.text)
                    self.mixer.enqueue(samples, {"text": item.text, "emotion": item.emotion,
                                                 "behavior": item.behavior, "source": item.source})
                except Exception as e:
                    self.log(f"[stream] synth error: {e}")

    def _update_ctx(self, t: float):
        # net_stats is refreshed by the background worker; never block the render loop on I/O.
        self.ctx.uptime_s = time.monotonic() - self._started
        self.ctx.daytime = (t / 3600.0) % 1.0

    # ── ffmpeg command ───────────────────────────────────────────────────────
    def _ffmpeg_cmd(self, ff: str, vfifo: str, afifo: str, max_seconds: float):
        c = self.cfg
        # Live must sustain real-time while sharing the box with the canonical node, so the
        # encode has to be cheap: ultrafast keeps libx264 well under one core at 540p. A
        # local preview/VOD render is not real-time bound, so it can afford veryfast quality.
        preset = "veryfast" if c.preview_path else "ultrafast"
        v = [
            ff, "-hide_banner", "-loglevel", "warning", "-y",
            # analyzeduration/probesize 0/32: raw inputs need no probing; without this
            # ffmpeg tries to buffer a ~5s analyze window of raw video and deadlocks.
            "-thread_queue_size", "512", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{c.width}x{c.height}", "-r", str(c.fps),
            "-analyzeduration", "0", "-probesize", "32", "-i", vfifo,
            "-thread_queue_size", "512", "-f", "s16le", "-ar", str(c.audio_rate), "-ac", "2",
            "-analyzeduration", "0", "-probesize", "32", "-i", afifo,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", preset,
            "-b:v", f"{c.bitrate_k}k", "-maxrate", f"{c.bitrate_k}k",
            "-bufsize", f"{2 * c.bitrate_k}k", "-g", str(c.fps * 2),
            "-c:a", "aac", "-b:a", "160k", "-ar", str(c.audio_rate),
        ]
        if c.preview_path:
            if max_seconds:
                v += ["-t", str(max_seconds)]
            v += ["-movflags", "+faststart", c.preview_path]
            return v
        # LIVE: encode once, tee to RTMP + a segmented 1-hour recording for VOD upload.
        # The tee muxer does NOT auto-select streams the way a normal output does, so we
        # must map the encoded video+audio explicitly — otherwise ffmpeg opens the tee
        # with zero streams ("Output file does not contain any stream") and exits.
        outs = []
        if c.rtmp_url:
            outs.append(f"[f=flv]{c.rtmp_url}")
        if c.record_dir:
            os.makedirs(c.record_dir, exist_ok=True)
            seg = os.path.join(c.record_dir, "seg_%05d.mp4")
            outs.append(f"[f=segment:segment_time={c.segment_seconds}:reset_timestamps=1:segment_format=mp4]{seg}")
        v += ["-map", "0:v:0", "-map", "1:a:0", "-f", "tee", "|".join(outs)]
        return v

    @staticmethod
    def _redact(cmd):
        out = []
        for a in cmd:
            out.append(a.split("/live2/")[0] + "/live2/****" if "rtmp" in a and "/live2/" in a else a)
        return out
