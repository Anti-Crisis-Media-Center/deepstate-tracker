"""Міні-мапа змін за добу/тиждень: де саме змінилися позначення на мапі DeepState.

Малюємо з того, що бот уже зберігає: межі області, окупована територія (state/current.json),
нові ділянки (changes/<дата>.geojson), населені пункти (reference/settlements.geojson).
Без підкладки-карти зі сторонніх сервісів – лише наші дані, тож ліцензійних питань немає.
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
LAND = (40, 56, 96)          # 24.11.2026: трохи світліша (було (34,46,78) – зливалася з тлом)
OCC = (66, 32, 41)           # окуповано раніше – темніше (14.11.2026: середнє між (80,38,48) і (52,26,34)), щоб нова червона ділянка «вистрибувала»
DEOCC = (88, 196, 255)   # деокуповані ділянки – блакитні
TITLE_RED = (255, 110, 110)   # назви біля окупованих у заголовку, коли є обидва напрямки
FLIP = (235, 235, 245)      # «позначили й зняли» – світлий контур
GREY = (98, 100, 114)        # район проникнення (сіра зона) – приглушена (24.11.2026), щоб домінувала нова ділянка
GREY_NEW = (190, 190, 205)   # з окупованих стали сірими – світло-сірі (це не деокупація)
NEW = (255, 204, 51)         # фірмовий жовтий – лише для акцентів (заголовок)
NEW_OCC = (255, 45, 60)      # нові окуповані ділянки – яскраво-червоні зі світінням (14.11.2026: темно-червоні
                             # губилися на темно-червоному тлі окупованого; у Threads охоплення тягне саме ділянка)
NEW_OCC_GLOW = (255, 95, 95) # світіння навколо нових окупованих
BORDER = (150, 165, 200)
NEIGH = (44, 47, 58)         # інші області в кадрі (Харківська, Дніпропетровська, Запорізька) – нейтральний колір:
                             # даних про окупацію там у нас немає, мапа нічого про них не стверджує (Юля 22.11.2026)
OBL_LINE = (190, 200, 225)   # межі областей – тонкий світлий пунктир
FRAME = (70, 84, 120)        # тонка рамка навколо мапи
OBL_LABEL = (165, 176, 205)  # підписи областей – дрібніше за назви міст; 24.11.2026: світліші (контраст із підконтрольною 5,3:1, було 4,5)
LOGO = ROOT / "assets" / "logo_white.png"   # знак АКМЦ зліва вгорі на кожній мапі (рішення Юлі 21.11.2026)
LOGO_W = 100                  # ширина знака на 1080 px (24.11.2026: у півтора раза менший, був 150)
OBL_NAMES = {"UA14": "ДОНЕЦЬКА ОБЛАСТЬ", "UA44": "ЛУГАНСЬКА ОБЛАСТЬ", "UA63": "ХАРКІВСЬКА ОБЛАСТЬ",
             "UA12": "ДНІПРОПЕТРОВСЬКА ОБЛАСТЬ", "UA23": "ЗАПОРІЗЬКА ОБЛАСТЬ"}
LAST_FLAGS: list[str] = []    # після render(): що не вдалося (назву з тексту не підписано тощо) – для перевірки
LAST_LABELS: set = set()      # після render(): які назви підписано на мапі
WHITE, MUTED = (250, 250, 250), (175, 185, 205)
V_NAMES_COLOR = WHITE          # колір назв у шапці відео – білий (Юля 24.11.2026; жовтий сперечався з червоним)

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
    """Зміни по днях у хронологічному порядку: [(окуповані, зняті), ...]. «Зняті» – і деокуповані,
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
    """Проходимо зміни по черзі. red – позначено окупованим (і ще не знято), blue – знято (і ще не повернуто),
    back – ділянки, що повернулися до стану на початок періоду («позначили й зняли»)."""
    from shapely.geometry import Polygon
    red = blue = back = Polygon()
    for occ, free in steps:
        for g in occ:   # повернули позначку на щойно зняте – ділянка знову як на початку
            back = back.union(g.intersection(blue))
            red = red.union(g.difference(blue))
            blue = blue.difference(g)
        for g in free:  # зняли щойно поставлену позначку – теж як на початку
            back = back.union(g.intersection(red))
            blue = blue.union(g.difference(red))
            red = red.difference(g)
    back = back.difference(red).difference(blue)
    ok = lambda g: g if g is not None and not g.is_empty and g.area > 1e-9 else None  # noqa: E731
    return ok(red), ok(blue), ok(back)


def step_groups(change_files: list[Path], oblast_key: str = "donetsk", max_steps: int = 5) -> list[int]:
    """Кроки для відео: межі (кількість днів зі змінами) після кожного кроку.
    16.11.2026 (Юля): показуємо динаміку за період – КОЖЕН день зі змінами окремим кроком (блимнуло й
    світиться, далі наступне), навіть якщо була лише окупація; не більше max_steps – найближчі (сусідні
    короткі) дні зливаємо. Порожньо – якщо день зі змінами лише один."""
    steps = _load_steps(change_files, oblast_key)
    if len(steps) <= 1:
        return []
    ends = list(range(1, len(steps) + 1))
    while len(ends) > max_steps:   # забагато кроків – зливаємо пару сусідніх із найменшою сумарною кількістю днів
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
    """Площі за підсумком періоду (км²): окуповано, деокуповано, з окупованих – у сіру зону,
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


def _wrap_title(d, title: str, width: int, big: int, small: int, one_min: int = 46) -> tuple[list[str], int]:
    """«Поблизу: A · B · C · D · E» – якомога менше рядків (до 3), по « · »; у один рядок – не дрібніше 46 px."""
    head, rest = (title.split(": ", 1) if title.startswith("Поблизу: ") else ("", title))
    parts = rest.split(" · ") if rest else [""]
    lead = head + ": " if head else ""

    def wrap(tsz):
        f = _font("800", tsz)
        lines, cur = [], lead
        for p_ in parts:
            cand = cur + ("" if cur == lead else " · ") + p_
            if cur != lead and d.textlength(cand, font=f) > width:
                lines.append(cur)
                cur = p_
            else:
                cur = cand
        lines.append(cur)
        return lines, all(d.textlength(ln, font=f) <= width for ln in lines)
    for n_lines in (1, 2, 3):
        lo = max(small, one_min) if n_lines == 1 else small
        for tsz in range(big, lo - 1, -2):
            lines, ok = wrap(tsz)
            if ok and len(lines) <= n_lines:
                return lines, tsz
    return wrap(small)[0], small


def _dashed(d, pts, fill, width=2, dash=12, gap=8):
    """Пунктирна лінія по точках (у пікселях)."""
    on, left = True, dash
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while pos < seg:
            step = min(left, seg - pos)
            if on:
                t0, t1 = pos / seg, (pos + step) / seg
                d.line([(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0), (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)],
                       fill=fill, width=width)
            pos += step
            left -= step
            if left <= 1e-6:
                on = not on
                left = dash if on else gap


def _oblast_shapes() -> list[tuple[str, object]]:
    """[(pcode, геометрія)]: Донецька, Луганська та сусідні (Харківська, Дніпропетровська, Запорізька)."""
    out = []
    for fn in ("oblasts.geojson", "neighbors.geojson"):
        p = DATA / "reference" / fn
        if p.exists():
            for f in json.loads(p.read_text())["features"]:
                out.append((f["properties"].get("adm1_pcode"), shape(f["geometry"]).buffer(0)))
    return out


def render(change_files: list[Path], out: Path, title: str, subtitle: str, size: int = 1080,
           oblast_key: str = "donetsk", show_new: bool = True, height: int | None = None,
           upto: int | None = None, since: int = 0, caption: str | None = None,
           hide_step: bool = False, backdrop: Path | None = None, date_marks: list | None = None,
           frame_override=None, head_color=None, inset: bool | None = None,
           must_label: list | None = None, blue_label: list | None = None,
           red_label: list | None = None) -> Path | None:
    """upto – для кадрів відео: показати стан після перших upto днів зі змінами, причому зміни днів
    since..upto (поточний крок) – їхнім кольором (червоним чи блакитним), навіть якщо потім їх знімуть.
    upto=None – підсумок періоду: червоне, блакитне й контур ділянок «позначили й зняли».
    height=None – квадрат для дописів; height=1920 – вертикальна мапа для відео (TikTok/Shorts),
    з полями під інтерфейс платформ зверху і знизу."""
    H = height or size
    vertical = H >= 1600          # TikTok/Shorts 9:16
    portrait = H != size and not vertical   # 4:5 (1080×1350) – Threads/Instagram: як квадрат, лише вища мапа
    steps = _load_steps(change_files, oblast_key) if change_files else []
    if not steps and frame_override is None:
        return None
    if steps:
        allg = [g for o, f in steps for g in o + f]
        new = unary_union(allg)   # для кадру й підписів
    else:   # день без змін (макет 24.11.2026): кадр – уся лінія фронту, нових ділянок немає
        new = frame_override.centroid.buffer(1e-7)
        show_new = False
    new_occ, new_free, flip = _timeline(steps)            # підсумок періоду
    grey_ch = _grey_changes(change_files, oblast_key)      # з окупованих – у сіру зону
    final_free, final_grey = _split(new_free, grey_ch)
    cur = _timeline(steps[:upto]) if upto is not None else (new_occ, new_free, flip)
    hl_occ = hl_free = None
    if upto is not None:
        ho = [g for o, _ in steps[since:upto] for g in o]
        hf = [g for _, f in steps[since:upto] for g in f]
        hl_occ = unary_union(ho) if ho else None
        hl_free = unary_union(hf) if hf else None
    st = json.loads((DATA / "state" / "current.json").read_text())
    # окупація обох областей: інакше сусідня область у кадрі (напр. Луганщина біля Сіверська)
    # зафарбовувалась як «підконтрольна Україні» (22.11.2026)
    occ = unary_union([g for v in st["occupied"].values() if v for g in _geoms(v)])
    # фон – окупація на початок періоду, щоб «до змін» і повернуті ділянки виглядали так, як були
    if new_free is not None:
        occ = occ.union(new_free)
    if new_occ is not None:
        occ = occ.difference(new_occ)
    _unk = [g for v in (st.get("unknown") or {}).values() if v for g in _geoms(v)]
    unk = unary_union(_unk) if _unk else None
    obl = json.loads((DATA / "reference" / "oblasts.geojson").read_text())
    region = unary_union([shape(f["geometry"]) for f in obl["features"]])  # обидві області

    # заголовок «Поблизу: A · B · …» – заздалегідь розбиваємо на рядки (до 3), бо від їхньої кількості
    # залежить, де починається мапа (24.11.2026: без порожньої смуги під підзаголовком – мапа більша)
    _md = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    _tw = size - 80
    # 24.11.2026 (Юля): жовті назви – трохи менші, щоб не «набрасувалися» (було 66/56)
    t_lines, t_size = _wrap_title(_md, title, _tw, 44 if vertical else 48, 32 if vertical else 34, one_min=32 if vertical else 40)

    # кадр: нова ділянка + ~18 км навколо, з урахуванням широти
    frame = frame_override if frame_override is not None else new
    if must_label:   # назви із заголовка мають потрапити в кадр – розширюємо його до цих пунктів
        try:
            from shapely.geometry import MultiPoint
            st_all = json.loads((DATA / "reference" / "settlements.geojson").read_text())["features"]
            pts_m = [shape(f["geometry"]).centroid for f in st_all if f["properties"]["ADM4_UA"] in set(must_label)]
            pts_m = [p for p in pts_m if p.distance(new) < 0.25]       # не далі ~25 км – інакше це інший тезка
            if pts_m:
                frame = unary_union([new.envelope, MultiPoint(pts_m).envelope.buffer(0.03)])
        except Exception:  # noqa: BLE001
            frame = new
    cx, cy = frame.centroid.x, frame.centroid.y
    kx = math.cos(math.radians(cy))
    minx, miny, maxx, maxy = frame.bounds
    extra_lines = max(0, len(t_lines) - 1)
    if vertical:
        # TikTok/Shorts/Reels (24.11.2026): зверху ~300 px – інтерфейс і рядок «Донеччина на мапі DeepState»
        # (його домальовує відео); шапка: спершу що сталося (цифра), потім де (назви) – так читає новий глядач;
        # праворуч ~170 px – кнопки лайків і коментарів, тож мапа вужча й притиснута ліворуч
        v_sub_y = 300
        v_names_y = v_sub_y + 66 + 22      # цифра у відео – велика (64 px), назви – дрібніші (Юля 24.11.2026)
        top, bottom = max(500, v_names_y + len(t_lines) * (t_size + 10) + 20), 480
        mw = size - 40 - 170   # YouTube: кнопки ширші за TikTok (~170 px), Reels і TikTok – ~145
        mh = H - top - bottom
    else:
        # 4:5 і квадрат: мапа на всю ширину (1000 px), висота – усе, що лишається під заголовком і над легендою
        top, bottom = 215 + 50 * extra_lines, 125
        mw, mh = size - 80, H - top - bottom
    ox = 40 if vertical else (size - mw) // 2
    # пікселів на градус широти: кадр (+~18 км з кожного боку) має влізти і по ширині, і по висоті
    sc = min(mw / ((maxx - minx) * kx + 0.32), mh / ((maxy - miny) + 0.32))
    hx, hy = mw / (2 * sc), mh / (2 * sc)
    # знак АКМЦ зліва вгорі: якщо під нього потрапляє ділянка змін – зсуваємо кадр (рішення 21.11.2026)
    logo_h = round(LOGO_W * 319 / 600)
    logo_box = box(0, 0, LOGO_W + 36, logo_h + 36)            # у пікселях відносно лівого верхнього кута мапи
    for _ in range(3):
        def _pxl(x, y, cx=cx, cy=cy):
            return ((x - (cx - hx / kx)) * kx * sc, (cy + hy - y) * sc)
        try:
            from shapely.ops import transform as _tf
            under = any(_tf(lambda x, y, z=None: _pxl(x, y), g_).intersects(logo_box)
                        for g_ in getattr(new, "geoms", [new]) if not g_.is_empty)
        except Exception:  # noqa: BLE001
            under = False
        if not under:
            break
        cx -= (LOGO_W + 40) / sc / kx        # кадр – лівіше й вище: ділянка відходить від лівого верхнього кута
        cy += (logo_h + 40) / sc
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

    obl_shapes = _oblast_shapes()
    # 24.11.2026 (Юля): сусідні області – так само, як наші: підконтрольне синім, окуповане червоним, сіра зона
    # сірим (люди думають ширше й лякаються «незрозумілого» кольору). Якщо даних про сусідів у знімку ще немає –
    # нейтральний колір, як раніше: мапа не стверджує того, чого ми не знаємо
    nb = st.get("neighbors") or {}
    nb_occ = [g for v in (nb.get("occupied") or {}).values() if v for g in _geoms(v)]
    nb_unk = [g for v in (nb.get("unknown") or {}).values() if v for g in _geoms(v)]
    for pc, g_ in obl_shapes:
        if pc not in ("UA14", "UA44"):
            poly(g_, fill=LAND if nb else NEIGH)
    poly(region, fill=LAND)
    if nb_occ:
        poly(unary_union(nb_occ), fill=OCC)
    if nb_unk:
        poly(unary_union(nb_unk), fill=GREY)
    poly(occ, fill=OCC)
    grey_bg = unk
    if upto is not None or not show_new:
        # кадри «до» і проміжні: там, де за період з'явилася окупація, до неї зазвичай була сіра зона –
        # малюємо її сірою, а не підконтрольною (знімків сірої зони на початок періоду ми не зберігаємо)
        occ_ch = [g for o, _ in steps for g in o]
        if occ_ch:
            add = unary_union(occ_ch).difference(occ)
            grey_bg = add if unk is None else unk.union(add)
    if grey_bg is not None and not grey_bg.is_empty:
        poly(grey_bg, fill=GREY)
    # межі областей – тонкий світлий пунктир, завжди (навіть якщо сусідньої області в кадрі лише смужка; Юля 22.11)
    try:
        lines_ = []
        for i, (pa, ga) in enumerate(obl_shapes):
            for pb, gb in obl_shapes[i + 1:]:
                if "UA14" not in (pa, pb) and "UA44" not in (pa, pb):
                    continue
                ln = ga.boundary.intersection(gb.buffer(0.004)).intersection(view)
                lines_ += [l_ for l_ in getattr(ln, "geoms", [ln]) if l_.geom_type == "LineString" and not l_.is_empty]
        for l_ in lines_:
            _dashed(d, [(a * S, b * S) for a, b in (px(x, y) for x, y in l_.coords)], OBL_LINE, width=2 * S,
                    dash=12 * S, gap=8 * S)
    except Exception:  # noqa: BLE001
        pass
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
        if hide_step:   # «вимкнений» кадр для блимання: ділянки поточного кроку – без кольору, як до них
            hs = [g for g in (hl_occ, hl_free) if g is not None]   # сіре не блимає – воно просто сіріє
            hu = unary_union(hs) if hs else None
            if hu is not None:
                c_red = c_red.difference(hu) if c_red is not None else None
                c_blue = c_blue.difference(hu) if c_blue is not None else None
                c_back = c_back.difference(hu) if c_back is not None else None
            hl_occ = hl_free = None
        for h in (hl_free, hl_grey):   # поточний крок: зняте – блакитним (чи сірим), навіть якщо було червоним
            if h is not None and c_red is not None:
                c_red = c_red.difference(h)
        layers = [(c_red, NEW_OCC, NEW_OCC_GLOW), (c_blue, DEOCC, (200, 240, 255)),
                  (hl_occ, NEW_OCC, NEW_OCC_GLOW), (hl_free, DEOCC, (200, 240, 255))]
        if hl_occ is not None:
            layers[3] = (hl_free.difference(hl_occ) if hl_free is not None else None, DEOCC, (200, 240, 255))
        for g in (c_grey, hl_grey):   # з окупованих у сіру зону – просто сіре, як решта сірої зони, без підсвітки
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
                if max(max(xs) - min(xs), max(ys) - min(ys)) < 24 * S:   # крихітна ділянка (під точкою міста) –
                    cx_, cy_ = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2   # ширше світіння, щоб її було видно
                    gld.ellipse([cx_ - 16 * S, cy_ - 16 * S, cx_ + 16 * S, cy_ + 16 * S], fill=255)
            wide = col == NEW_OCC   # червоне – ширше й щільніше світіння, як було в жовтого (14.11.2026)
            gl = gl.filter(ImageFilter.MaxFilter(45 if wide else 31)).filter(ImageFilter.GaussianBlur(22 if wide else 14))
            gcol = edge if col == NEW_OCC else col   # темно-червоне світіння на темному фоні губиться – світліше
            k_ = 1.0 if col == NEW_OCC else 0.5   # червоне світіння – яскравіше (14.11.2026)
            big.paste(Image.new("RGB", big.size, gcol), (0, 0), gl.point(lambda v, k_=k_: int(v * k_)))
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

    # ПІДПИСИ (24.11.2026, принцип «текст = мапа»): біля ділянок – лише назви з тексту (must_label), жирним білим;
    # «сусідні» села, яких немає в тексті, не підписуємо; великі міста – як орієнтир, іншим (приглушеним) стилем
    global LAST_FLAGS, LAST_LABELS
    LAST_FLAGS, LAST_LABELS = [], set()
    st_feats = json.loads((DATA / "reference" / "settlements.geojson").read_text())["features"]
    pts = []
    from tracker import is_city as _is_city
    for f in st_feats:
        g = shape(f["geometry"]).centroid
        if view.contains(g):
            nm = f["properties"]["ADM4_UA"]
            pts.append((_is_city(nm, f["properties"].get("ADM2_UA")), -g.distance(new), nm, g))
    must = list(dict.fromkeys(must_label or []))
    must_pts, seen_m = [], set()
    for p_ in sorted([p for p in pts if p[2] in set(must)], key=lambda p: -p[1]):   # для тезок – найближчий до змін
        if p_[2] not in seen_m:
            seen_m.add(p_[2])
            must_pts.append(p_)
    cities = sorted([p for p in pts if p[0] and p[2] not in set(must)], key=lambda p: -p[1])[:5]
    if not steps:   # день без змін: орієнтири – найбільші міста, а не «найближчі до змін»
        key_c = ["Краматорськ", "Слов'янськ", "Костянтинівка", "Дружківка", "Добропілля", "Лиман", "Покровськ"]
        cities = sorted([p for p in pts if p[2] in key_c], key=lambda p: key_c.index(p[2]))
    lg_x, lg_y = ox + 18, top + 18
    boxes = [(lg_x - 8, lg_y - 8, lg_x + LOGO_W + 12, lg_y + round(LOGO_W * 319 / 600) + 12)]   # місце знака АКМЦ
    used = set()
    label_at: dict = {}
    from shapely.geometry import Polygon as _Poly, box as _box
    patch_px = []
    for part in getattr(new, "geoms", [new]):
        if part.is_empty or part.geom_type != "Polygon":
            continue
        try:
            patch_px.append(_Poly([px(x_, y_) for x_, y_ in part.exterior.coords]).buffer(8))
        except Exception:  # noqa: BLE001
            pass
    # мінікарта-орієнтир у відео (24.11.2026, на пробу): контур Донеччини й Луганщини з рамкою «ми тут»;
    # у нижньому правому куті мапи, а якщо там ділянки змін – у нижньому лівому; якщо й там – без неї
    if (vertical if inset is None else inset):
        try:
            iw, ih = 270, 200
            spots = [(ox + mw - iw - 14, top + mh - ih - 14), (ox + 14, top + mh - ih - 14)]
            spot = next(((ix, iy) for ix, iy in spots
                         if not any(pp.intersects(_box(ix, iy, ix + iw, iy + ih)) for pp in patch_px)), None)
            if spot:
                ix, iy = spot
                rb = region.bounds
                kr = math.cos(math.radians((rb[1] + rb[3]) / 2))
                k2 = 2
                isc = min((iw - 24) / ((rb[2] - rb[0]) * kr), (ih - 24) / (rb[3] - rb[1])) * k2
                offx = (iw * k2 - (rb[2] - rb[0]) * kr * isc) / 2
                offy = (ih * k2 - (rb[3] - rb[1]) * isc) / 2
                ins = Image.new("RGB", (iw * k2, ih * k2), BG)
                di = ImageDraw.Draw(ins)

                def ip(x_, y_):
                    return (offx + (x_ - rb[0]) * kr * isc, offy + (rb[3] - y_) * isc)

                def ipoly(g_, fill):
                    for q in getattr(g_, "geoms", [g_]):
                        if q.geom_type == "Polygon" and not q.is_empty:
                            di.polygon([ip(*c_) for c_ in q.exterior.coords], fill=fill)
                ipoly(region, LAND)
                ipoly(occ.intersection(region), OCC)
                for pc_, g_o in obl_shapes:   # контури Донеччини й Луганщини – світлою лінією (окупована
                    if pc_ in ("UA14", "UA44"):   # Луганщина інакше зливається з тлом)
                        for q in getattr(g_o, "geoms", [g_o]):
                            if q.geom_type == "Polygon":
                                di.line([ip(*c_) for c_ in q.exterior.coords], fill=(150, 162, 192), width=2 * k2)
                vb = view.bounds
                di.rectangle([*ip(vb[0], vb[3]), *ip(vb[2], vb[1])], outline=WHITE, width=3 * k2)
                ins = ins.resize((iw, ih), Image.LANCZOS)
                img.paste(ins, (ix, iy))
                d = ImageDraw.Draw(img)
                d.rectangle([ix, iy, ix + iw, iy + ih], outline=FRAME, width=2)
                boxes.append((ix - 6, iy - 6, ix + iw + 6, iy + ih + 6))
        except Exception:  # noqa: BLE001
            pass
    hit = lambda bb: (any(not (bb[2] < b[0] or bb[0] > b[2] or bb[3] < b[1] or bb[1] > b[3]) for b in boxes)  # noqa: E731
                      or any(pp.intersects(_box(*bb)) for pp in patch_px))
    for is_must, group in ((True, must_pts), (False, cities)):
        for is_city, _, nm, g in group:
            if nm in used:
                continue
            x, y = px(g.x, g.y)
            if not (ox + 20 < x < ox + mw - 20 and top + 20 < y < top + mh - 20):
                continue
            f = _font("800", 34) if is_must else _font("500", 26)
            w = d.textlength(nm, font=f)
            hh = (40 + (26 if date_marks else 0)) if is_must else 32   # у відео під назвою – місце для дати
            cand = [(x + 12, y - 18), (x - 12 - w, y - 18)]
            if is_must:   # назву з тексту не пропускаємо: пробуємо також під і над точкою
                cand += [(x + 12, y + 14), (x - 12 - w, y + 14), (x + 12, y - 52), (x - 12 - w, y - 52),
                         (x - w / 2, y + 14), (x - w / 2, y - 52), (x + 12, y + 48), (x - 12 - w, y + 48),
                         (x - w / 2, y + 48), (x - w / 2, y - 86)]
            if is_must:   # 24.11.2026: ще далі від точки (місяць – багато назв поруч)
                cand += [(x + 12, y + 82), (x - 12 - w, y + 82), (x - w / 2, y + 82), (x + 12, y - 120),
                         (x - 12 - w, y - 120), (x - w / 2, y - 120), (x + 40, y - 18), (x - 40 - w, y - 18)]
            fits = [c for c in cand if ox + 8 <= c[0] and c[0] + w <= ox + mw - 8
                    and top + 8 <= c[1] and c[1] + hh <= top + mh - 8]
            cand = fits or (cand[:1] if is_must else [])   # приглушене місто, що не влазить у рамку, – не підписуємо
            pos = next(((tx_, ty_) for tx_, ty_ in cand if not hit((tx_ - 4, ty_ - 2, tx_ + w + 4, ty_ + hh))), None)
            if pos is None and is_must:   # назву з тексту краще покласти поверх ділянки, ніж загубити
                pos = next(((tx_, ty_) for tx_, ty_ in cand
                            if not any(not (tx_ + w + 4 < b[0] or tx_ - 4 > b[2] or ty_ + hh < b[1] or ty_ - 2 > b[3])
                                       for b in boxes)), None)
            if pos is None:
                continue
            tx, ty = pos
            boxes.append((tx - 4, ty - 2, tx + w + 4, ty + hh))
            used.add(nm)
            r = 6 if is_must else 4
            d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE if is_must else MUTED)
            d.text((tx + 2, ty + 2), nm, font=f, fill=(0, 0, 0))
            d.text((tx, ty), nm, font=f, fill=WHITE if is_must else MUTED)
            if is_must:
                label_at[nm] = (tx, ty, x, y)
    LAST_LABELS = set(used)
    # 24.11.2026 (Юля): дата кроку – дрібно під назвою громади біля ділянки; з'являється й блимає разом із нею,
    # потім лишається (на фінальному кадрі – дати всіх кроків)
    if date_marks and show_new and label_at:
        dates_at: dict = {}
        for s_, u_, lab in date_marks:
            if upto is not None and (u_ > upto or (hide_step and (s_, u_) == (since, upto))):
                continue
            gs = [g for o, f_ in steps[s_:u_] for g in o + f_]
            if not gs:
                continue
            u_g = unary_union(gs)
            for part in getattr(u_g, "geoms", [u_g]):   # кожна ділянка кроку – до найближчої назви
                cpt = part.centroid
                cx_p, cy_p = px(cpt.x, cpt.y)
                nm_, dist = min(((n_, math.hypot(v[2] - cx_p, v[3] - cy_p)) for n_, v in label_at.items()),
                                key=lambda q: q[1])
                if dist <= 170 and lab not in dates_at.get(nm_, []):
                    dates_at.setdefault(nm_, []).append(lab)
        fd = _font("500", 26)
        for nm_, labs in dates_at.items():
            tx, ty = label_at[nm_][:2]
            t_ = ", ".join(dict.fromkeys(labs))
            d.text((tx + 2, ty + 42), t_, font=fd, fill=(0, 0, 0))
            d.text((tx, ty + 40), t_, font=fd, fill=(235, 238, 245))
    for m_ in must:
        if m_ not in used:
            LAST_FLAGS.append(f"назву «{m_}» з тексту не підписано на мапі")
    if any(pp.intersects(_box(*boxes[0])) for pp in patch_px):
        LAST_FLAGS.append("ділянка змін під знаком АКМЦ")

    # підписи областей у кадрі. 24.11.2026 (Юля): сусіди тепер у тих самих кольорах, тож без підпису межа
    # незрозуміла – підписуємо кожну область, якої видно хоч 1,5% мапи; якщо місця мало – дрібнішим шрифтом.
    # 24.11.2026 (Юля): і саму Донецьку (Луганську) – раніше її пропускали, і на мапі не було видно, де вона
    try:
        view_area = view.area
        for pc, g_ in obl_shapes:
            inside = g_.intersection(view)
            if inside.is_empty or inside.area < 0.015 * view_area:
                continue
            words = OBL_NAMES.get(pc, "").split(" ")
            if not words[0]:
                continue
            ip = _Poly([px(x_, y_) for x_, y_ in inside.exterior.coords]) if inside.geom_type == "Polygon" else \
                _Poly([px(x_, y_) for x_, y_ in max(inside.geoms, key=lambda q: q.area).exterior.coords])
            placed = False
            # 26.11.2026 (Юля): свою область – одним рядком («ДОНЕЦЬКА ОБЛАСТЬ»), місця вистачає;
            # у два рядки – лише сусіди (від них у кадрі зазвичай невеликі шматки) і запасний варіант
            if pc == {"donetsk": "UA14", "luhansk": "UA44"}.get(oblast_key):
                tries = [(fs_, [" ".join(words)]) for fs_ in (22, 19)] + [(fs_, words) for fs_ in (22, 19, 16)]
            else:
                tries = [(fs_, words) for fs_ in (22, 19, 16)]
            for fs, words in tries:
                fo = _font("500", fs)
                lw = max(d.textlength(w_, font=fo) for w_ in words)
                lh = (fs + 6) * len(words)
                ipb = ip.buffer(-8)
                if ipb.is_empty:
                    break
                c0 = ipb.representative_point()
                if pc == {"donetsk": "UA14", "luhansk": "UA44"}.get(oblast_key):
                    # своя область: підпис – у найбільшу «порожню» окуповану частину, подалі від назв міст
                    try:
                        io_ = inside.intersection(occ)
                        io_ = max(getattr(io_, "geoms", [io_]), key=lambda q: q.area)
                        if io_.area > 0.1 * inside.area:
                            c0 = _Poly([px(x_, y_) for x_, y_ in io_.exterior.coords]).buffer(-8).representative_point()
                    except Exception:  # noqa: BLE001
                        pass
                cands = sorted(((x_, y_) for x_ in range(int(ox + 8), int(ox + mw - lw - 8), 15)
                                for y_ in range(int(top + 8), int(top + mh - lh - 8), 15)),
                               key=lambda q: (q[0] + lw / 2 - c0.x) ** 2 + (q[1] + lh / 2 - c0.y) ** 2)
                # 26.11.2026 (Юля): «ДОНЕЦЬКА ОБЛАСТЬ» стала впритул під «Рай-Олександрівка» і читалася як її
                # адреса. Своя область – спершу з відступом ≥45 px від інших підписів, лише якщо ніде – впритул.
                own = pc == {"donetsk": "UA14", "luhansk": "UA44"}.get(oblast_key)
                if own:
                    # 26.11.2026 (Юля): область велика – ставити в найвільніше місце, а не біля назв.
                    # Сортуємо кандидатів за відстанню до найближчого іншого підпису / ділянки (більша – краще).
                    def _clear(q):
                        bx = (q[0], q[1], q[0] + lw, q[1] + lh)
                        ds = [max(b[0] - bx[2], bx[0] - b[2], b[1] - bx[3], bx[1] - b[3], 0) for b in boxes]
                        return min(ds) if ds else 9999
                    # 26.11.2026 (Юля): підпис може йти наскрізь – через синю й червону частини, щоб було видно,
                    # що обидві – Донецька область. Перевага кандидатам, що перетинають лінію фронту.
                    try:
                        _io = inside.intersection(occ)
                        occ_px = _Poly([px(x_, y_) for x_, y_ in
                                        max(getattr(_io, "geoms", [_io]), key=lambda q: q.area).exterior.coords])
                    except Exception:  # noqa: BLE001
                        occ_px = None

                    def _both(q):
                        if occ_px is None:
                            return 0
                        bx = _box(q[0], q[1], q[0] + lw, q[1] + lh)
                        f = bx.intersection(occ_px).area / bx.area
                        return 1 if 0.3 <= f <= 0.7 else 0
                    cands = sorted(cands[:4000], key=lambda q: (-_both(q) if _clear(q) >= 25 else 0,
                                                                -min(_clear(q), 220)))
                for gap in ((45, 4) if own else (4,)):
                    for x_, y_ in cands:
                        bb = (x_ - 4, y_ - 4, x_ + lw + 4, y_ + lh + 4)
                        bbg = (x_ - gap, y_ - gap, x_ + lw + gap, y_ + lh + gap)
                        if ipb.contains(_box(*bb)) and not hit(bbg):
                            for i_, w_ in enumerate(words):
                                d.text((x_, y_ + (fs + 6) * i_), w_, font=fo, fill=OBL_LABEL)
                            boxes.append(bb)
                            placed = True
                            break
                    if placed:
                        break
                if placed:
                    break
    except Exception:  # noqa: BLE001
        pass

    km_px = sc / 111.0

    lines, tsz = t_lines, t_size
    ty0 = top - 200 if vertical else 60   # vertical: 300 – нижче верхніх ~285 px, що їх стрічка ховає
    if vertical and len(lines) > 2:
        ty0 -= 62 * (len(lines) - 2)
    # Заголовок, підзаголовок, легенда й підпис – рівно по лівому й правому краю мапи (рішення 11.11)
    for li, ln in enumerate(lines):
        yy = (v_names_y + li * (tsz + 10)) if vertical else ty0 + 58 - tsz + li * (tsz + 10)
        xx = ox
        two_way = bool(blue_label) or bool(red_label)   # обидва напрямки: кольорові лише назви, решта – біла
        name_col = head_color or (V_NAMES_COLOR if vertical else NEW)
        if li == 0 and ln.startswith("Поблизу: "):   # «Поблизу:» – нежирним, назви – жирним
            d.text((xx, yy), "Поблизу: ", font=_font("500", tsz),
                   fill=WHITE if two_way else (MUTED if name_col == WHITE else name_col))
            xx += d.textlength("Поблизу: ", font=_font("500", tsz))
            ln = ln[len("Поблизу: "):]
        blue_set, red_set = set(blue_label or []), set(red_label or [])
        for ni, nm_ in enumerate(ln.split(" · ")):   # назви біля деокупованих ділянок – блакитним
            if ni:
                d.text((xx, yy), " · ", font=_font("800", tsz), fill=WHITE if two_way else (MUTED if name_col == WHITE else name_col))
                xx += d.textlength(" · ", font=_font("800", tsz))
            d.text((xx, yy), nm_, font=_font("800", tsz), fill=DEOCC if nm_ in blue_set else TITLE_RED if nm_ in red_set else name_col)
            xx += d.textlength(nm_, font=_font("800", tsz))
    if len(lines) > 1:
        ty0 += (62 if vertical else 50) * (len(lines) - 1)   # підзаголовок – нижче, над мапою
    hw = size - 80 if vertical else mw   # у відео шапка – на всю ширину (кнопки нижче)
    ssz = 40
    while ssz > 30 and d.textlength(subtitle, font=_font("500", ssz)) > hw:
        ssz -= 2
    if d.textlength(subtitle, font=_font("500", ssz)) > hw:   # не влазить – коротший варіант
        import re as _re
        # прибираємо «за добу», але дату лишаємо (з 14.11.2026 підзаголовок закінчується датою)
        subtitle = _re.sub(r" за (добу|тиждень|місяць)(?=(,? [\d.–]+)?$)", "", subtitle).replace(" – ", " ")
        while ssz > 26 and d.textlength(subtitle, font=_font("500", ssz)) > hw:
            ssz -= 2
    if vertical:   # цифри – великі й жирні, решта – звичайним шрифтом, по одній базовій лінії
        import re as _re2
        toks = [t for t in _re2.split(r"(\d[\d,.]* км²)", subtitle) if t]
        nb, rs = 64, 38
        while nb > 40 and sum(d.textlength(t, font=_font("800", nb) if t[0].isdigit() else _font("500", rs))
                              for t in toks) > hw:
            nb, rs = nb - 4, max(28, rs - 2)
        xx = ox
        for t in toks:
            f_ = _font("800", nb) if t[0].isdigit() else _font("500", rs)
            d.text((xx, v_sub_y + 64), t, font=f_, fill=WHITE, anchor="ls")
            xx += d.textlength(t, font=f_)
    else:
        d.text((ox, ty0 + 85), subtitle, font=_font("500", ssz), fill=WHITE)
    if caption:   # для кадрів відео: коли це сталося («3–5 листопада · позначено окупованими»)
        cf = _font("800", 34)
        cw = d.textlength(caption, font=cf)
        cx0, cy0 = ox + mw - cw - 60, top + 20   # праворуч угорі (ліворуч – знак АКМЦ)
        d.rounded_rectangle([cx0, cy0, cx0 + cw + 40, cy0 + 60], radius=14, fill=(8, 12, 26))
        d.text((cx0 + 20, cy0 + 10), caption, font=cf, fill=WHITE)
    # легенда – коротша й в один рядок (24.11.2026); якщо не влазить навіть дрібно – переносимо
    if not steps:   # день без змін – без «нових ділянок» у легенді
        legend = [(OCC, "окуповано")]
    elif new_occ is not None and final_free is not None:
        legend = [(NEW_OCC, "позначені окупованими"), (DEOCC, "позначені деокупованими"), (OCC, "окуповано раніше")]
    elif final_free is not None and new_occ is None:
        legend = [(DEOCC, "нові деокуповані"), (OCC, "окуповано")]
    else:
        legend = [(NEW_OCC, "нові ділянки"), (OCC, "окуповано раніше")]
        if flip is not None:
            legend.insert(1, (DEOCC, "деокуповані"))
    legend += [(GREY, "сіра зона"), (LAND, "підконтрольна Україні")]
    lf_size = 32 if vertical else 26   # 24.11.2026: у відео легенда більша – місце внизу є
    width_of = lambda fs: sum(36 + d.textlength(lbl, font=_font("500", fs)) + 30 for _, lbl in legend) - 30  # noqa: E731
    while lf_size > 19 and width_of(lf_size) > mw:
        lf_size -= 1
    lf = _font("500", lf_size)
    lx, ly = ox, top + mh + 22
    for col, label in legend:
        if lx + 36 + d.textlength(label, font=lf) > ox + mw:   # не вміщається – новий рядок
            lx, ly = ox, ly + 36
        # 26.11.2026 (Юля): квадратик підконтрольної здавався темнішим за мапу – через світлу обвідку.
        # Колір той самий (LAND), обвідку прибрано: синій тепер достатньо відрізняється від тла.
        d.rectangle([lx, ly + 4, lx + 24, ly + 28], fill=col)
        d.text((lx + 34, ly + 1), label, font=lf, fill=MUTED)
        lx += 36 + d.textlength(label, font=lf) + 30
    # тонка рамка навколо мапи (24.11.2026)
    d.rectangle([ox - 1, top - 1, ox + mw, top + mh], outline=FRAME, width=2)
    # знак АКМЦ – зліва вгорі на кожній мапі, в одному місці (рішення Юлі 21.11.2026)
    try:
        lg = Image.open(LOGO).convert("RGBA")
        lg = lg.resize((LOGO_W, round(lg.height * LOGO_W / lg.width)), Image.LANCZOS)
        img = img.convert("RGBA")
        img.alpha_composite(lg, (ox + 18, top + 18))
        img = img.convert("RGB")
        d = ImageDraw.Draw(img)
    except Exception:  # noqa: BLE001
        pass
    # масштаб – у рядку легенди, довжина відрізка підбирається, щоб його було видно
    km = next((k for k in (5, 10, 20, 50, 100) if k * km_px >= 70), 100)
    bar = k_px = km * km_px
    t1 = "масштаб"
    w1 = d.textlength(t1, font=lf)
    t2 = f"{km} км"
    w2 = d.textlength(t2, font=lf)
    total = w1 + 14 + bar + 14 + w2
    sx = ox + mw - total
    sy2 = min(ly + 46, H - 40)
    foot = "За даними DeepStateMap.live · АКМЦ"
    ff = _font("500", 26)
    fw = d.textlength(foot, font=ff)
    if ox + fw + 24 > sx:   # не вміщається в один рядок з підписом джерела – без слова «масштаб» (12.11.2026)
        t1, w1 = "", -14
        total = bar + 14 + w2
        sx = ox + mw - total
    fy = sy2
    if ox + fw + 24 > sx:   # і так тісно – дрібніший шрифт підпису джерела
        ff = _font("500", 22)
        fw = d.textlength(foot, font=ff)
        fy = sy2 + 3
    d.text((sx, sy2), t1, font=lf, fill=MUTED)
    bx0, bx1, by = sx + w1 + 14, sx + w1 + 14 + bar, sy2 + 17
    d.line([bx0, by, bx1, by], fill=MUTED, width=4)
    d.line([bx0, by - 9, bx0, by + 9], fill=MUTED, width=3)
    d.line([bx1, by - 9, bx1, by + 9], fill=MUTED, width=3)
    d.text((bx1 + 14, sy2), t2, font=lf, fill=MUTED)
    d.text((ox, fy), foot, font=ff, fill=MUTED)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out


if __name__ == "__main__":
    import sys
    p = render([DATA / "changes" / f"{sys.argv[1]}.geojson"], Path(sys.argv[2]),
               "Біля Василівки та Світлого", "+4,24 км² позначено окупованими за добу")
    print(p)
