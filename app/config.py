"""全局配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "VoiceDub Studio"
    version: str = "0.4.0"

    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/studio.db"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("VOICEDUB_DATA_DIR", str(PROJECT_ROOT / "data"))))

    media_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "media")
    upload_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "uploads")
    work_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "work")

    ffmpeg: str = field(default_factory=lambda: os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe: str = field(default_factory=lambda: os.getenv("FFPROBE_BIN", "ffprobe"))

    sample_rate: int = field(default_factory=lambda: _int("VOICEDUB_SAMPLE_RATE", 24000))
    segment_gap: float = field(default_factory=lambda: _float("VOICEDUB_SEGMENT_GAP", 0.25))
    chapter_gap: float = field(default_factory=lambda: _float("VOICEDUB_CHAPTER_GAP", 0.8))
    max_workers: int = field(default_factory=lambda: _int("VOICEDUB_MAX_WORKERS", 1))

    @property
    def sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for path in (settings.data_dir, settings.media_dir, settings.upload_dir, settings.work_dir):
        path.mkdir(parents=True, exist_ok=True)
    return settings
