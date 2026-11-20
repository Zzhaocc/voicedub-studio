"""演示数据：预设音色 + 一段可配音的示例视频。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Media, Voice
from app.voices.service import PRESET_VOICES


def seed_all() -> None:
    settings = get_settings()
    with session_scope() as db:
        existing = {voice.name for voice in db.scalars(select(Voice)).all()}
        for item in PRESET_VOICES:
            if item["name"] in existing:
                continue
            db.add(
                Voice(
                    name=item["name"],
                    kind="preset",
                    pitch=item["pitch"],
                    speed=item["speed"],
                    timbre=item["timbre"],
                    description=item["description"],
                )
            )

        has_media = db.scalars(select(Media)).first()
        if has_media is None:
            target = settings.upload_dir / "demo_source.mp4"
            if not target.exists():
                from app.media.video import make_placeholder_video

                make_placeholder_video(target, seconds=8.0, width=640, height=360, color="0x0f1b2d")
            from app.media.video import probe

            info = probe(target)
            db.add(
                Media(
                    kind="video",
                    filename="demo_source.mp4",
                    path=str(target),
                    mime="video/mp4",
                    duration=float(info.get("duration") or 8.0),
                    size_bytes=int(info.get("size_bytes") or 0),
                )
            )


if __name__ == "__main__":
    from app.db import init_db

    init_db()
    seed_all()
    print("seed done", Path("data").resolve())
