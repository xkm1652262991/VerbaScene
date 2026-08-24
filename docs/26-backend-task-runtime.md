# 后端任务运行时

本文定义 VerbaScene 本地后端的统一任务合同。它取代剧本与视频各自维护的内存队列，
但不把单机工作台描述为分布式任务系统。

## 1. 目标与范围

- 数据库中的 `GenerationTask` 是任务状态真相源；内存队列只负责唤醒 Worker。
- 本轮迁移剧本生成、单镜头视频、视频重生成和项目级视频批次。
- 图片生成和 FFmpeg 导出仍使用同步 API。
- 运行环境仍为单 API 进程、本地线程、SQLite 或 PostgreSQL、本地媒体目录。
- 不引入 Celery、Redis 队列、LangGraph、对象存储、鉴权或多租户。

## 2. 状态机

```text
queued
  -> running
  -> waiting_provider
  -> waiting_children
  -> cancelling
  -> succeeded | failed | cancelled
```

- `queued`：输入快照已经持久化，等待领取。
- `running`：Worker 持有有效租约并执行本地步骤或 Provider 提交。
- `waiting_provider`：远端任务 ID 已落库，后续只能轮询，不能重新提交。
- `waiting_children`：批次父任务等待子任务进入终态。
- `cancelling`：运行中任务已收到取消请求，正在执行尽力取消。
- 终态任务必须清空 `active_dedupe_key`、租约和下一次执行时间。

合法迁移由任务仓储统一校验，业务 Handler 不直接写任意状态字符串。

## 3. 领取、租约与恢复

Worker 使用条件更新领取到期任务：只有 `queued` 或已到轮询时间的
`waiting_provider`，且租约为空或过期，才能被设置为 `running` 并写入
`lease_owner / lease_expires_at / heartbeat_at`。

- 剧本并发固定为 1。
- 视频并发读取 `VIDEO_GENERATION_CONCURRENCY`，上限为 2。
- Worker 执行期间续租；Provider 调用和媒体下载期间不持有数据库事务。
- 服务启动只回收过期租约，不把所有 `running` 任务无条件重置。
- 服务关闭停止领取新任务；未完成任务在租约过期后可恢复。

剧本 Provider 调用在提交前先保存 `inflight_phase`，响应返回后再保存阶段记录。
如果重启时只存在 `inflight_phase` 而没有响应，结果必须是
`provider_submission_uncertain`，不得重复调用该阶段。

## 4. 幂等、资源去重与重试

- `resource_key` 描述业务资源，例如 `project:{id}:script` 或 `shot:{id}:video`。
- 非终态任务同时持有相同值的 `active_dedupe_key`；该列使用唯一约束。
- 生成端点接受可选 `Idempotency-Key`，同项目、任务类型和 key 返回原任务。
- 自动重试最多一次，只允许 `retryable=true` 且
  `submission_state=not_submitted` 的失败。
- `submission_state=accepted` 必须从 `provider_task_id` 恢复轮询。
- `submission_state=unknown` 以 `provider_submission_uncertain` 结束，禁止自动重提。
- 人工重试创建新任务并写入 `retry_of_task_id`；批次子任务继续保留原
  `parent_task_id`。新任务复用原输入快照；使用最新业务数据时必须重新调用生成端点。

## 5. 取消

- 排队任务立即进入 `cancelled`。
- 运行或等待 Provider 的任务进入 `cancelling`。
- Provider 支持取消且已有远端任务 ID 时调用 `cancel()`。
- Provider 不支持取消时，在下一检查点停止本地处理并忽略迟到结果。
- 取消批次父任务会向所有非终态子任务传播取消请求。

## 6. 视频 Provider 合同

异步视频 Adapter 的 `submit()` 只能提交任务并立即返回：

- `status=queued|running`
- `execution_mode=async`
- 非空 `provider_task_id`
- 建议的 `poll_after_sec`

Worker 必须先保存远端任务 ID，再进入轮询。`poll()` 在成功时返回可下载的媒体描述，
Provider 负责使用自身认证获取临时媒体，应用层再通过 `MediaStore` 落入项目目录。

`ProviderError.submission_state` 只能是：

- `not_submitted`
- `accepted`
- `unknown`

## 7. 项目级视频批次

批次端点在一个事务内创建父任务和每个当前镜头的子任务：

- 任一镜头已有活动视频任务时，整批返回 `409`，不创建部分任务。
- 子任务独立领取、取消、失败和人工重试。
- 父任务进度由子任务终态数量汇总。
- 全部子任务成功时父任务成功；存在失败或取消时，待全部子任务终止后父任务失败。
- 父任务结果记录各状态数量和失败子任务 ID。

## 8. 破坏性 API 变更

- 剧本、单镜头视频、视频重生成和项目视频批次统一返回 `202 + GenerationTask`。
- 新增 `POST /api/tasks/{task_id}/cancel`。
- 新增 `POST /api/tasks/{task_id}/retry`。
- 删除 `DELETE /api/tasks/{task_id}/queue`。
- 任务列表增加 `parent_task_id / task_type / resource_key` 过滤。
- 当前前端不在本轮适配范围内。

## 9. 数据迁移

PostgreSQL 使用新 Alembic revision。SQLite 继续使用本地增量迁移，并在变更已有数据库前
创建备份。旧 Alembic 迁移含 PostgreSQL 专用类型，本轮不改写或重放到本地 SQLite。

- 迁移前已经 `running` 的旧视频任务没有可靠的远程任务 ID，会以
  `provider_submission_uncertain` 失败保留，避免重复计费。
- 迁移前处于 `queued` 的旧视频任务在首次领取时一次性补齐并冻结输入。
- 如果历史竞态留下同资源的多个活动任务，迁移保留最早任务，其余行以
  `task_dedupe_migration_conflict` 失败保留，然后再创建唯一约束。

## 10. 模块边界

- `app/scripts`：剧本任务创建与 Handler，质量流水线保留在 Agent 层。
- `app/production/video_request_compiler.py`：生成前冻结请求。
- `app/production/video_provider_executor.py`：Provider 请求重建、结果获取和临时媒体入库。
- `app/production/video_candidate_persistence.py`：候选版本和采用状态持久化。
- `app/production/video_batch_coordinator.py`：父子任务汇总。
- `app/platform/tasks` 和 `app/platform/media`：不依赖具体业务页面的共享设施。
