# 贡献指南

## 本地开发

```bash
pip install -r requirements.txt      # 需要 ffmpeg
python -m pytest -q
python -m uvicorn app.main:app --port 8000
```

## 提 PR 前

1. `python -m pytest -q` 全绿（媒体测试会真的合成 WAV、编码 MP4）；
2. 新增管线步骤时通过 `Reporter.begin/finish` 上报，保持前端可见；
3. 音色参数改动必须同步 `tests/test_pipeline.py::test_design_from_text_maps_keywords`；
4. 不提交真实人物音频、未授权音色或客户素材。

## 接真实 TTS

替换 `app/media/tts.py` 里的 `synthesize`，保持返回结构：

```python
{"path": str, "duration": float, "sample_rate": int, "size_bytes": int}
```

上层管线（对白、配音、有声书）与 API 都不用改。真实模型（CosyVoice / IndexTTS / FishSpeech）
通常提供 HTTP 或本地推理接口，建议在 `media/` 下加一个适配器，而不是改管线。

## 声音克隆的边界

当前是参数级分析（基频 / 过零率），不是神经网络克隆。若要升级为真实克隆：

1. 新增 `voices/embedding.py`，把参考音频转成音色向量；
2. 在 `analyze_reference` 的返回里加 `embedding` 字段；
3. 合成时把向量传给 TTS 后端。

需要同时更新 README 与 `docs/ARCHITECTURE.md` 里的能力描述，避免夸大。
