# 变更记录

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.5.0] - 2026-11-26

生产化加固版本：补齐可观测性、探针、容器化与运维文档，并修掉配置项"写了但没生效"的问题。
三条渲染链路的产出与 0.4.0 保持一致。

### 修复

- **`FFMPEG_BIN` / `FFPROBE_BIN` 之前只写在 `.env.example`，代码里根本没读** ——
  媒体层现在每次都按配置取可执行路径，换路径、进镜像、走多版本 ffmpeg 都不再需要改代码。
- **`VOICEDUB_SAMPLE_RATE` 之前是死配置** —— 现在贯穿 TTS、混音、时长对齐、抽取音轨。
- **`VOICEDUB_DATA_DIR` 之前只影响库文件** —— 现在由它派生 `media/`、`uploads/`、`work/`，
  容器里只挂一个卷即可；原来这三个目录写死在源码目录下，容器化会写不进去。
- 时间字段统一输出 UTC ISO8601（`Z` 结尾），不再依赖数据库返回的 naive datetime。
- `JobStep` 耗时计算在 PostgreSQL（aware datetime）下会算错，已做兼容。
- 任务取消集合会无限增长（每个被取消过的 job_id 永久驻留）—— 任务结束即移除。
- 删除任务时逐个删除文件会残留空目录，改为整目录清理（`shutil.rmtree`）。

### 新增

- **可观测性**：结构化日志（`text` / `json`），每条日志带 `request_id`，可在一次任务的全部步骤间串联；
  日志级别与格式由环境变量控制；stdout 关闭时不会反过来打断业务线程。
- **请求中间件**：关联 ID（可透传上游 `X-Request-ID`）、统一访问日志、基础安全响应头。
- **统一错误契约**：所有错误响应带 `request_id`；`422` 结构化返回逐字段校验原因；未预期异常不回显堆栈。
- **探针分离**：`/api/health` 为存活探针（不查库、不调 ffmpeg），`/api/ready` 为就绪探针
  （数据库 / ffmpeg / ffprobe / 数据目录可写），失败返回 `503`。
- **ffmpeg 命令超时**（`VOICEDUB_FFMPEG_TIMEOUT`）：异常素材不会把 worker 永久卡住。
- **部署资产**：多阶段 `Dockerfile`（内置 ffmpeg、非 root、HEALTHCHECK）、`docker-compose.yml`、
  `.dockerignore`、`Makefile`、`.editorconfig`。
- **工程规范**：`pyproject.toml` 补全元数据与 ruff / pytest / coverage 配置，
  拆分 `requirements-dev.txt`，CI 增加 ruff 与覆盖率，新增 `SECURITY.md`、`docs/OPERATIONS.md`。
- **测试**：新增 10 条平台级用例，覆盖探针、request_id、安全响应头、错误契约、配置解析，
  以及两条针对上述配置 bug 的回归用例。

### 变更

- CORS 来源改为可配置（`CORS_ORIGINS`），并修正通配来源与凭证同时开启的不兼容配置。
- `/api/catalog` 增加 `version` 字段。
- 日志替代 `traceback.print_exc()`：任务失败有结构化事件，不再污染 stderr。
- 文档中不再出现面向特定场合的表述，全部改为部署与运维口径。

## [0.4.0] - 2026-01-18

首个可运行版本。

### 新增

- 三条渲染链路：多角色对白、视频配音（`atempo` 时长对齐）、有声书（章节清单）。
- 声音设计（中文描述 → pitch/speed/timbre 规则解析）、参数级音色克隆（过零率估基频）、音色试听。
- 确定性离线 TTS：基频 + 谐波 + 包络，同输入逐字节可复现，不依赖模型权重。
- 媒体层：WAV 读写、静音拼接、对白混音时间轴、时长伸缩、响度归一、视频探测/混流/封面。
- 任务队列：单 worker、步骤级进度、协作式取消、重试、启动崩溃恢复。
- 零依赖 Web 控制台、CLI 端到端脚本、HTTP 冒烟脚本。
