# MiniMax H3 视频网关接入

## 1. 接入目标

VerbaScene 通过一个 `minimax_h3_gateway` Provider 对接 H3 黑盒网关，不让业务层感知
R2V、FL2VA、GPU 或 ComfyUI 的内部实现。

- Base URL：运行时配置，必须包含 `/v1`。
- 鉴权：支持 Bearer API Key，也允许受控内网环境显式选择“无密钥”。
- 运行方式：异步提交、持久化远端任务 ID、分次轮询、按需取消、完成后下载 MP4。
- 默认模型：`auto`，由冻结的素材语义决定实际提交模型。
- 当前部署地址和密钥不写入 Git；由模型管理页保存到本地运行时配置。

## 2. 自动模型路由

Adapter 按以下顺序选择实际模型：

1. 请求显式指定 `minimax-h3-r2v` 或 `minimax-h3-fl2va` 时尊重指定值。
2. 存在 `video_first_frame` 参考素材时使用 `minimax-h3-fl2va`，且只上传明确的首帧。
3. 没有首帧但存在角色、场景等身份参考图时使用 `minimax-h3-r2v`。
4. 没有任何参考素材时使用 `minimax-h3-fl2va` 文生视频。

FL2VA 不能把后续角色图误当成尾帧，因此自动模式下，明确首帧与其他实体参考同时存在时
只上传首帧。R2V 请求最多上传 9 张图片，并根据冻结的 `reference_assets` 顺序重新生成
`<Picture N>` 绑定，避免 Prompt 编号与实际上传顺序漂移。

## 3. 网关合同

```text
POST /v1/videos
  -> 202 + { id, status }
  -> GET /v1/videos/{id}
  -> completed 时 GET /v1/videos/{id}/content
  -> 需要取消时 POST /v1/videos/{id}/cancel
```

状态映射：

| 网关状态 | ProviderStatus |
| --- | --- |
| `queued` | `queued` |
| `running` | `running` |
| `completed` | `succeeded` |
| `failed` | `failed` |
| `cancelled` | `cancelled` |

`submit()` 只提交一次并立即返回远端 ID。Worker 在数据库落下 ID 后进入
`waiting_provider`；重启恢复只能继续 `poll()`，不得重复提交。下载结果先进入 Provider
临时文件，再由 `MediaStore` 保存到项目候选目录。

## 4. 输入与规格映射

- R2V 与 FL2VA 都使用 `multipart/form-data`，本地项目图片由后端读取后上传。
- 支持 `/storage/...`、受配置约束的本地媒体 URI、Data URL 和 HTTP(S) 图片。
- 项目横版生成映射为 `1024x576`，竖版映射为 `576x1024`；两者均满足宽高为 32
  的倍数。项目最终的 `854x480 / 480x854` 规格由 FFmpeg 导出阶段负责。
- 时长范围为 `0.2-15` 秒，FPS 固定为 24，默认 `steps=19`。
- 网关内部 GPU 并发为 1；VerbaScene 可以异步提交多个任务，`queued` 是正常状态。

## 5. H3 原生音频 Prompt 合同

`Shot.video_prompt` 仍保存用户可编辑的通用视频 Prompt，不因切换 Provider 被覆盖。
`minimax_h3_gateway` 只在提交前生成 H3 专属的发送稿，并标记
`h3-native-audio-v1` 合同版本：

- 通用 Prompt 外包为 H3 的 `integrated_multimodal_description`、
  `overall_soundscape`、`non_diegetic_music` 三段结构；已经完整使用 H3 三段或
  Ref2VA 六段结构的人工稿不再重复包裹。
- 项目编译出的 `姓名（情绪）说：{英文台词}` 转成稳定说话人 `(S1)`、`(S2)` 与
  `<d>[English] ...</d>`；ID 按首次发声顺序分配，同一说话人跨句复用同一 ID。
- 每句对白只保留在对应 `<d>` 中一次。发送稿明确把所有可听语言限定为这些对白，
  非说话角色保持安静，防止模型自行补出旁白、串台、含混人声或重复朗读。
- `overall_soundscape` 只概括环境底声、可见动作产生的物理声和非语言人声，不复制
  完整对白；混音保持在对白之下。明确要求全程无声时才使用 `N/A`。
- 项目没有画外配乐需求时固定发送 `non_diegetic_music: N/A`。同步画内音乐仍属于
  镜头事件，不能误放到画外配乐字段。

这层规则用于降低“乱音”的生成概率，不是确定性音频滤镜。生成后仍需检查多余人声、
错读、重复、爆音、截断与口型归属；真实效果只能通过用户明确发起的 H3 生成验收。

## 6. 安全与验收边界

- “无密钥”不是公开部署能力，只适用于有网络访问控制的内网；正式共享部署应恢复 API Key
  或仅放行 VerbaScene 后端所在主机。
- 合同测试使用 Mock HTTP，覆盖自动路由、multipart、状态映射、取消、下载和错误归一化。
- 只读连通性检查只证明 `/health` 与 `/models` 可达，不等于真实生成已验证。
- 不因接入测试自动提交真实视频；真实生成必须由用户在工作台明确发起。
