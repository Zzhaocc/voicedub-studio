"""音频处理：WAV 读写、静音拼接、对白混音、时长对齐、响度归一。

全部基于标准库 `wave` + numpy；时间伸缩与响度归一调 ffmpeg。

ffmpeg / ffprobe 的可执行路径、默认采样率、命令超时全部来自配置
（``FFMPEG_BIN`` / ``VOICEDUB_SAMPLE_RATE`` / ``VOICEDUB_FFMPEG_TIMEOUT``），
所以换镜像、换端口、加超时都不需要改代码。
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings


def ffmpeg_bin() -> str:
    """当前生效的 ffmpeg 路径（每次读取，便于测试与容器覆盖）。"""
    return get_settings().ffmpeg


def default_sample_rate(explicit: int | None = None) -> int:
    """显式传入优先，否则用配置里的默认采样率。"""
    return int(explicit) if explicit else get_settings().sample_rate


def _run(cmd: list[str]) -> None:
    """执行 ffmpeg/ffprobe；超时与非零退出都转换成可读错误。"""
    timeout = get_settings().ffmpeg_timeout
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{Path(cmd[0]).name} 执行超时（>{timeout:.0f}s）") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "")[-800:])


def wav_info(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        with wave.open(str(path), "rb") as fh:
            frames = fh.getnframes()
            rate = fh.getframerate()
            return {
                "path": str(path),
                "duration": round(frames / float(rate or 1), 3),
                "sample_rate": rate,
                "channels": fh.getnchannels(),
                "sample_width": fh.getsampwidth(),
                "frames": frames,
            }
    except wave.Error as exc:
        raise ValueError(f"不是合法的 WAV：{path}") from exc


def _read_mono(path: Path, sample_rate: int) -> np.ndarray:
    with wave.open(str(path), "rb") as fh:
        rate = fh.getframerate()
        channels = fh.getnchannels()
        width = fh.getsampwidth()
        frames = fh.getnframes()
        raw = fh.readframes(frames)
    if width != 2:
        raise ValueError(f"仅支持 16-bit PCM：{path}")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != sample_rate and len(data) > 1:
        duration = len(data) / float(rate)
        target = max(1, int(round(duration * sample_rate)))
        data = np.interp(
            np.linspace(0.0, 1.0, target, endpoint=False),
            np.linspace(0.0, 1.0, len(data), endpoint=False),
            data,
        )
    return data / 32767.0


def _write_mono(path: Path, data: np.ndarray, sample_rate: int) -> int:
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())
    return path.stat().st_size


def concat_with_gaps(
    paths: list[Path],
    out_path: Path,
    gap: float = 0.3,
    sample_rate: int | None = None,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("没有可拼接的音频")

    rate = default_sample_rate(sample_rate)
    silence = np.zeros(max(0, int(round(gap * rate))), dtype=np.float32)
    chunks: list[np.ndarray] = []
    for index, path in enumerate(paths):
        if index:
            chunks.append(silence)
        chunks.append(_read_mono(Path(path), rate))

    merged = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    out_path = Path(out_path)
    size = _write_mono(out_path, merged, rate)
    return {
        "path": str(out_path),
        "duration": round(len(merged) / rate, 3),
        "segments": len(paths),
        "size_bytes": size,
        "sample_rate": rate,
    }


def mix_dialogue(
    segments: list[dict],
    out_path: Path,
    gap: float = 0.25,
    sample_rate: int | None = None,
) -> dict[str, Any]:
    """把多段对白按顺序拼起来，并返回带角色的时间轴。"""
    if not segments:
        raise ValueError("没有可混音的台词")
    for segment in segments:
        if "path" not in segment or "text" not in segment:
            raise ValueError("每段台词都需要 path 与 text")

    rate = default_sample_rate(sample_rate)
    silence = np.zeros(max(0, int(round(gap * rate))), dtype=np.float32)
    chunks: list[np.ndarray] = []
    timeline: list[dict[str, Any]] = []
    cursor = 0.0

    for index, segment in enumerate(segments):
        if index:
            chunks.append(silence)
            cursor += gap
        data = _read_mono(Path(segment["path"]), rate)
        chunks.append(data)
        duration = len(data) / rate
        timeline.append(
            {
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
                "speaker": segment.get("speaker", "旁白"),
                "text": segment["text"],
            }
        )
        cursor += duration

    merged = np.concatenate(chunks)
    out_path = Path(out_path)
    size = _write_mono(out_path, merged, rate)
    return {
        "path": str(out_path),
        "duration": round(len(merged) / rate, 3),
        "segments": len(segments),
        "size_bytes": size,
        "timeline": timeline,
    }


def fit_duration(
    src: Path,
    out_path: Path,
    target_seconds: float,
    min_factor: float = 0.5,
    max_factor: float = 2.0,
    sample_rate: int | None = None,
) -> dict[str, Any]:
    """用 atempo 把音频伸缩到目标时长（factor = 原时长 / 目标时长）。"""
    if target_seconds <= 0:
        raise ValueError("目标时长必须大于 0")
    rate = default_sample_rate(sample_rate)
    info = wav_info(src)
    raw_factor = info["duration"] / float(target_seconds)
    # atempo 单次只接受 0.5–2.0，超出就串联；这里只做安全边界，不做业务钳制
    factor = max(0.125, min(8.0, raw_factor))

    filters: list[str] = []
    remaining = factor
    while remaining > max_factor:
        filters.append(f"atempo={max_factor:.6f}")
        remaining /= max_factor
    while remaining < min_factor:
        filters.append(f"atempo={min_factor:.6f}")
        remaining /= min_factor
    filters.append(f"atempo={max(0.5, min(2.0, remaining)):.6f}")

    _run(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(src),
            "-filter:a",
            ",".join(filters),
            "-ac",
            "1",
            "-ar",
            str(rate),
            str(out_path),
        ]
    )
    fitted = wav_info(out_path)
    return {
        "path": str(out_path),
        "duration": fitted["duration"],
        "target": round(float(target_seconds), 3),
        "factor": round(factor, 3),
        "size_bytes": Path(out_path).stat().st_size,
    }


def normalize_loudness(
    src: Path, out_path: Path, target_lufs: float = -16.0, sample_rate: int | None = None
) -> dict[str, Any]:
    rate = default_sample_rate(sample_rate)
    _run(
        [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(src),
            "-filter:a",
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            "-ar",
            str(rate),
            str(out_path),
        ]
    )
    info = wav_info(out_path)
    return {
        "path": str(out_path),
        "duration": info["duration"],
        "size_bytes": Path(out_path).stat().st_size,
        "target_lufs": target_lufs,
    }
