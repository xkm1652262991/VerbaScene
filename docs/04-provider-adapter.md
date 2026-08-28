# Provider Adapter

## 当前槽位

- `llm`
- `image`
- `video`

声音不再是独立 Provider 槽位。

## 公开视频能力

每个 Adapter descriptor 至少公开：

- `capabilities`
- `native_audio`
- `reference_images`
- `reference_videos`
- `reference_audio`
- `multi_reference`
- `smart_duration`
- `min_duration_sec`
- `max_duration_sec`
- `supported_resolutions`

前端视频模型栏必须展示原生音频、多参考和规格能力，不能根据供应商名称猜测。

## Prompt 与审计

视频请求使用 `Shot.video_prompt` 原样发送。任务审计保存：

- 最终 Prompt
- Prompt 输入指纹
- 引用资产 ID 与版本
- Provider 和模型
- Provider 能力快照

上游变化不会自动覆盖人工编辑稿。只有用户点击“重新编译 Prompt”才写入新的最终 Prompt。

## 异步视频合同

异步视频 Adapter 必须拆分为 `submit / poll / cancel / fetch_result`。
`submit()` 只完成一次远程提交并立即返回任务 ID，不得在 Adapter 内部循环轮询。
Worker 持久化远程 ID 后进入 `waiting_provider`，后续领取只能调用 `poll()`。

`ProviderError.submission_state` 为 `not_submitted | accepted | unknown`。只有明确
`not_submitted` 且可重试的错误允许最多一次自动重试；`unknown` 必须人工处理。

Provider 使用自身认证下载结果并返回临时文件。应用层通过 `MediaStore` 写入
`storage/projects/...`；Provider 不直接写项目最终目录。

## Seedance 2.0 方舟 Adapter

Provider 名称为 `seedance2_api`，默认模型为
`doubao-seedance-2-0-260128`，调用方舟内容生成任务接口：

- 创建：`POST /api/v3/contents/generations/tasks`
- 查询：`GET /api/v3/contents/generations/tasks/{task_id}`
- 取消：`DELETE /api/v3/contents/generations/tasks/{task_id}`

Adapter 使用 Bearer API Key，只允许从后端环境变量或本机私有密钥文件读取。
密钥不得进入数据库普通字段、任务输入、原始响应、日志、文档或前端回显。

能力声明：

- 原生音频：支持，默认开启。
- 参考图片、参考视频和参考音频：支持。
- 多参考输入：支持。
- 智能时长：支持。默认提交 `duration=-1`，由模型在 4–15 秒有效范围内选择整数秒。
- 固定时长：仅在用户显式选择时提交 4–15 秒整数。
- 支持 480p、720p 和 1080p。
- 支持 16:9、9:16、4:3、3:4、1:1、21:9 和 adaptive。

项目资产按照 ProviderRequest 中的精确引用顺序编译到 `content`。图片、视频和
音频分别独立编号；例如第一张图片是“图片1”，第一段视频是“视频1”。最终
Prompt 必须使用与 `content` 完全一致的“图片N”指代，不能把项目内部的
`@资产名` 当成模型可以识别的素材标识。

当前视频链路执行受控素材策略：

- 默认不发送片段首帧；只有 `shot_card.video_reference_asset_id` 明确绑定可用图片时，才把该图片作为首帧和构图起点。
- 应用层未使用首帧时，最多解析 20 张角色形象图和 1 张场景图；使用首帧时应用层总上限为 22 张。Provider Adapter 仍必须按各自合同执行更严格的上限（MiniMax H3 当前最多 9 张）。
- 同一角色只发送一个精确采用版本，不额外发送多角度源图。
- 默认资产图只生成角色和场景。道具不生成独立参考图，也不上传给视频模型；道具通过剧情、动作和空间关系文字描述。
- 显式绑定仍保存角色、场景和道具的精确版本，但视频 Provider 只消费角色与
  场景引用。引用变化只标记 Prompt 可能过期，用户重新编译后才更新映射。

图片允许使用公网 URL、`asset://` 或 Base64 Data URL。本机存储中的图片由
Adapter 转为 Data URL 后提交；本机视频和音频不会被隐式上传，必须先具有方舟
可访问的公网 URL 或 `asset://` 地址，否则在提交前明确失败。

Adapter 的 `submit()` 创建方舟任务后立即返回；Worker 分次调用 `poll()`。终态后
`fetch_result()` 下载短效 `content.video_url` 到临时文件，应用层再经
`LocalMediaStore` 创建候选版本。失败保留方舟错误码、远端任务 ID 和提交状态。

智能时长不是省略时长参数，而是 Seedance 专属的 `duration=-1` 合同。业务层使用
`duration_mode=provider_auto | fixed` 表达意图，不得把 `-1` 当成所有视频 Provider
都支持的通用时长。任务记录请求模式；候选资产记录 Provider 返回的实际时长；候选
被采用后，实际时长成为片段、合成和字幕的事实来源。若智能时长响应没有返回有效
实际时长，任务必须失败，不能把 `-1` 或规划值写成媒体时长。

## 当前实现边界

Seedance 2.0 方舟 Adapter 已纳入视频槽位。Seedream 仍未适配。合同测试使用
假密钥和 Mock HTTP 响应，不自动发起任何真实付费生成。

## MiniMax H3 网关 Adapter

Provider 名称为 `minimax_h3_gateway`，使用统一的异步视频合同。配置模型为
`auto` 时，明确首帧路由到 `minimax-h3-fl2va`，角色或场景参考图路由到
`minimax-h3-r2v`，无参考素材时使用 FL2VA 文生视频。完整接口、尺寸映射、
免 Key 边界和验收要求见 [27-minimax-h3-video-integration.md](27-minimax-h3-video-integration.md)。
