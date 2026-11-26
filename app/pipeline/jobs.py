"""任务队列：单 worker、步骤进度、协作式取消、重试、崩溃恢复。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.logging import get_logger
from app.models import Asset, Job, JobStep, Media, Voice, new_id
from app.pipeline.render import JobCancelled, render_audiobook, render_dialogue, render_dubbing

log = get_logger("app.pipeline")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """SQLite 取回的是 naive datetime，按 UTC 解释；其它库返回 aware 时原样使用。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class Reporter:
    def __init__(self, job_id: str, runner: JobRunner) -> None:
        self.job_id = job_id
        self.runner = runner
        self._seq = 0

    def begin(self, key: str, name: str) -> int:
        self._seq += 1
        with session_scope() as db:
            db.add(JobStep(job_id=self.job_id, seq=self._seq, name=f"{name}（{key}）", status="running"))
        return self._seq

    def detail(self, text: str) -> None:
        with session_scope() as db:
            step = db.scalars(select(JobStep).where(JobStep.job_id == self.job_id, JobStep.seq == self._seq)).first()
            if step is not None:
                step.detail = text[:500]

    def finish(self, seq: int, detail: str = "", status: str = "succeeded") -> None:
        with session_scope() as db:
            step = db.scalars(select(JobStep).where(JobStep.job_id == self.job_id, JobStep.seq == seq)).first()
            if step is None:
                return
            step.status = status
            step.detail = detail[:500]
            step.latency_ms = int((_now() - _aware(step.created_at)).total_seconds() * 1000)

    def progress(self, pct: int) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job is not None:
                job.progress = max(job.progress, min(100, int(pct)))

    def check_cancel(self) -> None:
        if self.runner.is_cancelled(self.job_id):
            raise JobCancelled("任务已取消")


class JobRunner:
    def __init__(self) -> None:
        settings = get_settings()
        self.executor = ThreadPoolExecutor(max_workers=max(1, settings.max_workers), thread_name_prefix="dub")
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, job_id: str) -> None:
        log.info("job_submitted", extra={"job_id": job_id})
        self.executor.submit(self._run, job_id)

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def _forget_cancel(self, job_id: str) -> None:
        """任务结束后从取消集合里移除，避免长跑进程里集合无限增长。"""
        with self._lock:
            self._cancelled.discard(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._cancelled.add(job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                self._forget_cancel(job_id)
                return {"ok": False, "reason": "任务不存在"}
            if job.status in {"succeeded", "failed", "cancelled"}:
                self._forget_cancel(job_id)
                return {"ok": False, "reason": f"任务已结束（{job.status}）"}
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.error = "排队中取消"
                job.finished_at = _now()
                self._forget_cancel(job_id)
                log.info("job_cancelled", extra={"job_id": job_id, "stage": "queued"})
                return {"ok": True, "status": "cancelled"}
        log.info("job_cancel_requested", extra={"job_id": job_id})
        return {"ok": True, "status": "cancelling"}

    def retry(self, job_id: str) -> str:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            clone = Job(
                kind=job.kind,
                title=f"{job.title}（重试）",
                status="queued",
                input=job.input,
            )
            db.add(clone)
            db.flush()
            new_job_id = clone.id
        self.submit(new_job_id)
        return new_job_id

    def recover(self) -> int:
        with session_scope() as db:
            rows = db.scalars(select(Job).where(Job.status.in_(["queued", "running"]))).all()
            for job in rows:
                job.status = "failed"
                job.error = "进程重启，任务中断（可重试）"
                job.finished_at = _now()
            return len(rows)

    # ------------------------------------------------------------------ #
    def _run(self, job_id: str) -> None:
        """执行入口：无论成功、失败还是取消，结束后都清掉取消标记。"""
        started = time.perf_counter()
        try:
            self._execute(job_id, started)
        finally:
            self._forget_cancel(job_id)

    def _execute(self, job_id: str, started: float) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                log.warning("job_missing", extra={"job_id": job_id})
                return
            if job.cancel_requested or self.is_cancelled(job_id):
                job.status = "cancelled"
                job.finished_at = _now()
                log.info("job_cancelled", extra={"job_id": job_id, "stage": "before_start"})
                return
            kind = job.kind
            payload = dict(job.input or {})
            voice = db.get(Voice, payload.get("voice_id")) if payload.get("voice_id") else None
            default_voice = _voice_dict(voice) if voice else _voice_dict(None)

            voice_map: dict[str, dict] = {}
            for speaker, voice_id in (payload.get("voice_map") or {}).items():
                mapped = db.get(Voice, voice_id)
                if mapped is not None:
                    voice_map[speaker] = _voice_dict(mapped)
            if not voice_map:
                voice_map = {payload.get("default_speaker", "旁白"): default_voice}
            else:
                voice_map.setdefault("旁白", default_voice)

            media = db.get(Media, payload["media_id"]) if payload.get("media_id") else None
            if media is not None:
                payload["media_path"] = media.path

            job.status = "running"
            job.started_at = _now()
            job.progress = 1

        log.info("job_started", extra={"job_id": job_id, "kind": kind})
        reporter = Reporter(job_id, self)
        try:
            if kind == "dialogue":
                result = render_dialogue(job_id, payload, voice_map, default_voice, reporter)
            elif kind == "dubbing":
                result = render_dubbing(job_id, payload, default_voice, reporter)
            elif kind == "audiobook":
                result = render_audiobook(job_id, payload, default_voice, reporter)
            else:
                raise ValueError(f"未知任务类型：{kind}")

            with session_scope() as db:
                for item in result["assets"]:
                    db.add(
                        Asset(
                            id=new_id("ast"),
                            job_id=job_id,
                            kind=item["kind"],
                            filename=item["filename"],
                            path=item["path"],
                            mime=item["mime"],
                            size_bytes=int(item.get("size_bytes") or 0),
                            duration=item.get("duration"),
                        )
                    )
                db.flush()
                assets = db.scalars(select(Asset).where(Asset.job_id == job_id)).all()
                summary = {key: value for key, value in result.items() if key != "assets"}
                summary["assets"] = [
                    {"id": a.id, "kind": a.kind, "filename": a.filename, "size_bytes": a.size_bytes} for a in assets
                ]
                job = db.get(Job, job_id)
                job.status = "succeeded"
                job.progress = 100
                job.output = summary
                job.finished_at = _now()
                job.duration_ms = int((time.perf_counter() - started) * 1000)

            log.info(
                "job_succeeded",
                extra={
                    "job_id": job_id,
                    "kind": kind,
                    "assets": len(assets),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )

        except JobCancelled:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is not None:
                    job.status = "cancelled"
                    job.error = "用户取消"
                    job.finished_at = _now()
                    job.duration_ms = int((time.perf_counter() - started) * 1000)
                    for step in job.steps:
                        if step.status == "running":
                            step.status = "cancelled"
            log.info("job_cancelled", extra={"job_id": job_id, "kind": kind})
        except Exception as exc:  # noqa: BLE001 —— 任何异常都要落到 job.error，不能让线程静默死掉
            log.exception("job_failed", extra={"job_id": job_id, "kind": kind})
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error = f"{type(exc).__name__}: {exc}"[:1000]
                    job.finished_at = _now()
                    job.duration_ms = int((time.perf_counter() - started) * 1000)
                    for step in job.steps:
                        if step.status == "running":
                            step.status = "failed"
                            step.detail = str(exc)[:300]


def _voice_dict(voice: Voice | None) -> dict[str, Any]:
    if voice is None:
        return {"id": None, "name": "默认音色", "pitch": 1.0, "speed": 1.0, "timbre": "warm"}
    return {
        "id": voice.id,
        "name": voice.name,
        "pitch": voice.pitch,
        "speed": voice.speed,
        "timbre": voice.timbre,
    }


runner = JobRunner()
