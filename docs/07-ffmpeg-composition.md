# FFmpeg 成片导出

## 输入

- 当前片段列表，按 `shot_no` 排序。
- 每个片段已采用的视频版本。
- 当前 Script 对应的 Dialogue。
- `subtitle_mode = none | en | bilingual`。

HTTP 接口只创建 `project_export` 后台任务并返回 `202 + GenerationTask`。创建任务时冻结
片段顺序、采用的视频版本、媒体 URI、实际时长、Dialogue 时间轴、项目分辨率和字幕模式；
排队期间的上游修改不会悄悄改变本次成片。

## 音频策略

每个视频先探测是否存在音轨：

- 有音轨：保留片段原生音频。
- 无音轨：按片段时长生成静音轨。

所有视频和音频片段分别规范化后用 concat 合并，保证时间线长度一致。

## 字幕策略

- `none`：不生成字幕输入，不烧录。
- `en`：使用 `Dialogue.text`。
- `bilingual`：英文下一行使用 `Dialogue.translation_zh`；中文为空时只显示英文。

字幕只在导出时渲染，不创建独立字幕资产，也不改变视频模型 Prompt 中“画面禁止文字”的约束。

## 追溯

最终资产 manifest 保存片段 ID、片段版本、时长、是否存在原生音轨、补静音策略、字幕模式和 Dialogue ID。

## 执行、恢复与取消

- 导出与视频截帧共用单并发 `media` 任务通道；FFmpeg 运行期间不持有数据库事务。
- 子进程使用可轮询的 `Popen` 执行。收到取消请求时终止进程、清理任务临时文件并进入 `cancelled`。
- 输出文件以任务 ID 命名并通过 `MediaStore` 落盘；进程崩溃后可从冻结快照重跑，不会重复调用付费 Provider。
- 只有 FFmpeg 成功且媒体已落盘后，才在短事务中创建 `final_export` Asset、Export 审计并完成任务。
- 同一项目同时只允许一个活动导出任务；`Idempotency-Key` 可返回原任务，失败或取消任务可使用原快照人工重试。
