"""Video director tests (CPU; real ffmpeg + PIL/numpy, image generation faked, no models).

  PYTHONPATH=/root/animica/python /root/animica/.venv/bin/python -m pytest animica/media/test_video_director.py -q
"""

from __future__ import annotations

import io
import json
import os
import subprocess

import numpy as np
import pytest
from PIL import Image

from animica.media import video_director as vd
from animica.media import video_gen, image_gen, learning
from animica.media.base import MediaError


def _ffprobe(path: str) -> dict:
    from animica.media.base import resolve_ffmpeg
    exe = resolve_ffmpeg()
    probe = exe.replace("ffmpeg", "ffprobe")
    if not os.path.exists(probe):
        # bundled imageio-ffmpeg has no ffprobe: parse ffmpeg -i stderr instead
        r = subprocess.run([exe, "-i", path], capture_output=True, text=True)
        from animica.media.video_studio import parse_ffmpeg_probe
        return parse_ffmpeg_probe(r.stderr)
    r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_frames:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {"width": s["width"], "height": s["height"], "fps": float(num) / float(den),
            "duration": float(d["format"]["duration"]), "frames": int(s.get("nb_frames") or 0)}


# ── planning ────────────────────────────────────────────────────────────────

def test_plan_single_idea_gets_film_coverage():
    shots = vd.plan_shots("a cat walking through a neon city at night", 8.0, seed=1)
    assert [s.role for s in shots] == ["wide", "main", "detail"]
    assert "neon city at night" in shots[0].prompt and shots[0].prompt.startswith("wide establishing shot")
    assert shots[1].prompt == "a cat walking through a neon city at night"
    assert shots[2].prompt.startswith("close-up of a cat walking")
    assert abs(sum(s.seconds for s in shots) - 8.0) < 0.2
    assert all(s.camera in vd.CAMERA_MOVES for s in shots)
    assert len({s.seed for s in shots}) == 3


def test_plan_short_brief_is_one_shot():
    shots = vd.plan_shots("a red cube", 3.0, seed=1)
    assert len(shots) == 1 and shots[0].role == "main" and shots[0].seconds == 3.0


def test_plan_explicit_beats_and_scenes():
    shots = vd.plan_shots("A rocket sits on the pad. Then it launches into the sky. Then it orbits the earth", 12.0, seed=1)
    assert len(shots) == 3 and all(s.role == "user" for s in shots)
    assert "launches" in shots[1].prompt and shots[2].camera.startswith("orbit")
    shots = vd.plan_shots("ignored", 6.0, scenes=["sunrise over hills", "a village market"], seed=1)
    assert [s.prompt for s in shots] == ["sunrise over hills", "a village market"]


def test_plan_long_duration_repeats_coverage_within_shot_bounds():
    shots = vd.plan_shots("a lighthouse in a storm", 30.0, seed=1)
    assert 6 <= len(shots) <= 8 and all(1.5 <= s.seconds <= 5.0 for s in shots)
    assert abs(sum(s.seconds for s in shots) - 30.0) < 1.0


def test_camera_phrase_and_t2v_prompt():
    s = vd.Shot(index=0, prompt="a cat", camera="pan_left", seconds=3)
    assert "pan to the left" in s.t2v_prompt() and s.t2v_prompt().startswith("a cat.")
    assert vd.camera_for("main", 0, "drone aerial view of a coast", 4) == "tilt_down"
    assert vd.camera_for("wide", 0, "a beach", 4) == "pan_right" and vd.camera_for("wide", 1, "a beach", 4) == "pan_left"


def test_camera_path_moves_and_is_bounded():
    for cam in vd.CAMERA_MOVES:
        p0, p1 = vd._camera_path(cam, 0.0), vd._camera_path(cam, 1.0)
        assert all(abs(v) < 0.2 for v in (p0[0], p0[1], p1[0], p1[1]))
        assert 0.9 < p0[2] < 1.4 and 0.9 < p1[2] < 1.4
        if cam != "static":
            assert p0 != p1, cam


# ── model regimes ────────────────────────────────────────────────────────────

def test_video_model_profiles_and_frame_rules():
    wan = video_gen.video_model_profile("Wan-AI/Wan2.1-T2V-1.3B-Diffusers")
    assert wan["fps"] == 16 and wan["max_frames"] == 81 and wan["frame_rule"] == 4 and wan["family"] == "wan"
    assert video_gen.frames_for(wan, 4.0) == 65        # 64 frames → 4k+1 = 65
    assert video_gen.frames_for(wan, 10.0) == 81       # capped
    assert video_gen.frames_for(wan, 0.1) == 5
    cog = video_gen.video_model_profile("THUDM/CogVideoX-2b")
    assert video_gen.frames_for(cog, 6.0) == 49
    ms = video_gen.video_model_profile("ali-vilab/text-to-video-ms-1.7b")
    assert ms["max_frames"] == 16 and video_gen.frames_for(ms, 4.0) == 16
    svd = video_gen.video_model_profile("stabilityai/stable-video-diffusion-img2vid-xt")
    assert svd["max_frames"] == 25 and not svd["cfg"]
    assert video_gen.resolve_video_model("video_t2v", "standard").startswith("Wan-AI/")


def test_call_kwargs_filters_by_signature():
    class P:
        def __call__(self, prompt, num_frames, generator=None):
            return None
    kw = video_gen._call_kwargs(P(), prompt="x", num_frames=5, width=1, negative_prompt=None)
    assert kw == {"prompt": "x", "num_frames": 5}


# ── parallax engine ──────────────────────────────────────────────────────────

def _synthetic_scene(w=320, h=180):
    """Sky gradient + a dark 'foreground' block bottom-left, so parallax is measurable."""
    img = Image.new("RGB", (w, h))
    px = np.zeros((h, w, 3), dtype="uint8")
    for y in range(h):
        px[y, :, :] = (80, 120, 200 - int(120 * y / h))
    px[h // 2:, : w // 3, :] = (20, 20, 20)
    return Image.fromarray(px)


def test_estimate_depth_prior_without_model(monkeypatch):
    monkeypatch.setenv("ANIMICA_DEPTH_MODEL", "0")
    d, name = vd.estimate_depth(_synthetic_scene())
    assert name == "ground-plane-prior" and d.shape == (180, 320)
    assert d[0, 0] < d[-1, 0]  # bottom is nearer


def test_parallax_frames_have_real_relative_motion(monkeypatch):
    monkeypatch.setenv("ANIMICA_DEPTH_MODEL", "0")
    img = _synthetic_scene()
    depth = np.zeros((180, 320), dtype="float32")
    depth[90:, :106] = 1.0          # the dark block is NEAR
    frames = vd.parallax_frames(img, depth, n_frames=12, camera="pan_right", width=320, height=180)
    assert len(frames) == 12 and frames[0].shape == (180, 320, 3)
    # The near block's right edge must travel across frames (parallax), not stay put.
    def edge(f):
        row = f[150, :, 0].astype(int)
        dark = np.where(row < 60)[0]
        return int(dark.max()) if len(dark) else -1
    e0, e1 = edge(frames[0]), edge(frames[-1])
    assert e0 >= 0 and e1 >= 0 and abs(e1 - e0) >= 4, (e0, e1)
    # and consecutive frames differ smoothly (no frozen frames, no jumps)
    diffs = [float(np.abs(frames[i + 1].astype(int) - frames[i].astype(int)).mean()) for i in range(11)]
    assert min(diffs) > 0.0 and max(diffs) < 40.0


# ── conform + assembly (real ffmpeg) ────────────────────────────────────────

def test_conform_clip_exact_fps_size_duration(tmp_path):
    frames = [np.full((96, 160, 3), int(255 * i / 15), dtype="uint8") for i in range(16)]
    src = str(tmp_path / "src.mp4")
    vd.write_frames_mp4(frames, src, 8)          # 2.0 s @ 8 fps
    dst = str(tmp_path / "dst.mp4")
    info = vd.conform_clip(src, dst, width=192, height=108, fps=24, seconds=3.0, src_fps=8, src_seconds=2.0,
                           retime="fps")
    assert info["stretch"] == 1.5
    pr = _ffprobe(dst)
    assert (pr["width"], pr["height"]) == (192, 108) and abs(pr["fps"] - 24) < 0.01
    assert abs(pr["duration"] - 3.0) < 0.15


def test_assemble_clips_with_transition(tmp_path):
    paths = []
    for i in range(2):
        frames = [np.full((96, 160, 3), 40 + 150 * i, dtype="uint8") for _ in range(24)]
        p = str(tmp_path / f"c{i}.mp4")
        vd.write_frames_mp4(frames, p, 12)        # 2.0 s each
        paths.append(p)
    out = str(tmp_path / "out.mp4")
    total = vd.assemble_clips(paths, out, fps=12, durations=[2.0, 2.0], transitions=["fade", "fade"], transition_secs=0.5)
    assert abs(total - 3.5) < 0.01
    pr = _ffprobe(out)
    assert abs(pr["duration"] - 3.5) < 0.2


# ── end to end: director with a faked keyframe generator ────────────────────

def _fake_generate_image(prompt, **kw):
    img = _synthetic_scene(kw.get("width", 320), kw.get("height", 180))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return {"bytes": buf.getvalue(), "mime": "image/png", "model": "fake-sd", "fidelity": 0.31,
            "candidates": 2, "seed": kw.get("seed"), "prompt": prompt}


def test_render_video_parallax_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_DEPTH_MODEL", "0")
    monkeypatch.setattr(image_gen, "generate_image", _fake_generate_image)
    monkeypatch.setattr(vd, "_cuda_vram_gb", lambda: 0.0)
    notes = []
    out = vd.render_video("a cat walking through a neon city at night", seconds=6.0, fps=12, width=320, height=180,
                          seed=42, progress=lambda p, n: notes.append((p, n)))
    assert out["mime"] == "video/mp4" and out["bytes"][4:8] == b"ftyp"
    m = out["meta"]
    assert m["engine"] == "parallax" and len(m["shots"]) == 3 and m["subject_motion"].startswith("camera-only")
    assert [s["role"] for s in m["shots"]] == ["wide", "main", "detail"]
    assert all(s["engine"] == "parallax" and s["fidelity"] == 0.31 for s in m["shots"])
    assert abs(m["duration_s"] - 5.0) < 0.6   # 6 s of shots minus two 0.5 s crossfades
    assert any("shot 2/3" in n for _, n in notes)
    p = tmp_path / "o.mp4"; p.write_bytes(out["bytes"])
    pr = _ffprobe(str(p))
    assert (pr["width"], pr["height"]) == (320, 180) and abs(pr["fps"] - 12) < 0.01


def test_render_video_stills_mode(monkeypatch):
    monkeypatch.setenv("ANIMICA_DEPTH_MODEL", "0")
    monkeypatch.setattr(vd, "_cuda_vram_gb", lambda: 0.0)
    out = vd.render_video("uploaded", seconds=4.0, fps=10, width=160, height=96, seed=3,
                          stills=[_synthetic_scene(200, 120), _synthetic_scene(200, 120)], engine="parallax")
    m = out["meta"]
    assert len(m["shots"]) == 2 and all(s["engine"] == "parallax" for s in m["shots"])


def test_generative_engine_failure_falls_back_to_parallax(monkeypatch):
    monkeypatch.setenv("ANIMICA_DEPTH_MODEL", "0")
    monkeypatch.setattr(image_gen, "generate_image", _fake_generate_image)
    monkeypatch.setattr(vd, "_cuda_vram_gb", lambda: 24.0)
    def boom(*a, **k):
        raise MediaError("CUDA out of memory")
    monkeypatch.setattr(video_gen, "generate_text_to_video", boom)
    monkeypatch.setattr(video_gen, "generate_image_to_video", boom)
    out = vd.render_video("a red cube", seconds=2.0, fps=10, width=160, height=96, seed=3, engine="t2v")
    s = out["meta"]["shots"][0]
    assert s["engine"] == "parallax" and any("t2v unavailable" in n for n in s["notes"])


def test_select_engine(monkeypatch):
    monkeypatch.delenv("ANIMICA_VIDEO_ENGINE", raising=False)
    monkeypatch.setattr(vd, "_cuda_vram_gb", lambda: 0.0)
    assert vd.select_engine() == "parallax" and vd.select_engine("t2v") == "parallax"
    monkeypatch.setattr(vd, "_cuda_vram_gb", lambda: 24.0)
    assert vd.select_engine() == "t2v" and vd.select_engine("keyframe") == "keyframe"
    monkeypatch.setattr(vd, "_cuda_vram_gb", lambda: 6.0)
    assert vd.select_engine() == "parallax"


# ── learning ledger ─────────────────────────────────────────────────────────

def test_learner_records_and_prefers_best_camera(tmp_path):
    L = learning.Learner(str(tmp_path / "learn.db"), epsilon=0.0)
    for cam, fid in (("pan_left", 0.20), ("pan_left", 0.22), ("dolly_in", 0.35), ("dolly_in", 0.33)):
        L.record_video("x", {"shots": [{"prompt": "a castle on a hill at dusk", "role": "main", "engine": "parallax",
                                          "camera": cam, "model": "m", "seed": 1, "fidelity": fid}]})
    assert L.choose_camera("main", 0, "a castle on a hill at dusk", 4.0) == "dolly_in"
    assert L.choose_camera("main", 0, "completely unrelated prompt about fish", 4.0) == vd.camera_for("main", 0, "completely unrelated prompt about fish", 4.0)
    L.record_image("a blue triangle", {"prompt": "a blue triangle", "model": "m", "seed": 777, "fidelity": 0.5, "candidates": 2})
    L.record_image("a blue triangle", {"prompt": "a blue triangle", "model": "m", "seed": 778, "fidelity": 0.3, "candidates": 2})
    assert L.best_seed_hint("a blue triangle") == 777 and L.best_seed_hint("nothing") is None
    L.remember_references("a blue triangle", ["https://x/1.jpg"])
    assert L.references_for("a blue triangle") == ["https://x/1.jpg"]
    st = L.stats()
    assert st["renders"] == 6 and st["by_camera"]["dolly_in"]["n"] == 2


# ── distributed mode: one shot per miner, another miner assembles ───────────

def test_render_shot_and_assemble_shots_across_workers(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_DEPTH_MODEL", "0")
    monkeypatch.setattr(image_gen, "generate_image", _fake_generate_image)
    monkeypatch.setattr(vd, "_cuda_vram_gb", lambda: 0.0)
    plan = [s for s in vd.plan_shots("a cat walking through a neon city at night", 6.0, seed=9)]
    shot_dicts = [{"index": s.index, "prompt": s.prompt, "camera": s.camera, "seconds": s.seconds,
                   "transition": s.transition, "role": s.role, "seed": s.seed} for s in plan]
    clips, metas = [], []
    for i, sd in enumerate(shot_dicts):          # "worker i" renders shot i independently
        out = vd.render_shot(sd, str(tmp_path / f"w{i}"), width=160, height=96, fps=10, precision="fast")
        assert out["mime"] == "video/mp4" and os.path.getsize(out["path"]) > 0
        assert out["meta"]["engine"] == "parallax" and out["meta"]["index"] == i
        clips.append(out["path"]); metas.append(out["meta"])
    # the assembler (a different worker) only sees the clip files + the plan
    fin = vd.assemble_shots(clips, shot_dicts, str(tmp_path / "asm"), fps=10, shot_metas=metas)
    assert fin["mime"] == "video/mp4" and fin["meta"]["distributed"] and fin["meta"]["workers"] == 3
    assert fin["meta"]["engine"] == "parallax" and len(fin["meta"]["shots"]) == 3
    pr = _ffprobe(fin["path"])
    assert abs(pr["duration"] - (6.0 - 2 * 0.5)) < 0.6 and (pr["width"], pr["height"]) == (160, 96)


def test_assemble_shots_rejects_bad_clip(tmp_path):
    bad = tmp_path / "bad.mp4"; bad.write_bytes(b"not a video at all" * 300)
    with pytest.raises(MediaError):
        vd.assemble_shots([str(bad)], [{"seconds": 2}], str(tmp_path / "o"), fps=10)
    with pytest.raises(MediaError):
        vd.assemble_shots([str(bad)], [{"seconds": 2}, {"seconds": 2}], str(tmp_path / "o"), fps=10)
