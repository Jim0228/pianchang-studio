from __future__ import annotations

import os
import subprocess
import wave
from pathlib import Path

from ..config import Settings

# Windows 自带 SAPI：zh-CN 一般有 Microsoft Huihui / Yaoyao。
PS_SYNTH = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "if ($env:DSH_TTS_VOICE) { try { $s.SelectVoice($env:DSH_TTS_VOICE) } catch {} }; "
    "$s.Rate = [int]$env:DSH_TTS_RATE; "
    "$s.Volume = 100; "
    "$s.SetOutputToWaveFile($env:DSH_TTS_OUT); "
    "$s.Speak($env:DSH_TTS_TEXT); "
    "$s.Dispose()"
)

PS_VOICES = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.GetInstalledVoices() | ForEach-Object { "
    "$_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture + '|' + $_.VoiceInfo.Gender }; "
    "$s.Dispose()"
)


def _power_shell(script: str, extra_env: dict | None = None, timeout: int = 120) -> str:
    env = {**os.environ, **(extra_env or {})}
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "PowerShell 执行失败").strip()[:500])
    return result.stdout


def list_voices() -> list[dict]:
    try:
        output = _power_shell(PS_VOICES, timeout=60)
    except Exception:  # noqa: BLE001
        return []
    voices: list[dict] = []
    for line in output.splitlines():
        parts = [item.strip() for item in line.strip().split("|")]
        if len(parts) == 3 and parts[0]:
            voices.append({"name": parts[0], "culture": parts[1], "gender": parts[2]})
    return voices


def pick_voice(settings: Settings) -> str:
    wanted = (settings.tts_voice or "").strip()
    voices = list_voices()
    names = [item["name"] for item in voices]
    if wanted and any(item.lower() == wanted.lower() for item in names):
        return wanted
    for item in voices:
        if item["culture"].lower().startswith("zh"):
            return item["name"]
    return names[0] if names else ""


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate() or 1
        return round(frames / float(rate), 3)


def synthesize(
    text: str,
    out_path: Path,
    *,
    settings: Settings,
    voice: str = "",
    rate: int | None = None,
) -> Path:
    """调用 Windows SAPI 生成一镜口播 wav（22.05kHz mono）。"""
    clean = " ".join((text or "").split())
    if not clean:
        raise ValueError("口播文本为空")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    selected = voice or pick_voice(settings)
    payload = {
        "DSH_TTS_TEXT": clean,
        "DSH_TTS_OUT": str(out_path),
        "DSH_TTS_VOICE": selected,
        "DSH_TTS_RATE": str(settings.tts_rate if rate is None else rate),
    }
    _power_shell(PS_SYNTH, payload, timeout=180)
    if not out_path.exists() or out_path.stat().st_size < 1000:
        raise RuntimeError("TTS 没有生成有效音频")
    return out_path
