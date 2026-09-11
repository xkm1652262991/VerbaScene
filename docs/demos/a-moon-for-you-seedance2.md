# 《送你一个月亮》Seedance 2.0 整片重制

这次沿用狐狸、兔子和月夜池塘的 3 张参考图，将[完整故事](../scripts/送你一个月亮-seedance2.md)重新组织为 9 个剧情段。最终画面与原生音轨全部来自本轮 Seedance 2.0 生成。

成片 **123 秒（2 分 03 秒）**，16:9，854×480，H.264 视频与 AAC 立体声音轨。

[观看与下载](https://github.com/xkm1652262991/VerbaScene/releases/tag/demo-a-moon-for-you-seedance2-20260911) · [英文 SRT](https://github.com/xkm1652262991/VerbaScene/releases/download/demo-a-moon-for-you-seedance2-20260911/a-moon-for-you.seedance2.en.srt) · [制作清单及校验值](https://github.com/xkm1652262991/VerbaScene/releases/download/demo-a-moon-for-you-seedance2-20260911/a-moon-for-you.seedance2.production.json)

## 生成方式

- 模型：`doubao-seedance-2-0-260128`，通过现有 `seedance2_api` Provider Adapter 调用方舟。
- 所有请求使用 `resolution=480p`、`ratio=16:9`、`generate_audio=true`、`watermark=false`。
- 不设置全片目标时长；各请求均使用 `duration=-1`。Prompt 描述动作、情绪、对白与镜头关系，不分配固定秒数。
- 参考图片直接复用上一版的 Qwen Image 3.0 资产，素材编号与实际请求顺序一致；未发送片段首帧。
- 9 个剧情段全部重新生成。共执行 10 次视频生成请求；揭示罐中倒影的一段另生成了修正版。
- 保留原剧本的 10 句英文对白，按要求跳过 Reflexion Agent；未使用独立 TTS 或音效生成。
- 旧 MiniMax H3 批次与候选版本保留在工作台，本次成片使用新的 Seedance 批次。

## 实际时长与剧情

以下为采用片段的实际时长，不是请求前设定的时间预算。

| 剧情段 | 内容 | 采用时长 | 英文对白 |
| --- | --- | --- | --- |
| 01 | 一个秘密的生日礼物 | 15 秒 | I love the moon. Wait here. I have a gift! |
| 02 | 太着急，把月亮弄碎了 | 12 秒 | （无对白） |
| 03 | 小心一点，还是捞不到 | 12 秒 | Come back, little moon! |
| 04 | 被忽略的生日邀请 | 12 秒 | Come and sit with me! Not yet! |
| 05 | 保守秘密，却伤了朋友 | 15 秒 | Don't look! |
| 06 | 狐狸选择回到朋友身边 | 15 秒 | I'm sorry. I have no gift. |
| 07 | 我想送你月亮 | 15 秒 | I want to give you the moon. Stay with me. |
| 08 | 一起发现罐子里的月亮 | 12 秒 | （无对白） |
| 09 | No lid，最好的生日礼物 | 15 秒 | No lid! |

## 结果检查

生成结果为带原生音轨的可解码视频；请求使用的 480p 档位在本轮返回 864×496 媒体，工作台按项目规格导出为 854×480。角色、场景、镜头动作和对白均记录在片段与任务中。

对各段抽帧和关键动作进行检查，并使用 ASR 核对 10 句英文对白。揭示镜头首版出现两个明显的月亮影像，修正版改用俯拍水面反射。字幕根据新音轨进行 ASR 与 VAD 定位，10 条全部对齐，无回退项。无字幕版、英文字幕版和首页压缩版均完成解码检查。

月亮倒影采用童话式视觉表达；小道具的颜色、比例与视角仍可能在独立生成的段落间变化。本记录说明这一次实际制作结果，不代表长期批量生产或所有 Provider 的验收结论。

密钥、内网地址、本机路径和原始任务响应不进入公开制作清单。费用未取得 Provider 结算记录，不填写估算金额。

## 历史版本

[MiniMax H3 首版制作记录](a-moon-for-you.md)保留为历史证据。
