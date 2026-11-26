# VoiceDub Studio —— 常用开发与运维命令
# Windows 建议直接用 README 里的 PowerShell / start.bat 命令

PYTHON ?= python
PORT ?= 8000
IMAGE ?= voicedub-studio:0.5.0

.PHONY: help install install-dev run demo test lint smoke docker-build docker-up docker-down clean

help:  ## 显示可用命令
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## 安装运行时依赖
	$(PYTHON) -m pip install -r requirements.txt

install-dev:  ## 安装开发依赖（含测试与 ruff）
	$(PYTHON) -m pip install -r requirements-dev.txt

run:  ## 启动控制台与 API
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

demo:  ## 端到端跑三条链路（不启服务，需要 ffmpeg）
	$(PYTHON) scripts/demo.py

test:  ## 单元测试 + 覆盖率（需要 ffmpeg）
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing

lint:  ## 静态检查
	$(PYTHON) -m ruff check app tests scripts

smoke:  ## 对已启动的服务做端到端冒烟（需先 make run）
	$(PYTHON) scripts/smoke_http.py $(PORT)

docker-build:  ## 构建镜像（内含 ffmpeg）
	docker build -t $(IMAGE) .

docker-up:  ## 用 compose 启动
	docker compose up -d --build

docker-down:  ## 停止 compose
	docker compose down

clean:  ## 清理本地缓存（不删源码与产物）
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
