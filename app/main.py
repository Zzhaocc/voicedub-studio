"""应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.db import init_db
from app.pipeline.jobs import runner
from app.seed import seed_all

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    seed_all()
    recovered = runner.recover()
    app.state.recovered_jobs = recovered
    app.state.settings = settings
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="VoiceDub Studio",
        description=(
            "AI 配音工作台：声音设计 + 音色克隆 + 多角色对白 + 视频配音（时长对齐）+ 有声书。"
            "内置离线语音引擎，无需 GPU 与模型权重。"
        ),
        version=settings.version,
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
