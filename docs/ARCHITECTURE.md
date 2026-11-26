# 架构与设计决策

## 1. 目标与非目标

**目标**

1. 把「声音设计 → 合成 → 混音 → 时长对齐 → 混流」做成一条可观测的管线；
2. 每条产物都能被播放器打开（WAV / SRT / MP4），不是 JSON 占位；
3. 音色、任务、产物都有明确边界，换真实 TTS 只改一个文件；
4. 部署形态可容器化：一个数据卷、一个对外端口、两个探针。

**非目标**

- 不做桌面端打包、不做模型权重下载、不做语音翻译与口型同步；
- 音色克隆是**参数级**（基频 / 音色倾向），不是神经网络音色嵌入 —— 文档里明确标注。

## 2. 分层

```text
接入层  FastAPI 路由 + 静态控制台 + 产物下载
       中间件：CORS → X-Request-ID → 安全响应头
编排层  JobRunner：单/多 worker、步骤、进度、取消、重试、恢复、超时
业务层  三条渲染管线（对话/配音/有声书）+ 音色服务
媒体层  TTS、混音、时长伸缩、响度归一、字幕、视频混流
支撑层  SQLAlchemy 模型、配置、结构化日志、文件存储
```

`pipeline` 只调用媒体层的几个函数，不直接碰 ffmpeg；ffmpeg 调用集中在 `media/`，
可执行路径与超时由配置注入（`FFMPEG_BIN` / `VOICEDUB_FFMPEG_TIMEOUT`），所以换镜像不用改代码。

## 3. 数据模型

| 表 | 作用 | 关键字段 |
|---|---|---|
| `voices` | 音色 | kind(preset/designed/cloned/custom)、pitch、speed、timbre、analysis |
| `media` | 源素材 | kind(video/audio)、path、duration、size_bytes |
| `jobs` | 任务 | kind、status、progress、input、output、duration_ms、cancel_requested |
| `job_steps` | 步骤 | seq、name、status、detail、latency_ms |
| `assets` | 产物 | kind(audio/srt/video/chapters)、path、mime、size、duration |

`Job` 与 `JobStep` / `Asset` 是级联关系；删除任务时同时整目录清理 `media/<job_id>/`。
SQLite 已开启 WAL 与 `foreign_keys`，前端轮询不会锁库，也不会留下孤儿行。

## 4. 三条管线

### 对白

```text
parse_dialogue → 逐句 TTS（按角色选音色）→ mix_dialogue（句间静音，返回 timeline）
→ 按 timeline 写 SRT（带 [角色] 前缀）→ audio + srt
```

### 视频配音

```text
probe(源视频) → 切句 TTS → concat → fit_duration(目标=视频时长)
→ replace_audio(-c:v copy) → SRT 按缩放系数重算 → video + audio + srt + original.wav
```

**时长对齐**是这条链路的核心：`factor = 合成时长 / 视频时长`，
用 `atempo` 级联（单级 0.5–2.0）实现任意倍率；视频轨直接 copy，所以一条 8 秒视频约 0.5 秒完成。

### 有声书

```text
章节 → 逐章切句合成 → 章内 0.25s / 章间 0.8s → 全书 WAV
→ 章节起止时间（chapters.json）+ 逐句 SRT
```

章节起止时间在拼接前按实际音频时长累加，不靠事后估算。

## 5. 任务队列

- `ThreadPoolExecutor`：默认 1 个 worker，对应单机顺序执行，避免多个 ffmpeg 抢 CPU；
- 取消：每句合成前检查标记，1 秒内生效；排队中直接标 `cancelled`；
  结束后从取消集合移除，长时间运行不会累积内存；
- 重试：复制任务生成新 job，保留原任务与它的产物；
- 恢复：启动时把 `queued/running` 标 `failed`，避免前端永远转圈；
- 超时：单条 ffmpeg 命令有超时上限，异常素材不会把 worker 永久卡住。

## 6. 音色

| 类型 | 来源 | 参数 |
|---|---|---|
| preset | 内置 4 个 | 手工调好的 pitch/speed/timbre |
| designed | 中文描述 | 规则解析器：关键词 → pitch/speed/timbre 增量 |
| cloned | 参考音频 | 过零率估基频 → pitch，ZCR 判音色倾向 |
| custom | 手动填参 | 直接指定 pitch/speed/timbre |

声音设计用规则而不是模型：**可解释、可测试、可复现**，每个参数都能对应到具体关键词。
接真实模型时保持 `design_from_text` 的返回结构即可，上层与 API 都不用改。

## 7. 可观测

**日志**（`app/logging.py`）

- 两种格式：`text`（本地阅读）/ `json`（Loki / ELK / CloudWatch 采集）；
- 每条日志自动带 `request_id`，与响应头 `X-Request-ID` 一致，报错可直接定位；
- 关键事件：`startup` / `request` / `job_submitted` / `job_started` / `job_succeeded` /
  `job_cancelled` / `job_failed`；
- 访问日志由中间件统一输出（`method`、`path`、`status`、`duration_ms`），不与 uvicorn 重复；
- stdout 关闭时写日志失败会被静默丢弃，不会反过来打断业务线程。

**指标**（`/api/metrics`）

- 任务：总数、成功/失败/取消、成功率、平均耗时、按类型分布；
- 音频：产物条数、总时长、总字节，视频数量；
- 库：音色数、素材数、克隆音色数。

**任务步骤**（`/api/jobs/{id}`）

每一步都有 `seq` / `name` / `status` / `detail` / `latency_ms`，
失败时 `detail` 保留出错原因，`job.error` 保留 ffmpeg stderr 尾部。

## 8. 安全与部署边界

| 关注点 | 设计 |
|---|---|
| 身份认证 | 内置接口不带鉴权，默认监听 `127.0.0.1`；对外部署须置于网关 / Ingress 后并接入 OIDC 或 API Key |
| 上传 | 只收 base64，上限 60MB；落盘文件名由服务端生成，不使用用户文件名 |
| 路径校验 | 产物下载前校验路径必须落在 `VOICEDUB_DATA_DIR` 内 |
| 命令注入 | ffmpeg 参数全部由代码拼装，不接受用户可控的原始参数 |
| 请求追踪 | 支持透传上游 `X-Request-ID`，缺失时生成，回写响应头并注入所有日志 |
| 错误回显 | 未预期异常只返回 `request_id`，不回显堆栈与内部路径 |
| 响应头 | `nosniff` / `SAMEORIGIN` / `no-referrer` / 最小化 `Permissions-Policy` |
| 数据 | 示例素材是程序生成的占位视频，不含真实人物音频 |

部署形态与运维细节见 [运维手册](OPERATIONS.md)，安全边界见 [SECURITY.md](../SECURITY.md)。

## 9. 关键取舍

| 决策 | 选择 | 理由 | 代价 |
|---|---|---|---|
| 形态 | 服务端 | 队列、批量、多端接入 | 没有桌面端体验 |
| 克隆 | 参数级分析 | 无权重、可解释 | 不是真音色克隆 |
| 视频 | 只换音轨 | 不依赖唇形模型 | 口型对不上 |
| TTS | 确定性谐波合成 | 离线、可复现、CI 不需要模型 | 不是自然人声 |
| 拼接 | numpy 直接读写 WAV | 比 ffmpeg concat 更可控、能拿到精确时间轴 | 只支持 16-bit PCM |
| 帧率/画质 | 源视频 copy | 快、无损 | 分辨率不能改 |
| 存储 | SQLite + 本地文件 | 零依赖 | 生产要换对象存储 |
| 队列 | 进程内线程池 | 部署简单、够单机用 | 不能多副本；要扩容得换外部队列 |

## 10. 失败模式

| 场景 | 现状 |
|---|---|
| 剧本为空 / 章节缺正文 | 入队前校验，API 422 |
| 素材或音色不存在 | 404 / 409 |
| 上传文件过大 | 413（base64 上限 60MB） |
| 声明的音色 id 不存在 | 回落到默认音色，任务照常执行 |
| 视频无音轨 | `probe` 后仍可换音轨（`-map 1:a` 来自新音频） |
| ffmpeg 失败 | 抛 `RuntimeError`，任务 `failed`，stderr 入 `job.error` |
| ffmpeg 卡死 | 超时（默认 300s）后置 `failed`，worker 释放 |
| 磁盘不可写 | `/api/ready` 返回 503，任务失败并记录原因 |
| 任务卡死 / 进程重启 | 启动恢复标 `failed`，可重试 |
| 产物被删 | 下载返回 410 |

## 11. 实测数据（本机，8 秒占位视频）

| 指标 | 数值 |
|---|---|
| 多角色对白（3 句 / 5.7s） | ~0.2 s |
| 视频配音（8s 视频 → 7.97s 成片） | ~0.6 s，倍率 0.762 |
| 有声书（2 章 / 9.7s） | ~0.2 s |
| 测试 | 35 passed（媒体 7 + 管线 11 + API 7 + 平台 10） |
| 产物 | WAV 24kHz · SRT · MP4(H.264/AAC) |

> 数字随机器、ffmpeg 版本与文案长度波动，可用 `python scripts/demo.py` 在本机复现。
