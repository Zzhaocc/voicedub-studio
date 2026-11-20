# API 参考

Base URL：`http://127.0.0.1:8000`　交互文档：`/docs`

任务异步：`POST /api/jobs/*` 返回 `202 + job_id`，轮询 `GET /api/jobs/{id}`。
错误码：`404` 资源不存在、`409` 状态冲突（预设音色不可删、任务未结束）、`410` 产物已清理、`422` 参数/剧本错误。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
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
| DELETE | `/api/jobs/{id}` | 删除任务与文件 |
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
