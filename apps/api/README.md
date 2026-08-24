# VerbaScene API

动画英语短剧工作台的 FastAPI 后端。当前实现是模块化单体，负责剧本、实体、资产、Shot、任务、Provider 配置与 FFmpeg 导出。

## 当前运行模型

```text
FastAPI
  → 领域服务
  → SQLite（默认）或 PostgreSQL
  → 本地媒体存储
  → 进程内剧本/视频队列
  → LLM / Image / Video Provider Adapter
  → FFmpeg 导出
```

剧本生成和单镜头视频生成使用持久化 `GenerationTask` 与进程内线程队列。剧本阶段在每次成功模型调用后保存检查点，API 重启后可以继续；视频运行中断后会标记失败并要求重新排队。

当前没有 Celery、LangGraph 或多实例分布式队列。其他图片与 FFmpeg 操作仍可能同步执行。

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

默认配置使用 Mock Provider 和 `../../storage/local/content.sqlite3`，不会自动调用付费生成服务。

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
```

PostgreSQL 模式启动前执行：

```bash
alembic upgrade head
```

不要对本地 SQLite 文件运行 PostgreSQL Alembic 历史。

## Provider

运行槽位为 `llm`、`image` 和 `video`。内置目录包含 Mock、OpenAI-compatible、DashScope、Gemini、ComfyUI、Seedance、LTX、Wan 等 Adapter；注册成功不代表已经配置或完成真实生成验收。

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

- 单机线程队列不能协调多个 API 实例。
- 运行中的外部视频请求不能保证被立即取消。
- 当前没有用户鉴权、角色权限和受控 `/storage` 访问。
- SQLite 适合单机联调，不适合多人并发写入。
- 真实 Provider 的能力声明必须通过实际请求验收，不能只依据 descriptor。

更多信息见 [当前架构](../../docs/21-current-architecture.md) 和 [Demo 与验证证据](../../docs/25-demo-evidence.md)。
