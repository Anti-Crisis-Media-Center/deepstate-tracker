"""Публікація зведень DeepState в Instagram (допис + сторіз) і підписники акаунта.

Працює через той самий маркер системного користувача, що й Facebook (FACEBOOK_PAGE_TOKEN),
коли в ньому є instagram_basic, instagram_content_publish, instagram_manage_insights,
а Instagram підключений до сторінки й призначений боту в бізнес-портфоліо.

Instagram не приймає файл — лише публічну адресу картинки. Тому спершу завантажуємо
картинку на сторінку Facebook як неопубліковане фото й беремо його адресу.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

ENABLED = os.getenv("IG_ENABLED", "1") == "1"
POSTS = os.getenv("IG_POSTS", "1") == "1"      # 0 — лише сторіз, без дописів у стрічку
_CACHE: dict = {}


def _check(js: dict) -> dict:
    if "error" in js:
        raise RuntimeError(js["error"].get("message", str(js["error"]))[:300])
    return js


def ig_user_id(graph: str, page_id: str, page_token: str) -> str | None:
    """ID бізнес-акаунта Instagram, підключеного до сторінки (None — якщо не підключений або немає дозволів)."""
    if "id" in _CACHE:
        return _CACHE["id"]
    js = requests.get(f"{graph}/{page_id}", params={"fields": "instagram_business_account{id,username}",
                                                     "access_token": page_token}, timeout=30).json()
    acc = js.get("instagram_business_account") or {}
    _CACHE["id"], _CACHE["username"] = acc.get("id"), acc.get("username")
    return _CACHE["id"]


def _public_url(graph: str, page_id: str, page_token: str, image: Path) -> str:
    with image.open("rb") as fh:
        up = _check(requests.post(f"{graph}/{page_id}/photos",
                                  data={"published": "false", "temporary": "true", "access_token": page_token},
                                  files={"source": fh}, timeout=120).json())
    js = _check(requests.get(f"{graph}/{up['id']}", params={"fields": "images", "access_token": page_token},
                             timeout=30).json())
    return max(js["images"], key=lambda i: i.get("width", 0))["source"]


def _publish(graph: str, ig_id: str, token: str, data: dict) -> str:
    cid = _check(requests.post(f"{graph}/{ig_id}/media", data={**data, "access_token": token}, timeout=60).json())["id"]
    for _ in range(15):  # Instagram обробляє картинку кілька секунд
        st = requests.get(f"{graph}/{cid}", params={"fields": "status_code", "access_token": token}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram не обробив картинку: {st.get('status_code')}")
        time.sleep(3)
    return _check(requests.post(f"{graph}/{ig_id}/media_publish",
                                data={"creation_id": cid, "access_token": token}, timeout=60).json())["id"]


def adapt_caption(fb_text: str) -> str:
    """Посилання в Instagram не клікабельні — ведемо в шапку профілю."""
    text = fb_text.replace("Посилання в першому коментарі 👇", "Посилання на Telegram — у шапці профілю.")
    return text[:2200]


def post(graph: str, page_id: str, page_token: str, fb_text: str, image: Path, log, alert,
         dry_run: bool = False) -> bool:
    if not ENABLED or not POSTS or not page_id or not page_token or not image.exists():
        return False
    try:
        ig_id = ig_user_id(graph, page_id, page_token)
        if not ig_id:
            log("Instagram: акаунт не підключений до сторінки або немає дозволів — пропускаю.")
            return False
        if dry_run:
            log("DRY_RUN — в Instagram не публікується. Підпис:\n" + adapt_caption(fb_text))
            return True
        url = _public_url(graph, page_id, page_token, image)
        media_id = _publish(graph, ig_id, page_token, {"image_url": url, "caption": adapt_caption(fb_text)})
        log(f"Допис в Instagram опубліковано: {media_id}")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"Instagram: помилка — {e}")
        alert(f"Не вдалося опублікувати в Instagram: {e}")
        return False


def post_story(graph: str, page_id: str, page_token: str, image: Path, log, alert, dry_run: bool = False) -> None:
    if not ENABLED or not page_id or not page_token or dry_run or not image.exists():
        return
    try:
        ig_id = ig_user_id(graph, page_id, page_token)
        if not ig_id:
            return
        url = _public_url(graph, page_id, page_token, image)
        media_id = _publish(graph, ig_id, page_token, {"image_url": url, "media_type": "STORIES"})
        log(f"Сторіз в Instagram опубліковано: {media_id}")
    except Exception as e:  # noqa: BLE001
        log(f"Instagram-сторіз: помилка — {e}")
        alert(f"Сторіз в Instagram не опубліковано: {e}")


def followers(graph: str, page_id: str, page_token: str, log) -> int | None:
    if not ENABLED or not page_id or not page_token:
        return None
    try:
        ig_id = ig_user_id(graph, page_id, page_token)
        if not ig_id:
            return None
        js = _check(requests.get(f"{graph}/{ig_id}", params={"fields": "followers_count", "access_token": page_token},
                                 timeout=30).json())
        return int(js.get("followers_count") or 0)
    except Exception as e:  # noqa: BLE001
        log(f"Instagram: підписників не отримано — {e}")
        return None
