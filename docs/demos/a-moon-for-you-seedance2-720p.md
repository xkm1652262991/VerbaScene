# 《送你一个月亮》720p中英双语成品

当前展示版本为 **118秒（1分58秒）**，1280×720，24fps，H.264视频、AAC立体声音轨，含10句中英双语字幕。

[双语成品](https://github.com/xkm1652262991/VerbaScene/releases/download/demo-a-moon-for-you-seedance2-720p-20260911/a-moon-for-you.seedance2.r2.flashvsr-1.5x.bilingual.mp4) · [无字幕版](https://github.com/xkm1652262991/VerbaScene/releases/download/demo-a-moon-for-you-seedance2-720p-20260911/a-moon-for-you.seedance2.r2.flashvsr-1.5x.clean.mp4) · [双语SRT](https://github.com/xkm1652262991/VerbaScene/releases/download/demo-a-moon-for-you-seedance2-720p-20260911/a-moon-for-you.seedance2.r2.flashvsr-1.5x.bilingual.srt) · [制作清单](https://github.com/xkm1652262991/VerbaScene/releases/download/demo-a-moon-for-you-seedance2-720p-20260911/a-moon-for-you.seedance2.720p.production.json)

## 本次修订

沿用[Seedance 2.0整片生成版本](a-moon-for-you-seedance2.md)的角色、场景与9个剧情段。片段7的两句对白在约8.6秒结束；本次保留原片0–10秒的对白、牵手和反应，剪去末尾发生场景漂移和蛋糕复制的5秒，接入片段8已经坐好的两位朋友。故事事件和10句英文对白保持完整，原始候选与旧成片保留为历史版本。

| 项目 | 本次结果 |
| --- | --- |
| 原始生成 | Seedance 2.0，480p，智能时长，无全片目标时长 |
| 视频重生成 | 本轮剪辑与超分未重新调用视频生成模型 |
| 剪辑后时长 | 118秒，2832帧 |
| 超分 | FlashVSR 1.5倍，最终1280×720 |
| 字幕 | 10句英文及对应中文释义，按原生音轨定位 |
| 音轨 | 直接保留修订版原生音轨 |

## 超分与合成

整片超分任务曾因常驻引擎退出而失败。服务恢复后，按现有9段的边界分段处理，9段均成功，再按原时间线拼接。为了保持画幅，输入先无损补边以适配服务尺寸对齐要求，超分后裁回16:9的720p成片。字幕在最终分辨率重新渲染。

FlashVSR为这次制作使用的外部后期服务，不表示应用新增了FlashVSR Provider Adapter。

## 已完成的检查

成品的帧数、时长和音轨经过核对；两份最终MP4完成解码检查，音频数据与修订版母带一致。中英字幕10条全部对齐，无回退项。对比了超分前后画面，并查看片段7到8的切点、长句双语字幕和结尾字幕。

独立生成片段间的小道具细节仍可能有变化；本记录说明本次实际成片，不代替长期批量生产验收。
