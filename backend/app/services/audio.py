from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .ffmpeg import run
from .tts import wav_duration


def fit_voice(settings: Settings, source: Path, max_seconds: float, *, cap: float = 1.5) -> tuple[Path, float]:
    """口播超过镜头时长时做轻度加速（atempo），避免压到下一镜。"""
    duration = wav_duration(source)
    if max_seconds <= 0.2 or duration <= max_seconds:
        return source, duration
    ratio = min(duration / max_seconds, cap)
    if ratio <= 1.02:
        return source, duration
    fitted = source.with_name(f"{source.stem}_fit.wav")
    result = run(
        settings,
        ["-i", str(source), "-filter:a", f"atempo={ratio:.4f}", "-ar", "44100", "-ac", "2", str(fitted)],
    )
    if result.returncode != 0 or not fitted.exists():
        return source, duration
    return fitted, wav_duration(fitted)


def mix_video(
    settings: Settings,
    video: Path,
    out_path: Path,
    *,
    bgm: Path,
    voices: list[tuple[Path, float]],
    duration: float,
    bgm_volume: float = 0.22,
    voice_volume: float = 1.0,
) -> dict:
    """把整条 BGM + 多条按时间轴排布的口播混进视频（替换原音轨）。"""
    if not voices and not bgm:
        raise ValueError("没有可混音的音轨")

    inputs: list[str] = ["-i", str(video)]
    filters: list[str] = []
    mix_labels: list[str] = []
    input_index = 1

    if bgm is not None:
        inputs += ["-i", str(bgm)]
        filters.append(
            f"[{input_index}:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={bgm_volume:.3f}[bg]"
        )
        mix_labels.append("[bg]")
        input_index += 1

    for index, (path, offset) in enumerate(voices):
        inputs += ["-i", str(path)]
        delay = max(0, int(round(offset * 1000)))
        label = f"v{index}"
        filters.append(
            f"[{input_index}:a]aresample=44100,adelay={delay}|{delay},"
            f"volume={voice_volume:.3f}[{label}]"
        )
        mix_labels.append(f"[{label}]")
        input_index += 1

    if len(mix_labels) == 1:
        filters.append(f"{mix_labels[0]}alimiter=limit=0.95[aout]")
    else:
        filters.append(
            "".join(mix_labels)
            + f"amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
            "alimiter=limit=0.95[aout]"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    result = run(settings, args)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"混音失败：{(result.stderr or result.stdout or '')[:400]}")
    return {
        "bgm": bgm.name if bgm else None,
        "voices": [{"file": path.name, "offset": round(offset, 2)} for path, offset in voices],
        "bgm_volume": bgm_volume,
        "voice_volume": voice_volume,
    }
