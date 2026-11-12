"""Міні-мапа змін за добу/тиждень: де саме змінилися позначення на мапі DeepState.

Малюємо з того, що бот уже зберігає: межі області, окупована територія (state/current.json),
нові ділянки (changes/<дата>.geojson), населені пункти (reference/settlements.geojson).
Без підкладки-карти зі сторонніх сервісів — лише наші дані, тож ліцензійних питань немає.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import box, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
FONTS = ROOT / "assets" / "fonts"

BG = (13, 20, 40)
LAND = (34, 46, 78)
OCC = (120, 52, 60)          # окуповано (приглушений червоний)
GREY = (92, 92, 104)         # район проникнення
NEW = (255, 204, 51)         # нові ділянки — фірмовий жовтий
BORDER = (150, 165, 200)
WHITE, MUTED = (250, 250, 250), (175, 185, 205)

CITIES = {"Краматорськ", "Слов'янськ", "Дружківка", "Костянтинівка", "Покровськ", "Мирноград", "Добропілля",
          "Білозерське", "Родинське", "Білицьке", "Лиман", "Сіверськ", "Часів Яр", "Торецьк", "Бахмут",
          "Курахове", "Селидове", "Новогродівка", "Українськ", "Гірник", "Святогірськ", "Миколаївка",
          "Авдіївка", "Мар'їнка", "Вугледар", "Залізне", "Соледар", "Світлодарськ", "Красногорівка"}


def _font(w: str, n: int):
    return ImageFont.truetype(str(FONTS / f"Montserrat-{w}.ttf"), n)


def _geoms(gj):
    if gj.get("type") == "FeatureCollection":
        return [shape(f["geometry"]) for f in gj["features"]]
    return [shape(gj)]


def render(change_files: list[Path], out: Path, title: str, subtitle: str, size: int = 1080,
           oblast_key: str = "donetsk", show_new: bool = True, height: int | None = None,
           must_label: list | None = None) -> Path | None:
    """height=None — квадрат для дописів; height=1920 — вертикальна мапа для відео (TikTok/Shorts),
    з полями під інтерфейс платформ зверху і знизу."""
    H = height or size
    vertical = H >= 1600          # TikTok/Shorts 9:16; 1350 — допис Instagram 4:5 (як квадрат, лише вища мапа)
    changes = []
    for p in change_files:
        if p.exists():
            gj = json.loads(p.read_text())
            changes += [shape(f["geometry"]) for f in gj["features"]
                        if f["properties"].get("oblast") == oblast_key]
    if not changes:
        return None
    new = unary_union(changes)
    st = json.loads((DATA / "state" / "current.json").read_text())
    occ = unary_union(_geoms(st["occupied"][oblast_key]))
    unk = unary_union(_geoms(st["unknown"][oblast_key])) if st.get("unknown", {}).get(oblast_key) else None
    obl = json.loads((DATA / "reference" / "oblasts.geojson").read_text())
    region = unary_union([shape(f["geometry"]) for f in obl["features"]])  # обидві області

    # кадр: нова ділянка + ~18 км навколо, квадрат з урахуванням широти
    cx, cy = new.centroid.x, new.centroid.y
    kx = math.cos(math.radians(cy))
    minx, miny, maxx, maxy = new.bounds
    half = max((maxx - minx) * kx, maxy - miny) / 2 + 0.16  # у «градусах широти»
    if vertical:
        top, bottom = 400, 480                               # TikTok/Shorts: інтерфейс зверху й знизу
        mw = size - 80
        mh = H - top - bottom
    elif H != size:                                          # Instagram 4:5: мапа майже на всю ширину
        top, bottom = 230, 170
        mw = size - 80
        mh = H - top - bottom
    else:
        top, bottom = 230, 170                               # місце під заголовок і підпис
        mh = mw = size - top - bottom
    ox = (size - mw) // 2
    sc = mw / (2 * half)                                     # пікселів на градус широти
    hx, hy = half, mh / (2 * sc)
    view = box(cx - hx / kx, cy - hy, cx + hx / kx, cy + hy)

    def px(x, y):
        return (ox + (x - (cx - hx / kx)) * kx * sc, top + (cy + hy - y) * sc)

    S = 2  # малюємо вдвічі більшим для згладжування
    big = Image.new("RGB", (size * S, H * S), BG)
    d = ImageDraw.Draw(big)

    def poly(g, fill=None, outline=None, width=1):
        g = g.intersection(view)
        for p in getattr(g, "geoms", [g]):
            if p.is_empty or p.geom_type != "Polygon":
                continue
            pts = [(a * S, b * S) for a, b in (px(x, y) for x, y in p.exterior.coords)]
            d.polygon(pts, fill=fill, outline=outline, width=width * S)
            for hole in p.interiors:
                d.polygon([(a * S, b * S) for a, b in (px(x, y) for x, y in hole.coords)], fill=LAND)

    poly(region, fill=LAND)
    poly(occ, fill=OCC)
    if unk is not None and not unk.is_empty:
        poly(unk, fill=GREY)
    if not show_new:
        new_draw = False
    else:
        new_draw = True
    # світіння навколо нових ділянок
    glow = Image.new("L", big.size, 0)
    gd = ImageDraw.Draw(glow)
    for p in getattr(new, "geoms", [new]):
        pts = [(a * S, b * S) for a, b in (px(x, y) for x, y in p.exterior.coords)]
        gd.polygon(pts, fill=255)
    glow = glow.filter(ImageFilter.MaxFilter(31)).filter(ImageFilter.GaussianBlur(14))
    if new_draw:
        big.paste(Image.new("RGB", big.size, NEW), (0, 0), glow.point(lambda v: int(v * 0.45)))
        d = ImageDraw.Draw(big)
        poly(new, fill=NEW, outline=(255, 235, 150), width=1)
    img = big.resize((size, H), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size, top - 10], fill=BG)
    d.rectangle([0, top + mh + 10, size, H], fill=BG)

    # населені пункти в кадрі: міста + найближчі села до змін
    st_feats = json.loads((DATA / "reference" / "settlements.geojson").read_text())["features"]
    pts = []
    for f in st_feats:
        g = shape(f["geometry"]).centroid
        if view.contains(g):
            nm = f["properties"]["ADM4_UA"]
            from tracker import is_city
            pts.append((is_city(nm, f["properties"].get("ADM2_UA")), -g.distance(new), nm, g))
    near = sorted([p for p in pts if not p[0]], key=lambda p: -p[1])[:2]   # 2 найближчі села
    cities = sorted([p for p in pts if p[0]], key=lambda p: -p[1])
    # назви із заголовка — першими (12.11.2026: на мапі не було села, назване в заголовку)
    must = set(must_label or [])
    must_pts, seen_m = [], set()
    for p_ in sorted([p for p in pts if p[2] in must], key=lambda p: -p[1]):   # лише найближчий тезка
        if p_[2] not in seen_m:
            seen_m.add(p_[2])
            must_pts.append(p_)
    pts = [p for p in pts if p[2] not in must]   # далекі тезки (інша Миколаївка) не підписуємо
    # є назви із заголовка — інших сіл не додаємо, щоб на мапі не з'являлися «зайві» назви
    near = [] if must_pts else sorted([p for p in pts if not p[0]], key=lambda p: -p[1])[:2]
    cities = sorted([p for p in pts if p[0]], key=lambda p: -p[1])
    pts = must_pts + near + cities
    shown, boxes, used = 0, [], set()
    for is_city, _, nm, g in pts:
        if nm in used:
            continue
        if shown >= 7:
            continue
        x, y = px(g.x, g.y)
        if not (ox + 20 < x < ox + mw - 20 and top + 20 < y < top + mh - 20):
            continue
        f = _font("800" if is_city else "500", 34 if is_city else 28)
        w = d.textlength(nm, font=f)
        hit = lambda bb: any(not (bb[2] < b[0] or bb[0] > b[2] or bb[3] < b[1] or bb[1] > b[3]) for b in boxes)
        cand = [(x + 12, y - 18), (x - 12 - w, y - 18)]
        if nm in must:   # назву із заголовка не пропускаємо: пробуємо також під і над точкою
            cand += [(x + 12, y + 14), (x - 12 - w, y + 14), (x + 12, y - 52), (x - 12 - w, y - 52)]
        cand = [c for c in cand if ox <= c[0] and c[0] + w <= ox + mw] or cand[:1]
        pos = next(((a_, b_) for a_, b_ in cand if not hit((a_ - 4, b_ - 2, a_ + w + 4, b_ + 40))), None)
        if pos is None:
            continue
        tx, ty = pos
        bb = (tx - 4, ty - 2, tx + w + 4, ty + 40)
        boxes.append(bb)
        used.add(nm)
        r = 7 if is_city else 5
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)
        d.text((tx + 2, ty + 2), nm, font=f, fill=(0, 0, 0))
        d.text((tx, ty), nm, font=f, fill=WHITE)
        shown += 1

    km_px = sc / 111.0

    ty0 = top - 200 if vertical else 60
    d.text((60, ty0), title, font=_font("800", 66 if vertical else 58), fill=NEW)
    d.text((60, ty0 + 85), subtitle, font=_font("500", 40), fill=WHITE)
    lx, ly = 60, top + mh + 40
    lf = _font("500", 26)
    for col, label in ((NEW, "зміни"), (OCC, "окуповано"), (GREY, "район проникнення")):
        d.rectangle([lx, ly + 4, lx + 26, ly + 30], fill=col)
        d.text((lx + 36, ly), label, font=lf, fill=MUTED)
        lx += 36 + d.textlength(label, font=lf) + 36
    # масштаб — у рядку легенди, довжина відрізка підбирається, щоб його було видно
    km = next((k for k in (5, 10, 20, 50, 100) if k * km_px >= 70), 100)
    bar = k_px = km * km_px
    t1 = "масштаб"
    w1 = d.textlength(t1, font=lf)
    t2 = f"{km} км"
    w2 = d.textlength(t2, font=lf)
    total = w1 + 14 + bar + 14 + w2
    sx = size - 60 - total
    sy2 = ly + 70
    d.text((sx, sy2), t1, font=lf, fill=MUTED)
    bx0, bx1, by = sx + w1 + 14, sx + w1 + 14 + bar, sy2 + 17
    d.line([bx0, by, bx1, by], fill=MUTED, width=4)
    d.line([bx0, by - 9, bx0, by + 9], fill=MUTED, width=3)
    d.line([bx1, by - 9, bx1, by + 9], fill=MUTED, width=3)
    d.text((bx1 + 14, sy2), t2, font=lf, fill=MUTED)
    d.text((60, sy2), "За даними DeepStateMap.live · АКМЦ", font=_font("500", 26), fill=MUTED)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out


if __name__ == "__main__":
    import sys
    p = render([DATA / "changes" / f"{sys.argv[1]}.geojson"], Path(sys.argv[2]),
               "Біля Василівки та Світлого", "+4,24 км² позначено окупованими за добу")
    print(p)
