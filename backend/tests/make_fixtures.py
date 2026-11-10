"""Generate a fixture folder covering every kind of duplicate the app must detect.

Usage:  python tests/make_fixtures.py <out_dir>
"""
from __future__ import annotations

import os
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# expected tier for each variant of base "a" (None = must NOT be grouped with a.jpg)
EXPECTED = {
    "a_copy_other_mtime.jpg": "exact",
    "a_recompressed_q60.jpg": "duplicate",
    "a_converted.png": "duplicate",
    "a_half_size.jpg": "duplicate",
    "a_rotated90.jpg": "duplicate",
    "a_cropped20.jpg": "near",
    "a_meme_caption.jpg": "near",
}
UNRELATED = ["b.jpg", "c_similar_colours.jpg"]
# different pictures that carry the same text -> "text" tier
SAME_TEXT = ["a_meme_caption.jpg", "b_same_caption.jpg"]
SCREENSHOT_LINES = ["Olá! Você vem amanhã?", "Reunião às 15h na sala 3", "Total: R$ 1.234,56", "Meeting notes for Q3 budget"]


def _font(size: int):
    import glob

    for pattern in ("/usr/share/fonts/**/DejaVuSans.ttf", "/usr/share/fonts/**/LiberationSans-Regular.ttf", "/Library/Fonts/Arial.ttf"):
        found = glob.glob(pattern, recursive=True)
        if found:
            return ImageFont.truetype(found[0], size), True
    try:
        return ImageFont.load_default(size=size), False
    except TypeError:
        return ImageFont.load_default(), False


def captioned(base: Image.Image, caption: str) -> Image.Image:
    w, h = base.size
    meme = Image.new("RGB", (w, h + 260), "white")
    meme.paste(base, (0, 260))
    font, _ = _font(110)
    ImageDraw.Draw(meme).text((40, 60), caption, fill="black", font=font)
    return meme.resize((w // 2, (h + 260) // 2))


def synthetic_photo(seed: int, size=(1600, 1200), palette=None) -> Image.Image:
    """A detailed procedural 'photo': gradient sky, textured shapes, noise. Rich in ORB keypoints."""
    rnd = random.Random(seed)
    w, h = size
    own = [tuple(rnd.randrange(256) for _ in range(3)) for _ in range(6)]
    palette = palette or own
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    c0, c1 = palette[0], palette[1]
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c0[i] * (1 - t) + c1[i] * t) for i in range(3)))
    for _ in range(140):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r = rnd.randrange(10, 140)
        col = palette[rnd.randrange(2, len(palette))]
        col = tuple(max(0, min(255, c + rnd.randrange(-50, 50))) for c in col)
        kind = rnd.random()
        if kind < 0.4:
            d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(0, 0, 0), width=2)
        elif kind < 0.8:
            d.rectangle([x - r, y - r // 2, x + r, y + r // 2], fill=col, outline=(255, 255, 255), width=2)
        else:
            pts = [(x + rnd.randrange(-r, r), y + rnd.randrange(-r, r)) for _ in range(5)]
            d.polygon(pts, fill=col, outline=(20, 20, 20))
    noise = Image.effect_noise(size, 18).convert("RGB")
    img = Image.blend(img, noise, 0.08)
    return img.filter(ImageFilter.SMOOTH)


def make(out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    (out / "sub" / "deeper").mkdir(parents=True)
    a = synthetic_photo(1)
    a.save(out / "a.jpg", quality=92)
    shutil.copyfile(out / "a.jpg", out / "sub" / "a_copy_other_mtime.jpg")
    os.utime(out / "sub" / "a_copy_other_mtime.jpg", (1_300_000_000, 1_300_000_000))
    a.save(out / "a_recompressed_q60.jpg", quality=60)
    a.save(out / "sub" / "a_converted.png")
    a.resize((800, 600), Image.Resampling.LANCZOS).save(out / "sub" / "deeper" / "a_half_size.jpg", quality=85)
    a.rotate(90, expand=True).save(out / "a_rotated90.jpg", quality=90)
    w, h = a.size
    a.crop((int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9))).save(out / "a_cropped20.jpg", quality=90)
    # meme: white caption bar on top + text
    captioned(a, "WHEN THE CODE WORKS").save(out / "a_meme_caption.jpg", quality=80)
    # unrelated
    b = synthetic_photo(2)
    b.save(out / "b.jpg", quality=90)
    captioned(b, "WHEN THE CODE WORKS").save(out / "b_same_caption.jpg", quality=80)
    # chat screenshot with accented Portuguese text
    shot = Image.new("RGB", (1080, 1920), "white")
    font, _ = _font(44)
    for i, line in enumerate(SCREENSHOT_LINES):
        ImageDraw.Draw(shot).text((60, 200 + i * 120), line, fill="black", font=font)
    shot.save(out / "Screenshot_20240102-101112.png")
    rnd1 = random.Random(1)
    a_palette = [tuple(rnd1.randrange(256) for _ in range(3)) for _ in range(6)]
    synthetic_photo(3, palette=a_palette).save(out / "c_similar_colours.jpg", quality=90)
    # odd extension (sniffed) and a broken file
    shutil.copyfile(out / "b.jpg", out / "download_b.jpg_large")
    (out / "broken.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    (out / "notes.txt").write_text("not an image")
    return EXPECTED


if __name__ == "__main__":
    make(Path(sys.argv[1]))
    print("fixtures written to", sys.argv[1])
