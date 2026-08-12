"""CPU smoke test for animica.media.image_gen — proves a REAL PNG is produced (fail-closed).
Uses a tiny SD pipeline so it runs in seconds on CPU (garbage pixels, but a valid PNG = plumbing OK).
Run: PYTHONPATH=/root/animica/python:/root/animica/.venv/lib/python3.12/site-packages \
     ANIMICA_IMAGE_MODEL=hf-internal-testing/tiny-stable-diffusion-pipe \
     /root/animica/.venv-media/bin/python media_smoke.py
"""
import sys
from animica.media.base import media_available, validate_magic
from animica.media.image_gen import generate_image, resolve_image_model

avail, why = media_available()
print(f"media_available: {avail} ({why})")
assert avail, "media backend must be importable"

print("resolve standard tier ->", resolve_image_model("standard"))
print("generating (tiny model, CPU)...")
out = generate_image("a red cube on a blue background", tier="standard", width=64, height=64, steps=2, seed=7)

data = out["bytes"]
print(f"  bytes={len(data)} mime={out['mime']} model={out['model']}")
print(f"  sha3={out['sha3'][:24]}… dims={out['width']}x{out['height']} steps={out['steps']}")
assert validate_magic(data, "png"), "output must be a valid PNG"
assert len(data) > 100, "PNG suspiciously small"

# Determinism: same seed -> same content hash.
out2 = generate_image("a red cube on a blue background", tier="standard", width=64, height=64, steps=2, seed=7)
print(f"  determinism: same-seed sha3 match = {out['sha3'] == out2['sha3']}")

# Fail-closed: empty prompt must raise, not stub.
try:
    generate_image("", tier="standard")
    print("  FAIL: empty prompt did not raise")
    sys.exit(1)
except Exception as e:
    print(f"  fail-closed on empty prompt: {type(e).__name__} ✓")

# Write a copy so we can eyeball it exists on disk.
with open("/tmp/claude-0/-root/b32592d1-6389-4dce-b72f-37b06dda06e6/scratchpad/smoke.png", "wb") as f:
    f.write(data)
print("wrote /tmp/.../scratchpad/smoke.png")
print("SMOKE OK")
