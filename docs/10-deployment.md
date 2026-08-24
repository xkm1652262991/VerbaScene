# 部署

## 服务

- `apps/web`: React/Vite 静态站点。
- `apps/api`: FastAPI。
- PostgreSQL 或本地 SQLite。
- 本地文件存储，后续可替换为对象存储。
- 可选 Redis/Worker 运行长视频任务。
- 外部 LLM、图片和视频 Provider。
- 本地 FFmpeg。

## 数据库

PostgreSQL 使用 Alembic。SQLite 启动时执行有版本号的一次性迁移，删旧字段前备份数据库。

## Provider

运行环境只配置 `llm / image / video`。生产视频 Provider 应明确声明原生音频、参考输入、多参考、最大时长和分辨率能力。

当前部署不包含 Seedream/Seedance Adapter；未完成真实适配前使用 Mock 验证合同。

## 验收层级

必须分别说明：

- 定向测试通过。
- 全量测试通过。
- 只读连通性通过。
- 真实生成通过。

本轮不把 Mock、服务启动或健康检查描述为真实图片/视频生成成功。
