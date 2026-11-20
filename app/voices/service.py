"""音色：预设、声音设计、克隆。"""

from __future__ import annotations

import re
import struct
import wave
from pathlib import Path
from typing import Any

PRESET_VOICES: list[dict[str, Any]] = [
    {
        "name": "林夕 · 温柔女声",
        "pitch": 1.10,
        "speed": 1.0,
        "timbre": "warm",
        "description": "默认旁白音色，适合叙述与产品介绍",
    },
    {
        "name": "周野 · 低沉男声",
        "pitch": 0.86,
        "speed": 0.95,
        "timbre": "deep",
        "description": "纪录片、企业宣传片常用",
    },
    {
        "name": "Nova · 清亮女声",
        "pitch": 1.24,
        "speed": 1.08,
        "timbre": "bright",
        "description": "短视频、带货口播",
    },
    {
        "name": "老陈 · 评书腔",
        "pitch": 0.92,
        "speed": 0.88,
        "timbre": "deep",
        "description": "有声书、故事讲述",
    },
]

# 声音设计关键词 → 参数（顺序敏感：先匹配更具体的词）
DESIGN_RULES: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (("老", "年长", "成熟"), {"pitch_delta": -0.06, "speed_delta": -0.05}),
    (("少年", "年轻", "少女"), {"pitch_delta": 0.06, "speed_delta": 0.03}),
    (("男", "先生", "大叔"), {"pitch_delta": -0.10}),
    (("女", "小姐", "姑娘"), {"pitch_delta": 0.10}),
    (("低沉", "浑厚", "磁性", "厚"), {"timbre": "deep", "pitch_delta": -0.06}),
    (("清亮", "明亮", "清脆", "甜"), {"timbre": "bright", "pitch_delta": 0.06}),
    (("温柔", "温暖", "柔和", "亲切"), {"timbre": "warm"}),
    (("慢", "沉稳", "舒缓"), {"speed_delta": -0.10}),
    (("快", "活泼", "急促", "热情"), {"speed_delta": 0.10}),
    (("播报", "新闻", "正式"), {"speed_delta": 0.02, "timbre": "warm"}),
    (("耳语", "轻声", "气声"), {"pitch_delta": -0.02, "speed_delta": -0.05}),
]


def design_from_text(description: str) -> dict[str, Any]:
    """把一句中文描述解析成可合成的音色参数。

    这是一个**规则解析器**，不是模型：可解释、可测试、可复现。
    真实产品里由 LLM 或声音设计模型完成，接口保持不变即可替换。
    """
    text = (description or "").strip()
    if not text:
        raise ValueError("声音描述不能为空")

    pitch, speed, timbre = 1.0, 1.0, "warm"
    matched: list[str] = []
    for keywords, effect in DESIGN_RULES:
        if any(word in text for word in keywords):
            pitch += float(effect.get("pitch_delta", 0.0))
            speed += float(effect.get("speed_delta", 0.0))
            timbre = str(effect.get("timbre", timbre))
            matched.extend([w for w in keywords if w in text])

    pitch = round(max(0.5, min(2.0, pitch)), 3)
    speed = round(max(0.5, min(2.0, speed)), 3)
    if not matched:
        matched = ["默认"]

    return {
        "pitch": pitch,
        "speed": speed,
        "timbre": timbre,
        "matched_keywords": matched[:6],
        "summary": f"pitch {pitch:.2f} · speed {speed:.2f} · {timbre}",
        "description": text,
    }


def analyze_reference(path: Path) -> dict[str, Any]:
    """从参考音频估算基频与音色（16-bit PCM WAV）。"""
    with wave.open(str(path), "rb") as fh:
        rate = fh.getframerate()
        width = fh.getsampwidth()
        channels = fh.getnchannels()
        total = fh.getnframes()
        frames = fh.readframes(min(total, rate * 3))
    if width != 2:
        raise ValueError("仅支持 16-bit PCM WAV")
    count = len(frames) // (2 * channels)
    if count <= 1:
        raise ValueError("参考音频为空")
    samples = struct.unpack("<" + "h" * count, frames[: count * 2])

    crossings = sum(1 for i in range(1, len(samples)) if (samples[i - 1] < 0) != (samples[i] < 0))
    zcr = crossings / max(1, len(samples) - 1)
    f0 = max(60.0, min(400.0, zcr * rate / 2))
    timbre = "bright" if zcr > 0.12 else "deep" if zcr < 0.05 else "warm"

    return {
        "duration": round(total / float(rate or 1), 3),
        "f0": round(f0, 1),
        "zcr": round(zcr, 4),
        "pitch": round(max(0.5, min(2.0, f0 / 165.0)), 3),
        "timbre": timbre,
        "sample_rate": rate,
    }


def guess_name(description: str) -> str:
    """从描述里截一个短名字，例如「低沉的中年男声」→「低沉的中年男声」。"""
    cleaned = re.sub(r"[\s，。,.!！?？]+", "", description or "")
    return (cleaned[:12] or "自定义音色")
