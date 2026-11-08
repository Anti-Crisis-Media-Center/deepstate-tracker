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
    """blocks: [{"label": "ДОНЕЧЧИНА", "big": "+4,92 км²", "text": "позначено окупованими",
                 "note": "поблизу: …"}]. Якщо big порожній — показується лише text."""
    img = Image.new("RGB", (W, H), NAVY_BOTTOM)
    # вертикальний градієнт
    grad = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(NAVY_TOP[i] * (1 - t) + NAVY_BOTTOM[i] * t) for i in range(3))
        grad.line([(0, y), (W, y)], fill=c)

    bg = img.copy()
    # обкладинка зверху з плавним переходом у фон
    if cover.exists():
        cv = Image.open(cover).convert("RGB").resize((W, W))
        mask = Image.new("L", (W, W), 255)
        md = ImageDraw.Draw(mask)
        fade = 220
        for i in range(fade):
            md.line([(0, W - fade + i), (W, W - fade + i)], fill=int(255 * (1 - i / fade)))
        for i in range(90):  # м'який перехід і зверху
            md.line([(0, i), (W, i)], fill=int(255 * i / 90))
        img.paste(cv, (0, SAFE_TOP), mask)

    d = ImageDraw.Draw(img)
    max_w = W - 2 * MARGIN
    footer_top = H - 200 - SAFE_BOTTOM - 60          # нижня межа для змісту (над лінією підвалу)

    def layout(k: float, draw: bool, y0: int, notes: bool = True) -> int:
        """Малює (або лише міряє) зміст із масштабом шрифтів k; повертає кінцеву y."""
        f_date, f_label = _font("800", int(46 * k)), _font("800", int(38 * k))
        f_big, f_text, f_note = _font("800", int(104 * k)), _font("500", int(44 * k)), _font("500", int(40 * k))
        y = y0
        if draw:
            d.text((MARGIN, y), date_label, font=f_date, fill=YELLOW)
        y += int(90 * k)
        for b in blocks:
            if b.get("label"):
                if draw:
                    d.text((MARGIN, y), b["label"], font=f_label, fill=GREY)
                y += int(58 * k)
            if b.get("big"):
                if draw:
                    d.text((MARGIN, y), b["big"], font=f_big, fill=YELLOW)
                y += int(122 * k)
            for line in _wrap(d, b.get("text", ""), f_text, max_w):
                if draw:
                    d.text((MARGIN, y), line, font=f_text, fill=WHITE)
                y += int(58 * k)
            if notes and b.get("note"):
                for line in _wrap(d, b["note"], f_note, max_w):
                    if draw:
                        d.text((MARGIN, y), line, font=f_note, fill=GREY)
                    y += int(54 * k)
            y += int(30 * k)
        return y

    # якщо змісту багато: зменшуємо шрифт (до 80%), прибираємо «поблизу», піднімаємо зміст на низ обкладинки
    choice = None
    for y0_try, notes_try in ((W - 40 + SAFE_TOP, True), (W - 40 + SAFE_TOP, False), (W - 230 + SAFE_TOP, True), (W - 230 + SAFE_TOP, False), (W - 380 + SAFE_TOP, True), (W - 380 + SAFE_TOP, False)):
        k_try = 1.0
        while k_try > 0.8 and layout(k_try, False, y0_try, notes_try) > footer_top:
            k_try -= 0.05
        if layout(k_try, False, y0_try, notes_try) <= footer_top:
            choice = (y0_try, notes_try, k_try)
            break
    if choice is None:
        k_try = 0.8
        while k_try > 0.5 and layout(k_try, False, W - 380 + SAFE_TOP, False) > footer_top:
            k_try -= 0.05
        choice = (W - 380 + SAFE_TOP, False, k_try)
    y0, notes, k = choice
    if y0 < W - 40 + SAFE_TOP:
        img.paste(bg.crop((0, y0 - 40, W, W + SAFE_TOP)), (0, y0 - 40))
        d = ImageDraw.Draw(img)
    layout(k, True, y0, notes)

    # підвал: заклик
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
