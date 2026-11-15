"""Вертикальна картинка 1080×1920 для сторіз Facebook.

Зверху — обкладинка (доба / тиждень / місяць), нижче — головні цифри
і заклик підписатися на Telegram. Без ШІ: Pillow + шрифт Montserrat (OFL).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "assets" / "fonts"

W, H = 1080, 1920
NAVY_TOP = (22, 34, 66)
NAVY_BOTTOM = (10, 17, 36)
YELLOW = (255, 204, 51)     # фірмовий жовтий АКМЦ #FFCC33
WHITE = (255, 255, 255)
GREY = (190, 198, 216)
MARGIN = 80
# Безпечні зони: зверху Instagram/Facebook показують ім'я профілю, знизу — поле відповіді
SAFE_TOP = 190
SAFE_BOTTOM = 170


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / f"Montserrat-{weight}.ttf"
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if weight == "800" else "DejaVuSans.ttf", size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_story(cover: Path, date_label: str, blocks: list[dict], out: Path,
                 footer: str = "Деталі — щоранку о 8:00", handle: str = "Telegram: @acmc_ua") -> Path:
    """Синє тло; обкладинка — прямокутник на тому ж місці й тієї ж ширини, що мапа у відео
    (x 40–1040, верх 400). Висота: повна обкладинка, якщо текст уміщається, інакше — компактна
    (заголовок + нижня смуга з регіоном, «7 днів» і лого). Період — жовтим над обкладинкою."""
    BG = (13, 19, 38)
    OX, TOP, MW = 40, 400, 1000
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    max_w = W - 2 * MARGIN
    footer_top = H - 200 - SAFE_BOTTOM - 40

    def cover_img(compact: bool):
        cv = Image.open(cover).convert("RGB").resize((1080, 1080))
        if compact:
            top, bot, blend = cv.crop((0, 0, 1080, 300)), cv.crop((0, 660, 1080, 1050)), 60
            c2 = Image.new("RGB", (1080, 300 + 390 - blend)); c2.paste(top, (0, 0))
            m = Image.new("L", (1080, 390), 255); mdd = ImageDraw.Draw(m)
            for i in range(blend):
                mdd.line([(0, i), (1080, i)], fill=int(255 * i / blend))
            c2.paste(bot, (0, 300 - blend), m)
            cv = c2
        return cv.resize((MW, int(cv.height * MW / 1080)), Image.LANCZOS)

    def layout(k: float, draw: bool, y0: int, notes: bool = True) -> int:
        f_label = _font("800", int(38 * k))
        f_big, f_text, f_note = _font("800", int(104 * k)), _font("500", int(44 * k)), _font("500", int(40 * k))
        y = y0
        for b in blocks:
            if b.get("label"):
                if draw:
                    d.text((MARGIN, y), b["label"], font=f_label, fill=GREY)
                y += int(58 * k)
            if b.get("big"):
                if draw:
                    d.text((MARGIN, y), b["big"], font=f_big, fill=YELLOW)
                y += int(122 * k)
            f_bold = _font("800", int(44 * k))
            for line in _wrap(d, b.get("text", ""), f_text, max_w):
                if draw:   # «окупованими» / «деокупованими» — жирним, як на першому кадрі відео (рішення 10.11)
                    x = MARGIN
                    for wi, w in enumerate(line.split(" ")):
                        fnt = f_bold if w.strip(".,:;·") in ("окупованими", "деокупованими") else f_text
                        tok = (" " if wi else "") + w
                        d.text((x, y), tok, font=fnt, fill=WHITE)
                        x += d.textlength(tok, font=fnt)
                y += int(58 * k)
            if notes and b.get("note"):
                for line in _wrap(d, b["note"], f_note, max_w):
                    if draw:
                        d.text((MARGIN, y), line, font=f_note, fill=GREY)
                    y += int(54 * k)
            y += int(30 * k)
        return y

    choice = None
    opts = [(False, True, 0.85), (False, False, 0.85), (True, True, 0.8), (True, False, 0.8), (True, False, 0.55)]
    if sum(1 for b in blocks if b.get("big") and b.get("note")) >= 2:
        # кілька цифр, кожна зі своїми назвами (напр. окупація й деокупація) — назви важливіші
        # за повну обкладинку: спершу пробуємо зменшену обкладинку, але з назвами (рішення 10.11)
        opts = [(False, True, 0.85), (True, True, 0.8), (True, True, 0.6), (False, False, 0.85),
                (True, False, 0.8), (True, False, 0.55)]
    for compact, notes, kmin in opts:
        cv = cover_img(compact)
        y0 = TOP + cv.height + 40
        k = 1.0
        while k > kmin and layout(k, False, y0, notes) > footer_top:
            k -= 0.05
        if layout(k, False, y0, notes) <= footer_top:
            choice = (cv, y0, notes, k)
            break
    if choice is None:
        cv = cover_img(True); choice = (cv, TOP + cv.height + 40, False, 0.55)
    cv, y0, notes, k = choice
    img.paste(cv, (OX, TOP))
    d = ImageDraw.Draw(img)
    d.text((MARGIN, TOP - 160), date_label, font=_font("800", 62), fill=YELLOW)   # період — над обкладинкою
    layout(k, True, y0, notes)

    f_foot = _font("500", 40)
    f_handle = _font("800", 56)
    fy = H - 200 - SAFE_BOTTOM
    d.line([(MARGIN, fy - 30), (W - MARGIN, fy - 30)], fill=(60, 74, 110), width=3)
    d.text((MARGIN, fy), footer, font=f_foot, fill=WHITE)
    d.text((MARGIN, fy + 58), handle, font=f_handle, fill=YELLOW)
    d.text((MARGIN, fy + 140), "За даними DeepStateMap.live", font=_font("500", 30), fill=GREY)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out
