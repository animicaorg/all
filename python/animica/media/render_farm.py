"""Blender render farm — miner side (9.0.0 GPU Studios, docs/gpu-studios-9.0.0.md).

A render_blender submit is split by the gateway into render_chunk jobs (frame ranges of
one packed .blend) and a final render_assemble job. This module is everything a miner
needs to serve those kinds:

  * resolve_blender()  — find Blender: PATH -> ANIMICA_BLENDER -> ~/.animica/tools cache
                          -> sha256-pinned auto-download of blender-4.2.9-linux-x64
  * split_frames()     — the pure chunking math (the gateway mirrors this exactly)
  * render_chunk()     — headless Cycles render of an inclusive frame range -> PNG zip
  * assemble_video()   — chunk zips -> final MP4 (or one consolidated frames zip)

SECURITY: uploaded .blend files are untrusted. Blender is ALWAYS invoked with
``--disable-autoexec`` placed BEFORE the file on the command line (arguments execute in
order; the file loads the moment it is encountered), so embedded Python scripts and
driver expressions can never auto-run on a miner. Fail-closed throughout: every function
returns a validated artifact or raises MediaError — never a stub.
"""

from __future__ import annotations

import hashlib
import os
import queue
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from collections import deque
from pathlib import Path
from typing import Callable, Optional, Sequence

from .base import MediaError, sha3_hex, validate_magic  # noqa: F401 (sha3_hex is API here)

# ── pinned Blender distribution ──────────────────────────────────────────────
BLENDER_VERSION = "4.2.9"
BLENDER_DIRNAME = f"blender-{BLENDER_VERSION}-linux-x64"
BLENDER_URL = ("https://download.blender.org/release/Blender4.2/"
               f"{BLENDER_DIRNAME}.tar.xz")
BLENDER_SHA256 = "dfbc127a7d28f9c2175b23bf9d6701b2855f31eedfb391f9a6e60adb24572846"

# Verified against the real blender-4.2.9 `--help` output: 4.2 ships the classic
# single-dash ``-noaudio`` ("Force sound system to None"); there is no --no-audio
# spelling, and an unknown argument makes Blender try to open it as a file. `-b`
# already disables the audio device by default, but we force it anyway — a .blend
# saved with '-setaudio'-style prefs must never probe sound hardware on a miner.
NO_AUDIO_FLAG = "-noaudio"

MAX_TOTAL_FRAMES = 2000          # mirrors the gateway's render_blender clamp
_MAX_FRAME_NUMBER = 99999        # keeps frame_##### names uniformly 5 digits wide
_DL_CHUNK = 1024 * 1024

_FRA_RE = re.compile(r"^Fra:(\d+)\b")
_FRAME_NAME_RE = re.compile(r"^frame_(\d+)\.png$")


# ── Blender resolution ───────────────────────────────────────────────────────

def tools_dir() -> Path:
    return Path.home() / ".animica" / "tools"


_BLENDER_EXE: Optional[str] = None


def resolve_blender(auto_fetch: bool = True) -> Optional[str]:
    """Return a usable Blender executable path, or None.

    Order: system ``blender`` on PATH -> ``ANIMICA_BLENDER`` env override -> the
    per-user cache ``~/.animica/tools/blender-4.2.9-linux-x64/blender`` -> (Linux
    x86_64 only, unless ``ANIMICA_BLENDER_AUTOFETCH=0``) a streamed download of the
    official sha256-pinned tarball, safe-extracted and atomically renamed into the
    cache. Only a successful resolution is cached for the process, so an early
    ``auto_fetch=False`` miss never poisons a later fetch.
    """
    global _BLENDER_EXE
    if _BLENDER_EXE is not None:
        return _BLENDER_EXE

    exe = shutil.which("blender")
    if exe:
        _BLENDER_EXE = exe
        return exe

    env = os.environ.get("ANIMICA_BLENDER")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        _BLENDER_EXE = env
        return env

    cached = tools_dir() / BLENDER_DIRNAME / "blender"
    if cached.is_file() and os.access(cached, os.X_OK):
        _BLENDER_EXE = str(cached)
        return _BLENDER_EXE

    if not auto_fetch or os.environ.get("ANIMICA_BLENDER_AUTOFETCH") == "0":
        return None
    import platform
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        return None  # the pinned tarball is linux-x64 only; other hosts must bring their own

    _BLENDER_EXE = _fetch_blender()
    return _BLENDER_EXE


def _fetch_blender() -> str:
    """Download + verify + extract the pinned Blender into ~/.animica/tools. Fail-closed."""
    tools = tools_dir()
    tools.mkdir(parents=True, exist_ok=True)
    final = tools / BLENDER_DIRNAME

    fd, tarball = tempfile.mkstemp(dir=tools, prefix=".blender-dl-", suffix=".tar.xz.part")
    h = hashlib.sha256()
    try:
        try:
            with os.fdopen(fd, "wb") as f:
                req = urllib.request.Request(
                    BLENDER_URL, headers={"User-Agent": f"animica-render-farm/{BLENDER_VERSION}"})
                with urllib.request.urlopen(req, timeout=120) as r:  # nosec B310 - pinned https URL
                    while True:
                        chunk = r.read(_DL_CHUNK)
                        if not chunk:
                            break
                        h.update(chunk)
                        f.write(chunk)
        except Exception as e:
            raise MediaError(f"blender download failed: {e}") from e

        digest = h.hexdigest()
        if digest != BLENDER_SHA256:
            # the tarball is deleted by the finally: below — never keep a bad archive
            raise MediaError(
                f"blender tarball sha256 mismatch: got {digest}, want {BLENDER_SHA256}")

        # Extract into a temp dir on the SAME filesystem, then atomically rename the
        # top-level directory into place — a crash mid-extract never leaves a
        # half-populated cache that a later resolve would trust.
        tmpdir = Path(tempfile.mkdtemp(dir=tools, prefix=".blender-extract-"))
        try:
            try:
                with tarfile.open(tarball, mode="r:xz") as tar:
                    _safe_extract_tar(tar, tmpdir)
            except MediaError:
                raise
            except Exception as e:
                raise MediaError(f"blender tarball extraction failed: {e}") from e
            src = tmpdir / BLENDER_DIRNAME
            if not (src / "blender").is_file():
                raise MediaError("blender tarball did not contain the expected layout")
            try:
                os.rename(src, final)
            except OSError:
                if not (final / "blender").is_file():  # lost a race to another process: fine
                    raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    finally:
        try:
            os.unlink(tarball)
        except OSError:
            pass

    exe = final / "blender"
    if not (exe.is_file() and os.access(exe, os.X_OK)):
        raise MediaError("blender extraction did not produce an executable")
    return str(exe)


# ── safe archive extraction (path-traversal guarded, checkpoints._safe_extract style) ──

def _safe_extract_tar(tar: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if target != dest and not str(target).startswith(str(dest) + os.sep):
            raise MediaError(f"unsafe path in tarball: {m.name}")
        if m.issym() or m.islnk():
            if os.path.isabs(m.linkname):
                raise MediaError(f"unsafe absolute link in tarball: {m.name} -> {m.linkname}")
            ltarget = Path(os.path.normpath(dest / os.path.dirname(m.name) / m.linkname))
            if ltarget != dest and not str(ltarget).startswith(str(dest) + os.sep):
                raise MediaError(f"unsafe link target in tarball: {m.name} -> {m.linkname}")
    try:
        tar.extractall(dest, filter="tar")  # nosec B202 - members vetted above
    except TypeError:  # Python < 3.12 has no extraction filters
        tar.extractall(dest)  # nosec B202


_ASSEMBLE_MAX_ENTRIES = 4096
_ZIP_CHUNK = 256 * 1024


def _assemble_max_bytes() -> int:
    try:
        v = int(os.environ.get("ANIMICA_RENDER_ASSEMBLE_MAX_BYTES") or 6 * 1024 ** 3)
    except (TypeError, ValueError):
        v = 6 * 1024 ** 3
    return max(1024 * 1024, v)


class _ExtractBudget:
    """Decompression budget shared across every chunk zip of one assemble call.

    Chunk zips come from OTHER miners — hostile input. Zip headers (file_size) are
    attacker-controlled, so the budget is enforced while STREAMING each member out:
    writing stops the moment the running uncompressed total would exceed the cap
    (ANIMICA_RENDER_ASSEMBLE_MAX_BYTES, default 6 GiB) or the total entry count
    passes _ASSEMBLE_MAX_ENTRIES (4096) — never after the disk is already full.
    """

    def __init__(self, max_bytes: Optional[int] = None,
                 max_entries: int = _ASSEMBLE_MAX_ENTRIES):
        self.remaining = _assemble_max_bytes() if max_bytes is None else int(max_bytes)
        self.entries = int(max_entries)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path,
                      budget: Optional[_ExtractBudget] = None) -> None:
    """Traversal-guarded, budget-bounded zip extraction (see _ExtractBudget)."""
    dest = dest.resolve()
    budget = budget if budget is not None else _ExtractBudget()
    infos = zf.infolist()
    for info in infos:
        target = (dest / info.filename).resolve()
        if target != dest and not str(target).startswith(str(dest) + os.sep):
            raise MediaError(f"unsafe path in zip: {info.filename}")
    for info in infos:
        budget.entries -= 1
        if budget.entries < 0:
            raise MediaError(
                f"zip bomb rejected: more than {_ASSEMBLE_MAX_ENTRIES} entries across chunks")
        target = (dest / info.filename).resolve()
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(_ZIP_CHUNK)
                    if not chunk:
                        break
                    budget.remaining -= len(chunk)
                    if budget.remaining < 0:
                        raise MediaError(
                            "zip bomb rejected: decompressed size exceeds the assemble "
                            "budget (ANIMICA_RENDER_ASSEMBLE_MAX_BYTES)")
                    out.write(chunk)
        except MediaError:
            try:
                os.unlink(target)  # never leave a partial blob behind
            except OSError:
                pass
            raise
        except Exception as ex:
            try:
                os.unlink(target)
            except OSError:
                pass
            raise MediaError(f"zip extraction failed on {info.filename}: {ex}") from ex


# ── pure chunking math (the gateway mirrors this exactly) ────────────────────

def split_frames(frame_start: int, frame_end: int, frame_step: int = 1,
                 chunk_frames: int = 20) -> "list[tuple[int, int]]":
    """Split an inclusive, stepped frame range into inclusive (start, end) chunks.

    Each chunk covers at most ``chunk_frames`` frames ON THE STEPPED GRID, and both
    bounds of every chunk are real frames of the sequence — so rendering s..e with the
    same step reproduces exactly those frames, chunk by chunk, with no overlap.
    """
    s, e, j, c = int(frame_start), int(frame_end), int(frame_step), int(chunk_frames)
    if j < 1:
        raise MediaError(f"frame_step must be >= 1 (got {frame_step})")
    if c < 1:
        raise MediaError(f"chunk_frames must be >= 1 (got {chunk_frames})")
    if e < s:
        raise MediaError(f"frame_end {e} is before frame_start {s}")
    total = (e - s) // j + 1
    if total > MAX_TOTAL_FRAMES:
        raise MediaError(f"{total} frames exceeds the {MAX_TOTAL_FRAMES}-frame cap")
    frames = list(range(s, e + 1, j))
    return [(frames[i], frames[min(i + c, total) - 1]) for i in range(0, total, c)]


# ── headless render invocation ───────────────────────────────────────────────

def _clamp(v, lo: int, hi: int) -> int:
    return max(lo, min(int(v), hi))


def _render_timeout() -> float:
    try:
        v = float(os.environ.get("ANIMICA_RENDER_TIMEOUT") or 3300.0)
    except (TypeError, ValueError):
        v = 3300.0
    return max(60.0, v)


def _python_expr(resolution_percent: int, samples: Optional[int]) -> str:
    """The --python-expr run AFTER the file loads: clamp resolution/samples, then try
    the GPU ladder OPTIX -> CUDA (enable all devices, cycles.device='GPU'), falling
    back to CPU. Every step is inside try/except — a render must never die because a
    box has no GPU. Prints ANM_RENDER_DEVICE=<OPTIX|CUDA|CPU> for the job meta."""
    rp = _clamp(resolution_percent, 25, 200)
    lines = [
        "import bpy",
        "sc = bpy.context.scene",
        "sc.render.engine = 'CYCLES'",
        f"sc.render.resolution_percentage = {rp}",
    ]
    if samples is not None:
        lines.append(f"sc.cycles.samples = {_clamp(samples, 16, 2048)}")
    lines += [
        "dev = 'CPU'",
        "try:",
        "    prefs = bpy.context.preferences.addons['cycles'].preferences",
        "    for dt in ('OPTIX', 'CUDA'):",
        "        try:",
        "            prefs.compute_device_type = dt",
        "            try:",
        "                prefs.refresh_devices()",
        "            except Exception:",
        "                prefs.get_devices()",
        "            if not [d for d in prefs.devices if getattr(d, 'type', '') == dt]:",
        "                continue",
        "            for d in prefs.devices:",
        "                d.use = True",
        "            sc.cycles.device = 'GPU'",
        "            dev = dt",
        "            break",
        "        except Exception:",
        "            continue",
        "except Exception:",
        "    pass",
        "if dev == 'CPU':",
        "    try:",
        "        sc.cycles.device = 'CPU'",
        "    except Exception:",
        "        pass",
        "print('ANM_RENDER_DEVICE=' + dev)",
    ]
    return "\n".join(lines)


def _blender_argv(exe: str, blend_path: str, out_prefix: str, *,
                  frame_start: int, frame_end: int, frame_step: int = 1,
                  resolution_percent: int = 100,
                  samples: Optional[int] = None) -> "list[str]":
    """Build the headless render command.

    Blender executes CLI arguments IN ORDER: ``--disable-autoexec`` and the audio kill
    must come BEFORE the .blend path (the file is loaded the moment it is encountered),
    while -E/--python-expr/-o/-F/-s/-e/-j must come AFTER it (settings stored in the
    file would otherwise override them) and -s/-e/-j must precede -a.
    """
    argv = [
        exe, "-b", "--disable-autoexec", NO_AUDIO_FLAG, blend_path,
        "-E", "CYCLES",
        "--python-expr", _python_expr(resolution_percent, samples),
        "-o", out_prefix, "-F", "PNG",
        "-s", str(int(frame_start)), "-e", str(int(frame_end)),
        "-j", str(int(frame_step)),
        "-a",
    ]
    # SECURITY — NON-NEGOTIABLE: user .blend files are untrusted; embedded scripts and
    # driver expressions must never auto-run on a miner. These asserts make the
    # invariant unremovable by refactor: every invocation this module can ever produce
    # carries --disable-autoexec, placed before the file it guards.
    assert "--disable-autoexec" in argv
    assert argv.index("--disable-autoexec") < argv.index(blend_path)
    assert argv[argv.index("-E") + 1] == "CYCLES" and argv[-1] == "-a"
    return argv


def _frames_done(line: str, frames: Sequence[int], prev_done: int) -> Optional[int]:
    """Progress from one blender stdout line. 'Fra:N ...' means frame N is being
    worked on, so every expected frame < N has completed. Returns the new done-count
    (monotonic, never below prev_done), or None for a non-Fra line."""
    m = _FRA_RE.match(line)
    if not m:
        return None
    n = int(m.group(1))
    return max(prev_done, sum(1 for f in frames if f < n))


def _notify(progress: Optional[Callable], pct: float) -> None:
    if progress is None:
        return
    try:
        progress(pct)
    except Exception:
        pass  # a broken progress sink must never kill a render


def _sha3_file(path: str) -> str:
    h = hashlib.sha3_256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


_BLEND_MAGIC = (b"BLENDER", b"\x28\xb5\x2f\xfd", b"\x1f\x8b")  # raw, zstd-packed, legacy gzip


def render_chunk(blend_path: str, out_dir: str, *, frame_start: int, frame_end: int,
                 frame_step: int = 1, resolution_percent: int = 100,
                 samples: Optional[int] = None,
                 progress: Optional[Callable] = None) -> dict:
    """Render an inclusive frame range of a .blend headlessly -> zip of PNG frames.

    Returns {"path": <zip>, "mime": "application/zip", "sha3", "meta"}. Fail-closed:
    every expected frame must exist and carry PNG magic, or MediaError. Subprocess is
    bounded by ANIMICA_RENDER_TIMEOUT (default 3300 s) and killed on overrun.
    """
    t0 = time.time()
    # Validate the inputs BEFORE resolving Blender: a garbage job must fail fast and
    # must never be the thing that triggers a ~300 MB auto-fetch.
    if not os.path.isfile(blend_path):
        raise MediaError(f"blend file not found: {blend_path}")
    with open(blend_path, "rb") as f:
        head = f.read(16)
    if not any(head.startswith(m) for m in _BLEND_MAGIC):
        raise MediaError("input is not a .blend file")

    s, e, j = int(frame_start), int(frame_end), int(frame_step)
    if j < 1:
        raise MediaError(f"frame_step must be >= 1 (got {frame_step})")
    if e < s:
        raise MediaError(f"frame_end {e} is before frame_start {s}")
    if s < 0 or e > _MAX_FRAME_NUMBER:
        raise MediaError(f"frame numbers must be within 0..{_MAX_FRAME_NUMBER}")
    frames = list(range(s, e + 1, j))
    if len(frames) > MAX_TOTAL_FRAMES:
        raise MediaError(f"{len(frames)} frames exceeds the {MAX_TOTAL_FRAMES}-frame cap")

    exe = resolve_blender()
    if not exe:
        raise MediaError("blender unavailable (PATH, ANIMICA_BLENDER and auto-fetch all failed)")
    os.makedirs(out_dir, exist_ok=True)
    timeout = _render_timeout()
    total = len(frames)

    with tempfile.TemporaryDirectory(prefix="anmrender_") as td:
        out_prefix = os.path.join(td, "frame_#####")
        argv = _blender_argv(exe, blend_path, out_prefix,
                             frame_start=s, frame_end=e, frame_step=j,
                             resolution_percent=resolution_percent, samples=samples)

        tail: "deque[str]" = deque(maxlen=60)
        device = "unknown"
        done = 0
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1, cwd=td)
        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def _pump():
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    q.put(line)
            finally:
                q.put(None)

        threading.Thread(target=_pump, daemon=True).start()
        deadline = time.monotonic() + timeout
        try:
            while True:
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait(timeout=30)
                    raise MediaError(
                        f"blender render timed out after {timeout:.0f}s (frames {s}-{e}); "
                        "raise ANIMICA_RENDER_TIMEOUT if the scene legitimately needs longer")
                try:
                    line = q.get(timeout=1.0)
                except queue.Empty:
                    continue
                if line is None:
                    break
                line = line.rstrip("\n")
                tail.append(line)
                if line.startswith("ANM_RENDER_DEVICE="):
                    device = line.split("=", 1)[1].strip() or "unknown"
                nd = _frames_done(line, frames, done)
                if nd is not None and nd != done:
                    done = nd
                    _notify(progress, done / total * 100.0)
            rc = proc.wait(timeout=max(5.0, deadline - time.monotonic()))
        except MediaError:
            raise
        except Exception as ex:
            proc.kill()
            raise MediaError(f"blender render failed: {ex}") from ex
        finally:
            if proc.poll() is None:
                proc.kill()
        if rc != 0:
            raise MediaError(
                f"blender exited with {rc}: " + " | ".join(list(tail)[-8:])[-500:])

        # Fail-closed frame validation: every expected frame, present + real PNG.
        rendered: "list[tuple[int, str]]" = []
        for n in frames:
            p = os.path.join(td, f"frame_{n:05d}.png")
            ok = os.path.isfile(p) and os.path.getsize(p) >= 12
            if ok:
                with open(p, "rb") as f:
                    ok = validate_magic(f.read(16), "png")
            if not ok:
                raise MediaError(
                    f"blender produced no valid PNG for frame {n}: "
                    + " | ".join(list(tail)[-5:])[-300:])
            rendered.append((n, p))

        zip_path = os.path.join(out_dir, f"chunk_{s:05d}_{e:05d}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:  # PNGs are pre-compressed
            for n, p in rendered:
                zf.write(p, arcname=f"frame_{n:05d}.png")  # stable, globally sortable names

    _notify(progress, 100.0)
    meta = {
        "kind": "render_chunk", "engine": "CYCLES", "device": device,
        "blender": BLENDER_VERSION, "frames": total,
        "frame_start": s, "frame_end": e, "frame_step": j,
        "resolution_percent": _clamp(resolution_percent, 25, 200),
        "samples": _clamp(samples, 16, 2048) if samples is not None else None,
        "seconds": round(time.time() - t0, 2),
    }
    return {"path": zip_path, "mime": "application/zip", "sha3": _sha3_file(zip_path),
            "meta": meta}


# ── final assembly ───────────────────────────────────────────────────────────

def assemble_video(chunk_zip_paths: Sequence[str], out_dir: str, *, fps: int = 24,
                   mode: str = "mp4", progress: Optional[Callable] = None) -> dict:
    """Merge chunk zips into the final artifact: mode 'mp4' -> H.264 MP4 at ``fps``,
    mode 'zip' -> one consolidated frames zip. MediaError on empty input, duplicate
    frames, non-uniform stride (a lost chunk shows up as a gap), or a bad encode.

    Chunk zips were produced by OTHER miners and are treated as hostile: extraction
    is traversal-guarded AND bounded by one shared decompression budget across all
    zips (bytes + entry count, enforced while streaming — see _ExtractBudget)."""
    paths = [p for p in (chunk_zip_paths or []) if p]
    if not paths:
        raise MediaError("no chunk zips to assemble")
    mode = (mode or "mp4").strip().lower()
    if mode not in ("mp4", "zip"):
        raise MediaError(f"unknown assemble mode: {mode!r} (want mp4|zip)")
    fps = _clamp(fps, 6, 60)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    with tempfile.TemporaryDirectory(prefix="anmassemble_") as td:
        frames: "dict[int, str]" = {}
        budget = _ExtractBudget()  # ONE budget across all chunk zips of this assemble
        for i, zp in enumerate(paths):
            if not os.path.isfile(zp):
                raise MediaError(f"chunk zip missing: {zp}")
            with open(zp, "rb") as f:
                if not validate_magic(f.read(16), "zip"):
                    raise MediaError(f"chunk is not a zip: {zp}")
            sub = Path(td) / f"chunk_{i:03d}"
            sub.mkdir()
            try:
                with zipfile.ZipFile(zp) as zf:
                    _safe_extract_zip(zf, sub, budget)
            except MediaError:
                raise
            except Exception as ex:
                raise MediaError(f"bad chunk zip {zp}: {ex}") from ex
            for p in sorted(sub.rglob("frame_*.png")):
                m = _FRAME_NAME_RE.match(p.name)
                if not m:
                    continue
                n = int(m.group(1))
                if n in frames:
                    raise MediaError(f"duplicate frame {n} across chunks")
                with open(p, "rb") as f:
                    if not validate_magic(f.read(16), "png"):
                        raise MediaError(f"frame {n} in {zp} is not a valid PNG")
                frames[n] = str(p)
            _notify(progress, (i + 1) / len(paths) * 40.0)

        if not frames:
            raise MediaError("chunk zips contained no frames")
        order = sorted(frames)
        if len(order) > 1:
            strides = {b - a for a, b in zip(order, order[1:])}
            if len(strides) != 1:
                raise MediaError(
                    f"frame sequence has gaps (mixed strides {sorted(strides)}) — "
                    "refusing to assemble an incomplete render")

        if mode == "zip":
            out_path = os.path.join(out_dir, "frames.zip")
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as zf:
                for n in order:
                    zf.write(frames[n], arcname=f"frame_{n:05d}.png")
            mime = "application/zip"
        else:
            from .base import resolve_ffmpeg
            exe = resolve_ffmpeg()
            if not exe:
                raise MediaError("ffmpeg not available for mp4 assembly")
            # Renumber into a dense 1..N sequence (hardlink, same tmp fs) so ffmpeg's
            # image2 demuxer accepts any frame_start/step combination.
            seq = Path(td) / "seq"
            seq.mkdir()
            for i, n in enumerate(order, start=1):
                dst = seq / f"seq_{i:06d}.png"
                try:
                    os.link(frames[n], dst)
                except OSError:
                    shutil.copyfile(frames[n], dst)
            _notify(progress, 50.0)
            out_path = os.path.join(out_dir, "render.mp4")
            cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
                   "-framerate", str(fps), "-start_number", "1",
                   "-i", str(seq / "seq_%06d.png"),
                   "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # yuv420p needs even dims
                   "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
            proc = subprocess.run(cmd, capture_output=True, timeout=_render_timeout())
            if proc.returncode != 0:
                raise MediaError(
                    f"ffmpeg assembly failed: {proc.stderr.decode(errors='replace')[-400:]}")
            with open(out_path, "rb") as f:
                head = f.read(64)
            if not validate_magic(head, "mp4"):
                raise MediaError("assembled output is not a valid MP4")
            mime = "video/mp4"

    _notify(progress, 100.0)
    meta = {
        "kind": "render_assemble", "mode": mode, "chunks": len(paths),
        "frames": len(order), "fps": fps,
        "duration_s": round(len(order) / fps, 2) if mode == "mp4" else None,
        "seconds": round(time.time() - t0, 2),
    }
    return {"path": out_path, "mime": mime, "sha3": _sha3_file(out_path), "meta": meta}
