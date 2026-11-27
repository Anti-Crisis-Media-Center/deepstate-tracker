"""Альтернативний текст (опис картинки для людей з порушеннями зору) – з 27.11.2026, рішення Юлі.

Будуємо з тексту самого допису: цифри й назви там уже є, вигадувати нічого не треба.
Threads (до 1000 знаків) і Instagram (лише дописи-картинки) – офіційно; Facebook – поле alt_text_custom,
якщо сторінка його не прийме, бот публікує без опису (див. tracker.post_facebook)."""
from __future__ import annotations

import re
from pathlib import Path

_SKIP = re.compile(r"Підписуйтеся|Telegram|👇|За даними|Примітка|Більше деталей|Щоранку о 8:00|#")


def _facts(text: str) -> str:
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or _SKIP.search(ln) or ln.startswith("http"):
            continue
        ln = re.sub(r"^DeepState:\s*", "", ln)
        ln = ln[:1].upper() + ln[1:]
        out.append(ln if ln.endswith((".", ":", "!", "?")) else ln + ".")
    return " ".join(out)


def is_cover(image: Path | str | None) -> bool:
    return bool(image) and Path(str(image)).name.startswith("cover")


def build(text: str, image: Path | str | None = None, cover: bool | None = None, limit: int = 900) -> str:
    cover = is_cover(image) if cover is None else cover
    facts = _facts(text)
    if cover:
        alt = f"Обкладинка рубрики «DeepState» Антикризового медіа-центру. {facts}"
    else:
        legend = ("Нові ділянки підсвічено червоним і підписано назвами населених пунктів; "
                  "темно-червоним – територія, позначена окупованою раніше, синім – підконтрольна Україні, "
                  "сірим – сіра зона.")
        if re.search(r"деокуп|звільнен", facts, re.I):
            legend = legend.replace("підсвічено червоним", "підсвічено червоним (окупація) і блакитним (деокупація)")
        alt = f"Мапа за даними DeepState. {facts} {legend}"
    alt = re.sub(r"\s+", " ", alt).strip()
    return alt if len(alt) <= limit else alt[:limit - 1].rsplit(" ", 1)[0] + "…"
