# 运维手册

面向部署与值班：怎么起、怎么探活、怎么看日志、怎么备份、出问题先看哪里。

---

## 1. 部署形态

### 1.1 本地（最快）

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

前置条件只有一个：**`ffmpeg` / `ffprobe` 在 PATH 里**（或通过 `FFMPEG_BIN` / `FFPROBE_BIN` 指定）。

数据全部落在 `VOICEDUB_DATA_DIR`（默认 `./data`）：`studio.db`、`uploads/`、`media/`、`work/`。

### 1.2 容器

```bash
docker build -t voicedub-studio:0.5.0 .
docker run -d --name voicedub \
  -p 8000:8000 \
  -v voicedub-data:/app/data \
  -e LOG_FORMAT=json \
  voicedub-studio:0.5.0
```

镜像里已装 ffmpeg，以非 root 用户（uid 10001）运行，内置 `HEALTHCHECK` 指向 `/api/health`。

### 1.3 Compose

```bash
docker compose up -d --build
docker compose logs -f studio
docker compose down
```

`docker-compose.yml` 把 `/app/data` 挂到具名卷 `studio-data`，容器重建不丢库与产物。

### 1.4 Kubernetes 要点

- **单副本 + RWO 卷**：队列是进程内线程池，多副本会各跑各的；SQLite 也不适合共享写；
- `livenessProbe` → `GET /api/health`（不查库）；`readinessProbe` → `GET /api/ready`（查库/ffmpeg/数据目录）；
- ffmpeg 是硬依赖，基础镜像必须带它，否则 `/api/ready` 会一直 503；
- 内置接口不带鉴权，对外必须置于网关之后。

> 要横向扩容，先把「队列」换成外部队列（Redis / SQS）并把任务状态与产物放到共享存储，见第 7 节。

---

## 2. 环境变量

完整清单见 [`.env.example`](../.env.example)，关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 环境标识，写入启动日志与 `/api/health` |
| `VOICEDUB_DATA_DIR` | `./data` | 数据根目录，派生 `uploads/` `media/` `work/` |
| `DATABASE_URL` | `sqlite:///./data/studio.db` | 换 PG 时改成 `postgresql+psycopg://...` |
| `FFMPEG_BIN` `FFPROBE_BIN` | `ffmpeg` `ffprobe` | 可执行文件路径 |
| `VOICEDUB_FFMPEG_TIMEOUT` | `300` | 单条 ffmpeg 命令超时（秒），超时任务置 failed |
| `VOICEDUB_SAMPLE_RATE` | `24000` | 输出采样率，贯穿 TTS / 混音 / 抽取音轨 |
| `VOICEDUB_SEGMENT_GAP` | `0.25` | 对白句间静音（秒） |
| `VOICEDUB_CHAPTER_GAP` | `0.8` | 有声书章间静音（秒） |
| `VOICEDUB_MAX_WORKERS` | `1` | 队列并发；CPU 核数少时保持 1 |
| `CORS_ORIGINS` | `*` | 逗号分隔；生产填具体域名 |
| `LOG_LEVEL` `LOG_FORMAT` | `INFO` `text` | `LOG_FORMAT=json` 用于日志采集 |
| `REQUEST_ID_HEADER` | `X-Request-ID` | 关联 ID 的头部名 |
| `DOCS_ENABLED` | `true` | 生产建议设为 `false`，关闭 `/docs` |

配置在进程启动时解析一次（`get_settings` 带缓存），**改完必须重启**。

---

## 3. 探针

| 探针 | 端点 | 期望 | 用途 |
|---|---|---|---|
| 存活 | `GET /api/health` | `200` + `{"status":"ok"}` | 进程是否还能响应；不查库、不调 ffmpeg |
| 就绪 | `GET /api/ready` | `200` + `{"status":"ready"}` | 数据库、ffmpeg、ffprobe、数据目录可写；`503` 时先摘流量 |

```bash
curl -s localhost:8000/api/ready | jq
# {"status":"ready","checks":{"database":true,"ffmpeg":true,"ffprobe":true,"data_dir":true},"version":"0.5.0"}
```

就绪检查里的 `data_dir` 是**真实写一次探针文件**，比看权限位可靠：磁盘满或卷只读会立刻暴露。

---

## 4. 日志与排障

### 4.1 结构

- `LOG_FORMAT=text`：`时间 级别 [request_id] logger: 事件 key=value`；
- `LOG_FORMAT=json`：一行一个 JSON，固定字段 `ts` / `level` / `logger` / `msg` / `request_id` + 业务字段。

### 4.2 关键事件

| 事件 | 级别 | 关键字段 |
|---|---|---|
| `startup` | INFO | 配置摘要（不含敏感信息） |
| `request` | INFO | `method` `path` `status` `duration_ms` `request_id` |
| `job_submitted` | INFO | `job_id` |
| `job_started` | INFO | `job_id` `kind` |
| `job_succeeded` | INFO | `job_id` `kind` `assets` `duration_ms` |
| `job_cancelled` | INFO | `job_id` `stage` |
| `job_failed` | ERROR | 带堆栈；`job.error` 里同时保存原因 |
| `unhandled_error` | ERROR | 带堆栈 + `request_id` |

### 4.3 定位一次报错

错误响应里带 `request_id`，与响应头 `X-Request-ID` 相同：

```bash
# 前端或用户反馈 {"detail": "...", "request_id": "9f2c1a7b3d10"}
grep '"request_id": "9f2c1a7b3d10"' /var/log/voicedub.log | jq
```

任务失败时再按 `job_id` 查 `GET /api/jobs/{id}`：`steps` 里会标出失败的步骤与 `detail`，
`error` 里保留 ffmpeg 的 stderr 尾部。

---

## 5. 数据与备份

### 5.1 SQLite + 文件

```bash
# 一致性备份（不要直接 cp 正在写入的库）
sqlite3 data/studio.db ".backup '/backup/studio-$(date +%F).db'"
tar czf /backup/media-$(date +%F).tgz data/media data/uploads
```

- 已开启 WAL：`data/` 下会有 `-wal` / `-shm`，用 `.backup` 或停机拷贝；
- `media/` 是产物、`uploads/` 是源素材，两者一起备份才能保证任务可重放；
- 保留策略按合规要求设定；本仓库不内置自动备份，交给宿主机 cron 或云盘快照。

### 5.2 迁移到 PostgreSQL

```bash
pip install "psycopg[binary]"
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/voicedub"
```

- 表由 SQLAlchemy `create_all` 自动创建，适合全新库；
- 已有数据需自行搬迁；生产建议引入 Alembic 管理变更；
- 换库后仍建议单写路径：任务队列本身就是顺序执行。

---

## 6. 升级与回滚

1. 打镜像标签：`docker build -t voicedub-studio:<版本> .`；
2. 备份数据库与 `media/`；
3. 滚动更新，观察 `/api/ready` 与失败率；
4. 出问题回滚镜像标签 + 备份的数据。

启动时会把上次未结束的 `queued` / `running` 任务标记为 `failed`（错误信息提示可重试），
这是刻意设计：宁可让用户点"重试"，也不让任务永远转圈。

---

## 7. 容量与扩容

| 瓶颈 | 现状 | 扩容方向 |
|---|---|---|
| 任务吞吐 | 进程内 `ThreadPoolExecutor`，默认 1 并发 | 调 `VOICEDUB_MAX_WORKERS`（受 CPU 与 ffmpeg 限制），或换外部队列 + 多副本 |
| CPU | ffmpeg 转码与响度归一是主要开销 | 给容器设 CPU limit；音频链路（对白/有声书）本身是 numpy，开销小 |
| 磁盘 | 每个任务的中间 WAV 都在 `media/<job_id>/` | 定期清理已完成任务的中间文件；产物换对象存储 |
| 数据库 | SQLite 单写者 | 换 PostgreSQL；队列外置后可多副本 |

压测建议：先用 `python scripts/smoke_http.py` 验证功能，再用 `hey` / `k6` 打
`POST /api/jobs/dialogue`，观察 `/api/metrics` 的成功率与 `avg_duration_ms`。

---

## 8. 常见故障

| 现象 | 先看什么 | 处理 |
|---|---|---|
| `/api/ready` 返回 503 | `checks` 里哪一项为 `false` | `ffmpeg=false` → 镜像/主机没装 ffmpeg 或 `FFMPEG_BIN` 写错；`data_dir=false` → 卷只读或磁盘满 |
| 任务 `failed` 且 error 含"超时" | `VOICEDUB_FFMPEG_TIMEOUT` | 素材过大或机器太慢，调大超时或缩短文案 |
| 任务 `failed` 且 error 是 ffmpeg stderr | 素材是否可解码 | `ffprobe` 手工验一遍；必要时先转码再上传 |
| 成片没有声音 | 源视频时长 vs 合成时长 | `-shortest` 会按较短的一路截断；文案过短就补齐文案 |
| 时长倍率异常（factor 贴边） | `output.fit_factor` | 1.5 秒视频配 30 秒文案，必然快得离谱；先校验素材时长 |
| 任务一直 `queued` | 进程是否重启过 | 启动恢复会把 stale 任务标 failed，重试即可 |
| 控制台接口 CORS 报错 | `CORS_ORIGINS` | 生产填具体域名，不要既留 `*` 又想带凭证 |
| 磁盘持续增长 | `data/media` 与 `data/uploads` | 删除已完成任务（会清理目录）或加保留策略 |
| 上传 413 | 60MB base64 上限 | 网关与 `MediaCreate` 双重限制；大文件建议走对象存储直传 |

---

## 9. 上线前检查清单

- [ ] `APP_ENV=production`，`DOCS_ENABLED=false`
- [ ] 基础镜像含 ffmpeg，`/api/ready` 返回 200
- [ ] `VOICEDUB_DATA_DIR` 指向持久卷，且已做备份
- [ ] `CORS_ORIGINS` 为具体域名，未使用 `*`
- [ ] 网关已终止 TLS 并接入鉴权
- [ ] `liveness` / `readiness` 探针已配置，告警接到人
- [ ] 日志已接入采集平台（`LOG_FORMAT=json`），保留期符合合规要求
- [ ] `python -m pytest` 与 `ruff check` 在发布分支全绿
- [ ] 参考音频与素材已取得授权，且支持删除 / 撤回
