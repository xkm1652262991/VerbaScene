# API 设计

## 项目与剧本

```text
POST  /api/projects
PUT   /api/projects/{project_id}/chapter
GET   /api/projects/{project_id}/workbench
POST  /api/projects/{project_id}/script/generate  -> 202 GenerationTask
PATCH /api/scripts/{script_id}
GET   /api/projects/{project_id}/dialogues
PUT   /api/projects/{project_id}/dialogues
```

项目创建支持 `ai_brief` 和 `imported_script`。新项目默认使用
`16:9 + 854x480`，竖屏可选 `9:16 + 480x854`。`creative_settings`
不再包含 `audience_age` 或 `learning_objective`；Dialogue 保存接口要求英文文本，中文释义可空。

剧本生成是持久化后台任务。同一项目已有非终态剧本任务时，数据库唯一约束使重复提交返回 `409`。客户端通过 `GET /api/tasks/{task_id}` 轮询；成功后从 `result_payload.script_id` 或项目最新剧本接口读取结果。任务可以从已保存的蓝图、初稿或审稿检查点恢复，重启后不重复执行已经成功的文本模型阶段。提交结果不确定时不自动重提。

## 资产与片段

```text
POST /api/projects/{project_id}/entities/generate
POST /api/projects/{project_id}/reference-images/generate-candidates -> 202 GenerationTask
POST /api/projects/{project_id}/reference-images/generate-candidate  -> 202 GenerationTask
POST /api/asset-candidates/{candidate_id}/promote
POST /api/assets/{asset_id}/select
POST /api/projects/{project_id}/shots/generate  -> 202 GenerationTask
PATCH /api/shots/{shot_id}
PUT   /api/shots/{shot_id}/reference-assets
GET   /api/shots/{shot_id}/video-prompt-preview
POST  /api/shots/{shot_id}/compile-video-prompt
POST  /api/shots/{shot_id}/image/generate-candidate -> 202 GenerationTask
POST  /api/shots/{shot_id}/video/generate-candidate
POST  /api/projects/{project_id}/shot-videos/generate-candidates
POST  /api/assets/{asset_id}/regenerate-video-candidate
POST  /api/assets/{asset_id}/regenerate-candidate -> 202 GenerationTask
POST  /api/asset-candidates/{candidate_id}/regenerate -> 202 GenerationTask
POST  /api/assets/{asset_id}/extract-frame -> 202 GenerationTask
```

图片生成接口均接受可选 `Idempotency-Key`，只创建后台任务。模型输出在任务成功后写入候选，采用与版本选择不改变页面可进入性。批量参考图和片段图生成返回 `waiting_children` 父任务，每个目标创建独立子任务；任一目标已有活动任务时整批原子拒绝。单目标任务冻结 Provider、模型、Prompt、引用素材、版本号和参数，但不持久化密钥。

`PUT /api/shots/{shot_id}/reference-assets` 接收按优先级排列的 `asset_ids`。服务只接受同项目、已经采用且可用的角色/场景/道具图片；当前采用版本和状态为 `approved` 的历史版本都可以精确绑定，未采用候选不可绑定；最多一个场景。绑定结果写入 `shot_card.reference_asset_ids`，同时同步 `scene_id`、`character_ids` 和 `prop_ids`，并仅把最终 Prompt 标记为可能过期。该绑定同时服务首帧和视频：首帧生成可以消费道具引用，视频生成会过滤道具图。

视频 Prompt 预览除 `reference_tokens` 外返回结构化 `reference_assets`，内容必须与
Provider 实际 `content` 顺序一致。应用层最多包含 20 个角色和 1 个场景，Provider 可以按自身合同执行更严格的上限；只有
`PATCH /api/shots/{shot_id}/video-reference` 明确设置 `asset_id` 时才增加片段首帧，
此时首帧为“图片1”。每项包含资产 ID、显示 token、Seedance 素材序号 `media_label`、实体
类型、实体 ID、状态变体、引用角色和 URI。道具不出现在视频预览引用中。Provider
解析优先使用显式绑定的精确资产版本；旧片段没有显式绑定时继续按实体关系和当前
采用版本自动解析，但仍遵守应用层和当前 Provider 中更严格的数量上限。

批量参考图生成默认只处理角色和场景。单独的道具图不属于标准生产链路；道具实体及
其状态仍保存在剧本和片段数据中。

分镜生成是使用 `resource_key=project:{project_id}:shots` 的持久化后台任务，支持 `Idempotency-Key`、取消和人工重试。任务冻结剧本、Dialogue、实体设定、采用资产元数据与制作规则，执行资产观察、草案、Reflection 和最多一次定点 Patch；成功后原子切换当前 Shot 批次，失败或取消不影响原批次。`result_payload.director_report` 是前端报告合同，Provider 原始响应不属于公共界面合同。

分镜导演只负责语义分段、内部镜头节拍和创意性的分镜图主体 Prompt，不要求 LLM 预测 Shot 或 beat 的秒数，也不选择最终媒体版本或提交视频。每个 Shot 在 `shot_card.beats` 中包含有序的内部镜头，`shot_card.segment_plan.duration_mode` 默认为 `provider_auto`。历史数据里的 `beats[].duration_sec` 继续可读，但不参与 Prompt 编译或片段时长计算。

`POST /api/shots/{shot_id}/video/generate-candidate` 返回 `202 + GenerationTask`，并支持 `duration_mode=provider_auto|fixed`。Seedance 2.0 默认使用 `provider_auto`，Adapter 向 Provider 发送 `duration=-1`；返回的真实时长在候选版本被采用后写入现有的 `Shot.duration_sec` 和 `segment_plan.actual_duration_sec`，供时间线、计费展示与合成使用。`fixed` 只是人工高级覆盖，必须同时提交 `duration_sec`。兼容旧客户端时，只提交 `duration_sec` 视为 `fixed`。

项目批次端点返回一个 `waiting_children` 父任务，每个当前 Shot 创建独立子任务。
任一 Shot 已有活动视频任务时整批 `409`，响应列出冲突 Shot，不会产生部分批次。

固定时长提交前必须同时比较当前 Provider 的 `min_duration_sec` 和 `max_duration_sec`。超出范围返回 `409`，服务不得调用 Provider、截断时长或静默拆分；Provider 不支持智能时长时，显式提交 `provider_auto` 同样返回 `409`。

## 任务 API（破坏性版本）

```text
GET  /api/tasks?parent_task_id=&task_type=&resource_key=
GET  /api/tasks/{task_id}
POST /api/tasks/{task_id}/cancel  -> 202 GenerationTask
POST /api/tasks/{task_id}/retry   -> 202 GenerationTask
```

生成端点可接收 `Idempotency-Key`；同一项目、任务类型和 key 返回原任务。
人工重试只允许 `failed / cancelled`，创建新任务并复用原输入快照。
`DELETE /api/tasks/{task_id}/queue` 已删除。本版需要后续单独适配前端。

## 导出

```text
POST /api/projects/{project_id}/compose
{
  "subtitle_mode": "none"
}
```

返回 `202 + GenerationTask`，支持 `Idempotency-Key` 和 `none | en | bilingual`。任务创建时冻结当前采用的视频版本与 Dialogue 时间线；成功后从 `result_payload.export_id / asset_id` 获取结果。排队期间上游修改不会改变该次导出。

## 审计

```text
GET  /api/projects/{project_id}/workflow/runs
POST /api/workflow/runs/{run_id}/cancel
```

运行记录是审计和取消入口，不是阶段门禁。不存在 Workflow Gate、阶段确认、声音生成或独立字幕生成接口。

## Provider

Provider 配置只接受 `llm | image | video`。读取结果包含公开视频能力字段。

`seedance2_api` 使用现有 Provider 配置接口：

```text
PUT /api/providers/configs/video
{
  "provider_name": "seedance2_api",
  "model_name": "doubao-seedance-2-0-260128",
  "base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "api_key_mode": "environment",
  "api_key_ref": "ARK_API_KEY",
  "default_params": {
    "resolution": "480p",
    "generate_audio": true,
    "watermark": false
  }
}
```

也可以使用 `api_key_mode=direct` 和写入专用的 `api_key` 字段。该字段只写入本机
私有密钥存储，响应和后续读取均不回显。项目画幅与片段时长优先于 Provider
默认值；生成时分别写入 `ratio` 和 `duration`。
