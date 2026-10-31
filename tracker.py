"""
DeepState: зміни за добу — Донецька і Луганська області.

Що робить скрипт (раз на добу, через GitHub Actions):
1. Завантажує актуальну карту DeepState.
2. Вирізає окуповані території (включно з ОРДЛО) в межах Донецької та Луганської областей.
3. Порівнює з попереднім збереженим знімком: що додалося (окуповані) і що зникло (деокуповані).
4. Знаходить найближчі населені пункти до кожної зміни.
5. Складає текст за шаблоном (без ШІ) і надсилає пост із картинкою в Telegram.
6. Зберігає новий знімок і рядок в історію (data/history.csv).

Налаштування — у блоці CONFIG нижче та в секретах репозиторію.
"""

from __future__ import annotations

import csv
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
PUBLISH_HOUR = int(os.getenv("PUBLISH_HOUR", "9"))

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

# Ділянки змін, менші за цю площу, ігноруємо (шум від перемальовування меж)
MIN_CHANGE_KM2 = float(os.getenv("MIN_CHANGE_KM2", "0.01"))

# Населені пункти: шукаємо в радіусі, показуємо не більше N на тип зміни
PLACES_RADIUS_KM = 5.0
PLACES_PER_CHANGE = 2      # найближчих на кожну окрему ділянку
PLACES_MAX_IN_TEXT = 6     # загалом у тексті на тип зміни в області

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

EDITOR_NOTE = (
    "<blockquote><i>Примітка редакції: це автоматизований аналіз змін на карті "
    "DeepState. Він показує, як змінилися позначення на карті порівняно з "
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
    щоб ідентичні карти давали нульову різницю."""
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


def occupied_by_oblast(deepstate: dict, oblasts: dict) -> dict:
    occupied = []
    for f in deepstate["map"]["features"]:
        name = (f.get("properties") or {}).get("name") or ""
        if not f.get("geometry") or not any(m in name for m in OCCUPIED_MARKERS):
            continue
        try:
            g = clean_polygonal(shape(f["geometry"]))
        except Exception:  # noqa: BLE001
            continue
        if g is not None:
            occupied.append(g)
    if not occupied:
        raise RuntimeError("На карті DeepState не знайдено жодного окупованого шару — схоже, змінився формат даних")
    log(f"Окупованих шарів на карті: {len(occupied)}")

    result = {}
    for key, oblast in oblasts.items():
        parts = []
        for g in occupied:
            if not g.intersects(oblast):
                continue
            inter = clean_polygonal(g.intersection(oblast))
            if inter is not None:
                parts.append(inter)
        merged = normalize(unary_union(parts)) if parts else None
        result[key] = merged
        log(f"{OBLASTS[key]['title']}: позначено окупованими {fmt_km2(area_km2(merged))} км² "
            f"({area_km2(merged) / area_km2(oblast) * 100:.2f}% області)")
    return result


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
                candidates.append((d, pl["name"]))
        for d, name in sorted(candidates)[:PLACES_PER_CHANGE]:
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


def change_sentence(km2: float, kind: str, places: list[str]) -> str:
    s = f"на карті DeepState {fmt_km2(km2)} км² територій змінили позначення на {kind}"
    if SHOW_FOOTBALL_FIELDS:
        s += f" (≈ {football_fields(km2)} футбольних полів)"
    s += "."
    if places:
        label = "Найближчий населений пункт" if len(places) == 1 else "Найближчі населені пункти"
        s += f" {label}: {', '.join(places)}."
    return s


def build_text(period: str, results: dict) -> str:
    lines = ["<b>DeepState: зміни за добу</b>", "", f"Період: {period}", ""]
    for key, cfg in OBLASTS.items():
        r = results[key]
        if r["newly_km2"] == 0 and r["deocc_km2"] == 0:
            lines.append(f"<b>{cfg['title']}:</b> змін не зафіксовано.")
        else:
            parts = []
            if r["newly_km2"] > 0:
                parts.append(change_sentence(r["newly_km2"], "окуповані", r["newly_places"]))
            if r["deocc_km2"] > 0:
                parts.append(change_sentence(r["deocc_km2"], "деокуповані", r["deocc_places"]))
            body = " ".join(parts)
            lines.append(f"<b>{cfg['title']}:</b> {body[0].upper()}{body[1:]}")
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
            body = "змін не зафіксовано."
        else:
            parts = []
            if newly > 0:
                parts.append(f"{fmt_km2(newly)} км² територій змінили позначення на окуповані")
            if deocc > 0:
                parts.append(f"{fmt_km2(deocc)} км² — на деокуповані")
            body = f"{word.lower()} на карті DeepState " + ", ".join(parts) + "."
            body += f" Зміни фіксувалися в {days} із {len(rows)} днів."
            newly_pl = _places(rows, f"{key}_newly_places")
            deocc_pl = _places(rows, f"{key}_deoccupied_places")
            if newly_pl:
                body += f" Окуповані — поблизу населених пунктів: {', '.join(newly_pl)}."
            if deocc_pl:
                body += f" Деокуповані — поблизу населених пунктів: {', '.join(deocc_pl)}."
            body = body[0].upper() + body[1:]
        last = rows[-1]
        pct = _f(last.get(f"{key}_occupied_percent"))
        total = f" Загалом позначено окупованими {fmt_km2(_f(last[f'{key}_occupied_km2']))} км²"
        total += f" ({str(round(pct, 1)).replace('.', ',')}% області)." if pct else "."
        lines.append(f"<b>{cfg['title']}:</b> {body}{total}")
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


def run_summaries(now: datetime, state: dict) -> None:
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
        publish(text, s["image"])
        done[s["kind"]] = today


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
        if now.hour < PUBLISH_HOUR:
            log(f"Зараз {now:%H:%M} за Києвом — ще рано (публікація о {PUBLISH_HOUR}:00). Вихід.")
            return 0
        if all_done(current_state):
            log("Сьогодні вже все опубліковано. Вихід.")
            return 0
        if current_state and current_state.get("date") == today and current_state.get("published"):
            # Щоденний пост уже вийшов, лишилися тільки підсумки
            run_summaries(now, current_state)
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
    occupied = occupied_by_oblast(deepstate, oblasts)

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
    text = build_text(period, results)

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
    })

    publish(text, COVER_IMAGE)
    new_state["published"] = True
    save_json(current_path, new_state)

    run_summaries(now, new_state)
    save_json(current_path, new_state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"ПОМИЛКА: {e}")
        alert(str(e))
        raise
