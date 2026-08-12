"""Render-farm plumbing tests (CPU, no network — Blender is NEVER downloaded here).

Run from the repo root:
  cd /root/animica && .venv/bin/python -m pytest python/animica/media/test_render_farm.py -q
Or with the media venv + shared torch on PYTHONPATH (recipe from test_media.py):
  PYTHONPATH=/root/animica/python:/root/animica/.venv/lib/python3.12/site-packages \
  /root/animica/.venv-media/bin/python -m pytest animica/media/test_render_farm.py -q
Or as a plain script: .venv/bin/python python/animica/media/test_render_farm.py

The real-render smoke test only runs when a local Blender is already resolvable
without fetching (auto_fetch=False); everything else is pure plumbing.
"""

import io
import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from animica.media.base import MediaError, validate_magic
from animica.media.render_farm import (
    BLENDER_SHA256, BLENDER_URL, MAX_TOTAL_FRAMES, NO_AUDIO_FLAG,
    _blender_argv, _ExtractBudget, _frames_done, _python_expr,
    _safe_extract_tar, _safe_extract_zip,
    assemble_video, render_chunk, resolve_blender, split_frames,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _png_bytes(w=32, h=24, color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _chunk_zip(path, frame_numbers, payload=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for n in frame_numbers:
            data = payload if payload is not None else _png_bytes(color=((n * 41) % 255, 80, 120))
            zf.writestr(f"frame_{n:05d}.png", data)
    return path


# ── split_frames (server mirrors this math) ──────────────────────────────────

def test_split_frames_basic():
    assert split_frames(1, 100, 1, 20) == [(1, 20), (21, 40), (41, 60), (61, 80), (81, 100)]
    assert split_frames(5, 5, 1) == [(5, 5)]                       # single frame
    assert split_frames(1, 40, 1, 20) == [(1, 20), (21, 40)]        # exact multiple
    assert split_frames(1, 41, 1, 20) == [(1, 20), (21, 40), (41, 41)]


def test_split_frames_step():
    # frames on the stepped grid: 1,4,7,10,13,16,19 — chunks of 3 grid frames
    assert split_frames(1, 20, 3, 3) == [(1, 7), (10, 16), (19, 19)]
    # bounds of every chunk are real frames, so re-rendering (s, e, step) is exact
    for s, e in split_frames(10, 99, 7, 4):
        assert (s - 10) % 7 == 0 and (e - 10) % 7 == 0 and s <= e
    assert split_frames(2, 3, 10) == [(2, 2)]                       # step overshoots end


def test_split_frames_guards():
    assert len(split_frames(1, MAX_TOTAL_FRAMES, 1, 20)) == 100     # exactly at the cap
    for bad in ((1, MAX_TOTAL_FRAMES + 1, 1, 20),                   # over the 2000 cap
                (10, 9, 1, 20),                                     # end before start
                (1, 10, 0, 20),                                     # step < 1
                (1, 10, 1, 0)):                                     # chunk_frames < 1
        try:
            split_frames(*bad)
            assert False, f"split_frames{bad} must fail"
        except MediaError:
            pass


# ── argv builder (SECURITY: --disable-autoexec is non-negotiable) ────────────

def test_argv_builder_security_every_combination():
    blend = "/uploads/untrusted.blend"
    for s, e, j in ((1, 1, 1), (1, 20, 1), (21, 40, 1), (1, 100, 5), (0, 7, 3)):
        for rp in (25, 100, 200):
            for samples in (None, 16, 128, 2048):
                argv = _blender_argv("/opt/blender", blend, "/tmp/x/frame_#####",
                                     frame_start=s, frame_end=e, frame_step=j,
                                     resolution_percent=rp, samples=samples)
                # untrusted .blend: autoexec must be disabled BEFORE the file loads
                assert "--disable-autoexec" in argv
                assert argv.index("--disable-autoexec") < argv.index(blend)
                assert NO_AUDIO_FLAG in argv and argv.index(NO_AUDIO_FLAG) < argv.index(blend)
                assert argv[1] == "-b"                              # headless, always
                # engine forced to CYCLES, after the file (scene settings can't override)
                assert argv[argv.index("-E") + 1] == "CYCLES"
                assert argv.index(blend) < argv.index("-E")
                # frame range flags precede -a, which is last
                assert argv[argv.index("-s") + 1] == str(s)
                assert argv[argv.index("-e") + 1] == str(e)
                assert argv[argv.index("-j") + 1] == str(j)
                assert argv[argv.index("-F") + 1] == "PNG"
                assert argv[-1] == "-a"
                assert "--python-expr" in argv


def test_python_expr_clamps_and_gpu_ladder():
    expr = _python_expr(300, 8)                                     # both out of range
    assert "resolution_percentage = 200" in expr                    # 25..200 clamp
    assert "cycles.samples = 16" in expr                            # 16..2048 clamp
    assert "'OPTIX', 'CUDA'" in expr and "sc.cycles.device = 'GPU'" in expr
    assert "except Exception" in expr                               # CPU fallback path
    assert "ANM_RENDER_DEVICE" in expr
    low = _python_expr(1, None)
    assert "resolution_percentage = 25" in low and "cycles.samples" not in low
    assert "cycles.samples = 2048" in _python_expr(100, 99999)


# ── progress parser on canned blender stdout ─────────────────────────────────

def test_progress_parser_canned_stdout():
    canned = [
        ("Blender 4.2.9 LTS", None),
        ("Fra:1 Mem:27.53M (Peak 27.60M) | Time:00:00.10 | Scene, ViewLayer "
         "| Synchronizing object | Cube", 0),
        ("Fra:1 Mem:34.90M (Peak 40.68M) | Time:00:00.55 | Sample 8/8", 0),
        ("Saved: '/tmp/x/frame_00001.png'", None),
        ("Fra:2 Mem:27.53M | Time:00:00.60 | Scene, ViewLayer | Updating Scene", 1),
        ("ANM_RENDER_DEVICE=CPU", None),
        ("Blender quit", None),
    ]
    frames, done = [1, 2], 0
    for line, expect in canned:
        nd = _frames_done(line, frames, done)
        assert nd == expect, f"{line!r}: got {nd}, want {expect}"
        if nd is not None:
            done = nd
    assert done == 1                       # frame 2 completion is signalled by exit 0
    # stepped grid: Fra:7 means frames 1 and 4 are done
    assert _frames_done("Fra:7 Mem:1M | rendering", [1, 4, 7], 0) == 2
    # monotonic: a late out-of-order Fra:1 can never move progress backwards
    assert _frames_done("Fra:1 Mem:1M", [1, 2], 1) == 1
    assert _frames_done("  Fra:9 indented is not a Fra line", [1, 2], 0) is None


# ── safe extraction (path-traversal guards) ──────────────────────────────────

def test_safe_extract_zip_rejects_traversal():
    with tempfile.TemporaryDirectory() as td:
        for evil in ("../evil.png", "/abs.png", "a/../../evil.png"):
            zp = os.path.join(td, "evil.zip")
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr(evil, b"boom")
            try:
                with zipfile.ZipFile(zp) as zf:
                    _safe_extract_zip(zf, Path(td) / "out")
                assert False, f"zip member {evil!r} must be rejected"
            except MediaError:
                pass
        assert not os.path.exists(os.path.join(td, "evil.png"))


def test_safe_extract_tar_rejects_traversal_and_bad_links():
    def tar_with(name, linkname=None):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name)
            if linkname is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = linkname
                tar.addfile(info)
            else:
                data = b"x"
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        return tarfile.open(fileobj=buf)

    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out"
        dest.mkdir()
        for name, link in (("../evil.txt", None), ("ok/../../evil.txt", None),
                           ("link", "/etc/passwd"), ("a/link", "../../../etc/passwd")):
            with tar_with(name, link) as tar:
                try:
                    _safe_extract_tar(tar, dest)
                    assert False, f"tar member {name!r} -> {link!r} must be rejected"
                except MediaError:
                    pass
        with tar_with("dir/inside.txt") as tar:                     # benign member passes
            _safe_extract_tar(tar, dest)
        assert (dest / "dir" / "inside.txt").is_file()


# ── assemble on synthetic chunk zips ─────────────────────────────────────────

def test_assemble_mp4_from_synthetic_chunks():
    with tempfile.TemporaryDirectory() as td:
        zips = [_chunk_zip(os.path.join(td, "a.zip"), [1, 2]),
                _chunk_zip(os.path.join(td, "b.zip"), [3, 4])]
        pcts = []
        out = assemble_video(zips, os.path.join(td, "out"), fps=8, mode="mp4",
                             progress=pcts.append)
        assert out["mime"] == "video/mp4"
        with open(out["path"], "rb") as f:
            data = f.read()
        assert validate_magic(data, "mp4") and len(data) > 100
        assert out["meta"]["frames"] == 4 and out["meta"]["fps"] == 8
        assert pcts == sorted(pcts) and pcts[-1] == 100.0           # monotonic → 100


def test_assemble_mp4_odd_dimensions_and_stepped_frames():
    # 33x27 exercises the even-dimension scale for yuv420p; stride 3 is uniform → OK
    with tempfile.TemporaryDirectory() as td:
        png = _png_bytes(33, 27, (10, 200, 90))
        zips = [_chunk_zip(os.path.join(td, "a.zip"), [1, 4], png),
                _chunk_zip(os.path.join(td, "b.zip"), [7], png)]
        out = assemble_video(zips, os.path.join(td, "out"), fps=6, mode="mp4")
        with open(out["path"], "rb") as f:
            assert validate_magic(f.read(64), "mp4")
        assert out["meta"]["frames"] == 3


def test_assemble_zip_mode_consolidates():
    with tempfile.TemporaryDirectory() as td:
        zips = [_chunk_zip(os.path.join(td, "b.zip"), [3, 4]),      # order-independent
                _chunk_zip(os.path.join(td, "a.zip"), [1, 2])]
        out = assemble_video(zips, os.path.join(td, "out"), mode="zip")
        assert out["mime"] == "application/zip"
        with zipfile.ZipFile(out["path"]) as zf:
            assert zf.namelist() == ["frame_00001.png", "frame_00002.png",
                                     "frame_00003.png", "frame_00004.png"]
            assert validate_magic(zf.read("frame_00003.png")[:16], "png")


def test_assemble_failclosed():
    with tempfile.TemporaryDirectory() as td:
        good = _chunk_zip(os.path.join(td, "good.zip"), [1, 2])
        cases = [
            ([], "mp4"),                                            # nothing to assemble
            ([good], "webm"),                                       # unknown mode
            ([os.path.join(td, "missing.zip")], "mp4"),             # zip not on disk
        ]
        for zips, mode in cases:
            try:
                assemble_video(zips, os.path.join(td, "out"), mode=mode)
                assert False, f"assemble_video({zips}, mode={mode}) must fail"
            except MediaError:
                pass
        # gap: frames 1,2 then 4 → mixed strides
        gap = _chunk_zip(os.path.join(td, "gap.zip"), [4])
        try:
            assemble_video([good, gap], os.path.join(td, "out"), mode="zip")
            assert False, "gapped frame sequence must fail"
        except MediaError:
            pass
        # duplicate frame across chunks
        dup = _chunk_zip(os.path.join(td, "dup.zip"), [2, 3])
        try:
            assemble_video([good, dup], os.path.join(td, "out"), mode="zip")
            assert False, "duplicate frames must fail"
        except MediaError:
            pass
        # a member that claims to be a frame but is not a PNG
        bad = _chunk_zip(os.path.join(td, "bad.zip"), [1], payload=b"not a png at all!!")
        try:
            assemble_video([bad], os.path.join(td, "out"), mode="zip")
            assert False, "non-PNG frame must fail"
        except MediaError:
            pass
        # a zip of zero frames (only unrelated members)
        empty = os.path.join(td, "empty.zip")
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("readme.txt", b"hi")
        try:
            assemble_video([empty], os.path.join(td, "out"), mode="zip")
            assert False, "frameless chunks must fail"
        except MediaError:
            pass


# ── zip-bomb defense (chunk zips come from OTHER miners — hostile input) ─────

def test_assemble_rejects_decompression_bomb():
    # 8 MiB of zeros deflates to a few KiB — headers are irrelevant, the budget is
    # enforced while streaming the member out.
    with tempfile.TemporaryDirectory() as td:
        bomb = os.path.join(td, "bomb.zip")
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("frame_00001.png", b"\x00" * (8 * 1024 * 1024))
        assert os.path.getsize(bomb) < 64 * 1024                    # really a bomb
        os.environ["ANIMICA_RENDER_ASSEMBLE_MAX_BYTES"] = str(2 * 1024 * 1024)
        try:
            assemble_video([bomb], os.path.join(td, "out"), mode="zip")
            assert False, "decompression bomb must be rejected"
        except MediaError as e:
            assert "bomb" in str(e) and "ANIMICA_RENDER_ASSEMBLE_MAX_BYTES" in str(e)
        finally:
            del os.environ["ANIMICA_RENDER_ASSEMBLE_MAX_BYTES"]


def test_assemble_rejects_entry_count_bomb():
    with tempfile.TemporaryDirectory() as td:
        bomb = os.path.join(td, "entries.zip")
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_STORED) as zf:
            for i in range(4200):                                   # > 4096 cap
                zf.writestr(f"junk_{i:05d}.txt", b"x")
        try:
            assemble_video([bomb], os.path.join(td, "out"), mode="zip")
            assert False, "entry-count bomb must be rejected"
        except MediaError as e:
            assert "entries" in str(e)


def test_extract_budget_streams_cleanup_and_spares_normal_zips():
    with tempfile.TemporaryDirectory() as td:
        # helper level: the partial file is removed the moment the budget blows
        zp = os.path.join(td, "z.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.bin", b"\x00" * (64 * 1024))
        dest = Path(td) / "out"
        dest.mkdir()
        with zipfile.ZipFile(zp) as zf:
            try:
                _safe_extract_zip(zf, dest, _ExtractBudget(max_bytes=1024))
                assert False, "over-budget member must be rejected"
            except MediaError:
                pass
        assert not (dest / "big.bin").exists()
        # normal chunk zips sail through untouched, even under a modest explicit budget
        os.environ["ANIMICA_RENDER_ASSEMBLE_MAX_BYTES"] = str(64 * 1024 * 1024)
        try:
            zips = [_chunk_zip(os.path.join(td, "a.zip"), [1, 2]),
                    _chunk_zip(os.path.join(td, "b.zip"), [3])]
            out = assemble_video(zips, os.path.join(td, "out2"), mode="zip")
            assert out["meta"]["frames"] == 3
        finally:
            del os.environ["ANIMICA_RENDER_ASSEMBLE_MAX_BYTES"]


# ── render_chunk input validation (never fetches Blender) ────────────────────

def test_render_chunk_rejects_bad_input_before_resolving_blender():
    with tempfile.TemporaryDirectory() as td:
        fake = os.path.join(td, "fake.blend")
        with open(fake, "wb") as f:
            f.write(b"definitely not a blend file")
        cases = [
            (os.path.join(td, "missing.blend"), dict(frame_start=1, frame_end=1)),
            (fake, dict(frame_start=1, frame_end=1)),               # bad magic
            (fake, dict(frame_start=2, frame_end=1)),               # end before start
            (fake, dict(frame_start=1, frame_end=10, frame_step=0)),
            (fake, dict(frame_start=1, frame_end=100000)),          # frame number too wide
            (fake, dict(frame_start=1, frame_end=2001)),            # over the 2000 cap
        ]
        for blend, kw in cases:
            try:
                render_chunk(blend, td, **kw)
                assert False, f"render_chunk({blend}, {kw}) must fail"
            except MediaError:
                pass


# ── pinned distribution constants ────────────────────────────────────────────

def test_blender_pin_constants():
    assert BLENDER_SHA256 == ("dfbc127a7d28f9c2175b23bf9d6701b2855f31eedfb391f9a6e60adb24572846")
    assert BLENDER_URL.startswith("https://download.blender.org/release/Blender4.2/")
    assert BLENDER_URL.endswith("blender-4.2.9-linux-x64.tar.xz")
    assert NO_AUDIO_FLAG == "-noaudio"                              # verified vs 4.2.9 --help
    assert MAX_TOTAL_FRAMES == 2000


# ── opportunistic real-render smoke (only when Blender is ALREADY local) ─────

def test_render_chunk_real_smoke():
    exe = resolve_blender(auto_fetch=False)
    if not exe:
        return  # no local Blender — tests must never download it
    with tempfile.TemporaryDirectory() as td:
        blend = os.path.join(td, "cube.blend")
        expr = ("import bpy\nsc = bpy.context.scene\nsc.render.engine = 'CYCLES'\n"
                "sc.render.resolution_x = 32\nsc.render.resolution_y = 32\n"
                "sc.cycles.samples = 8\n"
                f"bpy.ops.wm.save_as_mainfile(filepath={blend!r})\n")
        subprocess.run([exe, "-b", "--factory-startup", "--python-expr", expr],
                       check=True, capture_output=True, timeout=300)
        pcts = []
        res = render_chunk(blend, td, frame_start=1, frame_end=1, samples=16,
                           progress=pcts.append)
        assert res["mime"] == "application/zip" and len(res["sha3"]) == 64
        with zipfile.ZipFile(res["path"]) as zf:
            assert zf.namelist() == ["frame_00001.png"]
            assert validate_magic(zf.read("frame_00001.png")[:16], "png")
        assert res["meta"]["device"] in ("CPU", "CUDA", "OPTIX")
        assert pcts and pcts[-1] == 100.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("ALL RENDER-FARM TESTS PASS")
