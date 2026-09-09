# 部署

## Docker Compose 本地一键启动

Docker 默认部署形态为单 API 进程、PostgreSQL 17、本地媒体、进程内 Worker，
以及一个负责静态站点和同源反向代理的 Nginx Web 容器。PostgreSQL 保存业务、
Agent 检查点和任务租约；Redis 不在当前运行链路中。

```bash
./docker/up.sh
```

脚本先使用标准 `docker build` 构建两个本地镜像，再执行
`docker compose up --detach --no-build`，用于规避部分 Docker Desktop/Buildx
版本在非 ASCII 仓库路径下创建 Compose Bake 会话失败的问题。在 ASCII 路径下也可
直接运行 `docker compose up --build -d`。

启动后访问：

- Web：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

默认使用 Mock Provider，不产生付费生成请求。Compose 依次读取仓库中的
`.env.example` 和可选的根目录 `.env`；真实 Provider 密钥只能放入未提交的
`.env`，或者通过模型管理页写入本地运行时密钥文件。

PostgreSQL 使用 Compose 命名卷，媒体和导出继续绑定到仓库的 `storage/`，Provider
运行时密钥绑定到 `apps/api/.runtime/`。`docker compose down` 不删除数据库或媒体；
只有显式执行 `docker compose down --volumes` 才会删除 Compose 数据库卷。

Web 容器将 `/api`、`/health`、`/storage` 和 API 文档路径代理到 API 容器，前端
不需要写死宿主机地址。端口和绑定地址可以在根目录 `.env` 中覆盖：

```env
VERBASCENE_BIND_ADDRESS=127.0.0.1
VERBASCENE_WEB_PORT=5173
VERBASCENE_API_PORT=8000
```

项目当前没有鉴权，默认只监听回环地址。只有在增加访问控制并确认密钥、CORS
和反向代理策略后，才应将 `VERBASCENE_BIND_ADDRESS` 改为 `0.0.0.0`。

连接运行在宿主机上的 LTX、Wan 或其他本地 Provider 时，容器内不能继续使用
`127.0.0.1`，应将对应 Base URL 改成 `http://host.docker.internal:<端口>`；
Compose 已为 Linux 添加对应的 host-gateway 映射。

常用维护命令：

```bash
docker compose ps
docker compose logs -f postgres api web
docker compose down
```

## 服务

- `apps/web`: React/Vite 静态站点。
- `apps/api`: FastAPI。
- PostgreSQL（推荐）或本地 SQLite（零依赖开发模式）。
- `LocalMediaStore`，本轮没有 S3/MinIO 实现。
- API 进程内的租约式本地 Worker；数据库是任务真相源。
- 外部 LLM、图片和视频 Provider。
- 本地 FFmpeg。

## 数据库

PostgreSQL 使用 Alembic，当前 revision 为 `6f708192a3b4`。API 启动前必须完成
`alembic upgrade head`。可选 `DATABASE_SCHEMA` 用于在同一数据库中隔离 VerbaScene
表；schema 名只允许字母、数字和下划线。SQLite 启动时继续执行有版本号的一次性迁移，
任务结构变更前会在原数据库旁创建 `.bak` 备份。

```env
PERSISTENCE_MODE=database
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/verbascene
DATABASE_SCHEMA=verbascene_app
```

已有 SQLite 数据切换前必须同时备份 SQLite 文件和 PostgreSQL，再在空 schema 中执行
迁移与复制。复制工具拒绝写入非空目标，不会合并或覆盖已有记录：

```bash
cd apps/api
PERSISTENCE_MODE=database .venv/bin/alembic upgrade head
.venv/bin/python scripts/copy_local_to_database.py
```

本地零依赖模式仍可显式配置 `PERSISTENCE_MODE=local`；它用于开发和演示，不是推荐的
持久化部署形态。

```env
SCRIPT_GENERATION_CONCURRENCY=2
IMAGE_GENERATION_CONCURRENCY=2
VIDEO_GENERATION_CONCURRENCY=2
LLM_STREAM_PROGRESS_INTERVAL_SEC=2
TASK_POLL_INTERVAL_SEC=0.5
TASK_LEASE_SEC=60
TASK_HEARTBEAT_SEC=15
TASK_SHUTDOWN_TIMEOUT_SEC=30
```

剧本与分镜共用文本通道，默认并发为 2、上限为 4；图片与视频通道并发最多为 2。
当 LLM Provider 支持 OpenAI-compatible SSE 时，文本任务每隔约
`LLM_STREAM_PROGRESS_INTERVAL_SEC` 秒持久化一次分片进度。
本地媒体通道同时承载视频截帧与成片导出，避免多个 FFmpeg 进程争抢机器资源。
成片容器同时安装 Noto Sans CJK，用于 Pillow 渲染中英双语字幕。自定义部署可指定：

```env
SUBTITLE_FONT_PATH=/absolute/path/to/NotoSansCJK-Regular.ttc
```

该路径必须在 API 进程或容器内可读，并覆盖字幕实际使用的字形。

成片字幕可选接入 OpenAI-compatible ASR。关闭时完全使用确定性时间轴；开启后 ASR 失败会
按 Dialogue 粒度回退，不阻断 FFmpeg 导出：

```env
ASR_PROVIDER=openai_compatible
ASR_API_KEY=
ASR_BASE_URL=http://asr-gateway.example/v1
ASR_MODEL=qwen3-asr
ASR_RESPONSE_FORMAT=auto
ASR_TIMEOUT_SEC=300
ASR_MIN_MATCH_SCORE=0.55
```

网关必须允许该 Key 访问 `ASR_MODEL`。word 或 segment 时间戳会被直接使用；只支持普通 JSON
纯文本时，系统通过 FFmpeg VAD 后逐段识别来建立近似词窗。已知网关只支持纯文本时应设置
`ASR_RESPONSE_FORMAT=json`，避免每次导出先探测一次不支持的 `verbose_json`；默认 `auto` 会探测并
在明确的 400 响应后降级。`ASR_PROVIDER=disabled` 为默认关闭状态。

当前部署目标是单 API 进程。不应仅因 PostgreSQL 中有租约字段就宣称多实例生产就绪。

## Provider

运行环境只配置 `llm / image / video`。生产视频 Provider 应明确声明原生音频、参考输入、多参考、最大时长和分辨率能力。

仓库包含 Seedance、LTX、MiniMax H3 和 Wan 视频 Adapter。合同测试使用 Mock HTTP；未完成真实主链路验收前，不得将“Adapter 已注册”表述为“真实生成已验证”。H3 网关的免 Key 模式只适用于有网络访问控制的内网，不能直接暴露到不受信网络。

## 验收层级

必须分别说明：

- 定向测试通过。
- 全量测试通过。
- 只读连通性通过。
- 真实生成通过。

本轮不把 Mock、服务启动或健康检查描述为真实图片/视频生成成功。
