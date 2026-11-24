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


INTRO_Y = 205          # рядок «Донеччина на мапі DeepState» – над шапкою, нижче верхнього інтерфейсу


def _intro_layer(text: str):
    """Маска рядка-заголовка «Донеччина на мапі DeepState» (білий текст, жирний)."""
    from PIL import ImageDraw
    import change_map
    probe = ImageDraw.Draw(Image.new("L", (10, 10)))
    size = 56
    while size > 36 and probe.textlength(text, font=change_map._font("800", size)) > W - 80:
        size -= 2
    lay = Image.new("L", (W, size + 30), 0)
    ImageDraw.Draw(lay).text((40, 4), text, font=change_map._font("800", size), fill=255)
    return lay


def make_video(first: Path, map_base: Path | None, map_full: Path | None, card: Path, out: Path,
               steps: list[tuple[Path, float, Path]] | None = None, segments: list[dict] | None = None) -> Path | None:
    """segments – частини відео, кожна зі своєю мапою: {"base", "full", "steps", "intro"} (Донеччина, потім
    Луганщина чи далекі ділянки). Без segments – як раніше: одна мапа map_base/map_full/steps."""
    if not shutil.which("ffmpeg"):
        return None
    a, c = _vert(Image.open(first)), _vert(Image.open(card))
    seq: list[Image.Image] = []
    intros: list[tuple[int, int, int, Image.Image]] = []   # (початок, кінець мапи, кінець переходу, маска рядка)

    def hold(img, sec):
        seq.extend([img] * int(sec * FPS))

    def fade(x, y, sec):
        n = int(sec * FPS)
        seq.extend(Image.blend(x, y, i / n) for i in range(1, n + 1))

    if segments is None and map_base and map_full:
        segments = [{"base": map_base, "full": map_full, "steps": steps, "intro": None}]
    segments = segments or []
    prev_full = None
    for si, sg in enumerate(segments):
        mb, mf = _vert(Image.open(sg["base"])), _vert(Image.open(sg["full"]))
        if prev_full is not None:
            fade(prev_full, mb, 0.3)              # тут згасає рядок попередньої частини
        start = len(seq)
        # 24.11.2026 (Юля): одразу з мапи (без титульного кадру); ділянки спалахують по черзі, кожна двічі
        if sg.get("steps"):
            prev = mb
            for i_, (p, sec, off) in enumerate(sg["steps"]):
                st, of = _vert(Image.open(p)), _vert(Image.open(off))
                if i_:
                    fade(prev, of, 0.2)
                else:
                    hold(of, 0.2)
                fade(of, st, 0.15)                        # спалах 1
                hold(st, 0.35)
                fade(st, of, 0.15)
                hold(of, 0.15)
                fade(of, st, 0.15)                        # спалах 2 – і лишається
                hold(st, max(0.4, sec - 1.15))
                prev = st
            fade(prev, mf, 0.3)
        else:
            hold(mb, 0.2)
            fade(mb, mf, 0.15)
            hold(mf, 0.35)
            fade(mf, mb, 0.15)
            hold(mb, 0.15)
            fade(mb, mf, 0.15)
        hold(mf, 4 if si == len(segments) - 1 else 2.5)
        end = len(seq)
        if sg.get("intro"):
            intros.append((start, end, end + int((0.3 if si < len(segments) - 1 else 0.4) * FPS),
                           _intro_layer(sg["intro"])))
        prev_full = mf
    if prev_full is not None:
        fade(prev_full, c, 0.4)
    elif map_full:                            # без кадрів «до змін» — просто мапа
        mf = _vert(Image.open(map_full))
        hold(mf, 6)
        fade(mf, c, 0.4)
    else:
        hold(a, 3)
        fade(a, c, 0.5)
    hold(c, 4)
    with tempfile.TemporaryDirectory() as td:
        # 24.11.2026 (Юля): рядок «Донеччина на мапі DeepState» швидко проявляється (~0,4 с) на початку
        # кожної частини й згасає на переході до наступної мапи чи до кінцевого кадру
        n_in = int(0.4 * FPS)
        for i, im in enumerate(seq):
            for s0, s1, s2, lay in intros:
                if s0 <= i < s2:
                    k = min(1.0, (i - s0 + 1) / n_in) if i < s1 else max(0.0, 1 - (i - s1 + 1) / max(1, s2 - s1))
                    im = im.copy()
                    im.paste(Image.new("RGB", lay.size, (250, 250, 250)), (0, INTRO_Y), lay.point(lambda v, k=k: int(v * k)))
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
