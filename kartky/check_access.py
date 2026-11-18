"""Разова перевірка доступів бота листівок: календар, папка на Диску, Telegram.

Нічого не публікує в соцмережі. Звіт іде в гілку «тест» Суперчату
і зберігається в data/kartky_check.txt.
"""
import datetime as dt
import json
import os
import sys

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

CALENDAR_ID = "4f25090a1ec5507f89f9a45f76d7691a824918276a1109522baf0ea5931a47fc@group.calendar.google.com"
FOLDER_NAME = "Визначні дати АКМЦ"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive",
]
MARKERS = ["#TG", "#FB", "#IG", "#THREADS", "#СТОРІЗ"]
KYIV = dt.timezone(dt.timedelta(hours=2))


def main() -> int:
    lines = ["🔧 Перевірка доступів бота листівок"]
    ok = True

    raw = os.environ.get("GOOGLE_SA_JSON", "").strip()
    if not raw:
        lines.append("❌ Секрет GOOGLE_SA_JSON порожній або не знайдений")
        return finish(lines, False)
    try:
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        lines.append(f"✅ Ключ прочитано: {info.get('client_email')}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"❌ Ключ не читається як JSON: {type(e).__name__}")
        return finish(lines, False)

    s = AuthorizedSession(creds)

    # Календар
    now = dt.datetime.now(KYIV)
    r = s.get(
        f"https://www.googleapis.com/calendar/v3/calendars/{requests.utils.quote(CALENDAR_ID)}/events",
        params={
            "timeMin": now.isoformat(),
            "timeMax": (now + dt.timedelta(days=14)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
        },
        timeout=30,
    )
    if r.ok:
        items = r.json().get("items", [])
        lines.append(f"✅ Календар «Визначні події АКМЦ»: подій на 14 днів — {len(items)}")
        for ev in items:
            date = ev.get("start", {}).get("date") or ev.get("start", {}).get("dateTime", "")[:10]
            desc = ev.get("description", "") or ""
            found = [m for m in MARKERS if m in desc]
            lines.append(f"   • {date} {ev.get('summary', '')} — мітки: {', '.join(found) or 'немає'}")
    else:
        ok = False
        lines.append(f"❌ Календар: помилка {r.status_code} — {r.text[:200]}")

    # Диск
    q = (
        "mimeType='application/vnd.google-apps.folder' and trashed=false "
        f"and name contains '{FOLDER_NAME}'"
    )
    r = s.get(
        "https://www.googleapis.com/drive/v3/files",
        params={"q": q, "fields": "files(id,name)", "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true"},
        timeout=30,
    )
    if not r.ok:
        ok = False
        lines.append(f"❌ Диск: помилка {r.status_code} — {r.text[:200]}")
    elif not r.json().get("files"):
        ok = False
        lines.append("❌ Диск: папку «Визначні дати АКМЦ…» не видно — перевір доступ")
    else:
        root = r.json()["files"][0]
        lines.append(f"✅ Папка на Диску: {root['name']}")
        subs = list_children(s, root["id"], folders=True)
        for sub in sorted(subs, key=lambda f: f["name"]):
            files = list_children(s, sub["id"], folders=False)
            desc = []
            for f in files:
                m = f.get("imageMediaMetadata") or {}
                w, h = m.get("width"), m.get("height")
                desc.append(f"{f['name']} ({w}×{h})" if w else f["name"])
            lines.append(f"   📁 {sub['name']}: {len(files)} файл(и)")
            for d in desc:
                lines.append(f"      – {d}")

    return finish(lines, ok)


def list_children(s, parent_id, folders):
    cond = "=" if folders else "!="
    r = s.get(
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": f"'{parent_id}' in parents and trashed=false and "
                 f"mimeType{cond}'application/vnd.google-apps.folder'",
            "fields": "files(id,name,mimeType,imageMediaMetadata(width,height))",
            "pageSize": 200,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("files", [])


def finish(lines, ok):
    text = "\n".join(lines)
    print(text)
    os.makedirs("data", exist_ok=True)
    with open("data/kartky_check.txt", "w", encoding="utf-8") as f:
        f.write(dt.datetime.now(KYIV).strftime("%Y-%m-%d %H:%M") + "\n" + text + "\n")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("KARTKY_CHAT_ID")
    thread = os.environ.get("KARTKY_THREAD_ID")
    if token and chat:
        payload = {"chat_id": chat, "text": text}
        if thread:
            payload["message_thread_id"] = int(thread)
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=30)
        tg = "✅ Telegram: повідомлення в гілку надіслано" if r.ok else f"❌ Telegram: {r.status_code} {r.text[:200]}"
        print(tg)
        with open("data/kartky_check.txt", "a", encoding="utf-8") as f:
            f.write(tg + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
