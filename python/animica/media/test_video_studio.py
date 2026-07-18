"""Video Studio plumbing tests (CPU-only, no network, no model weights).

Run with the repo venv:
  cd /root/animica && .venv/bin/python -m pytest python/animica/media/test_video_studio.py -q
Or with the media venv + shared torch on PYTHONPATH (test_media.py recipe):
  PYTHONPATH=/root/animica/python:/root/animica/.venv/lib/python3.12/site-packages \
  /root/animica/.venv-media/bin/python -m pytest animica/media/test_video_studio.py -q
Or as a plain script via the __main__ loop at the bottom.
"""

import os
import tempfile
import zipfile

from animica.media.base import MediaError, sha3_hex, validate_magic
from animica.media import video_studio as vs

# ── probe parser (canned `ffmpeg -i` stderr) ─────────────────────────────────
_PROBE_H264 = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'in.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:01:23.46, start: 0.000000, bitrate: 1005 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1280x720 [SAR 1:1 DAR 16:9], 900 kb/s, 25 fps, 25 tbr, 12800 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 128 kb/s (default)
At least one output file must be specified
"""

_PROBE_TBR_ONLY = """\
Input #0, avi, from 'clip.avi':
  Duration: 00:00:08.00, start: 0.000000, bitrate: 64 kb/s
  Stream #0:0: Video: mjpeg (Baseline), yuvj420p(pc, bt470bg/unknown/unknown), 640x360, 30 tbr, 30 tbn
At least one output file must be specified
"""


def test_probe_parser_full():
    info = vs.parse_ffmpeg_probe(_PROBE_H264)
    assert info == {"width": 1280, "height": 720, "fps": 25.0,
                    "duration_s": 83.46, "has_audio": True}


def test_probe_parser_tbr_fallback_no_audio():
    info = vs.parse_ffmpeg_probe(_PROBE_TBR_ONLY)
    assert (info["width"], info["height"]) == (640, 360)
    assert info["fps"] == 30.0 and info["duration_s"] == 8.0
    assert info["has_audio"] is False


def test_probe_parser_fail_closed():
    for bad in ("", "Duration: 00:00:05.00\n  Stream #0:0: Audio: aac",
                "  Stream #0:0: Video: h264, yuv420p, 640x360, 30 fps"):  # no duration
        try:
            vs.parse_ffmpeg_probe(bad)
            assert False, f"probe must fail closed on: {bad!r}"
        except MediaError:
            pass


def test_probe_parser_ignores_hex_fourcc():
    # the (avc1 / 0x31637661) token must never be read as WxH
    info = vs.parse_ffmpeg_probe(_PROBE_H264)
    assert info["width"] == 1280 and info["height"] == 720


# ── SRT writer ───────────────────────────────────────────────────────────────
def test_srt_time_format():
    assert vs.format_srt_time(0) == "00:00:00,000"
    assert vs.format_srt_time(3661.5) == "01:01:01,500"
    assert vs.format_srt_time(59.9999) == "00:01:00,000"
    assert vs.format_srt_time(-3) == "00:00:00,000"


def test_srt_ordering_and_numbering():
    srt = vs.srt_from_segments([(10.0, 12.0, "second"), (1.0, 3.5, "first")])
    blocks = srt.strip().split("\n\n")
    assert blocks[0].startswith("1\n00:00:01,000 --> 00:00:03,500\nfirst")
    assert blocks[1].startswith("2\n00:00:10,000 --> 00:00:12,000\nsecond")


def test_srt_multiline_open_end_and_skips():
    srt = vs.srt_from_segments([
        (0.0, None, "line one\nline two"),   # open end -> +2.5s, multiline kept
        (5.0, 5.0, "tick"),                  # zero-length -> stretched
        (6.0, 7.0, "   "),                   # empty -> skipped
        (None, 9.0, "no start"),             # no start -> skipped
    ])
    assert "00:00:00,000 --> 00:00:02,500\nline one\nline two" in srt
    assert "00:00:05,000 --> 00:00:05,300\ntick" in srt
    assert "no start" not in srt and srt.count("-->") == 2
    assert vs.srt_from_segments([]) == ""


# ── scene parser + shorts window picking ─────────────────────────────────────
_SCENE_TEXT = """\
[Parsed_metadata_1 @ 0x55f] frame:47   pts:48128   pts_time:6.016
[Parsed_metadata_1 @ 0x55f] lavfi.scene_score=0.406213
[Parsed_metadata_1 @ 0x55f] frame:151  pts:154624  pts_time:19.328
[Parsed_metadata_1 @ 0x55f] lavfi.scene_score=0.512001
noise line without keys
"""


def test_scene_parser():
    assert vs.parse_scene_times(_SCENE_TEXT) == [(6.016, 0.406213), (19.328, 0.512001)]
    assert vs.parse_scene_times("") == []
    # a score line with no preceding pts_time is dropped, not mispaired
    assert vs.parse_scene_times("lavfi.scene_score=0.9") == []


def test_mean_volume_parser():
    assert vs.parse_mean_volume("[Parsed_volumedetect_0 @ 0x1] mean_volume: -18.1 dB") == -18.1
    assert vs.parse_mean_volume("no volume here") is None


def test_window_scoring():
    loud = vs.score_window(30, 30, -10.0)
    quiet = vs.score_window(30, 30, -50.0)
    assert loud > quiet
    assert vs.score_window(15, 30, None) == 0.5 + 0.5   # half length + neutral volume


def test_pick_windows_by_score_non_overlapping():
    cands = [(0.0, 1.0), (30.0, 1.8), (60.0, 1.5)]
    wins = vs.pick_short_windows(cands, 90.0, count=2, duration=20.0)
    assert wins == [(30.0, 50.0), (60.0, 80.0)]


def test_pick_windows_no_scene_cuts_degrades_evenly():
    # 0 scene changes -> single candidate at 0 -> evenly-spaced fill
    wins = vs.pick_short_windows([(0.0, 1.0)], 60.0, count=3, duration=10.0)
    assert wins == [(0.0, 10.0), (25.0, 35.0), (50.0, 60.0)]


def test_pick_windows_tiny_video_yields_fewer():
    wins = vs.pick_short_windows([(0.0, 1.0)], 8.0, count=3, duration=30.0)
    assert wins == [(0.0, 8.0)]   # graceful: one window spanning the whole clip


# ── tile math ────────────────────────────────────────────────────────────────
def test_iter_tiles_covers_frame():
    from animica.media.sr_models import iter_tiles
    tiles = iter_tiles(100, 100, tile=50, overlap=16)
    assert len(tiles) == 4
    covered = set()
    for (y0, y1, x0, x1, py0, py1, px0, px1) in tiles:
        assert 0 <= py0 <= y0 < y1 <= py1 <= 100
        assert 0 <= px0 <= x0 < x1 <= px1 <= 100
        covered.update((y, x) for y in range(y0, y1) for x in range(x0, x1, 25))
    assert all((y, x) in covered for y in range(0, 100, 25) for x in range(0, 100, 25))


def test_iter_tiles_small_frame_single_tile():
    from animica.media.sr_models import iter_tiles
    assert iter_tiles(20, 24, tile=512, overlap=16) == [(0, 20, 0, 24, 0, 20, 0, 24)]


# ── arch forwards (random init — no weights, no network) ─────────────────────
def test_srvgg_forward_shape():
    import torch
    from animica.media.sr_models import SRVGGNetCompact
    m = SRVGGNetCompact(num_conv=2, upscale=4).eval()
    with torch.no_grad():
        out = m(torch.rand(1, 3, 16, 16))
    assert out.shape == (1, 3, 64, 64)


def test_rrdb_forward_shapes_both_scales():
    import torch
    from animica.media.sr_models import RRDBNet
    for scale, exp in ((2, 32), (4, 64)):
        m = RRDBNet(scale=scale, num_block=2).eval()
        with torch.no_grad():
            out = m(torch.rand(1, 3, 16, 16))
        assert out.shape == (1, 3, exp, exp), f"scale {scale}"


def test_upscale_tiled_matches_target_shape():
    import torch
    from animica.media.sr_models import SRVGGNetCompact, upscale_tiled
    m = SRVGGNetCompact(num_conv=2, upscale=4).eval()
    out = upscale_tiled(m, torch.rand(1, 3, 33, 41), 4, tile=16, overlap=4)  # odd dims
    assert out.shape == (1, 3, 132, 164)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_rife_arch_random_init_forward():
    import numpy as np
    from animica.media.rife_arch import IFNet, interpolate_mid
    m = IFNet().eval().float()
    f0 = (np.random.rand(32, 48, 3) * 255).astype("uint8")
    f1 = (np.random.rand(32, 48, 3) * 255).astype("uint8")
    mid = interpolate_mid(m, f0, f1)
    assert mid.shape == (32, 48, 3) and mid.dtype == np.uint8


# ── ffmpeg arg builders ──────────────────────────────────────────────────────
def test_decode_cmd():
    cmd = vs.build_decode_cmd("ffmpeg", "/x/in.mp4", fps=25.0)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "pipe:1"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "rgb24"
    assert cmd[cmd.index("-r") + 1] == "25"
    assert "-loop" not in cmd                       # house rule: never -loop 1


def test_encode_cmd_audio_carryover_and_caps():
    cmd = vs.build_encode_cmd("ffmpeg", "/x/out.mp4", width=640, height=360,
                              fps=23.976, audio_from="/x/in.mp4")
    s = " ".join(cmd)
    assert "-map 1:a? -c:a aac" in s                # optional audio, never an error
    assert "-shortest" in s and "-movflags +faststart" in s
    assert "-s 640x360" in s and "-framerate 23.976" in s
    assert "-loop" not in cmd
    # no audio input -> no audio args at all
    s2 = " ".join(vs.build_encode_cmd("ffmpeg", "/x/out.mp4", width=64, height=64, fps=8))
    assert "1:a?" not in s2 and "-shortest" not in s2


def test_encode_cmd_webm_alpha():
    cmd = vs.build_encode_cmd("ffmpeg", "/x/out.webm", width=64, height=64, fps=24,
                              in_pix_fmt="rgba", vcodec="libvpx-vp9",
                              out_pix_fmt="yuva420p", extra=["-auto-alt-ref", "0"])
    s = " ".join(cmd)
    assert "-c:v libvpx-vp9" in s and "-pix_fmt yuva420p" in s and "-auto-alt-ref 0" in s
    assert "faststart" not in s and "-crf" not in s   # x264-only args must not leak


def test_crop_filter():
    f = vs.build_crop_filter("9:16")
    assert "min(iw,ih*9/16)" in f and "min(ih,iw*16/9)" in f and "/2)*2" in f
    assert vs.build_crop_filter("1:1").count("min(") == 2
    try:
        vs.build_crop_filter("4:3")
        assert False, "unknown aspect must fail"
    except MediaError:
        pass


# ── zip round-trip + finalize contract ───────────────────────────────────────
def test_zip_roundtrip_and_finalize():
    with tempfile.TemporaryDirectory() as td:
        zp = os.path.join(td, "bundle.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("captions.srt", "1\n00:00:00,000 --> 00:00:01,000\nhi\n")
            z.writestr("transcript.txt", "hi\n")
        data = open(zp, "rb").read()
        assert validate_magic(data, "zip")
        with zipfile.ZipFile(zp) as z:
            assert sorted(z.namelist()) == ["captions.srt", "transcript.txt"]
            assert z.read("transcript.txt") == b"hi\n"
        out = vs._finalize(zp, "zip", "application/zip", {"model": "m"})
        assert out["path"] == zp and out["mime"] == "application/zip"
        assert out["sha3"] == sha3_hex(data)         # streamed hash == base.sha3_hex
        # wrong magic claim must fail closed
        try:
            vs._finalize(zp, "mp4", "video/mp4", {})
            assert False
        except MediaError:
            pass


def test_input_resolution_guard():
    # at the cap (either orientation) is fine
    vs.check_input_resolution(4096, 2304)
    vs.check_input_resolution(2304, 4096)
    vs.check_input_resolution(3840, 2160)
    vs.check_input_resolution(2160, 3840)
    # over the cap (any orientation, incl. 8K) must fail closed
    for w, h in ((7680, 4320), (4320, 7680), (4097, 2304), (4096, 2305), (8192, 64)):
        try:
            vs.check_input_resolution(w, h)
            assert False, f"{w}x{h} must be rejected"
        except MediaError as e:
            assert "resolution limit" in str(e)


def test_input_resolution_guard_from_probe():
    # probe-parser-driven: a crafted 8K upload is rejected before any decode
    probe_8k = _PROBE_H264.replace("1280x720", "7680x4320")
    info = vs.parse_ffmpeg_probe(probe_8k)
    assert (info["width"], info["height"]) == (7680, 4320)
    try:
        vs.check_input_resolution(info["width"], info["height"])
        assert False
    except MediaError:
        pass


def test_caps_reject_bad_params():
    for kwargs, exc in ((dict(scale=3), MediaError), (dict(model="ultra"), MediaError)):
        try:
            vs.upscale_video("/nonexistent.mp4", "/tmp", **kwargs)
            assert False
        except MediaError:
            pass
    try:
        vs.interpolate_video("/nonexistent.mp4", "/tmp", factor=3)
        assert False
    except MediaError:
        pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("ALL VIDEO STUDIO TESTS PASS")
