# API 参考

Base URL：`http://127.0.0.1:8000`　交互文档：`/docs`（`DOCS_ENABLED=false` 时关闭）

任务异步：`POST /api/jobs/*` 返回 `202 + job_id`，轮询 `GET /api/jobs/{id}`。

**请求关联 ID**：每个响应都带 `X-Request-ID`（可用同名请求头透传上游值，最长 128 字符），
同一条 ID 会出现在服务端所有相关日志里。

**统一错误体**：

```json
{"detail": "任务不存在", "request_id": "9f2c1a7b3d10"}
```

`detail` 可能是字符串，也可能是结构化对象（字段级校验原因）。报障时请提供 `request_id`。

| 状态码 | 含义 |
|---|---|
| `404` | 资源不存在（任务 / 音色 / 素材 / 产物） |
| `409` | 状态冲突（预设音色不可删、任务未结束、任务已结束不可取消） |
| `410` | 产物文件已被清理 |
| `413` | 上传文件过大（base64 上限 60MB） |
| `422` | 参数 / 剧本 / 声音描述错误，或请求体不合法 |
| `503` | `/api/ready` 探测到依赖不可用 |

## 探针

`GET /api/health`（存活，不查库、不调 ffmpeg）：

```json
{
  "status": "ok", "version": "0.5.0", "environment": "production",
  "engine": "local-engine", "ffmpeg": "ffmpeg", "workers": 1,
  "sample_rate": 24000, "database": "sqlite", "docs_enabled": false, "uptime_s": 1284.5
}
```

`GET /api/ready`（就绪，检查数据库 / ffmpeg / ffprobe / 数据目录）：

```json
{"status": "ready", "checks": {"database": true, "ffmpeg": true, "ffprobe": true, "data_dir": true}, "version": "0.5.0"}
```

任一检查失败返回 `503` + `{"status": "degraded", ...}`。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活探针 |
| GET | `/api/ready` | 就绪探针（含 ffmpeg 与数据目录可写） |
| GET | `/api/catalog` | 音色、素材、示例文案、说话人 |
| GET/POST | `/api/voices` | 列表 / 手动新建 |
| POST | `/api/voices/design` | 声音设计（中文描述 → 参数） |
| POST | `/api/voices/clone` | 克隆（参考音频 base64，可选） |
| GET | `/api/voices/{id}/preview?text=` | 试听（返回 WAV） |
| DELETE | `/api/voices/{id}` | 删除非预设音色 |
| GET/POST | `/api/media` | 素材列表 / 上传（base64） |
| GET | `/api/media/{id}` | 播放源视频 |
| POST | `/api/jobs/dialogue` | 多角色对白 |
| POST | `/api/jobs/dubbing` | 视频配音 |
| POST | `/api/jobs/audiobook` | 有声书 |
| GET | `/api/jobs` / `/api/jobs/{id}` | 列表 / 详情（步骤 + 产物） |
| POST | `/api/jobs/{id}/cancel` · `/retry` | 取消 / 重试 |
| DELETE | `/api/jobs/{id}` | 删除任务与文件（运行中需先取消） |
| GET | `/api/assets/{id}` | 下载；`?inline=1` 预览 |
| GET | `/api/metrics` | 指标 |

## 1. 声音设计

```http
POST /api/voices/design
{"description": "低沉的中年男声，语速偏慢"}
```

```json
{
  "id": "voi_x", "name": "低沉的中年男声语速偏慢", "kind": "designed",
  "pitch": 0.84, "speed": 0.9, "timbre": "deep",
  "design": {"matched_keywords": ["低沉", "男", "慢"], "summary": "pitch 0.84 · speed 0.90 · deep"}
}
```

试听直接当 `<audio src>` 用：`GET /api/voices/voi_x/preview` → `audio/wav`。

## 2. 多角色对白

```http
POST /api/jobs/dialogue
{
  "title": "对白",
  "script": "[林夕] 你好。\n[周野] 你好啊。",
  "voice_map": {"林夕": "voi_a", "周野": "voi_b"},
  "default_voice_id": "voi_a"
}
```

```json
{"job_id": "job_x", "status": "queued", "poll": "/api/jobs/job_x"}
```

完成后：

```json
{
  "status": "succeeded",
  "output": {
    "duration": 5.68,
    "speakers": ["林夕", "周野"],
    "timeline": [
      {"start": 0.0, "end": 2.0, "speaker": "林夕", "text": "你好。"},
      {"start": 2.25, "end": 3.6, "speaker": "周野", "text": "你好啊。"}
    ]
  },
  "assets": [{"kind": "audio", "filename": "dialogue.wav"}, {"kind": "srt"}],
  "steps": [{"name": "解析剧本（script）"}, {"name": "逐句合成（tts）"}, {"name": "拼接与混音（mix）"}, {"name": "生成字幕（subtitle）"}]
}
```

## 3. 视频配音

```http
POST /api/jobs/dubbing
{"media_id": "med_x", "script": "新的配音。", "voice_id": "voi_x"}
```

输出 `video` / `audio` / `srt` / `original` 四个产物，并给出：

```json
{"kind": "dubbing", "source_duration": 8.0, "duration": 7.969, "fit_factor": 0.762}
```

`fit_factor = 合成时长 / 视频时长`；>1 表示加速，<1 表示放慢。

## 4. 有声书

```http
POST /api/jobs/audiobook
{"title": "试听", "voice_id": "voi_x", "chapters": [{"title": "第一章", "text": "正文…"}]}
```

```json
{"kind": "audiobook", "duration": 9.74, "chapter_count": 2,
 "chapters": [{"index": 1, "title": "第一章", "start": 0.0, "end": 3.7, "sentences": 2}]}
```

产物：`audiobook.wav`、`audiobook.srt`、`chapters.json`。

## 5. 上传素材

```http
POST /api/media
{"filename": "clip.mp4", "kind": "video", "data_b64": "<base64>"}
```

```json
{"id": "med_x", "kind": "video", "duration": 8.0, "size_bytes": 78968, "url": "/api/media/med_x"}
```

## 6. 指标

```json
{
  "jobs": {"total": 3, "succeeded": 3, "success_rate": 1.0, "avg_duration_ms": 336,
           "by_kind": {"dialogue": 1, "dubbing": 1, "audiobook": 1}},
  "audio": {"outputs": 4, "seconds": 39.4, "total_bytes": 1590000},
  "library": {"voices": 4, "media": 1, "cloned": 0}
}
```

## 7. 错误响应示例

业务校验失败（剧本为空、声音描述为空）：

```json
{"detail": "剧本为空", "request_id": "9f2c1a7b3d10"}
```

请求体本身不合法（缺必填字段 / 多传字段）：

```json
{
  "detail": {
    "message": "请求参数校验失败",
    "errors": [
      {"loc": ["body", "name"], "msg": "Field required", "type": "missing"},
      {"loc": ["body", "nope"], "msg": "Extra inputs are not permitted", "type": "extra_forbidden"}
    ]
  },
  "request_id": "9f2c1a7b3d10"
}
```

未预期异常（不回显堆栈）：

```json
{"detail": "服务内部错误，请携带 request_id 反馈", "request_id": "9f2c1a7b3d10"}
```

任务失败不是 HTTP 错误：接口返回 `200`，任务体里 `status=failed`，原因在 `error` 与失败步骤的 `detail` 中。
