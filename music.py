"""Фонова музика для відео — власна, згенерована тут же (без чужих треків, прав і ліцензій).

Стиль — спокійне «кінематографічне» фортепіано: м'які арпеджіо з довгим відлунням, світлий настрій
(не похмурий), як у популярних фортепіанних треках для коротких відео. Мелодії — власні: звичайні
гармонічні послідовності, розкладені на арпеджіо; жоден відомий твір не відтворюється.
Чотири варіанти (різні тональності й малюнки) — бот чергує їх щотижня.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
A4 = 440.0
NOTE = {"C": -9, "C#": -8, "D": -7, "D#": -6, "E": -5, "F": -4, "F#": -3, "G": -2, "G#": -1, "A": 0, "A#": 1, "B": 2}


def _hz(semi: int) -> float:
    return A4 * 2 ** (semi / 12)


def n(name: str, octave: int) -> int:
    return NOTE[name] + 12 * (octave - 4)


def chord(root: str, kind: str, octave: int = 3) -> list[int]:
    r = n(root, octave)
    steps = {"maj": [0, 4, 7], "min": [0, 3, 7], "sus2": [0, 2, 7], "add9": [0, 4, 7, 14]}[kind]
    return [r + s for s in steps]


# (акорди, малюнок арпеджіо — індекси нот акорду, темп BPM)
VARIANTS = [
    ([("C", "maj"), ("G", "maj"), ("A", "min"), ("F", "maj")], [0, 1, 2, 3, 2, 1, 2, 3], 76),   # C — світлий
    ([("G", "maj"), ("D", "maj"), ("E", "min"), ("C", "add9")], [0, 2, 1, 3, 2, 3, 1, 2], 72),  # G — теплий
    ([("F", "maj"), ("A", "min"), ("D", "min"), ("C", "sus2")], [0, 1, 2, 1, 3, 1, 2, 1], 70),  # F — м'який
    ([("D", "maj"), ("A", "maj"), ("B", "min"), ("G", "maj")], [0, 2, 3, 2, 1, 2, 3, 2], 78),   # D — надійний
]


def _piano(freq: float, dur: float, vel: float) -> np.ndarray:
    """М'який фортепіанний тон: гармоніки з різним загасанням."""
    t = np.arange(int(dur * SR)) / SR
    tone = np.zeros_like(t)
    for h, amp, dec in ((1, 1.0, 1.6), (2, 0.42, 2.4), (3, 0.18, 3.4), (4, 0.09, 4.5), (5, 0.04, 6.0)):
        f = freq * h * (1 + 0.0004 * h * h)
        tone += amp * np.sin(2 * np.pi * f * t) * np.exp(-dec * t)
    attack = np.clip(t / 0.006, 0, 1)
    rel = np.clip((dur - t) / 0.25, 0, 1)
    return tone * attack * rel * vel


def _reverb(x: np.ndarray, seconds: float = 2.4, mix: float = 0.32) -> np.ndarray:
    rng = np.random.default_rng(3)
    L = int(seconds * SR)
    ir = rng.standard_normal(L) * np.exp(-np.linspace(0, 7, L))
    ir = np.convolve(ir, np.ones(30) / 30, mode="same")
    size = 1 << int(np.ceil(np.log2(len(x) + L)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return (1 - mix) * x + mix * wet / (np.abs(wet).max() + 1e-9) * np.abs(x).max()


def make_ambient(seconds: float, out: Path, variant: int | None = None) -> Path:
    if variant is None:
        import datetime
        variant = datetime.date.today().isocalendar()[1]
    prog, pattern, bpm = VARIANTS[variant % len(VARIANTS)]
    eighth = 60 / bpm / 2
    total = int((seconds + 3) * SR)
    mix = np.zeros(total)
    pos, bar = 0.0, 0
    while pos < seconds:
        root, kind = prog[bar % len(prog)]
        ch = chord(root, kind, 3) + [chord(root, kind, 4)[0]]
        b = _piano(_hz(n(root, 2)), eighth * 8 + 1.0, 0.55)
        i0 = int(pos * SR)
        mix[i0:i0 + len(b)] += b[: max(0, total - i0)]
        for k, idx in enumerate(pattern):
            s = _piano(_hz(ch[idx % len(ch)] + 12), eighth * 3, 0.42 if k % 4 == 0 else 0.30)
            j = int((pos + k * eighth) * SR)
            if j < total:
                mix[j:j + len(s)] += s[: total - j]
        pos += eighth * 8
        bar += 1
    mix = _reverb(mix[: int(seconds * SR)])
    fi, fo = int(0.4 * SR), int(2.0 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)
    mix[-fo:] *= np.linspace(1, 0, fo) ** 1.5
    mix = mix / (np.abs(mix).max() + 1e-9) * 0.30
    stereo = np.stack([mix, np.roll(mix, int(0.009 * SR))], axis=1)
    data = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return out
