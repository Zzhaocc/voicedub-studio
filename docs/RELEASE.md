# 版本与发布

本文定义 VoiceDub Studio 的版本号规则、分支策略、发布检查清单与回滚方式。

## 版本号规则

采用语义化版本 `MAJOR.MINOR.PATCH`：

| 位 | 递增条件 | 例子 |
|---|---|---|
| `MAJOR` | 破坏性变更：接口契约、配置项语义、数据库结构、产物格式 | 改 SRT 时间轴口径、改任务状态机 |
| `MINOR` | 向下兼容的新能力 | 新增生产模式、新增工具箱能力 |
| `PATCH` | 向下兼容的修复 | 修 ffmpeg 超时处理、修字幕漂移 |

预发布用 `-rc.N` 后缀；`main` 上的正式版本不带后缀。

## 分支与合并

- `main` 始终可发布，任何提交都必须能通过 CI；
- 功能与修复走 `feat/*`、`fix/*` 短分支，通过 PR 合入（squash merge）；
- 每个 PR 必须满足：[PR 模板](../.github/PULL_REQUEST_TEMPLATE.md) 的自检项、CI 全绿、至少一次 review；
- 面向用户的变更必须在 `CHANGELOG.md` 的 `Unreleased` 段落有对应条目。

## 发布检查清单

- [ ] `CHANGELOG.md`：把 `Unreleased` 收敛为版本号 + 日期
- [ ] 版本号同步：`pyproject.toml`、`app/__init__.py`、镜像 tag（`Dockerfile` / `docker-compose.yml` / `Makefile`）
- [ ] `python -m ruff check app tests scripts` 通过
- [ ] `python -m pytest` 全绿（媒体测试会真实编码 MP4）
- [ ] 构建镜像并按 [运维手册](OPERATIONS.md) 完成一次冒烟
- [ ] 若改动时间轴或时长对齐逻辑：跑一次 `scripts/demo.py` 并核对 SRT 与音频时长一致
- [ ] 数据库结构有变更时：备份 + 回滚演练已完成
- [ ] 发布说明写明：兼容性、配置项变化、升级步骤

## 兼容性承诺

- `PATCH`：不改接口路径、字段、错误码与配置项语义；
- `MINOR`：只新增；废弃项先标记 `deprecated`，跨 `MAJOR` 才移除；
- 产物格式（WAV 24kHz 单声道 / SRT / H.264+AAC MP4）视为对外契约，变更走 `MAJOR`；
- 环境变量只增不改语义；新增项必须同时写进 `.env.example` 与运维手册。

## 回滚

1. 镜像回滚到上一个 tag；
2. 数据库结构不兼容时用发布前备份恢复（见运维手册「数据与备份」）；
3. 回滚后确认 `/api/ready` 与指标页恢复，再排查原因。

启动时会把上次未结束的 `queued` / `running` 任务标记为 failed（可重试），
所以回滚不会留下永远转圈的任务。
