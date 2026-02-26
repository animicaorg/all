from __future__ import annotations

import random
from PIL import Image


def generate_image(prompt: str, width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height))
    pix = img.load()
    for y in range(height):
        for x in range(width):
            base = (x * 13 + y * 7 + len(prompt) * 5) % 255
            pix[x, y] = ((base + rng.randint(0, 60)) % 255, (base * 2) % 255, (base * 3) % 255)
    return img
