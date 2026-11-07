"""TikTok (Sandbox-застосунок ACMC Analytics): доступ і статистика акаунта @acmc.ua.

Секрети: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN (зберігається автоматично
через GH_ADMIN_TOKEN). Перше підключення: python tiktok_api.py <code з адреси після «Дозволити»>.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import requests

import threads_api

API = "https://open.tiktokapis.com/v2"
REDIRECT = "https://acmc.ua/"


def _key():
    return os.getenv("TIKTOK_CLIENT_KEY", "").strip(), os.getenv("TIKTOK_CLIENT_SECRET", "").strip()


def _token_request(data: dict) -> dict:
    ck, cs = _key()
    r = requests.post(f"{API}/oauth/token/", data={"client_key": ck, "client_secret": cs, **data},
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30).json()
    if not r.get("access_token"):
        raise RuntimeError(r.get("error_description") or r.get("error") or str(r))
    return r


def exchange_code(code: str) -> None:
    r = _token_request({"code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    ok = threads_api._save_secret("TIKTOK_REFRESH_TOKEN", r["refresh_token"])
    print(f"TikTok підключено (scope: {r.get('scope')}). Refresh-токен діє ~{int(r.get('refresh_expires_in', 0))//86400} дн.,"
          f" {'збережено в секретах' if ok else 'НЕ збережено (немає GH_ADMIN_TOKEN)'}.")


def access_token(log=print) -> str | None:
    rt = os.getenv("TIKTOK_REFRESH_TOKEN", "").strip()
    if not rt or not all(_key()):
        return None
    try:
        r = _token_request({"grant_type": "refresh_token", "refresh_token": rt})
        if r.get("refresh_token") and r["refresh_token"] != rt:
            threads_api._save_secret("TIKTOK_REFRESH_TOKEN", r["refresh_token"])
        return r["access_token"]
    except Exception as e:  # noqa: BLE001
        log(f"TikTok: доступ не оновлено — {e}")
        return None


def stats(since: datetime, log=print) -> dict | None:
    tok = access_token(log)
    if not tok:
        return None
    h = {"Authorization": f"Bearer {tok}"}
    info = requests.get(f"{API}/user/info/", headers=h, timeout=30,
                        params={"fields": "display_name,follower_count,likes_count,video_count"}).json()
    u = (info.get("data") or {}).get("user") or {}
    out = {"name": u.get("username") or u.get("display_name"), "followers": u.get("follower_count"),
           "likes_total": u.get("likes_count"), "posts": []}
    cursor, more = None, True
    while more:
        body = {"max_count": 20, **({"cursor": cursor} if cursor else {})}
        js = requests.post(f"{API}/video/list/", headers={**h, "Content-Type": "application/json"}, json=body,
                           params={"fields": "id,title,video_description,create_time,share_url,view_count,"
                                             "like_count,comment_count,share_count"}, timeout=30).json()
        data = js.get("data") or {}
        for v in data.get("videos", []):
            if datetime.fromtimestamp(v["create_time"], timezone.utc) < since:
                more = False
                break
            out["posts"].append({"text": v.get("title") or v.get("video_description", ""), "url": v.get("share_url"),
                                 "reach": v.get("view_count"),
                                 "eng": (v.get("like_count") or 0) + (v.get("comment_count") or 0) + (v.get("share_count") or 0)})
        else:
            more = data.get("has_more", False)
            cursor = data.get("cursor")
        if js.get("error", {}).get("code") not in (None, "ok"):
            log(f"TikTok: {js['error']}")
            break
    out["reach"] = sum(p["reach"] or 0 for p in out["posts"])
    return out


if __name__ == "__main__":
    exchange_code(sys.argv[1])
