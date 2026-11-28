"""Прев'ю відео в Суперчат АКМЦ, гілка «DeepState» (тести) – з 28.11.2026, замість видаленої «Тіні».

Запускається workflow «Прев'ю в Тести», коли Claude змінює файл preview/request.json
(у ньому – що саме показати). Нічого не публікує в канали й соцмережі й нічого не комітить.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

os.environ["TIKTOK_PREVIEW"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tracker as T  # noqa: E402

T.THREAD_TIKTOK = T.THREAD_TESTS          # усе, що зазвичай іде в гілку «TikTok», – у тести


def say(text: str) -> None:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_ALERT_CHAT_ID", "")
    if token and chat:
        T.tg("sendMessage", token, {"chat_id": chat, "message_thread_id": T.THREAD_TESTS, "text": text})


def video(kind: str, d0: str, d1: str) -> None:
    rows = [x for x in T.read_history() if d0 <= x["date"] <= d1]
    if not rows:
        say(f"🧪 Прев'ю {kind}: даних за {d0}–{d1} немає")
        return
    start = min(x["compared_with"] for x in rows)
    nets = {k: T._net_period(rows, k) for k in T.OBLASTS}
    rp = {k: [p for x in rows for p in (x.get(f"{k}_newly_places") or "").split("; ") if p] for k in T.OBLASTS}
    main = max(T.OBLASTS, key=lambda k: abs(nets.get(k) or 0))
    mp = T.make_change_map(kind, [x["date"] for x in rows], nets, rp, f"map_{kind}.jpg", T._change_areas(rows, main))
    lbl = T._period_label(start, d1)
    cover = T.COVER_WEEK if kind == "week" else getattr(T, "COVER_MONTH", T.COVER_WEEK)
    T.send_tiktok_pack(kind, rows, cover, lbl, start, mp)


def main() -> None:
    req = json.loads((Path(__file__).parent / "request.json").read_text())
    today = date.today().isoformat()
    say("🧪 ПРЕВ'Ю (не публікується): " + req.get("note", "перевірка відео"))
    for item in req.get("items", []):
        kind = item["kind"]
        d1 = item.get("to") or today
        d0 = item.get("from") or (date.fromisoformat(d1) - timedelta(days=7 if kind == "week" else 30)).isoformat()
        try:
            say(f"🧪 {'Тижневе' if kind == 'week' else 'Місячне'} відео: {d0} – {d1}")
            video(kind, d0, d1)
        except Exception as e:  # noqa: BLE001
            say(f"⚠️ Прев'ю {kind} не вдалося: {str(e)[:300]}")
    say("🧪 Кінець прев'ю. Зауваження – Claude в проєкт «Автоматизація редакції АКМЦ».")


if __name__ == "__main__":
    main()
