"""Публікація зведень DeepState у Threads і збір статистики акаунта.

Секрети (GitHub → Settings → Secrets and variables → Actions):
  THREADS_TOKEN          — довгостроковий маркер Threads (діє 60 днів);
  THREADS_TELEGRAM_LINK  — окреме запрошення в Telegram для Threads (щоб рахувати переходи);
  GH_ADMIN_TOKEN         — необов'язково: ключ GitHub з правом змінювати секрети,
                           тоді бот сам зберігатиме продовжений маркер Threads.

Threads не приймає файл картинки — лише публічне посилання. Обкладинки лежать у
публічному репозиторії, тож беремо їх звідти (raw.githubusercontent.com).
"""
from __future__ import annotations

import base64
import os
import time
from datetime import datetime
from pathlib import Path

import requests

GRAPH = "https://graph.threads.net/v1.0"
REPO = os.getenv("GITHUB_REPOSITORY", "Anti-Crisis-Media-Center/deepstate-tracker")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"
MAX_LEN = 500


def _token() -> str:
    return os.getenv("THREADS_TOKEN", "").strip()


def enabled() -> bool:
    return bool(_token())


def _check(js: dict) -> dict:
    if "error" in js:
        raise RuntimeError(js["error"].get("message", str(js["error"]))[:300])
    return js


def _user_id() -> str:
    return _check(requests.get(f"{GRAPH}/me", params={"fields": "id,username", "access_token": _token()},
                               timeout=30).json())["id"]


def image_url(image: Path) -> str | None:
    """Публічна адреса обкладинки з репозиторію (лише для файлів у корені репозиторію)."""
    root = Path(__file__).resolve().parent
    try:
        rel = image.resolve().relative_to(root).as_posix()
    except ValueError:
        return None
    return f"{RAW_BASE}/{rel}?v={datetime.now():%Y%m%d}"


def _wait_ready(container_id: str, tries: int = 10) -> None:
    """Threads обробляє картинку кілька секунд — чекаємо, поки контейнер буде готовий."""
    for _ in range(tries):
        js = requests.get(f"{GRAPH}/{container_id}", params={"fields": "status,error_message",
                                                              "access_token": _token()}, timeout=30).json()
        status = js.get("status")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(js.get("error_message") or status)
        time.sleep(3)


def _publish(uid: str, data: dict) -> str:
    data = {**data, "access_token": _token()}
    cid = _check(requests.post(f"{GRAPH}/{uid}/threads", data=data, timeout=60).json())["id"]
    _wait_ready(cid)
    return _check(requests.post(f"{GRAPH}/{uid}/threads_publish",
                                data={"creation_id": cid, "access_token": _token()}, timeout=60).json())["id"]


def adapt_text(fb_text: str) -> str:
    """Тизер із Facebook → Threads. Threads — окремий канал, а не воронка в Telegram:
    прибираємо абзаци із закликом перейти в Telegram, лишаємо саму інформацію. Ліміт 500 знаків."""
    paras = [p for p in fb_text.split("\n\n") if "Telegram" not in p and "коментар" not in p]
    text = "\n\n".join(paras)
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1].rstrip() + "…"
    return text


def post(fb_text: str, image: Path, log, alert, dry_run: bool = False) -> bool:
    """Допис із картинкою + відповідь із посиланням на Telegram. Помилка не зупиняє бота."""
    if not enabled():
        return False
    text = adapt_text(fb_text)
    link = (os.getenv("THREADS_TELEGRAM_LINK", "").strip()
            or os.getenv("FB_TELEGRAM_LINK", "").strip() or "https://t.me/acmc_ua")
    reply = f"Ці зведення щоранку о 8:00 виходять і в нашому Telegram-каналі: {link}"
    if dry_run:
        log("DRY_RUN — у Threads не публікується. Текст:\n" + text)
        return True
    try:
        uid = _user_id()
        url = image_url(image) if image.exists() else None
        data = {"media_type": "IMAGE", "image_url": url, "text": text} if url else {"media_type": "TEXT", "text": text}
        post_id = _publish(uid, data)
        log(f"Допис у Threads опубліковано: {post_id}")
        try:
            _publish(uid, {"media_type": "TEXT", "text": reply, "reply_to_id": post_id})
        except Exception as e:  # noqa: BLE001
            log(f"Threads: відповідь із посиланням не додано — {e}")
            alert(f"Допис у Threads вийшов, але відповідь із посиланням на Telegram не додано: {e}")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"Threads: помилка — {e}")
        alert(f"Не вдалося опублікувати у Threads: {e}")
        return False


# ============================ СТАТИСТИКА ============================
def followers(log) -> int | None:
    """Кількість підписників акаунта Threads (для щоденного запису в історію)."""
    if not enabled():
        return None
    try:
        uid = _user_id()
        js = _check(requests.get(f"{GRAPH}/{uid}/threads_insights",
                                 params={"metric": "followers_count", "access_token": _token()}, timeout=30).json())
        for m in js.get("data", []):
            if m.get("name") == "followers_count":
                return int(m.get("total_value", {}).get("value") or 0)
    except Exception as e:  # noqa: BLE001
        log(f"Threads: статистику не отримано — {e}")
    return None


def account_insights(since: datetime, until: datetime, log) -> dict:
    """Перегляди, вподобання, відповіді, репости за період — для тижневого звіту редакції."""
    if not enabled():
        return {}
    try:
        uid = _user_id()
        js = _check(requests.get(f"{GRAPH}/{uid}/threads_insights", params={
            "metric": "views,likes,replies,reposts,quotes",
            "since": int(since.timestamp()), "until": int(until.timestamp()),
            "access_token": _token()}, timeout=30).json())
        out = {}
        for m in js.get("data", []):
            if "total_value" in m:
                out[m["name"]] = m["total_value"].get("value", 0)
            else:
                out[m["name"]] = sum(v.get("value", 0) for v in m.get("values", []))
        return out
    except Exception as e:  # noqa: BLE001
        log(f"Threads: статистику за період не отримано — {e}")
        return {}


# ============================ ПРОДОВЖЕННЯ МАРКЕРА ============================
def _save_secret(name: str, value: str) -> bool:
    """Записує секрет у GitHub (потрібен GH_ADMIN_TOKEN з правом Secrets: write)."""
    gh = os.getenv("GH_ADMIN_TOKEN", "").strip()
    if not gh:
        return False
    from nacl import encoding, public  # PyNaCl
    hdr = {"Authorization": f"Bearer {gh}", "Accept": "application/vnd.github+json"}
    key = requests.get(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key", headers=hdr, timeout=30).json()
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    enc = base64.b64encode(box.encrypt(value.encode())).decode()
    r = requests.put(f"https://api.github.com/repos/{REPO}/actions/secrets/{name}", headers=hdr,
                     json={"encrypted_value": enc, "key_id": key["key_id"]}, timeout=30)
    return r.status_code in (201, 204)


def refresh_token(log, alert) -> None:
    """Раз на тиждень продовжує маркер Threads ще на 60 днів."""
    if not enabled():
        return
    try:
        js = _check(requests.get("https://graph.threads.net/refresh_access_token",
                                 params={"grant_type": "th_refresh_token", "access_token": _token()},
                                 timeout=30).json())
        new, days = js.get("access_token", ""), int(js.get("expires_in", 0)) // 86400
        if not new or new == _token():
            log(f"Маркер Threads продовжено, діє ще ~{days} дн.")
            return
        if _save_secret("THREADS_TOKEN", new):
            log(f"Маркер Threads продовжено й збережено в секретах, діє ще ~{days} дн.")
        else:
            alert("Маркер Threads продовжено, але зберегти новий автоматично не вдалося (немає GH_ADMIN_TOKEN). "
                  f"Старий діє ще ~{days} дн. — напишіть Claude, щоб налаштувати автозбереження.")
    except Exception as e:  # noqa: BLE001
        log(f"Threads: маркер не продовжено — {e}")
        alert(f"Не вдалося продовжити маркер Threads: {e}. Якщо він прострочиться, публікації в Threads зупиняться.")
