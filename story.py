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

    d = ImageDraw.Draw(img)

    def paste_cover(compact: bool) -> int:
        """Обкладинка зверху з плавним переходом у фон; повертає її нижній край.
        compact — без середини з мапою: лише заголовок і нижня смуга (регіон, «24 години» / «7 днів», лого),
        щоб для довгого змісту зберегти однаковий вигляд з усіма позначками."""
        if not cover.exists():
            return SAFE_TOP
        cv = Image.open(cover).convert("RGB").resize((W, W))
        if compact:
            top, bot, blend = cv.crop((0, 0, W, 300)), cv.crop((0, 660, W, 1050)), 60
            c2 = Image.new("RGB", (W, 300 + 390 - blend))
            c2.paste(top, (0, 0))
            m = Image.new("L", (W, 390), 255)
            mdd = ImageDraw.Draw(m)
            for i in range(blend):
                mdd.line([(0, i), (W, i)], fill=int(255 * i / blend))
            c2.paste(bot, (0, 300 - blend), m)
            cv = c2
        h = cv.height
        mask = Image.new("L", (W, h), 255)
        md = ImageDraw.Draw(mask)
        fade = 120 if compact else 220
        for i in range(fade):
            md.line([(0, h - fade + i), (W, h - fade + i)], fill=int(255 * (1 - i / fade)))
        for i in range(90):  # м'який перехід і зверху
            md.line([(0, i), (W, i)], fill=int(255 * i / 90))
        img.paste(cv, (0, SAFE_TOP), mask)
        return SAFE_TOP + h
    max_w = W - 2 * MARGIN
    footer_top = H - 200 - SAFE_BOTTOM - 60          # нижня межа для змісту (над лінією підвалу)

    def layout(k: float, draw: bool, y0: int, notes: bool = True) -> int:
        """Малює (або лише міряє) зміст із масштабом шрифтів k; повертає кінцеву y."""
        f_date, f_label = _font("800", int(62 * k)), _font("800", int(38 * k))
        f_big, f_text, f_note = _font("800", int(104 * k)), _font("500", int(44 * k)), _font("500", int(40 * k))
        y = y0
        if draw:
            d.text((MARGIN, y), date_label, font=f_date, fill=YELLOW)
        y += int(105 * k)
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

    # Повна обкладинка, якщо зміст уміщається (шрифт можна зменшити до 85%); інакше — компактна обкладинка
    # (заголовок + смуга з регіоном, «24 години» / «7 днів» і лого), а не обрізана знизу.
    def fits(y0, notes, k_min):
        k = 1.0
        while k > k_min and layout(k, False, y0, notes) > footer_top:
            k -= 0.05
        return k if layout(k, False, y0, notes) <= footer_top else None

    full_y0 = W - 40 + SAFE_TOP
    compact_y0 = SAFE_TOP + 300 + 390 - 60 + 10
    choice = None
    for compact, y0_try, notes_try, k_min in ((False, full_y0, True, 0.85), (False, full_y0, False, 0.85),
                                              (True, compact_y0, True, 0.8), (True, compact_y0, False, 0.8),
                                              (True, compact_y0, False, 0.5)):
        k_try = fits(y0_try, notes_try, k_min)
        if k_try is not None:
            choice = (compact, y0_try, notes_try, k_try)
            break
    if choice is None:
        choice = (True, compact_y0, False, 0.5)
    compact, y0, notes, k = choice
    paste_cover(compact)
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
