from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100

# mood -> 速度、和弦（MIDI 音高）、鼓组强度、琶音亮度
MOODS: dict[str, dict] = {
    "warm": {"bpm": 92, "drums": 0.55, "arp": 0.5, "chords": [(48, 55, 60, 64), (45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59)]},
    "calm": {"bpm": 76, "drums": 0.2, "arp": 0.35, "chords": [(48, 55, 60, 64), (45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59)]},
    "upbeat": {"bpm": 118, "drums": 0.9, "arp": 0.7, "chords": [(48, 55, 60, 64), (45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59)]},
    "fresh": {"bpm": 104, "drums": 0.65, "arp": 0.6, "chords": [(50, 57, 62, 66), (47, 54, 59, 62), (43, 50, 55, 59), (45, 52, 57, 60)]},
    "tech": {"bpm": 112, "drums": 0.75, "arp": 0.65, "chords": [(45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 58), (45, 52, 57, 60)]},
    "inspiring": {"bpm": 96, "drums": 0.6, "arp": 0.55, "chords": [(48, 55, 60, 64), (43, 50, 55, 59), (45, 52, 57, 60), (41, 48, 53, 57)]},
    "soft": {"bpm": 84, "drums": 0.3, "arp": 0.4, "chords": [(50, 57, 62, 66), (47, 54, 59, 62), (45, 52, 57, 60), (43, 50, 55, 59)]},
    "clean": {"bpm": 108, "drums": 0.6, "arp": 0.55, "chords": [(48, 55, 60, 64), (45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59)]},
    "light": {"bpm": 102, "drums": 0.5, "arp": 0.5, "chords": [(48, 55, 60, 64), (45, 52, 57, 60), (41, 48, 53, 57), (43, 50, 55, 59)]},
}


def _hz(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))


def _adsr(length: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    a = max(1, int(attack * SAMPLE_RATE))
    d = max(1, int(decay * SAMPLE_RATE))
    r = max(1, int(release * SAMPLE_RATE))
    s = max(1, length - a - d - r)
    env = np.concatenate(
        [
            np.linspace(0.0, 1.0, a, endpoint=False),
            np.linspace(1.0, sustain, d, endpoint=False),
            np.full(s, sustain),
            np.linspace(sustain, 0.0, r),
        ]
    )
    if len(env) < length:
        env = np.pad(env, (0, length - len(env)))
    return env[:length]


def _osc(freq: float, length: int, detune: float = 0.0, harmonics: int = 4) -> np.ndarray:
    t = np.arange(length, dtype=np.float64) / SAMPLE_RATE
    phase = 2.0 * math.pi * freq * (1.0 + detune) * t
    signal = np.sin(phase)
    for index in range(2, harmonics + 1):
        signal = signal + (0.7 ** index) * np.sin(phase * index) / index
    return signal.astype(np.float32)


def _place(buffer: np.ndarray, start: float, chunk: np.ndarray, gain: float) -> None:
    begin = int(start * SAMPLE_RATE)
    if begin >= len(buffer) or begin < 0:
        return
    end = min(len(buffer), begin + len(chunk))
    buffer[begin:end] += chunk[: end - begin] * gain


def _lowpass(signal: np.ndarray, alpha: float = 0.35) -> np.ndarray:
    out = np.empty_like(signal)
    state = 0.0
    for index in range(len(signal)):
        state += alpha * (float(signal[index]) - state)
        out[index] = state
    return out


def _noise(length: int) -> np.ndarray:
    return (np.random.default_rng(7).standard_normal(length) * 0.5).astype(np.float32)


def generate_bgm(path: Path, seconds: float, mood: str = "warm") -> Path:
    """本地合成一条完整的背景音乐（与视频等长），不依赖外部模型。"""
    preset = MOODS.get((mood or "").strip().lower(), MOODS["warm"])
    bpm = float(preset["bpm"])
    beat = 60.0 / bpm
    bar = beat * 4
    length = int(math.ceil(seconds * SAMPLE_RATE))
    left = np.zeros(length, dtype=np.float32)
    right = np.zeros(length, dtype=np.float32)
    chords = preset["chords"]

    # 1) 铺底和弦（pad）
    bar_index = 0
    start = 0.0
    while start < seconds:
        chord = chords[bar_index % len(chords)]
        for voice_index, note in enumerate(chord):
            dur = min(bar * 1.05, seconds - start)
            if dur <= 0.1:
                break
            size = int(dur * SAMPLE_RATE)
            env = _adsr(size, attack=0.25, decay=0.4, sustain=0.55, release=0.5)
            tone = _osc(_hz(note), size, detune=0.0009 if voice_index % 2 else -0.0009, harmonics=3)
            pad = tone * env * 0.10
            _place(left, start, pad, 1.0)
            _place(right, start + 0.004, pad, 1.0)
        start += bar
        bar_index += 1

    # 2) 低音
    start = 0.0
    bar_index = 0
    while start < seconds:
        root = chords[bar_index % len(chords)][0] - 12
        for offset in (0.0, beat * 2):
            if start + offset >= seconds:
                break
            size = int(min(beat * 1.6, seconds - start - offset) * SAMPLE_RATE)
            if size <= 0:
                continue
            env = _adsr(size, attack=0.01, decay=0.15, sustain=0.45, release=0.2)
            bass = _osc(_hz(root), size, harmonics=2) * env * 0.22
            _place(left, start + offset, bass, 1.0)
            _place(right, start + offset, bass, 1.0)
        start += bar
        bar_index += 1

    # 3) 琶音
    step = beat / 2.0
    start = 0.0
    note_index = 0
    while start < seconds:
        chord = chords[int(start // bar) % len(chords)]
        note = chord[1 + (note_index % (len(chord) - 1))] + 12
        size = int(min(step * 0.9, seconds - start) * SAMPLE_RATE)
        if size > 50:
            env = _adsr(size, attack=0.005, decay=0.12, sustain=0.0, release=0.15)
            pluck = _osc(_hz(note), size, harmonics=2) * env * 0.055 * preset["arp"]
            _place(left, start, pluck, 1.0)
            _place(right, start + 0.012, pluck, 0.9)
        start += step
        note_index += 1

    # 4) 鼓组
    drums = preset["drums"]
    if drums > 0:
        beat_index = 0
        start = 0.0
        while start < seconds:
            is_downbeat = beat_index % 4 in (0, 2)
            if is_downbeat or bpm >= 110:
                size = int(min(0.22, seconds - start) * SAMPLE_RATE)
                if size > 50:
                    t = np.arange(size) / SAMPLE_RATE
                    freq = np.linspace(120.0, 45.0, size)
                    kick = np.sin(2 * math.pi * np.cumsum(freq) / SAMPLE_RATE)
                    kick *= np.exp(-t * 18.0)
                    _place(left, start, kick.astype(np.float32), 0.55 * drums)
                    _place(right, start, kick.astype(np.float32), 0.55 * drums)
            size = int(min(0.06, seconds - start) * SAMPLE_RATE)
            if size > 30:
                hat = _noise(size) * np.exp(-np.arange(size) / SAMPLE_RATE * 60.0)
                hat = np.diff(hat, prepend=hat[0]).astype(np.float32)
                _place(left, start, hat, 0.06 * drums)
                _place(right, start, hat, 0.06 * drums)
            start += beat
            beat_index += 1

    # 5) 母带：低通、软削波、淡入淡出、归一
    left = _lowpass(left, 0.5)
    right = _lowpass(right, 0.5)
    mixed = np.stack([left, right], axis=1)
    peak = float(np.max(np.abs(mixed))) or 1.0
    mixed = np.tanh(mixed / peak * 2.2) * 0.82
    fade_in = int(0.35 * SAMPLE_RATE)
    fade_out = int(min(1.4, seconds * 0.25) * SAMPLE_RATE)
    mixed[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)[:, None]
    mixed[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)[:, None]

    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(mixed * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    return path
