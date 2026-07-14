"""Media plumbing tests (CPU, no heavy models).

Run with the media venv + shared torch on PYTHONPATH:
  PYTHONPATH=/root/animica/python:/root/animica/.venv/lib/python3.12/site-packages \
  /root/animica/.venv-media/bin/python -m pytest animica/media/test_media.py -q
Or as a plain script: ... /root/animica/.venv-media/bin/python animica/media/test_media.py
"""

import numpy as np
from PIL import Image

from animica.media.base import validate_magic, MediaError, is_media_kind, MediaKind, sha3_hex
from animica.media.video_gen import encode_mp4, resolve_video_model
from animica.media.audio_gen import encode_wav, resolve_audio_model
from animica.media.image_gen import resolve_image_model


def test_media_kinds():
    assert is_media_kind("image") and is_media_kind("video_t2v") and is_media_kind("audio")
    assert not is_media_kind("chat") and not is_media_kind(None)
    assert MediaKind.parse("video_i2v") is MediaKind.VIDEO_I2V


def test_magic_validation():
    assert validate_magic(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "png")
    assert not validate_magic(b"not an image", "png")
    assert not validate_magic(b"", "mp4")


def test_model_resolution():
    assert resolve_image_model("standard") == "stabilityai/sd-turbo"
    assert "musicgen" in resolve_audio_model("standard")
    assert resolve_video_model("video_t2v", "premium")
    try:
        resolve_image_model("free")
        assert False, "free tier must have no image model"
    except MediaError:
        pass


def test_mp4_encode_and_failclosed():
    frames = [Image.fromarray((np.random.rand(48, 48, 3) * 255).astype("uint8")) for _ in range(6)]
    data = encode_mp4(frames, fps=8)
    assert validate_magic(data, "mp4") and len(data) > 100
    try:
        encode_mp4([])
        assert False
    except MediaError:
        pass


def test_wav_encode_and_failclosed():
    sr = 16000
    tone = 0.4 * np.sin(2 * np.pi * 440 * np.linspace(0, 1, sr))
    data = encode_wav(tone, sr)
    assert validate_magic(data, "wav") and len(data) > 100
    try:
        encode_wav(np.array([]), sr)
        assert False
    except MediaError:
        pass


def test_sha3_stable():
    assert sha3_hex(b"abc") == sha3_hex(b"abc")
    assert sha3_hex(b"abc") != sha3_hex(b"abd")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("ALL MEDIA TESTS PASS")
