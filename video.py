"""Коротке вертикальне відео (TikTok / YouTube Shorts) з підсумку: цифра → мапа, де зміни «наливаються» жовтим → картка.
Музика — трек «новинний фон» Сергія (assets/music/news.mp3, рішення 10.11.2026); якщо файлу немає —
запасна згенерована музика з music.py. Без голосу. ffmpeg є на GitHub-раннерах."""
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


def _headline(img: Image.Image, headline) -> Image.Image:
    """ДЕМО 17.11: велика цифра над мапою — щоб із першого кадру було видно, про що відео."""
    if not headline:
        return img
    from PIL import ImageDraw, ImageFont
    fonts = Path(__file__).parent / "assets" / "fonts"
    big, what, _ = headline
    im = img.copy()
    d = ImageDraw.Draw(im)
    f1 = ImageFont.truetype(str(fonts / "Montserrat-800.ttf"), 128)
    f2 = ImageFont.truetype(str(fonts / "Montserrat-500.ttf"), 46)
    d.text((W // 2, 175), big, font=f1, fill=(247, 197, 49), anchor="mm")
    d.text((W // 2, 268), what, font=f2, fill=(250, 250, 250), anchor="mm")
    return im


def make_video(first: Path, map_base: Path | None, map_full: Path | None, card: Path, out: Path,
               steps: list[tuple[Path, float, Path]] | None = None, intro: bool = True, headline=None,
               step_headlines=None, blinks_per_step: int | None = None, start_full: bool = True) -> Path | None:
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

    if not intro and map_base and map_full:   # ДЕМО 17.11: без титульного кадру — одразу мапа з червоним
        H_ = lambda im: _headline(im, headline)  # noqa: E731
        mb, mf = H_(_vert(Image.open(map_base))), H_(_vert(Image.open(map_full)))
        if start_full or not steps:
            hold(mf, 1.2)
            fade(mf, mb, 0.4)
            hold(mb, 0.4)
            prev = mb
        elif blinks_per_step == 0:   # ДЕМО 18.11 (Юля): без блимання — ділянки «розповзаються» по черзі
            prev = H_(_vert(Image.open(steps[0][2])))   # мапа до першої ділянки
            hold(prev, 0.3)
            for p, sec, off in steps:
                st = H_(_vert(Image.open(p)))
                fade(prev, st, 0.5)
                hold(st, 0.5)
                prev = st
            hold(prev, 4)
            fade(prev, c, 0.5)
            hold(c, 5)
            return _encode(seq, out)
        else:   # ДЕМО 17.11 (Юля): починаємо з першого, найменшого етапу — він уже світиться з першого кадру
            prev = H_(_vert(Image.open(steps[0][0])))
            hold(prev, 0.5)
        for k, (p, sec, off) in enumerate(steps or []):
            hk = (lambda im: _headline(im, step_headlines[k])) if step_headlines else H_
            st, of = hk(_vert(Image.open(p))), hk(_vert(Image.open(off)))
            fade(prev, of, 0.5)
            blinks = blinks_per_step or max(2, int(sec / 1.0) - 1)
            for _ in range(blinks):
                fade(of, st, 0.4)
                hold(st, 0.3)
                fade(st, of, 0.3)
            fade(of, st, 0.4)
            hold(st, max(0.6, sec - blinks * 1.0 - 1.0))
            prev = st
        if not steps:
            for _ in range(2):
                fade(mb, mf, 0.8)
                hold(mf, 0.4)
                fade(mf, mb, 0.4)
            prev = mb
        fade(prev, mf, 0.6)
        hold(mf, 4)
        fade(mf, c, 0.5)
        hold(c, 5)
        return _encode(seq, out)
    hold(a, 5.0)   # перший кадр: 5 с — щоб устигнути прочитати назви й цифри (рішення 10.11: +1 с після фідбеку)
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
            hold(mf, 5)
        else:
            for _ in range(2):                # нові ділянки «наливаються» й пульсують
                fade(mb, mf, 0.8)
                hold(mf, 0.4)
                fade(mf, mb, 0.4)
            fade(mb, mf, 0.8)
            hold(mf, 5)
        fade(mf, c, 0.5)
    elif map_full:                            # без кадру «до змін» — просто мапа
        mf = _vert(Image.open(map_full))
        fade(a, mf, 0.5)
        hold(mf, 6)
        fade(mf, c, 0.5)
    else:
        fade(a, c, 0.5)
    hold(c, 6)
    return _encode(seq, out)


def _encode(seq, out: Path) -> Path:
    with tempfile.TemporaryDirectory() as td:
        for i, im in enumerate(seq):
            im.save(f"{td}/f{i:04d}.jpg", quality=90)
        dur = len(seq) / FPS
        af = []
        news = Path(__file__).parent / "assets" / "music" / "news.mp3"
        if news.exists():
            # Ньюсовий підклад, який Сергій написав для АКМЦ (рішення Юлі 10.11) — на всі Reels/Shorts/TikTok.
            # Трек рівний (~4 хв), тож щотижня беремо інший фрагмент, щоб звук не набридав.
            import datetime
            total = 264.0
            span = max(1.0, total - dur - 2)
            start = (datetime.date.today().isocalendar()[1] * 37) % int(span)
            audio = ["-ss", str(start), "-t", f"{dur:.2f}", "-i", str(news)]
            af = ["-af", f"loudnorm=I=-16:TP=-1.5,afade=t=in:d=0.3,afade=t=out:st={max(0, dur - 1.7):.2f}:d=1.7"]
        else:
            try:   # запасний варіант: тиха музика, згенерована тут же
                from music import make_ambient
                import os
                audio = ["-i", str(make_ambient(dur, Path(td) / "music.wav", os.getenv("MUSIC_VARIANT") or None))]
            except Exception:  # noqa: BLE001
                audio = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{td}/f%04d.jpg",
                        *audio, *af, "-shortest",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
    return out
