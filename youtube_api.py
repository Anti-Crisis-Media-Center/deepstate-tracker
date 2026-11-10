"""YouTube для щотижневої аналітики: публічна статистика каналів через YouTube Data API v3.

Потрібен лише ключ API (секрет YOUTUBE_API_KEY) — без входу в акаунт і без аудиту Google.
Канали — змінна YOUTUBE_CHANNELS: через кому, @handle або ID каналу (UC…).
Досмотрюваність і «swiped away» ключ не дає — їх дивимося в YouTube Studio.
"""
from __future__ import annotations

import html
import os
from datetime import datetime

import requests

API = "https://www.googleapis.com/youtube/v3"


def _get(path: str, **params) -> dict:
    params["key"] = os.getenv("YOUTUBE_API_KEY", "").strip()
    try:
        return requests.get(f"{API}/{path}", params=params, timeout=40).json()
    except Exception as e:  # noqa: BLE001
        return {"error": {"message": str(e)}}


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ") if n is not None else "—"


def _short(t: str, n: int = 60) -> str:
    t = " ".join((t or "").split())
    return html.escape(t[:n] + ("…" if len(t) > n else ""))


def _channel(ref: str, log) -> dict | None:
    ref = ref.strip()
    if not ref:
        return None
    q = {"id": ref} if ref.startswith("UC") else {"forHandle": ref if ref.startswith("@") else "@" + ref}
    js = _get("channels", part="snippet,statistics,contentDetails", **q)
    if "error" in js or not js.get("items"):
        log(f"YouTube: канал {ref} не знайдено — {js.get('error', {}).get('message', 'немає даних')}")
        return None
    return js["items"][0]


def _videos(ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        js = _get("videos", part="snippet,statistics", id=",".join(ids[i:i + 50]))
        out += js.get("items", [])
    return out


def _playlist_ids(pid: str, limit: int = 50) -> list[str]:
    js = _get("playlistItems", part="contentDetails", playlistId=pid, maxResults=limit)
    return [x["contentDetails"]["videoId"] for x in js.get("items", [])]


def _views(v: dict) -> int:
    return int(v.get("statistics", {}).get("viewCount") or 0)


def section(ref: str, since: datetime, log) -> list[str]:
    ch = _channel(ref, log)
    if not ch:
        return [f"<b>YouTube {html.escape(ref)}</b>: дані недоступні", ""]
    name = ch["snippet"]["title"]
    st = ch.get("statistics", {})
    lines = [f"<b>YouTube «{html.escape(name)}»</b>",
             "Підписники: " + ("приховано" if st.get("hiddenSubscriberCount") else _fmt(st.get("subscriberCount")))]
    uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    vids = _videos(_playlist_ids(uploads)) if uploads else []
    new = [v for v in vids if v["snippet"]["publishedAt"] >= since.strftime("%Y-%m-%dT%H:%M:%SZ")]
    _d = int(os.getenv("ANALYTICS_DAYS", "7"))
    lines.append(f"Нових відео {'за тиждень' if _d == 7 else 'за добу' if _d == 1 else f'за {_d} дн.'}: {len(new)}"
                 + (f", переглядів у них: {_fmt(sum(_views(v) for v in new))}" if new else ""))
    for i, v in enumerate(sorted(new, key=_views, reverse=True)[:3], 1):
        s = v["statistics"]
        lines.append(f"{i}. <a href=\"https://youtu.be/{v['id']}\">{_short(v['snippet']['title'])}</a> — "
                     f"{_fmt(_views(v))} перегл., {_fmt(s.get('likeCount'))} вподоб., {_fmt(s.get('commentCount'))} коментарів")
    # рубрика DeepState — окремо, якщо на каналі є такий плейлист
    pls = _get("playlists", part="snippet", channelId=ch["id"], maxResults=50).get("items", [])
    ds = next((p for p in pls if "DeepState" in p["snippet"]["title"]), None)
    if ds:
        dv = sorted(_videos(_playlist_ids(ds["id"])), key=lambda v: v["snippet"]["publishedAt"], reverse=True)
        if dv:
            last4 = dv[:4]
            avg = sum(_views(v) for v in last4) / len(last4)
            lines.append(f"🗺 DeepState: {len(dv)} відео в плейлисті, останнє — {_fmt(_views(dv[0]))} перегл., "
                         f"середнє за останні {len(last4)} — {_fmt(avg)}")
    return lines + [""]


def sections(since: datetime, log) -> list[str]:
    if not os.getenv("YOUTUBE_API_KEY", "").strip():
        return []
    refs = [r.strip() for r in os.getenv("YOUTUBE_CHANNELS", "").split(",") if r.strip()]
    if not refs:
        log("YouTube: змінну YOUTUBE_CHANNELS не задано — додайте @handle каналів через кому.")
        return []
    out = []
    for r in refs:
        out += section(r, since, log)
    return out
