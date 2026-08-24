# FFmpeg 成片导出

## 输入

- 当前片段列表，按 `shot_no` 排序。
- 每个片段已采用的视频版本。
- 当前 Script 对应的 Dialogue。
- `subtitle_mode = none | en | bilingual`。

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
