"""Листівки до визначних дат АКМЦ: календар → превʼю → публікація в соцмережах.

Як це працює (узгоджено з Юлею 18.11.2026):
• Дати — у Google-календарі «Визначні події АКМЦ». В описі події тексти під мітками,
  кожна мітка окремим рядком: #TG, #FB, #IG, #THREADS, #СТОРІЗ (+ необов'язкові #ALT і #СТОП).
  Немає мітки — у цю мережу нічого не йде. Текст над першою міткою — нотатки, не публікується.
  У розділі #THREADS рядок «Тема: …» стає темою допису (topic_tag), а не текстом.
• Картки — на Диску АКМЦ, папка «Визначні дати АКМЦ (листівки для соцмереж)», підпапка
  «ММ.ДД Назва». Формат бот визначає за розміром: 1:1 → Telegram і Facebook, 4:5 → Instagram
  і Threads, 9:16 → сторіз (Facebook + Instagram). Файли зі словом «вручну» в назві бот не бере.
• Напередодні о 18:00 — превʼю в гілку «Тести» Суперчату (кожна мережа окремо + перелік ✅/❌/⚠️
  і кнопка «❌ Не публікувати»). О 8:30 — публікація і звіт із посиланнями. Після публікації
  папка дати переїжджає в «Опубліковано РРРР», тож наступного року старий текст сам не вийде.
• Facebook — поки лише біла сторінка «У фокусі». Telegram — канал @acmc_ua, без звуку.
• Тестовий режим (KARTKY_TEST=1): у соцмережі нічого не йде, вранці — «Опублікувала б: …».

Режими (KARTKY_MODE): preview | publish | poll. Дата — KARTKY_DATE (РРРР-ММ-ДД), інакше:
preview — завтра, publish — сьогодні.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ig_api  # noqa: E402  (спільні помічники Instagram із DeepState-трекера)

KYIV = ZoneInfo("Europe/Kyiv")
CALENDAR_ID = "4f25090a1ec5507f89f9a45f76d7691a824918276a1109522baf0ea5931a47fc@group.calendar.google.com"
ROOT_FOLDER = "Визначні дати АКМЦ"
FB_GRAPH = "https://graph.facebook.com/" + (os.getenv("FB_GRAPH_VERSION", "v24.0").strip() or "v24.0")
THREADS_GRAPH = "https://graph.threads.net/v1.0"
STATE_FILE = ROOT / "data" / "kartky_state.json"
WORK = ROOT / "data" / "kartky_tmp"

PUBLISH_AT = (8, 30)
PREVIEW_AT = (18, 0)

MODE = os.getenv("KARTKY_MODE", "preview").strip()
TEST = os.getenv("KARTKY_TEST", "1").strip() != "0"
FORCE = os.getenv("KARTKY_FORCE", "0").strip() == "1"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
REVIEW_CHAT = os.getenv("KARTKY_CHAT_ID", "-1003946430136").strip()      # Суперчат АКМЦ
REVIEW_THREAD = os.getenv("KARTKY_THREAD_ID", "6").strip()              # гілка «Тести»
CHANNEL = os.getenv("TELEGRAM_CHAT_ID", "@acmc_ua").strip() or "@acmc_ua"

MARKERS = {"#TG": "tg", "#FB": "fb", "#IG": "ig", "#THREADS": "threads", "#СТОРІЗ": "stories",
           "#STORIES": "stories", "#ALT": "alt", "#СТОП": "stop", "#STOP": "stop"}
NET_LABEL = {"tg": "Telegram", "fb": "Facebook (біла сторінка)", "ig": "Instagram",
             "threads": "Threads", "stories": "Сторіз (Facebook + Instagram)"}
NET_FORMAT = {"tg": "square", "fb": "square", "ig": "45", "threads": "45", "stories": "story"}
FORMAT_LABEL = {"square": "1:1", "45": "4:5", "story": "9:16"}
LIMITS = {"tg": 1024, "fb": 60000, "ig": 2200, "threads": 500}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня",
              "вересня", "жовтня", "листопада", "грудня"]


LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[{datetime.now(KYIV):%Y-%m-%d %H:%M:%S}] {msg}"
    LOG.append(line)
    print(line, flush=True)


def save_log() -> None:
    """Останній запуск — у data/kartky_last_run.txt (щоб розбиратися без доступу до Actions)."""
    try:
        (ROOT / "data").mkdir(exist_ok=True)
        (ROOT / "data" / "kartky_last_run.txt").write_text(
            f"Режим: {MODE}, тест: {TEST}, force: {FORCE}\n" + "\n".join(LOG) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} ({WEEKDAYS[d.weekday()]})"


# ============================ СТАН ============================

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ============================ GOOGLE ============================

_SESSION = None


def google():
    global _SESSION
    if _SESSION is None:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        info = json.loads(os.environ["GOOGLE_SA_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=[
            "https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/drive"])
        _SESSION = AuthorizedSession(creds)
    return _SESSION


def events_on(d: date) -> list[dict]:
    start = datetime(d.year, d.month, d.day, tzinfo=KYIV)
    r = google().get(f"https://www.googleapis.com/calendar/v3/calendars/{quote(CALENDAR_ID)}/events",
                     params={"timeMin": start.isoformat(), "timeMax": (start + timedelta(days=1)).isoformat(),
                             "singleEvents": "true", "orderBy": "startTime"}, timeout=30)
    r.raise_for_status()
    out = []
    for ev in r.json().get("items", []):
        if ev.get("status") == "cancelled":
            continue
        day = (ev.get("start") or {}).get("date") or ((ev.get("start") or {}).get("dateTime") or "")[:10]
        if day == d.isoformat():
            out.append(ev)
    return out


def drive_list(q: str, fields: str = "files(id,name,mimeType,parents,modifiedTime,imageMediaMetadata(width,height))") -> list:
    r = google().get("https://www.googleapis.com/drive/v3/files",
                     params={"q": q, "fields": fields, "pageSize": 200, "supportsAllDrives": "true",
                             "includeItemsFromAllDrives": "true"}, timeout=30)
    r.raise_for_status()
    return r.json().get("files", [])


FOLDER = "application/vnd.google-apps.folder"


def root_folder() -> dict | None:
    found = drive_list(f"mimeType='{FOLDER}' and trashed=false and name contains '{ROOT_FOLDER}'")
    return found[0] if found else None


def date_folders(root: dict, d: date) -> list[dict]:
    prefix = f"{d.month:02d}.{d.day:02d}"
    subs = drive_list(f"'{root['id']}' in parents and trashed=false and mimeType='{FOLDER}'")
    return [f for f in subs if f["name"].strip().startswith(prefix)]


def _norm_title(s: str) -> str:
    return re.sub(r"[^\w]+", " ", s.lower().replace("~", "")).strip()


def pick_folder(folders: list[dict], title: str, n_events: int) -> tuple[dict | None, str | None]:
    if not folders:
        return None, None
    if len(folders) == 1 and n_events == 1:
        return folders[0], None
    t = _norm_title(title)
    match = [f for f in folders if t and t in _norm_title(f["name"])]
    if len(match) == 1:
        return match[0], None
    return None, "кілька папок на цю дату — не знаю, яку взяти (назва папки має містити назву події)"


def classify(w: int, h: int) -> str | None:
    if not w or not h:
        return None
    r = w / h
    if 0.95 <= r <= 1.05:
        return "square"
    if 0.76 <= r <= 0.84:
        return "45"
    if 0.53 <= r <= 0.60:
        return "story"
    return None


def folder_cards(folder: dict) -> tuple[dict, list[str]]:
    """{формат: файл} і зауваження (два файли одного формату, невідомі розміри)."""
    files = drive_list(f"'{folder['id']}' in parents and trashed=false and mimeType!='{FOLDER}'")
    by_fmt: dict[str, list] = {}
    notes = []
    for f in files:
        if "вручну" in f["name"].lower():
            continue
        m = f.get("imageMediaMetadata") or {}
        fmt = classify(m.get("width"), m.get("height"))
        if not fmt:
            if f.get("mimeType", "").startswith("image/"):
                notes.append(f"файл «{f['name']}» має незвичний розмір {m.get('width')}×{m.get('height')} — пропускаю")
            continue
        by_fmt.setdefault(fmt, []).append(f)
    cards = {}
    for fmt, lst in by_fmt.items():
        if len(lst) == 1:
            cards[fmt] = lst[0]
        else:
            names = ", ".join(f"«{x['name']}»" for x in lst)
            notes.append(f"кілька карток {FORMAT_LABEL[fmt]} ({names}) — не знаю, яку взяти. "
                         f"Зайву перейменуй, додавши слово «вручну»")
            cards[fmt] = None
    return cards, notes


def download(f: dict) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"{f['id']}{Path(f['name']).suffix or '.png'}"
    if not out.exists():
        r = google().get(f"https://www.googleapis.com/drive/v3/files/{f['id']}",
                         params={"alt": "media", "supportsAllDrives": "true"}, timeout=120)
        r.raise_for_status()
        out.write_bytes(r.content)
    return out


def prepared(f: dict, fmt: str) -> Path:
    """JPEG точного розміру: 1080×1080, 1080×1350 (Instagram не бере 1080×1351), 1080×1920."""
    from PIL import Image
    size = {"square": (1080, 1080), "45": (1080, 1350), "story": (1080, 1920)}[fmt]
    src = Image.open(download(f)).convert("RGB")
    w, h = src.size
    scale = max(size[0] / w, size[1] / h)
    img = src.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    img = img.crop((left, top, left + size[0], top + size[1]))
    out = WORK / f"{f['id']}_{fmt}.jpg"
    img.save(out, "JPEG", quality=93)
    return out


def archive_folder(root: dict, folder: dict, year: int) -> str:
    name = f"Опубліковано {year}"
    found = [f for f in drive_list(f"'{root['id']}' in parents and trashed=false and mimeType='{FOLDER}'")
             if f["name"] == name]
    if found:
        arch = found[0]
    else:
        r = google().post("https://www.googleapis.com/drive/v3/files", params={"supportsAllDrives": "true"},
                          json={"name": name, "mimeType": FOLDER, "parents": [root["id"]]}, timeout=30)
        r.raise_for_status()
        arch = r.json()
    r = google().patch(f"https://www.googleapis.com/drive/v3/files/{folder['id']}",
                       params={"addParents": arch["id"], "removeParents": root["id"], "supportsAllDrives": "true"},
                       timeout=30)
    r.raise_for_status()
    return name


# ============================ ОПИС ПОДІЇ ============================

ALLOWED = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "s": "s", "strike": "s", "a": "a"}


def unwrap_link(href: str) -> str:
    """Календар загортає посилання в google.com/url?q=… — дістаємо справжню адресу."""
    try:
        p = urlparse(href)
        if "google." in p.netloc and p.path == "/url":
            q = parse_qs(p.query).get("q")
            if q:
                return q[0]
    except Exception:  # noqa: BLE001
        pass
    return href


class _Walker(HTMLParser):
    """Розбирає один рядок опису: tg — HTML для Telegram, plain — звичайний текст."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tg: list[str] = []
        self.plain: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        t = ALLOWED.get(tag)
        if not t:
            return
        if t == "a":
            href = unwrap_link(dict(attrs).get("href") or "")
            if not href:
                self.stack.append("")
                return
            self.tg.append(f'<a href="{html.escape(href, quote=True)}">')
        else:
            self.tg.append(f"<{t}>")
        self.stack.append(t)

    def handle_endtag(self, tag):
        t = ALLOWED.get(tag)
        if not t or not self.stack:
            return
        # закриваємо найближчий відкритий такий самий тег
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == t or (t == "a" and self.stack[i] == ""):
                opened = self.stack.pop(i)
                if opened:
                    self.tg.append(f"</{opened}>")
                return

    def handle_data(self, data):
        data = data.replace("\xa0", " ")
        self.tg.append(html.escape(data, quote=False))
        self.plain.append(data)

    def result(self) -> tuple[str, str]:
        while self.stack:
            t = self.stack.pop()
            if t:
                self.tg.append(f"</{t}>")
        return "".join(self.tg), "".join(self.plain)


def split_lines(desc: str) -> list[str]:
    if "<" not in desc:   # звичайний текст (подію створено не через вебінтерфейс)
        return [html.escape(x, quote=False) for x in desc.replace("\r", "").split("\n")]
    s = desc.replace("\r", "").replace("\n", "")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|h\d)>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "• ", s, flags=re.I)
    s = re.sub(r"<(p|div|ul|ol|h\d)[^>]*>", "", s, flags=re.I)
    return s.split("\n")


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_description(desc: str) -> dict:
    """{мережа: {"tg": html, "plain": текст}}, плюс "stop", "stories" (без тексту), "alt", "topic"."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = None
    for line in split_lines(desc or ""):
        w = _Walker()
        w.feed(line)
        tg, plain = w.result()
        key = plain.strip().upper().replace(" ", "")
        if key in MARKERS:
            current = MARKERS[key]
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append((tg, plain))
    out: dict = {}
    for net, lines in sections.items():
        if net == "threads":
            topic = None
            keep = []
            for tg, plain in lines:
                m = re.match(r"\s*Тема\s*:\s*(.+?)\s*$", plain)
                if m and topic is None:
                    topic = re.sub(r"[.&]", "", m.group(1)).strip()[:50] or None
                    continue
                keep.append((tg, plain))
            lines = keep
            out["topic"] = topic
        out[net] = {"tg": _tidy("\n".join(t for t, _ in lines)), "plain": _tidy("\n".join(p for _, p in lines))}
    return out


# ============================ ПЛАН ПУБЛІКАЦІЇ ============================

def build_plan(d: date) -> list[dict]:
    """Для кожної події дня: що, куди і з якою карткою піде, і що не так."""
    events = events_on(d)
    if not events:
        return []
    root = root_folder()
    folders = date_folders(root, d) if root else []
    plans = []
    for ev in events:
        title = (ev.get("summary") or "").strip()
        parsed = parse_description(ev.get("description") or "")
        p = {"title": title, "event_id": ev.get("id"), "parsed": parsed, "nets": {}, "warn": [], "skip": [],
             "folder": None, "root": root, "stop": "stop" in parsed}
        upd = (ev.get("updated") or "")[:4]
        texts = [n for n in ("tg", "fb", "ig", "threads") if parsed.get(n, {}).get("plain")]
        if texts and upd.isdigit() and int(upd) < d.year:
            p["warn"].append(f"текст востаннє змінювали в {upd} році — перевір, чи він актуальний")
        folder, why = pick_folder(folders, title, len(events))
        if why:
            p["warn"].append(why)
        cards, notes = ({}, [])
        if folder:
            p["folder"] = folder
            cards, notes = folder_cards(folder)
            p["warn"] += notes
        p["cards"] = cards
        for net in ("tg", "fb", "ig", "threads", "stories"):
            if net not in parsed:
                p["skip"].append(net)
                continue
            fmt = NET_FORMAT[net]
            card = cards.get(fmt)
            text = (parsed[net]["plain"] if net != "stories" else "")
            n = {"fmt": fmt, "card": card, "text": text, "html": parsed[net]["tg"] if net != "stories" else "",
                 "problems": [], "notes": []}
            if not folder:
                n["problems"].append(f"немає папки «{d.month:02d}.{d.day:02d} …» з картками")
            elif card is None:
                n["problems"].append(f"немає картки {FORMAT_LABEL[fmt]}")
            if net != "stories" and not text:
                n["problems"].append("немає тексту під міткою")
            if net in LIMITS and len(text) > LIMITS[net]:
                if net == "tg":
                    n["notes"].append(f"текст {len(text)} знаків — більше за 1024, тож вийде двома повідомленнями "
                                      f"(картка, потім текст). Скороти, щоб було одне")
                else:
                    n["problems"].append(f"текст {len(text)} знаків — ліміт {LIMITS[net]}")
            if net == "threads" and parsed.get("topic"):
                n["notes"].append(f"тема «{parsed['topic']}»")
            if net == "ig" and parsed.get("alt", {}).get("plain"):
                n["notes"].append("є альт-текст")
            p["nets"][net] = n
        plans.append(p)
    return plans


def checklist(p: dict, d: date, when: str) -> str:
    lines = [f"🗓 <b>{html.escape(when)}, {human_date(d)} — {html.escape(p['title'])}</b>"]
    if p["stop"]:
        lines.append("🚫 В описі є #СТОП — нічого не публікую.")
    if not p["nets"]:
        if p["folder"]:
            lines.append("❌ В описі події немає жодної мітки (#TG, #FB, #IG, #THREADS, #СТОРІЗ) — "
                         "картки є, але бот не знає, куди їх ставити.")
        else:
            lines.append("Листівки й текстів поки немає. Якщо цю дату відзначаємо — "
                         f"поклади картки в папку «{d.month:02d}.{d.day:02d} …» і допиши тексти під мітками.")
        for w in p["warn"]:
            lines.append(f"⚠️ {html.escape(w)}")
        return "\n".join(lines)
    lines.append(f"О {PUBLISH_AT[0]}:{PUBLISH_AT[1]:02d} вийде:")
    for net, n in p["nets"].items():
        mark = "❌" if n["problems"] else ("⚠️" if n["notes"] and net == "tg" and len(n["text"]) > 1024 else "✅")
        info = [f"картка {FORMAT_LABEL[n['fmt']]}"]
        if net in LIMITS and n["text"]:
            info.append(f"текст {len(n['text'])}/{LIMITS[net]}" if net != "fb" else f"текст {len(n['text'])} зн.")
        info += n["notes"]
        info += n["problems"]
        lines.append(f"{mark} {NET_LABEL[net]} — {html.escape('; '.join(info))}")
    if p["skip"]:
        lines.append("➖ Без мітки, не публікую: " + ", ".join(NET_LABEL[s].split(' (')[0] for s in p["skip"]))
    for w in p["warn"]:
        lines.append(f"⚠️ {html.escape(w)}")
    if any(n["problems"] for n in p["nets"].values()):
        lines.append("\nДе ❌ — ця мережа не вийде, доки не виправиш. Решта вийде.")
    return "\n".join(lines)


# ============================ TELEGRAM ============================

def tg(method: str, data: dict, files: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    if files:
        payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in data.items()}
        r = requests.post(url, data=payload, files=files, timeout=120)
    else:
        r = requests.post(url, json=data, timeout=60)
    js = r.json()
    if not js.get("ok"):
        raise RuntimeError(f"Telegram {method}: {js.get('description')}")
    return js["result"]


def review(method: str, data: dict, files: dict | None = None) -> dict:
    log(f"→ гілка «Тести»: {method}")
    data = {"chat_id": REVIEW_CHAT, **data}
    if REVIEW_THREAD:
        data["message_thread_id"] = int(REVIEW_THREAD)
    return tg(method, data, files)


def review_text(text: str, button: dict | None = None) -> dict:
    data = {"text": text[:4096], "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    if button:
        data["reply_markup"] = button
    return review("sendMessage", data)


def review_photo(img: Path, caption: str, parse_html: bool) -> None:
    with img.open("rb") as fh:
        data = {"caption": caption[:1024]}
        if parse_html:
            data["parse_mode"] = "HTML"
        review("sendPhoto", data, {"photo": fh})


def preview_network(net: str, n: dict, parsed: dict) -> None:
    label = {"tg": "✈️ Telegram", "fb": "📘 Facebook", "ig": "📷 Instagram", "threads": "🧵 Threads",
             "stories": "📱 Сторіз"}[net]
    card = n["card"]
    img = prepared(card, n["fmt"]) if card else None
    if net == "stories":
        if img:
            review_photo(img, f"{label} — Facebook + Instagram", False)
        return
    if net == "tg" and img and len(n["text"]) <= 1024:   # саме так, як вийде в каналі
        review_text(f"{label} — так виглядатиме в каналі:")
        review_photo(img, n["html"], True)
        return
    body = n["html"] if net == "tg" else html.escape(n["text"])
    extra = ""
    if net == "threads" and parsed.get("topic"):
        extra = f"\n\n<i>Тема: {html.escape(parsed['topic'])}</i>"
    if net == "ig" and parsed.get("alt", {}).get("plain"):
        extra = f"\n\n<i>Альт-текст: {html.escape(parsed['alt']['plain'])}</i>"
    if img:
        review_photo(img, f"{label} — картка {FORMAT_LABEL[n['fmt']]}", False)
    else:
        review_text(f"{label} — ❌ картки немає")
    if n["text"]:
        review_text(body + extra)


def stop_button(d: date) -> dict:
    return {"inline_keyboard": [[{"text": "❌ Не публікувати", "callback_data": f"kartky_stop:{d.isoformat()}"}]]}


def buttons_work() -> bool:
    """Кнопки працюють, лише якщо в бота немає вебхука (інакше натискання йдуть туди)."""
    try:
        return not tg("getWebhookInfo", {}).get("url")
    except Exception:  # noqa: BLE001
        return False


def poll(st: dict) -> None:
    """Забирає натискання «❌ Не публікувати» і позначає дату скасованою."""
    if not buttons_work():
        log("Кнопки недоступні (вебхук) — скасування лише через #СТОП в описі.")
        return
    try:
        upd = tg("getUpdates", {"timeout": 0, "allowed_updates": ["callback_query"]})
    except Exception as e:  # noqa: BLE001
        log(f"getUpdates: {e}")
        return
    if not upd:
        return
    tg("getUpdates", {"offset": upd[-1]["update_id"] + 1, "timeout": 0})
    for u in upd:
        cq = u.get("callback_query") or {}
        m = re.match(r"kartky_stop:(\d{4}-\d{2}-\d{2})$", cq.get("data", ""))
        msg = cq.get("message") or {}
        if not m or str((msg.get("chat") or {}).get("id")) != REVIEW_CHAT:
            continue
        day = m.group(1)
        who = (cq.get("from") or {}).get("first_name") or "хтось"
        if day in st.get("cancelled", {}):
            continue
        st.setdefault("cancelled", {})[day] = {"by": who, "at": datetime.now(KYIV).isoformat(timespec="minutes")}
        save_state(st)
        try:
            tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Прийнято: не публікую"})
        except Exception:  # noqa: BLE001
            pass
        try:
            tg("editMessageReplyMarkup", {"chat_id": REVIEW_CHAT, "message_id": msg["message_id"],
                                          "reply_markup": {"inline_keyboard": []}})
        except Exception:  # noqa: BLE001
            pass
        d = date.fromisoformat(day)
        review_text(f"🚫 Скасовано ({html.escape(who)}): листівка на {human_date(d)} не вийде. "
                    f"Щоб повернути — напиши мені в чат або запусти публікацію вручну.")
        log(f"Скасовано {day} ({who})")


# ============================ ПУБЛІКАЦІЯ ============================

_FB: dict = {}


def fb_credentials() -> tuple[str, str]:
    if "v" in _FB:
        return _FB["v"]
    page_id = os.getenv("FACEBOOK_PAGE_ID", "").strip() or "111460408546782"   # біла сторінка «У фокусі»
    token = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
    res = (page_id, token)
    if page_id and token and page_id != "me":
        js = requests.get(f"{FB_GRAPH}/{page_id}", params={"fields": "access_token,name", "access_token": token},
                          timeout=30).json()
        if js.get("access_token"):
            res = (page_id, js["access_token"])
    _FB["v"] = res
    return res


def _fb_check(js: dict) -> dict:
    if "error" in js:
        raise RuntimeError(js["error"].get("message", str(js["error"]))[:300])
    return js


def pub_tg(n: dict) -> str:
    img = prepared(n["card"], n["fmt"])
    base = {"chat_id": CHANNEL, "disable_notification": "true"}
    if len(n["text"]) <= 1024:
        with img.open("rb") as fh:
            res = tg("sendPhoto", {**base, "caption": n["html"], "parse_mode": "HTML"}, {"photo": fh})
    else:
        with img.open("rb") as fh:
            tg("sendPhoto", base, {"photo": fh})
        res = tg("sendMessage", {"chat_id": CHANNEL, "disable_notification": True, "text": n["html"],
                                 "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}})
    chat = res.get("chat") or {}
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{res['message_id']}"
    return f"повідомлення {res['message_id']}"


def pub_fb(n: dict, alt: str | None) -> str:
    page_id, token = fb_credentials()
    img = prepared(n["card"], n["fmt"])
    data = {"caption": n["text"], "published": "true", "access_token": token}
    if alt:
        data["alt_text_custom"] = alt[:1000]
    with img.open("rb") as fh:
        js = requests.post(f"{FB_GRAPH}/{page_id}/photos", data=data, files={"source": fh}, timeout=120).json()
    if "error" in js and alt:   # альт-текст не прийняли — публікуємо без нього
        data.pop("alt_text_custom", None)
        with img.open("rb") as fh:
            js = requests.post(f"{FB_GRAPH}/{page_id}/photos", data=data, files={"source": fh}, timeout=120).json()
    js = _fb_check(js)
    post_id = js.get("post_id") or js.get("id")
    return f"https://www.facebook.com/{post_id}"


def pub_ig(n: dict, alt: str | None) -> str:
    page_id, token = fb_credentials()
    ig_id = ig_api.ig_user_id(FB_GRAPH, page_id, token)
    if not ig_id:
        raise RuntimeError("Instagram не підключений до сторінки або немає дозволів")
    url = ig_api._public_url(FB_GRAPH, page_id, token, prepared(n["card"], n["fmt"]))
    data = {"image_url": url, "caption": n["text"]}
    if alt:
        data["alt_text"] = alt[:1000]
    media_id = ig_api._publish(FB_GRAPH, ig_id, token, data)
    js = requests.get(f"{FB_GRAPH}/{media_id}", params={"fields": "permalink", "access_token": token},
                      timeout=30).json()
    return js.get("permalink") or f"Instagram {media_id}"


def pub_threads(n: dict, topic: str | None) -> str:
    tok = os.getenv("THREADS_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("немає THREADS_TOKEN")
    page_id, fb_token = fb_credentials()
    url = ig_api._public_url(FB_GRAPH, page_id, fb_token, prepared(n["card"], n["fmt"]))
    uid = _fb_check(requests.get(f"{THREADS_GRAPH}/me", params={"fields": "id", "access_token": tok},
                                 timeout=30).json())["id"]
    data = {"media_type": "IMAGE", "image_url": url, "text": n["text"], "access_token": tok}
    if topic:
        data["topic_tag"] = topic
    js = requests.post(f"{THREADS_GRAPH}/{uid}/threads", data=data, timeout=60).json()
    if "error" in js and topic:   # тему не прийняли — без неї
        data.pop("topic_tag")
        js = requests.post(f"{THREADS_GRAPH}/{uid}/threads", data=data, timeout=60).json()
    cid = _fb_check(js)["id"]
    for _ in range(15):
        stt = requests.get(f"{THREADS_GRAPH}/{cid}", params={"fields": "status,error_message", "access_token": tok},
                           timeout=30).json()
        if stt.get("status") == "FINISHED":
            break
        if stt.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError(stt.get("error_message") or stt.get("status"))
        time.sleep(3)
    pid = _fb_check(requests.post(f"{THREADS_GRAPH}/{uid}/threads_publish",
                                  data={"creation_id": cid, "access_token": tok}, timeout=60).json())["id"]
    js = requests.get(f"{THREADS_GRAPH}/{pid}", params={"fields": "permalink", "access_token": tok},
                      timeout=30).json()
    return js.get("permalink") or f"Threads {pid}"


def pub_stories(n: dict) -> str:
    page_id, token = fb_credentials()
    img = prepared(n["card"], n["fmt"])
    done = []
    errors = []
    try:
        with img.open("rb") as fh:
            up = _fb_check(requests.post(f"{FB_GRAPH}/{page_id}/photos",
                                         data={"published": "false", "access_token": token},
                                         files={"source": fh}, timeout=120).json())
        _fb_check(requests.post(f"{FB_GRAPH}/{page_id}/photo_stories",
                                data={"photo_id": up["id"], "access_token": token}, timeout=60).json())
        done.append("Facebook")
    except Exception as e:  # noqa: BLE001
        errors.append(f"Facebook: {e}")
    try:
        ig_id = ig_api.ig_user_id(FB_GRAPH, page_id, token)
        if not ig_id:
            raise RuntimeError("Instagram не підключений")
        url = ig_api._public_url(FB_GRAPH, page_id, token, img)
        ig_api._publish(FB_GRAPH, ig_id, token, {"image_url": url, "media_type": "STORIES"})
        done.append("Instagram")
    except Exception as e:  # noqa: BLE001
        errors.append(f"Instagram: {e}")
    if errors and not done:
        raise RuntimeError("; ".join(errors))
    return ("опубліковано: " + ", ".join(done)) + (f" (помилка — {'; '.join(errors)})" if errors else "")


PUBLISHERS = {
    "tg": lambda n, p: pub_tg(n),
    "fb": lambda n, p: pub_fb(n, (p["parsed"].get("alt") or {}).get("plain")),
    "ig": lambda n, p: pub_ig(n, (p["parsed"].get("alt") or {}).get("plain")),
    "threads": lambda n, p: pub_threads(n, p["parsed"].get("topic")),
    "stories": lambda n, p: pub_stories(n),
}


# ============================ РЕЖИМИ ============================

def wait_until(hm: tuple[int, int], d: date) -> None:
    target = datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=KYIV)
    now = datetime.now(KYIV)
    if now < target and not FORCE:
        secs = (target - now).total_seconds()
        if secs > 3 * 3600:
            log(f"До {hm[0]}:{hm[1]:02d} ще {secs / 3600:.1f} год — завершую, запуститься пізніше.")
            sys.exit(0)
        log(f"Чекаю до {hm[0]}:{hm[1]:02d} ({secs / 60:.0f} хв)…")
        time.sleep(secs)


def run_preview(d: date, st: dict) -> None:
    key = d.isoformat()
    if key in st.get("previewed", {}) and not FORCE:
        log(f"Превʼю на {key} вже надсилали.")
        return
    wait_until(PREVIEW_AT, d - timedelta(days=1))
    plans = build_plan(d)
    if not plans:
        log(f"На {key} подій у календарі немає.")
        st.setdefault("previewed", {})[key] = "no-events"
        return
    btn = buttons_work()
    for p in plans:
        when = "Завтра" if d == datetime.now(KYIV).date() + timedelta(days=1) else "Превʼю"
        if p["nets"] and not p["stop"]:
            for net, n in p["nets"].items():
                try:
                    preview_network(net, n, p["parsed"])
                except Exception as e:  # noqa: BLE001
                    review_text(f"⚠️ Не вдалося показати превʼю {NET_LABEL[net]}: {html.escape(str(e))}")
        text = checklist(p, d, when)
        if p["nets"] and not p["stop"]:
            text += ("\n\n🧪 <b>Тестовий режим:</b> у соцмережі нічого не піде, вранці лише звіт." if TEST else "")
            text += ("\n\nЩоб скасувати — кнопка нижче" if btn else "\n\nЩоб скасувати") + \
                " або рядок <code>#СТОП</code> в описі події. Виправлення в календарі й папці бот підхопить до ранку."
        review_text(text, stop_button(d) if (btn and p["nets"] and not p["stop"]) else None)
    if not FORCE:   # ручний тестовий запуск не рахується — справжнє превʼю однаково прийде о 18:00
        st.setdefault("previewed", {})[key] = datetime.now(KYIV).isoformat(timespec="minutes")


def run_publish(d: date, st: dict) -> None:
    key = d.isoformat()
    if key in st.get("published", {}) and not FORCE:
        log(f"На {key} вже публікували.")
        return
    wait_until(PUBLISH_AT, d)
    poll(st)
    plans = build_plan(d)
    if not plans:
        log(f"На {key} подій немає.")
        st.setdefault("published", {})[key] = "no-events"
        return
    if key in st.get("cancelled", {}):
        c = st["cancelled"][key]
        review_text(f"🚫 Листівку на {human_date(d)} не публікую — скасувала {html.escape(c.get('by', ''))}.")
        st.setdefault("published", {})[key] = "cancelled"
        return
    report_all = []
    for p in plans:
        if p["stop"] or not p["nets"]:
            if p["stop"]:
                report_all.append(f"🚫 {html.escape(p['title'])}: в описі #СТОП — не публікую.")
            continue
        ready = {net: n for net, n in p["nets"].items() if not n["problems"]}
        broken = {net: n for net, n in p["nets"].items() if n["problems"]}
        lines = [f"<b>{html.escape(p['title'])}</b>"]
        if TEST:
            lines.insert(0, "🧪 <b>Тест.</b> Зараз опублікувала б:")
            for net in ready:
                lines.append(f"✅ {NET_LABEL[net]}")
        else:
            ok_any, fail_any = False, False
            done = st.setdefault("done", {}).setdefault(f"{key}:{p['event_id']}", {})
            for net, n in ready.items():
                if net in done:   # уже вийшло при попередньому запуску — не дублюємо
                    lines.append(f"✅ {NET_LABEL[net]}: {html.escape(done[net])}")
                    ok_any = True
                    continue
                try:
                    res = PUBLISHERS[net](n, p)
                    done[net] = res
                    save_state(st)   # одразу: якщо запуск обірветься, повтор не продублює
                    lines.append(f"✅ {NET_LABEL[net]}: {html.escape(res)}")
                    ok_any = True
                except Exception as e:  # noqa: BLE001
                    lines.append(f"❌ {NET_LABEL[net]}: {html.escape(str(e))}")
                    fail_any = True
            if ok_any and not fail_any and not broken and p["folder"] and p["root"]:
                try:
                    where = archive_folder(p["root"], p["folder"], d.year)
                    lines.append(f"📁 Папку перенесено в «{where}».")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"⚠️ Папку не перенесено в архів: {html.escape(str(e))}")
        for net, n in broken.items():
            lines.append(f"❌ {NET_LABEL[net]} — не вийшло: {html.escape('; '.join(n['problems']))}")
        report_all.append("\n".join(lines))
    if report_all:
        review_text("\n\n".join(report_all))
    if not (FORCE and TEST):
        st.setdefault("published", {})[key] = datetime.now(KYIV).isoformat(timespec="minutes")


def main() -> int:
    st = load_state()
    today = datetime.now(KYIV).date()
    forced = os.getenv("KARTKY_DATE", "").strip()
    try:
        if MODE == "poll":
            poll(st)
        elif MODE == "check":   # лише перевірка у файл журналу, нічого в Telegram (для Claude)
            for ds in (forced or (today + timedelta(days=1)).isoformat()).split(","):
                d = date.fromisoformat(ds.strip())
                for p in build_plan(d):
                    log("\n" + re.sub(r"<[^>]+>", "", checklist(p, d, "Перевірка")))
                    for fmt, f in (p.get("cards") or {}).items():
                        if f:
                            log(f"   картка {FORMAT_LABEL[fmt]}: {f['name']}, змінено {f.get('modifiedTime')}")
        elif MODE == "publish":
            run_publish(date.fromisoformat(forced) if forced else today, st)
        else:
            run_preview(date.fromisoformat(forced) if forced else today + timedelta(days=1), st)
    except Exception as e:  # noqa: BLE001
        import traceback
        log(f"Помилка: {e}\n{traceback.format_exc()}")
        try:
            review_text(f"⚠️ Бот листівок: помилка — {html.escape(str(e))[:3000]}")
        except Exception:  # noqa: BLE001
            pass
        save_state(st)
        save_log()
        return 1
    save_state(st)
    log("Готово.")
    save_log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
