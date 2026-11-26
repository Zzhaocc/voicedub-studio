"""应用入口：装配 API、静态控制台、可观测中间件与启动自检。

启动顺序：配置日志 → 建表 → 灌入预设音色与示例素材 → 恢复中断任务 → 暴露 /api 与 /。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.db import init_db
from app.errors import install_error_handlers
from app.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.pipeline.jobs import runner
from app.seed import seed_all

log = get_logger("app.main")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

OPENAPI_TAGS = [
    {"name": "meta", "description": "健康检查、就绪探针、能力目录"},
    {"name": "voices", "description": "音色：预设、声音设计、参数级克隆、试听"},
    {"name": "media", "description": "素材：上传与播放源视频/音频"},
    {"name": "jobs", "description": "任务：多角色对白、视频配音、有声书；取消/重试"},
    {"name": "assets", "description": "产物下载：音频、字幕、成片、章节清单"},
    {"name": "metrics", "description": "平台指标：成功率、平均耗时、产出时长"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log.info("startup", extra=settings.describe())

    init_db()
    seed_all()
    recovered = runner.recover()
    if recovered:
        log.warning("recovered_interrupted_jobs", extra={"count": recovered})

    app.state.settings = settings
    app.state.started_at = time.time()
    app.state.recovered_jobs = recovered

    yield

    log.info("shutdown", extra={"uptime_s": round(time.time() - app.state.started_at, 3)})


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        description=(
            "AI 配音工作台：声音设计 + 音色克隆 + 多角色对白 + 视频配音（时长对齐）+ 有声书。"
            "内置离线语音引擎，无需 GPU 与模型权重。"
        ),
        version=__version__,
        openapi_tags=OPENAPI_TAGS,
        license_info={"name": "MIT"},
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    # 添加顺序决定执行顺序：最后添加的在最外层（CORS 需要最先处理预检与错误响应）
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, header_name=settings.request_id_header)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.request_id_header],
    )

    install_error_handlers(app)
    app.include_router(router)

    @app.get("/api", include_in_schema=False)
    def api_root() -> dict[str, object]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "environment": settings.environment,
            "docs": "/docs" if settings.docs_enabled else None,
            "console": "/",
        }

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
