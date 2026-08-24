# VerbaScene（语境片场）Agents 总纲

## 1. 项目定位

本项目是一个面向内部内容生产的动画风格启蒙英语短剧生产工具。

核心目标是将「AI 创意描述或已有剧本」自动改编为单集动画英语短剧。系统保留强可控的人工编辑、版本采用和重生成能力，但不使用阶段门禁阻止用户进入其他工作区。

第一阶段优先内部使用，后续再考虑商业化能力。

## 2. 已确认核心需求

- 内容方向：动画风格启蒙英语短剧
- 默认英语级别：CEFR A1；不限制受众年龄
- 输入：AI 创意描述或已有剧本
- 输出：单集动画短剧
- 默认规格：16:9，854x480；可切换 9:16，480x854
- 单集时长：1-3 分钟
- 视频元素：英文对白、原生音效、可选导出字幕
- 对白：英文必填，中文释义可选；对白与音效提示直接编译进视频 Prompt
- 音频：由视频模型生成原生音轨，不再使用独立 TTS 或音效生成
- 字幕：默认不烧录；导出时可选择英文或中英双语
- 暂不支持：片头片尾、封面、完整商业化计费
- 生成路径：支持「分镜图 -> 图生视频」和「文本/参考图 -> 视频」
- 默认主路径：角色/场景参考图 + 文本 Prompt 生成视频；片段首帧是显式可选输入，不自动发送
- 工作区：剧本、资产库、视频制作可自由进入；运行中只防止同一资源重复提交
- 项目管理：必须支持项目、章节、资产、任务和导出管理
- 模型接入：采用 Provider Adapter，不绑定单一供应商

## 3. 总体生产流程

```text
创建项目（AI 创意 / 导入剧本）
  -> 生成并编辑英语剧本
  -> 提取并编辑角色/场景/道具及状态变体
  -> 生成并选择角色/场景参考资产版本
  -> 拆解视频片段及内部镜头节拍
  -> 编译含英文对白和音效提示的最终视频 Prompt
  -> 生成并采用视频候选
  -> 选择字幕模式并导出成品
```

## 4. 核心模块目录

后续具体设计均放入 `docs/` 目录，每个模块单独维护 Markdown 文档。

```text
docs/
  00-product-requirements.md
  01-user-workflow.md
  02-data-model.md
  03-agent-design.md
  04-provider-adapter.md
  05-generation-pipeline.md
  06-review-and-regeneration.md
  07-ffmpeg-composition.md
  08-frontend-workbench.md
  09-api-design.md
  10-deployment.md
  11-implementation-roadmap.md
  12-asset-management.md
```

## 5. 建议项目结构

```text
verbascene/
  apps/
    web/
    api/
  docs/
  storage/
    projects/
    assets/
    exports/
  scripts/
  tests/
  docker/
  .env.example
  README.md
```

## 6. Agent 总览

具体职责、输入输出、Prompt 和依赖关系见 `docs/03-agent-design.md`。

- Project Planner Agent
- Script Adaptation Agent
- Entity Extraction Agent
- Character Consistency Agent
- Scene Design Agent
- Shot Breakdown Agent
- Prompt Engineering Agent
- Image Generation Agent
- Video Generation Agent
- Subtitle Agent
- Composition Agent
- Review Agent
- Asset Resolver

## 7. 技术路线

具体技术选型和模块细节以后续设计文档为准。

- 架构：前后端分离
- 前端：React + TypeScript + Vite
- 后端：FastAPI
- 工作流：当前使用领域服务与显式状态模型；只有复杂度被真实场景证明后再评估 LangGraph
- 异步任务：当前使用持久化任务记录与进程内队列；多人/多实例阶段再迁移 Celery + Redis
- 数据库：本地 SQLite；共享部署可切换 PostgreSQL
- 存储：本地文件系统，后续兼容 MinIO/S3/OSS
- 合成：FFmpeg
- 实时状态：任务查询；后续按需要引入 SSE 或 WebSocket 事件流

## 8. 实施原则

- 文档先行：核心模块实现前，必须先有对应设计文档。
- Provider 可替换：模型能力必须通过 Provider Adapter 接入。
- 自由导航：工作区不以阶段确认作为进入条件。
- 人工可控：关键内容可编辑、可采用版本、可重生成，Prompt 过期不自动覆盖人工稿。
- 资源可追溯：图片和视频需要记录来源、Prompt、Provider、引用资产和任务状态。
- 支持重生成：单个角色状态、场景状态、可选片段首帧和视频片段都应可重生成；道具默认只保留结构化剧情数据。
- 保留版本：重生成产生新版本，不直接覆盖历史资源。
- 合成可重复：同一组已确认资源应能重复合成出一致结果。
- 先内部可用，再商业化。
- 最小充分验证：默认只运行与本次改动直接相关的定向测试，并在需要时执行一次对应的构建或类型检查。
- 禁止过度测试：不得为了“更放心”而重复执行全量测试、重复构建、重复健康探测或与改动无关的检查。同一项验证通过后，若相关代码未再变更，不得重跑。
- 全量测试门槛：只有在修改共享基础模块、数据库结构、公共 API 合同、进行跨模块重构，或定向测试暴露出跨模块回归风险时，才允许执行全量测试；用户明确要求时除外。
- 生成调用门槛：不得仅为验证 Adapter 而自动调用付费或高耗时的图片、视频、TTS 生成接口。合同测试应优先使用 mock；真实生成必须由用户明确要求，或属于用户明确要求的端到端验收。
- 验证结果应区分“定向测试通过”、“全量测试通过”、“只读连通性已验证”和“真实生成已验证”，不得混为同一结论。

## 9. 文档生成顺序

1. `docs/00-product-requirements.md`
2. `docs/01-user-workflow.md`
3. `docs/02-data-model.md`
4. `docs/03-agent-design.md`
5. `docs/04-provider-adapter.md`
6. `docs/05-generation-pipeline.md`
7. `docs/06-review-and-regeneration.md`
8. `docs/07-ffmpeg-composition.md`
9. `docs/08-frontend-workbench.md`
10. `docs/09-api-design.md`
11. `docs/10-deployment.md`
12. `docs/11-implementation-roadmap.md`
13. `docs/12-asset-management.md`

完成核心设计文档后，再开始项目脚手架和代码实现。
