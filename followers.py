"""Кількість підписників на всіх майданчиках — щодня в data/followers.csv (дата,майданчик,кількість) і зміна
«(+2)» у звіті аналітики (рішення 12–13.11.2026). Майже ніде платформи не віддають «скільки було тиждень тому»,
тому ведемо власний запис: щоранку окремим кроком daily.yml після публікації (python followers.py); збій на пости
не впливає. Зміна з'являється, щойно в записі є день до початку періоду; поки запис коротший — порівнюємо
з першим днем запису й пишемо його дату."""
import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FILE = Path("data/followers.csv")
OLD_TG = Path("data/tg_subscribers.csv")   # перший запис Telegram (13.11.2026) — переносимо сюди
KYIV = ZoneInfo("Europe/Kyiv")


def _rows() -> list[tuple[str, str, int]]:
    rows = []
    if FILE.exists():
        for r in list(csv.reader(FILE.open()))[1:]:
            if len(r) > 2 and r[2].lstrip("-").isdigit():
                rows.append((r[0], r[1], int(r[2])))
    elif OLD_TG.exists():
        for r in list(csv.reader(OLD_TG.open()))[1:]:
            if len(r) > 1 and r[1].isdigit():
                rows.append((r[0], "telegram", int(r[1])))
    return rows


def delta(key: str, now_count, since: datetime, now: datetime | None = None) -> str:
    """« (+2)», « (без змін)», « (+2 з 13.11)» або «» — якщо порівнювати ні з чим."""
    try:
        now_count = int(str(now_count).replace(" ", ""))
    except (TypeError, ValueError):
        return ""
    rows = sorted((d, c) for d, k, c in _rows() if k == key)
    if not rows:
        return ""
    start = since.astimezone(KYIV).strftime("%Y-%m-%d")
    today = (now or datetime.now(KYIV)).astimezone(KYIV).strftime("%Y-%m-%d")
    before = [r for r in rows if r[0] <= start]
    base_date, base = before[-1] if before else rows[0]
    if base_date == today:
        return ""
    d = now_count - base
    note = "" if before else f" з {base_date[8:10]}.{base_date[5:7]}"
    return f" ({'+' if d > 0 else ''}{d}{note})" if d else f" (без змін{note})"


def collect(log=print) -> dict[str, int]:
    """Поточна кількість підписників на кожному майданчику, де її вдається отримати."""
    import os
    import analytics as A
    out: dict[str, int] = {}

    def put(key, fn):
        try:
            v = fn()
            if v is not None:
                out[key] = int(v)
        except Exception as e:  # noqa: BLE001
            log(f"Підписники {key}: {e}")

    put("telegram", A.telegram)
    if A.YELLOW_PAGE_ID:
        put("fb_yellow", lambda: (A.facebook(A.YELLOW_PAGE_ID) or {}).get("followers"))
    put("fb_white", lambda: (A.facebook() or {}).get("followers"))
    put("instagram", lambda: (A.instagram() or {}).get("followers"))
    put("threads", lambda: (A.threads() or {}).get("followers"))
    if os.getenv("TIKTOK_REFRESH_TOKEN", "").strip():
        put("tiktok", lambda: (A.tiktok_api.stats(A.SINCE, log) or {}).get("followers"))
    if os.getenv("YOUTUBE_API_KEY", "").strip():
        import youtube_api
        for ref in [r.strip() for r in os.getenv("YOUTUBE_CHANNELS", "").split(",") if r.strip()]:
            put(f"youtube:{ref}", lambda ref=ref: (youtube_api._channel(ref, log) or {})
                .get("statistics", {}).get("subscriberCount"))
    return out


def record(log=print) -> None:
    today = datetime.now(KYIV).strftime("%Y-%m-%d")
    rows = _rows()
    have = {(d, k) for d, k, _ in rows}
    new = [(today, k, v) for k, v in collect(log).items() if (today, k) not in have]
    rows += new
    FILE.parent.mkdir(exist_ok=True)
    w = csv.writer(FILE.open("w", newline=""))
    w.writerow(["date", "platform", "followers"])
    w.writerows(sorted(rows))
    log("Записано: " + ", ".join(f"{k}={v}" for _, k, v in new) if new else "Сьогодні вже записано")


if __name__ == "__main__":
    record()
