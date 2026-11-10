"""«Тіньовий» запуск нової версії (v9): той самий день, але все — лише в гілку «Тести» службової групи.

Нічого не публікує в канали й соцмережі, нічого не зберігає в репозиторій.
Дані (стан, історія, зміни) беруться з основної гілки — їх щоранку оновлює робочий бот.

Режими (змінна SHADOW_KIND):
  day  — сторіз-картка за добу (+ мапа, якщо були зміни);
  week — тижневий Reels, сторіз-картка тижня, підпис для Instagram і перевірка автопублікації
         (відео кладемо в GitHub Releases і просимо Instagram його «підготувати», але НЕ публікуємо).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import tracker as T
from story import render_story

THREAD_TESTS = os.getenv("THREAD_TESTS", "6")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
OUT = T.DATA / "shadow"
OUT.mkdir(parents=True, exist_ok=True)


def send(method: str, data: dict, files: dict | None = None) -> None:
    if not TOKEN or not CHAT:
        print("Немає секретів Telegram —", method, data.get("caption") or data.get("text", "")[:80])
        return
    data = {"chat_id": CHAT, "message_thread_id": THREAD_TESTS, "disable_notification": "true", **data}
    T.tg(method, TOKEN, data, files)


def photo(p: Path, caption: str) -> None:
    with p.open("rb") as fh:
        send("sendPhoto", {"caption": caption}, {"photo": fh})


def results_from_row(r: dict) -> dict:
    out = {}
    for k in T.OBLASTS:
        sp = lambda col: [x.strip() for x in (r.get(col) or "").split(";") if x.strip()]  # noqa: E731
        out[k] = {"newly_km2": T._f(r.get(f"{k}_newly_occupied_km2")), "deocc_km2": T._f(r.get(f"{k}_deoccupied_km2")),
                  "grey_km2": T._f(r.get(f"{k}_to_grey_km2")),
                  "newly_places": sp(f"{k}_newly_places"), "deocc_places": sp(f"{k}_deoccupied_places"),
                  "grey_places": sp(f"{k}_to_grey_places")}
    return out


def run_day(hist: list[dict]) -> None:
    r = hist[-1]
    day = r["date"]
    res = results_from_row(r)
    label = T._date_label(datetime.strptime(day, "%Y-%m-%d").date())
    foot, handle = T.STORY_FOOTERS["day"]
    card = render_story(T.COVER_IMAGE, label, T.story_blocks_daily(res, {k: T.place_names([day], k) for k in T.OBLASTS}), OUT / "story_day.jpg", footer=foot, handle=handle)
    photo(card, f"🧪 Тінь v9 · сторіз за добу ({day})")
    nets = {k: res[k]["newly_km2"] - res[k]["deocc_km2"] for k in T.OBLASTS}
    places = {k: res[k]["newly_places"] + res[k]["deocc_places"] for k in T.OBLASTS}
    mp = T.make_change_map("day", [day], nets, places, "shadow_map_day.jpg")
    if mp:
        v45 = mp.with_name(mp.stem + "_45.jpg")
        photo(v45 if v45.exists() else mp, f"🧪 Тінь v9 · мапа за добу для Threads, 4:5 ({day})")


def release_upload(video: Path) -> str | None:
    """Кладемо відео в GitHub Releases (тег reels) і повертаємо публічне посилання; лишаємо 4 останні."""
    repo = os.getenv("GITHUB_REPOSITORY", "Anti-Crisis-Media-Center/deepstate-tracker")
    name = f"reel_{datetime.now():%Y%m%d_%H%M}.mp4"
    dst = video.with_name(name)
    dst.write_bytes(video.read_bytes())
    try:
        subprocess.run(["gh", "release", "view", "reels", "-R", repo], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.run(["gh", "release", "create", "reels", "-R", repo, "--title", "Reels (тимчасові файли)",
                        "--notes", "Тимчасові відео для Instagram Reels. Бот зберігає лише 4 останні."], check=True)
    subprocess.run(["gh", "release", "upload", "reels", str(dst), "-R", repo, "--clobber"], check=True)
    assets = json.loads(subprocess.run(["gh", "release", "view", "reels", "-R", repo, "--json", "assets"],
                                       check=True, capture_output=True, text=True).stdout)["assets"]
    for a in sorted(assets, key=lambda a: a["name"])[:-4]:
        subprocess.run(["gh", "release", "delete-asset", "reels", a["name"], "-R", repo, "-y"], check=False)
    return f"https://github.com/{repo}/releases/download/reels/{name}"


def ig_container_test(url: str) -> str:
    """Просимо Instagram підготувати Reels з посилання, але НЕ публікуємо. Повертаємо, чим закінчилось."""
    try:
        import ig_api
        page_id, page_token = T.fb_page_credentials()
        if not page_id or not page_token:
            return "немає ключів Facebook/Instagram"
        ig = ig_api.ig_user_id(T.FB_GRAPH, page_id, page_token)
        if not ig:
            return "Instagram не підключений до сторінки"
        js = requests.post(f"{T.FB_GRAPH}/{ig}/media", data={"media_type": "REELS", "video_url": url,
                           "caption": "тест", "share_to_feed": "true", "access_token": page_token}, timeout=60).json()
        if "id" not in js:
            return f"Instagram відмовив: {str(js.get('error', js))[:300]}"
        for _ in range(40):
            st = requests.get(f"{T.FB_GRAPH}/{js['id']}", params={"fields": "status_code,status",
                              "access_token": page_token}, timeout=30).json()
            if st.get("status_code") in ("FINISHED", "ERROR", "EXPIRED"):
                return f"{st.get('status_code')} {st.get('status', '')}".strip()
            time.sleep(6)
        return "не встиг обробити за 4 хв"
    except Exception as e:  # noqa: BLE001
        return f"помилка: {e}"


def run_week(hist: list[dict]) -> None:
    rows = hist[-7:]
    dates = [r["date"] for r in rows]
    d0 = datetime.strptime(dates[0], "%Y-%m-%d").date() - timedelta(days=1)
    label = T._period_label(d0.isoformat(), dates[-1])
    nets = {k: T._net_period(rows, k) for k in T.OBLASTS}
    places = {k: T._rows_places(rows) if k == "donetsk" else [] for k in T.OBLASTS}
    mp = T.make_change_map("week", dates, nets, places, "shadow_map_week.jpg")
    first = T._tiktok_first_frame("week", rows, T.COVER_WEEK, label, OUT / "first.jpg")
    end = T._tiktok_end_card("week", rows, T.COVER_WEEK, label, OUT / "end.jpg")
    D = T.DATA
    sj = D / "shadow_map_week_steps.json"
    steps = [(D / n, s, D / o) for n, s, o in json.loads(sj.read_text())] if sj.exists() else None
    from video import make_video
    base, full = D / "shadow_map_week_base_v.jpg", D / "shadow_map_week_v.jpg"
    v = make_video(first, base if mp and base.exists() else None, full if mp and full.exists() else None, end,
                   OUT / "reel_week.mp4", steps=steps)
    foot, handle = T.STORY_FOOTERS["week"]
    card = render_story(T.COVER_WEEK, label, T.story_blocks_summary("week", rows), OUT / "story_week.jpg",
                        footer=foot, handle=handle)
    photo(card, f"🧪 Тінь v9 · сторіз тижня ({label})")
    if not v:
        send("sendMessage", {"text": "🧪 Тінь v9: відео не зібралося (немає ffmpeg або помилка) — Reels не перевірено"})
    if v:
        with v.open("rb") as fh:
            send("sendVideo", {"caption": f"🧪 Тінь v9 · Reels тижня ({label})", "supports_streaming": "true"},
                 {"video": fh})
        url = None
        if os.getenv("SHADOW_NO_UPLOAD") == "1":
            return
        try:
            url = release_upload(v)
        except Exception as e:  # noqa: BLE001
            send("sendMessage", {"text": f"🧪 Відео на GitHub не завантажилось: {e}"})
        if url:
            send("sendMessage", {"text": f"🧪 Перевірка автопублікації Reels (без публікації):\n"
                                         f"посилання: {url}\nInstagram: {ig_container_test(url)}",
                                 "disable_web_page_preview": "true"})


def main() -> None:
    hist = T.read_history()
    kind = os.getenv("SHADOW_KIND", "day")
    run_day(hist) if kind == "day" else run_week(hist)


if __name__ == "__main__":
    main()
