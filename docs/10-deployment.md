# 部署

## 服务

- `apps/web`: React/Vite 静态站点。
- `apps/api`: FastAPI。
- PostgreSQL 或本地 SQLite。
- `LocalMediaStore`，本轮没有 S3/MinIO 实现。
- API 进程内的租约式本地 Worker；数据库是任务真相源。
- 外部 LLM、图片和视频 Provider。
- 本地 FFmpeg。

## 数据库

PostgreSQL 使用 Alembic，任务运行时 revision 为 `4d5e6f708192`。SQLite 启动时执行有版本号的一次性迁移，任务结构变更前会在原数据库旁创建 `.bak` 备份。迁移不删除项目、任务、资产或媒体。

```env
VIDEO_GENERATION_CONCURRENCY=2
TASK_POLL_INTERVAL_SEC=0.5
TASK_LEASE_SEC=60
TASK_HEARTBEAT_SEC=15
TASK_SHUTDOWN_TIMEOUT_SEC=30
```

当前部署目标是单 API 进程。不应仅因 PostgreSQL 中有租约字段就宣称多实例生产就绪。

## Provider

运行环境只配置 `llm / image / video`。生产视频 Provider 应明确声明原生音频、参考输入、多参考、最大时长和分辨率能力。

仓库包含 Seedance、LTX 和 Wan 视频 Adapter。合同测试使用 Mock HTTP；未完成真实主链路验收前，不得将“Adapter 已注册”表述为“真实生成已验证”。

## 验收层级

必须分别说明：

- 定向测试通过。
- 全量测试通过。
- 只读连通性通过。
- 真实生成通过。

本轮不把 Mock、服务启动或健康检查描述为真实图片/视频生成成功。
