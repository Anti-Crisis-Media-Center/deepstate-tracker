"""Коротке вертикальне відео (TikTok / YouTube Shorts) з підсумку: цифра → мапа, де зміни «наливаються» жовтим → картка.
Без голосу й музики — музику додають при завантаженні. ffmpeg є на GitHub-раннерах."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

W, H, FPS = 1080, 1920, 30
BG = (13, 20, 40)


def _vert(img: Image.Image) -> Image.Image:
    if img.size == (W, H):
        return img.convert("RGB")
    c = Image.new("RGB", (W, H), BG)
    c.paste(img.convert("RGB").resize((W, W)), (0, (H - W) // 2))
    return c


def make_video(first: Path, map_base: Path | None, map_full: Path | None, card: Path, out: Path) -> Path | None:
    if not shutil.which("ffmpeg"):
        return None
    a, c = _vert(Image.open(first)), _vert(Image.open(card))
    seq: list[Image.Image] = []

    def hold(img, sec):
        seq.extend([img] * int(sec * FPS))

    def fade(x, y, sec):
        n = int(sec * FPS)
        seq.extend(Image.blend(x, y, i / n) for i in range(1, n + 1))

    hold(a, 2.5)
    if map_base and map_full:
        mb, mf = _vert(Image.open(map_base)), _vert(Image.open(map_full))
        fade(a, mb, 0.5)
        hold(mb, 0.7)
        for _ in range(2):                    # нові ділянки «наливаються» й пульсують
            fade(mb, mf, 0.8)
            hold(mf, 0.4)
            fade(mf, mb, 0.4)
        fade(mb, mf, 0.8)
        hold(mf, 2.5)
        fade(mf, c, 0.5)
    else:
        fade(a, c, 0.5)
    hold(c, 4)
    with tempfile.TemporaryDirectory() as td:
        for i, im in enumerate(seq):
            im.save(f"{td}/f{i:04d}.jpg", quality=90)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{td}/f%04d.jpg",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-movflags", "+faststart", str(out)], check=True)
    return out
