# 文档导航

本目录维护当前产品合同、运行架构与 Provider 专题说明。判断“现在是否支持某能力”时，优先级为：

```text
当前代码与 OpenAPI → 当前事实源文档 → 专题说明与路线图
```

设计目标不能替代实现证据，Mock 测试也不能替代真实 Provider 或成片验收。

## 当前事实源

| 文档 | 内容 |
| --- | --- |
| [00-product-requirements.md](00-product-requirements.md) | 产品范围、默认规格与锁定决策 |
| [01-user-workflow.md](01-user-workflow.md) | 剧本、资产库、视频制作三工作区流程 |
| [02-data-model.md](02-data-model.md) | 单集、Dialogue、Shot、状态变体和审计模型 |
| [03-agent-design.md](03-agent-design.md) | Agent 职责与输入输出边界 |
| [04-provider-adapter.md](04-provider-adapter.md) | LLM、图片、视频能力合同 |
| [05-generation-pipeline.md](05-generation-pipeline.md) | 生成链路与 Prompt 过期策略 |
| [06-review-and-regeneration.md](06-review-and-regeneration.md) | 人工采用、拒绝和局部重生成 |
| [07-ffmpeg-composition.md](07-ffmpeg-composition.md) | 原生音轨、静音补齐与导出字幕 |
| [08-frontend-workbench.md](08-frontend-workbench.md) | 前端三工作区布局 |
| [09-api-design.md](09-api-design.md) | 当前公共 API 合同 |
| [10-deployment.md](10-deployment.md) | 本地运行与共享部署边界 |
| [11-implementation-roadmap.md](11-implementation-roadmap.md) | 当前迭代与后续 Provider 验收 |
| [12-asset-management.md](12-asset-management.md) | 资产状态变体与版本管理 |
| [21-current-architecture.md](21-current-architecture.md) | 当前运行架构与限制 |
| [22-ltx23-video-integration.md](22-ltx23-video-integration.md) | LTX-2.3 视频 Adapter 合同与配置 |
| [24-script-prompt-quality-pipeline.md](24-script-prompt-quality-pipeline.md) | 剧本开发、审稿、定点修订与语义编译 |
| [25-demo-evidence.md](25-demo-evidence.md) | 可对外陈述的证据、样片清单和验收缺口 |
| [26-backend-task-runtime.md](26-backend-task-runtime.md) | 统一任务状态、租约、重试、取消与视频批次合同 |
| [27-minimax-h3-video-integration.md](27-minimax-h3-video-integration.md) | MiniMax H3 网关、自动模型路由与异步任务合同 |
| [28-反思式分镜导演Agent.md](28-反思式分镜导演Agent.md) | 分镜草案、资产观察、Reflection、受限 Patch 与任务恢复合同 |

## 求职与项目表达

| 文档 | 内容 |
| --- | --- |
| [AI-Agent开发岗项目简历.md](AI-Agent开发岗项目简历.md) | 面向 AI / Agent 开发岗的仓库证据分析与单版项目经历 |
| [应届生项目简历描述.md](应届生项目简历描述.md) | 可直接投递的项目经历、岗位微调、STAR 映射与事实边界 |
| [应届生项目面试问答.md](应届生项目面试问答.md) | 简历描述、STAR 表达、项目介绍与面试官逐层追问 |

前后端机器可读合同位于 [api/openapi.json](api/openapi.json)。路由或 Schema 变化后，应重新导出并执行合同检查。

## 维护规则

- 产品默认值变化：同步 `AGENTS.md`、`00-product-requirements.md`、前后端 Schema 与根 README。
- API 变化：同步 `09-api-design.md`、`api/openapi.json` 和前端合同检查。
- Provider 变化：分别记录 Adapter 实现、只读连通、真实生成与完整链路验收。
- 真实样片：只提交元数据和可公开链接，不把大体积媒体、密钥或内网地址提交到 Git。
