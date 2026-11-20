# 架构与设计决策

## 1. 目标与非目标

**目标**

1. 把「声音设计 → 合成 → 混音 → 时长对齐 → 混流」做成一条可观测的管线；
2. 每条产物都能被播放器打开（WAV / SRT / MP4），不是 JSON 占位；
3. 音色、任务、产物都有明确边界，换真实 TTS 只改一个文件。

**非目标**

- 不做桌面端（参考项目是 Electron）、不做模型权重下载、不做语音翻译与口型同步；
- 音色克隆是**参数级**（基频/音色），不是神经网络音色嵌入 —— 文档里明确标注。

## 2. 分层

```text
接入层  FastAPI 路由 + 静态控制台 + 产物下载
编排层  JobRunner：单 worker、步骤、进度、取消、重试、恢复
业务层  三条渲染管线（对话/配音/有声书）+ 音色服务
媒体层  TTS、混音、时长伸缩、字幕、视频混流
支撑层  SQLAlchemy 模型、配置、文件存储
```

`pipeline` 只调用媒体层的几个纯函数，不直接碰 ffmpeg；ffmpeg 调用集中在 `media/`。

## 3. 数据模型

| 表 | 作用 | 关键字段 |
|---|---|---|
| `voices` | 音色 | kind(preset/designed/cloned)、pitch、speed、timbre、analysis |
| `media` | 源素材 | kind(video/audio)、path、duration |
| `jobs` | 任务 | kind、status、progress、input、output、duration_ms |
| `job_steps` | 步骤 | seq、name、status、detail、latency_ms |
| `assets` | 产物 | kind(audio/srt/video/chapters/cover)、path、size、duration |

## 4. 三条管线

### 对白

```text
parse_dialogue → 逐句 TTS（按角色选音色）→ mix_dialogue（0.25s 句间静音，返回 timeline）
→ 按 timeline 写 SRT（带 [角色] 前缀）→ audio + srt
```

### 视频配音

```text
probe(源视频) → 切句 TTS → concat → fit_duration(目标=视频时长)
→ replace_audio(-c:v copy) → SRT 按缩放系数重算 → video + audio + srt + original.wav
```

**时长对齐**是这条链路的核心：`factor = 合成时长 / 视频时长`，
用 `atempo` 级联（单级 0.5–2.0）实现任意倍率；视频轨直接 copy，所以一条 8 秒视频 ~0.5 秒完成。

### 有声书

```text
章节 → 逐章切句合成 → 章内 0.25s / 章间 0.8s → 全书 WAV
→ 章节起止时间（chapters.json）+ 逐句 SRT
```

章节起止时间在拼接前按实际音频时长累加，不靠事后估算。

## 5. 任务队列

- `ThreadPoolExecutor(max_workers=1)`：对应单卡/单机顺序执行，避免多个 ffmpeg 抢 CPU；
- 取消：每句合成前检查标记，1 秒内生效；排队中直接标 cancelled；
- 重试：复制任务生成新 job，保留原任务；
- 恢复：启动时把 `queued/running` 标 failed，避免前端永远转圈。

## 6. 音色

| 类型 | 来源 | 参数 |
|---|---|---|
| preset | 内置 4 个 | 手工调好的 pitch/speed/timbre |
| designed | 中文描述 | 规则解析器：关键词 → pitch/speed/timbre 增量 |
| cloned | 参考音频 | 过零率估基频 → pitch，ZCR 判音色倾向 |

声音设计用规则而不是模型：**可解释、可测试、可复现**，面试时能说清每个参数怎么来的。
接真实模型时保持 `design_from_text` 的返回结构即可。

## 7. 关键取舍

| 决策 | 选择 | 理由 | 代价 |
|---|---|---|---|
| 形态 | 服务端 | 队列、批量、多端接入 | 没有桌面端体验 |
| 克隆 | 参数级分析 | 无权重、可解释 | 不是真音色克隆 |
| 视频 | 只换音轨 | 不依赖唇形模型 | 口型对不上 |
| 拼接 | numpy 直接读写 WAV | 比 ffmpeg concat 更可控、能拿到精确时间轴 | 只支持 16-bit PCM |
| 帧率/画质 | 源视频 copy | 快、无损 | 分辨率不能改 |
| 存储 | SQLite + 本地文件 | 零依赖 | 生产要换对象存储 |

## 8. 失败模式

| 场景 | 现状 |
|---|---|
| 剧本为空 / 章节缺正文 | API 422 |
| 素材或音色不存在 | 404 / 409 |
| 视频无音轨 | `probe` 后仍可换音轨（-map 1:a 来自新音频） |
| ffmpeg 失败 | 抛 `RuntimeError`，任务 failed，stderr 入 job.error |
| 任务卡死 | 启动恢复标 failed，可重试 |
| 产物被删 | 下载返回 410 |

## 9. 实测数据（本机）

| 指标 | 数值 |
|---|---|
| 多角色对白（3 句 / 5.7s） | ~190 ms |
| 视频配音（8s 视频 → 7.97s 成片） | ~530 ms，倍率 0.762 |
| 有声书（2 章 / 9.7s） | ~100 ms |
| 测试 | 25 passed（媒体 7 + 管线 11 + API 7） |
| 产物 | WAV 24kHz · SRT · MP4(H.264/AAC) |
