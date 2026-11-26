"""SRT 字幕的确定性生成与解析。

时间轴规则：从 0 开始，每段 ``end = start + duration``；负时长按 0 处理；
空文本（``strip()`` 后为空）不写字幕，但它的时长仍推进时间轴，保证后续
字幕的时间与音频一致。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

# 字幕正文最多回传 20000 字符，避免 API 响应体过大。
MAX_RETURN_CHARS = 20000

_TIMESTAMP_RE = re.compile(r"(?P<hours>\d{1,3}):(?P<minutes>\d{2}):(?P<seconds>\d{2})[,.](?P<millis>\d{1,3})")
_ARROW_RE = re.compile(r"-->|—>")


def format_ts(seconds: float) -> str:
    """把秒数格式化为 ``HH:MM:SS,mmm``；负数按 0 处理。"""
    try:
        value = float(seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法秒数：{seconds!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"非法秒数：{seconds!r}")

    total_ms = int(round(max(0.0, value) * 1000.0))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _parse_timestamp(value: str) -> float:
    match = _TIMESTAMP_RE.search(value)
    if match is None:
        raise ValueError(f"非法 SRT 时间戳：{value!r}")
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    secs = int(match.group("seconds"))
    millis = int(match.group("millis").ljust(3, "0"))
    return round(hours * 3600.0 + minutes * 60.0 + secs + millis / 1000.0, 3)


def build_srt(segments: list[dict], out_path: Path) -> dict:
    """根据 ``[{"text","duration"}]`` 生成 UTF-8（无 BOM）SRT 文件。

    :returns: ``{"path","count","duration","text"}``；
              ``text`` 是字幕正文（最多 20000 字符，便于预览/API）。
    """
    if not isinstance(segments, list):
        raise ValueError("segments 必须是列表")

    cursor = 0.0
    cues: list[tuple[int, float, float, str]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("segments 的每一项必须是字典")
        try:
            duration = float(segment.get("duration", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"非法 duration：{segment.get('duration')!r}") from exc
        if not math.isfinite(duration):
            raise ValueError(f"非法 duration：{segment.get('duration')!r}")

        duration = max(0.0, duration)
        start, end = cursor, cursor + duration
        cursor = end

        text = str(segment.get("text") or "").strip()
        if text:
            cues.append((len(cues) + 1, start, end, text))

    blocks = [f"{index}\n{format_ts(start)} --> {format_ts(end)}\n{text}" for index, start, end, text in cues]
    body = "\n\n".join(blocks)
    if body:
        body += "\n"

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")

    return {
        "path": str(target),
        "count": len(cues),
        "duration": round(cursor, 3),
        "text": body[:MAX_RETURN_CHARS],
    }


def parse_srt(text: str) -> list[dict]:
    """解析 SRT 文本，返回 ``[{"index","start","end","text"}]``。

    兼容 ``,`` 与 ``.`` 两种毫秒分隔符、BOM、CRLF 与多行字幕文本。
    """
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")

    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", normalized.strip())
    result: list[dict] = []

    for block in blocks:
        if not block.strip():
            continue
        lines = block.split("\n")
        index = len(result) + 1
        if lines and lines[0].strip().isdigit():
            index = int(lines[0].strip())
            lines = lines[1:]
        if not lines:
            continue

        arrow_index = next(
            (position for position, line in enumerate(lines) if _ARROW_RE.search(line)),
            None,
        )
        if arrow_index is None:
            continue

        left, right = _ARROW_RE.split(lines[arrow_index], maxsplit=1)
        try:
            start = _parse_timestamp(left)
            end = _parse_timestamp(right)
        except ValueError:
            continue

        cue_text = "\n".join(lines[arrow_index + 1 :]).strip()
        result.append({"index": index, "start": start, "end": end, "text": cue_text})

    return result


__all__ = ["MAX_RETURN_CHARS", "build_srt", "format_ts", "parse_srt"]
