"""ЗАПАСНА фонова музика для відео — згенерована тут же (без чужих треків, прав і ліцензій).
Основна з 10.11.2026 — трек Сергія assets/music/news.mp3 (див. video.py); цей файл потрібен,
лише якщо основного треку немає.

Два стилі:
  pad   — спокійний мінорний «пад» (м'які протяжні акорди з плавними переходами);
  piano — приглушене, розмите фортепіано в мінорі (м'які клавіші, довге відлуння).
Бот чергує варіанти зі списку ROTATION щотижня.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
N = {"A2": 110.0, "C3": 130.81, "E3": 164.81, "F2": 87.31, "G2": 98.0, "A3": 220.0, "C4": 261.63,
     "E4": 329.63, "F3": 174.61, "G3": 196.0, "B3": 246.94, "D4": 293.66, "D3": 146.83, "E2": 82.41,
     "F4": 349.23, "G4": 392.0, "A4": 440.0, "D2": 73.42, "B2": 123.47, "G#3": 207.65, "C5": 523.25,
     "B4": 493.88, "E5": 659.25, "D5": 587.33, "A#2": 116.54, "A#3": 233.08}

# Пади: (бас, ноти акорду), по 4 с на акорд, з накладанням — плавні переходи
PADS = {
    "pad1": [("A2", ["A3", "C4", "E4"]), ("F2", ["F3", "A3", "C4"]), ("C3", ["G3", "C4", "E4"]), ("G2", ["G3", "B3", "D4"])],
    "pad2": [("A2", ["A3", "C4", "E4"]), ("F2", ["F3", "A3", "C4"]), ("D2", ["D4", "F4", "A3"]), ("E2", ["E3", "G#3", "B3"])],
    "pad3": [("D2", ["D4", "F4", "A3"]), ("A#2", ["A#3", "D4", "F4"]), ("F2", ["F3", "A3", "C4"]), ("A2", ["A3", "C4", "E4"])],
}
# Приглушене фортепіано: акорди (бас, ноти), малюнок — повільні розкладені ноти
PIANO = [("A2", ["A3", "C4", "E4", "A4"]), ("F2", ["F3", "A3", "C4", "F4"]),
         ("C3", ["C4", "E4", "G4", "C5"]), ("E2", ["E3", "G#3", "B3", "E4"])]

ROTATION = ["pad1", "pad2", "piano"]   # за номером тижня


def _env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a, r = int(attack * SR), int(release * SR)
    e[:a] = np.linspace(0, 1, a) ** 2
    e[-r:] = np.linspace(1, 0, r) ** 2
    return e


def _pad(seconds: float, chords, chord_sec: float = 4.0) -> np.ndarray:
    total = int(seconds * SR) + SR
    mix = np.zeros(total)
    t0, i = 0, 0
    while t0 < total:
        bass, notes = chords[i % len(chords)]
        n = int((chord_sec + 1.5) * SR)
        t = np.arange(n) / SR
        tone = 0.22 * np.sin(2 * np.pi * N[bass] * 2 * t)   # бас — на октаву вище і тихіше: на телефоні не «деренчить»
        for nm in notes:
            f = N[nm]
            tone += 0.35 * (np.sin(2 * np.pi * f * t) + np.sin(2 * np.pi * f * 1.003 * t)) / 2
        tone *= _env(n, 1.2, 1.8)
        end = min(total, t0 + n)
        mix[t0:end] += tone[: end - t0]
        t0 += int(chord_sec * SR)
        i += 1
    mix = mix[: int(seconds * SR)]
    return np.convolve(mix, np.ones(40) / 40, mode="same")


def _soft_key(freq: float, dur: float, vel: float) -> np.ndarray:
    """Приглушена клавіша: майже без верхніх гармонік, м'який наступ, довге загасання."""
    t = np.arange(int(dur * SR)) / SR
    tone = (np.sin(2 * np.pi * freq * t) * np.exp(-0.9 * t)
            + 0.18 * np.sin(2 * np.pi * freq * 2.001 * t) * np.exp(-1.8 * t)
            + 0.05 * np.sin(2 * np.pi * freq * 3.003 * t) * np.exp(-3.0 * t))
    attack = np.clip(t / 0.03, 0, 1) ** 1.5           # без різкого удару
    rel = np.clip((dur - t) / 0.6, 0, 1)
    return tone * attack * rel * vel


def _reverb(x: np.ndarray, seconds: float = 3.2, mix: float = 0.5) -> np.ndarray:
    rng = np.random.default_rng(5)
    L = int(seconds * SR)
    ir = rng.standard_normal(L) * np.exp(-np.linspace(0, 6, L))
    ir = np.convolve(ir, np.ones(60) / 60, mode="same")
    size = 1 << int(np.ceil(np.log2(len(x) + L)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return (1 - mix) * x + mix * wet / (np.abs(wet).max() + 1e-9) * np.abs(x).max()


def _piano(seconds: float, bpm: float = 58) -> np.ndarray:
    beat = 60 / bpm
    total = int((seconds + 4) * SR)
    mix = np.zeros(total)
    pos, bar = 0.0, 0
    pattern = [0, 2, 1, 3, 2, 1]                       # повільний «перебір», по пів долі
    while pos < seconds:
        bass, notes = PIANO[bar % len(PIANO)]
        b = _soft_key(N[bass] * 2, beat * 4 + 2, 0.28)
        i0 = int(pos * SR)
        mix[i0:i0 + len(b)] += b[: max(0, total - i0)]
        for k, idx in enumerate(pattern):
            s = _soft_key(N[notes[idx]], beat * 2.5, 0.30 if k else 0.36)
            j = int((pos + k * beat * 4 / len(pattern)) * SR)
            if j < total:
                mix[j:j + len(s)] += s[: total - j]
        pos += beat * 4
        bar += 1
    mix = mix[: int(seconds * SR)]
    mix = np.convolve(mix, np.ones(25) / 25, mode="same")   # ще м'якше — «розмито»
    return _reverb(mix)


def make_ambient(seconds: float, out: Path, variant: int | str | None = None) -> Path:
    if variant is None:
        import datetime
        variant = datetime.date.today().isocalendar()[1]
    name = variant if isinstance(variant, str) else ROTATION[variant % len(ROTATION)]
    mix = _piano(seconds) if name == "piano" else _pad(seconds, PADS[name])
    # чистий звук: прибираємо глибокі низи (динаміки телефонів на них тріщать) і різкі верхи
    hp = np.convolve(mix, np.ones(300) / 300, mode="same")      # усе нижче ~150 Гц
    mix = mix - hp
    mix = np.convolve(mix, np.ones(9) / 9, mode="same")         # м'якше вгорі (~4 кГц)
    mix *= _env(len(mix), 1.5, 2.0)
    mix = mix / (np.abs(mix).max() + 1e-9) * (0.24 if name == "piano" else 0.18)
    stereo = np.stack([mix, np.roll(mix, int(0.012 * SR))], axis=1)
    data = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return out
