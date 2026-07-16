"""Upload the 24/7 recording to YouTube in 1-hour VOD chunks.

ffmpeg writes the live recording as `seg_00000.mp4`, `seg_00001.mp4`, … (one hour each,
via the segment muxer). A segment is complete once ffmpeg has started the next one, so
this thread uploads every segment except the newest, deletes each after a successful
upload, and flushes the final segment on stop.
"""
from __future__ import annotations

import os
import re
import threading
import time

_SEG = re.compile(r"seg_\d+\.mp4$")


class SegmentUploader(threading.Thread):
    def __init__(self, yt, record_dir: str, char, log=print, privacy: str = "public"):
        super().__init__(daemon=True)
        self.yt = yt
        self.dir = record_dir
        self.char = char
        self.log = log
        self.privacy = privacy
        self._stop = threading.Event()
        self._done: set = set()
        self._episode = 0

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._sweep(final=False)
            except Exception as e:
                self.log(f"[segments] sweep error: {e}")
            self._stop.wait(30)
        time.sleep(2)                      # let ffmpeg flush the last segment
        try:
            self._sweep(final=True)
        except Exception as e:
            self.log(f"[segments] final sweep error: {e}")

    def _segments(self):
        try:
            return sorted(f for f in os.listdir(self.dir) if _SEG.match(f))
        except FileNotFoundError:
            return []

    def _sweep(self, final: bool):
        segs = self._segments()
        ready = segs if final else segs[:-1]     # skip the segment still being written
        for f in ready:
            if f in self._done:
                continue
            path = os.path.join(self.dir, f)
            if not os.path.exists(path) or os.path.getsize(path) < 4096:
                continue
            self._episode += 1
            title = f"{self.char.name} — Animica Live · part {self._episode}"
            desc = ("An auto-recorded hour from the Animica Animal 24/7 interactive AI livestream. "
                    "AI-generated / synthetic content. Watch live: https://animica.dev/animal")
            try:
                self.yt.upload_video(path, title, desc, privacy=self.privacy)
                self._done.add(f)
                try:
                    os.remove(path)
                except OSError:
                    pass
            except Exception as e:
                self.log(f"[segments] upload failed for {f} (will retry): {e}")
