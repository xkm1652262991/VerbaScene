# Agent 设计文档

## 1. 设计原则

- Agent 输出必须结构化，便于审核和持久化。
- Agent 不直接调用具体模型 API，应通过 Provider Adapter。
- 剧本 Agent 之间只通过版本化产物合同传递结果；当前运行态保存在任务 checkpoint，不把数据库表当作协作协议。
- 每个 Agent 只负责一个清晰阶段。
- Agent 不依赖阶段审批门禁；缺少输入时只拒绝当前动作，不锁住其他工作区。
- 所有 Agent 身份保持媒介中立，不得在系统提示词中写死二维、三维、赛璐璐或写实风格。
- `Project.style` 是视觉风格的唯一事实源；`creative_settings.animation_style` 仅作为兼容镜像，保存时必须与其同步。
- 当前产品默认风格为“高品质风格化三维儿童动画”。默认值必须在创建界面明确展示，不能以隐藏 Prompt 的方式注入。

## 2. Agent 列表

### 2.1 Project Planner Agent

职责：

- 分析 AI 创意描述或导入剧本。
- 识别 A1 语言边界、角色、情节和动画基调。
- 估算目标时长和视频片段数量。

输入：

- 创作输入和输入模式。
- 英语级别和动画风格；不接收受众年龄约束。

输出：

- 项目生产摘要。
- 风格关键词。
- 推荐时长。
- 推荐镜头数量。

当前实现：

- 根据项目和章节生成生产计划。
- 项目风格只在 Project `style` 中维护，剧情事实来自大纲与正文，连续性来自已确认资产和 Shot Card。

### 2.2 Script Multi-Agent Pipeline

职责与边界：

- Story Architect 只输出 `StoryBlueprint`，解决故事承诺、人物欲望与阻碍、因果节拍、结局兑现、英语交流机会和受保护要求，不写生产场景。
- Script Writer 只根据输入与蓝图输出 `ScriptDraft.scenes`，不审稿、不维护可读正文和顶层对白副本。
- Comprehensive Reviewer 只输出 `ScriptReview.issues`，从因果、人物、对白、连续性和生产可执行性五个维度定位问题，不打分、不改稿；英语质量属于对白维度，不设独立英语教练。
- Targeted Reviser 只输出 `ScriptPatch`，按审稿允许的场号提供场景替换，不得重写未点名场景。
- Contract Validator 是确定性程序，不是 Agent；只检查可解析性、场号、对白归属、明确英文表达和生产字段，不评价审美。
- 流水线最多执行一次自动 Patch，不建立 Agent 自由聊天或循环辩论。

共同要求：

- 以“故事开发、生产写作、综合审稿、必要时一次定点修订、确定性合同校验”完成创意或已有剧本的改编。
- AI 创意模式依据创意描述和英语等级合理创作；导入模式优先保留人物、事件、因果、道具、场景和对白意图。
- 故事开发先建立人物欲望、具体阻碍、选择、后果、结尾兑现和自然英语交流机会，不直接填写生产场景表。
- 将抽象叙述转成适合动画视频呈现的可见动作，并显式记录场景连续性的入场/离场状态。
- 角色对白使用符合目标英语等级的英文，并可附可选中文释义。
- 同时输出供视频模型使用的结构化音效提示。
- 审稿问题统一为 `issues`，严重度分为 `must_fix` 与 `editorial_note`；只有前者触发一次局部修订，不按长度或总分自动改稿。

输入：

- 项目生产摘要。
- 创作输入与输入模式。
- 项目创作设置。

输出：

- 可追踪的 `StoryBlueprint`、`ScriptDraft`、`ScriptReview`、可选 `ScriptPatch`、`ContractReport`、降级记录和 `quality_gate`。
- 结构化生产场景 `scenes`，包含地点、时间、人物、道具、可见动作、剧情作用、连续性状态、原文依据、补足元素、场内英文对白和音效提示。
- 由后端根据 `scenes` 确定性渲染的可读剧本 `content`。
- 由 `scenes[].dialogues` 确定性展开的顶层对白索引 `dialogues`。

一致性约束：

- `scenes` 是唯一真相源；页面不再允许分别编辑 `content` 与顶层 `dialogues`。
- 保存会重新派生 `content` 与 `dialogues`，防止资产提取、片段拆解、视频 Prompt 和字幕消费不同版本的剧情。
- 新项目角色对白的英文 `text` 必填，`translation_zh` 可空；对白必须有稳定顺序。
- 为兼容历史数据，旧版 `summary` 可映射为 `visible_action`；未出现在旧版正文里的顶层对白不会迁移。
- LLM Provider 使用真正分离的 system/user 消息：职责和质量规则进入 system，原稿、蓝图、草稿与审稿内容只作为 user 数据。
- 不按输入字数推导时长，不按场景数、台词字数、句数或 Prompt 长度拒绝剧本。
- Patch 由后端按 `scene_no` 确定性合并；重复、未知、越权场号全部拒绝，未替换场景保持不变。
- `quality_gate` 只有 `pass`、`needs_attention` 和 `review_unavailable`，仅用于如实提示，不形成页面门禁。

### 2.3 Entity Extraction Agent

职责：

- 从当前结构化生产剧本建立角色、场景和道具清单。
- 合并同一基础场景的子区域，区分可复用资产与一次性画面元素。
- 将稳定身份、临时状态、来源依据和参考图计划分开建模。
- 具有自主行为的人、动物、怪物和自主机器人一律归入角色，不得归入道具。

输入：

- 当前剧本的结构化 `scenes` 与可读 `content`。
- 上一版已采用资产；同一实体再次出现时继承不可变身份。

输出：

- 结构化角色卡、场景卡和道具卡草稿。
- 每个资产的原文依据、状态变体与参考图计划。
- 同一次实体 LLM 调用输出的参考图最终自然语言 Prompt。

一致性约束：

- `asset_spec` 保存结构化身份和 `image_prompts`；`appearance`、`description`、`fixed_prompt` 和 `visual_prompt` 只作为现有界面的兼容镜像。
- 每个基础形象与状态变体具有稳定 `variant_key`；重新生成只增加媒体版本，不改变变体键。
- 受伤、康复、表情、姿态、正在手持、时间、天气和光线属于状态变体，不得污染固定身份。
- 项目视觉风格在真正调用图片或视频 Provider 时统一注入，不复制到实体身份、参考图主体描述或分镜主体描述里。
- Agent 可以读取项目风格来做兼容的造型与光线设计，但其结构化产物必须保持风格可替换；修改项目风格不要求重新提取剧情事实。
- 模型输出在写库前必须执行分类、稳定身份、来源依据、重复项和状态污染检查；检查结果作为审核信息随草稿保存，不丢弃整批输出。
- 写库前先保留模型设定，再从结构化剧本回填依据与状态，用类型化生产默认补齐必需视觉字段，并将临时状态从稳定身份中移出。
- 生成阶段即使有 blocker 也进入待审核；只有“确认资产”会因 blocker 失败。
- 分镜必须保留 `shot_card.source_scene_no`，并在同一次调用中把当前场次状态写入最终图片 Prompt。

### 2.4 Character Consistency Agent

职责：

- 生成角色固定描述。
- 生成角色参考图 Prompt。
- 为分镜注入角色一致性信息。

输入：

- 角色卡。
- 项目风格。

输出：

- 角色固定 Prompt。
- 角色参考图 Prompt。
- 分镜角色描述片段。

### 2.5 Scene Design Agent

职责：

- 生成场景固定描述。
- 生成场景参考图 Prompt。

输入：

- 场景卡。
- 项目风格。

输出：

- 场景固定 Prompt。
- 场景参考图 Prompt。

### 2.6 反思式分镜导演 Agent

职责：

- 将当前剧本拆解为可独立生成的视频片段。
- 先观察冻结的资产设定、采用版本和媒体元数据，再对完整草案做一次独立 Reflection。
- 为每个片段规划多个内部镜头节拍，绑定角色、场景、道具和对白 ID。
- 默认片段时长 8–15 秒、优选约 12 秒，每片段包含 2–4 个内部镜头；不得因为普通景别变化、对白轮次或单个反应动作创建新的片段。
- 不预测 Shot 或内部镜头秒数；内部镜头只按剧情顺序描述，由视频 Provider 根据完整动作和对白选择实际时长。
- 只有场景/时空变化、叙事连续性断裂、主要资产集合明显变化，或单片段超过规划上限时才拆片段。
- 项目分段是稳定创作结构，不随当前临时选中的 Provider 自动缩短；Provider 时长不足时由生成前能力校验明确阻止。

输入：

- 创建任务时冻结的剧本、Dialogue、角色、场景、道具和状态变体。
- 当前采用资产的版本、URI 与媒体元数据。
- 项目视觉风格、画幅、分辨率和分段合同。

输出：

- 片段表。
- 含有序镜头语言、动作、对白 ID 和音效提示的 Shot Card。
- `AssetReadinessReport`、`ReflectionReport`、可选 `ShotPatch` 与最终 `ContractReport`。
- 创意性的分镜图主体 Prompt；项目风格、媒体编号和最终视频 Prompt 仍由确定性服务编译。

### 2.7 Prompt Engineering Agent

职责：

- 将片段与资产引用转化为图片和视频 Prompt。
- 根据 Provider 格式生成适配参数。

输入：

- 分镜。
- 角色/场景/道具的当前状态变体。
- 英文对白、说话人、情绪和音效提示。
- Provider 能力描述。

输出：

- 图片 Prompt。
- Seedance 式可编辑最终视频 Prompt。
- 负面 Prompt。
- Provider 参数。

视频 Prompt 必须：

- 精确包含片段内部节拍、英文对白、说话人、情绪和音效提示。
- 内部镜头直接使用“镜头1、镜头2……”按顺序组织，不编译单镜头时间区间或持续秒数。
- 只使用 Provider 能识别的“图片N / 视频N / 音频N”绑定，不暴露项目内部 `@资产` 标识。
- 明确要求模型生成原生对白与环境/动作音效。
- 明确禁止画面出现字幕、标题、气泡文字或其他可读文字。
- 保存输入指纹；上游变化只标记过期，只有用户主动重新编译才覆盖 `Shot.video_prompt`。

### 2.8 Image Generation Agent

职责：

- 调用图片 Provider 生成图片资产。
- 保存任务和资源版本。
- 生成角色参考图、场景参考图、道具/动物参考图和镜头分镜图。
- 默认选中新生成版本，保留旧版本用于后续人工切换。

输入：

- 图片 Prompt。
- 参考图。
- Provider 配置。

输出：

- 图片 Asset。
- GenerationTask。

当前实现：

- `character_reference_prompt` 生成角色参考图 Prompt。
- `scene_reference_prompt` 生成场景参考图 Prompt。
- `prop_reference_prompt` 生成道具/动物单主体参考图 Prompt。
- `shot_image_prompt` 复用分镜图片 Prompt。
- 通过 Mock Image Provider 写入 `assets` 和 `generation_tasks`。

### 2.9 Video Generation Agent

职责：

- 调用视频 Provider 生成片段视频。
- 支持图生视频和参考图视频。
- 根据 Provider 能力选择图片、视频、音频和多参考输入。
- 保存视频片段版本并默认选中新版本。

输入：

- 视频 Prompt。
- 分镜图。
- 角色/场景参考图。

输出：

- 视频 Asset。
- GenerationTask。

当前实现：

- `shot_video_prompt` 生成镜头视频 Prompt。
- 通过 Mock Video Provider 生成 `video` Asset。
- 生成前只校验当前片段自身必需输入，不检查阶段确认状态。
- 生成结果写入 `assets` 和 `generation_tasks`。

### 2.10 Composition Agent

职责：

- 调用 FFmpeg 合成正片。

输入：

- 当前采用的视频片段版本。
- 当前 Dialogue 时间轴。
- `subtitle_mode=none|en|bilingual`。

输出：

- 成片 Asset。
- Export 记录。

当前实现：

- 校验每个片段都有当前采用的视频版本。
- 保留视频片段原生音轨；无音轨片段补等长静音。
- 字幕只在导出时从当前 Dialogue 生成并烧录，默认不烧录。
- 生成并同步执行 FFmpeg 合成命令。
- 生成 `final_video` Asset。
- 生成 Export 记录。
- 若输入仍是 `mock://` 或无法解析的内部路径，合成会拒绝执行并返回可解释错误；真实本地/HTTP 媒体会落盘为 MP4。

### 2.11 Review Agent

职责：

- 检查资源完整性。
- 后续支持视觉一致性和音画同步检查。

输入：

- 当前阶段所有资源。

输出：

- 检查报告。
- 待修复项。

## 3. Agent 输出要求

- LLM 结构化 Agent 必须输出可解析 JSON，字段名保持稳定，不能只输出自然语言。
- 图片和视频 Provider 必须返回统一 `ProviderResponse` 和版本化媒体信息。
- 失败时输出可解释错误。
- 所有 Prompt 需要保存到任务记录。

## 4. 人工控制边界

- 剧本、对白、资产卡和片段 Prompt 可直接编辑。
- 图片和视频候选通过“采用版本”进入当前生产选择，历史版本保留。
- 采用版本不等于流程审批，也不解锁页面。
- 上游修改标记下游可能过期，用户决定是否重新编译或重生成。
- 导出前明确展示将采用的视频版本和字幕模式。

## 5. 当前实现状态

当前“Agent”是 Python Prompt/解析模块和业务服务组合，不是 LangGraph 中的持久化节点。主要已实现能力：

- Project Planner、Script Multi-Agent Pipeline：按 Story Architect、Script Writer、Comprehensive Reviewer 和可选 Targeted Reviser 调用 LLM；Contract Validator 与 Patch 合并由后端确定性执行，可读剧本和顶层对白由 `scenes` 派生，完整阶段轨迹保存在生成任务中。
- Entity Extraction：一次 LLM 调用生成角色、场景、道具、结构化资产卡和参考图最终 Prompt。
- Shot Breakdown 已升级为有边界的反思式分镜导演 Agent：它读取冻结的剧本、实体和采用资产元数据，先生成完整片段草案，再由独立 `storyboard_reviewer` 按 `coverage / continuity / dialogue / cinematography / asset_feasibility / production` 六类问题反思；只有 `must_fix` 才触发至多一次受限 Patch。确定性服务负责校验、重排编号、Dialogue 保护、Prompt 编译和原子切换批次。完整合同见 `28-反思式分镜导演Agent.md`。
- Image Generation：原样读取已保存图片 Prompt 和结构化可见实体 ID；不调用 Refiner、图片编译器或运行时重写。
- Shot Frame：直接从 `shot_card.image_prompts` 生成首帧、关键帧和尾帧，不再创建新的 `ShotFramePrompt` 中间记录。
- Video Generation：默认使用文本 Prompt 与角色/场景参考图；明确启用首帧时才额外发送分镜图，当前在请求内轮询到完成。
- Dialogue：作为视频 Prompt 与导出字幕的共同数据源。
- Composition：拼接原生音轨视频，对无音轨片段补静音，并按需烧录字幕。
- Review：规则质检已覆盖分镜、图片和视频，结果写入 `quality_checks`；尚未接 VLM。
- Asset Resolver：视频默认只解析角色和场景图，明确启用时再解析片段首帧；道具只保留结构化剧情信息，并把实际引用写入任务上下文。
- AVD Prompt/Rule/Asset Strategy/Patch Pipeline：已接入 Prompt 包、规则检查、资产策略和变更影响预演。

运行时可以通过 `AgentConfig` / `PromptVersion` 为 `script_rewriter`、`script_reviewer`、`extractor`、`storyboard_breaker` 和 `storyboard_reviewer` 选择文本 Provider、模型和系统 Prompt。两个 reviewer 都与创作配置分离；未单独配置 `storyboard_reviewer` 时回退到 `storyboard_breaker` 的 Provider 和模型。

当前明确缺口：

- LangGraph 或分布式编排尚未引入；当前剧本流水线使用本地持久化队列和 checkpoint 恢复，不能冒充分布式多 Agent 系统。
- VLM 视觉审核、角色身份一致性模型和音画同步模型尚未接入。
