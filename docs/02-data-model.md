# 数据模型

## Project

- `title`
- `style`
- `target_duration_sec`
- `aspect_ratio`: `16:9 | 9:16`
- `resolution`: 新项目使用 `854x480 | 480x854`；历史项目可继续保留原分辨率
- `creative_settings`
  - `english_level`
  - `animation_style`
  - `dialogue_language`
  - `translation_language`
  - `default_subtitle_mode`

## Chapter

单集项目只保留一个主输入：

- `input_mode`: `ai_brief | imported_script`
- `outline`: AI 创意描述
- `source_text`: 导入的已有剧本

AI 模式要求 `outline`，导入模式要求 `source_text`。旧项目迁移为 `imported_script`。

## Script

每次生成保存新版本。`scenes` 是结构化生产事实，`content` 是确定性可读稿，`dialogues` 是当前版本的索引。

## Dialogue

- `script_id`
- `character_id`
- `shot_id`
- `speaker_name`
- `text`: 英文对白
- `translation_zh`: 可空
- `emotion`
- `sequence_order`
- `beat_id`
- `sound_cues`
- `start_time / end_time`

对白同时是视频 Prompt 和导出字幕的数据源，不保存独立音频引用。

`speaker_name` 表示声音归属，不等同于可视角色。旁白、画外音、解说、系统语音以及未在任何 `visible_action` 中出镜的虚拟引导声保留对白内容，但 `character_id` 必须为空；它们不得进入 Character、角色参考图、镜头 `character_ids` 或媒体生成任务。真实角色即使在某一场画外说话，只要在其他场明确出镜，仍保留 Character 身份。

## Character / Scene / Prop

`asset_spec.state_variants[]` 每项包含稳定 `key`、名称、描述和出现的场次。实体本身只保存稳定身份；受伤、持物、天气、损坏等临时状态属于变体。

Character 只表示至少在一个生产场景中具有可见身体、动作或表情的实体。纯声音职责不是 Character，也不建立 `reference_plan`。

## Shot

`Shot` 是可独立生成的视频片段：

- `duration_sec` 不再由 LLM 预测。生成前为空表示由 Provider 智能决定；人工明确使用固定时长时保存覆盖值；视频版本采用后保存 Provider 返回或媒体检测得到的实际时长。
- 新生成方案只规划连续叙事边界和片段数量，不给单个 Shot 分配精确秒数，也不把每个短动作单独保存成 Shot。
- `dialogue_ids / character_ids / prop_ids`
- `shot_card.schema_version = 3`
- `shot_card.beats[]`: `beat_id / camera / action / dialogue_ids / sound_cues`
- `shot_card.segment_plan`: `duration_mode / expected_duration_range / internal_shot_count / actual_duration_sec`
- `video_prompt`: 实际编辑稿和实际发送稿
- `shot_card.prompt_fingerprint`
- `shot_card.prompt_stale`

`beats[]` 中的每一项是片段内部镜头，只表达有顺序的剧情、镜头语言、动作、对白和音效，不保存或要求人工分配秒数。默认每个片段包含 2–4 个内部镜头；只有场景或时空变化、连续性断裂、主要资产集合明显变化，或单次生成无法自然承载完整动作时，才拆为新的 Shot。历史项目中的 `beats[].duration_sec` 只作为旧数据兼容，不参与新 Prompt 编译。

## Asset / AssetCandidate

图片版本键：

```text
project_id + asset_type + entity_type + entity_id + asset_role + variant_key
```

`variant_key=base` 表示基础形象，其他稳定键表示剧情状态。同一键的重新生成只增加版本，不覆盖历史。

视频片段按 `shot + shot_video` 管理候选、当前版本和历史。

## ProjectStageRun

只保存操作审计，分类为 `script / assets / production / export`。它不是页面状态机。

`ProjectStage` 已删除。活动任务只对同一资源提供重复提交保护。

## GenerationTask

- 状态：`queued / running / waiting_provider / waiting_children / cancelling / succeeded / failed / cancelled`。
- `parent_task_id`：项目视频批次父子关系。
- `retry_of_task_id`：人工重试尝试链，不占用批次父子语义。
- `resource_key / active_dedupe_key`：资源范围与数据库活动去重键。
- `idempotency_key`：按项目与任务类型幂等。
- `available_at / lease_owner / lease_expires_at / heartbeat_at`：本地 Worker 领取和恢复信息。
- `cancel_requested_at`：幂等取消请求时间。

租约所有者是内部实现字段，不输出到 `GenerationTaskRead`。终态必须清空
`active_dedupe_key` 和租约。

## Export

- `subtitle_mode`: `none | en | bilingual`
- `asset_id`
- `resolution / duration_sec / format`
- `ffmpeg_command`
- `status`

旧声音媒体文件不自动物理删除，但不会进入新工作台、生成链路或导出链路。
