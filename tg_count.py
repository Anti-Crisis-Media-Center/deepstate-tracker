"""Щодня записує кількість підписників @acmc_ua у data/tg_subscribers.csv (дата,кількість).
Telegram не зберігає історії підписників і показує боту лише число «зараз», тому для «+1 / −1» у звіті
аналітики ведемо власний запис (рішення 12.11.2026). Запускається окремим кроком у daily.yml після
публікації; збій тут на пости не впливає."""
import csv
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

FILE = Path("data/tg_subscribers.csv")
tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
chat = os.getenv("TELEGRAM_CHAT_ID", "@acmc_ua").strip() or "@acmc_ua"
today = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d")
n = requests.get(f"https://api.telegram.org/bot{tok}/getChatMemberCount", params={"chat_id": chat},
                 timeout=30).json().get("result")
if not isinstance(n, int):
    raise SystemExit("Не вдалося отримати кількість підписників")
rows = list(csv.reader(FILE.open())) if FILE.exists() else [["date", "subscribers"]]
if any(r and r[0] == today for r in rows[1:]):
    print("Сьогодні вже записано")
else:
    rows.append([today, str(n)])
    FILE.parent.mkdir(exist_ok=True)
    csv.writer(FILE.open("w", newline="")).writerows(rows)
    print("Записано:", today, n)
