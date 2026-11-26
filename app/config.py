"""全局配置：全部来自环境变量，便于容器化部署与多环境切换。

约定：
- 只读：进程启动时解析一次（``get_settings`` 带缓存），运行期不热更新；
- 数据目录单一来源：``VOICEDUB_DATA_DIR`` 决定库文件、上传、工作区的位置，
  换部署形态只需要挂一个卷；
- 可审计：``describe()`` 只导出非敏感字段，用于启动日志与 /api/health。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app import __version__

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _items(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """进程级配置快照。"""

    app_name: str = "VoiceDub Studio"
    version: str = __version__
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    debug: bool = field(default_factory=lambda: _bool("DEBUG", False))

    # ---- 存储：一个根目录派生全部子目录 ----
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/studio.db"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("VOICEDUB_DATA_DIR", str(PROJECT_ROOT / "data"))))

    # ---- 媒体 ----
    ffmpeg: str = field(default_factory=lambda: os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe: str = field(default_factory=lambda: os.getenv("FFPROBE_BIN", "ffprobe"))
    sample_rate: int = field(default_factory=lambda: _int("VOICEDUB_SAMPLE_RATE", 24000))
    segment_gap: float = field(default_factory=lambda: _float("VOICEDUB_SEGMENT_GAP", 0.25))
    chapter_gap: float = field(default_factory=lambda: _float("VOICEDUB_CHAPTER_GAP", 0.8))

    # ---- 任务队列 ----
    max_workers: int = field(default_factory=lambda: _int("VOICEDUB_MAX_WORKERS", 1))
    ffmpeg_timeout: float = field(default_factory=lambda: _float("VOICEDUB_FFMPEG_TIMEOUT", 300.0))

    # ---- 接入与可观测 ----
    cors_origins: tuple[str, ...] = field(default_factory=lambda: _items("CORS_ORIGINS", ("*",)))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    log_format: str = field(default_factory=lambda: os.getenv("LOG_FORMAT", "text").lower())
    request_id_header: str = field(default_factory=lambda: os.getenv("REQUEST_ID_HEADER", "X-Request-ID"))
    docs_enabled: bool = field(default_factory=lambda: _bool("DOCS_ENABLED", True))

    # ---- 派生子目录（单一根目录，容器里只挂一个卷）----
    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def describe(self) -> dict[str, object]:
        """对外暴露的运行态摘要（不含敏感信息）。"""
        return {
            "app": self.app_name,
            "version": self.version,
            "environment": self.environment,
            "database": "sqlite" if self.sqlite else "external",
            "data_dir": str(self.data_dir),
            "sample_rate": self.sample_rate,
            "workers": max(1, self.max_workers),
            "log_level": self.log_level,
            "docs_enabled": self.docs_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    for path in (settings.data_dir, settings.media_dir, settings.upload_dir, settings.work_dir):
        path.mkdir(parents=True, exist_ok=True)
    return settings


def reset_settings_cache() -> None:
    """仅供测试：环境变量变更后重新解析配置。"""
    get_settings.cache_clear()
