"""REST 接口：音色 / 素材 / 三类配音任务 / 产物 / 指标。"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AudiobookJobRequest,
    DialogueJobRequest,
    DubbingJobRequest,
    MediaCreate,
    VoiceCloneRequest,
    VoiceCreate,
    VoiceDesignRequest,
)
from app.config import get_settings
from app.db import get_db
from app.models import Asset, Job, Media, Voice, new_id
from app.pipeline.jobs import runner
from app.pipeline.script import normalize_chapters, parse_dialogue
from app.voices.service import analyze_reference, design_from_text, guess_name

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def _dt(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _voice_out(voice: Voice) -> dict[str, Any]:
    return {
        "id": voice.id,
        "name": voice.name,
        "kind": voice.kind,
        "pitch": voice.pitch,
        "speed": voice.speed,
        "timbre": voice.timbre,
        "description": voice.description,
        "reference_duration": voice.reference_duration,
        "analysis": voice.analysis or {},
        "created_at": _dt(voice.created_at),
    }


def _media_out(media: Media) -> dict[str, Any]:
    return {
        "id": media.id,
        "kind": media.kind,
        "filename": media.filename,
        "mime": media.mime,
        "duration": media.duration,
        "size_bytes": media.size_bytes,
        "url": f"/api/media/{media.id}",
        "created_at": _dt(media.created_at),
    }


def _asset_out(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "job_id": asset.job_id,
        "kind": asset.kind,
        "filename": asset.filename,
        "mime": asset.mime,
        "size_bytes": asset.size_bytes,
        "duration": asset.duration,
        "url": f"/api/assets/{asset.id}",
        "preview_url": f"/api/assets/{asset.id}?inline=1",
    }


def _job_out(job: Job, detail: bool = False) -> dict[str, Any]:
    data = {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "duration_ms": job.duration_ms,
        "created_at": _dt(job.created_at),
        "started_at": _dt(job.started_at),
        "finished_at": _dt(job.finished_at),
        "output": job.output or {},
    }
    if detail:
        data["input"] = job.input or {}
        data["steps"] = [
            {
                "seq": step.seq,
                "name": step.name,
                "status": step.status,
                "detail": step.detail,
                "latency_ms": step.latency_ms,
            }
            for step in job.steps
        ]
        data["assets"] = [_asset_out(asset) for asset in job.assets]
    return data


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #
@router.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    running = db.scalar(select(func.count()).select_from(Job).where(Job.status == "running")) or 0
    queued = db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) or 0
    return {
        "status": "ok",
        "version": settings.version,
        "engine": "local-engine",
        "ffmpeg": settings.ffmpeg,
        "running_jobs": running,
        "queued_jobs": queued,
        "workers": settings.max_workers,
    }


@router.get("/catalog", tags=["meta"])
def catalog(db: Session = Depends(get_db)) -> dict[str, Any]:
    voices = db.scalars(select(Voice).order_by(Voice.created_at)).all()
    media = db.scalars(select(Media).order_by(desc(Media.created_at)).limit(10)).all()
    return {
        "voices": [_voice_out(v) for v in voices],
        "media": [_media_out(m) for m in media],
        "demo": {
            "dialogue": (
                "[林夕] 大家好，欢迎收听本期节目。\n"
                "[周野] 今天我们聊一个很有意思的话题：声音能不能被设计。\n"
                "[林夕] 我觉得可以。同一个人的音色，也可以调出完全不同的气质。\n"
                "[周野] 那不如现场试一下，用三种音色念同一句话。"
            ),
            "dubbing": (
                "这是一段自动生成的配音。我们先把文案合成成语音，"
                "再按原视频的时长做时间伸缩，最后替换掉原来的音轨。"
            ),
            "audiobook": {
                "title": "第一章 · 声音的形状",
                "chapters": [
                    {
                        "title": "第一章 · 声音的形状",
                        "text": "每一种声音都有形状。低沉的声音像宽厚的木头，清亮的声音像玻璃。"
                        "我们记住了这些形状，于是能在人群里认出一个人。",
                    },
                    {
                        "title": "第二章 · 克隆与边界",
                        "text": "克隆一段声音只需要几秒钟的样本。技术本身没有立场，"
                        "但使用它的人必须有。授权、标注、可撤回，是这类产品的底线。",
                    },
                ],
            },
        },
        "speakers": ["林夕", "周野", "旁白"],
        "kind_labels": {"dialogue": "多角色对白", "dubbing": "视频配音", "audiobook": "有声书"},
    }


# --------------------------------------------------------------------------- #
# 音色
# --------------------------------------------------------------------------- #
@router.get("/voices", tags=["voices"])
def list_voices(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(Voice).order_by(Voice.created_at)).all()
    return {"items": [_voice_out(v) for v in rows]}


@router.post("/voices", status_code=201, tags=["voices"])
def create_voice(body: VoiceCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    voice = Voice(
        name=body.name,
        kind="custom",
        pitch=body.pitch,
        speed=body.speed,
        timbre=body.timbre,
        description=body.description,
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)
    return _voice_out(voice)


@router.post("/voices/design", status_code=201, tags=["voices"])
def design_voice(body: VoiceDesignRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        design = design_from_text(body.description)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    voice = Voice(
        name=(body.name or guess_name(body.description)).strip(),
        kind="designed",
        pitch=design["pitch"],
        speed=design["speed"],
        timbre=design["timbre"],
        description=design["description"],
        analysis={"matched_keywords": design["matched_keywords"]},
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)
    return {**_voice_out(voice), "design": design, "preview_text": body.preview_text}


@router.post("/voices/clone", status_code=201, tags=["voices"])
def clone_voice(body: VoiceCloneRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    voice_id = new_id("voi")
    reference = settings.upload_dir / f"{voice_id}.wav"

    if body.audio_b64:
        try:
            reference.write_bytes(base64.b64decode(body.audio_b64, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(422, f"audio_b64 不是合法 base64：{exc}") from exc
    else:
        from app.media.tts import make_reference_sample

        make_reference_sample(reference, seconds=4.0)

    try:
        analysis = analyze_reference(reference)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"参考音频无法解析（需要 16-bit WAV）：{exc}") from exc

    voice = Voice(
        id=voice_id,
        name=body.name,
        kind="cloned",
        pitch=analysis["pitch"],
        speed=1.0,
        timbre=analysis["timbre"],
        description=f"克隆自参考音频（{analysis['duration']:.1f}s，估算基频 {analysis['f0']:.0f}Hz）",
        reference_path=str(reference),
        reference_duration=analysis["duration"],
        analysis=analysis,
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)
    return {**_voice_out(voice), "analysis": analysis}


@router.get("/voices/{voice_id}/preview", tags=["voices"])
def preview_voice(voice_id: str, text: str = Query(default="你好，我是你的配音助手。", max_length=60), db: Session = Depends(get_db)) -> FileResponse:
    voice = db.get(Voice, voice_id)
    if voice is None:
        raise HTTPException(404, "音色不存在")
    settings = get_settings()
    from app.media.tts import VoiceSpec, synthesize

    out = settings.work_dir / f"preview_{voice.id}.wav"
    synthesize(text, out, VoiceSpec(pitch=voice.pitch, speed=voice.speed, timbre=voice.timbre))
    return FileResponse(out, media_type="audio/wav", filename=f"preview_{voice.id}.wav", content_disposition_type="inline")


@router.delete("/voices/{voice_id}", status_code=204, tags=["voices"])
def delete_voice(voice_id: str, db: Session = Depends(get_db)) -> None:
    voice = db.get(Voice, voice_id)
    if voice is None:
        raise HTTPException(404, "音色不存在")
    if voice.kind == "preset":
        raise HTTPException(409, "预设音色不能删除")
    db.delete(voice)
    db.commit()


# --------------------------------------------------------------------------- #
# 素材
# --------------------------------------------------------------------------- #
@router.get("/media", tags=["media"])
def list_media(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(Media).order_by(desc(Media.created_at))).all()
    return {"items": [_media_out(m) for m in rows]}


@router.post("/media", status_code=201, tags=["media"])
def upload_media(body: MediaCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if len(body.data_b64) > 60_000_000:
        raise HTTPException(413, "文件过大（base64 上限 60MB）")
    try:
        payload = base64.b64decode(body.data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, f"data_b64 不是合法 base64：{exc}") from exc

    media_id = new_id("med")
    suffix = Path(body.filename).suffix.lower() or (".mp4" if body.kind == "video" else ".wav")
    path = settings.upload_dir / f"{media_id}{suffix}"
    path.write_bytes(payload)

    duration = None
    try:
        if body.kind == "video":
            from app.media.video import probe

            duration = float(probe(path).get("duration") or 0) or None
        else:
            from app.media.audio import wav_info

            duration = wav_info(path)["duration"]
    except Exception:  # noqa: BLE001 —— 探测失败不阻断上传
        duration = None

    media = Media(
        id=media_id,
        kind=body.kind,
        filename=body.filename,
        path=str(path),
        mime="video/mp4" if body.kind == "video" else "audio/wav",
        duration=duration,
        size_bytes=len(payload),
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    return _media_out(media)


@router.get("/media/{media_id}", tags=["media"])
def stream_media(media_id: str, inline: int = 1, db: Session = Depends(get_db)) -> FileResponse:
    media = db.get(Media, media_id)
    if media is None or not Path(media.path).exists():
        raise HTTPException(404, "素材不存在")
    return FileResponse(
        media.path,
        media_type=media.mime,
        filename=media.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
def _require_voice(db: Session, voice_id: str | None) -> Voice:
    voice = db.get(Voice, voice_id) if voice_id else db.scalars(select(Voice).order_by(Voice.created_at)).first()
    if voice is None:
        raise HTTPException(409, "没有可用音色，请先初始化")
    return voice


def _create_job(db: Session, kind: str, title: str, payload: dict[str, Any]) -> dict[str, Any]:
    job = Job(kind=kind, title=title, status="queued", input=payload)
    db.add(job)
    db.commit()
    db.refresh(job)
    runner.submit(job.id)
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}


@router.post("/jobs/dialogue", status_code=202, tags=["jobs"])
def create_dialogue_job(body: DialogueJobRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        parse_dialogue(body.script)  # 先用解析器校验剧本，错误在入队前返回 422
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    voice_map = {speaker: voice_id for speaker, voice_id in body.voice_map.items() if db.get(Voice, voice_id)}
    default_voice = _require_voice(db, body.default_voice_id)
    payload = {
        "script": body.script,
        "voice_map": voice_map,
        "voice_id": default_voice.id,
        "gap": body.gap,
    }
    return _create_job(db, "dialogue", body.title, payload)


@router.post("/jobs/dubbing", status_code=202, tags=["jobs"])
def create_dubbing_job(body: DubbingJobRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Media, body.media_id) is None:
        raise HTTPException(404, "素材不存在")
    voice = _require_voice(db, body.voice_id)
    payload = {"media_id": body.media_id, "script": body.script, "voice_id": voice.id}
    return _create_job(db, "dubbing", body.title, payload)


@router.post("/jobs/audiobook", status_code=202, tags=["jobs"])
def create_audiobook_job(body: AudiobookJobRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        chapters = normalize_chapters(body.chapters)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    voice = _require_voice(db, body.voice_id)
    payload = {"chapters": chapters, "voice_id": voice.id}
    return _create_job(db, "audiobook", body.title, payload)


@router.get("/jobs", tags=["jobs"])
def list_jobs(status: str | None = None, limit: int = Query(30, le=200), db: Session = Depends(get_db)) -> dict[str, Any]:
    stmt = select(Job).order_by(desc(Job.created_at)).limit(limit)
    if status:
        stmt = stmt.where(Job.status == status)
    rows = db.scalars(stmt).all()
    return {"items": [_job_out(job) for job in rows]}


@router.get("/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return _job_out(job, detail=True)


@router.post("/jobs/{job_id}/cancel", tags=["jobs"])
def cancel_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "任务不存在")
    result = runner.cancel(job_id)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "取消失败"))
    return {"ok": True, "status": result["status"]}


@router.post("/jobs/{job_id}/retry", status_code=202, tags=["jobs"])
def retry_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "任务不存在")
    new_id_value = runner.retry(job_id)
    return {"job_id": new_id_value, "status": "queued", "poll": f"/api/jobs/{new_id_value}"}


@router.delete("/jobs/{job_id}", status_code=204, tags=["jobs"])
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status in {"running", "queued"}:
        raise HTTPException(409, "任务未结束，先取消再删除")
    settings = get_settings()
    target = settings.media_dir / job_id
    if target.exists():
        for path in sorted(target.rglob("*"), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except OSError:
                pass
    db.delete(job)
    db.commit()


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
@router.get("/assets/{asset_id}", tags=["assets"])
def download_asset(asset_id: str, inline: int = 0, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "产物不存在")
    settings = get_settings()
    path = Path(asset.path).resolve()
    if not str(path).startswith(str(settings.data_dir.resolve())):
        raise HTTPException(403, "非法的产物路径")
    if not path.exists():
        raise HTTPException(410, "产物文件已被清理")
    return FileResponse(
        path,
        media_type=asset.mime,
        filename=asset.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
@router.get("/metrics", tags=["metrics"])
def metrics(db: Session = Depends(get_db)) -> dict[str, Any]:
    jobs = db.scalars(select(Job)).all()
    assets = db.scalars(select(Asset)).all()
    done = [job for job in jobs if job.status == "succeeded"]
    durations = [job.duration_ms for job in done if job.duration_ms]
    return {
        "jobs": {
            "total": len(jobs),
            "succeeded": len(done),
            "failed": sum(1 for job in jobs if job.status == "failed"),
            "cancelled": sum(1 for job in jobs if job.status == "cancelled"),
            "running": sum(1 for job in jobs if job.status == "running"),
            "success_rate": round(len(done) / len(jobs), 3) if jobs else None,
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
            "by_kind": {kind: sum(1 for job in jobs if job.kind == kind) for kind in ("dialogue", "dubbing", "audiobook")},
        },
        "audio": {
            "outputs": sum(1 for asset in assets if asset.kind == "audio"),
            "seconds": round(sum(asset.duration or 0 for asset in assets), 1),
            "total_bytes": sum(asset.size_bytes for asset in assets),
            "videos": sum(1 for asset in assets if asset.kind == "video"),
        },
        "library": {
            "voices": db.scalar(select(func.count()).select_from(Voice)) or 0,
            "media": db.scalar(select(func.count()).select_from(Media)) or 0,
            "cloned": db.scalar(select(func.count()).select_from(Voice).where(Voice.kind == "cloned")) or 0,
        },
        "recent": [_job_out(job) for job in sorted(jobs, key=lambda item: item.created_at, reverse=True)[:5]],
    }
