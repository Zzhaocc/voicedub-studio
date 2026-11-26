# 贡献指南

## 本地开发

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                  # 运行时依赖 + pytest/ruff

python -m pytest -q                    # 35 个测试（媒体测试会真的合成 WAV、编码 MP4）
python -m ruff check app tests scripts
python -m uvicorn app.main:app --port 8000
```

**ffmpeg / ffprobe 必须在 PATH 里**（或用 `FFMPEG_BIN` / `FFPROBE_BIN` 指定绝对路径）。
部署与排障见 [运维手册](docs/OPERATIONS.md)。

## 提 PR 前

1. `python -m pytest -q` 全绿；
2. `python -m ruff check app tests scripts` 无告警；
3. 新增管线步骤时通过 `Reporter.begin/finish` 上报，保持前端可见；
4. 音色参数改动必须同步 `tests/test_pipeline.py::test_design_from_text_maps_keywords`；
5. 改了对外接口，同步更新 `docs/API.md`；改了配置项，同步更新 `.env.example` 与 `docs/OPERATIONS.md`；
6. 不提交真实人物音频、未授权音色或客户素材。

## 接真实 TTS

替换 `app/media/tts.py` 里的 `synthesize`，保持返回结构：

```python
{"path": str, "duration": float, "sample_rate": int, "size_bytes": int}
```

上层管线（对白、配音、有声书）与 API 都不用改。真实模型（CosyVoice / IndexTTS / FishSpeech）
通常提供 HTTP 或本地推理接口，建议在 `media/` 下加一个适配器，而不是改管线；
默认采样率从 `settings.sample_rate` 取，不要在适配器里再写死。

## 声音克隆的边界

当前是参数级分析（基频 / 过零率），不是神经网络克隆。若要升级为真实克隆：

1. 新增 `voices/embedding.py`，把参考音频转成音色向量；
2. 在 `analyze_reference` 的返回里加 `embedding` 字段；
3. 合成时把向量传给 TTS 后端。

需要同时更新 README 与 `docs/ARCHITECTURE.md` 里的能力描述，避免夸大。

## 提交信息

用 `feat:` / `fix:` / `docs:` / `test:` / `refactor:` / `chore:` 前缀，一句话说清改了什么、为什么。
