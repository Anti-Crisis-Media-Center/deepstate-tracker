"""Звіт аналітики на вимогу — кнопками в гілці «Аналітика» службової групи.

Як це працює (з 10.11.2026): Telegram одразу пересилає натискання кнопок у Make (вебхук),
Make запускає цю задачу на GitHub і передає їй натискання (параметр update, base64).
Задача відповідає «⏳ Готую…» і віддає список днів наступному кроку (analytics.py).
Звіт приходить через 2–5 хвилин після натискання.

Сценарій Make: «Webhooks → Custom webhook» (JSON pass-through) → «HTTP → Make a request»:
POST https://api.github.com/repos/Anti-Crisis-Media-Center/deepstate-tracker/actions/workflows/analytics-ondemand.yml/dispatches
тіло: {"ref":"main","inputs":{"update":"{{base64(1.value)}}"}}, токен GitHub — у заголовку Authorization.
Адресу вебхука Make бот отримує один раз: Run workflow → поле «webhook_url».

Як попросити звіт:
• кнопка під закріпленим повідомленням бота: Доба / 3 дні / 7 днів / 14 днів / 30 днів;
• або відповісти (Reply) на це повідомлення числом днів, напр. «5»;
• або написати в групі /report 5 (від 1 до 90 днів).

Перший раз (і якщо закріплене повідомлення загубилось): Actions → «Аналітика на вимогу»
→ Run workflow → поставити галочку «Опублікувати кнопки».
"""
import os
import re
import sys

import requests

TOK = os.getenv("ANALYTICS_BOT_TOKEN", "").strip()
CHAT = os.getenv("ANALYTICS_CHAT_ID", "").strip() or os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
THREAD = os.getenv("THREAD_ANALYTICS", "5")
API = f"https://api.telegram.org/bot{TOK}/"
BUTTONS = [(1, "Доба"), (3, "3 дні"), (7, "7 днів"), (14, "14 днів"), (30, "30 днів")]


def tg(method: str, **data) -> dict:
    try:
        return requests.post(API + method, json=data, timeout=40).json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def setup() -> None:
    kb = {"inline_keyboard": [[{"text": t, "callback_data": f"rep:{d}"} for d, t in BUTTONS]]}
    r = tg("sendMessage", chat_id=CHAT, message_thread_id=int(THREAD), reply_markup=kb, parse_mode="HTML",
           text="📊 <b>Звіт аналітики на вимогу</b>\n\nНатисніть, за який період потрібен звіт. "
                "Або відповідайте на це повідомлення числом днів (1–90), напр. «5».\n\n"
                "<i>Звіт приходить сюди через 2–5 хвилин.</i>")
    print("Кнопки:", "опубліковано" if r.get("ok") else r)
    if r.get("ok"):
        tg("pinChatMessage", chat_id=CHAT, message_id=r["result"]["message_id"], disable_notification=True)


def set_webhook(url: str) -> None:
    """Telegram надсилатиме натискання кнопок і повідомлення боту одразу на адресу Make."""
    r = tg("setWebhook", url=url, allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    print("Вебхук:", "встановлено" if r.get("ok") else r)


def updates_from_env() -> list[dict] | None:
    """Натискання, передане з Make (base64 від JSON оновлення Telegram)."""
    raw = os.getenv("UPDATE_B64", "").strip()
    if not raw:
        return None
    import base64
    import json
    try:
        return [json.loads(base64.b64decode(raw).decode("utf-8"))]
    except Exception as e:  # noqa: BLE001
        print("Не вдалося прочитати update:", e)
        return []


def collect() -> list[int]:
    """Забирає нові запити; повертає список днів (без повторів, до 3 звітів за раз)."""
    res = updates_from_env()
    if res is None:   # запасний шлях без Make (працює, лише поки вебхук не встановлено)
        upd = tg("getUpdates", timeout=0, allowed_updates=["message", "callback_query"])
        if not upd.get("ok"):
            print("getUpdates:", upd)
            return []
        res = upd.get("result", [])
        if res:   # підтверджуємо отримане, щоб не обробити вдруге
            tg("getUpdates", offset=res[-1]["update_id"] + 1, timeout=0)
    days: list[int] = []
    for u in res:
        cq = u.get("callback_query")
        if cq:
            msg = cq.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != CHAT:
                continue
            m = re.match(r"rep:(\d+)$", cq.get("data", ""))
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Прийнято, звіт буде за кілька хвилин")
            if m:
                days.append(int(m.group(1)))
            continue
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != CHAT:
            continue
        text = (msg.get("text") or "").strip()
        m = re.match(r"^/report(?:@\w+)?\s+(\d+)", text) or (
            re.match(r"^(\d+)", text) if (msg.get("reply_to_message") or {}).get("from", {}).get("is_bot") else None)
        if m:
            days.append(int(m.group(1)))
    out = []
    for d in days:
        d = max(1, min(90, d))
        if d not in out:
            out.append(d)
    return out[:3]


if __name__ == "__main__":
    if not TOK or not CHAT:
        print("Немає ANALYTICS_BOT_TOKEN або чату — пропускаю.")
        sys.exit(0)
    if os.getenv("WEBHOOK_URL", "").strip():
        set_webhook(os.getenv("WEBHOOK_URL").strip())
    if os.getenv("SETUP") == "1":
        setup()
    days = collect()
    for d in days:
        per = "добу" if d == 1 else f"{d} дн."
        tg("sendMessage", chat_id=CHAT, message_thread_id=int(THREAD), text=f"⏳ Готую звіт за {per}…",
           disable_notification=True)
    print("Запити:", days or "немає")
    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"days={' '.join(map(str, days))}\n")
