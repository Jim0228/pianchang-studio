from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg

from ..config import Settings

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@lru_cache(maxsize=4)
def _resolve(ffmpeg_bin: str) -> str:
    if ffmpeg_bin:
        path = Path(ffmpeg_bin)
        if path.exists():
            return str(path)
        raise RuntimeError(f"配置的 FFMPEG_BIN 不存在：{ffmpeg_bin}")
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise RuntimeError("未找到 ffmpeg：请安装 imageio-ffmpeg 或配置 FFMPEG_BIN") from None


def ffmpeg_exe(settings: Settings) -> str:
    return _resolve(settings.ffmpeg_bin.strip())


def run(settings: Settings, args: list[str], *, timeout: int = 1200) -> subprocess.CompletedProcess:
    command = [ffmpeg_exe(settings), "-hide_banner", "-loglevel", "error", "-y", *args]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def probe(settings: Settings, path: Path) -> dict:
    result = subprocess.run(
        [ffmpeg_exe(settings), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=_CREATE_NO_WINDOW,
    )
    text = result.stderr or ""
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = 0.0
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    size_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text, re.S)
    width = int(size_match.group(1)) if size_match else 0
    height = int(size_match.group(2)) if size_match else 0
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
    fps = float(fps_match.group(1)) if fps_match else 0.0
    return {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": "Audio:" in text,
        "size": path.stat().st_size if path.exists() else 0,
    }


def has_encoder(settings: Settings, name: str) -> bool:
    result = subprocess.run(
        [ffmpeg_exe(settings), "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=_CREATE_NO_WINDOW,
    )
    return name in (result.stdout or "")


def video_codec(settings: Settings) -> tuple[str, list[str]]:
    if has_encoder(settings, "libx264"):
        return "libx264", ["-preset", "veryfast", "-crf", "22"]
    return "mpeg4", ["-q:v", "3"]


def make_clip(
    settings: Settings,
    keyframe: Path,
    subtitle: Path | None,
    out_path: Path,
    seconds: float,
    *,
    width: int,
    height: int,
    fps: int,
    zoom: str = "in",
) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scale_w = int(width * 1.25)
    scale_h = int(height * 1.25)
    if zoom == "out":
        zoom_expr = "max(1.08-0.0006*on,1.0)"
    else:
        zoom_expr = "min(1.0+0.0006*on,1.08)"

    inputs = ["-loop", "1", "-i", str(keyframe)]
    audio_index = 1
    if subtitle is not None:
        inputs += ["-loop", "1", "-i", str(subtitle)]
        audio_index = 2
    inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    filter_complex = (
        f"[0:v]scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
        f"crop={scale_w}:{scale_h},"
        f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
        f"s={width}x{height}:fps={fps},setsar=1[bg]"
    )
    if subtitle is not None:
        filter_complex += ";[bg][1:v]overlay=0:0:shortest=1,format=yuv420p[v]"
    else:
        filter_complex += ";[bg]format=yuv420p[v]"

    codec, codec_args = video_codec(settings)
    args = [
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        f"{audio_index}:a",
        "-t",
        f"{seconds:.2f}",
        "-r",
        str(fps),
        "-c:v",
        codec,
        *codec_args,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    result = run(settings, args)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 生成片段失败：{(result.stderr or '')[-800:]}")
    return probe(settings, out_path)


def normalize_video(
    settings: Settings,
    input_path: Path,
    out_path: Path,
    *,
    seconds: float,
    width: int,
    height: int,
    fps: int,
    subtitle: Path | None = None,
    label: Path | None = None,
) -> dict:
    """Trim/resize an external clip and burn subtitle + AI label overlays."""
    info = probe(settings, input_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    inputs = ["-i", str(input_path)]
    index = 1
    subtitle_index = None
    label_index = None
    if subtitle is not None and Path(subtitle).exists():
        inputs += ["-loop", "1", "-i", str(subtitle)]
        subtitle_index = index
        index += 1
    if label is not None and Path(label).exists():
        inputs += ["-loop", "1", "-i", str(label)]
        label_index = index
        index += 1

    if info.get("has_audio"):
        audio_index = None
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_index = index

    filters = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"tpad=stop_mode=clone:stop_duration={max(2.0, seconds):.2f},"
        f"trim=duration={seconds:.2f},setpts=PTS-STARTPTS,setsar=1[base]"
    )
    last = "base"
    if subtitle_index is not None:
        filters += (
            f";[{subtitle_index}:v]scale={width}:{height}[subimg];"
            f"[{last}][subimg]overlay=0:0[withsub]"
        )
        last = "withsub"
    if label_index is not None:
        filters += (
            f";[{label_index}:v]scale={width}:{height}[labelimg];"
            f"[{last}][labelimg]overlay=0:0[withlabel]"
        )
        last = "withlabel"
    filters += f";[{last}]format=yuv420p[out]"

    codec, codec_args = video_codec(settings)
    args = [
        *inputs,
        "-filter_complex",
        filters,
        "-map",
        "[out]",
    ]
    if audio_index is None:
        args += ["-map", "0:a:0?"]
    else:
        args += ["-map", f"{audio_index}:a"]
    args += [
        "-t",
        f"{seconds:.2f}",
        "-r",
        str(fps),
        "-c:v",
        codec,
        *codec_args,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    result = run(settings, args)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 归一化失败：{(result.stderr or '')[-800:]}")
    return probe(settings, out_path)


def concat(settings: Settings, clips: list[Path], out_path: Path) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / "concat_list.txt"
    with list_path.open("w", encoding="utf-8") as handle:
        for clip in clips:
            safe = clip.as_posix().replace("'", "'\\''")
            handle.write(f"file '{safe}'\n")

    base = ["-f", "concat", "-safe", "0", "-i", str(list_path), "-movflags", "+faststart", str(out_path)]
    result = run(settings, [*base[:6], "-c", "copy", *base[6:]])
    if result.returncode != 0:
        codec, codec_args = video_codec(settings)
        result = run(
            settings,
            [
                *base[:6],
                "-c:v",
                codec,
                *codec_args,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-pix_fmt",
                "yuv420p",
                *base[6:],
            ],
        )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 拼接失败：{(result.stderr or '')[-800:]}")
    return probe(settings, out_path)


def status(settings: Settings) -> dict:
    try:
        path = ffmpeg_exe(settings)
        return {
            "available": True,
            "path": path,
            "codec": video_codec(settings)[0],
            "libx264": has_encoder(settings, "libx264"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "path": "", "codec": "", "error": str(exc)}
