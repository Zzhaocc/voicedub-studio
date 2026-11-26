"""端到端演示：对白 → 视频配音 → 有声书。

python scripts/demo.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import Asset, Job, Media, Voice  # noqa: E402
from app.pipeline.jobs import runner  # noqa: E402
from app.seed import seed_all  # noqa: E402

LINE = "─" * 72


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def wait(job_id: str, timeout: float = 180.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job.status in {"succeeded", "failed", "cancelled"}:
                db.expunge(job)
                return job
        time.sleep(0.2)
    raise TimeoutError(job_id)


def run(kind: str, title: str, payload: dict) -> Job:
    with session_scope() as db:
        job = Job(kind=kind, title=title, status="queued", input=payload)
        db.add(job)
        db.flush()
        job_id = job.id
    print(f"  job_id = {job_id}")
    started = time.time()
    runner.submit(job_id)
    job = wait(job_id)
    print(f"  状态 {job.status}｜耗时 {time.time() - started:.1f}s｜进度 {job.progress}%")
    if job.status != "succeeded":
        print("  错误：", job.error)
    return job


def assets_of(job_id: str) -> list[Asset]:
    with session_scope() as db:
        return db.scalars(select(Asset).where(Asset.job_id == job_id)).all()


def main() -> int:
    init_db()
    seed_all()
    runner.recover()

    with session_scope() as db:
        voices = db.scalars(select(Voice).order_by(Voice.created_at)).all()
        media = db.scalars(select(Media)).first()
        print(f"音色 {len(voices)} 个｜素材：{media.filename}（{media.duration}s）")
        voice_id = voices[0].id
        media_id = media.id

    section("① 多角色对白")
    job = run(
        "dialogue",
        "演示 · 多角色对白",
        {
            "script": "[林夕] 大家好，欢迎收听本期节目。\n[周野] 今天聊聊声音设计。\n[林夕] 一句话就能生成一个音色。",
            "voice_map": {},
            "voice_id": voice_id,
            "gap": 0.2,
        },
    )
    if job.status == "succeeded":
        print(f"  时长 {job.output['duration']}s｜角色 {job.output['speakers']}")
        for item in job.output["timeline"]:
            print(f"    {item['start']:>5.2f}-{item['end']:<5.2f} [{item['speaker']}] {item['text']}")
        for asset in assets_of(job.id):
            print(f"    {asset.kind:<8} {asset.filename:<18} {asset.size_bytes:>8,} B")

    section("② 视频配音（对齐原视频时长）")
    job = run(
        "dubbing",
        "演示 · 视频配音",
        {
            "media_id": media_id,
            "script": "这是一段自动生成的配音。先合成语音，再按原视频时长做时间伸缩，最后替换音轨。",
            "voice_id": voice_id,
        },
    )
    if job.status == "succeeded":
        output = job.output
        print(f"  原视频 {output['source_duration']}s → 配音 {output['duration']}s（倍率 {output['fit_factor']}）")
        for asset in assets_of(job.id):
            print(f"    {asset.kind:<8} {asset.filename:<18} {asset.size_bytes:>8,} B")

    section("③ 有声书（分章 + 章节清单）")
    job = run(
        "audiobook",
        "演示 · 有声书",
        {
            "voice_id": voice_id,
            "chapters": [
                {"title": "第一章 · 声音的形状", "text": "每一种声音都有形状。低沉的声音像宽厚的木头。"},
                {
                    "title": "第二章 · 克隆与边界",
                    "text": "克隆一段声音只需要几秒钟的样本。技术没有立场，使用它的人必须有。",
                },
            ],
        },
    )
    if job.status == "succeeded":
        print(f"  全书 {job.output['duration']}s｜{job.output['chapter_count']} 章｜{job.output['total_chars']} 字")
        for chapter in job.output["chapters"]:
            print(f"    第{chapter['index']}章 {chapter['title']}  {chapter['start']:.1f}-{chapter['end']:.1f}s")

    section("④ 指标")
    from app.api.routes import metrics as metrics_fn

    with session_scope() as db:
        data = metrics_fn(db)
    jobs_meta = data["jobs"]
    audio_meta = data["audio"]
    print(f"任务 {jobs_meta['total']} 个（成功 {jobs_meta['succeeded']}），平均 {jobs_meta['avg_duration_ms']} ms")
    total_kb = audio_meta["total_bytes"] / 1024
    print(f"音频 {audio_meta['outputs']} 条 / {audio_meta['seconds']}s，产物 {total_kb:.0f} KB")
    print(f"音色库 {data['library']['voices']} 个，素材 {data['library']['media']} 个")

    print("\n启动控制台：python -m uvicorn app.main:app --port 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
