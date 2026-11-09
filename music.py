"""Тиха фонова музика для відео — згенерована тут же, без сторонніх треків (жодних прав і ліцензій).

Спокійний «пад» у мінорі: акорди Am – F – C – G по 4 секунди, м'які синусоїди з легким
розстроюванням, повільний наступ і загасання, тихий бас. Гучність низька — це фон, не мелодія.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
# частоти нот (Гц)
N = {"A2": 110.0, "C3": 130.81, "E3": 164.81, "F2": 87.31, "G2": 98.0, "A3": 220.0, "C4": 261.63,
     "E4": 329.63, "F3": 174.61, "G3": 196.0, "B3": 246.94, "D4": 293.66}
N.update({"D3": 146.83, "E2": 82.41, "F4": 349.23, "G4": 392.0, "A4": 440.0, "D2": 73.42, "B2": 123.47})
# Кілька спокійних мінорних варіантів — бот чергує їх за номером тижня, щоб не звучало однаково
VARIANTS = [
    [("A2", ["A3", "C4", "E4"]), ("F2", ["F3", "A3", "C4"]), ("C3", ["G3", "C4", "E4"]), ("G2", ["G3", "B3", "D4"])],
    [("D2", ["D4", "F4", "A4"]), ("B2", ["B3", "D4", "F4"]), ("F2", ["F3", "A3", "C4"]), ("A2", ["A3", "C4", "E4"])],
    [("E2", ["E3", "G3", "B3"]), ("C3", ["C4", "E4", "G4"]), ("G2", ["G3", "B3", "D4"]), ("D3", ["D4", "F4", "A4"])],
    [("A2", ["A3", "C4", "E4"]), ("D3", ["D4", "F4", "A4"]), ("F2", ["F3", "A3", "C4"]), ("E2", ["E3", "G3", "B3"])],
]
CHORDS = VARIANTS[0]


def _env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a, r = int(attack * SR), int(release * SR)
    e[:a] = np.linspace(0, 1, a) ** 2
    e[-r:] = np.linspace(1, 0, r) ** 2
    return e


def make_ambient(seconds: float, out: Path, chord_sec: float = 4.0, variant: int | None = None) -> Path:
    if variant is None:
        import datetime
        variant = datetime.date.today().isocalendar()[1]
    chords = VARIANTS[variant % len(VARIANTS)]
    total = int(seconds * SR) + SR
    mix = np.zeros(total)
    t0 = 0
    i = 0
    while t0 < total:
        bass, notes = chords[i % len(chords)]
        n = int((chord_sec + 1.5) * SR)          # акорди трохи накладаються — плавні переходи
        t = np.arange(n) / SR
        tone = 0.5 * np.sin(2 * np.pi * N[bass] * t)
        for nm in notes:
            f = N[nm]
            tone += 0.35 * (np.sin(2 * np.pi * f * t) + np.sin(2 * np.pi * f * 1.003 * t)) / 2
        tone *= _env(n, 1.2, 1.8)
        end = min(total, t0 + n)
        mix[t0:end] += tone[: end - t0]
        t0 += int(chord_sec * SR)
        i += 1
    mix = mix[: int(seconds * SR)]
    k = np.ones(40) / 40                          # дуже м'який фільтр — без «дзвону»
    mix = np.convolve(mix, k, mode="same")
    mix *= _env(len(mix), 1.5, 2.0)               # загальний наступ і затухання
    mix = mix / (np.abs(mix).max() + 1e-9) * 0.18  # тихо (~ -15 дБ)
    stereo = np.stack([mix, np.roll(mix, int(0.012 * SR))], axis=1)   # трохи «ширини»
    data = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return out
