# AI Agent 开发岗项目简历

## 证据判断

- 主要证据来自 `apps/api/app/agents/`、`apps/api/app/production/shot_direction/`、统一任务运行时、Provider 合同及对应测试。
- 与 Agent 开发岗最匹配的是：Prompt 与上下文编排、生成/审稿角色隔离、结构化输出、确定性校验、阶段追踪、断点恢复和失败降级。
- 强证据包括两条多阶段 Agent 流水线、Reflexion 分镜流程、受限 Patch、输入快照、阶段检查点和 Provider 提交状态语义。
- 当前没有 RAG、向量数据库、工具调用或 LangGraph，因此简历不写这些关键词，也不包装成通用多智能体平台。

## 可直接投递版

**VerbaScene（语境片场）｜可恢复式 AI 短剧 Agent 工作台**

面向 AI 短剧创作，将剧本与资产上下文转为可编辑分镜，重点解决 LLM 输出不可控、长链路不可恢复及失败后全量重做问题。

**技术栈：** `Python / FastAPI / Agent Workflow / Prompt Engineering / Structured Output / OpenAI-compatible LLM`

- 构建 Reflexion 分镜 Agent，串联资产观察、草案生成、规则校验、独立审稿与至多一次定点修订。
- 定义 `ShotDraft`、`ReflectionReport`、`ShotPatch` 等结构化合同，以 ID 白名单、对白覆盖和 Patch 授权范围约束模型幻觉与越界修改。
- 冻结剧本与资产上下文并持久化阶段检查点，按 Provider 提交状态恢复或降级，避免服务重启后重复调用。
- 将“蓝图—编剧—审稿—修订”剧本 Agent 接入同一运行时；后端全量 235 项自动化测试通过。
