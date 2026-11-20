"""视频处理：探测、抽音轨、替换音轨、生成占位视频、截封面。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "")[-800:])


def probe(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    result = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration,size,bit_rate",
            "-show_entries", "stream=codec_type,codec_name,width,height",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "")[-800:])
    data = json.loads(result.stdout or "{}")
    fmt = data.get("format") or {}
    streams = [
        {
            "type": stream.get("codec_type"),
            "codec": stream.get("codec_name"),
            "width": stream.get("width"),
            "height": stream.get("height"),
        }
        for stream in (data.get("streams") or [])
    ]
    return {
        "path": str(path),
        "duration": round(float(fmt.get("duration") or 0), 3),
        "size_bytes": int(fmt.get("size") or path.stat().st_size),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "streams": streams,
    }


def extract_audio(video_path: Path, out_path: Path, fmt: str = "wav") -> dict[str, Any]:
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    if fmt not in {"wav", "mp3"}:
        raise ValueError("fmt 只支持 wav / mp3")
    codec = ["-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1"] if fmt == "wav" else ["-acodec", "libmp3lame", "-q:a", "4"]
    _run([FFMPEG, "-y", "-i", str(video_path), "-vn", *codec, str(out_path)])
    info = probe(out_path)
    return {
        "path": str(out_path),
        "duration": info["duration"],
        "size_bytes": Path(out_path).stat().st_size,
        "format": fmt,
    }


def replace_audio(video_path: Path, audio_path: Path, out_path: Path) -> dict[str, Any]:
    video_path, audio_path = Path(video_path), Path(audio_path)
    for path in (video_path, audio_path):
        if not path.exists():
            raise FileNotFoundError(path)
    _run(
        [
            FFMPEG, "-y", "-i", str(video_path), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(out_path),
        ]
    )
    info = probe(out_path)
    return {
        "path": str(out_path),
        "duration": info["duration"],
        "size_bytes": Path(out_path).stat().st_size,
        "video_reused": True,
    }


def make_placeholder_video(
    out_path: Path,
    seconds: float = 8.0,
    width: int = 640,
    height: int = 360,
    color: str = "0x0f1b2d",
) -> dict[str, Any]:
    """生成一段带静音的占位视频，保证"视频配音"离线可演示。"""
    if seconds <= 0:
        raise ValueError("seconds 必须大于 0")
    _run(
        [
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}:r=24:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=24000:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(out_path),
        ]
    )
    info = probe(out_path)
    return {
        "path": str(out_path),
        "duration": info["duration"],
        "width": width,
        "height": height,
        "size_bytes": Path(out_path).stat().st_size,
    }


def thumbnail(video_path: Path, out_path: Path, at: float = 1.0) -> dict[str, Any]:
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    _run([FFMPEG, "-y", "-ss", f"{max(0.0, at):.2f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(out_path)])
    try:
        from PIL import Image

        with Image.open(out_path) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001
        width = height = None
    return {
        "path": str(out_path),
        "width": width,
        "height": height,
        "size_bytes": Path(out_path).stat().st_size,
    }
