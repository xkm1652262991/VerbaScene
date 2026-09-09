# 反思式分镜导演 Agent

## 1. 定位与边界

分镜导演 Agent 负责把一份冻结的剧本和资产上下文整理成可编辑的视频片段方案。这里的 `Shot` 是一次视频模型调用承载的连续叙事片段；`shot_card.beats` 是该片段内部按顺序发生的摄影镜头节拍。两者不能混为一层。Agent 按剧情事件为每个 Shot 规划一个总时长，但不为 beat 分配秒数或时间区间。

Agent 的终点是“可编辑的新 Shot 批次 + 规范化导演报告”。它可以发现资产缺口、提出镜头方案并做一次受限修订，但不能生成或采用资产、提交视频、删除媒体、修改资产卡，也不能替代后端决定引用版本、编译最终视频 Prompt 或切换数据库批次。

## 2. 冻结输入

创建 `shot_breakdown` 任务时一次性保存以下快照，排队后的上游修改不影响本任务：

- 当前剧本正文、结构化场次和 Dialogue；
- 当前角色、场景、道具设定与状态变体文本；
- 当前采用资产的版本、角色、URI 和媒体元数据；
- 项目视觉风格、画幅、分辨率、目标时长和分段规则；
- 草案与审稿 Agent 的 Provider、模型、系统 Prompt 和阶段温度。

新调用生成端点读取最新数据。人工重试复用原任务快照和已经成功的 checkpoint，避免“重试”在不知情时变成另一份创作输入。

## 3. 五类产物合同

### 3.1 `AssetReadinessReport`

固定包含：

- `planning_ready`：是否具备至少一个结构化角色和场景；
- `generation_ready`：默认参考资产路径所需的角色、场景采用图是否齐全；
- `inspection_level=metadata_only`；
- `missing_references`：建议补充的角色或场景参考图；
- `conflicts`：采用状态、URI、版本或本地文件解析冲突；
- `inspected_assets`：本次实际读取的采用版本元数据。

缺少结构化角色或场景时不创建任务。缺少参考图只令 `generation_ready=false`，不阻止规划和人工编辑。状态变体是文本事实，不要求独立状态图；道具也不默认要求参考图。

`AssetInspector` 是内部协议，当前实现为 `MetadataAssetInspector`。`VisualAssetInspector` 仅保留相同报告边界，未来接 VLM；本轮不读取像素，也不声称做过视觉审核。

### 3.2 `ShotDraft`

包含完整 `shots` 数组。每个 Shot 必须使用快照内的实体 ID 和 Dialogue ID，包含连续叙事、片段总时长、时长依据、2–4 个有序 beats、单张分镜图主体 Prompt，以及后续确定性编译所需的 Shot Card。

时长规划遵循事件预算，而不是平均切片：建立信息、短反应和简单动作使用较短片段；完整对白、动作转折、失败或情绪兑现获得更长片段。所有 Shot 的规划时长总和必须落在项目目标时长的允许浮动范围内。beat 只表达“先发生什么、接着什么、最后如何收束”，不得出现 `0.0-2.0 秒`、`第 3 秒`等内部时间码。

### 3.3 `ReflectionReport`

问题分类限定为：

```text
coverage / continuity / dialogue / cinematography / asset_feasibility / production
```

严重度限定为 `must_fix / editorial_note`。问题必须带稳定 `code`、说明、命中的 `shot_nos` 和修订建议。只有 `must_fix` 触发自动修订；编辑建议只进入报告。

### 3.4 `ShotPatch`

只允许 `replace / insert_after / remove` 三种操作。操作目标必须落在审稿明确点名的 Shot 范围；插入只能紧邻点名片段。Patch 不得引入未知实体或 Dialogue ID，不得让 Dialogue 重复绑定，也不得改变未点名 Shot。后端应用后统一重排 `shot_no`。

### 3.5 `ContractReport`

后端确定性校验实体引用、Dialogue 唯一绑定、片段总时长、整集时长预算、内部 beats、场次来源和必要结构。报告包含 `errors`、`warnings`、未解决问题和最终 `quality_gate`：

- `pass`：审稿可用且没有未解决的必须修复项；
- `needs_attention`：草案合同有效，但修订失败、Patch 被拒绝或仍有内容问题；
- `review_unavailable`：审稿调用或格式不可用，保留有效草案。

`quality_gate` 是提示，不是页面门禁。

## 4. 固定运行流程

```text
资产元数据观察
  -> storyboard_draft（0.4）
  -> 确定性草案合同校验
  -> storyboard_reflection（0.2）
  -> 有 must_fix 时 storyboard_patch（0.3，最多一次）
  -> 最终合同校验
  -> 单个短事务原子保存新 Shot 批次
```

草案 JSON 损坏时允许一次 `structure_recovery`（0.1）。恢复后仍非法则任务失败。审稿不可用时不阻断有效草案；Patch 非法或越界时丢弃 Patch 并保留原始有效草案。最终结构合同非法时任务失败，当前 Shot 批次保持不变。

`storyboard_breaker` 负责草案和 Patch；`storyboard_reviewer` 负责 Reflection。未配置 reviewer 时回退到 breaker 的 Provider 和模型，但仍使用独立低温参数。

## 5. 恢复、重试与提交不确定性

每个 LLM 阶段提交前先写入 `raw_response.checkpoint.inflight_phase`，收到结果后再保存响应和阶段产物。重启跳过已成功阶段：

- 草案提交结果不确定：以 `provider_submission_uncertain` 失败，禁止自动重提；
- 审稿提交结果不确定：不重提，按 `review_unavailable` 保存有效草案；
- Patch 提交结果不确定：不重提，保留修订前草案并标记 `needs_attention`；
- 明确 `not_submitted` 的可重试错误：统一任务运行时最多自动重试一次；
- 取消：在下一 checkpoint 生效，取消前不得切换 Shot 批次。

任务使用 `resource_key=project:{project_id}:shots` 和数据库活动去重键，运行在单并发文本任务通道。创建端点支持 `Idempotency-Key`。

## 6. 持久化与界面合同

只有最终合同通过，Handler 才在一个短事务中将旧 Shot 批次设为非当前、写入新 Shot、绑定 Dialogue、生成 Prompt 指纹并标记下游过期。旧批次始终保留为历史。

完整 Provider 响应和恢复轨迹保存在 `raw_response.checkpoint`。界面只读取 `result_payload.director_report`，并同时获得：

- `shot_ids`、`shot_count`、`pipeline_version`；
- `quality_gate`、`planning_ready`、`generation_ready`、`inspection_level`；
- `issue_counts`、`patch_applied`、`patched_shot_nos`；
- `resolved_issue_codes`、`unresolved_issue_codes`、`contract_error_codes`；
- 资产缺口与冲突。

旧任务没有这些字段时按历史记录展示，不解析 Provider 原始响应。
