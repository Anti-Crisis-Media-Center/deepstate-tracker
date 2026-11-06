"""
DeepState: зміни за добу — Донецька і Луганська області.

Що робить скрипт (раз на добу, через GitHub Actions):
1. Завантажує актуальну мапу DeepState.
2. Вирізає окуповані території (включно з ОРДЛО) в межах Донецької та Луганської областей.
3. Порівнює з попереднім збереженим знімком: що додалося (окуповані) і що зникло (деокуповані).
4. Знаходить найближчі населені пункти до кожної зміни.
5. Складає текст за шаблоном (без ШІ) і надсилає пост із картинкою в Telegram.
6. Зберігає новий знімок і рядок в історію (data/history.csv).

Налаштування — у блоці CONFIG нижче та в секретах репозиторію.
"""

from __future__ import annotations

import csv
import html
import re
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from pyproj import Geod, Transformer
import numpy as np
from shapely import force_2d, make_valid, set_precision
from shapely import transform as shp_transform
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry import Point
from shapely.ops import unary_union

# ============================ CONFIG ============================

KYIV = ZoneInfo("Europe/Kyiv")

# О котрій годині за Києвом публікувати (скрипт не публікує раніше цієї години
# і не публікує двічі за день).
PUBLISH_HOUR = int(os.getenv("PUBLISH_HOUR", "8"))  # щоденний пост о 8:00 за Києвом
# GitHub стартує заплановані запуски із запізненням, тому запускаємося завчасно
# і чекаємо до PUBLISH_HOUR:00. Якщо старт раніше, ніж за стільки хвилин, — виходимо.
SUMMARY_DELAY_MINUTES = {"week": 10, "month": 20}  # тижневий о 8:10, місячний о 8:20
SHOW_DISTRICT = True  # «Миколаївка (Краматорський р-н)»
MAX_WAIT_MINUTES = int(os.getenv("MAX_WAIT_MINUTES", "100"))

DEEPSTATE_URL = "https://deepstatemap.live/api/history/last"

OCHA_OBLASTS_URL = (
    "https://gis.unocha.org/server/rest/services/Hosted/cod_ab_ukr_v05/"
    "FeatureServer/4/query"
)
SETTLEMENTS_URL = (
    "https://services3.arcgis.com/7J7WB6yJX0pYke9q/ArcGIS/rest/services/"
    "Admin4point/FeatureServer/0/query"
)

OBLASTS = {
    "donetsk": {"pcode": "UA14", "name_ua": "Донецька", "title": "Донецька область"},
    "luhansk": {"pcode": "UA44", "name_ua": "Луганська", "title": "Луганська область"},
}

# Які шари DeepState вважаємо окупованими
OCCUPIED_MARKERS = ("geoJSON.status.occupied", "geoJSON.territories.ordlo")
# Сіра зона — «територія, що потребує уточнення»
UNKNOWN_MARKERS = ("geoJSON.status.unknown",)

# Текстові повідомлення DeepState («Оновлення мапи»)
DEEPSTATE_HISTORY_URL = "https://deepstatemap.live/api/history/public"
MAX_MESSAGES_IN_POST = 12

# Ділянки змін, менші за цю площу, ігноруємо (шум від перемальовування меж)
MIN_CHANGE_KM2 = float(os.getenv("MIN_CHANGE_KM2", "0.01"))

# Населені пункти: шукаємо в радіусі, показуємо не більше N на тип зміни
PLACES_RADIUS_KM = 5.0
PLACES_PER_CHANGE = 2      # найближчих на кожну окрему ділянку
PLACES_MAX_IN_TEXT = 6
# Міста Донеччини й Луганщини: якщо місто в радіусі — воно завжди серед названих
CITIES = {
    "Авдіївка", "Бахмут", "Білицьке", "Білозерське", "Волноваха", "Вугледар", "Гірник", "Добропілля",
    "Докучаєвськ", "Дружківка", "Залізне", "Костянтинівка", "Краматорськ", "Красногорівка", "Курахове",
    "Лиман", "Мар'їнка", "Миколаївка", "Мирноград", "Новогродівка", "Покровськ", "Родинське", "Святогірськ",
    "Світлодарськ", "Селидове", "Сіверськ", "Слов'янськ", "Соледар", "Торецьк", "Українськ", "Часів Яр",
    "Сєвєродонецьк", "Лисичанськ", "Рубіжне", "Кремінна", "Попасна", "Привілля", "Гірське", "Золоте",
}     # загалом у тексті на тип зміни в області

# Показувати порівняння з футбольними полями (105×68 м)
SHOW_FOOTBALL_FIELDS = os.getenv("SHOW_FOOTBALL_FIELDS", "0") == "1"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE_DIR = DATA / "state"
REF_DIR = DATA / "reference"
CHANGES_DIR = DATA / "changes"
HISTORY_CSV = DATA / "history.csv"
def _cover(name: str) -> Path:
    """Картинку шукаємо в папці assets/, а якщо там немає — у корені репозиторію."""
    p = ROOT / "assets" / name
    return p if p.exists() else ROOT / name


COVER_IMAGE = _cover("cover.jpg")          # щоденний пост
COVER_WEEK = _cover("cover_week.jpg")      # тижневий підсумок
COVER_MONTH = _cover("cover_month.jpg")    # місячний підсумок

# Тижневий підсумок — у цей день тижня (0 = понеділок), місячний — 1-го числа
WEEKLY_WEEKDAY = 0

# Тестовий запуск: нічого не публікує і нічого не зберігає
DRY_RUN = os.getenv("DRY_RUN") == "1"

MONTHS_UA = ["січень", "лютий", "березень", "квітень", "травень", "червень",
             "липень", "серпень", "вересень", "жовтень", "листопад", "грудень"]

SOURCE_LINE = 'За даними <a href="https://deepstatemap.live">DeepStateMap.live</a>'
EDITOR_NOTE = (
    SOURCE_LINE + "\n\n"
    "<blockquote><i>Примітка редакції: це автоматизований аналіз змін на мапі "
    "DeepState. Він показує, як змінилися позначення на мапі порівняно з "
    "попередньою версією, але не підтверджує фактичну зміну лінії фронту в "
    "реальному часі. DeepState не є офіційним джерелом інформації.</i></blockquote>"
)

HEADERS = {"User-Agent": "ACMC-DeepState-tracker/1.0 (+https://acmc.ua)"}

GEOD = Geod(ellps="WGS84")
# UTM 37N — коректні відстані в метрах для Донеччини й Луганщини
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32637", always_xy=True).transform


def to_utm(geom):
    return shp_transform(geom, lambda c: np.column_stack(TO_UTM(c[:, 0], c[:, 1])))

# ============================ HELPERS ============================


def log(msg: str) -> None:
    print(f"[{datetime.now(KYIV):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def http_get_json(url: str, params: dict | None = None, tries: int = 4) -> dict:
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"Спроба {attempt}/{tries} не вдалася для {url}: {e}")
            time.sleep(10 * attempt)
    raise RuntimeError(f"Не вдалося завантажити {url}: {last_err}")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj) -> None:
    if DRY_RUN:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def clean_polygonal(geom):
    """Залишає лише полігони, виправляє невалідну геометрію, прибирає Z."""
    if geom is None or geom.is_empty:
        return None
    geom = force_2d(geom)
    if not geom.is_valid:
        geom = make_valid(geom)
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    return unary_union(polys) if polys else None


def normalize(geom):
    """Однакове округлення (~0,1 м) для поточного і збереженого знімка,
    щоб ідентичні мапи давали нульову різницю."""
    if geom is None or geom.is_empty:
        return None
    return clean_polygonal(set_precision(geom, 1e-6))


def area_km2(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    area, _ = GEOD.geometry_area_perimeter(geom)
    return abs(area) / 1_000_000


def drop_slivers(geom):
    """Прибирає дрібні ділянки (шум) і повертає список полігонів."""
    if geom is None or geom.is_empty:
        return []
    parts = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    parts = [p for p in parts if isinstance(p, Polygon)]
    return [p for p in parts if area_km2(p) >= MIN_CHANGE_KM2]


def fmt_km2(x: float) -> str:
    return f"{x:,.2f}".replace(",", "\u00a0").replace(".", ",")  # 16 625,84


def fmt_date(d: str) -> str:
    return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")


def football_fields(km2: float) -> int:
    fields = km2 / 0.00714
    if fields >= 100:
        return int(round(fields / 100) * 100)
    if fields >= 10:
        return int(round(fields / 10) * 10)
    return int(round(fields))


# ============================ DATA SOURCES ============================


def get_oblasts() -> dict:
    """Межі областей OCHA. Кешуються в data/reference — вони не змінюються."""
    cache = REF_DIR / "oblasts.geojson"
    try:
        if os.getenv("OBLASTS_FILE"):
            gj = load_json(Path(os.environ["OBLASTS_FILE"]))
        elif cache.exists():
            gj = load_json(cache)
        else:
            gj = http_get_json(
                OCHA_OBLASTS_URL,
                {
                    "where": "adm1_pcode IN ('UA14','UA44')",
                    "outFields": "adm1_pcode,adm1_name,area_sqkm",
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "f": "geojson",
                },
            )
            if not gj.get("features"):
                raise RuntimeError(f"OCHA повернула порожню відповідь: {str(gj)[:300]}")
            save_json(cache, gj)
    except Exception as e:
        raise RuntimeError(f"Межі областей: {e}") from e

    result = {}
    for key, cfg in OBLASTS.items():
        feat = next((f for f in gj["features"] if f["properties"].get("adm1_pcode") == cfg["pcode"]), None)
        if not feat:
            raise RuntimeError(f"У межах OCHA не знайдено {cfg['title']}")
        result[key] = clean_polygonal(shape(feat["geometry"]))
    return result


def get_settlements() -> list[dict]:
    """Населені пункти (точки ADM4). Кешуються в data/reference."""
    cache = REF_DIR / "settlements.geojson"
    if os.getenv("SETTLEMENTS_FILE"):
        features = load_json(Path(os.environ["SETTLEMENTS_FILE"]))["features"]
    elif cache.exists():
        features = load_json(cache)["features"]
    else:
        features, offset = [], 0
        while True:  # сервіс віддає дані сторінками — забираємо всі
            page = http_get_json(
                SETTLEMENTS_URL,
                {
                    "where": "ADM1_PCODE IN ('UA14','UA44')",
                    "outFields": "ADM4_UA,ADM4_PCODE,ADM2_UA,ADM1_UA,ADM1_PCODE",
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "resultOffset": offset,
                    "resultRecordCount": 1000,
                    "f": "geojson",
                },
            )
            batch = page.get("features", [])
            features.extend(batch)
            more = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
            if not batch or not more:
                break
            offset += len(batch)
        if not features:
            raise RuntimeError("Не вдалося отримати населені пункти")
        save_json(cache, {"type": "FeatureCollection", "features": features})
        log(f"Збережено довідник населених пунктів: {len(features)}")

    places = []
    for f in features:
        g, p = f.get("geometry"), f.get("properties", {})
        if not g or g.get("type") != "Point" or not p.get("ADM4_UA"):
            continue
        lon, lat = g["coordinates"][:2]
        x, y = TO_UTM(lon, lat)
        places.append({
            "name": p["ADM4_UA"],
            "district": p.get("ADM2_UA"),
            "oblast": p.get("ADM1_UA"),
            "x": x, "y": y,
        })
    return places


def get_deepstate() -> dict:
    if os.getenv("DEEPSTATE_FILE"):
        return load_json(Path(os.environ["DEEPSTATE_FILE"]))
    data = http_get_json(DEEPSTATE_URL)
    if not isinstance(data.get("map", {}).get("features"), list):
        raise RuntimeError("DeepState не повернув map.features")
    return data


# ============================ ANALYSIS ============================


def log_layer_names(deepstate: dict) -> None:
    """У журнал — які шари є на мапі (щоб помітити, якщо DeepState змінить формат)."""
    counts: dict[str, int] = {}
    for f in deepstate["map"]["features"]:
        name = (f.get("properties") or {}).get("name") or ""
        m = re.search(r"geoJSON\.[\w.]+", name)
        key = m.group(0) if m else (name[:40] or "<без назви>")
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:15]
    log("Шари на мапі: " + "; ".join(f"{k} × {v}" for k, v in top))


def layer_by_oblast(deepstate: dict, oblasts: dict, markers: tuple, label: str, required: bool) -> dict:
    layer = []
    for f in deepstate["map"]["features"]:
        name = (f.get("properties") or {}).get("name") or ""
        if not f.get("geometry") or not any(m in name for m in markers):
            continue
        try:
            g = clean_polygonal(shape(f["geometry"]))
        except Exception:  # noqa: BLE001
            continue
        if g is not None:
            layer.append(g)
    if not layer and required:
        raise RuntimeError(f"На мапі DeepState не знайдено шару «{label}» — схоже, змінився формат даних")
    log(f"Шар «{label}»: {len(layer)} полігонів на всій мапі")

    result = {}
    for key, oblast in oblasts.items():
        parts = []
        for g in layer:
            if not g.intersects(oblast):
                continue
            inter = clean_polygonal(g.intersection(oblast))
            if inter is not None:
                parts.append(inter)
        merged = normalize(unary_union(parts)) if parts else None
        result[key] = merged
        log(f"{OBLASTS[key]['title']}: «{label}» {fmt_km2(area_km2(merged))} км² "
            f"({area_km2(merged) / area_km2(oblast) * 100:.2f}% області)")
    return result


def occupied_by_oblast(deepstate: dict, oblasts: dict) -> dict:
    return layer_by_oblast(deepstate, oblasts, OCCUPIED_MARKERS, "окуповано", required=True)


def unknown_by_oblast(deepstate: dict, oblasts: dict) -> dict:
    return layer_by_oblast(deepstate, oblasts, UNKNOWN_MARKERS, "сіра зона", required=False)


# ============================ MESSAGES ============================


_ALT = str.maketrans({"і": "*", "е": "*", "о": "*", "и": "*", "ї": "*", "є": "*", "'": "", "’": "", "ʼ": ""})


def _norm(word: str) -> str:
    return word.lower().translate(_ALT)


def _name_key(name: str) -> str:
    """Основа назви для пошуку у відмінках: Гранів → «гр*н*», Майське → «м*йськ»."""
    n = _norm(name)
    k = max(5, len(n) - 3) if len(n) > 5 else max(3, len(n) - 1)
    return n[:k]


def build_name_index(places: list[dict]) -> dict:
    idx: dict[str, set] = {}
    for pl in places:
        idx.setdefault(_name_key(pl["name"]), set()).add(pl["oblast"])
    return idx


def clean_description(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # markdown-посилання → текст
    text = re.sub(r"<[^>]+>", "", text)                       # HTML-теги
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def message_oblasts(raw: str, oblasts: dict, name_idx: dict) -> set:
    """До яких наших областей стосується повідомлення: за координатами в посиланнях або за назвами."""
    found = set()
    coords = re.findall(r"(4[4-9]\.\d{3,})[/,\s]+(3[0-9]\.\d{3,}|4[0-1]\.\d{3,})", raw or "")
    if coords:
        for lat, lon in coords:
            pt = Point(float(lon), float(lat))
            for key, poly in oblasts.items():
                if poly.contains(pt):
                    found.add(key)
        return found
    text = clean_description(raw)
    low = text.lower()
    if "донеччин" in low or "донецьк" in low:
        found.add("donetsk")
    if "луганщин" in low or "луганськ" in low:
        found.add("luhansk")
    name_to_key = {cfg["name_ua"]: k for k, cfg in OBLASTS.items()}
    # слова з великої літери, разом із дефісами й складеними назвами (Нового Донбасу, Рай-Олександрівці)
    for m in re.finditer(r"[А-ЯҐЄІЇ][а-яґєіїʼ’'\-]+(?:\s+[А-ЯҐЄІЇ][а-яґєіїʼ’'\-]+)?", text):
        for cand in {m.group(0), m.group(0).split()[-1]}:
            key = _name_key(cand)
            for k, obl in ((k, o) for k, o in name_idx.items() if len(k) >= 4 and _norm(cand).startswith(k)):
                for o in obl:
                    if o in name_to_key:
                        found.add(name_to_key[o])
    return found


def get_messages(since_id: int | None, until_id: int | None, oblasts: dict, places: list) -> list[dict]:
    """Повідомлення DeepState між двома версіями мапи, що стосуються Донеччини/Луганщини."""
    if not since_id or not until_id or until_id <= since_id:
        return []
    try:
        if os.getenv("HISTORY_FILE"):
            items = load_json(Path(os.environ["HISTORY_FILE"]))
        else:
            items = http_get_json(DEEPSTATE_HISTORY_URL)
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося отримати повідомлення DeepState (пост вийде без них): {e}")
        return []
    if not isinstance(items, list):
        log("Повідомлення DeepState: неочікуваний формат, пропускаю.")
        return []
    name_idx = build_name_index(places)
    out = []
    for it in items:
        try:
            mid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        if not (since_id < mid <= until_id) or it.get("status") is False:
            continue
        raw = it.get("description") or ""
        text = clean_description(raw)
        if not text:
            continue
        obl = message_oblasts(raw, oblasts, name_idx)
        log(f"Повідомлення {datetime.fromtimestamp(mid, KYIV):%d.%m %H:%M}: {text[:120]} → "
            f"{', '.join(sorted(obl)) or 'не наші області'}")
        if obl:
            out.append({"id": mid, "time": datetime.fromtimestamp(mid, KYIV), "text": text, "oblasts": sorted(obl)})
    out.sort(key=lambda m: m["id"])
    return out


def nearest_places(polys: list, places: list, oblast_name: str) -> list[str]:
    found: dict[str, float] = {}
    for poly in polys:
        poly_utm = to_utm(poly)
        candidates = []
        for pl in places:
            if pl["oblast"] != oblast_name:
                continue
            d = poly_utm.distance(Point(pl["x"], pl["y"])) / 1000
            if d <= PLACES_RADIUS_KM:
                label = pl["name"]
                if SHOW_DISTRICT and pl.get("district"):
                    label += f" ({pl['district']} р-н)"
                candidates.append((d, label))
        ranked = sorted(candidates)
        # місто поблизу важливіше за найближче село: читач його знає
        city = [c for c in ranked if re.sub(r"\s*\(.*?\)", "", c[1]) in CITIES][:1]
        picked = city + [c for c in ranked if c not in city]
        for d, name in picked[:PLACES_PER_CHANGE]:
            found[name] = min(d, found.get(name, d))
    return [n for n, _ in sorted(found.items(), key=lambda kv: kv[1])][:PLACES_MAX_IN_TEXT]


def compare(current, previous, places, oblast_name):
    def diff(a, b):
        if a is None:
            return []
        if b is None:
            return drop_slivers(a)
        return drop_slivers(clean_polygonal(a.difference(b)))

    newly = diff(current, previous)
    deocc = diff(previous, current)
    return {
        "newly_km2": round(sum(area_km2(p) for p in newly), 2),
        "deocc_km2": round(sum(area_km2(p) for p in deocc), 2),
        "newly_polys": newly,
        "deocc_polys": deocc,
        "newly_places": nearest_places(newly, places, oblast_name),
        "deocc_places": nearest_places(deocc, places, oblast_name),
    }


# ============================ TEXT ============================


def join_places(places: list) -> str:
    """«Русин Яр, Іллінівка (Краматорський р-н), Часів Яр (Бахмутський р-н)» —
    район один раз після групи сусідніх пунктів з того самого району."""
    groups: list[tuple[str, list[str]]] = []
    for p in places:
        m = re.match(r"^(.*?)\s*\((.+?)\)\s*$", p)
        name, district = (m.group(1), m.group(2)) if m else (p, "")
        if groups and groups[-1][0] == district:
            groups[-1][1].append(name)
        else:
            groups.append((district, [name]))
    return ", ".join(", ".join(names) + (f" ({d})" if d else "") for d, names in groups)


def change_sentence(km2: float, kind: str, places: list[str]) -> str:
    s = f"на мапі DeepState {fmt_km2(km2)} км² територій змінили позначення на {kind}"
    if SHOW_FOOTBALL_FIELDS:
        s += f" (≈ {football_fields(km2)} футбольних полів)"
    s += "."
    if places:
        label = "Найближчий населений пункт" if len(places) == 1 else "Найближчі населені пункти"
        s += f" {label}: {join_places(places)}."
    return s


def grey_sentence(g: dict | None) -> str:
    if not g:
        return ""
    net = g["net_km2"]
    if abs(net) < 0.01 and not g["added_km2"]:
        return ""
    if net >= 0.01:
        s = f"Сіра зона (території, що потребують уточнення) збільшилася на {fmt_km2(net)} км²"
    elif net <= -0.01:
        s = f"Сіра зона (території, що потребують уточнення) зменшилася на {fmt_km2(-net)} км²"
    else:
        s = "Сіра зона (території, що потребують уточнення) змінила обриси"
    if g["places"]:
        s += f", нові сірі ділянки — поблизу: {join_places(g['places'])}"
    return s + "."


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def build_text(period: str, results: dict, grey: dict | None = None, messages: list | None = None) -> str:
    lines = ["<b>DeepState: зміни за добу</b>", "", f"Період: {period}", ""]
    for key, cfg in OBLASTS.items():
        r = results[key]
        parts = []
        if r["newly_km2"] > 0:
            parts.append(change_sentence(r["newly_km2"], "окуповані", r["newly_places"]))
        if r["deocc_km2"] > 0:
            parts.append(change_sentence(r["deocc_km2"], "деокуповані", r["deocc_places"]))
        gs = grey_sentence((grey or {}).get(key))
        if not parts:
            parts.append("змін позначень окупованих територій не зафіксовано.")
        if gs:
            parts.append(gs)
        body = " ".join(parts)
        lines.append(f"<b>{cfg['title']}:</b> {body[0].upper()}{body[1:]}")
        lines.append("")
    if messages:
        lines.append("<b>Повідомлення на сайті DeepState:</b>")
        for m in messages[:MAX_MESSAGES_IN_POST]:
            lines.append(f"• {m['time']:%d.%m, %H:%M} — {esc(m['text'])}")
        if len(messages) > MAX_MESSAGES_IN_POST:
            lines.append(f"…та ще {len(messages) - MAX_MESSAGES_IN_POST}.")
        lines.append("")
    else:
        lines.append("<b>Повідомлення на сайті DeepState:</b> нових повідомлень про Донеччину й Луганщину за добу не було.")
        lines.append("")
    lines.append(EDITOR_NOTE)
    return "\n".join(lines)


# ============================ TELEGRAM ============================


def tg(method: str, token: str, data: dict, files: dict | None = None) -> dict:
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=60)
    js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not js.get("ok"):
        raise RuntimeError(f"Telegram {method}: {r.status_code} {str(js or r.text)[:300]}")
    return js


def publish(text: str, image: Path = COVER_IMAGE) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if DRY_RUN or not token or not chat_id:
        log("DRY_RUN або немає секретів Telegram — пост не надсилається. Текст:\n" + text)
        return
    base = {"chat_id": chat_id, "parse_mode": "HTML"}
    if image.exists() and len(text) <= 1024:  # ліміт підпису до фото в Telegram
        with image.open("rb") as fh:
            tg("sendPhoto", token, {**base, "caption": text}, {"photo": fh})
    else:
        if image.exists():
            with image.open("rb") as fh:
                tg("sendPhoto", token, {"chat_id": chat_id}, {"photo": fh})
        tg("sendMessage", token, {**base, "text": text, "disable_web_page_preview": "true"})
    log(f"Пост надіслано в Telegram ({image.name if image.exists() else 'без картинки'}).")


# ============================ FACEBOOK-ТИЗЕР ============================
# Короткий текст для ручної публікації у Facebook. Бот надсилає його у службовий
# чат (TELEGRAM_ALERT_CHAT_ID): Юля копіює текст і обкладинку у Facebook,
# а друге повідомлення — у перший коментар. Повна версія лишається в Telegram.

FB_TELEGRAM_LINK = os.getenv("FB_TELEGRAM_LINK", "https://t.me/acmc_ua").strip() or "https://t.me/acmc_ua"
FB_CTA = ("Повідомлення DeepState, щодобові, щотижневі та щомісячні підсумки змін на мапі — "
          "щоранку о 8:00 у нашому Telegram-каналі. Посилання в першому коментарі 👇")
FB_COMMENT = f"Підписуйтеся на наш Telegram, щоб щоранку бачити зміни на мапі DeepState по Донеччині й Луганщині: {FB_TELEGRAM_LINK}"


def _teaser_line(title: str, newly: float, deocc: float, newly_pl: list, deocc_pl: list) -> str | None:
    parts = []
    if newly > 0:
        s = f"{fmt_km2(newly)} км² позначено окупованими"
        if newly_pl:
            s += f" — поблизу: {join_places(newly_pl[:3])}"
        parts.append(s)
    if deocc > 0:
        s = f"{fmt_km2(deocc)} км² — деокупованими"
        if deocc_pl:
            s += f" — поблизу: {join_places(deocc_pl[:3])}"
        parts.append(s)
    return f"{title}: " + "; ".join(parts) + "." if parts else None


def build_fb_teaser(day: str, results: dict) -> str:
    lines = [f"DeepState: зміни за добу, {day}", ""]
    found = [_teaser_line(cfg["title"], results[k]["newly_km2"], results[k]["deocc_km2"],
                          results[k]["newly_places"], results[k]["deocc_places"]) for k, cfg in OBLASTS.items()]
    found = [f for f in found if f]
    lines += found or ["На Донеччині й Луганщині змін позначень окупованих територій за добу не зафіксовано."]
    lines += ["", FB_CTA, "", "За даними DeepStateMap.live"]
    return "\n".join(lines)


def build_fb_summary_teaser(kind: str, rows: list[dict], label: str | None = None) -> str:
    title = "DeepState: підсумки тижня" if kind == "week" else f"DeepState: підсумки місяця — {label}"
    word = "тиждень" if kind == "week" else "місяць"
    lines = [title, ""]
    for key, cfg in OBLASTS.items():
        first, last = rows[0], rows[-1]
        before = (_f(first[f"{key}_occupied_km2"]) - _f(first[f"{key}_newly_occupied_km2"])
                  + _f(first[f"{key}_deoccupied_km2"]))
        net = round(_f(last[f"{key}_occupied_km2"]) - before, 2)
        if net >= 0.01:
            lines.append(f"{cfg['title']}: за {word} площа, позначена окупованою, зросла на {fmt_km2(net)} км².")
        elif net <= -0.01:
            lines.append(f"{cfg['title']}: за {word} площа, позначена окупованою, зменшилася на {fmt_km2(-net)} км².")
        else:
            lines.append(f"{cfg['title']}: за {word} помітних змін не зафіксовано.")
    lines += ["", "Повний підсумок із населеними пунктами — у Telegram-каналі Антикризового медіа-центру. "
              "Посилання в першому коментарі 👇", "", "За даними DeepStateMap.live"]
    return "\n".join(lines)


FB_GRAPH = "https://graph.facebook.com/" + (os.getenv("FB_GRAPH_VERSION", "v24.0").strip() or "v24.0")


def post_facebook(text: str, image: Path) -> bool:
    """Публікує тизер на сторінці Facebook і додає перший коментар із посиланням на Telegram.
    Повертає True, якщо Facebook налаштований і пост вийшов."""
    page_id = os.getenv("FACEBOOK_PAGE_ID", "").strip()
    page_token = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
    if not page_id or not page_token:
        return False
    if DRY_RUN:
        log("DRY_RUN — у Facebook не публікується. Текст:\n" + text)
        return True
    try:
        if image.exists():
            with image.open("rb") as fh:
                r = requests.post(f"{FB_GRAPH}/{page_id}/photos",
                                  data={"caption": text, "published": "true", "access_token": page_token},
                                  files={"source": fh}, timeout=120)
        else:
            r = requests.post(f"{FB_GRAPH}/{page_id}/feed",
                              data={"message": text, "access_token": page_token}, timeout=60)
        js = r.json()
        if "error" in js:
            raise RuntimeError(js["error"].get("message", str(js["error"]))[:300])
        post_id = js.get("post_id") or js.get("id")
        log(f"Пост у Facebook опубліковано: {post_id}")
        c = requests.post(f"{FB_GRAPH}/{post_id}/comments",
                          data={"message": FB_COMMENT, "access_token": page_token}, timeout=60).json()
        if "error" in c:
            log(f"Коментар у Facebook не додано: {c['error'].get('message')}")
            alert(f"Пост у Facebook вийшов, але перший коментар не додано: {c['error'].get('message')}")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"Facebook: помилка — {e}")
        alert(f"Не вдалося опублікувати у Facebook: {e}. Нижче — тизер для ручної публікації.")
        return False  # тоді send_fb_teaser надішле готовий тизер у службовий чат


def send_fb_teaser(text: str, image: Path) -> None:
    """Тизер для Facebook: якщо є секрети Facebook — публікує автоматично,
    інакше надсилає готовий текст у службовий чат. Помилка тут не зупиняє бота."""
    if post_facebook(text, image):
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if DRY_RUN or not token or not chat_id:
        log("Тизер для Facebook (не надсилається):\n" + text)
        return
    try:
        head = {"chat_id": chat_id, "caption": "📘 Для Facebook — обкладинка, нижче текст посту і перший коментар"}
        if image.exists():
            with image.open("rb") as fh:
                tg("sendPhoto", token, head, {"photo": fh})
        else:
            tg("sendMessage", token, {"chat_id": chat_id, "text": head["caption"]})
        tg("sendMessage", token, {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})
        tg("sendMessage", token, {"chat_id": chat_id, "text": "💬 Перший коментар:"})
        tg("sendMessage", token, {"chat_id": chat_id, "text": FB_COMMENT, "disable_web_page_preview": "true"})
        log("Тизер для Facebook надіслано в службовий чат.")
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося надіслати тизер для Facebook: {e}")


# ============================ FACEBOOK-СТОРІЗ ============================

MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
STORY_LABELS = {"donetsk": "ДОНЕЧЧИНА", "luhansk": "ЛУГАНЩИНА"}
FB_STORIES = os.getenv("FB_STORIES", "1") != "0"  # сторіз у Facebook увімкнено (4.11.2026)


def _short_places(places: list, n: int = 3) -> str:
    return ", ".join(re.sub(r"\s*\(.*?\)", "", p) for p in places[:n])


def _date_label(d) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


def story_blocks_daily(results: dict) -> list[dict]:
    blocks = []
    for k in OBLASTS:
        r = results[k]
        if r["newly_km2"] > 0:
            blocks.append({"label": STORY_LABELS[k], "big": f"+{fmt_km2(r['newly_km2'])} км²",
                           "text": "позначено окупованими",
                           "note": f"поблизу: {_short_places(r['newly_places'])}" if r["newly_places"] else ""})
        if r["deocc_km2"] > 0:
            blocks.append({"label": "" if r["newly_km2"] > 0 else STORY_LABELS[k],
                           "big": f"−{fmt_km2(r['deocc_km2'])} км²", "text": "позначено деокупованими",
                           "note": f"поблизу: {_short_places(r['deocc_places'])}" if r["deocc_places"] else ""})
    if not blocks:
        blocks.append({"big": "", "text": "На Донеччині й Луганщині змін позначень окупованих територій "
                                          "за добу не зафіксовано."})
    else:
        for k in OBLASTS:
            if results[k]["newly_km2"] <= 0 and results[k]["deocc_km2"] <= 0:
                blocks.append({"label": STORY_LABELS[k], "big": "", "text": "змін не зафіксовано"})
    return blocks


def story_blocks_summary(kind: str, rows: list[dict]) -> list[dict]:
    word = "тиждень" if kind == "week" else "місяць"
    blocks = []
    for k in OBLASTS:
        first, last = rows[0], rows[-1]
        before = (_f(first[f"{k}_occupied_km2"]) - _f(first[f"{k}_newly_occupied_km2"])
                  + _f(first[f"{k}_deoccupied_km2"]))
        net = round(_f(last[f"{k}_occupied_km2"]) - before, 2)
        if abs(net) >= 0.01:
            blocks.append({"label": STORY_LABELS[k], "big": f"{'+' if net > 0 else '−'}{fmt_km2(abs(net))} км²",
                           "text": f"за {word} площа, позначена окупованою, "
                                   f"{'зросла' if net > 0 else 'зменшилася'}"})
        else:
            blocks.append({"label": STORY_LABELS[k], "big": "", "text": f"за {word} помітних змін не зафіксовано"})
    return blocks


def post_facebook_story(image_path: Path) -> None:
    """Публікує картинку як сторіз сторінки Facebook. Помилка не зупиняє бота."""
    page_id = os.getenv("FACEBOOK_PAGE_ID", "").strip()
    page_token = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
    if not FB_STORIES or not page_id or not page_token or DRY_RUN:
        if DRY_RUN:
            log(f"DRY_RUN — сторіз не публікується ({image_path}).")
        return
    try:
        with image_path.open("rb") as fh:
            up = requests.post(f"{FB_GRAPH}/{page_id}/photos",
                               data={"published": "false", "access_token": page_token},
                               files={"source": fh}, timeout=120).json()
        if "error" in up:
            raise RuntimeError(up["error"].get("message", str(up["error"]))[:300])
        st = requests.post(f"{FB_GRAPH}/{page_id}/photo_stories",
                           data={"photo_id": up["id"], "access_token": page_token}, timeout=60).json()
        if "error" in st:
            raise RuntimeError(st["error"].get("message", str(st["error"]))[:300])
        log(f"Сторіз у Facebook опубліковано: {st.get('post_id')}")
    except Exception as e:  # noqa: BLE001
        log(f"Facebook-сторіз: помилка — {e}")
        alert(f"Пост у Facebook вийшов, але сторіз не опубліковано: {e}")


def _period_label(start: str, end: str) -> str:
    a, b = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
    if a.month == b.month and a.year == b.year:
        return f"{a.day}–{b.day} {MONTHS_GEN[b.month - 1]} {b.year}"
    return f"{a.day} {MONTHS_GEN[a.month - 1]} – {b.day} {MONTHS_GEN[b.month - 1]} {b.year}"


def make_and_post_story(cover: Path, date_label: str, blocks: list[dict]) -> None:
    if not FB_STORIES or not os.getenv("FACEBOOK_PAGE_TOKEN", "").strip():
        return
    try:
        from story import render_story
        out = render_story(cover, date_label, blocks, DATA / "story.jpg")
        post_facebook_story(out)
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося зробити сторіз: {e}")


def alert(message: str) -> None:
    """Повідомлення про помилку в окремий (службовий) чат, якщо він налаштований."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        tg("sendMessage", token, {"chat_id": chat_id, "text": f"⚠️ DeepState-трекер: {message}"[:4000]})
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося надіслати сповіщення про помилку: {e}")


# ============================ STATE ============================
# data/state/current.json — останній знімок (дата D)
# data/state/base.json    — знімок, з яким порівнювали сьогодні
# Повторний запуск того ж дня порівнює з тим самим base, тож дублі не виникають.


def geom_to_json(g):
    return mapping(g) if g is not None else None


def geom_from_json(j):
    return normalize(shape(j)) if j else None


HISTORY_FIELDS = [
    "date", "compared_with", "deepstate_id",
    "donetsk_occupied_km2", "luhansk_occupied_km2",
    "donetsk_occupied_percent", "luhansk_occupied_percent",
    "donetsk_newly_occupied_km2", "donetsk_deoccupied_km2",
    "luhansk_newly_occupied_km2", "luhansk_deoccupied_km2",
    "donetsk_unknown_km2", "luhansk_unknown_km2",
    "donetsk_unknown_net_km2", "luhansk_unknown_net_km2",
    "messages_count", "messages",
    "donetsk_newly_places", "donetsk_deoccupied_places",
    "luhansk_newly_places", "luhansk_deoccupied_places",
]


def read_history() -> list[dict]:
    if not HISTORY_CSV.exists():
        return []
    with HISTORY_CSV.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def upsert_history(row: dict) -> None:
    """Додає рядок за день або замінює наявний (повторний запуск того ж дня)."""
    rows = [r for r in read_history() if r.get("date") != row["date"]] + [row]
    rows.sort(key=lambda r: r["date"])
    if DRY_RUN:
        return
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ============================ SUMMARIES ============================


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _places(rows: list[dict], col: str, limit: int = PLACES_MAX_IN_TEXT) -> list[str]:
    """Населені пункти, що найчастіше траплялися за період (за кількістю днів)."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for r in rows:
        for name in filter(None, (n.strip() for n in (r.get(col) or "").split(";"))):
            if name not in counts:
                order.append(name)
            counts[name] = counts.get(name, 0) + 1
    order.sort(key=lambda n: -counts[n])
    return order[:limit]


def build_summary(kind: str, rows: list[dict], period_start: str, period_end: str, label: str | None = None) -> str:
    title = "DeepState: підсумки тижня" if kind == "week" else f"DeepState: підсумки місяця — {label}"
    word = "За тиждень" if kind == "week" else "За місяць"
    lines = [f"<b>{title}</b>", "", f"Період: {fmt_date(period_start)}–{fmt_date(period_end)}", ""]
    for key, cfg in OBLASTS.items():
        newly = round(sum(_f(r[f"{key}_newly_occupied_km2"]) for r in rows), 2)
        deocc = round(sum(_f(r[f"{key}_deoccupied_km2"]) for r in rows), 2)
        days = sum(1 for r in rows if _f(r[f"{key}_newly_occupied_km2"]) > 0 or _f(r[f"{key}_deoccupied_km2"]) > 0)
        if newly == 0 and deocc == 0:
            body = "Змін позначень окупованих територій не зафіксовано."
        else:
            parts = []
            if newly > 0:
                parts.append(f"{fmt_km2(newly)} км² територій змінили позначення на окуповані")
            if deocc > 0:
                parts.append(f"{fmt_km2(deocc)} км² — на деокуповані")
            body = f"{word.lower()} на мапі DeepState " + ", ".join(parts) + "."
            body += f" Зміни фіксувалися в {days} із {len(rows)} днів."
            newly_pl = _places(rows, f"{key}_newly_places")
            deocc_pl = _places(rows, f"{key}_deoccupied_places")
            if newly_pl:
                body += f" Окуповані — поблизу населених пунктів: {join_places(newly_pl)}."
            if deocc_pl:
                body += f" Деокуповані — поблизу населених пунктів: {join_places(deocc_pl)}."
            body = body[0].upper() + body[1:]
            # Чистий баланс: стан на кінець періоду мінус стан перед його початком
            first, last_row = rows[0], rows[-1]
            before = (_f(first[f"{key}_occupied_km2"]) - _f(first[f"{key}_newly_occupied_km2"])
                      + _f(first[f"{key}_deoccupied_km2"]))
            net = round(_f(last_row[f"{key}_occupied_km2"]) - before, 2)
            period_word = "тиждень" if kind == "week" else "місяць"
            if net >= 0.01:
                body += f" У підсумку площа, позначена окупованою, за {period_word} зросла на {fmt_km2(net)} км²."
            elif net <= -0.01:
                body += f" У підсумку площа, позначена окупованою, за {period_word} зменшилася на {fmt_km2(-net)} км²."
            else:
                body += f" У підсумку площа, позначена окупованою, за {period_word} майже не змінилася."
        grey_net = round(sum(_f(r.get(f"{key}_unknown_net_km2")) for r in rows), 2)
        if abs(grey_net) >= 0.01:
            body += (f" Сіра зона {'збільшилася' if grey_net > 0 else 'зменшилася'} на {fmt_km2(abs(grey_net))} км².")
        last = rows[-1]
        pct = _f(last.get(f"{key}_occupied_percent"))
        total = f" Загалом позначено окупованими {fmt_km2(_f(last[f'{key}_occupied_km2']))} км²"
        total += f" ({str(round(pct, 1)).replace('.', ',')}% області)." if pct else "."
        lines.append(f"<b>{cfg['title']}:</b> {body}{total}")
        lines.append("")
    if kind == "week":
        # Тижневий підсумок: перелік повідомлень DeepState за тиждень (місячний — без них)
        msgs = []
        for r in rows:
            for m in filter(None, (x.strip() for x in (r.get("messages") or "").split(" | "))):
                mm = re.match(r"(\d{2}\.\d{2}) (\d{2}:\d{2}) (.*)", m)
                msgs.append(f"• {mm.group(1)}, {mm.group(2)} — {esc(mm.group(3))}" if mm else f"• {esc(m)}")
        msgs = list(dict.fromkeys(msgs))
        if msgs:
            lines.append("<b>Повідомлення на сайті DeepState за тиждень:</b>")
            lines.extend(msgs[:MAX_MESSAGES_IN_POST])
            if len(msgs) > MAX_MESSAGES_IN_POST:
                lines.append(f"…та ще {len(msgs) - MAX_MESSAGES_IN_POST}.")
            lines.append("")
    lines.append(EDITOR_NOTE)
    return "\n".join(lines)


def due_summaries(now: datetime) -> list[dict]:
    """Які підсумки належить опублікувати сьогодні."""
    today = now.date()
    due = []
    if today.weekday() == WEEKLY_WEEKDAY:
        due.append({"kind": "week", "start": (today - timedelta(days=7)).isoformat(), "image": COVER_WEEK})
    if today.day == 1:
        prev_last = today - timedelta(days=1)
        due.append({"kind": "month", "start": prev_last.replace(day=1).isoformat(), "image": COVER_MONTH,
                    "label": f"{MONTHS_UA[prev_last.month - 1]} {prev_last.year}"})
    return due


def wait_until(target: datetime) -> None:
    """Чекає до вказаного часу (крім ручного запуску з force, тесту і dry run)."""
    if os.getenv("FORCE_RUN") == "1" or os.getenv("NOW") or DRY_RUN:
        return
    left = (target - datetime.now(KYIV)).total_seconds()
    if left > 0:
        log(f"Чекаю до {target:%H:%M}, щоб опублікувати підсумок…")
        time.sleep(left)


def run_summaries(now: datetime, state: dict, save=None) -> None:
    today = now.strftime("%Y-%m-%d")
    done = state.setdefault("summaries_done", {})
    history = read_history()
    for s in due_summaries(now):
        if done.get(s["kind"]) == today:
            continue
        rows = [r for r in history if s["start"] < r["date"] <= today]
        need = 6 if s["kind"] == "week" else 25
        if len(rows) < need:
            log(f"Підсумок ({s['kind']}): даних лише за {len(rows)} дн. (потрібно {need}) — пропускаю.")
            done[s["kind"]] = today
            continue
        start = min(r["compared_with"] for r in rows)
        text = build_summary(s["kind"], rows, start, today, s.get("label"))
        publish_at = now.replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
        wait_until(publish_at + timedelta(minutes=SUMMARY_DELAY_MINUTES.get(s["kind"], 0)))
        publish(text, s["image"])
        send_fb_teaser(build_fb_summary_teaser(s["kind"], rows, s.get("label")), s["image"])
        story_date = (s["label"][0].upper() + s["label"][1:]) if s["kind"] == "month" and s.get("label") \
            else _period_label(start, today)
        make_and_post_story(s["image"], story_date, story_blocks_summary(s["kind"], rows))
        done[s["kind"]] = today
        if save:
            save()


# ============================ MAIN ============================


def main() -> int:
    now = datetime.fromisoformat(os.environ["NOW"]).astimezone(KYIV) if os.getenv("NOW") else datetime.now(KYIV)
    today = now.strftime("%Y-%m-%d")
    force = os.getenv("FORCE_RUN") == "1"
    if DRY_RUN:
        log("ТЕСТОВИЙ ЗАПУСК: нічого не публікується і не зберігається.")

    current_path, base_path = STATE_DIR / "current.json", STATE_DIR / "base.json"
    current_state = load_json(current_path) if current_path.exists() else None

    def all_done(st) -> bool:
        if not st or st.get("date") != today or not st.get("published"):
            return False
        done = st.get("summaries_done", {})
        return all(done.get(s["kind"]) == today for s in due_summaries(now))

    if not force:
        publish_at = now.replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
        if now < publish_at:
            wait = (publish_at - now).total_seconds()
            if wait > MAX_WAIT_MINUTES * 60:
                log(f"Зараз {now:%H:%M} за Києвом — ще зарано навіть для очікування. Вихід.")
                return 0
            if all_done(current_state):
                log("Сьогодні вже все опубліковано. Вихід.")
                return 0
            log(f"Зараз {now:%H:%M:%S}. Чекаю до {publish_at:%H:%M} за Києвом, щоб опублікувати вчасно…")
            if not os.getenv("NOW"):          # у тестах не чекаємо
                time.sleep(wait)
                now = datetime.now(KYIV)
            else:
                now = publish_at
            today = now.strftime("%Y-%m-%d")
        if all_done(current_state):
            log("Сьогодні вже все опубліковано. Вихід.")
            return 0
        if current_state and current_state.get("date") == today and current_state.get("published"):
            # Щоденний пост уже вийшов, лишилися тільки підсумки
            run_summaries(now, current_state, lambda: save_json(current_path, current_state))
            save_json(current_path, current_state)
            return 0

    # Визначаємо, з чим порівнювати
    if current_state and current_state["date"] < today:
        base_state = current_state
    elif base_path.exists():
        base_state = load_json(base_path)
    else:
        base_state = None

    oblasts = get_oblasts()
    places = get_settlements()
    deepstate = get_deepstate()
    log_layer_names(deepstate)
    occupied = occupied_by_oblast(deepstate, oblasts)
    unknown = unknown_by_oblast(deepstate, oblasts)

    new_state = {
        "date": today,
        "saved_at": now.isoformat(timespec="seconds"),
        "deepstate_id": deepstate.get("id"),
        "deepstate_datetime": deepstate.get("datetime"),
        "published": False,
        "summaries_done": (current_state or {}).get("summaries_done", {}),
        "occupied": {k: geom_to_json(v) for k, v in occupied.items()},
        "occupied_km2": {k: round(area_km2(v), 2) for k, v in occupied.items()},
        "occupied_percent": {k: round(area_km2(v) / area_km2(oblasts[k]) * 100, 2) for k, v in occupied.items()},
        "unknown": {k: geom_to_json(v) for k, v in unknown.items()},
        "unknown_km2": {k: round(area_km2(v), 2) for k, v in unknown.items()},
    }

    if base_state is None:
        save_json(current_path, new_state)
        log("Це перший запуск: знімок збережено як точку відліку. Перший пост буде завтра о "
            f"{PUBLISH_HOUR}:00.")
        return 0

    results = {}
    for key, cfg in OBLASTS.items():
        prev = geom_from_json(base_state["occupied"].get(key))
        results[key] = compare(occupied[key], prev, places, cfg["name_ua"])
        log(f"{cfg['title']}: +{results[key]['newly_km2']} / −{results[key]['deocc_km2']} км²")

    period = f"{fmt_date(base_state['date'])}–{fmt_date(today)}"
    # Сіра зона: чиста зміна площі + нові сірі ділянки (якщо в попередньому знімку вона є)
    grey = {}
    if "unknown" in base_state:
        for key, cfg in OBLASTS.items():
            prev_u = geom_from_json(base_state["unknown"].get(key))
            cur_u = unknown[key]
            added = drop_slivers(cur_u if prev_u is None else clean_polygonal(cur_u.difference(prev_u))) if cur_u else []
            grey[key] = {
                "net_km2": round(area_km2(cur_u) - area_km2(prev_u), 2),
                "added_km2": round(sum(area_km2(p) for p in added), 2),
                "places": nearest_places(added, places, cfg["name_ua"]),
            }
            log(f"{cfg['title']}: сіра зона {grey[key]['net_km2']:+} км² (нових ділянок {grey[key]['added_km2']} км²)")
    else:
        log("У попередньому знімку немає сірої зони — її зміну покажемо з наступного дня.")

    messages = get_messages(base_state.get("deepstate_id"), deepstate.get("id"), oblasts, places)
    log(f"Повідомлень DeepState про Донеччину/Луганщину за період: {len(messages)}")

    text = build_text(period, results, grey, messages)

    # Зберігаємо стан ДО публікації: якщо Telegram впаде, дані не загубляться
    if current_state and current_state["date"] < today:
        save_json(base_path, current_state)
    save_json(current_path, new_state)

    # Геометрія змін — для майбутніх карт
    feats = []
    for key, r in results.items():
        for kind in ("newly", "deocc"):
            for p in r[f"{kind}_polys"]:
                feats.append({"type": "Feature", "geometry": mapping(p),
                              "properties": {"oblast": key, "change": "occupied" if kind == "newly" else "deoccupied",
                                             "km2": round(area_km2(p), 3)}})
    if feats:
        save_json(CHANGES_DIR / f"{today}.geojson", {"type": "FeatureCollection", "features": feats})

    upsert_history({
        "date": today,
        "compared_with": base_state["date"],
        "deepstate_id": deepstate.get("id"),
        **{f"{k}_occupied_km2": new_state["occupied_km2"][k] for k in OBLASTS},
        **{f"{k}_occupied_percent": new_state["occupied_percent"][k] for k in OBLASTS},
        **{f"{k}_newly_occupied_km2": results[k]["newly_km2"] for k in OBLASTS},
        **{f"{k}_deoccupied_km2": results[k]["deocc_km2"] for k in OBLASTS},
        **{f"{k}_newly_places": "; ".join(results[k]["newly_places"]) for k in OBLASTS},
        **{f"{k}_deoccupied_places": "; ".join(results[k]["deocc_places"]) for k in OBLASTS},
        **{f"{k}_unknown_km2": new_state["unknown_km2"][k] for k in OBLASTS},
        **{f"{k}_unknown_net_km2": (grey.get(k) or {}).get("net_km2", "") for k in OBLASTS},
        "messages_count": len(messages),
        "messages": " | ".join(f"{m['time']:%d.%m %H:%M} {m['text']}" for m in messages),
    })

    publish(text, COVER_IMAGE)
    send_fb_teaser(build_fb_teaser(fmt_date(today), results), COVER_IMAGE)
    make_and_post_story(COVER_IMAGE, _date_label(now.date()), story_blocks_daily(results))
    new_state["published"] = True
    save_json(current_path, new_state)

    run_summaries(now, new_state, lambda: save_json(current_path, new_state))
    save_json(current_path, new_state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"ПОМИЛКА: {e}")
        alert(str(e))
        raise
