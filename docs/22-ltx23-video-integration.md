# LTX-2.3 视频 Provider 接入

## 1. 目标

将可选视频模型从 Wan2.2 I2V 切换为 LTX-2.3 服务，同时保留旧
`wan2_i2v_api` Adapter，便于后续在模型管理页回切。

- Provider 名称：`ltx23_api`
- 默认模型名：`LTX-2.3`
- 服务地址：通过 `LTX23_API_BASE_URL` 配置；本地示例为 `http://127.0.0.1:18109`
- 默认规格：`1024x576`、`81` 帧、`16 FPS`、`8` 步、`CFG 1.0`
- 鉴权：无

## 2. 上游接口

纯文本请求提交到：

```http
POST /v1/ltx23/videos/text
Content-Type: application/json
```

带首帧图请求提交到：

```http
POST /v1/ltx23/videos/image
Content-Type: multipart/form-data
```

两个提交接口都返回 `202` 和队列任务标识。Adapter 在当前生成任务内继续：

```text
提交任务
  -> GET /v1/ltx23/jobs/{job_id}
  -> 等待 succeeded / failed / cancelled
  -> GET /v1/ltx23/jobs/{job_id}/result
  -> 将 MP4 转存到项目 storage
```

## 3. ProviderRequest 映射

| ProviderRequest | LTX-2.3 字段 |
| --- | --- |
| `prompt` | `prompt` |
| `negative_prompt` | `negative` |
| 第一张 `references` 图片 | multipart `image` |
| `params.frames` | `frames` |
| `params.fps` | `fps` |
| `params.steps` | `steps` |
| `params.guidance` / `params.cfg` | `cfg` |
| `params.seed` | `seed` |
| `params.strength` | `strength`，仅图生视频 |
| Adapter 生成的幂等标识 | `client_job_id` |

没有参考图时走文生视频；存在参考图时只取第一张作为首帧，走图生视频。现有漫剧
生产主链路已有已确认分镜图，因此默认使用图生视频端点。

## 4. 边界

- 主 API 仍同步等待上游队列完成，以兼容当前 `ProviderAdapter.submit()` 和候选资产落库流程。
- 不在接入验证中发起真实生成，只用 mock 合同测试覆盖提交、轮询、下载和本地转存。
- 不删除或改写旧 Wan Adapter；模型切换只改变当前视频运行槽位。
- 上游服务已有自己的 FIFO 队列，本项目现有视频任务队列继续负责项目级任务持久化与并发限制。
