"""剧本解析：对白格式、有声书章节、长句切分。"""

from __future__ import annotations

import re

MAX_SEGMENT_CHARS = 28
SENTENCE_END = "。！？!?；;\n"
SOFT_BREAK = "，,、：: "

_SPEAKER_BRACKET = re.compile(r"^\s*[\[【]([^\]】]{1,20})[\]】]\s*(.+)$")
_SPEAKER_COLON = re.compile(r"^\s*([^\s:：]{1,12})\s*[:：]\s*(.+)$")


def split_sentences(text: str, max_chars: int = MAX_SEGMENT_CHARS) -> list[str]:
    """按句末标点切句，超长句再按逗号与长度切。"""
    raw = re.sub(r"[ \t\u3000]+", " ", (text or "").strip())
    if not raw:
        raise ValueError("文本为空")

    sentences: list[str] = []
    buf = ""
    for char in raw:
        buf += char
        if char in SENTENCE_END:
            if buf.strip(" " + SENTENCE_END):
                sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    result: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            result.append(sentence)
            continue
        current = ""
        for part in re.split(r"(?<=[，,、：:])", sentence):
            if not part:
                continue
            if len(current) + len(part) <= max_chars:
                current += part
            else:
                if current:
                    result.append(current)
                current = part
        if current:
            result.append(current)
    return [item for item in result if item.strip()]


def parse_dialogue(script: str) -> list[dict]:
    """解析多角色剧本。

    支持三种行：
        [小雅] 你好，欢迎收听。
        小雅：你好，欢迎收听。
        你好，欢迎收听。          ← 无角色标记，归为「旁白」
    """
    lines = [line.strip() for line in (script or "").splitlines() if line.strip()]
    if not lines:
        raise ValueError("剧本为空")

    segments: list[dict] = []
    for line in lines:
        match = _SPEAKER_BRACKET.match(line) or _SPEAKER_COLON.match(line)
        if match:
            speaker, text = match.group(1).strip(), match.group(2).strip()
        else:
            speaker, text = "旁白", line
        if not text:
            continue
        for part in split_sentences(text):
            segments.append({"speaker": speaker, "text": part})

    if not segments:
        raise ValueError("剧本里没有可合成的台词")
    return segments


def normalize_chapters(chapters: list[dict]) -> list[dict]:
    """校验并规范化有声书章节。"""
    if not chapters:
        raise ValueError("至少需要一章")
    normalized: list[dict] = []
    for index, chapter in enumerate(chapters, start=1):
        title = (chapter.get("title") or f"第 {index} 章").strip()
        text = (chapter.get("text") or "").strip()
        if not text:
            raise ValueError(f"第 {index} 章正文为空")
        normalized.append({"index": index, "title": title, "text": text})
    return normalized


def estimate_duration(text: str, speed: float = 1.0) -> float:
    units = len(text or "")
    pauses = sum(1 for char in text if char in "，。！？,.!?;；:：")
    return round((units * 0.16 + pauses * 0.12) / max(0.5, speed), 2)
