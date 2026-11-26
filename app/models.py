"""持久化模型。

四个实体：
- Voice     音色（preset 预设 / designed 声音设计 / cloned 参考音频 / custom 手填参数）
- Media     上传的源视频或音频
- Job       一次配音任务（对白 / 视频配音 / 有声书）
- Asset     产物（音频 / 字幕 / 视频 / 章节清单）

Job 与 JobStep、Asset 是级联关系：删除任务即删除步骤与产物记录，
文件清理由 ``DELETE /api/jobs/{id}`` 负责（先取消再删除）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Voice(Base):
    __tablename__ = "voices"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("voi"))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="preset")  # preset|designed|cloned|custom
    pitch: Mapped[float] = mapped_column(Float, default=1.0)
    speed: Mapped[float] = mapped_column(Float, default=1.0)
    timbre: Mapped[str] = mapped_column(String(20), default="warm")
    description: Mapped[str] = mapped_column(Text, default="")
    reference_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    reference_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Media(Base):
    __tablename__ = "media"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("med"))
    kind: Mapped[str] = mapped_column(String(20), default="video")  # video|audio
    filename: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(80), default="video/mp4")
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String(20), default="dialogue")  # dialogue|dubbing|audiobook
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    steps: Mapped[list[JobStep]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStep.seq"
    )
    assets: Mapped[list[Asset]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobStep(Base):
    __tablename__ = "job_steps"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("stp"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="steps")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (Index("ix_asset_job_kind", "job_id", "kind"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ast"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # audio|srt|video|chapters|cover
    filename: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="assets")
