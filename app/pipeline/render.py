"""三条渲染管线：多角色对白 / 视频配音 / 有声书。

共同点：切句 → 逐句合成 → 拼接 → 字幕 →（可选）混流。
不同点只在"怎么拼"和"要不要对齐原视频时长"。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.pipeline.script import normalize_chapters, parse_dialogue, split_sentences


class JobCancelled(RuntimeError):
    """用户取消。"""


# --------------------------------------------------------------------------- #
# 公共工具
# --------------------------------------------------------------------------- #
def _synth_segments(
    segments: list[dict],
    voice_for: Any,
    work_dir: Path,
    reporter: Any,
    prefix: str = "seg",
) -> list[Path]:
    from app.media.tts import VoiceSpec, synthesize

    paths: list[Path] = []
    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        reporter.check_cancel()
        voice = voice_for(segment)
        out = work_dir / f"{prefix}_{index:03d}.wav"
        synthesize(
            segment["text"],
            out,
            VoiceSpec(pitch=float(voice["pitch"]), speed=float(voice["speed"]), timbre=str(voice["timbre"])),
        )
        segment["path"] = out
        paths.append(out)
        reporter.progress(min(86, 10 + int(76 * index / total)))
        reporter.detail(f"已合成 {index}/{total} · {segment['text'][:14]}")
    return paths


def _write_srt(entries: list[dict], out_path: Path) -> dict:
    """按显式 start/end 写 SRT（拼接有静音时不能靠累加 duration）。"""
    from app.media.subtitles import format_ts

    lines: list[str] = []
    for index, entry in enumerate(entries, start=1):
        lines.append(str(index))
        lines.append(f"{format_ts(entry['start'])} --> {format_ts(entry['end'])}")
        prefix = f"[{entry['speaker']}] " if entry.get("speaker") else ""
        lines.append(prefix + entry["text"])
        lines.append("")
    body = "\n".join(lines)
    out_path.write_text(body, encoding="utf-8")
    duration = entries[-1]["end"] if entries else 0.0
    return {
        "path": str(out_path),
        "count": len(entries),
        "duration": round(duration, 3),
        "size_bytes": out_path.stat().st_size,
    }


# --------------------------------------------------------------------------- #
# 1) 多角色对白
# --------------------------------------------------------------------------- #
def render_dialogue(job_id: str, payload: dict, voices: dict[str, dict], default_voice: dict, reporter: Any) -> dict:
    from app.media.audio import mix_dialogue

    settings = get_settings()
    work_dir = settings.media_dir / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    seq = reporter.begin("script", "解析剧本")
    segments = parse_dialogue(payload.get("script") or "")
    speakers: list[str] = []
    for segment in segments:
        if segment["speaker"] not in speakers:
            speakers.append(segment["speaker"])
    reporter.finish(seq, f"{len(segments)} 句台词，{len(speakers)} 个角色：{'、'.join(speakers)}")
    reporter.progress(6)

    def voice_for(segment: dict) -> dict:
        return voices.get(segment["speaker"], default_voice)

    seq = reporter.begin("tts", "逐句合成")
    _synth_segments(segments, voice_for, work_dir, reporter)
    reporter.finish(seq, f"{len(segments)} 段音频")

    seq = reporter.begin("mix", "拼接与混音")
    audio_path = work_dir / "dialogue.wav"
    gap = float(payload.get("gap") or settings.segment_gap)
    mixed = mix_dialogue(
        [{"path": segment["path"], "text": segment["text"], "speaker": segment["speaker"]} for segment in segments],
        audio_path,
        gap=gap,
    )
    reporter.finish(seq, f"总时长 {mixed['duration']:.1f}s，{len(speakers)} 个角色")
    reporter.progress(90)

    seq = reporter.begin("subtitle", "生成字幕")
    srt = _write_srt(mixed["timeline"], work_dir / "dialogue.srt")
    reporter.finish(seq, f"{srt['count']} 条字幕")
    reporter.progress(100)

    return {
        "kind": "dialogue",
        "duration": mixed["duration"],
        "segment_count": len(segments),
        "speakers": speakers,
        "timeline": mixed["timeline"],
        "assets": [
            {
                "kind": "audio",
                "filename": "dialogue.wav",
                "path": str(audio_path),
                "mime": "audio/wav",
                "size_bytes": audio_path.stat().st_size,
                "duration": mixed["duration"],
            },
            {
                "kind": "srt",
                "filename": "dialogue.srt",
                "path": srt["path"],
                "mime": "text/plain; charset=utf-8",
                "size_bytes": srt["size_bytes"],
                "duration": None,
            },
        ],
    }


# --------------------------------------------------------------------------- #
# 2) 视频配音
# --------------------------------------------------------------------------- #
def render_dubbing(job_id: str, payload: dict, voice: dict, reporter: Any) -> dict:
    from app.media.audio import concat_with_gaps, fit_duration, wav_info
    from app.media.video import extract_audio, probe, replace_audio

    settings = get_settings()
    work_dir = settings.media_dir / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    source = Path(payload["media_path"])
    seq = reporter.begin("probe", "读取源视频")
    info = probe(source)
    video_duration = float(info.get("duration") or 0)
    reporter.finish(seq, f"{video_duration:.1f}s · {info['size_bytes']:,} B")
    reporter.progress(5)

    seq = reporter.begin("script", "切分台词")
    segments = [{"speaker": "配音", "text": part} for part in split_sentences(payload.get("script") or "")]
    reporter.finish(seq, f"{len(segments)} 句")
    reporter.progress(8)

    seq = reporter.begin("tts", "合成配音")
    paths = _synth_segments(segments, lambda _s: voice, work_dir, reporter)
    reporter.finish(seq, f"{len(paths)} 段音频")

    seq = reporter.begin("concat", "拼接配音")
    raw_audio = work_dir / "voice_raw.wav"
    merged = concat_with_gaps(paths, raw_audio, gap=min(settings.segment_gap, 0.18))
    reporter.finish(seq, f"{merged['duration']:.1f}s")
    reporter.progress(88)

    seq = reporter.begin("fit", "对齐视频时长")
    fitted = fit_duration(raw_audio, work_dir / "voice_fit.wav", target_seconds=video_duration)
    reporter.finish(seq, f"系数 {fitted['factor']:.2f} → {fitted['duration']:.1f}s")
    reporter.progress(92)

    seq = reporter.begin("mux", "替换音轨")
    dubbed = work_dir / "dubbed.mp4"
    replace_audio(source, Path(fitted["path"]), dubbed)
    reporter.finish(seq, f"{dubbed.stat().st_size:,} B")
    reporter.progress(96)

    # 字幕按配音时长比例缩放（fit 后时间轴整体缩放）
    seq = reporter.begin("subtitle", "生成字幕")
    scale = (fitted["duration"] / merged["duration"]) if merged["duration"] else 1.0
    entries: list[dict] = []
    cursor = 0.0
    for segment in segments:
        duration = wav_info(segment["path"])["duration"]
        entries.append(
            {
                "speaker": "",
                "text": segment["text"],
                "start": round(cursor * scale, 3),
                "end": round((cursor + duration) * scale, 3),
            }
        )
        cursor += duration + min(settings.segment_gap, 0.18)
    srt = _write_srt(entries, work_dir / "dubbed.srt")
    reporter.finish(seq, f"{srt['count']} 条字幕")
    reporter.progress(100)

    original = work_dir / "original.wav"
    extract_audio(source, original, fmt="wav")

    return {
        "kind": "dubbing",
        "duration": fitted["duration"],
        "source_duration": round(video_duration, 3),
        "fit_factor": fitted["factor"],
        "segment_count": len(segments),
        "assets": [
            {
                "kind": "video",
                "filename": "dubbed.mp4",
                "path": str(dubbed),
                "mime": "video/mp4",
                "size_bytes": dubbed.stat().st_size,
                "duration": fitted["duration"],
            },
            {
                "kind": "audio",
                "filename": "voice_fit.wav",
                "path": str(fitted["path"]),
                "mime": "audio/wav",
                "size_bytes": Path(fitted["path"]).stat().st_size,
                "duration": fitted["duration"],
            },
            {
                "kind": "srt",
                "filename": "dubbed.srt",
                "path": srt["path"],
                "mime": "text/plain; charset=utf-8",
                "size_bytes": srt["size_bytes"],
                "duration": None,
            },
            {
                "kind": "audio",
                "filename": "original.wav",
                "path": str(original),
                "mime": "audio/wav",
                "size_bytes": original.stat().st_size,
                "duration": round(video_duration, 3),
            },
        ],
    }


# --------------------------------------------------------------------------- #
# 3) 有声书
# --------------------------------------------------------------------------- #
def render_audiobook(job_id: str, payload: dict, voice: dict, reporter: Any) -> dict:
    from app.media.audio import concat_with_gaps, wav_info

    settings = get_settings()
    work_dir = settings.media_dir / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    seq = reporter.begin("script", "整理章节")
    chapters = normalize_chapters(payload.get("chapters") or [])
    total_chars = sum(len(chapter["text"]) for chapter in chapters)
    reporter.finish(seq, f"{len(chapters)} 章，{total_chars} 字")
    reporter.progress(5)

    chapter_files: list[Path] = []
    chapter_meta: list[dict] = []
    entries: list[dict] = []
    cursor = 0.0

    for chapter in chapters:
        reporter.check_cancel()
        chapter_start = cursor
        sentences = [{"speaker": "", "text": part} for part in split_sentences(chapter["text"])]
        seq = reporter.begin("tts", f"第 {chapter['index']} 章")
        paths = _synth_segments(sentences, lambda _s: voice, work_dir, reporter, prefix=f"ch{chapter['index']}")
        chapter_audio = work_dir / f"chapter_{chapter['index']:02d}.wav"
        merged = concat_with_gaps(paths, chapter_audio, gap=settings.segment_gap)
        reporter.finish(seq, f"{merged['duration']:.1f}s · {len(sentences)} 句")
        chapter_files.append(chapter_audio)

        for sentence in sentences:
            duration = wav_info(sentence["path"])["duration"]
            entries.append(
                {
                    "speaker": "",
                    "text": sentence["text"],
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                }
            )
            cursor += duration + settings.segment_gap
        chapter_end = cursor - settings.segment_gap
        chapter_meta.append(
            {
                "index": chapter["index"],
                "title": chapter["title"],
                "start": round(chapter_start, 3),
                "end": round(chapter_end, 3),
                "duration": round(chapter_end - chapter_start, 3),
                "sentences": len(sentences),
            }
        )
        cursor = chapter_end + settings.chapter_gap

    seq = reporter.begin("concat", "合并全书")
    book_path = work_dir / "audiobook.wav"
    book = concat_with_gaps(chapter_files, book_path, gap=settings.chapter_gap)
    reporter.finish(seq, f"{book['duration']:.1f}s · {len(chapter_files)} 章")
    reporter.progress(90)

    seq = reporter.begin("subtitle", "生成字幕")
    srt = _write_srt(entries, work_dir / "audiobook.srt")
    reporter.finish(seq, f"{srt['count']} 条字幕")

    seq = reporter.begin("chapters", "生成章节清单")
    chapters_path = work_dir / "chapters.json"
    chapters_path.write_text(
        json.dumps(
            {
                "title": payload.get("title") or "有声书",
                "total_duration": book["duration"],
                "chapters": chapter_meta,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    reporter.finish(seq, f"{len(chapter_meta)} 章")
    reporter.progress(100)

    return {
        "kind": "audiobook",
        "duration": book["duration"],
        "chapter_count": len(chapters),
        "total_chars": total_chars,
        "chapters": chapter_meta,
        "assets": [
            {
                "kind": "audio",
                "filename": "audiobook.wav",
                "path": str(book_path),
                "mime": "audio/wav",
                "size_bytes": book_path.stat().st_size,
                "duration": book["duration"],
            },
            {
                "kind": "srt",
                "filename": "audiobook.srt",
                "path": srt["path"],
                "mime": "text/plain; charset=utf-8",
                "size_bytes": srt["size_bytes"],
                "duration": None,
            },
            {
                "kind": "chapters",
                "filename": "chapters.json",
                "path": str(chapters_path),
                "mime": "application/json",
                "size_bytes": chapters_path.stat().st_size,
                "duration": None,
            },
        ],
    }
