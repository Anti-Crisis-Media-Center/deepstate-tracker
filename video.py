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


def make_video(first: Path, map_base: Path | None, map_full: Path | None, card: Path, out: Path,
               steps: list[tuple[Path, float, Path]] | None = None) -> Path | None:
    """steps — проміжні кадри мапи по черзі (окупація червоним, деокупація блакитним…) з тривалістю в секундах,
    пропорційною тому, скільки днів тривав цей етап; кожен блимає, далі — кінцевий стан map_full."""
    if not shutil.which("ffmpeg"):
        return None
    a, c = _vert(Image.open(first)), _vert(Image.open(card))
    seq: list[Image.Image] = []

    def hold(img, sec):
        seq.extend([img] * int(sec * FPS))

    def fade(x, y, sec):
        n = int(sec * FPS)
        seq.extend(Image.blend(x, y, i / n) for i in range(1, n + 1))

    hold(a, 4.3)   # перший кадр: 4,3 с — щоб устигнути прочитати назви й цифру (рішення 10.11)
    if map_base and map_full:
        mb, mf = _vert(Image.open(map_base)), _vert(Image.open(map_full))
        fade(a, mb, 0.5)
        hold(mb, 0.7)
        if steps:                             # хронологія: кожен крок блимає, потім кінцевий стан
            hold(mb, 0.8)
            prev = mb
            for p, sec, off in steps:
                # блимає лише те, що змінилося на цьому кроці: «до» (без кольору) ↔ «після» (червоне чи блакитне)
                st, of = _vert(Image.open(p)), _vert(Image.open(off))
                fade(prev, of, 0.8)                       # плавно: напр. червоне, що стало сірою зоною, сіріє
                blinks = max(2, int(sec / 1.0) - 1)       # одне блимання ≈ 1 с
                for _ in range(blinks):
                    fade(of, st, 0.4)
                    hold(st, 0.3)
                    fade(st, of, 0.3)
                fade(of, st, 0.4)
                hold(st, max(0.8, sec - blinks * 1.0 - 1.2))
                prev = st
            fade(prev, mf, 0.6)
            hold(mf, 4)
        else:
            for _ in range(2):                # нові ділянки «наливаються» й пульсують
                fade(mb, mf, 0.8)
                hold(mf, 0.4)
                fade(mf, mb, 0.4)
            fade(mb, mf, 0.8)
            hold(mf, 4)
        fade(mf, c, 0.5)
    elif map_full:                            # без кадру «до змін» — просто мапа
        mf = _vert(Image.open(map_full))
        fade(a, mf, 0.5)
        hold(mf, 5)
        fade(mf, c, 0.5)
    else:
        fade(a, c, 0.5)
    hold(c, 5)
    with tempfile.TemporaryDirectory() as td:
        for i, im in enumerate(seq):
            im.save(f"{td}/f{i:04d}.jpg", quality=90)
        dur = len(seq) / FPS
        try:   # тиха фонова музика, згенерована тут же (без чужих треків)
            from music import make_ambient
            import os
            audio = ["-i", str(make_ambient(dur, Path(td) / "music.wav", os.getenv("MUSIC_VARIANT") or None))]
        except Exception:  # noqa: BLE001
            audio = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{td}/f%04d.jpg",
                        *audio, "-shortest",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
    return out
