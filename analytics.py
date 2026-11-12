"""Щотижнева аналітика соцмереж АКМЦ у Telegram-чат редакції.

Платформи: біла сторінка Facebook, Instagram acmc.ua, Threads acmc.ua, Telegram-канал, TikTok, YouTube (АКМЦ і «Перевірено на Сході»).
Рахує всі дописи (не лише DeepState), топ-3 по кожній платформі, окремо — середнє по DeepState.

Секрети: FACEBOOK_PAGE_TOKEN, THREADS_TOKEN, TELEGRAM_BOT_TOKEN (для підписників каналу),
ANALYTICS_BOT_TOKEN + ANALYTICS_CHAT_ID (куди надсилати; якщо їх немає — бот DeepState у службовий чат).
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone

import requests

import threads_api
import tiktok_api
import youtube_api

G = "https://graph.facebook.com/v24.0"
PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "111460408546782")
# жовта сторінка «Антикризовий медіа-центр» (36 тис.): бот бачить її з 12.11.2026 — Тетяна призначила її
# основному портфоліо («Призначити партнерів», дозвіл «Статистика») і системному користувачу
YELLOW_PAGE_ID = os.getenv("FACEBOOK_YELLOW_PAGE_ID", "414302258746592")
DAYS = int(os.getenv("ANALYTICS_DAYS", "7"))
NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(days=DAYS)
PREV = SINCE - timedelta(days=DAYS)


def log(m):
    print(m, flush=True)


def get(url, **params):
    try:
        return requests.get(url, params=params, timeout=40).json()
    except Exception as e:  # noqa: BLE001
        return {"error": {"message": str(e)}}


def fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ") if n is not None else "—"


def delta(cur, prev) -> str:
    if not cur or not prev:
        return ""
    p = round((cur - prev) / prev * 100)
    return f" ({'+' if p >= 0 else ''}{p}%)"


def short(text: str, n: int = 60) -> str:
    t = " ".join((text or "").split())
    return html.escape(t[:n] + ("…" if len(t) > n else "")) or "(без тексту)"


def is_deepstate(text: str) -> bool:
    return (text or "").lstrip().startswith("DeepState")


def _metric(js) -> int | None:
    for m in js.get("data", []):
        if "total_value" in m:
            return int(m["total_value"].get("value") or 0)
        vals = m.get("values") or []
        if vals:
            return int(sum((v.get("value") or 0) for v in vals if isinstance(v.get("value"), (int, float))))
    return None


# ============================ FACEBOOK ============================
def facebook(PAGE_ID: str = PAGE_ID):
    tok = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
    if not tok:
        return None
    pg = get(f"{G}/{PAGE_ID}", fields="access_token,name,followers_count", access_token=tok)
    if "error" in pg:
        log(f"FB: {pg['error'].get('message')}")
        return None
    ptok = pg.get("access_token", tok)
    out = {"name": pg.get("name"), "followers": pg.get("followers_count"), "posts": []}
    # охоплення сторінки: пробуємо актуальні назви показників по черзі
    for metric in ("page_impressions_unique", "page_total_media_view_unique", "page_views_total"):
        cur = _metric(get(f"{G}/{PAGE_ID}/insights", metric=metric, period="day",
                          since=int(SINCE.timestamp()), until=int(NOW.timestamp()), access_token=ptok))
        if cur is not None:
            prev = _metric(get(f"{G}/{PAGE_ID}/insights", metric=metric, period="day",
                               since=int(PREV.timestamp()), until=int(SINCE.timestamp()), access_token=ptok))
            out.update(reach=cur, reach_prev=prev, reach_metric=metric)
            break
    posts = get(f"{G}/{PAGE_ID}/posts", fields="id,message,created_time,permalink_url,shares,"
                "reactions.summary(true).limit(0),comments.summary(true).limit(0)",
                since=int(SINCE.timestamp()), limit=50, access_token=ptok)
    for p in posts.get("data", []):
        eng = ((p.get("reactions") or {}).get("summary", {}).get("total_count", 0)
               + (p.get("comments") or {}).get("summary", {}).get("total_count", 0)
               + (p.get("shares") or {}).get("count", 0))
        reach = None
        for metric in ("post_impressions_unique", "post_total_media_view_unique"):
            reach = _metric(get(f"{G}/{p['id']}/insights", metric=metric, access_token=ptok))
            if reach is not None:
                break
        out["posts"].append({"text": p.get("message", ""), "url": p.get("permalink_url"),
                             "reach": reach, "eng": eng,
                             "react": (p.get("reactions") or {}).get("summary", {}).get("total_count", 0),
                             "comm": (p.get("comments") or {}).get("summary", {}).get("total_count", 0)})
    return out


# ============================ INSTAGRAM ============================
def instagram():
    tok = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
    if not tok:
        return None
    pg = get(f"{G}/{PAGE_ID}", fields="access_token,instagram_business_account{id,username,followers_count}",
             access_token=tok)
    acc = pg.get("instagram_business_account")
    if not acc:
        log(f"IG: акаунт не видно {pg.get('error', {}).get('message', '')}")
        return None
    ptok, ig = pg.get("access_token", tok), acc["id"]
    out = {"name": acc.get("username"), "followers": acc.get("followers_count"), "posts": []}
    cur = _metric(get(f"{G}/{ig}/insights", metric="reach", period="day",
                      since=int(SINCE.timestamp()), until=int(NOW.timestamp()), access_token=ptok))
    prev = _metric(get(f"{G}/{ig}/insights", metric="reach", period="day",
                       since=int(PREV.timestamp()), until=int(SINCE.timestamp()), access_token=ptok))
    out.update(reach=cur, reach_prev=prev)
    media = get(f"{G}/{ig}/media", fields="id,caption,timestamp,permalink,like_count,comments_count",
                limit=50, access_token=ptok)
    for m in media.get("data", []):
        ts = datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
        if ts < SINCE:
            continue
        reach = _metric(get(f"{G}/{m['id']}/insights", metric="reach", access_token=ptok))
        out["posts"].append({"text": m.get("caption", ""), "url": m.get("permalink"), "reach": reach,
                             "eng": (m.get("like_count") or 0) + (m.get("comments_count") or 0),
                             "react": m.get("like_count") or 0, "comm": m.get("comments_count") or 0})
    return out


# ============================ THREADS ============================
def threads():
    tok = threads_api._token()
    if not tok:
        return None
    me = get(f"{threads_api.GRAPH}/me", fields="id,username", access_token=tok)
    if "error" in me:
        log(f"Threads: {me['error'].get('message')}")
        return None
    cur = threads_api.account_insights(SINCE, NOW, log)
    prev = threads_api.account_insights(PREV, SINCE, log)
    out = {"name": me.get("username"), "followers": threads_api.followers(log),
           "reach": cur.get("views"), "reach_prev": prev.get("views"), "posts": []}
    posts = get(f"{threads_api.GRAPH}/{me['id']}/threads", fields="id,text,timestamp,permalink",
                since=int(SINCE.timestamp()), limit=50, access_token=tok)
    for p in posts.get("data", []):
        ins = get(f"{threads_api.GRAPH}/{p['id']}/insights", metric="views,likes,replies,reposts", access_token=tok)
        vals = {m["name"]: (m.get("values") or [{}])[0].get("value", 0) for m in ins.get("data", [])}
        out["posts"].append({"text": p.get("text", ""), "url": p.get("permalink"), "reach": vals.get("views"),
                             "eng": vals.get("likes", 0) + vals.get("replies", 0) + vals.get("reposts", 0),
                             "react": vals.get("likes", 0), "comm": vals.get("replies", 0)})
    return out


# ============================ TELEGRAM ============================
def telegram():
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "@acmc_ua").strip() or "@acmc_ua"
    if not tok:
        return None
    js = get(f"https://api.telegram.org/bot{tok}/getChatMemberCount", chat_id=chat)
    return js.get("result")


# ============================ ЗВІТ ============================
def section(title: str, d: dict | None, reach_word: str) -> list[str]:
    if not d:
        return [f"<b>{title}</b>: дані недоступні", ""]
    lines = [f"<b>{title}</b>"]
    if d.get("reach") is not None:
        lines.append(f"{reach_word}: {fmt(d['reach'])}{delta(d.get('reach'), d.get('reach_prev'))}")
    lines.append(f"Підписники: {fmt(d.get('followers'))}")
    posts = d["posts"]
    lines.append(f"Дописів {'за тиждень' if DAYS == 7 else 'за добу' if DAYS == 1 else f'за {DAYS} дн.'}: {len(posts)}")
    if any("comm" in p for p in posts):   # реакції й коментарі — щоб бачити, де реагують і що модерувати
        lines.append(f"Реакції: {fmt(sum(p.get('react', 0) for p in posts))} · "
                     f"коментарі: {fmt(sum(p.get('comm', 0) for p in posts))}")
    key = (lambda p: p["reach"] or 0) if any(p["reach"] for p in posts) else (lambda p: p["eng"])
    top = sorted(posts, key=key, reverse=True)[:3]
    for i, p in enumerate(top, 1):
        val = f"{fmt(p['reach'])} охопл." if p["reach"] is not None else f"{fmt(p['eng'])} взаємодій"
        if p.get("comm"):
            val += f", коментарі: {fmt(p['comm'])}"
        name = short(p["text"])
        lines.append(f"{i}. <a href=\"{p['url']}\">{name}</a> — {val}" if p.get("url") else f"{i}. {name} — {val}")
    ds = [p for p in posts if is_deepstate(p["text"])]
    if ds:
        avg = sum((p["reach"] or 0) for p in ds) / len(ds)
        others = [p for p in posts if not is_deepstate(p["text"])]
        avg_o = sum((p["reach"] or 0) for p in others) / len(others) if others else 0
        lines.append(f"🗺 DeepState: {len(ds)} доп., середнє охоплення {fmt(avg)}"
                     + (f" (інші дописи — {fmt(avg_o)})" if others else "")
                     + (f", коментарів: {fmt(sum(p.get('comm', 0) for p in ds))}" if any("comm" in p for p in ds) else ""))
    return lines + [""]


def build() -> str:
    # Період — ковзний: останні DAYS×24 год до моменту запуску. Тому для доби — 9.11 20:24 – 10.11 20:24,
    # а не «10.11–10.11» (рішення 10.11.2026). Час — київський.
    from zoneinfo import ZoneInfo
    kyiv = ZoneInfo("Europe/Kyiv")
    s_, e_ = SINCE.astimezone(kyiv), NOW.astimezone(kyiv)
    label = (f"{s_:%d.%m %H:%M} – {e_:%d.%m %H:%M}" if DAYS <= 3 else f"{s_:%d.%m}–{e_:%d.%m}")
    lines = [f"📊 <b>Аналітика АКМЦ: {label}</b>", ""]
    tg = telegram()
    lines += [f"<b>Telegram @acmc_ua</b>", f"Підписники: {fmt(tg)}", ""]
    # жовта — основна й більша, тож першою (рішення Юлі 12.11.2026)
    if YELLOW_PAGE_ID:
        lines += section("Facebook «АКМЦ» (жовта)", facebook(YELLOW_PAGE_ID), "Охоплення сторінки")
    lines += section("Facebook «АКМЦ. У фокусі» (біла)", facebook(), "Охоплення сторінки")
    lines += section("Instagram acmc.ua", instagram(), "Охоплення")
    lines += section("Threads acmc.ua", threads(), "Перегляди")
    if os.getenv("TIKTOK_REFRESH_TOKEN", "").strip():
        lines += section("TikTok @acmc.ua", tiktok_api.stats(SINCE, log), "Перегляди нових відео")
    lines += youtube_api.sections(SINCE, log)
    per = "останню добу" if DAYS == 1 else f"останні {DAYS} дн."
    prev = "попередньої доби" if DAYS == 1 else ("попереднього тижня" if DAYS == 7 else f"попередніх {DAYS} дн.")
    lines.append(f"<i>Охоплення — за {per}, у дужках — зміна до {prev}.</i>")
    return "\n".join(lines)


def send(text: str) -> None:
    tok = os.getenv("ANALYTICS_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("ANALYTICS_CHAT_ID", "").strip() or os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not chat and tok:  # підказка: які чати бачить бот
        upd = get(f"https://api.telegram.org/bot{tok}/getUpdates")
        chats = {(u.get("message") or u.get("my_chat_member") or {}).get("chat", {}).get("id"):
                 (u.get("message") or u.get("my_chat_member") or {}).get("chat", {}).get("title")
                 for u in upd.get("result", [])}
        log(f"ANALYTICS_CHAT_ID не задано. Чати, які бачить бот: {chats}")
        return
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "message_thread_id": os.getenv("THREAD_ANALYTICS", "5"),
                            "text": text[:4000], "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=40).json()
    log("Звіт надіслано." if r.get("ok") else f"Не вдалося надіслати: {r}")


if __name__ == "__main__":
    report = build()
    log(report)
    if os.getenv("DRY_RUN", "0") != "1":
        send(report)
