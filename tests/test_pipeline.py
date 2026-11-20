"""剧本解析、音色设计与三条渲染管线的测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Asset, Job, Media, Voice
from app.pipeline.jobs import runner
from app.pipeline.script import normalize_chapters, parse_dialogue, split_sentences
from app.voices.service import analyze_reference, design_from_text


def test_parse_dialogue_formats() -> None:
    script = "[林夕] 你好。\n周野：我很好。\n这是一句旁白。"
    segments = parse_dialogue(script)
    assert [s["speaker"] for s in segments] == ["林夕", "周野", "旁白"]
    assert segments[0]["text"] == "你好。"
    with pytest.raises(ValueError):
        parse_dialogue("   ")


def test_split_sentences_limits_length() -> None:
    parts = split_sentences("这是一句很长的话，需要被切成多个片段，因为合成时要逐句处理才稳定。")
    assert all(len(part) <= 28 for part in parts)
    assert len(parts) >= 2


def test_normalize_chapters_validates() -> None:
    chapters = normalize_chapters([{"title": "第一章", "text": "正文"}])
    assert chapters[0]["index"] == 1
    with pytest.raises(ValueError):
        normalize_chapters([{"title": "空的", "text": "  "}])


def test_design_from_text_maps_keywords() -> None:
    deep = design_from_text("低沉的中年男声，语速偏慢")
    bright = design_from_text("清亮的少女音，语速偏快")
    assert deep["pitch"] < 1.0
    assert deep["speed"] < 1.0
    assert deep["timbre"] == "deep"
    assert bright["pitch"] > 1.0
    assert bright["speed"] > 1.0
    assert "低沉" in deep["matched_keywords"]
    with pytest.raises(ValueError):
        design_from_text("")


def _voice_id() -> str:
    with session_scope() as db:
        return db.scalars(select(Voice).order_by(Voice.created_at)).first().id


def _media_id() -> str:
    with session_scope() as db:
        return db.scalars(select(Media)).first().id


def _create_job(kind: str, payload: dict) -> str:
    with session_scope() as db:
        job = Job(kind=kind, title=f"测试-{kind}", status="queued", input=payload)
        db.add(job)
        db.flush()
        return job.id


def _wait(job_id: str, timeout: float = 120.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job.status in {"succeeded", "failed", "cancelled"}:
                db.expunge(job)
                return job
        time.sleep(0.2)
    raise AssertionError(f"任务超时：{job_id}")


def test_dialogue_job_end_to_end() -> None:
    job_id = _create_job(
        "dialogue",
        {
            "script": "[林夕] 你好，欢迎收听。\n[周野] 谢谢邀请。",
            "voice_map": {},
            "voice_id": _voice_id(),
            "gap": 0.2,
        },
    )
    runner.submit(job_id)
    job = _wait(job_id)
    assert job.status == "succeeded", job.error
    assert job.output["duration"] > 1.0
    assert job.output["speakers"] == ["林夕", "周野"]
    with session_scope() as db:
        assets = db.scalars(select(Asset).where(Asset.job_id == job_id)).all()
        kinds = {asset.kind for asset in assets}
        assert {"audio", "srt"} <= kinds
        srt = next(asset for asset in assets if asset.kind == "srt")
        body = Path(srt.path).read_text(encoding="utf-8")
        assert "[林夕]" in body and "[周野]" in body


def test_audiobook_job_writes_chapters() -> None:
    job_id = _create_job(
        "audiobook",
        {
            "voice_id": _voice_id(),
            "chapters": [
                {"title": "第一章", "text": "第一段正文。"},
                {"title": "第二章", "text": "第二段正文。"},
            ],
        },
    )
    runner.submit(job_id)
    job = _wait(job_id)
    assert job.status == "succeeded", job.error
    assert job.output["chapter_count"] == 2
    chapters = job.output["chapters"]
    assert chapters[0]["start"] == 0
    assert chapters[1]["start"] >= chapters[0]["end"]
    with session_scope() as db:
        assets = db.scalars(select(Asset).where(Asset.job_id == job_id)).all()
        chapter_asset = next(asset for asset in assets if asset.kind == "chapters")
        data = json.loads(Path(chapter_asset.path).read_text(encoding="utf-8"))
        assert len(data["chapters"]) == 2


def test_dubbing_job_replaces_audio_track() -> None:
    job_id = _create_job(
        "dubbing",
        {
            "media_id": _media_id(),
            "script": "这是一段测试配音。",
            "voice_id": _voice_id(),
        },
    )
    runner.submit(job_id)
    job = _wait(job_id)
    assert job.status == "succeeded", job.error
    assert job.output["kind"] == "dubbing"
    with session_scope() as db:
        assets = db.scalars(select(Asset).where(Asset.job_id == job_id)).all()
        video = next(asset for asset in assets if asset.kind == "video")
        assert Path(video.path).exists()
        assert video.size_bytes > 20_000
        assert abs((video.duration or 0) - job.output["source_duration"]) < 1.0


def test_analyze_reference_from_generated_sample() -> None:
    from app.config import get_settings
    from app.media.tts import make_reference_sample

    path = get_settings().work_dir / "ref_test.wav"
    make_reference_sample(path, seconds=1.5)
    analysis = analyze_reference(path)
    assert analysis["f0"] > 50
    assert 0.5 <= analysis["pitch"] <= 2.0
    assert analysis["timbre"] in {"warm", "bright", "deep"}


def test_cancel_and_retry() -> None:
    job_id = _create_job(
        "dialogue",
        {"script": "[旁白] 这是一句用来测试取消的台词。" * 4, "voice_id": _voice_id(), "gap": 0.2},
    )
    runner.submit(job_id)
    time.sleep(0.2)
    runner.cancel(job_id)
    job = _wait(job_id)
    assert job.status in {"cancelled", "succeeded"}

    new_id = runner.retry(job_id)
    assert new_id != job_id
