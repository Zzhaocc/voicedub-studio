"""测试入口：导入 app 前切到独立测试库。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DB = BASE_DIR / "data" / "test_studio.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["VOICEDUB_DATA_DIR"] = str(BASE_DIR / "data")
os.environ["VOICEDUB_MAX_WORKERS"] = "1"

for suffix in ("", "-wal", "-shm"):
    candidate = Path(str(TEST_DB) + suffix)
    if candidate.exists():
        candidate.unlink()

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepared_db():
    from app.db import init_db
    from app.seed import seed_all

    init_db()
    seed_all()
    yield

    # 收尾前等后台任务跑完：否则最后一个 job 的日志会落到已关闭的 stdout 上。
    from app.pipeline.jobs import runner

    runner.executor.shutdown(wait=True)
