# VerbaScene API

动画英语短剧工作台的 FastAPI 后端。当前实现是模块化单体，负责剧本、实体、资产、Shot、任务、Provider 配置与 FFmpeg 导出。

## 当前运行模型

```text
FastAPI
  → 领域服务
  → PostgreSQL（推荐）或 SQLite（开发后备）
  → LocalMediaStore
  → 数据库租约任务运行时
  → LLM / Image / Video Provider Adapter
  → FFmpeg 导出
```

剧本、分镜导演、图片候选、视频候选、视频抽帧和成片导出共用持久化
`GenerationTask`。数据库是任务真相源，本地线程只负责领取、续租和唤醒。
剧本从最后成功检查点继续；已受理的异步 Provider 任务只恢复轮询，不会重复提交。

当前没有 Celery、Redis 队列、LangGraph 或多实例 Worker。运行时仍以单 API
进程和本地文件系统为目标。

## 本地开发

从仓库根目录执行：

```bash
cp .env.example apps/api/.env
python -m venv apps/api/.venv
source apps/api/.venv/bin/activate
pip install -r apps/api/requirements.txt
cd apps/api
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

原生开发仍可使用 Mock Provider 和 `../../storage/local/content.sqlite3`，不会自动调用付费生成服务。

检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/providers
```

## 持久化

本地模式：

```env
PERSISTENCE_MODE=local
DATABASE_URL=
LOCAL_DATA_DIR=../../storage/local
LOCAL_DATABASE_FILENAME=content.sqlite3
```

共享数据库模式：

```env
PERSISTENCE_MODE=database
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/animation_drama
DATABASE_SCHEMA=verbascene_app
```

PostgreSQL 模式启动前执行：

```bash
alembic upgrade head
```

`DATABASE_SCHEMA` 可留空并使用默认 `search_path`；指定时只允许字母、数字和下划线。
Alembic 在线迁移会创建该 schema，并把版本表与业务表写入其中。将现有 SQLite 数据迁往
空 PostgreSQL schema 时使用：

```bash
.venv/bin/python scripts/copy_local_to_database.py
```

复制工具会校验目标为空和各表行数，不会覆盖或合并旧 PostgreSQL 数据。

不要对本地 SQLite 文件运行 PostgreSQL Alembic 历史。

SQLite 任务结构升级前会在数据库同目录生成
`content.sqlite3.pre-20260824_unified_task_runtime_v1-*.bak`。迁移保留项目、任务和媒体；
旧版运行中视频任务因无法确定 Provider 是否已受理，会以
`provider_submission_uncertain` 失败保留，不自动重提。

## 本地任务运行时

```env
IMAGE_GENERATION_CONCURRENCY=2
VIDEO_GENERATION_CONCURRENCY=2
TASK_POLL_INTERVAL_SEC=0.5
TASK_LEASE_SEC=60
TASK_HEARTBEAT_SEC=15
TASK_SHUTDOWN_TIMEOUT_SEC=30
```

文本 Worker 固定为 1；图片与视频 Worker 各最多为 2；抽帧和导出共用单并发媒体
Worker。关闭时先停止领取新任务，再等待当前 Handler；未完成工作可通过过期租约恢复。
详细状态机和取消/重试语义见
[`docs/26-backend-task-runtime.md`](../../docs/26-backend-task-runtime.md)。

## Provider

运行槽位为 `llm`、`image` 和 `video`。内置目录包含 Mock、OpenAI-compatible、DashScope、Gemini、ComfyUI、Seedance、LTX、MiniMax H3、Wan 等 Adapter；注册成功不代表已经配置或完成真实生成验收。

```text
GET    /api/providers
GET    /api/providers/configs
PUT    /api/providers/configs/{provider_type}
DELETE /api/providers/configs/{provider_type}
POST   /api/providers/test
```

直接输入的 API Key 保存在 `PROVIDER_SECRET_FILE` 指定的本机文件中，只返回是否已配置，不通过读取接口回传明文。不要提交 `.env`、密钥、内网地址或运行时 secret 文件。

## OpenAPI 合同

前后端合同位于 `../../docs/api/openapi.json`：

> 当前生成合同为异步版本：剧本、分镜、图片、视频、抽帧和导出端点返回
> `202 + GenerationTask`，并通过 `POST /api/tasks/{id}/cancel|retry` 管理任务。前端已同步适配该合同。

```bash
.venv/bin/python scripts/export_openapi.py
.venv/bin/python scripts/check_openapi_contract.py
```

修改路由、Schema、分页或错误结构后，需要重新导出并检查。

## 定向测试

```bash
.venv/bin/python -m unittest \
  tests.test_animation_english_workflow \
  tests.test_export_service
```

完整测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

普通测试默认不进行真实图片或视频生成。自动化测试、只读连通、真实生成和完整成片验收应分别报告。

## 当前边界

- 本地租约运行时只以单 API 进程为部署目标，尚未宣称多实例就绪。
- 运行中的外部视频请求不能保证被立即取消。
- 当前没有用户鉴权、角色权限和受控 `/storage` 访问。
- SQLite 适合单机联调，不适合多人并发写入。
- 真实 Provider 的能力声明必须通过实际请求验收，不能只依据 descriptor。

更多信息见 [当前架构](../../docs/21-current-architecture.md) 和 [Demo 与验证证据](../../docs/25-demo-evidence.md)。
