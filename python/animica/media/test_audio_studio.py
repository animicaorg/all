"""Audio Studio DSP tests (CPU-only, no models, no network, no ffmpeg).

Run with the repo venv:
  cd /root/animica && .venv/bin/python -m pytest python/animica/media/test_audio_studio.py -q
Or as a plain script:
  cd /root/animica && .venv/bin/python python/animica/media/test_audio_studio.py
"""

import os
import tempfile
import zipfile

import numpy as np
import pyloudnorm as pyln  # hard dep of the audio studio (loudness assertions)

from animica.media.base import MediaError, validate_magic
from animica.media import audio_studio as st

_CEILING = 10.0 ** (-1.0 / 20.0)  # the limiter's -1.0 dBFS ceiling, linear


def _sine(freq, seconds, sr, amp=1.0):
    t = np.arange(int(seconds * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t)


def test_limiter_honors_ceiling_on_hot_sine():
    sr = 44100
    hot = _sine(440.0, 1.0, sr, amp=1.5)
    x = np.stack([hot, hot])
    y = st.limit_peaks(x, sr=sr)
    assert y.shape == x.shape
    assert float(np.max(np.abs(y))) <= _CEILING + 1e-12   # never above the ceiling
    assert float(np.max(np.abs(y))) > 0.5                 # still real audio, not silence
    # deterministic: same input, same output
    assert np.array_equal(y, st.limit_peaks(x, sr=sr))


def test_limiter_unity_gain_on_quiet_signal():
    sr = 44100
    quiet = _sine(440.0, 1.0, sr, amp=0.1)
    x = np.stack([quiet, 0.5 * quiet])
    assert np.array_equal(st.limit_peaks(x, sr=sr), x)      # bit-exact passthrough
    assert np.array_equal(st.limit_peaks(quiet, sr=sr), quiet)  # mono shape preserved


def test_limiter_windowed_matches_one_shot():
    sr = 44100
    rng = np.random.default_rng(5)
    # 2 s of hot, bursty material so the limiter genuinely engages
    burst = np.where(np.abs(_sine(3.0, 2.0, sr)) > 0.7, 1.4, 0.2)
    x = np.stack([burst * _sine(440.0, 2.0, sr) + 0.05 * rng.standard_normal(2 * sr),
                  burst * _sine(311.0, 2.0, sr)])
    y_win = st.limit_peaks(x, sr=sr, tp_window_s=0.25)   # many overlapped windows
    y_one = st.limit_peaks(x, sr=sr, tp_window_s=1e9)    # single-shot envelope
    assert np.allclose(y_win, y_one, atol=1e-7)
    assert float(np.max(np.abs(y_win))) <= _CEILING + 1e-12
    # the underlying envelopes agree too, and never under-read the raw samples
    e_win = st._true_peak_env(x, sr, 4, window_s=0.25)
    e_one = st._true_peak_env(x, sr, 4, window_s=1e9)
    assert np.allclose(e_win, e_one, atol=1e-9)
    assert np.all(e_win >= np.abs(x).max(axis=0) - 1e-12)


def test_decode_pipe_cap_failclosed():
    import io

    class EndlessPipe:  # a forged stream that never runs dry
        def read(self, k):
            return b"\x00" * k

    try:
        st._read_stream_capped(EndlessPipe(), max_bytes=3 * (1 << 20))
        assert False, "oversized stream must raise"
    except MediaError:
        pass
    # under the cap the stream passes through intact
    data = b"ab" * 1000
    got = st._read_stream_capped(io.BytesIO(data), max_bytes=4096, chunk=256)
    assert bytes(got) == data
    # boundary: exactly max_bytes is allowed, one past is not
    assert bytes(st._read_stream_capped(io.BytesIO(b"x" * 64), max_bytes=64)) == b"x" * 64
    try:
        st._read_stream_capped(io.BytesIO(b"x" * 65), max_bytes=64)
        assert False
    except MediaError:
        pass


def test_limiter_failclosed_on_empty():
    try:
        st.limit_peaks(np.array([]), sr=44100)
        assert False, "empty audio must raise"
    except MediaError:
        pass


def test_normalize_loudness_hits_target():
    sr = 44100
    tone = _sine(440.0, 3.0, sr, amp=0.05)
    x = np.stack([tone, tone])
    y, before = st.normalize_loudness(x, sr, -16.0)
    after = pyln.Meter(sr).integrated_loudness(y.T)
    assert abs(after - (-16.0)) < 0.5                      # within ±0.5 LU of target
    assert before < -16.0                                  # the quiet tone started below


def test_band_gain_computation_on_synthetic_spectra():
    prog = np.zeros(31)
    ref = np.zeros(31)
    ref[10] = 6.0    # reference brighter here -> boost
    ref[20] = 20.0   # way brighter -> clamped to +9
    ref[5] = -15.0   # reference darker -> cut, clamped to -9
    g = st.band_gains_db(prog, ref)
    assert g[10] == 6.0 and g[20] == 9.0 and g[5] == -9.0
    assert g[0] == 0.0 and g[30] == 0.0
    try:
        st.band_gains_db(np.zeros(31), np.zeros(30))
        assert False, "shape mismatch must raise"
    except MediaError:
        pass


def test_fir_match_lifts_the_requested_band():
    sr = 44100
    rng = np.random.default_rng(7)
    noise = 0.1 * rng.standard_normal(4 * sr)
    centers, base_db = st.band_spectrum_db(noise, sr)
    gains = np.zeros(centers.size)
    lift = (centers > 900.0) & (centers < 1300.0)
    assert lift.any()
    gains[lift] = 6.0
    taps = st.design_match_fir(centers, gains, sr)
    y = st.apply_fir(np.stack([noise, noise]), taps)
    assert y.shape == (2, noise.size)                      # group delay compensated
    _, out_db = st.band_spectrum_db(y[0], sr)
    assert np.all(out_db[lift] - base_db[lift] > 3.0)      # the band really lifted
    far = (centers < 200.0) | (centers > 8000.0)
    assert np.all(np.abs(out_db[far] - base_db[far]) < 2.0)  # the rest untouched


def test_chunked_apply_identity_with_passthrough_model():
    sr = 1000
    rng = np.random.default_rng(3)
    x = (0.5 * rng.standard_normal((2, 25000))).astype(np.float32)
    calls, notes = [], []

    def passthrough(seg):
        calls.append(seg.shape[-1])
        return seg[None]  # (K=1, C, m)

    out = st.chunked_apply(x, sr, passthrough, chunk_s=10.0, overlap_s=1.0,
                           progress=lambda pct, note="": notes.append(pct))
    assert out.shape == (1, 2, 25000)
    assert len(calls) >= 3                                  # really chunked
    assert np.allclose(out[0], x, atol=1e-5)                # overlap-add reconstructs
    assert notes and notes[-1] == 100.0
    assert all(b >= a for a, b in zip(notes, notes[1:]))    # monotonic progress
    try:  # a model returning the wrong shape must fail closed
        st.chunked_apply(x, sr, lambda seg: seg, chunk_s=10.0, overlap_s=1.0)
        assert False
    except MediaError:
        pass


def test_parse_duration_on_canned_ffmpeg_stderr():
    err = ("Input #0, mp3, from 'song.mp3':\n"
           "  Duration: 00:03:07.44, start: 0.000000, bitrate: 320 kb/s\n")
    assert abs(st.parse_duration(err) - 187.44) < 1e-9
    assert st.parse_duration("Duration: 01:02:03.50, start: 0") == 3723.5
    assert st.parse_duration("  Duration: N/A, bitrate: N/A") is None
    assert st.parse_duration("") is None
    assert st.parse_duration(None) is None


def test_wav_roundtrip_with_validate_magic():
    sr = 22050
    x = np.stack([_sine(440.0, 1.0, sr, amp=0.5), _sine(220.0, 1.0, sr, amp=0.5)])
    with tempfile.TemporaryDirectory() as td:
        path, mime, actual = st._encode_audio_file(x, sr, td, "tone", "wav")
        assert actual == "wav" and mime == "audio/wav"
        assert os.path.dirname(path) == td and path.endswith("tone.wav")
        with open(path, "rb") as f:
            assert validate_magic(f.read(), "wav")
        from scipy.io import wavfile
        got_sr, got = wavfile.read(path)
        assert got_sr == sr and got.shape == (x.shape[1], 2)
        assert np.allclose(got.T.astype(np.float64) / 32767.0, x, atol=1e-3)
    try:  # unknown formats fail closed before any encoder runs
        st._encode_audio_file(x, sr, "/tmp", "x", "ogg")
        assert False
    except MediaError:
        pass


def test_mp3_and_zip_magic_checks():
    assert validate_magic(b"ID3\x04\x00" + b"\x00" * 20, "mp3")          # ID3v2
    assert validate_magic(b"\xff\xfb\x90\x00" + b"\x00" * 20, "mp3")     # bare frame sync
    assert not validate_magic(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 8, "mp3")
    with tempfile.TemporaryDirectory() as td:
        z = os.path.join(td, "a.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("stem.txt", "hello")
        with open(z, "rb") as f:
            assert validate_magic(f.read(), "zip")
    assert not validate_magic(b"\x00" * 32, "zip")


def test_master_preset_failclosed():
    try:
        st.master_audio("/nonexistent.wav", "/tmp", preset="nope")
        assert False, "unknown preset must raise before any IO"
    except MediaError:
        pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("ALL AUDIO STUDIO TESTS PASS")
