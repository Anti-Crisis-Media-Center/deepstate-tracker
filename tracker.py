"""
DeepState: зміни за добу – Донецька і Луганська області.

Що робить скрипт (раз на добу, через GitHub Actions):
1. Завантажує актуальну мапу DeepState.
2. Вирізає окуповані території (включно з ОРДЛО) в межах Донецької та Луганської областей.
3. Порівнює з попереднім збереженим знімком: що додалося (окуповані) і що зникло (деокуповані).
4. Знаходить найближчі населені пункти до кожної зміни.
5. Складає текст за шаблоном (без ШІ) і надсилає пост із картинкою в Telegram.
6. Зберігає новий знімок і рядок в історію (data/history.csv).

Налаштування – у блоці CONFIG нижче та в секретах репозиторію.
"""

from __future__ import annotations

import csv
import html
import re
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import ig_api
import threads_api
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
# і чекаємо до PUBLISH_HOUR:00. Якщо старт раніше, ніж за стільки хвилин, – виходимо.
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
    "donetsk": {"pcode": "UA14", "name_ua": "Донецька", "title": "Донецька область", "short": "Донеччина"},
    "luhansk": {"pcode": "UA44", "name_ua": "Луганська", "title": "Луганська область", "short": "Луганщина"},
}

# Які шари DeepState вважаємо окупованими
OCCUPIED_MARKERS = ("geoJSON.status.occupied", "geoJSON.territories.ordlo")
# Сіра зона – «територія, що потребує уточнення»
UNKNOWN_MARKERS = ("geoJSON.status.unknown",)

# Текстові повідомлення DeepState («Оновлення мапи»)
# Сіра зона в тексті зведень вимкнена: читачам незрозуміла, перевантажує. Дані далі пишуться в history.csv.
SHOW_GREY_IN_TEXT = os.getenv("SHOW_GREY_IN_TEXT", "0") == "1"
DEEPSTATE_HISTORY_URL = "https://deepstatemap.live/api/history/public"
MAX_MESSAGES_IN_POST = 12

# Ділянки змін, менші за цю площу, ігноруємо (шум від перемальовування меж)
MIN_CHANGE_KM2 = float(os.getenv("MIN_CHANGE_KM2", "0.01"))

# Населені пункти: шукаємо в радіусі, показуємо не більше N на тип зміни
PLACES_RADIUS_KM = 5.0
PLACES_PER_CHANGE = 2      # найближчих на кожну окрему ділянку
PLACES_MAX_IN_TEXT = 6
# Міста Донеччини й Луганщини: якщо місто в радіусі – воно завжди серед названих
# Миколаївку прибрано з міст 12.11.2026: у Краматорському районі їх дві – місто біля Слов'янська
# і село біля Костянтинівки (біля фронту). Розрізнити за назвою неможливо, тож бот помилково писав «місто».
CITIES = {
    "Авдіївка", "Бахмут", "Білицьке", "Білозерське", "Волноваха", "Вугледар", "Гірник", "Добропілля",
    "Докучаєвськ", "Дружківка", "Залізне", "Костянтинівка", "Краматорськ", "Красногорівка", "Курахове",
    "Лиман", "Мар'їнка", "Мирноград", "Новогродівка", "Покровськ", "Родинське", "Святогірськ",
    "Світлодарськ", "Селидове", "Сіверськ", "Слов'янськ", "Соледар", "Торецьк", "Українськ", "Часів Яр",
    "Сєвєродонецьк", "Лисичанськ", "Рубіжне", "Кремінна", "Попасна", "Привілля", "Гірське", "Золоте",
}     # загалом у тексті на тип зміни в області
# Назви, які мають і міста, і села: містом вважаємо лише в цьому районі
CITY_DISTRICT = {"Лиман": "Краматорський", "Залізне": "Бахмутський",
                 "Гірник": "Покровський", "Українськ": "Покровський", "Білицьке": "Покровський",
                 "Родинське": "Покровський", "Білозерське": "Краматорський", "Золоте": "Сєвєродонецький",
                 "Гірське": "Сєвєродонецький", "Привілля": "Сєвєродонецький", "Світлодарськ": "Бахмутський",
                 "Рубіжне": "Сєвєродонецький", "Кремінна": "Сєвєродонецький", "Попасна": "Сєвєродонецький",
                 "Сєвєродонецьк": "Сєвєродонецький", "Лисичанськ": "Сєвєродонецький", "Добропілля": "Покровський",
                 "Костянтинівка": "Краматорський", "Дружківка": "Краматорський"}


def is_city(name: str, district: str | None = None) -> bool:
    """name може бути «Лиман (Краматорський р-н)» – тоді район беремо з дужок."""
    m = re.match(r"\s*(.*?)\s*\((.*?) р-н\)", name)
    if m:
        name, district = m.group(1), m.group(2)
    if name not in CITIES:
        return False
    need = CITY_DISTRICT.get(name)
    return not need or not district or district == need

CITY_GENITIVE = {'Авдіївка': 'Авдіївки', 'Бахмут': 'Бахмута', 'Білицьке': 'Білицького', 'Білозерське': 'Білозерського', 'Волноваха': 'Волновахи', 'Вугледар': 'Вугледара', 'Гірник': 'Гірника', 'Добропілля': 'Добропілля', 'Докучаєвськ': 'Докучаєвська', 'Дружківка': 'Дружківки', 'Залізне': 'Залізного', 'Костянтинівка': 'Костянтинівки', 'Краматорськ': 'Краматорська', 'Красногорівка': 'Красногорівки', 'Курахове': 'Курахового', 'Лиман': 'Лимана', "Мар'їнка": "Мар'їнки", 'Миколаївка': 'Миколаївки', 'Мирноград': 'Мирнограда', 'Новогродівка': 'Новогродівки', 'Покровськ': 'Покровська', 'Родинське': 'Родинського', 'Святогірськ': 'Святогірська', 'Світлодарськ': 'Світлодарська', 'Селидове': 'Селидового', 'Сіверськ': 'Сіверська', "Слов'янськ": "Слов'янська", 'Соледар': 'Соледара', 'Торецьк': 'Торецька', 'Українськ': 'Українська', 'Часів Яр': 'Часового Яру', 'Сєвєродонецьк': 'Сєвєродонецька', 'Лисичанськ': 'Лисичанська', 'Рубіжне': 'Рубіжного', 'Кремінна': 'Кремінної', 'Попасна': 'Попасної', 'Привілля': 'Привілля', 'Гірське': 'Гірського', 'Золоте': 'Золотого'}

# Показувати порівняння з футбольними полями (105×68 м)
SHOW_FOOTBALL_FIELDS = os.getenv("SHOW_FOOTBALL_FIELDS", "0") == "1"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE_DIR = DATA / "state"
REF_DIR = DATA / "reference"
CHANGES_DIR = DATA / "changes"
HISTORY_CSV = DATA / "history.csv"
def _cover(name: str) -> Path:
    """Картинку шукаємо в папці assets/, а якщо там немає – у корені репозиторію."""
    p = ROOT / "assets" / name
    return p if p.exists() else ROOT / name


COVER_IMAGE = _cover("cover.jpg")          # щоденний пост
COVER_WEEK = _cover("cover_week.jpg")      # тижневий підсумок
COVER_MONTH = _cover("cover_month.jpg")    # місячний підсумок

# Тижневий підсумок – у цей день тижня (0 = понеділок), місячний – 1-го числа
WEEKLY_WEEKDAY = 0

# Тестовий запуск: нічого не публікує і нічого не зберігає
DRY_RUN = os.getenv("DRY_RUN") == "1"

MONTHS_UA = ["січень", "лютий", "березень", "квітень", "травень", "червень",
             "липень", "серпень", "вересень", "жовтень", "листопад", "грудень"]

SOURCE_LINE = 'За даними DeepStateMap.'   # без «.live» і без посилання – щоб читачі лишались у нас (рішення Юлі 17.11, увімкнено 22.11)
# Заклик підписатися на сам майданчик (рішення Юлі 15.11.2026; у Telegram не додаємо – там уже підписники, пост довгий): мета – і інформувати, і нарощувати
# аудиторію АКМЦ; підписуємо на АКМЦ, а не «на DeepState». Щоденні дописи – «підпишіться тут»,
# тижневі/місячні – як і раніше, посилання на Telegram (там повний перелік населених пунктів).
# Заклики по майданчиках (погоджено Юлею 22–23.11.2026; див. накопичувач правок, розділ «Заклики»):
# де нас бачать переважно не підписники (Threads, Reels, TikTok, YouTube) – підписатися;
# Facebook – у Telegram (посилання в першому коментарі); Telegram – без заклику.
SUB_CTA = "Підписуйтеся, аби щоранку бачити зміни на мапі Донеччини."   # Threads – усі дописи
SUB_CTA_WEEKLY = "Підписуйтеся, аби щотижня бачити зміни на мапі Донеччини."   # Reels/IG, TikTok, YouTube
SUB_CTA_YT = SUB_CTA_TT = SUB_CTA_IG = SUB_CTA_WEEKLY
FB_WHITE_CTA = "Щоранку о 8:00 – подробиці в нашому Telegram. Посилання в першому коментарі 👇"
FB_YELLOW_CTA = "Більше деталей – щоранку о 8:00 у нашому Telegram. Посилання в першому коментарі 👇"
NAMES_MAX = 8   # ліміт «4 назви» знято 24.11.2026 (текст = мапа: кожна ділянка підписана; за листопад – до 6 назв)

def editor_note(kind: str = "week") -> str:
    """Повна примітка для підсумків тижня/місяця (текст погоджено Юлею 24.11.2026)."""
    return (SOURCE_LINE + "\n\n<blockquote expandable><i>Примітка: ці цифри автоматично рахує наша програма. "
            "Вона порівнює мапу DeepState з попередньою версією і показує, що змінилось у позначеннях. "
            "Це зміни на мапі, а не точна хронологія подій на фронті. "
            "DeepState не є офіційним джерелом інформації.</i></blockquote>")

HEADERS = {"User-Agent": "ACMC-DeepState-tracker/1.0 (+https://acmc.ua)"}

GEOD = Geod(ellps="WGS84")
# UTM 37N – коректні відстані в метрах для Донеччини й Луганщини
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
    """Межі областей OCHA. Кешуються в data/reference – вони не змінюються."""
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
        while True:  # сервіс віддає дані сторінками – забираємо всі
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
    """У журнал – які шари є на мапі (щоб помітити, якщо DeepState змінить формат)."""
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
        raise RuntimeError(f"На мапі DeepState не знайдено шару «{label}» – схоже, змінився формат даних")
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


def neighbors_by_oblast(deepstate: dict) -> dict:
    """Окупація й сіра зона в сусідніх областях (Харківська, Дніпропетровська, Запорізька) – лише як тло мап,
    щоб сусіди мали ті самі кольори, що й Донеччина (Юля 24.11.2026). Спрощено до ~100 м – для тла досить."""
    try:
        nb = {f["properties"]["adm1_pcode"]: shape(f["geometry"]).buffer(0)
              for f in load_json(REF_DIR / "neighbors.geojson")["features"]}
        out: dict = {}
        for kind, markers in (("occupied", OCCUPIED_MARKERS), ("unknown", UNKNOWN_MARKERS)):
            layer = []
            for f in deepstate["map"]["features"]:
                name = (f.get("properties") or {}).get("name") or ""
                if f.get("geometry") and any(m in name for m in markers):
                    try:
                        g = clean_polygonal(shape(f["geometry"]))
                    except Exception:  # noqa: BLE001
                        g = None
                    if g is not None:
                        layer.append(g)
            out[kind] = {}
            for pc, o in nb.items():
                parts = [g.intersection(o) for g in layer if g.intersects(o)]
                merged = unary_union(parts).simplify(0.001) if parts else None
                out[kind][pc] = geom_to_json(merged) if merged is not None and not merged.is_empty else None
        return out
    except Exception as e:  # noqa: BLE001
        log(f"Сусідні області для тла мап: {e}")
        return {}


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
        if not (since_id < mid <= until_id):
            continue
        if it.get("status") is False:
            log(f"Повідомлення {datetime.fromtimestamp(mid, KYIV):%d.%m %H:%M} приховане (status=false): "
                f"{clean_description(it.get('description') or '')[:120]}")
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
        city = [c for c in ranked if is_city(c[1])][:1]
        picked = city + [c for c in ranked if c not in city]
        for d, name in picked[:PLACES_PER_CHANGE]:
            found[name] = min(d, found.get(name, d))
    return [n for n, _ in sorted(found.items(), key=lambda kv: kv[1])][:PLACES_MAX_IN_TEXT]


def compare(current, previous, places, oblast_name, unknown=None):
    """unknown – сіра зона зараз: якщо з ділянки зняли червону позначку, а вона стала сірою,
    це не деокупація, а «перейшли в сіру зону» – рахуємо окремо."""
    def diff(a, b):
        if a is None:
            return []
        if b is None:
            return drop_slivers(a)
        return drop_slivers(clean_polygonal(a.difference(b)))

    newly = diff(current, previous)
    removed = diff(previous, current)
    to_grey: list = []
    deocc = removed
    if removed and unknown is not None and not unknown.is_empty:
        rem = unary_union(removed)
        to_grey = drop_slivers(clean_polygonal(rem.intersection(unknown)))
        deocc = drop_slivers(clean_polygonal(rem.difference(unknown)))
    return {
        "newly_km2": round(sum(area_km2(p) for p in newly), 2),
        "deocc_km2": round(sum(area_km2(p) for p in deocc), 2),
        "grey_km2": round(sum(area_km2(p) for p in to_grey), 2),
        "newly_polys": newly,
        "deocc_polys": deocc,
        "grey_polys": to_grey,
        "newly_places": nearest_places(newly, places, oblast_name),
        "deocc_places": nearest_places(deocc, places, oblast_name),
        "grey_places": nearest_places(to_grey, places, oblast_name),
    }


# ============================ TEXT ============================


def _short_date(d: str) -> str:
    dt = datetime.strptime(d, "%Y-%m-%d")
    return f"{dt.day} {MONTHS_GEN[dt.month - 1]}"


def _short_range(d1: str, d2: str) -> str:
    a, b = datetime.strptime(d1, "%Y-%m-%d"), datetime.strptime(d2, "%Y-%m-%d")
    if a == b:
        return _short_date(d1)
    if a.month == b.month:
        return f"{a.day}–{b.day} {MONTHS_GEN[b.month - 1]}"
    return f"{_short_date(d1)} – {_short_date(d2)}"


def _net_period(rows: list[dict], k: str) -> float:
    """Чиста зміна позначених окупованими за період (км²). Ділянки, що з червоних стали сірими,
    у неї не входять – це не деокупація."""
    first, last = rows[0], rows[-1]
    before = (_f(first[f"{k}_occupied_km2"]) - _f(first[f"{k}_newly_occupied_km2"])
              + _f(first[f"{k}_deoccupied_km2"]) + _f(first.get(f"{k}_to_grey_km2")))
    return round(_f(last[f"{k}_occupied_km2"]) - before + sum(_f(r.get(f"{k}_to_grey_km2")) for r in rows), 2)


def join_places(places: list) -> str:
    """«Русин Яр, Іллінівка (Краматорський р-н), Часів Яр (Бахмутський р-н)» –
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


def cities_near(polys: list, places: list, oblast_name: str) -> list[str]:
    """Великі міста, біля яких (до PLACES_RADIUS_KM) з'явилися нові ділянки."""
    found: dict[str, float] = {}
    for poly in polys:
        poly_utm = to_utm(poly)
        for pl in places:
            if pl["oblast"] != oblast_name or not is_city(pl["name"], pl.get("district")):
                continue
            d = poly_utm.distance(Point(pl["x"], pl["y"])) / 1000
            if d <= PLACES_RADIUS_KM:
                found[pl["name"]] = min(d, found.get(pl["name"], d))
    return [n for n, _ in sorted(found.items(), key=lambda kv: kv[1])]


def penetration_sentence(g: dict | None) -> str:
    """Район проникнення згадуємо лише біля великих міст – решта перевантажує текст."""
    cities = (g or {}).get("cities") or []
    if not cities:
        return ""
    names = [CITY_GENITIVE.get(c, c) for c in cities[:3]]
    where = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " та " + names[-1]
    return (f"Біля {where} на мапі DeepState з'явився новий район проникнення "
            f"(територія, що потребує уточнення; окупованою її ще не позначено).")


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
        s += f", нові сірі ділянки – поблизу: {join_places(g['places'])}"
    return s + "."


def esc(text: str) -> str:
    return html.escape(text, quote=False)


SHORT_NOTE = (SOURCE_LINE + "\n\n<blockquote><i>Примітка: це автоматичне зведення змін на мапі DeepState, "
              "а не точна хронологія подій на фронті. "
              "DeepState не є офіційним джерелом інформації.</i></blockquote>")   # 24.11.2026 (Юля)


def build_text(period: str, results: dict, grey: dict | None = None, messages: list | None = None,
               names: dict | None = None, mentioned: dict | None = None) -> str:
    """Щоденне зведення для Telegram. Стиль як у Threads/Facebook: дата в заголовку, коротко,
    без канцеляриту; назви – ті самі, що на мапі; рядок про повідомлення DeepState – лише якщо вони є."""
    names = names or {}
    mentioned = mentioned or {}
    head = f"<b>DeepState: зміни за добу, {period}</b>"
    # 18.11.2026 (Юля): повідомлення DeepState у дописах не публікуємо – лише наша мапа
    quiet = (all(not results[k]["newly_km2"] and not results[k]["deocc_km2"] and not results[k]["grey_km2"]
                 for k in OBLASTS)
             and not any(((grey or {}).get(k) or {}).get("cities") for k in OBLASTS))
    if quiet:  # день без змін – коротка версія
        return "\n".join([head, "", "Донеччина й Луганщина: за добу на мапі DeepState без змін.", "", SHORT_NOTE])
    lines = [head, ""]
    for key, cfg in OBLASTS.items():
        r = results[key]
        parts = []
        nm = names.get(key) or []
        if r["newly_km2"] > 0:
            near = tg_near(pick(nm, "occ"), r["newly_places"])
            parts.append(f"{fmt_km2(r['newly_km2'])} км² позначено окупованими" + (f" – {near}" if near else "") + ".")
        if r["deocc_km2"] > 0:
            dn = pick(nm, "deocc") if (isinstance(nm, Names) and nm.both) or r["newly_km2"] <= 0 else []
            near = tg_near(dn, r["deocc_places"])
            parts.append(f"{fmt_km2(r['deocc_km2'])} км² позначено деокупованими" + (f" – {near}" if near else "") + ".")
        if r.get("grey_km2", 0) > 0:   # червоне стало сірим – не деокупація
            g = (f"Ще {fmt_km2(r['grey_km2'])} км², раніше позначених окупованими, тепер позначено як район "
                 f"проникнення (сіра зона) – це не деокупація.")
            if r["grey_places"]:
                g += f" Поблизу: {join_places(r['grey_places'])}."
            parts.append(g)
        gs = (grey_sentence((grey or {}).get(key)) if SHOW_GREY_IN_TEXT
              else penetration_sentence((grey or {}).get(key)))
        if not parts:
            parts.append("без змін.")
        if gs:
            parts.append(gs)
        if r["newly_km2"] > 0 and r["deocc_km2"] > 0:   # обидва напрямки – кожен окремим рядком
            lines.append(f"<b>{cfg['short']}:</b>\n" + "\n".join("– " + p.rstrip(".") for p in parts[:2])
                         + ("\n" + " ".join(parts[2:]) if parts[2:] else ""))
        else:
            lines.append(f"<b>{cfg['short']}:</b> " + " ".join(parts))
    lines.append("")
    lines.append(SHORT_NOTE)
    return "\n".join(lines)


# ============================ TELEGRAM ============================


# Гілки службової групи «Тестування ботів АКМЦ»
THREAD_ERRORS = os.getenv("THREAD_ERRORS", "3")
THREAD_TIKTOK = os.getenv("THREAD_TIKTOK", "4")
THREAD_TESTS = os.getenv("THREAD_TESTS", "407")   # гілка «DeepState» (з 25.11.2026; раніше «Тести» = 6)


def map_problems(mp) -> list[str]:
    """Перевірка «текст = мапа» (рішення Юлі 18.11.2026): кожна назва з тексту має бути підписана на мапі.
    Повертає список проблем (порожній – усе гаразд); дрібниці (напр. ділянка під знаком) – лише в журнал."""
    if not mp:
        return []
    flags = MAP_FLAGS.get(mp, []) + [f for x in EXTRA_MAPS.get(mp, []) for f in MAP_FLAGS.get(x, [])]
    other = [f for f in flags if "не підписано" not in f]
    if other:
        alert("Мапа: " + "; ".join(other))
    return [f for f in flags if "не підписано" in f]


def hold_for_review(what: str, text: str, image: Path, problems: list[str], extra_images=None) -> None:
    """Текст і мапа не збігаються – допис не публікуємо, а надсилаємо в гілку «DeepState» на ручну перевірку."""
    msg = (f"⚠️ {what}: назви в тексті не збігаються з мапою – допис НЕ опубліковано.\n• " + "\n• ".join(problems)
           + "\nПеревірте прев'ю нижче. Якщо мапа в порядку, опублікувати можна так: GitHub → deepstate-tracker → "
             "Actions → «DeepState — моніторинг змін» → Run workflow → позначте force і skip_map_check → Run workflow "
             "(через 3–5 хв допис вийде скрізь). Або напишіть Claude.")
    log(msg)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if DRY_RUN or not token or not chat_id:
        return
    try:
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_TESTS, "text": msg})
        publish(text, image, silent=True, extra_images=extra_images, chat_id=chat_id, thread_id=THREAD_TESTS)
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося надіслати прев'ю в гілку «DeepState»: {e}")
    alert(msg)


def tg(method: str, token: str, data: dict, files: dict | None = None) -> dict:
    for attempt in (1, 2):   # 24.11.2026: один повтор, якщо з'єднання обірвалося (Connection reset by peer)
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=60)
            break
        except requests.exceptions.ConnectionError:
            if attempt == 2:
                raise
            time.sleep(5)
            for fh in (files or {}).values():
                if hasattr(fh, "seek"):
                    fh.seek(0)
    js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not js.get("ok"):
        raise RuntimeError(f"Telegram {method}: {r.status_code} {str(js or r.text)[:300]}")
    return js


def _tg_image(mp) -> Path | None:
    """Мапа для Telegram: КВАДРАТНА (16.11.2026 – 4:5 Telegram обрізав у стрічці: заголовок і легенда
    зникали). 4:5 лишається для Threads/Instagram. Немає мапи – None."""
    if not mp:
        return None
    mp = Path(mp)
    return mp if mp.exists() else None


TG_IMAGES_BRANCH = "tg-images"   # службова гілка з картинками для прев'ю над текстом (лише останні TG_IMAGES_KEEP_DAYS днів)
TG_IMAGES_KEEP_DAYS = 30


def _host_image(img: Path) -> str | None:
    """Кладе картинку в службову гілку й повертає публічну адресу (для прев'ю над текстом у Telegram) —
    так само, як у Меморіал-боті. Гілка щоразу перезаписується одним комітом без історії, тож репозиторій
    не росте. None — якщо не вдалося (тоді — картинка й текст окремо, як раніше)."""
    import secrets
    import subprocess

    def git(*a, inp=None):
        r = subprocess.run(["git", *a], input=inp, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"git {a[0]}: {r.stderr.strip()[:300]}")
        return r.stdout.strip()
    try:
        now = datetime.now(KYIV)
        name = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.jpg"
        blob = git("hash-object", "-w", str(img))
        entries = []
        if subprocess.run(["git", "fetch", "-q", "origin", TG_IMAGES_BRANCH], capture_output=True).returncode == 0:
            cutoff = (now - timedelta(days=TG_IMAGES_KEEP_DAYS)).strftime("%Y%m%d")
            entries = [ln for ln in git("ls-tree", "FETCH_HEAD").splitlines() if ln.split("\t", 1)[-1][:8] >= cutoff]
        entries.append(f"100644 blob {blob}\t{name}")
        tree = git("mktree", inp="\n".join(entries) + "\n")
        commit = git("-c", "user.name=deepstate-bot", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                     "commit-tree", tree, "-m", f"Картинки для прев'ю в Telegram (останні {TG_IMAGES_KEEP_DAYS} днів)")
        git("push", "-q", "-f", "origin", f"{commit}:refs/heads/{TG_IMAGES_BRANCH}")
        repo = os.getenv("GITHUB_REPOSITORY", "Anti-Crisis-Media-Center/deepstate-tracker")
        time.sleep(3)
        return f"https://raw.githubusercontent.com/{repo}/{TG_IMAGES_BRANCH}/{name}"
    except Exception as e:  # noqa: BLE001
        log(f"Telegram: картинку для прев'ю не викладено — {e}")
        return None


def publish(text: str, image: Path = COVER_IMAGE, silent: bool = True, extra_images: list | None = None,
            chat_id: str | None = None, thread_id: str | None = None) -> None:
    """Telegram — ОДНИМ повідомленням (рішення Юлі 23.11.2026; впроваджено 24.11):
    • текст до 1 024 знаків — фото з підписом;
    • довший — текст із великою картинкою-прев'ю НАД текстом (до 4 096 знаків), як у «Хвилині мовчання»;
    • дві мапи (extra_images) — альбом із двох картинок, текст — підписом до альбому.
    Якщо прев'ю не вийшло — як раніше: картинка й текст окремо."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if DRY_RUN or not token or not chat_id:
        log("DRY_RUN або немає секретів Telegram – пост не надсилається. Текст:\n" + text)
        return
    base = {"chat_id": chat_id, "parse_mode": "HTML", **({"disable_notification": "true"} if silent else {}),
            **({"message_thread_id": thread_id} if thread_id else {})}
    extra = [Path(x) for x in (extra_images or []) if x and Path(x).exists()]
    if extra and image.exists():
        imgs = [image, *extra]
        files = {f"f{i}": p.open("rb") for i, p in enumerate(imgs)}
        try:
            cap = len(text) <= 1024
            media = json.dumps([{"type": "photo", "media": f"attach://f{i}",
                                 **({"caption": text, "parse_mode": "HTML"} if cap and i == 0 else {})}
                                for i in range(len(imgs))])
            tg("sendMediaGroup", token, {k: v for k, v in base.items() if k != "parse_mode"} | {"media": media}, files)
        finally:
            for fh in files.values():
                fh.close()
        if not cap:
            tg("sendMessage", token, {**base, "text": text, "disable_web_page_preview": "true"})
        log(f"Пост надіслано в Telegram (альбом із {len(imgs)} мап).")
        return
    if image.exists() and len(text) <= 1024:  # ліміт підпису до фото в Telegram
        with image.open("rb") as fh:
            tg("sendPhoto", token, {**base, "caption": text}, {"photo": fh})
        log(f"Пост надіслано в Telegram ({image.name}).")
        return
    url = _host_image(image) if image.exists() else None
    if url:
        try:
            # невидиме посилання на картинку — першим у тексті: інакше при ручному редагуванні допису
            # прев'ю зникає (досвід Меморіал-бота, 20.11)
            tg("sendMessage", token, {**base, "text": f'<a href="{url}">\u2060</a>' + text,
                                      "link_preview_options": json.dumps({"url": url, "prefer_large_media": True,
                                                                          "show_above_text": True})})
            log(f"Пост надіслано в Telegram одним повідомленням ({image.name} над текстом).")
            return
        except Exception as e:  # noqa: BLE001
            log(f"Telegram: прев'ю над текстом не вийшло ({e}) – картинка й текст окремо")
    if image.exists():
        with image.open("rb") as fh:
            tg("sendPhoto", token, {k: v for k, v in base.items() if k != "parse_mode"}, {"photo": fh})
    tg("sendMessage", token, {**base, "text": text, "disable_web_page_preview": "true"})
    log(f"Пост надіслано в Telegram ({image.name if image.exists() else 'без картинки'}, текст окремо).")


# ============================ FACEBOOK-ТИЗЕР ============================
# Короткий текст для ручної публікації у Facebook. Бот надсилає його у службовий
# чат (TELEGRAM_ALERT_CHAT_ID): Юля копіює текст і обкладинку у Facebook,
# а друге повідомлення – у перший коментар. Повна версія лишається в Telegram.

# посилання для білої сторінки: нова назва секрету FB_WHITE_TELEGRAM_LINK (16.11.2026), стара FB_TELEGRAM_LINK – запасна,
# поки Юля не перейменує; для жовтої – FB_YELLOW_TELEGRAM_LINK («FB DeepState жовта»)
FB_TELEGRAM_LINK = (os.getenv("FB_WHITE_TELEGRAM_LINK", "").strip() or os.getenv("FB_TELEGRAM_LINK", "").strip()
                    or "https://t.me/acmc_ua")
FB_YELLOW_TELEGRAM_LINK = os.getenv("FB_YELLOW_TELEGRAM_LINK", "").strip() or FB_TELEGRAM_LINK
# окреме посилання-запрошення для YouTube (створене з «АКМЦ Робочий»), щоб рахувати переходи з YouTube (11.11)
YOUTUBE_TELEGRAM_LINK = os.getenv("YOUTUBE_TELEGRAM_LINK", "https://t.me/acmc_ua").strip() or "https://t.me/acmc_ua"
FB_COMMENT = f"Telegram-канал Антикризового медіа-центру: {FB_TELEGRAM_LINK}"
FB_YELLOW_COMMENT = f"Telegram-канал Антикризового медіа-центру: {FB_YELLOW_TELEGRAM_LINK}"   # коротко: картка з логотипом з’являється сама (16.11.2026)


def near_phrase(names: list[str], villages: list[str]) -> str:
    """Ті самі назви, що на мапі: «поблизу: Добропілля, Білицьке». Дужки з селами прибрано 12.11.2026."""
    if not names:
        return f"поблизу: {join_places(villages[:3])}" if villages else ""
    # завжди «поблизу:» і називний відмінок (рішення Юлі 10.11): однаково для міст і сіл, без
    # помилок у відмінках сіл; так само, як у заголовку мапи («Поблизу: Білицьке · Костянтинівка»)
    # без дужок із селами (рішення Юлі 12.11.2026: читачеві незрозуміло, що означають дужки) –
    # лише ті самі назви, що на мапі; села потрібні тільки тоді, коли головних назв немає
    return "поблизу: " + ", ".join(names)


def tg_near_summary(names: list[str], villages: list[str]) -> str:
    """Підсумки в Telegram: назви як на мапі, без громад (16.11.2026: громади лише в добових)."""
    base = near_phrase(names, [])
    return base or tg_near([], villages)


def tg_near(names: list[str], villages: list[str]) -> str:
    """Для Telegram: ті самі назви, що на мапі й у Threads, а села – з районом (Telegram читають уважно):
    «поблизу: Добропілля, Білицьке». Дужки з селами прибрано 12.11.2026."""
    if names:   # 16.11.2026: як у сторіз – села з громадою («Вільне – Шахівська громада»), міста без
        return story_near(list(names))
    vs = list(dict.fromkeys(villages))[:4]
    if not vs:
        return ""
    vtxt = re.sub(r" \((.*?)\)", r" – \1", join_places(vs))
    return "поблизу: " + vtxt


def _teaser_line(title: str, newly: float, deocc: float, newly_pl: list, deocc_pl: list,
                 grey: float = 0.0, names: list[str] | None = None) -> str | None:
    parts = []
    split = isinstance(names, Names) and names.both
    if newly > 0:
        s = f"{fmt_km2(newly)} км² позначено окупованими"
        on = pick(names, "occ")
        if on or newly_pl:
            s += " – " + near_phrase(on, newly_pl) if on else f" – поблизу: {join_places(newly_pl[:3])}"
        parts.append(s)
    if deocc > 0:
        s = f"{fmt_km2(deocc)} км² – деокупованими"
        dn = pick(names, "deocc")
        if split:
            s += " – " + near_phrase(dn, deocc_pl)
        elif deocc_pl and not (names and newly > 0):
            s += " – " + near_phrase(dn, deocc_pl) if dn else f" – поблизу: {join_places(deocc_pl[:3])}"
        parts.append(s)
    if newly > 0 and deocc > 0:   # обидва напрямки – кожен окремим рядком (рішення 10.11)
        def ln(v, w, nm_, pl):
            near = near_phrase(nm_, pl) if nm_ else (f"поблизу: {join_places(pl[:3])}" if pl else "")
            return f"– {fmt_km2(v)} км² позначено {w}" + (f" – {near}" if near else "")
        rows_ = [ln(newly, "окупованими", pick(names, "occ"), newly_pl),
                 ln(deocc, "деокупованими", pick(names, "deocc") if split else [], deocc_pl)]
        if grey > 0:
            rows_.append(f"– {fmt_km2(grey)} км² – з окупованих на сіру зону")
        return f"{title}:\n" + "\n".join(rows_)
    if grey > 0:
        parts.append(f"{fmt_km2(grey)} км² – з окупованих на сіру зону")
    return f"{title}: " + "; ".join(parts) + "." if parts else None


def build_fb_teaser(day: str, results: dict, names: dict | None = None, cta: str | None = None) -> str:
    """Короткий текст доби: Threads (cta=SUB_CTA) і жовта сторінка FB (cta=FB_YELLOW_CTA).
    Рядка «За даними DeepStateMap.» немає (24.11.2026): DeepState – у заголовку, тексті й на мапі."""
    lines = [f"DeepState: зміни за добу, {day}", ""]
    names = names or {}
    found = [_teaser_line(cfg["short"], results[k]["newly_km2"], results[k]["deocc_km2"],
                          results[k]["newly_places"], results[k]["deocc_places"], results[k].get("grey_km2", 0),
                          names.get(k))
             or f"{cfg['short']}: без змін." for k, cfg in OBLASTS.items()]
    if all(f.endswith("без змін.") for f in found):   # день без змін – одне речення (рішення Юлі 17.11)
        found = ["Донеччина й Луганщина: за добу на мапі DeepState без змін."]
    lines += found
    lines += ["", cta or SUB_CTA]
    return "\n".join(lines)


def build_fb_summary_teaser(kind: str, rows: list[dict], label: str | None = None) -> str:
    title = "DeepState: підсумки тижня" if kind == "week" else f"DeepState: підсумки місяця – {label}"
    word = "тиждень" if kind == "week" else "місяць"
    lines = [title, ""]
    for key, cfg in OBLASTS.items():
        near = ""
        occ, free = _period_totals(rows, key)
        # 16.11.2026: текст має збігатися з відео/обкладинкою (було 19,25 у тексті й 19,23 у відео) –
        # беремо ту саму цифру, що на мапі й у відео
        net = round(occ - free, 2)
        if occ >= 0.01 and free >= 0.01:   # обидва напрямки – дві цифри, кожна зі своїми назвами
            nm = place_names([r["date"] for r in rows], key)
            no, nd = near_phrase(pick(nm, "occ"), []), near_phrase(pick(nm, "deocc"), [])
            lines += [f"{cfg['short']} за {word}:",
                      f"– {fmt_km2(occ)} км² позначено окупованими" + (f" – {no}" if no else ""),
                      f"– {fmt_km2(free)} км² позначено деокупованими" + (f" – {nd}" if nd else "")]
            continue
        if abs(net) >= 0.01:
            vil = [x for r in rows for x in (r.get(f"{key}_newly_places") or "").split("; ") if x]
            near = near_phrase(place_names([r["date"] for r in rows], key), vil)
            near = (" – " + near) if near else ""   # тире перед «поблизу:», як у щоденних (16.11.2026)
        if net >= 0.01:
            lines.append(f"{cfg['short']}: за {word} {fmt_km2(net)} км² позначено окупованими{near}.")
        elif net <= -0.01:
            lines.append(f"{cfg['short']}: за {word} {fmt_km2(-net)} км² позначено деокупованими{near}.")
        else:
            lines.append(f"{cfg['short']}: за {word} без змін.")
    if all(l.endswith("без змін.") for l in lines[2:]):
        lines = [title, "", f"Донеччина й Луганщина: за {word} на мапі DeepState без змін."]
    # один заклик – у Telegram (23.11.2026, варіант Б); «повний перелік» прибрано: перелік тепер у самому дописі
    lines += ["", FB_WHITE_CTA]
    return "\n".join(lines)


def _names_join(names: list[str]) -> str:
    return ", ".join(names)


def build_yellow_summary(kind: str, rows: list[dict], label: str | None = None) -> str:
    """Жовта сторінка FB, підсумок тижня/місяця – окремий текст (~50% відмінності від білої; рішення Юлі 22–23.11):
    «Тиждень на мапі DeepState / 17,94 км² – стільки DeepState позначив окупованими на Донеччині. Найближчі
    населені пункти: … / На Луганщині змін немає. / заклик у Telegram»."""
    title = "Тиждень на мапі DeepState" if kind == "week" else "Місяць на мапі DeepState"
    word = "тиждень" if kind == "week" else "місяць"
    where = {"donetsk": "на Донеччині", "luhansk": "на Луганщині"}
    dates = [r["date"] for r in rows]
    paras, quiet = [], []
    for key in OBLASTS:
        occ, free = _period_totals(rows, key)
        nm = place_names(dates, key)
        if occ >= 0.01 and free >= 0.01:
            s_ = (f"{where[key][0].upper()}{where[key][1:]} DeepState позначив окупованими {fmt_km2(occ)} км², "
                  f"деокупованими – {fmt_km2(free)} км².")
            no, nd = pick(nm, "occ"), pick(nm, "deocc")
            if no or nd:
                s_ += " Найближчі населені пункти: " + "; ".join(
                    x for x in ([_names_join(no) + " (окупація)"] if no else []) +
                    ([_names_join(nd) + " (деокупація)"] if nd else [])) + "."
        elif occ >= 0.01 or free >= 0.01:
            v, what = (occ, "окупованими") if occ >= 0.01 else (free, "деокупованими")
            s_ = f"{fmt_km2(v)} км² – стільки DeepState позначив {what} {where[key]}."
            if nm:
                s_ += (" Найближчий населений пункт: " if len(nm) == 1 else " Найближчі населені пункти: ") \
                      + _names_join(list(nm)) + "."
        else:
            quiet.append(key)
            continue
        paras.append(s_)
    if len(quiet) == len(OBLASTS):
        paras = [f"Донеччина й Луганщина: за {word} на мапі DeepState без змін."]
    else:
        paras += [f"{where[k][0].upper()}{where[k][1:]} змін немає." for k in quiet]
    return "\n\n".join([title, *paras, FB_YELLOW_CTA])


FB_GRAPH = "https://graph.facebook.com/" + (os.getenv("FB_GRAPH_VERSION", "v24.0").strip() or "v24.0")


_FB_PAGE_CACHE: dict = {}


FB_YELLOW_PAGE_ID = os.getenv("FB_YELLOW_PAGE_ID", "414302258746592").strip()   # жовта «Антикризовий медіа-центр»
FB_YELLOW = os.getenv("FB_YELLOW", "1") == "1"                     # жовта сторінка: щоденні мапи й підсумки
FB_YELLOW_FROM = os.getenv("FB_YELLOW_FROM", "2026-11-25")          # перший допис на жовтій – 25.11.2026
FB_YELLOW_QUIET = os.getenv("FB_YELLOW_QUIET", "0") == "1"          # дні без змін на жовтій – поки ні (рішення 18.11)


def fb_page_credentials(page_id: str | None = None) -> tuple[str, str]:
    """(page_id, page_token). Токен може бути токеном сторінки або токеном системного
    користувача бізнес-портфоліо – тоді токен сторінки отримуємо за її ID.
    page_id=None – біла сторінка (FACEBOOK_PAGE_ID); інакше – вказана (напр. жовта)."""
    ck = page_id or "v"
    if ck in _FB_PAGE_CACHE:
        return _FB_PAGE_CACHE[ck]
    page_id = page_id or os.getenv("FACEBOOK_PAGE_ID", "").strip()
    token = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
    result = (page_id, token)
    if page_id and token and page_id != "me":
        try:
            js = requests.get(f"{FB_GRAPH}/{page_id}", params={"fields": "access_token,name",
                              "access_token": token}, timeout=30).json()
            if js.get("access_token"):
                result = (page_id, js["access_token"])
                log(f"Facebook: токен сторінки «{js.get('name')}» отримано.")
        except Exception as e:  # noqa: BLE001
            log(f"Facebook: не вдалося отримати токен сторінки: {e}")
    _FB_PAGE_CACHE[ck] = result
    return result


FB_DAILY_POSTS = os.getenv("FB_DAILY_POSTS", "0") == "1"   # щоденні дописи в стрічку білої сторінки
FB_POSTS = os.getenv("FB_POSTS", "1") == "1"   # 0 – на сторінці лише сторіз, без дописів у стрічку


def post_facebook(text: str, image: Path, page_id: str | None = None, comment: str | None = None,
                  extra_images: list | None = None, label: str = "Facebook") -> bool:
    """Публікує допис на сторінці Facebook і додає перший коментар із посиланням на Telegram.
    page_id=None – біла сторінка; extra_images – друга мапа (тоді допис із кількома фото).
    Повертає True, якщо Facebook налаштований і пост вийшов."""
    page_id, page_token = fb_page_credentials(page_id)
    comment = comment or FB_COMMENT
    if not page_id or not page_token:
        return False
    if not FB_POSTS:
        log(f"{label}: дописи вимкнено (FB_POSTS=0), лише сторіз.")
        return True
    if DRY_RUN:
        log(f"DRY_RUN – у {label} не публікується. Текст:\n" + text + "\n💬 " + comment)
        return True
    try:
        extra = [Path(x) for x in (extra_images or []) if x and Path(x).exists()]
        if extra and image.exists():   # кілька фото в одному дописі (дві мапи – рішення Юлі 18 і 22.11)
            ids = []
            for img in [image, *extra]:
                with img.open("rb") as fh:
                    up = requests.post(f"{FB_GRAPH}/{page_id}/photos",
                                       data={"published": "false", "access_token": page_token},
                                       files={"source": fh}, timeout=120).json()
                if "error" in up:
                    raise RuntimeError(up["error"].get("message", str(up["error"]))[:300])
                ids.append(up["id"])
            data = {"message": text, "access_token": page_token}
            for i, fid in enumerate(ids):
                data[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
            r = requests.post(f"{FB_GRAPH}/{page_id}/feed", data=data, timeout=60)
        elif image.exists():
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
        log(f"Пост у {label} опубліковано: {post_id}")
        c = requests.post(f"{FB_GRAPH}/{post_id}/comments",
                          data={"message": comment, "access_token": page_token}, timeout=60).json()
        if "error" in c:
            log(f"Коментар у {label} не додано: {c['error'].get('message')}")
            alert(f"Пост у {label} вийшов, але перший коментар не додано: {c['error'].get('message')}")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"{label}: помилка – {e}")
        alert(f"Не вдалося опублікувати у {label}: {e}. Нижче – тизер для ручної публікації.")
        return False  # тоді send_fb_teaser надішле готовий тизер у службовий чат


def post_fb_yellow(text: str, image: Path, day: str, extra_images: list | None = None) -> None:
    """Жовта сторінка FB (з 25.11.2026): мапа + короткий текст + перший коментар-посилання на Telegram
    («FB DeepState жовта»). Збій – у «Помилки» з текстом для ручної публікації."""
    if not FB_YELLOW or day < FB_YELLOW_FROM:
        log(f"Жовта FB: ще не ввімкнено (з {FB_YELLOW_FROM}).")
        return
    if not post_facebook(text, image, FB_YELLOW_PAGE_ID, FB_YELLOW_COMMENT, extra_images, label="жовта FB"):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
        if token and chat_id and not DRY_RUN:
            tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_ERRORS,
                                      "text": "📒 Жовта FB – текст для ручної публікації:\n\n" + text
                                              + "\n\n💬 Перший коментар:\n" + FB_YELLOW_COMMENT,
                                      "disable_web_page_preview": "true"})


def send_fb_teaser(text: str, image: Path) -> None:
    """Тизер для Facebook: якщо є секрети Facebook – публікує автоматично,
    інакше надсилає готовий текст у службовий чат. Помилка тут не зупиняє бота."""
    if post_facebook(text, image):
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if DRY_RUN or not token or not chat_id:
        log("Тизер для Facebook (не надсилається):\n" + text)
        return
    try:
        head = {"chat_id": chat_id, "message_thread_id": THREAD_ERRORS, "caption": "📘 Для Facebook – обкладинка, нижче текст посту і перший коментар"}
        if image.exists():
            with image.open("rb") as fh:
                tg("sendPhoto", token, head, {"photo": fh})
        else:
            tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_ERRORS, "text": head["caption"]})
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_ERRORS, "text": text, "disable_web_page_preview": "true"})
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_ERRORS, "text": "💬 Перший коментар:"})
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_ERRORS, "text": FB_COMMENT, "disable_web_page_preview": "true"})
        log("Тизер для Facebook надіслано в службовий чат.")
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося надіслати тизер для Facebook: {e}")


# ============================ FACEBOOK-СТОРІЗ ============================

MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
STORY_LABELS = {"donetsk": "ДОНЕЧЧИНА", "luhansk": "ЛУГАНЩИНА"}
IG_DAILY_POSTS = os.getenv("IG_DAILY_POSTS", "0") == "1"   # щоденні дописи в стрічку Instagram вимкнено
# (9.11.2026): щоб сітка профілю не складалася з однакових мап. Щодня – лише сторіз; у стрічку – тиждень і місяць.
FB_STORIES = os.getenv("FB_STORIES", "1") != "0"  # сторіз у Facebook увімкнено (4.11.2026)
FB_YELLOW_STORIES = os.getenv("FB_YELLOW_STORIES", "1") != "0"  # сторіз на жовтій (вимкнути: "0")
# 25.11.2026 (Юля): на жовтій сторіз стартують одразу в новому дизайні (з мапою, хвиля 2), а не зі старою обкладинкою
FB_YELLOW_STORIES_FROM = os.getenv("FB_YELLOW_STORIES_FROM", "2026-11-30")


def _short_places(places: list, n: int = 3) -> str:
    return ", ".join(re.sub(r"\s*\(.*?\)", "", p) for p in places[:n])


def _date_label(d) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


def story_blocks_daily(results: dict, names: dict | None = None) -> list[dict]:
    """Сторіз за добу. Назви – ті самі, що на мапі й у текстах (без дужок із селами – з 12.11.2026)."""
    names = names or {}
    blocks = []
    for k in OBLASTS:
        r = results[k]
        nm = names.get(k) or []
        if r["newly_km2"] > 0:
            blocks.append({"label": STORY_LABELS[k], "big": f"{fmt_km2(r['newly_km2'])} км²",
                           "text": "позначено окупованими",
                           "note": (story_near(pick(nm, "occ")) if nm else
                                    f"поблизу: {_short_places(r['newly_places'])}" if r["newly_places"] else "")})
        if r["deocc_km2"] > 0:
            blocks.append({"label": "" if r["newly_km2"] > 0 else STORY_LABELS[k],
                           "big": f"{fmt_km2(r['deocc_km2'])} км²", "text": "позначено деокупованими",
                           "note": (story_near(pick(nm, "deocc") if (isinstance(nm, Names) and nm.both)
                                               or r["newly_km2"] <= 0 else []) if nm else
                                    f"поблизу: {_short_places(r['deocc_places'])}" if r["deocc_places"] else "")})
        if r.get("grey_km2", 0) > 0:
            blocks.append({"label": "" if (r["newly_km2"] > 0 or r["deocc_km2"] > 0) else STORY_LABELS[k],
                           "big": f"{fmt_km2(r['grey_km2'])} км²", "text": "з окупованих – на сіру зону",
                           "note": f"поблизу: {_short_places(r['grey_places'])}" if r["grey_places"] else ""})
    if not blocks:
        blocks.append({"big": "", "text": "Донеччина й Луганщина: за добу на мапі DeepState без змін."})
    else:
        for k in OBLASTS:
            if results[k]["newly_km2"] <= 0 and results[k]["deocc_km2"] <= 0 and not results[k].get("grey_km2"):
                # одним рядком – економить місце (рішення 10.11)
                blocks.append({"label": "", "big": "", "text": f"{OBLASTS[k]['short']} – без змін"})
    return blocks


def story_blocks_summary(kind: str, rows: list[dict]) -> list[dict]:
    word = "тиждень" if kind == "week" else "місяць"
    blocks = []
    dates = [r["date"] for r in rows]
    for k in OBLASTS:
        occ, free = _period_totals(rows, k)
        names = place_names(dates, k)
        nt = lambda xs: ("поблизу: " + " · ".join(xs)) if xs else ""   # noqa: E731
        split = isinstance(names, Names) and names.both
        if occ >= 0.01:
            blocks.append({"label": STORY_LABELS[k], "big": f"{fmt_km2(occ)} км²",
                           "text": f"позначено окупованими за {word}",
                           "note": nt(pick(names, "occ")) if free < 0.01 or split else ""})
        if free >= 0.01:
            blocks.append({"label": "" if occ >= 0.01 else STORY_LABELS[k], "big": f"{fmt_km2(free)} км²",
                           "text": f"позначено деокупованими за {word}", "note": nt(pick(names, "deocc"))})
        if occ < 0.01 and free < 0.01:
            blocks.append({"label": "", "big": "", "text": f"{OBLASTS[k]['short']} – без змін"})
    return blocks


def post_facebook_story(image_path: Path, page_id: str | None = None, label: str = "Facebook") -> None:
    """Публікує картинку як сторіз сторінки Facebook (page_id=None – біла). Помилка не зупиняє бота."""
    page_id, page_token = fb_page_credentials(page_id)
    if not FB_STORIES or not page_id or not page_token or DRY_RUN:
        if DRY_RUN:
            log(f"DRY_RUN – сторіз не публікується ({image_path}).")
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
        log(f"Сторіз ({label}) опубліковано: {st.get('post_id')}")
    except Exception as e:  # noqa: BLE001
        log(f"Сторіз ({label}): помилка – {e}")
        alert(f"Сторіз ({label}) не опубліковано: {e}")


def _period_label(start: str, end: str) -> str:
    a, b = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
    if a.month == b.month and a.year == b.year:
        return f"{a.day}–{b.day} {MONTHS_GEN[b.month - 1]} {b.year}"
    return f"{a.day} {MONTHS_GEN[a.month - 1]} – {b.day} {MONTHS_GEN[b.month - 1]} {b.year}"


STORY_FOOTERS = {  # підвал сторіз: заклик повертатися (посилання в сторіз не клікабельні)
    "day": ("Нові зміни на мапі DeepState", "щоранку о 8:00"),
    "week": ("Підсумки тижня", "щопонеділка"),
    "month": ("Підсумки місяця", "на початку кожного місяця"),
}


def send_tg_story_image(img: Path) -> None:
    """Сторіз у Telegram-каналі бот сам опублікувати не може – надсилає картинку в гілку «TikTok»."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat_id or DRY_RUN:
        return
    try:
        with img.open("rb") as fh:
            tg("sendPhoto", token, {"chat_id": chat_id, "message_thread_id": THREAD_TIKTOK,
                                    "caption": "📱 Для сторіз у Telegram-каналі @acmc_ua (вручну)"}, {"photo": fh})
    except Exception as e:  # noqa: BLE001
        log(f"Картинка для сторіз Telegram: {e}")


def make_and_post_story(cover: Path, date_label: str, blocks: list[dict], kind: str = "day") -> None:
    fb_ok = FB_STORIES and os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
    if not fb_ok and kind == "day":
        return
    try:
        from story import render_story
        foot, handle = STORY_FOOTERS.get(kind, STORY_FOOTERS["day"])
        out = render_story(cover, date_label, blocks, DATA / "story.jpg", footer=foot, handle=handle)
        # 25.11.2026 (Юля): сторіз у Telegram-каналі не публікуємо – картинку в «Рілзи» більше не шлемо
        if kind in ("week", "month") and os.getenv("TG_STORY_IMAGE", "0") == "1":
            send_tg_story_image(out)
        if not fb_ok:
            return
        post_facebook_story(out)
        # 25.11.2026 (Юля): сторіз і на жовтій сторінці – щодня, і в тихі дні (дописи в стрічку там – лише в дні змін)
        if FB_YELLOW_STORIES and datetime.now(KYIV).strftime("%Y-%m-%d") >= FB_YELLOW_STORIES_FROM:
            post_facebook_story(out, FB_YELLOW_PAGE_ID, "жовта FB")
        ig_api.post_story(FB_GRAPH, *fb_page_credentials(), out, log, alert, DRY_RUN)
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося зробити сторіз: {e}")


# ============================ TIKTOK (ручна публікація) ============================
# Раз на тиждень і раз на місяць бот надсилає в службовий чат вертикальні картки й готовий підпис.
# Щодня – лише позначка «⚡ варто опублікувати», якщо зміни великі або біля міста.
TIKTOK_BIG_KM2 = float(os.getenv("TIKTOK_BIG_KM2", "10"))
TIKTOK_TAGS = "#Донеччина #DeepState #Україна #мапа"


def _tiktok_tags(places: list[str], luhansk_changed: bool = False) -> str:
    """TikTok дозволяє не більше 5 хештегів. #Луганщина – лише коли там справді були зміни,
    щоб не обманювати очікування глядачів, які шукають новини Луганщини."""
    cities = []
    for p in places:
        name = re.sub(r"\s*\(.*?\)", "", p).strip()
        if is_city(p) and name not in cities:
            cities.append(name)
    base = ["#Донеччина"] + (["#Луганщина"] if luhansk_changed else []) + ["#DeepState"]
    tags = base + ["#" + re.sub(r"[\s'’ʼ-]", "", c) for c in cities[:5 - len(base)]]
    # лише справжні місця біля змін – без «доповнення» іншими містами (рішення 9.11.2026)
    return " ".join(tags[:5])


def _rows_places(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        for k in OBLASTS:
            for col in (f"{k}_newly_places", f"{k}_deoccupied_places", f"{k}_penetration_cities"):
                out += [x.strip() for x in (r.get(col) or "").split(";") if x.strip()]
    return out


def _rows_luhansk_changed(rows: list[dict]) -> bool:
    return any(_f(r.get("luhansk_newly_occupied_km2")) or _f(r.get("luhansk_deoccupied_km2")) for r in rows)


def _change_areas(rows: list[dict], key: str = "donetsk") -> list[str]:
    """Окремі ділянки змін за період (кожна доба – своя ділянка), від більшої до меншої.
    Для кожної – головний населений пункт: місто, якщо є, інакше перший у списку."""
    areas: dict[str, float] = {}
    for r in rows:
        km = _f(r.get(f"{key}_newly_occupied_km2")) + _f(r.get(f"{key}_deoccupied_km2"))
        ps = [p.strip() for p in ((r.get(f"{key}_newly_places") or "") + ";" + (r.get(f"{key}_deoccupied_places") or "")).split(";")
              if p.strip()]
        if km <= 0 or not ps:
            continue
        cities = [p for p in ps if is_city(p)]
        lead = _nearest_city(r.get("date", ""), key) or re.sub(r"\s*\(.*?\)", "", (cities or ps)[0]).strip()
        areas[lead] = areas.get(lead, 0) + km
    return sorted(areas, key=areas.get, reverse=True)


_CITY_POINTS: list | None = None


def _nearest_city(day: str, key: str = "donetsk", max_km: float = 20.0) -> str | None:
    """Найближче місто до ділянки змін цього дня (якщо ближче за max_km) – читачам воно знайоміше за село."""
    global _CITY_POINTS
    try:
        from shapely.geometry import shape
        from shapely.ops import nearest_points, unary_union
        f = CHANGES_DIR / f"{day}.geojson"
        if not f.exists():
            return None
        geoms = [shape(x["geometry"]) for x in json.loads(f.read_text())["features"]
                 if x["properties"].get("oblast") == key]
        if not geoms:
            return None
        g = unary_union(geoms)
        if _CITY_POINTS is None:
            feats = json.loads((DATA / "reference" / "settlements.geojson").read_text())["features"]
            _CITY_POINTS = [(x["properties"]["ADM4_UA"], shape(x["geometry"]).centroid) for x in feats
                            if is_city(x["properties"]["ADM4_UA"], x["properties"].get("ADM2_UA"))]
        k = math.cos(math.radians(g.centroid.y))
        best = None
        for name, pt in _CITY_POINTS:
            a, b = nearest_points(g, pt)
            dkm = math.hypot((a.x - b.x) * k, a.y - b.y) * 111
            if dkm <= max_km and (best is None or dkm < best[0]):
                best = (dkm, name)
        return best[1] if best else None
    except Exception:  # noqa: BLE001
        return None


def _nearest_city_geom(g, max_km: float = 20.0) -> str | None:
    """Найближче місто до геометрії (у межах max_km)."""
    global _CITY_POINTS
    from shapely.geometry import shape
    from shapely.ops import nearest_points
    if _CITY_POINTS is None:
        feats = json.loads((DATA / "reference" / "settlements.geojson").read_text())["features"]
        _CITY_POINTS = [(x["properties"]["ADM4_UA"], shape(x["geometry"]).centroid) for x in feats
                        if is_city(x["properties"]["ADM4_UA"], x["properties"].get("ADM2_UA"))]
    k = math.cos(math.radians(g.centroid.y))
    c = g.centroid
    cands = []
    for name, pt in _CITY_POINTS:
        a, b = nearest_points(g, pt)
        dkm = math.hypot((a.x - b.x) * k, a.y - b.y) * 111
        if dkm <= max_km:
            cands.append((round(dkm), math.hypot((c.x - pt.x) * k, c.y - pt.y), name))   # нічия → ближче до центру
    return min(cands)[2] if cands else None


_ALL_POINTS: list | None = None


def _nearest_settlement_geom(g) -> str | None:
    """Найближчий населений пункт будь-якого розміру (з тієї ж області)."""
    global _ALL_POINTS
    from shapely.geometry import shape
    if _ALL_POINTS is None:
        feats = json.loads((DATA / "reference" / "settlements.geojson").read_text())["features"]
        _ALL_POINTS = [(x["properties"]["ADM4_UA"], shape(x["geometry"]).centroid, x["properties"]["ADM4_PCODE"])
                       for x in feats]
    k = math.cos(math.radians(g.centroid.y))
    c = g.centroid
    best = min(_ALL_POINTS, key=lambda t: (g.distance(t[1]), math.hypot((c.x - t[1].x) * k, c.y - t[1].y)))
    _NAME_PCODE[best[0]] = best[2]   # щоб знати громаду саме цього пункту (однакових назв багато)
    return best[0]


_NAME_PCODE: dict[str, str] = {}
_HROMADAS: dict[str, str] | None = None


def hromada_of(name: str) -> str | None:
    """Громада населеного пункту, обраного як найближчий (за кодом КАТОТТГ; дані – KSE Loc Data Hub, MIT)."""
    global _HROMADAS
    pc = _NAME_PCODE.get(name)
    if not pc:
        return None
    if _HROMADAS is None:
        try:
            _HROMADAS = json.loads((DATA / "reference" / "hromadas.json").read_text())
        except Exception:  # noqa: BLE001
            _HROMADAS = {}
    h = _HROMADAS.get(pc[:9])
    return f"{h} громада" if h else None


_VOWELS = "аеєиіїоуюяАЕЄИІЇОУЮЯ"


def join_and(names: list[str]) -> str:
    """«A», «A й B», «A, B і C»: «й» – після голосного перед приголосним, інакше «і» (милозвучність, 22.11.2026)."""
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    a, b = names[-2], names[-1]
    conj = " й " if a[-1] in _VOWELS and b[0] not in _VOWELS else " і "
    return ", ".join(names[:-1]) + conj + b


def story_near(names: list[str]) -> str:
    """Telegram (добові) і сторіз: до села – громада в дужках, один раз на групу (рішення Юлі 16 і 18.11.2026):
    «поблизу: Василівка й Матяшеве (Криворізька громада), Русин Яр (Костянтинівська громада)».
    Села групуємо за громадою (порядок – як у списку), міста – без громади."""
    if not names:
        return ""
    groups: list[tuple[str | None, list[str]]] = []
    for n in names:
        h = None if is_city(n) else hromada_of(n)
        g = next((g for g in groups if h and g[0] == h), None)
        if g:
            g[1].append(n)
        else:
            groups.append((h, [n]))
    return "поблизу: " + ", ".join(join_and(ns) + (f" ({h})" if h else "") for h, ns in groups)



MERGE_KM = 3.0     # ділянки, ближчі за 3 км, – одне місце й одна назва (12.11.2026; до того ~5 км)
MENTION_KM = 5.0   # назва з повідомлення DeepState – лише якщо пункт не далі 5 км від змін


def _mentioned_names(dates: list[str], parts: list, text: str | None = None) -> list[tuple[str, object]]:
    """Населені пункти, які DeepState назвав у своїх повідомленнях за ці дні, – лише ті, що в межах
    MENTION_KM (5 км) від наших ділянок (щоб не зачепити тезку деінде). Порядок – як у повідомленнях.
    Рішення 12.11.2026: показуємо їх окремим рядком лише в Telegram, у графіку не додаємо."""
    global _ALL_POINTS
    try:
        if text is None:   # при публікації текст передаємо напряму – в історії доби ще немає
            rows = [r for r in read_history() if r.get("date") in set(dates)]
            text = " ".join(r.get("messages") or "" for r in rows)
        if not text.strip() or not parts:
            return []
        from shapely.geometry import shape
        from shapely.ops import unary_union
        if _ALL_POINTS is None:
            feats = json.loads((DATA / "reference" / "settlements.geojson").read_text())["features"]
            _ALL_POINTS = [(x["properties"]["ADM4_UA"], shape(x["geometry"]).centroid, x["properties"]["ADM4_PCODE"])
                           for x in feats]
        u = unary_union(parts)
        k = math.cos(math.radians(u.centroid.y))
        found = []
        for name, pt, *_ in _ALL_POINTS:
            if u.distance(pt) * 111 * max(k, 0.5) > MENTION_KM:   # запобіжник 12.11: лише справді поруч
                continue
            def word_rx(w: str) -> str:
                # відмінки: Костянтинівк-и / Костянтинівц-і, Червон-ого, Русин-ого Яр-у
                stem = w[:-1] if len(w) > 3 and w[-1] in "аяеоиіїуюь" else w
                alts = [re.escape(stem)]
                if w.endswith("ка") and len(w) > 4:
                    alts.append(re.escape(w[:-2] + "ц"))
                return "(?:" + "|".join(alts) + r")\w{0,3}"
            rx = r"(?<![\w'’ʼ])" + r"\s+".join(word_rx(w) for w in name.split()) + r"(?![\w'’ʼ])"
            pos = [m.start() for m in re.finditer(rx, text)]
            if pos:
                found.append((min(pos), u.distance(pt), name, pt))
        out, seen = [], set()
        for _, _, name, pt in sorted(found):
            if name not in seen:
                seen.add(name)
                out.append((name, pt))
        return out
    except Exception as e:  # noqa: BLE001
        log(f"Назви з повідомлень DeepState: {e}")
        return []


def deepstate_mentioned(dates: list[str], key: str = "donetsk", skip: list | None = None,
                        text: str | None = None) -> list[str]:
    """Для окремого рядка в Telegram (рішення 12.11.2026): населені пункти, які DeepState назвав у
    повідомленнях за добу і які не далі 5 км від наших змін у цій області. Без тих, що вже є в
    головних назвах (skip). Далекі від змін назви сюди не потрапляють – вони й так є в самих
    повідомленнях DeepState у кінці поста."""
    try:
        import change_map
        from shapely.ops import unary_union
        files = [CHANGES_DIR / f"{d}.geojson" for d in dates]
        red, blue, _ = change_map._timeline(change_map._load_steps(files, key))
        geoms = [g for g in (red, blue) if g is not None]
        if not geoms:
            return []
        u = unary_union(geoms)
        parts = [p for p in getattr(u, "geoms", [u]) if not p.is_empty]
        skip = set(skip or [])
        return [nm for nm, _ in _mentioned_names(dates, parts, text) if nm not in skip]
    except Exception as e:  # noqa: BLE001
        log(f"Згадані DeepState пункти: {e}")
        return []


class Names(list):
    """Список назв (усі – для заголовка мапи) + ті самі назви окремо за напрямком:
    .occ – біля ділянок, позначених окупованими, .deocc – деокупованими (рішення 10.11:
    коли є обидва напрямки, назви не змішуємо, щоб було видно, де що)."""
    occ: list = []
    deocc: list = []

    @property
    def both(self) -> bool:
        return bool(self.occ) and bool(self.deocc)



_MSG_STOP = {"Ворог", "ЗСУ", "Сили", "Оборони", "Росія", "Російські", "Окупанти", "Також", "Українські",
             "Україна", "України", "DeepState", "Донеччина", "Донеччині", "Донеччини", "Луганщина", "Луганщині",
             "Луганщини", "Донецька", "Донецькій", "Луганська", "Луганській", "Мапу", "Мапа", "Оновлено"}


def _name_rx(name: str) -> str:
    def word_rx(w: str) -> str:
        stem = w[:-1] if len(w) > 3 and w[-1] in "аяеоиіїуюь" else w
        alts = [re.escape(stem)]
        if w.endswith("ка") and len(w) > 4:
            alts.append(re.escape(w[:-2] + "ц"))
        return "(?:" + "|".join(alts) + r")\w{0,3}"
    return r"(?<![\w'’ʼ])" + r"\s+".join(word_rx(w) for w in name.split()) + r"(?![\w'’ʼ])"


def localize_messages(messages: list[dict], date: str, results: dict) -> list[dict]:
    """Рішення Юлі 16.11.2026 (випадок «Вільне та Петропавлівка»):
    • повідомлення DeepState беремо, лише якщо назва з нього – це пункт поруч (≤5 км) із ділянкою, позначеною
      на нашій мапі за цю добу (назва + місце, а не лише назва: тезок по Україні багато);
    • якщо в повідомленні лише наші пункти – цитуємо дослівно;
    • якщо там є й інші назви (інша область, тезка далеко) – НЕ цитуємо, а переказуємо власним рядком лише
      з нашими пунктами: «DeepState повідомив про просування ворога поблизу: Вільне.» – щоб жителі
      донецької Петропавлівки не вирішили, що це про них;
    • у дні без змін на мапі (нема з чим звірити) – лише повідомлення, де прямо названо Донеччину/Луганщину."""
    out = []
    try:
        import change_map
        from shapely.ops import unary_union
        zones = {}
        for key in OBLASTS:
            red, blue, _ = change_map._timeline(change_map._load_steps([CHANGES_DIR / f"{date}.geojson"], key))
            geoms = [g for g in (red, blue) if g is not None]
            if geoms:
                u = unary_union(geoms)
                zones[key] = [p for p in getattr(u, "geoms", [u]) if not p.is_empty]
    except Exception as e:  # noqa: BLE001
        log(f"Повідомлення DeepState: не вдалося звірити з мапою – {e}")
        zones = {}
    for m in messages:
        text = m["text"]
        near = []   # (ключ області, назва)
        for key, parts in zones.items():
            near += [(key, nm) for nm, _ in _mentioned_names([date], parts, text)]
        if not near:
            if not zones and re.search(r"Донеччин|Луганщин|Донецьк\w* обл|Луганськ\w* обл", text):
                out.append(m)   # змін на мапі нема – лишаємо лише явно «наші» повідомлення
            else:
                log(f"Повідомлення {m['time']:%d.%m %H:%M} пропущено: назви не біля наших змін на мапі")
            continue
        covered = [(mt.start(), mt.end()) for _, nm in near for mt in re.finditer(_name_rx(nm), text)]
        foreign = []
        for mt in re.finditer(r"(?<![\w'’ʼ])[А-ЯІЇЄҐ][а-яіїєґ'’ʼ-]{2,}", text):
            w = mt.group(0)
            if w in _MSG_STOP or any(a <= mt.start() < b for a, b in covered):
                continue
            if mt.start() == 0 or text[max(0, mt.start() - 2):mt.start()].strip() in (".", "!", "?", ":"):
                continue   # перше слово речення
            foreign.append(w)
        if not foreign:
            out.append(m)
            continue
        keys = {k for k, _ in near}
        occ = any(results.get(k, {}).get("newly_km2", 0) > 0 for k in keys)
        de = any(results.get(k, {}).get("deocc_km2", 0) > 0 for k in keys)
        what = ("про просування ворога" if occ and not de else "про деокупацію" if de and not occ else "про зміни")
        names = list(dict.fromkeys(nm for _, nm in near))
        log(f"Повідомлення {m['time']:%d.%m %H:%M}: є назви не з наших змін ({', '.join(foreign)}) – переказуємо")
        out.append({**m, "text": f"Повідомлено {what} поблизу: {', '.join(names)}.", "retold": True})
    return out

def pick(nm, which: str) -> list:
    """Назви для рядка про окупацію (which="occ") чи деокупацію ("deocc"). Якщо за період був лише
    один напрямок – повний список, як і раніше."""
    if isinstance(nm, Names) and nm.both:
        return list(getattr(nm, which))
    return list(nm or [])


def place_names(dates: list[str], key: str = "donetsk", n: int | None = None) -> list[str]:
    """ЄДИНИЙ список назв для всіх місць (перший кадр, заголовок мапи, останній кадр, сторіз).
    Беремо лише те, що лишилося на кінець періоду (без «позначили й зняли»). Ділянки, ближчі одна
    до одної ніж 3 км (MERGE_KM), – одне місце й одна назва (щоб «Добропілля · Білицьке» не займали два місця);
    назва – місто в межах 10 км, інакше найближчий населений пункт; до чотирьох найбільших за площею,
    перелічені зліва направо, як людина читає мапу (рішення 10.11)."""
    try:
        import change_map
        files = [CHANGES_DIR / f"{d}.geojson" for d in dates]
        red, blue, _ = change_map._timeline(change_map._load_steps(files, key))
        geoms = [g for g in (red, blue) if g is not None]
        if not geoms:
            return []
        from shapely.ops import unary_union
        u = unary_union(geoms)
        parts = [p for p in getattr(u, "geoms", [u]) if not p.is_empty]
        # групуємо ділянки, ближчі за MERGE_KM одна до одної (справжні км, а не градуси).
        # 12.11.2026: було ~5 км – ділянка біля Часового Яру за 4,4 км «злилася» з Костянтинівкою й лишилася
        # без назви; тепер 3 км (рішення Юлі: по одній назві на кожну ділянку)
        from shapely.affinity import scale as _scale
        _k = math.cos(math.radians(u.centroid.y))
        km = {id(p): _scale(p, xfact=111.32 * _k, yfact=110.57, origin=(0, 0)) for p in parts}
        clusters: list[list] = []
        for p in parts:
            # 18.11.2026 (Юля): у ДОБОВИХ зливаємо лише майже суміжні ділянки (<1 км) – дві ділянки за 1,6 км
            # (Матяшеве / Василівка) злилися, і більша лишилася без назви
            _mk = 1.0 if len(dates) == 1 else MERGE_KM
            hit = [c for c in clusters if any(km[id(p)].distance(km[id(q)]) < _mk for q in c)]
            merged = [p] + [q for c in hit for q in c]
            clusters = [c for c in clusters if c not in hit] + [merged]
        parts = [unary_union(c) for c in clusters]
        score: dict[str, float] = {}
        west: dict[str, float] = {}        # довгота – щоб перелічувати зліва направо, як читають мапу
        side: dict[str, set] = {}          # біля якої ділянки назва: окупованої, деокупованої чи обох
        part_name: list[tuple[str, object]] = []
        for g in parts:
            # ЩОДЕННА мапа/текст (одна дата): найближчий населений пункт – той, що видно біля ділянки
            # (рішення Юлі 16.11.2026: текст «поблизу: Білицьке», а пляма біля «Вільне» – розбіжність;
            # міста лишаються на мапі як орієнтир). Тижневі/місячні – як було: місто в межах 10 км,
            # інакше найближчий пункт (рішення 9.11.2026)
            if len(dates) == 1:
                name = _nearest_settlement_geom(g)
            else:
                name = _nearest_city_geom(g, max_km=10.0) or _nearest_settlement_geom(g)
            if name:
                part_name.append((name, g))
                score[name] = score.get(name, 0) + g.area
                west[name] = min(west.get(name, 999), g.centroid.x)
                if red is not None and g.intersection(red).area > 0:
                    side.setdefault(name, set()).add("occ")
                if blue is not None and g.intersection(blue).area > 0:
                    side.setdefault(name, set()).add("deocc")
        top = sorted(score, key=score.get, reverse=True)[:n or NAMES_MAX]   # ЯКІ назви – за площею змін
        ordered = sorted(top, key=lambda nm: west[nm])             # ПОРЯДОК – зліва направо (рішення 10.11)
        # назви з повідомлень DeepState сюди НЕ додаємо (рішення 12.11.2026): сторіз, шортси й Threads
        # не безрозмірні – тут лише головна назва кожної ділянки; згадані DeepState пункти йдуть
        # окремим рядком тільки в Telegram (див. deepstate_mentioned і build_text)
        out = Names(ordered)
        out.occ = [x for x in out if "occ" in side.get(x, ())]
        out.deocc = [x for x in out if "deocc" in side.get(x, ())]
        return out
    except Exception as e:  # noqa: BLE001
        log(f"Назви ділянок: {e}")
        return []


def names_title(names: list[str]) -> str:
    """Завжди нейтральний перелік: «Поблизу: Білицьке» / «Поблизу: A · B · C» (рішення Юлі 9.11 –
    «Біля Білицького» звучить як анонс наступу)."""
    if not names:
        return ""
    return "Поблизу: " + " · ".join(names)


def _period_breakdown(rows: list[dict], key: str = "donetsk") -> dict | None:
    """Підсумок періоду за геометрією змін: окуповано / деокуповано / «позначили й зняли» (км²).
    Ділянку, яку за період і позначили, і зняли, не рахуємо ні туди, ні туди."""
    try:
        import change_map
        files = [CHANGES_DIR / f"{r.get('date', '')}.geojson" for r in rows]
        if not any(f.exists() for f in files):
            return None
        return change_map.breakdown(files, key)
    except Exception:  # noqa: BLE001
        return None


def _period_totals(rows: list[dict], key: str = "donetsk") -> tuple[float, float]:
    """Окуповане і деокуповане за період окремо (а не лише різниця), без «позначили й зняли»."""
    b = _period_breakdown(rows, key)
    if b:
        return round(b["occupied"], 2), round(b["deoccupied"], 2)
    return (round(sum(_f(r.get(f"{key}_newly_occupied_km2")) for r in rows), 2),
            round(sum(_f(r.get(f"{key}_deoccupied_km2")) for r in rows), 2))


def _period_flip(rows: list[dict], key: str = "donetsk") -> float:
    b = _period_breakdown(rows, key)
    return round(b["flip"], 2) if b else 0.0


def _tiktok_headline(kind: str, rows: list[dict]) -> tuple[str, str, str]:
    """Головна цифра й місце для першого кадру TikTok (по Донеччині)."""
    _o, _fr = _period_totals(rows, "donetsk")
    net = round(_o - _fr, 2)   # 16.11.2026: та сама цифра, що в тексті й на мапі (було 19,25 проти 19,23)
    places: dict[str, int] = {}
    city_names: set[str] = set()
    for r in rows:
        for p in (r.get("donetsk_newly_places") or "").split(";"):
            name = re.sub(r"\s*\(.*?\)", "", p).strip()
            if name:
                places[name] = places.get(name, 0) + (3 if is_city(p) else 1)
                if is_city(p):
                    city_names.add(name)
    if abs(net) < 0.01:
        return "Без змін", "позначень на мапі", "на Донеччині за " + ("тиждень" if kind == "week" else "місяць")
    big = f"{fmt_km2(abs(net))} км²"
    what = "позначено окупованими" if net > 0 else "позначено деокупованими"
    if not places:
        return big, what, "на Донеччині"
    names = place_names([r["date"] for r in rows], n=4)   # відео – до 4 назв (відео в роботі в Юлі)
    if names:
        t = names_title(names)
        return big, what, t[:1].lower() + t[1:]
    top = max(places, key=places.get)
    return big, what, f"поблизу: {top}"


COVER_GAMMA, COVER_SHADE = 1.0, 0   # 16.11.2026: обкладинка TikTok/Reels – без затемнення, ті самі пікселі, що в Instagram і Facebook (фірмова скрізь однакова)


def _tiktok_first_frame(kind: str, rows: list[dict], cover: Path, date_label: str, out: Path,
                        bg: Path | None = None) -> Path:
    """Перший кадр відео: фірмове синє тло, оксамитова обкладинка – точно там і такого ж розміру,
    як мапа на наступному кадрі (тож здається, що в квадраті оксамит змінюється на мапу).
    На обкладинці: «Поблизу: …» (той самий список, що на мапі), цифра – жовта."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    import change_map
    fonts = ROOT / "assets" / "fonts"
    f800 = lambda n: ImageFont.truetype(str(fonts / "Montserrat-800.ttf"), n)  # noqa: E731
    f500 = lambda n: ImageFont.truetype(str(fonts / "Montserrat-500.ttf"), n)  # noqa: E731
    OX, TOP, MW, MH = 40, 500, 1000, 1040            # верх – як у change_map для висоти 1920 (дата й заголовок у зоні, видимій у стрічці)
    img = Image.new("RGB", (1080, 1920), change_map.BG)
    sq = Image.open(cover).convert("RGB").resize((MH, MH), Image.LANCZOS)
    # 16.11.2026 (Юля): обкладинка в стрічці TikTok надто темна, оксамиту не видно – висвітлюємо фактуру
    if COVER_GAMMA != 1.0:   # гамма піднімає темну фактуру, а світлий текст обкладинки майже не чіпає
        sq = sq.point([round(255 * ((i / 255) ** COVER_GAMMA)) for i in range(256)] * 3)
    img.paste(sq.crop(((MH - MW) // 2, 0, (MH - MW) // 2 + MW, MH)), (OX, TOP))
    sh = Image.new("L", img.size, 0)
    ImageDraw.Draw(sh).rectangle([OX, TOP + 290, OX + MW, TOP + 730], fill=COVER_SHADE)   # легке затемнення під текстом
    box = Image.new("L", img.size, 0)
    ImageDraw.Draw(box).rectangle([OX, TOP, OX + MW, TOP + MH], fill=255)
    img.paste(Image.new("RGB", img.size, (8, 10, 18)), (0, 0), ImageChops.multiply(sh.filter(ImageFilter.GaussianBlur(70)), box))
    d = ImageDraw.Draw(img)
    yel, white = (247, 197, 49), (250, 250, 250)

    def centered(text, font, y, fill):
        d.text(((1080 - d.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)

    def centered_mix(parts, y, fill):   # рядок з частин різної жирності: [(текст, шрифт), …]
        x = (1080 - sum(d.textlength(t, font=f) for t, f in parts)) / 2
        for t, f in parts:
            d.text((x, y), t, font=f, fill=fill)
            x += d.textlength(t, font=f)

    d.text((60, TOP - 200), date_label, font=f800(60), fill=yel)   # там, де далі заголовок мапи
    dates = [r["date"] for r in rows]
    names = place_names(dates, n=4)
    occ, free = _period_totals(rows, "donetsk")
    y = TOP + 300                     # текст – між заголовком обкладинки і смугою «Донеччина · Луганщина»
    word = "за тиждень" if kind == "week" else "за місяць"
    split = isinstance(names, Names) and names.both and occ >= 0.01 and free >= 0.01
    if split:
        # обидва напрямки (рішення 10.11): кожна цифра – зі своїми назвами, щоб не було каші
        y += 10
        centered(f"позначено {word}:", f500(38), y, white)
        y += 50
        for v, w, nm_ in ((occ, "окупованими", names.occ), (free, "деокупованими", names.deocc)):
            num, lab = f"{fmt_km2(v)} км²", f"  {w}"
            x = (1080 - d.textlength(num, font=f800(70)) - d.textlength(lab, font=f800(42))) / 2
            d.text((x, y + 66), num, font=f800(70), fill=yel, anchor="ls")
            d.text((x + d.textlength(num, font=f800(70)), y + 66), lab, font=f800(42), fill=white, anchor="ls")
            ln = near_phrase(nm_, [])
            fs = 38
            while d.textlength(ln, font=f500(fs)) > 920 and fs > 28:
                fs -= 2
            centered(ln, f500(fs), y + 80, white)
            y += 80 + fs + 34
        img.save(out, "JPEG", quality=92)
        return out
    if names:
        # ієрархія (рішення Юлі 10.11): «Поблизу:» помітніше, назви – спокійніше, головне – цифра
        centered("Поблизу:", f500(44), y, white)
        line = " · ".join(names)
        rows_ = [line]
        if d.textlength(line, font=f800(52)) > 920 and len(names) > 2:   # 3–4 назви – на два рядки
            rows_ = [" · ".join(names[:2]), " · ".join(names[2:])]
        fs = 46 if len(rows_) > 1 else 52
        while max(d.textlength(r_, font=f800(fs)) for r_ in rows_) > 920 and fs > 38:
            fs -= 2
        for ri, r_ in enumerate(rows_):
            centered(r_, f800(fs), y + 58 + ri * (fs + 10), white)
        y += 58 + len(rows_) * (fs + 10) + 22
    big = 88 if names and len(names) > 2 else 96
    if occ >= 0.01 and free >= 0.01:
        # обидва напрямки: дві короткі рядки замість одного довгого (довгий виходив за краї кадру)
        centered(f"позначено {word}:", f500(36), y - 8, white)
        for i, (v, w) in enumerate(((occ, "окупованими"), (free, "деокупованими"))):
            num, lab = f"{fmt_km2(v)} км²", f"  {w}"
            x = (1080 - d.textlength(num, font=f800(64)) - d.textlength(lab, font=f800(40))) / 2
            base = y + 86 + i * 76
            d.text((x, base), num, font=f800(64), fill=yel, anchor="ls")
            d.text((x + d.textlength(num, font=f800(64)), base), lab, font=f800(40), fill=white, anchor="ls")
    elif occ >= 0.01 or free >= 0.01:
        centered(f"{fmt_km2(max(occ, free))} км²", f800(big), y, yel)
        centered_mix([("позначено ", f500(42)), ("окупованими" if occ >= 0.01 else "деокупованими", f800(42)),
                      (f" {word}", f500(42))], y + big + 14, white)
    else:
        centered("без змін", f800(100), y, yel)
    img.save(out, "JPEG", quality=92)
    return out


END_STYLE = "full"   # останній кадр: "full" – «позначено окупованими: N км²»; "header" – «позначено як:» + коротко


def _tiktok_end_card(kind: str, rows: list[dict], cover: Path, date_label: str, out: Path) -> Path:
    """Фінальний кадр відео: коротко цифри + великий заклик. Усе вище нижньої зони інтерфейсу TikTok."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
    fonts = ROOT / "assets" / "fonts"
    f800 = lambda n: ImageFont.truetype(str(fonts / "Montserrat-800.ttf"), n)  # noqa: E731
    f500 = lambda n: ImageFont.truetype(str(fonts / "Montserrat-500.ttf"), n)  # noqa: E731
    im = Image.open(cover).convert("RGB")
    w, h = im.size
    mp = im.crop((int(w * .28), int(h * .28), int(w * .98), int(h * .64)))
    mp = mp.resize((int(1920 * mp.width / mp.height), 1920)).crop((0, 0, 1080, 1920))
    import change_map
    canvas = Image.new("RGB", (1080, 1920), change_map.BG)   # той самий темно-синій, що й фон мапи
    d = ImageDraw.Draw(canvas)
    yel, white, grey = (247, 197, 49), (250, 250, 250), (200, 200, 200)

    def centered(text, font, y, fill):
        d.text(((1080 - d.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)

    red, blue = (255, 90, 90), (88, 196, 255)
    # мінімум тексту: область, дві цифри з одним словом, заклик. «Позначено» – у рядку джерела внизу.
    period = re.sub(r"\s*20\d\d", "", date_label)
    if kind == "month":
        period = period[:1].lower() + period[1:]   # «Донеччина · листопад»
    y = 380
    shown = False
    for k in OBLASTS:
        occ, free = _period_totals(rows, k)
        if occ < 0.01 and free < 0.01:
            continue
        shown = True
        centered(f"{STORY_LABELS[k][:1]}{STORY_LABELS[k][1:].lower()} · {period}", f500(46), y, white)
        y += 70
        nm = place_names([r["date"] for r in rows], k, n=4)
        if isinstance(nm, Names) and nm.both and occ >= 0.01 and free >= 0.01:
            # обидва напрямки: під кожною цифрою – свої назви
            y += 20
            for val, word, col, xs in ((occ, "позначено окупованими", red, nm.occ),
                                       (free, "позначено деокупованими", blue, nm.deocc)):
                centered(f"{fmt_km2(val)} км²", f800(104), y, col)
                centered(word, f500(46), y + 120, white)
                ln = near_phrase(xs, [])
                fs = 36
                while d.textlength(ln, font=f500(fs)) > 960 and fs > 28:
                    fs -= 2
                centered(ln, f500(fs), y + 180, grey)
                y += 260
            y += 30
            continue
        if nm:
            ln = "поблизу: " + " · ".join(nm)
            if d.textlength(ln, font=f500(38)) > 960 and len(nm) > 2:   # 3–4 назви – на два рядки
                centered("поблизу: " + " · ".join(nm[:2]), f500(38), y, grey)
                y += 50
                ln = " · ".join(nm[2:])
            centered(ln, f500(38), y, grey)
            y += 60
        y += 20
        for val, word, col in ((occ, "позначено окупованими", red), (free, "позначено деокупованими", blue)):
            if val >= 0.01:
                centered(f"{fmt_km2(val)} км²", f800(104), y, col)
                centered(word, f500(46), y + 120, white)
                y += 210
        y += 30
    if not shown:
        centered(f"Донеччина · {period}", f500(46), y, white)
        centered("без змін", f800(80), y + 90, white)
        y += 220
    centered("За даними DeepStateMap", f500(34), y - 10, grey)   # джерело – одразу під цифрами
    y = max(y + 140, 1150)
    # 24.11.2026 (Юля): заклик – той самий, що в описах: щотижня бачити зміни на мапі Донеччини
    centered("Підписуйтеся,", f800(78), y, yel)
    centered("аби щотижня бачити", f800(58), y + 100, white)
    centered("зміни на мапі Донеччини", f800(58), y + 172, white)
    canvas.save(out, "JPEG", quality=90)
    return out


def _vertical_cover(cover: Path, out: Path) -> Path:
    from PIL import Image
    im = Image.open(cover).convert("RGB")
    canvas = Image.new("RGB", (1080, 1920), (10, 14, 26))
    bg = im.resize((1920, 1920)).crop((420, 0, 1500, 1920))
    from PIL import ImageFilter, ImageEnhance
    canvas.paste(ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(30))).enhance(0.45), (0, 0))
    canvas.paste(im.resize((1080, 1080)), (0, 420))
    canvas.save(out, "JPEG", quality=90)
    return out


def youtube_text(kind: str, body: list[str], date_label: str, places: list[str], rows: list[dict]) -> str:
    """Назва й опис для YouTube Shorts (основний канал АКМЦ, плейлист DeepState)."""
    # 24.11.2026 (Юля): головне – у перших ~40 знаках назви (у стрічці Shorts решта обрізається):
    # «Донеччина: +17,94 км² окупованих за тиждень | мапа DeepState»
    occ, free = _period_totals(rows, "donetsk")
    net = round(occ - free, 2)
    span = "тиждень" if kind == "week" else MONTHS_UA[int(rows[-1]["date"][5:7]) - 1]
    if occ >= 0.01 and free >= 0.01:   # 24.11.2026 (Юля): обидва напрямки – окремо, як у Telegram, без «чистої різниці»
        a, b = (f"+{fmt_km2(occ)} км² окупованих", f"{fmt_km2(free)} км² деокупованих")
        title = f"Донеччина: {a if occ >= free else b}, {b if occ >= free else a} за {span} | мапа DeepState"
        lead = (f"За {span} DeepState позначив окупованими {fmt_km2(occ)} км² Донеччини, "
                f"а деокупованими – {fmt_km2(free)} км².")
    elif net >= 0.01:
        title = f"Донеччина: +{fmt_km2(net)} км² окупованих за {span} | мапа DeepState"
        lead = f"За {span} DeepState позначив окупованими ще {fmt_km2(net)} км² Донеччини."
    elif net <= -0.01:
        title = f"Донеччина: {fmt_km2(-net)} км² деокупованих за {span} | мапа DeepState"
        lead = f"За {span} DeepState позначив деокупованими {fmt_km2(-net)} км² Донеччини."
    else:
        title = f"Донеччина: за {span} без змін | мапа DeepState"
        lead = f"За {span} на мапі DeepState на Донеччині без змін."
    dates = [r["date"] for r in rows]
    try:
        names = place_names(dates, "donetsk")
    except Exception:  # noqa: BLE001
        names = []
    if names and abs(net) >= 0.01:
        lead += f" Найближчі населені пункти: {', '.join(names)}."
    luh_changed = any(_f(r.get("luhansk_newly_occupied_km2")) or _f(r.get("luhansk_deoccupied_km2")) for r in rows)
    luh = next((b for b in body if b.startswith("Луганщина")), "") if luh_changed else ""
    lead += f" {luh}" if luh else " На Луганщині змін немає."
    # хештеги: #Донеччина + два найбільші населені пункти періоду (міста – першими)
    big = sorted(names, key=lambda n: not is_city(n))[:2]
    hashtags = " ".join(["#Донеччина", *["#" + re.sub(r"[\s'’ʼ-]", "", n) for n in big]])
    desc = [lead, "", "Щопонеділка – як змінилася мапа Донеччини за тиждень. Підписуйтеся, щоб не пропустити.",
            "", hashtags]
    # 16.11.2026 (Юля): готовий рядок тегів – постійні DeepState + міста цього періоду (ті ж, що на мапі)
    # загальні теги (Донеччина, Донбас, АКМЦ, міста…) стоять у стандартних тегах каналу (Юля 16.11.2026),
    # тут – лише DeepState-специфічні + міста періоду; разом із загальними – у межах 500 знаків
    tags = ["DeepState", "ДіпСтейт", "мапа DeepState", "DeepState map", "зміни на мапі", "лінія фронту",
            "мапа бойових дій"] + (["Луганська область"] if luh_changed else [])
    try:
        tags += [n for n in place_names([r["date"] for r in rows], n=4) if n not in tags]
    except Exception:  # noqa: BLE001
        pass
    tags_line = ", ".join(tags)
    while len(tags_line) > 260 and len(tags) > 8:
        tags.pop(); tags_line = ", ".join(tags)
    return "\n".join(["▶️ YouTube Shorts", "", "Назва:", title, "", "Опис:", *desc, "",
                      "Теги (додати в кінець до тегів каналу, нічого не стирати):", tags_line, "",
                      "Плейлист: «DeepState: зміни на мапі Донеччини та Луганщини» · Не для дітей · Категорія: Новини та політика"])


def tiktok_caption(kind: str, rows: list[dict], date_label: str) -> tuple[str, list[str], list[str]]:
    """Підпис для TikTok + рядки новини (body) і населені пункти – для YouTube."""
    teaser = build_fb_summary_teaser(kind, rows, None if kind == "week" else date_label)
    body = [l for l in teaser.split("\n\n")[1].split("\n")] if "\n\n" in teaser else []
    places = []
    for r in rows:
        for k in OBLASTS:
            places += [x.strip() for x in (r.get(f"{k}_newly_places") or "").split(";") if x.strip()]
            places += [x.strip() for x in (r.get(f"{k}_penetration_cities") or "").split(";") if x.strip()]
    span = re.sub(r"\s*20\d\d", "", date_label)
    title = f"DeepState: підсумки тижня, {span}" if kind == "week" else f"DeepState: підсумки місяця, {date_label}"
    # 23–24.11.2026: один заклик підписатися; без рядка джерела (DeepState – у заголовку й на кадрі)
    caption = "\n".join([title, "", *body, "", SUB_CTA_TT, "",
                          _tiktok_tags(places, any(_f(r.get("luhansk_newly_occupied_km2")) or _f(r.get("luhansk_deoccupied_km2")) for r in rows))])
    return caption, body, places


def send_tiktok_pack(kind: str, rows: list[dict], cover: Path, date_label: str, start: str,
                     change_map_img: Path | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        from story import render_story
        card = render_story(cover, date_label, story_blocks_summary(kind, rows), DATA / "tiktok_card.jpg",
                            footer="Підписуйтеся, щоб не пропустити",
                            handle="Підсумки щопонеділка" if kind == "week" else "Підсумки щомісяця")
        first = _tiktok_first_frame(kind, rows, cover, date_label, DATA / "tiktok_cover.jpg")  # «оксамитовий» фон обкладинки
        end_card = _tiktok_end_card(kind, rows, cover, date_label, DATA / "tiktok_end.jpg")
        caption, body, places = tiktok_caption(kind, rows, date_label)
        if DRY_RUN and not os.getenv("TIKTOK_PREVIEW"):
            log("DRY_RUN – матеріали для TikTok не надсилаються. Підпис:\n" + caption)
            return
        video = None
        try:
            from video import make_video
            segs = []   # 24.11.2026: кожна мапа (Донеччина, Луганщина чи далекі ділянки) – окремою частиною відео
            for m_ in ([change_map_img] + EXTRA_MAPS.get(change_map_img, [])) if change_map_img else []:
                vf, vb = m_.with_name(m_.stem + "_v.jpg"), m_.with_name(m_.stem + "_base_v.jpg")
                if not (vf.exists() and vb.exists()):
                    continue
                sj = m_.with_name(m_.stem + "_steps.json")
                st_ = ([(m_.with_name(n), sec, m_.with_name(off)) for n, sec, off in json.loads(sj.read_text())]
                       if sj.exists() else [])
                st_ = [x for x in st_ if x[0].exists() and x[2].exists()]
                key_ = MAP_OBLAST.get(str(m_), "donetsk")
                segs.append({"base": vb, "full": vf, "steps": st_ or None,
                             "intro": f"{'Луганщина' if key_ == 'luhansk' else 'Донеччина'} на мапі DeepState"})
            video = make_video(first, None, change_map_img, end_card, DATA / "tiktok_video.mp4", segments=segs)
        except Exception as e:  # noqa: BLE001
            log(f"Відео для TikTok/Shorts: не вдалося – {e}")
        if video:
            with video.open("rb") as fh:
                tg("sendVideo", token, {"chat_id": chat_id, "message_thread_id": THREAD_TIKTOK, "supports_streaming": "true",
                                        "caption": "🎬 Для TikTok і YouTube Shorts: завантажте відео, додайте музику. Підпис – нижче."},
                   {"video": fh})
        else:  # запасний варіант – карусель картинок
            frames = [first] + ([_vertical(change_map_img, DATA / "tiktok_map.jpg")] if change_map_img else []) + [end_card]
            files = {f"f{i}": p.open("rb") for i, p in enumerate(frames)}
            try:
                media = json.dumps([{"type": "photo", "media": f"attach://f{i}",
                                     **({"caption": "🎵 Для TikTok: фото-допис у цьому порядку, додайте музику"} if i == 0 else {})}
                                    for i in range(len(frames))])
                tg("sendMediaGroup", token, {"chat_id": chat_id, "message_thread_id": THREAD_TIKTOK, "media": media}, files)
            finally:
                for fh in files.values():
                    fh.close()
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_TIKTOK, "text": caption})
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_TIKTOK,
                                  "text": youtube_text(kind, body, date_label, places, rows)})
        log("Матеріали для TikTok надіслано в службовий чат.")
    except Exception as e:  # noqa: BLE001
        log(f"TikTok-матеріали: помилка – {e}")


def tiktok_daily_flag(results: dict, grey: dict) -> None:
    """Щоденна підказка «⚡ Варто опублікувати в TikTok» ВИМКНЕНА 12.11.2026 (рішення Юлі: плутає;
    відео в TikTok/Shorts – лише щопонеділка і 1-го). Увімкнути знову – TIKTOK_DAILY_FLAG=1."""
    if os.getenv("TIKTOK_DAILY_FLAG", "0") != "1":
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    big = [cfg["short"] for k, cfg in OBLASTS.items() if results[k]["newly_km2"] >= TIKTOK_BIG_KM2]
    near = []
    for k in OBLASTS:
        for p in results[k]["newly_places"]:
            name = re.sub(r"\s*\(.*?\)", "", p).strip()
            if is_city(p):
                near.append(name)
        near += (grey.get(k) or {}).get("cities") or []
    if not (big or near) or DRY_RUN or not token or not chat_id:
        return
    why = []
    if big:
        why.append(f"великі зміни ({', '.join(big)}: понад {fmt_km2(TIKTOK_BIG_KM2)} км²)")
    if near:
        why.append("зміни біля міст: " + ", ".join(dict.fromkeys(near)))
    try:
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_TIKTOK, "text": "⚡ Варто опублікувати в TikTok сьогодні: "
                                  + "; ".join(why) + ". Сторіз-картка – у Facebook/Instagram, текст – у Telegram."})
    except Exception as e:  # noqa: BLE001
        log(f"TikTok-позначка: помилка – {e}")


def alert(message: str) -> None:
    """Повідомлення про помилку в окремий (службовий) чат, якщо він налаштований."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        tg("sendMessage", token, {"chat_id": chat_id, "message_thread_id": THREAD_ERRORS,
                                  "text": f"⚠️ DeepState-трекер: {message}"[:4000]})
    except Exception as e:  # noqa: BLE001
        log(f"Не вдалося надіслати сповіщення про помилку: {e}")


# ============================ STATE ============================
# data/state/current.json – останній знімок (дата D)
# data/state/base.json    – знімок, з яким порівнювали сьогодні
# Повторний запуск того ж дня порівнює з тим самим base, тож дублі не виникають.


def geom_to_json(g):
    return mapping(g) if g is not None else None


GREY_DIR = DATA / "grey"


def save_grey_snapshot(day: str, unknown: dict) -> None:
    """Щоденний знімок сірої зони (район проникнення) по двох областях, ~25 КБ на день.
    Окуповане на будь-яку дату ми й так точно відновлюємо зі змін, а сіру зону – ні, тому зберігаємо її.
    Потрібно для тижневих/місячних мап: якою була сіра зона на початок періоду і як вона змінилася."""
    try:
        from shapely import set_precision
        GREY_DIR.mkdir(parents=True, exist_ok=True)
        save_json(GREY_DIR / f"{day}.json", {
            "date": day,
            "unknown": {k: (mapping(set_precision(v, 0.0001)) if v is not None and not v.is_empty else None)
                        for k, v in unknown.items()},
        })
    except Exception as e:  # noqa: BLE001
        log(f"Знімок сірої зони не збережено: {e}")


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
    "donetsk_to_grey_km2", "luhansk_to_grey_km2", "donetsk_to_grey_places", "luhansk_to_grey_places",
    "donetsk_penetration_cities", "luhansk_penetration_cities",
    "threads_followers", "instagram_followers",
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
    """Підсумок для Telegram. Одна цифра на область – чистий підсумок за період (як у Threads/Facebook);
    період – від стану мапи на початок до стану на кінець (16.11.2026, Юля: «09.11–16.11», як «15.11–16.11»
    у добовому заголовку, на обкладинці й на мапі; було «09.11–15.11» – календарні дні)."""
    d0 = (datetime.fromisoformat(rows[0]["date"]) - timedelta(days=1)).strftime("%d.%m")
    d1 = datetime.fromisoformat(rows[-1]["date"]).strftime("%d.%m.%Y")
    title = (f"DeepState: підсумки тижня, {d0}–{d1}" if kind == "week"
             else f"DeepState: підсумки місяця – {label}")
    word = "за тиждень" if kind == "week" else "за місяць"
    lines = [f"<b>{title}</b>", ""]
    if all(not _f(r[f"{k}_newly_occupied_km2"]) and not _f(r[f"{k}_deoccupied_km2"]) and not _f(r.get(f"{k}_to_grey_km2"))
           for r in rows for k in OBLASTS):   # рішення Юлі 17.11: «Донеччина й Луганщина: за тиждень … без змін.»
        return "\n".join([lines[0], "", f"Донеччина й Луганщина: {word} на мапі DeepState без змін.", "",
                          editor_note(kind)])
    for key, cfg in OBLASTS.items():
        newly = round(sum(_f(r[f"{key}_newly_occupied_km2"]) for r in rows), 2)
        deocc = round(sum(_f(r[f"{key}_deoccupied_km2"]) for r in rows), 2)
        grey_p = round(sum(_f(r.get(f"{key}_to_grey_km2")) for r in rows), 2)
        days = sum(1 for r in rows if _f(r[f"{key}_newly_occupied_km2"]) > 0 or _f(r[f"{key}_deoccupied_km2"]) > 0)
        _o, _fr = _period_totals(rows, key)
        net = round(_o - _fr, 2)   # 16.11.2026: та сама цифра, що на мапі й у відео
        if newly == 0 and deocc == 0 and not grey_p:
            body = f"{word} без змін."
        else:
            near = tg_near_summary(place_names([r["date"] for r in rows], key),
                           _places(rows, f"{key}_newly_places") + _places(rows, f"{key}_deoccupied_places"))
            near = f" – {near}" if near else ""
            nmp = place_names([r["date"] for r in rows], key)
            if newly > 0 and deocc > 0 and isinstance(nmp, Names) and nmp.both:   # назви окремо за напрямком
                no = tg_near_summary(pick(nmp, "occ"), _places(rows, f"{key}_newly_places"))
                nd = tg_near_summary(pick(nmp, "deocc"), _places(rows, f"{key}_deoccupied_places"))
                o_, f_ = _period_totals(rows, key)   # ті самі цифри, що на мапі, у відео й сторіз
                net = round(o_ - f_, 2)              # щоб підсумок точно дорівнював різниці двох цифр
                body = (f"– {fmt_km2(o_)} км² позначено окупованими" + (f" – {no}" if no else "")
                        + f"\n– {fmt_km2(f_)} км² позначено деокупованими" + (f" – {nd}" if nd else "")
                        + "\nУ підсумку окупована площа "
                        + (f"зросла на {fmt_km2(net)} км²." if net >= 0.01 else
                           f"зменшилася на {fmt_km2(-net)} км²." if net <= -0.01 else "майже не змінилася."))
            elif newly > 0 and deocc > 0:   # були і окупація, і деокупація – показуємо обидві та підсумок
                body = (f"{word} {fmt_km2(newly)} км² позначено окупованими, {fmt_km2(deocc)} км² – "
                        f"деокупованими{near}. У підсумку окупована площа "
                        + (f"зросла на {fmt_km2(net)} км²." if net >= 0.01 else
                           f"зменшилася на {fmt_km2(-net)} км²." if net <= -0.01 else "майже не змінилася."))
            elif newly > 0:
                body = f"{word} {fmt_km2(net if net > 0 else newly)} км² позначено окупованими{near}."
            elif deocc > 0:
                body = f"{word} {fmt_km2(-net if net < 0 else deocc)} км² позначено деокупованими{near}."
            else:
                body = ""
            if grey_p > 0:
                body += f" Ще {fmt_km2(grey_p)} км² змінили позначення з окупованих на сіру зону."
            body += f" Зміни були в {days} із {len(rows)} днів."
        pen = []
        for r in rows:
            for c in (r.get(f"{key}_penetration_cities") or "").split(";"):
                c = c.strip()
                if c and c not in pen:
                    pen.append(c)
        if pen and not SHOW_GREY_IN_TEXT:
            nms = [CITY_GENITIVE.get(c, c) for c in pen[:4]]
            where = nms[0] if len(nms) == 1 else ", ".join(nms[:-1]) + " та " + nms[-1]
            body += f" Нові райони проникнення (території, що потребують уточнення) з'являлися біля {where}."
        last = rows[-1]
        pct = _f(last.get(f"{key}_occupied_percent"))
        total = f" Загалом окупованими позначено {fmt_km2(_f(last[f'{key}_occupied_km2']))} км²"
        total += f" ({str(round(pct, 1)).replace('.', ',')}% області)." if pct else "."
        body = body.strip()
        if body.startswith("– "):   # обидва напрямки – кожен окремим рядком
            lines.append(f"<b>{cfg['short']} {word}:</b>\n{body}{total}")
        else:
            lines.append(f"<b>{cfg['short']}:</b> {body[0].upper()}{body[1:]}{total}")
        lines.append("")
    # 18.11.2026 (Юля): повідомлення DeepState у підсумках більше не публікуємо
    lines.append(editor_note(kind))
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
            log(f"Підсумок ({s['kind']}): даних лише за {len(rows)} дн. (потрібно {need}) – пропускаю.")
            done[s["kind"]] = today
            continue
        start = min(r["compared_with"] for r in rows)
        text = build_summary(s["kind"], rows, start, today, s.get("label"))
        publish_at = now.replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
        wait_until(publish_at + timedelta(minutes=SUMMARY_DELAY_MINUTES.get(s["kind"], 0)))
        rows_places = {k: [x for r in rows for x in (r.get(f"{k}_newly_places") or "").split("; ") if x] for k in OBLASTS}
        nets = {}
        for k in OBLASTS:
            nets[k] = _net_period(rows, k)
        mp = make_change_map(s["kind"], [r["date"] for r in rows], nets, rows_places, f"map_{s['kind']}.jpg",
                             _change_areas(rows, max(OBLASTS, key=lambda k: abs(nets.get(k) or 0))))
        # без звуку (12.11); у Telegram – мапа періоду, якщо вона є, інакше обкладинка (рішення Юлі 14.11.2026;
        # до того – завжди обкладинка): мапу з датою охочіше пересилають, а пересилання – головне джерело нових читачів
        problems = map_problems(mp) if os.getenv("MAP_CHECK", "1") == "1" else []
        if problems:
            hold_for_review("Підсумок " + ("тижня" if s["kind"] == "week" else "місяця"), text,
                            _tg_image(mp) or s["image"], problems, EXTRA_MAPS.get(mp))
            done[s["kind"]] = today
            if save:
                save()
            continue
        publish(text, _tg_image(mp) or s["image"], silent=True, extra_images=EXTRA_MAPS.get(mp))
        done[s["kind"]] = today          # підсумок у Telegram вийшов – повтору не буде
        if save:
            save()

        def step(name, fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log(f"{name}: помилка – {e}")
                alert(f"{name} (підсумок): {e}")

        fb_text = build_fb_summary_teaser(s["kind"], rows, s.get("label"))
        step("Facebook", lambda: send_fb_teaser(fb_text, s["image"]))   # фірмова обкладинка (рішення Юлі 9.11)
        step("Threads", lambda: threads_api.post(fb_text, s["image"], log, alert, DRY_RUN,
                                                 image_url=threads_url_for(mp) if mp else None, reply=True,
                                                 extra_urls=[threads_url_for(x) for x in EXTRA_MAPS.get(mp, [])]))
        step("Жовта FB", lambda: post_fb_yellow(build_yellow_summary(s["kind"], rows, s.get("label")),
                                                (ig_version(mp) if mp else None) or s["image"], today,
                                                [ig_version(x) for x in EXTRA_MAPS.get(mp, [])] if mp else None))
        # Instagram (запасний підпис, доки Reels не публікується автоматично – хвиля 2): заклик підписатися
        ig_text = (fb_text.replace(FB_WHITE_CTA, SUB_CTA_IG) + "\n\n"
                   + _tiktok_tags(_rows_places(rows), _rows_luhansk_changed(rows)))
        step("Instagram", lambda: ig_api.post(FB_GRAPH, *fb_page_credentials(), ig_text, s["image"], log, alert,
                                              DRY_RUN, extra_images=([ig_version(mp)] + [ig_version(x) for x in
                                                                     EXTRA_MAPS.get(mp, [])]) if mp else None))
        story_date = (s["label"][0].upper() + s["label"][1:]) if s["kind"] == "month" and s.get("label") \
            else _period_label(start, today)
        step("Сторіз", lambda: make_and_post_story(s["image"], story_date, story_blocks_summary(s["kind"], rows), s["kind"]))
        step("TikTok", lambda: send_tiktok_pack(s["kind"], rows, s["image"], story_date, start, mp))
        done[s["kind"]] = today
        if save:
            save()


# ============================ МІНІ-МАПА ЗМІН ============================


EXTRA_MAPS: dict = {}   # головна мапа → [друга мапа] (дві області або далекі ділянки – рішення Юлі 18 і 22.11.2026)
MAP_FLAGS: dict = {}    # мапа → що не так (назву з тексту не підписано тощо) – перевірка «текст = мапа»
SPLIT_KM = 35.0         # ділянки в межах області далі ~35 км одна від одної – дві мапи


def _map_groups(dates: list[str], oblast_km2: dict) -> list[tuple]:
    """Скільки мап: [(ключ області, геометрія групи або None)]. Дві – якщо змінились обидві області
    або ділянки однієї області далеко одна від одної (> SPLIT_KM)."""
    changed = sorted([k for k in OBLASTS if abs(oblast_km2.get(k) or 0) >= 0.01],
                     key=lambda k: -abs(oblast_km2.get(k) or 0))
    if len(changed) >= 2:
        return [(k, None) for k in changed[:2]]
    if not changed:
        return []
    key = changed[0]
    try:
        import change_map
        from shapely.ops import unary_union
        from shapely.affinity import scale as _scale
        steps = change_map._load_steps([CHANGES_DIR / f"{d}.geojson" for d in dates], key)
        geoms = [g for o, f in steps for g in o + f]
        if not geoms:
            return [(key, None)]
        u = unary_union(geoms)
        parts = [p for p in getattr(u, "geoms", [u]) if not p.is_empty]
        k_ = math.cos(math.radians(u.centroid.y))
        kmg = {id(p): _scale(p, xfact=111.32 * k_, yfact=110.57, origin=(0, 0)) for p in parts}
        clusters: list[list] = []
        for p in parts:   # ділянки ближчі за SPLIT_KM – одна група
            hit = [c for c in clusters if any(kmg[id(p)].distance(kmg[id(q)]) < SPLIT_KM for q in c)]
            clusters = [c for c in clusters if c not in hit] + [[p] + [q for c in hit for q in c]]
        if len(clusters) < 2:
            return [(key, None)]
        clusters.sort(key=lambda c: -sum(q.area for q in c))
        g1 = unary_union(clusters[0])
        g2 = unary_union([q for c in clusters[1:] for q in c])
        return [(key, g1), (key, g2)]
    except Exception as e:  # noqa: BLE001
        log(f"Дві мапи: не вдалося розділити – {e}")
        return [(key, None)]


def _split_files(dates: list[str], key: str, group, tag: str) -> list[Path]:
    """Файли змін лише з ділянками цієї групи (для окремої мапи)."""
    from shapely.geometry import shape
    out_dir = DATA / "split"
    out_dir.mkdir(exist_ok=True)
    out = []
    area = group.buffer(0.02)
    for d in dates:
        src = CHANGES_DIR / f"{d}.geojson"
        dst = out_dir / f"{tag}_{d}.geojson"
        feats = []
        if src.exists():
            feats = [f for f in json.loads(src.read_text())["features"]
                     if f["properties"].get("oblast") == key and shape(f["geometry"]).intersects(area)]
        dst.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        out.append(dst)
    return out


MAP_OBLAST: dict = {}   # шлях мапи → область (для рядка «Донеччина/Луганщина на мапі DeepState» у відео)


def make_change_map(kind: str, dates: list[str], oblast_km2: dict, oblast_places: dict, out_name: str,
                    areas: list[str] | None = None, totals: dict | None = None) -> Path | None:
    """Мапа змін; якщо змінились обидві області або ділянки далеко одна від одної – дві мапи окремими
    картинками повного розміру (друга – у EXTRA_MAPS[головна]). Склеювати в одну не будемо (Юля 22.11)."""
    groups = _map_groups(dates, oblast_km2)
    if len(groups) < 2:
        mp = _make_one_map(kind, dates, oblast_km2, oblast_places, out_name, areas, totals)
        return mp
    import change_map
    pn_all = {k: place_names(dates, k) for k in OBLASTS}
    paths = []
    for i, (key, grp) in enumerate(groups):
        name_i = out_name if i == 0 else out_name.replace(".jpg", f"_{i + 1}.jpg")
        if grp is None:
            km_i = {key: oblast_km2.get(key) or 0}
            mp = _make_one_map(kind, dates, km_i, oblast_places, name_i, areas, totals, key_override=key,
                               video=True)
        else:
            files = _split_files(dates, key, grp, f"g{i + 1}")
            bd = change_map.breakdown(files, key)
            net = round(bd["occupied"] - bd["deoccupied"], 2)
            # назви цієї групи – ті з загального списку, чий населений пункт поруч із її ділянками
            if _ALL_POINTS is None:
                _nearest_settlement_geom(grp)   # завантажує перелік населених пунктів
            pts = {}
            for nm, pt, *_ in (_ALL_POINTS or []):   # для тезок – найближчий до групи
                if nm not in pts or grp.distance(pt) < grp.distance(pts[nm]):
                    pts[nm] = pt
            nm_i = Names([n for n in pn_all[key] if (n in pts and grp.distance(pts[n]) < 0.15)] or list(pn_all[key]))
            nm_i.occ = [x for x in getattr(pn_all[key], "occ", []) if x in nm_i]
            nm_i.deocc = [x for x in getattr(pn_all[key], "deocc", []) if x in nm_i]
            mp = _make_one_map(kind, dates, {key: net}, oblast_places, name_i, areas,
                               {key: (bd["occupied"], bd["deoccupied"])}, key_override=key, files_override=files,
                               pn_override=nm_i, video=True)   # 24.11.2026: у відео – усі мапи по черзі
        if mp:
            paths.append(mp)
    if not paths:
        return None
    if len(paths) > 1:
        EXTRA_MAPS[paths[0]] = paths[1:]
        log(f"Дві мапи: {', '.join(p.name for p in paths)}")
    return paths[0]


def _make_one_map(kind: str, dates: list[str], oblast_km2: dict, oblast_places: dict, out_name: str,
                  areas: list[str] | None = None, totals: dict | None = None, key_override: str | None = None,
                  files_override: list | None = None, pn_override=None, video: bool = True) -> Path | None:
    """Мапа нових ділянок за добу/період. oblast_km2: {key: км² (додатне – окупованими)}."""
    try:
        import change_map
        key = key_override or max(OBLASTS, key=lambda k: abs(oblast_km2.get(k) or 0))
        km = oblast_km2.get(key) or 0
        if abs(km) < 0.01:
            return None
        pl = oblast_places.get(key) or []
        cities = [re.sub(r"\s*\(.*?\)", "", p).strip() for p in pl if is_city(p)]
        names = list(dict.fromkeys(re.sub(r"\s*\(.*?\)", "", p).strip() for p in pl))
        pn = pn_override if pn_override is not None else place_names(dates, key)
        # обидва напрямки: у заголовку назви біля окупованих – рожево-червоним, біля деокупованих – блакитним
        blue_nm = [x for x in pn.deocc if x not in pn.occ] if isinstance(pn, Names) and pn.both else []
        red_nm = [x for x in pn.occ if x not in pn.deocc] if isinstance(pn, Names) and pn.both else []
        if pn:
            title = names_title(pn)
        elif cities:
            title = "Поблизу: " + cities[0]
        elif names:
            title = "Поблизу: " + ", ".join(names[:2])
        else:
            title = OBLASTS[key]["short"]
        word = {"day": "за добу", "week": "за тиждень", "month": "за місяць"}[kind]
        # дата на самій мапі (рішення Юлі 14.11.2026): мапу пересилають і скріншотять без тексту допису
        _dd = lambda d_: f"{d_[8:10]}.{d_[5:7]}"
        # підсумки: від стану мапи на початок періоду до стану на кінець – як «15.11–16.11» у добовому
        # заголовку Telegram і «9–16 листопада» на обкладинці (Юля 16.11.2026: на мапі було «10.11–16.11»)
        _start = (datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        word += f", {_dd(dates[-1])}" if kind == "day" else f" {_dd(_start)}–{_dd(dates[-1])}"
        if kind == "month":   # 24.11.2026: «за листопад», а не «за місяць 31.10–30.11»
            word = "за " + MONTHS_UA[int(dates[-1][5:7]) - 1]
        sub = f"{fmt_km2(abs(km))} км² позначено {'окупованими' if km > 0 else 'деокупованими'} {word}"
        files = files_override or [CHANGES_DIR / f"{d}.geojson" for d in dates]
        bd = change_map.breakdown(files, key)
        if totals and key in totals:   # доба: ті самі цифри, що в тексті й сторіз (а не перерахунок площі)
            bd = {**bd, "occupied": totals[key][0], "deoccupied": totals[key][1]}
        elif kind != "day" and (bd["occupied"] >= 0.01 or bd["deoccupied"] >= 0.01):
            # 16.11.2026: підсумки – та сама цифра, що в тексті, на обкладинці й у відео (було 19,25 на мапі
            # і 19,23 на обкладинці: сума добових проти чистого результату за тиждень)
            _net = round(bd["occupied"] - bd["deoccupied"], 2)
            sub = f"{fmt_km2(abs(_net))} км² позначено {'окупованими' if _net > 0 else 'деокупованими'} {word}"
        if bd["occupied"] >= 0.01 and bd["deoccupied"] >= 0.01:   # обидва напрямки – дві цифри
            sub = (f"позначено окупованими {fmt_km2(bd['occupied'])} км², "
                   f"деокупованими – {fmt_km2(bd['deoccupied'])} км² {word}")
        if kind != "day" and video and os.getenv("NO_VIDEO_FRAMES") != "1":   # кадри для відео TikTok/Shorts
            frames = [("_base_v.jpg", {"show_new": False}), ("_v.jpg", {})]
            # 1–2. ділянки «позначили й зняли» блимають по черзі: червоним, потім блакитним (або навпаки)
            for f in [*DATA.glob(out_name.replace(".jpg", "_step*_v.jpg")), DATA / out_name.replace(".jpg", "_steps.json")]:
                f.unlink(missing_ok=True)
            ends = change_map.step_groups(files, key)   # окупація й деокупація – по черзі, як відбувалися
            info = change_map.step_info(files, key)
            # 24.11.2026 (Юля): дата кожного кроку – дрібно під назвою громади біля ділянки (не плашкою в куті)
            marks = []
            # 24.11.2026: якщо день зі змінами один (або мапа – окрема група ділянок), кроків немає – але дата
            # однаково потрібна; назви файлів груп мають префікс («g1_2026-11-18») – беремо лише дату
            for i, end in enumerate(ends or ([len(info)] if info else [])):
                since_ = ends[i - 1] if (ends and i) else 0
                ds = [dd[-10:] for dd, _ in info[since_:end]]
                if ds:
                    a_, b_ = date.fromisoformat(ds[0]), date.fromisoformat(ds[-1])
                    dm = lambda x: f"{x.day}.{x.month:02d}"   # noqa: E731
                    marks.append((since_, end, dm(a_) if a_ == b_ else f"{a_.day}–{dm(b_)}"
                                  if a_.month == b_.month else f"{dm(a_)}–{dm(b_)}"))
            frames = [(sfx, {**kw, "date_marks": marks}) for sfx, kw in frames]
            for i, end in enumerate(ends):
                kw = {"upto": end, "since": ends[i - 1] if i else 0, "date_marks": marks}
                frames.append((f"_step{i + 1}_v.jpg", kw))
                frames.append((f"_step{i + 1}_off_v.jpg", {**kw, "hide_step": True}))   # для блимання
            # скільки секунд показувати кожен етап – пропорційно до днів (від нього до наступного), мінімум 2,5 с
            starts = [info[(ends[i - 1] if i else 0)][0] for i in range(len(ends))]
            bounds = [dates.index(d) if d in dates else 0 for d in starts] + [len(dates)]
            total = 5.0 if kind == "week" else 7.0   # 24.11.2026: коротше – ціль 15–20 с на все відео
            # 16.11.2026 (Юля): загальний час не збільшуємо – ділимо його пропорційно до днів; кожен крок
            # щонайменше 1,2 с (щоб встигнути побачити блимання), а тоді приводимо суму назад до total
            raw = [max(1.3, total * (bounds[i + 1] - bounds[i]) / max(1, len(dates))) for i in range(len(ends))]
            secs = [round(x * total / sum(raw), 2) for x in raw]
            (DATA / out_name.replace(".jpg", "_steps.json")).write_text(json.dumps(
                [[out_name.replace(".jpg", f"_step{i + 1}_v.jpg"), sec, out_name.replace(".jpg", f"_step{i + 1}_off_v.jpg")]
                 for i, sec in enumerate(secs)]))
            MAP_OBLAST[str(DATA / out_name)] = key
            v_names = sorted(list(pn), key=lambda n: not is_city(n))[:3] if pn else []
            title_v = ("Поблизу: " + " · ".join(v_names)) if v_names and title.startswith("Поблизу: ") else title
            for suffix, kw in frames:
                try:
                    change_map.render(files, DATA / out_name.replace(".jpg", suffix), title_v, sub, oblast_key=key,
                                      height=1920, must_label=pn, blue_label=blue_nm, red_label=red_nm, **kw)
                except Exception as e:  # noqa: BLE001
                    log(f"Вертикальна мапа для відео: {e}")
        flags = []
        try:   # 4:5 – для Threads, Instagram і жовтої FB
            change_map.render(files, DATA / out_name.replace(".jpg", "_45.jpg"), title, sub, oblast_key=key,
                              height=1350, must_label=pn, blue_label=blue_nm, red_label=red_nm)
            flags += [f"4:5: {x}" for x in change_map.LAST_FLAGS]
        except Exception as e:  # noqa: BLE001
            log(f"Мапа 4:5: {e}")
        out = change_map.render(files, DATA / out_name, title, sub, oblast_key=key, must_label=pn,
                                blue_label=blue_nm, red_label=red_nm)
        flags += list(change_map.LAST_FLAGS)
        if out:
            MAP_FLAGS[out] = flags
            if flags:
                log(f"Мапа {out.name}: {'; '.join(flags)}")
        return out
    except Exception as e:  # noqa: BLE001
        log(f"Мапа змін: не вдалося – {e}")
        return None


def _release_image(img: Path) -> str:
    """Запасне публічне посилання на мапу: GitHub Releases (тег maps), лишаємо 10 останніх."""
    import json as _json
    import subprocess
    repo = os.getenv("GITHUB_REPOSITORY", "Anti-Crisis-Media-Center/deepstate-tracker")
    name = f"map_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    dst = img.with_name(name)
    dst.write_bytes(img.read_bytes())
    if subprocess.run(["gh", "release", "view", "maps", "-R", repo], capture_output=True).returncode:
        subprocess.run(["gh", "release", "create", "maps", "-R", repo, "--title", "Мапи (тимчасові файли)",
                        "--notes", "Запасні копії мап для Threads. Бот зберігає лише 10 останніх."], check=True)
    subprocess.run(["gh", "release", "upload", "maps", str(dst), "-R", repo, "--clobber"], check=True)
    assets = _json.loads(subprocess.run(["gh", "release", "view", "maps", "-R", repo, "--json", "assets"],
                                        check=True, capture_output=True, text=True).stdout)["assets"]
    for a in sorted(assets, key=lambda a: a["name"])[:-10]:
        subprocess.run(["gh", "release", "delete-asset", "maps", a["name"], "-R", repo, "-y"], check=False)
    return f"https://github.com/{repo}/releases/download/maps/{name}"


def ig_version(img: Path | None) -> Path | None:
    """Для Instagram беремо 4:5-версію мапи, якщо вона є."""
    if not img:
        return img
    p = img.with_name(img.stem + "_45.jpg")
    return p if p.exists() else img


def threads_url_for(img: Path) -> str | None:
    """4:5-версію мапи (якщо є) віддаємо в Threads. Threads бере картинку за публічною адресою: мапу завантажуємо у Facebook,
    а якщо там збій (напр., токен) – у GitHub Releases. Тоді мапа в Threads не зникає.
    Беремо версію 4:5 (_45.jpg), якщо вона є: у каруселі Threads тоді видно край наступної картки."""
    v45 = img.with_name(img.stem + "_45.jpg")
    img = v45 if v45.exists() else img
    try:
        pid, ptok = fb_page_credentials()
        if pid and ptok:
            return ig_api._public_url(FB_GRAPH, pid, ptok, img)
    except Exception as e:  # noqa: BLE001
        log(f"Threads: мапу через Facebook не підготовлено – {e}; пробую GitHub")
    try:
        return _release_image(img)
    except Exception as e:  # noqa: BLE001
        log(f"Threads: не вдалося підготувати мапу – {e}")
        return None


def _vertical(img: Path, out: Path) -> Path:
    from PIL import Image
    canvas = Image.new("RGB", (1080, 1920), (13, 20, 40))
    canvas.paste(Image.open(img).convert("RGB").resize((1080, 1080)), (0, 420))
    canvas.save(out, "JPEG", quality=90)
    return out


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
                log(f"Зараз {now:%H:%M} за Києвом – ще зарано навіть для очікування. Вихід.")
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
        "neighbors": neighbors_by_oblast(deepstate),
    }

    if base_state is None:
        save_json(current_path, new_state)
        log("Це перший запуск: знімок збережено як точку відліку. Перший пост буде завтра о "
            f"{PUBLISH_HOUR}:00.")
        return 0

    results = {}
    for key, cfg in OBLASTS.items():
        prev = geom_from_json(base_state["occupied"].get(key))
        results[key] = compare(occupied[key], prev, places, cfg["name_ua"], unknown.get(key))
        log(f"{cfg['title']}: +{results[key]['newly_km2']} / −{results[key]['deocc_km2']} км²")

    period = fmt_date(today)   # одна дата, як у Threads/FB/сторіз (рішення Юлі 17.11.2026)
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
                "cities": cities_near(added, places, cfg["name_ua"]),
            }
            log(f"{cfg['title']}: сіра зона {grey[key]['net_km2']:+} км² (нових ділянок {grey[key]['added_km2']} км²)")
    else:
        log("У попередньому знімку немає сірої зони – її зміну покажемо з наступного дня.")

    # Геометрія змін – для мап і звірки повідомлень (16.11.2026: зберігаємо ДО тексту – раніше назви
    # й повідомлення для Telegram рахувалися ще без файлу доби)
    feats = []
    for key, r in results.items():
        for kind in ("newly", "deocc", "grey"):
            for p in r[f"{kind}_polys"]:
                feats.append({"type": "Feature", "geometry": mapping(p),
                              "properties": {"oblast": key, "change": {"newly": "occupied", "deocc": "deoccupied",
                                                                       "grey": "to_grey"}[kind],
                                             "km2": round(area_km2(p), 3)}})
    if feats:
        save_json(CHANGES_DIR / f"{today}.geojson", {"type": "FeatureCollection", "features": feats})

    messages = get_messages(base_state.get("deepstate_id"), deepstate.get("id"), oblasts, places)
    messages = localize_messages(messages, today, results)
    log(f"Повідомлень DeepState про Донеччину/Луганщину за період: {len(messages)}")

    tg_names = {k: place_names([today], k) for k in OBLASTS
                if results[k]["newly_km2"] or results[k]["deocc_km2"]}
    text = build_text(period, results, grey, None, tg_names)   # повідомлення DeepState – лише в історію (звірка – хвиля 2)

    # Зберігаємо стан ДО публікації: якщо Telegram впаде, дані не загубляться
    if current_state and current_state["date"] < today:
        save_json(base_path, current_state)
    save_json(current_path, new_state)
    save_grey_snapshot(today, unknown)


    upsert_history({
        "date": today,
        "compared_with": base_state["date"],
        "deepstate_id": deepstate.get("id"),
        **{f"{k}_occupied_km2": new_state["occupied_km2"][k] for k in OBLASTS},
        **{f"{k}_occupied_percent": new_state["occupied_percent"][k] for k in OBLASTS},
        **{f"{k}_newly_occupied_km2": results[k]["newly_km2"] for k in OBLASTS},
        **{f"{k}_deoccupied_km2": results[k]["deocc_km2"] for k in OBLASTS},
        **{f"{k}_to_grey_km2": results[k]["grey_km2"] for k in OBLASTS},
        **{f"{k}_to_grey_places": "; ".join(results[k]["grey_places"]) for k in OBLASTS},
        **{f"{k}_newly_places": "; ".join(results[k]["newly_places"]) for k in OBLASTS},
        **{f"{k}_deoccupied_places": "; ".join(results[k]["deocc_places"]) for k in OBLASTS},
        **{f"{k}_unknown_km2": new_state["unknown_km2"][k] for k in OBLASTS},
        **{f"{k}_unknown_net_km2": (grey.get(k) or {}).get("net_km2", "") for k in OBLASTS},
        **{f"{k}_penetration_cities": "; ".join((grey.get(k) or {}).get("cities") or []) for k in OBLASTS},
        "threads_followers": threads_api.followers(log) or "",
        "instagram_followers": ig_api.followers(FB_GRAPH, *fb_page_credentials(), log) or "",
        "messages_count": len(messages),
        "messages": " | ".join(f"{m['time']:%d.%m %H:%M} {m['text']}" for m in messages),
    })

    day_map = make_change_map("day", [today], {k: results[k]["newly_km2"] - results[k]["deocc_km2"] for k in OBLASTS},
                              {k: results[k]["newly_places"] + results[k]["deocc_places"] for k in OBLASTS}, "map_day.jpg",
                              totals={k: (results[k]["newly_km2"], results[k]["deocc_km2"]) for k in OBLASTS})
    # без звуку (рішення Юлі 12.11: звук лише в «Хвилини мовчання» о 9:00). Картинка (рішення 14.11.2026): у день змін –
    # мапа (як у Threads, сторіз і FB), у тихий день – фірмова обкладинка
    problems = map_problems(day_map) if os.getenv("MAP_CHECK", "1") == "1" else []
    if problems:
        hold_for_review("Добовий допис", text, _tg_image(day_map) or COVER_IMAGE, problems, EXTRA_MAPS.get(day_map))
        new_state["published"] = True   # щоб запасні запуски не публікували його самі
        new_state["held_for_review"] = True
        save_json(current_path, new_state)
        run_summaries(now, new_state, lambda: save_json(current_path, new_state))
        save_json(current_path, new_state)
        return 0
    publish(text, _tg_image(day_map) or COVER_IMAGE, silent=True, extra_images=EXTRA_MAPS.get(day_map))
    # Позначаємо «опубліковано» одразу після Telegram: якщо далі щось впаде,
    # запасний запуск не повторить пост ще раз.
    new_state["published"] = True
    save_json(current_path, new_state)

    def step(name, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 – одна платформа не має зупиняти інші
            log(f"{name}: помилка – {e}")
            alert(f"{name}: {e}")

    fb_text = build_fb_teaser(fmt_date(today), results,
                              {k: place_names([today], k) for k in OBLASTS
                               if results[k]["newly_km2"] or results[k]["deocc_km2"]})
    day_places = [p for k in OBLASTS for p in results[k]["newly_places"] + results[k]["deocc_places"]]
    day_places += [c for k in OBLASTS for c in ((grey or {}).get(k) or {}).get("cities") or []]
    lug = bool(results["luhansk"]["newly_km2"] or results["luhansk"]["deocc_km2"])
    # «тихий» день для соцмереж – лише за змінами на мапі. Повідомлення DeepState без змін на мапі
    # показуємо тільки в Telegram, тож через них Instagram не публікує пост «без змін» (виправлення 12.11.2026)
    quiet = (all(not results[k]["newly_km2"] and not results[k]["deocc_km2"] and not results[k]["grey_km2"]
                 for k in OBLASTS)
             and not any((grey.get(k) or {}).get("cities") for k in OBLASTS))
    if quiet:  # день без змін: Facebook і Instagram пропускаємо; Threads – щодня (рішення Юлі 9.11)
        log("Змін немає – дописи у Facebook і Instagram пропускаємо; у Threads – обкладинка.")
        step("Threads", lambda: threads_api.post(fb_text, COVER_IMAGE, log, alert, DRY_RUN))
    else:
        if FB_DAILY_POSTS:   # щоденні дописи в стрічку білої сторінки вимкнено (рішення Юлі 9.11)
            step("Facebook", lambda: send_fb_teaser(fb_text, day_map or COVER_IMAGE))
        else:
            log("Facebook: щоденні дописи в стрічку вимкнено (FB_DAILY_POSTS=0), лише сторіз.")
        step("Threads", lambda: threads_api.post(fb_text, COVER_IMAGE, log, alert, DRY_RUN,
                                                 image_url=threads_url_for(day_map) if day_map else None,
                                                 extra_urls=[threads_url_for(x) for x in EXTRA_MAPS.get(day_map, [])]))
    # жовта FB (з 25.11.2026): щодня о 8:00 – лише дні зі змінами (дні без змін – вимкненим перемикачем FB_YELLOW_QUIET)
    yellow_names = {k: place_names([today], k) for k in OBLASTS if results[k]["newly_km2"] or results[k]["deocc_km2"]}
    if not quiet:
        step("Жовта FB", lambda: post_fb_yellow(build_fb_teaser(fmt_date(today), results, yellow_names, FB_YELLOW_CTA),
                                                ig_version(day_map) or COVER_IMAGE, today,
                                                [ig_version(x) for x in EXTRA_MAPS.get(day_map, [])] if day_map else None))
    elif FB_YELLOW_QUIET:
        step("Жовта FB", lambda: post_fb_yellow(build_fb_teaser(fmt_date(today), results, {}, FB_YELLOW_CTA),
                                                COVER_IMAGE, today))
    step("TikTok-позначка", lambda: tiktok_daily_flag(results, grey))
    if not quiet and IG_DAILY_POSTS:   # у стрічку Instagram щодня не публікуємо – лише сторіз (рішення Юлі 9.11)
        step("Instagram", lambda: ig_api.post(FB_GRAPH, *fb_page_credentials(),
                                              fb_text + "\n\n" + _tiktok_tags(day_places, lug),
                                              COVER_IMAGE, log, alert, DRY_RUN,
                                              extra_images=[ig_version(day_map)] if day_map else None))
    step("Сторіз", lambda: make_and_post_story(COVER_IMAGE, _date_label(now.date()), story_blocks_daily(results,
                                                     {k: place_names([today], k) for k in OBLASTS})))
    if now.date().weekday() == WEEKLY_WEEKDAY and not DRY_RUN:
        step("Маркер Threads", lambda: threads_api.refresh_token(log, alert))

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
