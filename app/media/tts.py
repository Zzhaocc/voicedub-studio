"""确定性参数化语音合成（TTS）。

这里没有神经网络，也不需要联网：用基频 + 谐波 + 包络拼出"像人声"的
浊音段，再按字符时长铺成时间轴。相同 ``text + voice + sample_rate``
永远生成逐字节相同的 PCM 数据，适合离线演示与回归测试。

时长模型（速度只缩放"发声字符"的时长，停顿保持绝对时长）::

    Latin / 中文 / 其他非空白字符 : 0.16 / speed 秒
    空白（空格、换行、制表符）    : 0.05 秒静音
    标点（，。！？,.!?;；:：）    : 0.12 秒停顿

音色对基频与谐波幅度的作用：

- ``warm``   : 基频 ×1.00，谐波幅度 [1.0, 0.45, 0.22, 0.10]（圆润）
- ``bright`` : 基频 ×1.15，谐波幅度 [1.0, 0.58, 0.32, 0.16]（明亮、高频多）
- ``deep``   : 基频 ×0.85，谐波幅度 [1.0, 0.32, 0.14, 0.06]（低沉、高频少）

每个字符还会依据 ``hashlib.md5(字符)`` 得到 ±1% 的基频抖动、相位与
力度差异，所以不是机械的纯音，同时完全可复现。
"""

from __future__ import annotations

import hashlib
import math
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ---- 时长常量 ------------------------------------------------------------- #
BASE_CHAR_SECONDS = 0.16
SPACE_SECONDS = 0.05
PUNCT_SECONDS = 0.12
SEGMENT_GAP_SECONDS = 0.15

PUNCTUATION: frozenset[str] = frozenset("，。！？,.!?;；:：")

# 音色：基频系数 + 谐波幅度（第 1 项为基频，后 3 项为 2/3/4 次谐波）
TIMBRE_FACTORS: dict[str, float] = {"warm": 1.0, "bright": 1.15, "deep": 0.85}
TIMBRE_HARMONICS: dict[str, tuple[float, ...]] = {
    "warm": (1.0, 0.45, 0.22, 0.10),
    "bright": (1.0, 0.58, 0.32, 0.16),
    "deep": (1.0, 0.32, 0.14, 0.06),
}

BASE_F0_HZ = 165.0
PEAK_AMPLITUDE = 0.7
FADE_SECONDS = 0.02


@dataclass(frozen=True)
class VoiceSpec:
    """声音参数（不可变）。

    :param pitch: 基频倍数，0.5..2.0。
    :param speed: 语速倍数，0.5..2.0；越大每个发声字符越短。
    :param timbre: ``warm`` / ``bright`` / ``deep`` 三种音色之一。
    """

    pitch: float = 1.0
    speed: float = 1.0
    timbre: str = "warm"

    def __post_init__(self) -> None:
        if not (0.5 <= float(self.pitch) <= 2.0):
            raise ValueError("pitch 必须在 0.5..2.0 之间")
        if not (0.5 <= float(self.speed) <= 2.0):
            raise ValueError("speed 必须在 0.5..2.0 之间")
        if self.timbre not in TIMBRE_FACTORS:
            raise ValueError("timbre 必须是 warm / bright / deep 之一")

    def as_dict(self) -> dict[str, float | str]:
        """返回可直接 JSON 序列化的参数字典。"""
        return {"pitch": float(self.pitch), "speed": float(self.speed), "timbre": self.timbre}


def _validate_sample_rate(sample_rate: int) -> int:
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise ValueError("sample_rate 必须是正整数")
    return sample_rate


def _char_seconds(char: str, speed: float) -> float:
    """单个字符在时间轴上占用的秒数。"""
    if char.isspace():
        return SPACE_SECONDS
    if char in PUNCTUATION:
        return PUNCT_SECONDS
    return BASE_CHAR_SECONDS / speed


def _text_seconds(text: str, speed: float) -> float:
    return float(sum(_char_seconds(char, speed) for char in text))


def _voice_segment(char: str, sample_count: int, sample_rate: int, voice: VoiceSpec) -> np.ndarray:
    """生成单个字符的浊音段（基频 + 3 个谐波 + 起落包络）。"""
    if sample_count <= 0:
        return np.zeros(0, dtype=np.float32)

    digest = hashlib.md5(char.encode("utf-8")).digest()
    factor = TIMBRE_FACTORS[voice.timbre]
    harmonics = TIMBRE_HARMONICS[voice.timbre]

    # md5 前两个字节决定 ±1% 的基频抖动与相位，保证逐次运行一致。
    jitter = (digest[0] / 255.0 - 0.5) * 0.02
    f0 = BASE_F0_HZ * float(voice.pitch) * factor * (1.0 + jitter)
    phase = 2.0 * math.pi * (digest[1] / 255.0)

    t = np.arange(sample_count, dtype=np.float32) / float(sample_rate)
    signal = np.zeros(sample_count, dtype=np.float32)
    for order, amplitude in enumerate(harmonics, start=1):
        if amplitude == 0.0:
            continue
        signal += amplitude * np.sin(
            2.0 * math.pi * f0 * order * t + phase * (1.0 + 0.37 * order)
        ).astype(np.float32)

    # 轻微的 5.5 Hz 幅度颤音，避免长音听起来像合成器。
    signal *= (1.0 + 0.04 * np.sin(2.0 * math.pi * 5.5 * t + phase)).astype(np.float32)

    attack = min(int(0.012 * sample_rate), max(1, sample_count // 3))
    release = min(int(0.028 * sample_rate), max(1, sample_count // 3))
    envelope = np.ones(sample_count, dtype=np.float32)
    envelope[:attack] = (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, attack))).astype(np.float32)
    envelope[-release:] = (0.5 + 0.5 * np.cos(np.linspace(0.0, math.pi, release))).astype(np.float32)

    # 每字符力度略有差异（0.75..1.0），由 md5 决定。
    strength = 0.75 + 0.25 * (digest[2] / 255.0)
    return (signal * envelope * strength).astype(np.float32)


def _render_samples(text: str, voice: VoiceSpec, sample_rate: int) -> np.ndarray:
    """按字符时间轴合成原始浮点样本（未归一化、未淡入淡出）。"""
    durations = [_char_seconds(char, float(voice.speed)) for char in text]

    # 用累计时间的四舍五入确定每个字符的样本边界，总和严格等于目标样本数。
    bounds: list[int] = [0]
    elapsed = 0.0
    for duration in durations:
        elapsed += duration
        bounds.append(int(round(elapsed * sample_rate)))

    samples = np.zeros(max(1, bounds[-1]), dtype=np.float32)
    for index, char in enumerate(text):
        start, end = bounds[index], bounds[index + 1]
        if end <= start or char.isspace() or char in PUNCTUATION:
            continue
        samples[start:end] += _voice_segment(char, end - start, sample_rate, voice)
    return samples


def _finalize(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """峰值归一化到 0.7，并加 20ms 淡入淡出。"""
    audio = np.asarray(samples, dtype=np.float32).copy()
    if audio.size == 0:
        return audio
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

    peak = float(np.max(np.abs(audio)))
    if peak > 1e-9:
        audio *= PEAK_AMPLITUDE / peak

    fade = min(int(FADE_SECONDS * sample_rate), audio.size // 2)
    if fade > 0:
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, fade))).astype(np.float32)
        audio[:fade] *= ramp
        audio[-fade:] *= ramp[::-1]

    return np.clip(audio, -1.0, 1.0).astype(np.float32)


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> Path:
    """写标准 mono 16-bit PCM WAV，返回实际路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return target


def synthesize(
    text: str,
    out_path: Path,
    voice: VoiceSpec = VoiceSpec(),
    sample_rate: int = 24000,
) -> dict:
    """把文本合成为 mono 16-bit PCM WAV。

    :param text: 待合成文本；空白文本抛 ``ValueError``。
    :param out_path: 输出 WAV 路径（父目录会自动创建）。
    :param voice: 声音参数，默认 :class:`VoiceSpec`。
    :param sample_rate: 采样率，默认 24000。
    :returns: ``{"path","duration","sample_rate","size_bytes","char_count","voice"}``。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空或纯空白")
    if not isinstance(voice, VoiceSpec):
        raise ValueError("voice 必须是 VoiceSpec 实例")
    sample_rate = _validate_sample_rate(sample_rate)

    raw = _render_samples(text, voice, sample_rate)
    audio = _finalize(raw, sample_rate)
    target = _write_wav(Path(out_path), audio, sample_rate)

    return {
        "path": str(target),
        "duration": round(audio.size / sample_rate, 3),
        "sample_rate": sample_rate,
        "size_bytes": target.stat().st_size,
        "char_count": len(text),
        "voice": voice.as_dict(),
    }


def _read_pcm16_mono(path: Path, sample_rate: int) -> np.ndarray:
    """读取 mono 16-bit WAV，必要时线性重采样到目标采样率。"""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"WAV 不存在：{source}")
    try:
        with wave.open(str(source), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"不是有效的 WAV 文件：{source}") from exc

    if channels != 1 or width != 2:
        raise ValueError(f"仅支持 mono 16-bit PCM WAV：{source}")
    if rate <= 0:
        raise ValueError(f"WAV 采样率非法：{source}")

    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if rate != sample_rate and data.size > 1:
        out_count = max(1, int(round(data.size * sample_rate / rate)))
        old_axis = np.arange(data.size, dtype=np.float64)
        new_axis = np.linspace(0.0, data.size - 1.0, out_count)
        data = np.interp(new_axis, old_axis, data).astype(np.float32)
    return data


def concat_wavs(paths: list[Path], out_path: Path, sample_rate: int = 24000) -> dict:
    """按顺序拼接多个 mono 16-bit WAV，相邻段之间插入 0.15 秒静音。

    :returns: ``{"path","duration","segments","size_bytes"}``。
    """
    sample_rate = _validate_sample_rate(sample_rate)
    if not paths:
        raise ValueError("paths 不能为空")

    gap = np.zeros(int(round(SEGMENT_GAP_SECONDS * sample_rate)), dtype=np.float32)
    pieces: list[np.ndarray] = []
    for index, item in enumerate(paths):
        if index:
            pieces.append(gap)
        pieces.append(_read_pcm16_mono(Path(item), sample_rate))

    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0).astype(np.float32)
    target = _write_wav(Path(out_path), audio, sample_rate)

    return {
        "path": str(target),
        "duration": round(audio.size / sample_rate, 3),
        "segments": len(paths),
        "size_bytes": target.stat().st_size,
    }


_REFERENCE_PHRASES: tuple[str, ...] = (
    "你好，我是你的数字人助理，很高兴见到你。",
    "欢迎来到 AIGC 数字人演播室，今天分享一段声音。",
    "这是一段用于声音克隆演示的参考音频，请保持自然。",
)


def make_reference_sample(
    out_path: Path,
    seconds: float = 4.0,
    seed: int = 7,
    sample_rate: int = 24000,
) -> dict:
    """生成确定性的伪语音参考样本，用于演示"音色克隆"流程。

    文本由固定短语按种子选择并循环补齐，再用同一套谐波生成器合成，
    最后裁剪/补零到 ``seconds`` 的精确样本数。

    :returns: 与 :func:`synthesize` 相同的键，另加 ``{"kind": "reference"}``。
    """
    sample_rate = _validate_sample_rate(sample_rate)
    if seconds <= 0:
        raise ValueError("seconds 必须大于 0")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed 必须是整数")

    phrase = _REFERENCE_PHRASES[seed % len(_REFERENCE_PHRASES)]
    timbre = ("warm", "bright", "deep")[seed % 3]
    voice = VoiceSpec(pitch=0.95 + 0.02 * (seed % 5), speed=1.0, timbre=timbre)

    text = phrase
    # 多留一点素材，随后精确裁剪；最多循环 64 次防止异常输入。
    for _ in range(64):
        if _text_seconds(text, float(voice.speed)) >= float(seconds) + 0.25:
            break
        text += phrase

    raw = _render_samples(text, voice, sample_rate)
    target_count = max(1, int(round(float(seconds) * sample_rate)))
    if raw.size < target_count:
        raw = np.pad(raw, (0, target_count - raw.size))
    else:
        raw = raw[:target_count]

    audio = _finalize(raw, sample_rate)
    target = _write_wav(Path(out_path), audio, sample_rate)
    return {
        "path": str(target),
        "duration": round(audio.size / sample_rate, 3),
        "sample_rate": sample_rate,
        "size_bytes": target.stat().st_size,
        "char_count": len(text),
        "voice": voice.as_dict(),
        "kind": "reference",
    }


__all__ = [
    "BASE_CHAR_SECONDS",
    "PUNCTUATION",
    "SEGMENT_GAP_SECONDS",
    "TIMBRE_FACTORS",
    "TIMBRE_HARMONICS",
    "VoiceSpec",
    "concat_wavs",
    "make_reference_sample",
    "synthesize",
]
