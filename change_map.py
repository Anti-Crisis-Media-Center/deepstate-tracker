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
OCC = (80, 38, 48)           # окуповано раніше (темний, щоб нові червоні ділянки вирізнялися)
DEOCC = (88, 196, 255)   # деокуповані ділянки — блакитні
FLIP = (235, 235, 245)      # «позначили й зняли» — світлий контур
GREY = (128, 128, 142)       # район проникнення (сіра зона) — світліша, як у DeepState
GREY_NEW = (190, 190, 205)   # з окупованих стали сірими — світло-сірі (це не деокупація)
NEW = (255, 204, 51)         # фірмовий жовтий — лише для акцентів (заголовок)
NEW_OCC = (196, 18, 32)      # нові окуповані ділянки — темно-червоні, «кров'яні»
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


def _net(occ_u, free_u):
    """Підсумок за період: те, що позначили окупованим і не зняли (червоне); те, з чого позначку зняли
    і не повернули (блакитне); ділянки, де за період позначку і поставили, і зняли (штрихування)."""
    def ok(g):
        return g if g is not None and not g.is_empty and g.area > 1e-9 else None
    if occ_u is None or free_u is None:
        return ok(occ_u), ok(free_u), None
    return ok(occ_u.difference(free_u)), ok(free_u.difference(occ_u)), ok(occ_u.intersection(free_u))


def _load_steps(change_files: list[Path], oblast_key: str) -> list[tuple[list, list]]:
    """Зміни по днях у хронологічному порядку: [(окуповані, зняті), ...]. «Зняті» — і деокуповані,
    і ті, що з окупованих стали сірою зоною (to_grey); їх розрізняє _grey_changes."""
    out = []
    for p in sorted(change_files, key=lambda x: x.name):
        occ, free = [], []
        if p.exists():
            for f in json.loads(p.read_text())["features"]:
                if f["properties"].get("oblast") == oblast_key:
                    (free if f["properties"].get("change") in ("deoccupied", "to_grey") else occ).append(
                        shape(f["geometry"]))
        if occ or free:
            out.append((occ, free))
    return out


def _timeline(steps: list[tuple[list, list]]):
    """Проходимо зміни по черзі. red — позначено окупованим (і ще не знято), blue — знято (і ще не повернуто),
    back — ділянки, що повернулися до стану на початок періоду («позначили й зняли»)."""
    from shapely.geometry import Polygon
    red = blue = back = Polygon()
    for occ, free in steps:
        for g in occ:   # повернули позначку на щойно зняте — ділянка знову як на початку
            back = back.union(g.intersection(blue))
            red = red.union(g.difference(blue))
            blue = blue.difference(g)
        for g in free:  # зняли щойно поставлену позначку — теж як на початку
            back = back.union(g.intersection(red))
            blue = blue.union(g.difference(red))
            red = red.difference(g)
    back = back.difference(red).difference(blue)
    ok = lambda g: g if g is not None and not g.is_empty and g.area > 1e-9 else None  # noqa: E731
    return ok(red), ok(blue), ok(back)


def step_groups(change_files: list[Path], oblast_key: str = "donetsk", max_steps: int = 4) -> list[int]:
    """Кроки для відео: межі (кількість днів зі змінами) після кожного кроку. Сусідні дні з однаковим
    напрямком (лише окупація / лише деокупація) — один крок. Порожньо — якщо був лише один напрямок."""
    steps = _load_steps(change_files, oblast_key)
    kinds = ["occ" if o and not f else "free" if f and not o else "mix" for o, f in steps]
    if len(set(kinds)) == 1 and kinds[0] != "mix":
        return []
    ends = [i + 1 for i in range(len(kinds)) if i + 1 == len(kinds) or kinds[i + 1] != kinds[i]]
    while len(ends) > max_steps:   # забагато кроків (місяць) — зливаємо найкоротші сусідні
        gaps = [ends[0]] + [ends[i] - ends[i - 1] for i in range(1, len(ends))]
        i = min(range(len(ends) - 1), key=lambda j: gaps[j] + gaps[j + 1])
        del ends[i]
    return ends


def step_info(change_files: list[Path], oblast_key: str = "donetsk") -> list[tuple[str, str]]:
    """Для кожного дня зі змінами: (дата, напрямок «occ» / «free» / «mix»)."""
    days = [p.stem for p in sorted(change_files, key=lambda x: x.name)
            if p.exists() and any(f["properties"].get("oblast") == oblast_key
                                  for f in json.loads(p.read_text())["features"])]
    return [(d, "occ" if o and not f else "free" if f and not o else "mix")
            for d, (o, f) in zip(days, _load_steps(change_files, oblast_key))]


def _grey_changes(change_files: list[Path], oblast_key: str):
    gs = []
    for p in change_files:
        if p.exists():
            gs += [shape(f["geometry"]) for f in json.loads(p.read_text())["features"]
                   if f["properties"].get("oblast") == oblast_key and f["properties"].get("change") == "to_grey"]
    return unary_union(gs) if gs else None


def _split(g, grey):
    """Ділянку «знято» ділимо на деокуповану і ту, що стала сірою."""
    if g is None or grey is None:
        return g, None
    ok = lambda x: x if not x.is_empty and x.area > 1e-9 else None  # noqa: E731
    return ok(g.difference(grey)), ok(g.intersection(grey))


def _km2(g) -> float:
    if g is None:
        return 0.0
    return round(g.area * 111.32 ** 2 * math.cos(math.radians(g.centroid.y)), 2)


def breakdown(change_files: list[Path], oblast_key: str = "donetsk") -> dict:
    """Площі за підсумком періоду (км²): окуповано, деокуповано, з окупованих — у сіру зону,
    «позначили й зняли» (не входять ні в окуповане, ні в деокуповане)."""
    red, blue, back = _timeline(_load_steps(change_files, oblast_key))
    free, grey = _split(blue, _grey_changes(change_files, oblast_key))
    return {"occupied": _km2(red), "deoccupied": _km2(free), "to_grey": _km2(grey), "flip": _km2(back)}


def velvet(cover: Path, w: int, h: int):
    """Підкладка «як обкладинка»: та сама оксамитова текстура без тексту й мапи.
    Беремо колір країв обкладинки, додаємо м'які плями, дрібне зерно й темніші краї."""
    import numpy as np
    from PIL import ImageFilter
    cv = np.asarray(Image.open(cover).convert("RGB").resize((200, 200)), dtype=np.float32)
    base = np.median(cv[60:150, 130:190].reshape(-1, 3), axis=0) * 1.1   # колір текстури праворуч від мапи (як на обкладинці)
    rng = np.random.default_rng(7)
    big = Image.fromarray((rng.random((h // 40 + 2, w // 40 + 2)) * 255).astype("uint8")).resize(
        (w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(60))
    lum = 0.75 + 0.5 * (np.asarray(big, dtype=np.float32) / 255.0)
    grain = 1 + (rng.random((h, w)) - 0.5) * 0.16
    yy, xx = np.mgrid[0:h, 0:w]
    vig = 1 - 0.25 * (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 2
    arr = base[None, None, :] * (lum * grain * vig)[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))


def render(change_files: list[Path], out: Path, title: str, subtitle: str, size: int = 1080,
           oblast_key: str = "donetsk", show_new: bool = True, height: int | None = None,
           upto: int | None = None, since: int = 0, caption: str | None = None,
           hide_step: bool = False, backdrop: Path | None = None,
           must_label: list | None = None) -> Path | None:
    """upto — для кадрів відео: показати стан після перших upto днів зі змінами, причому зміни днів
    since..upto (поточний крок) — їхнім кольором (червоним чи блакитним), навіть якщо потім їх знімуть.
    upto=None — підсумок періоду: червоне, блакитне й контур ділянок «позначили й зняли».
    height=None — квадрат для дописів; height=1920 — вертикальна мапа для відео (TikTok/Shorts),
    з полями під інтерфейс платформ зверху і знизу."""
    H = height or size
    vertical = H != size
    steps = _load_steps(change_files, oblast_key)
    if not steps:
        return None
    allg = [g for o, f in steps for g in o + f]
    new = unary_union(allg)   # для кадру й підписів
    new_occ, new_free, flip = _timeline(steps)            # підсумок періоду
    grey_ch = _grey_changes(change_files, oblast_key)      # з окупованих — у сіру зону
    final_free, final_grey = _split(new_free, grey_ch)
    cur = _timeline(steps[:upto]) if upto is not None else (new_occ, new_free, flip)
    hl_occ = hl_free = None
    if upto is not None:
        ho = [g for o, _ in steps[since:upto] for g in o]
        hf = [g for _, f in steps[since:upto] for g in f]
        hl_occ = unary_union(ho) if ho else None
        hl_free = unary_union(hf) if hf else None
    st = json.loads((DATA / "state" / "current.json").read_text())
    occ = unary_union(_geoms(st["occupied"][oblast_key]))
    # фон — окупація на початок періоду, щоб «до змін» і повернуті ділянки виглядали так, як були
    if new_free is not None:
        occ = occ.union(new_free)
    if new_occ is not None:
        occ = occ.difference(new_occ)
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
    grey_bg = unk
    if upto is not None or not show_new:
        # кадри «до» і проміжні: там, де за період з'явилася окупація, до неї зазвичай була сіра зона —
        # малюємо її сірою, а не підконтрольною (знімків сірої зони на початок періоду ми не зберігаємо)
        occ_ch = [g for o, _ in steps for g in o]
        if occ_ch:
            add = unary_union(occ_ch).difference(occ)
            grey_bg = add if unk is None else unk.union(add)
    if grey_bg is not None and not grey_bg.is_empty:
        poly(grey_bg, fill=GREY)
    layers = []
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
        c_red, c_blue, c_back = cur
        c_blue, c_grey = _split(c_blue, grey_ch)
        hl_free, hl_grey = _split(hl_free, grey_ch)
        if hide_step:   # «вимкнений» кадр для блимання: ділянки поточного кроку — без кольору, як до них
            hs = [g for g in (hl_occ, hl_free) if g is not None]   # сіре не блимає — воно просто сіріє
            hu = unary_union(hs) if hs else None
            if hu is not None:
                c_red = c_red.difference(hu) if c_red is not None else None
                c_blue = c_blue.difference(hu) if c_blue is not None else None
                c_back = c_back.difference(hu) if c_back is not None else None
            hl_occ = hl_free = None
        for h in (hl_free, hl_grey):   # поточний крок: зняте — блакитним (чи сірим), навіть якщо було червоним
            if h is not None and c_red is not None:
                c_red = c_red.difference(h)
        layers = [(c_red, NEW_OCC, (232, 80, 80)), (c_blue, DEOCC, (200, 240, 255)),
                  (hl_occ, NEW_OCC, (232, 80, 80)), (hl_free, DEOCC, (200, 240, 255))]
        if hl_occ is not None:
            layers[3] = (hl_free.difference(hl_occ) if hl_free is not None else None, DEOCC, (200, 240, 255))
        for g in (c_grey, hl_grey):   # з окупованих у сіру зону — просто сіре, як решта сірої зони, без підсвітки
            if g is not None:
                poly(g, fill=GREY)
        for geom, col, edge in layers:
            if geom is None:
                continue
            gl = Image.new("L", big.size, 0)
            gld = ImageDraw.Draw(gl)
            for p in getattr(geom, "geoms", [geom]):
                if p.geom_type != "Polygon" or p.is_empty:
                    continue
                pts = [(a * S, b * S) for a, b in (px(x, y) for x, y in p.exterior.coords)]
                gld.polygon(pts, fill=255)
                xs, ys = zip(*pts)
                if max(max(xs) - min(xs), max(ys) - min(ys)) < 24 * S:   # крихітна ділянка (під точкою міста) —
                    cx_, cy_ = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2   # ширше світіння, щоб її було видно
                    gld.ellipse([cx_ - 16 * S, cy_ - 16 * S, cx_ + 16 * S, cy_ + 16 * S], fill=255)
            gl = gl.filter(ImageFilter.MaxFilter(31)).filter(ImageFilter.GaussianBlur(14))
            gcol = edge if col == NEW_OCC else col   # темно-червоне світіння на темному фоні губиться — світліше
            big.paste(Image.new("RGB", big.size, gcol), (0, 0), gl.point(lambda v: int(v * 0.5)))
            d = ImageDraw.Draw(big)
            poly(geom, fill=col, outline=edge, width=1)
    img = big.resize((size, H), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size, top - 10], fill=BG)
    d.rectangle([0, top + mh + 10, size, H], fill=BG)
    if backdrop is not None and Path(backdrop).exists():   # оксамитова підкладка, як у обкладинки
        bd = velvet(Path(backdrop), size, H)
        bd.paste(img.crop((ox, top, ox + mw, top + mh)), (ox, top))
        img = bd
        d = ImageDraw.Draw(img)

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
    must = set(must_label or [])
    # міста з заголовка («Поблизу: …») підписуємо першими й обов'язково — щоб назви збігалися
    pts = [p for p in cities if p[2] in must] + near + [p for p in cities if p[2] not in must]
    shown, boxes, used = 0, [], set()
    for is_city, _, nm, g in pts:
        if nm in used:
            continue
        if shown >= 7 and nm not in must:
            continue
        x, y = px(g.x, g.y)
        if not (ox + 20 < x < ox + mw - 20 and top + 20 < y < top + mh - 20):
            continue
        f = _font("800" if is_city else "500", 34 if is_city else 28)
        w = d.textlength(nm, font=f)
        tx, ty = (x + 12, y - 18) if x + 12 + w < ox + mw else (x - 12 - w, y - 18)
        bb = (tx - 4, ty - 2, tx + w + 4, ty + 40)
        if any(not (bb[2] < b[0] or bb[0] > b[2] or bb[3] < b[1] or bb[1] > b[3]) for b in boxes):
            continue
        boxes.append(bb)
        used.add(nm)
        r = 7 if is_city else 5
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)
        d.text((tx + 2, ty + 2), nm, font=f, fill=(0, 0, 0))
        d.text((tx, ty), nm, font=f, fill=WHITE)
        shown += 1

    km_px = sc / 111.0

    ty0 = top - 200 if vertical else 60
    tsz = 66 if vertical else 58
    lines = [title]
    if vertical and d.textlength(title, font=_font("800", 60)) > size - 120 and " · " in title:
        # довгий список назв — на два рядки, а не дрібним шрифтом
        head, rest = title.split(": ", 1) if ": " in title else ("", title)
        parts = rest.split(" · ")
        lines = [(head + ": " if head else "") + " · ".join(parts[:-1]), parts[-1]] if len(parts) == 2 else \
                [(head + ": " if head else "") + " · ".join(parts[:2]), " · ".join(parts[2:])]
        tsz = 60
    while tsz > 34 and max(d.textlength(ln, font=_font("800", tsz)) for ln in lines) > size - 120:
        tsz -= 2
    for li, ln in enumerate(lines):
        d.text((60, ty0 + (66 if vertical else 58) - tsz + li * (tsz + 10)), ln, font=_font("800", tsz), fill=NEW)
    if len(lines) > 1:
        ty0 += 62   # підзаголовок — нижче, над мапою
    ssz = 40
    while ssz > 26 and d.textlength(subtitle, font=_font("500", ssz)) > size - 120:
        ssz -= 2
    d.text((60, ty0 + 85), subtitle, font=_font("500", ssz), fill=WHITE)
    if caption:   # для кадрів відео: коли це сталося («3–5 листопада · позначено окупованими»)
        cf = _font("800", 34)
        cw = d.textlength(caption, font=cf)
        cx0, cy0 = ox + 20, top + 20
        d.rounded_rectangle([cx0, cy0, cx0 + cw + 40, cy0 + 60], radius=14, fill=(8, 12, 26))
        d.text((cx0 + 20, cy0 + 10), caption, font=cf, fill=WHITE)
    lx, ly = 60, top + mh + 40
    lf = _font("500", 26)
    legend = ([(NEW_OCC, "позначені окупованими")] if new_occ is not None else []) + \
             ([(DEOCC, "позначені деокупованими")] if final_free is not None else []) + \
             [(OCC, "окуповано"), (GREY, "район проникнення")]
    if new_occ is None and flip is not None:   # окупація була лише тимчасова — у кадрах відео вона блимає
        legend.insert(0, (NEW_OCC, "позначені окупованими"))
    if new_free is None and flip is not None:
        legend.insert(len(legend) - 2, (DEOCC, "позначені деокупованими"))
    if new_free is None and flip is None and final_grey is None:   # звичний випадок — як і раніше
        legend = [(NEW_OCC, "позначені окупованими"), (OCC, "окуповано раніше"), (GREY, "район проникнення")]
    legend.append((LAND, "підконтрольна Україні"))
    lf_size = 26 if len(legend) <= 3 else 22
    lf = _font("500", lf_size)
    for col, label in legend:
        if lx + 36 + d.textlength(label, font=lf) > size - 50:   # не вміщається — новий рядок
            lx, ly = 60, ly + 40
        d.rectangle([lx, ly + 4, lx + 26, ly + 30], fill=LAND if col == FLIP else col,
                    outline=col if col == FLIP else None, width=2)
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
    sy2 = min(ly + 70, H - 45)
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
