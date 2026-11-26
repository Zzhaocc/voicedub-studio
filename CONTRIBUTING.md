# 贡献指南

本文说明本地开发、变更流程与扩展方式。版本号规则与发布流程见 [版本与发布](docs/RELEASE.md)，
部署与排障见 [运维手册](docs/OPERATIONS.md)。

## 本地开发

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                  # 运行时依赖 + pytest/ruff

python -m pytest -q                    # 35 个测试（媒体用例会真实编码 MP4）
python -m ruff check app tests scripts
python -m uvicorn app.main:app --port 8000
```

**ffmpeg / ffprobe 必须在 PATH 里**（或用 `FFMPEG_BIN` / `FFPROBE_BIN` 指定绝对路径）。

## 变更流程

1. 从 `main` 切短分支：`feat/*`、`fix/*`、`docs/*`；
2. 提交信息用 `feat:` / `fix:` / `docs:` / `test:` / `refactor:` / `chore:` 前缀；
3. PR 按 [模板](.github/PULL_REQUEST_TEMPLATE.md) 填写自检项，CI 全绿并至少一次 review；
4. 面向用户的变更，在 `CHANGELOG.md` 的 `Unreleased` 段落补一条；
5. 合并使用 squash merge；发布按 [版本与发布](docs/RELEASE.md) 执行。

## 提 PR 前

1. `python -m pytest -q` 全绿；
2. `python -m ruff check app tests scripts` 无告警；
3. 新增引擎实现 `app/providers/base.py` 里的 Protocol，不要绕过管线直接写路由逻辑；
4. 新增工具走 `POST /api/tools/{tool}` 的约定：输入 `asset_id`，输出 `result` + 可选新 `asset`；
5. 改了对外接口同步 `docs/API.md`；改了配置项同步 `.env.example` 与 `docs/OPERATIONS.md`；
6. 不提交真实人物音频、未授权音色或客户素材。

## 接真实 TTS

替换 `app/media/tts.py` 里的 `synthesize`，保持返回结构：

```python
{"path": str, "duration": float, "sample_rate": int, "size_bytes": int}
```

上层管线（对白、配音、有声书）与 API 都不用改。真实模型（CosyVoice / IndexTTS / FishSpeech）
通常提供 HTTP 或本地推理接口，建议在 `media/` 下加一个适配器，而不是改管线；
采样率取 `settings.sample_rate`，不要在适配器里写死。

## 声音克隆的边界

当前是参数级分析（基频 / 过零率），不是神经网络克隆。若要升级为真实克隆：

1. 新增 `voices/embedding.py`，把参考音频转成音色向量；
2. 在 `analyze_reference` 的返回里加 `embedding` 字段；
3. 合成时把向量传给 TTS 后端。

需要同时更新 README 与 `docs/ARCHITECTURE.md` 里的能力描述，避免夸大。
