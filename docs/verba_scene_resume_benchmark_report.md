# VerbaScene（语境片场）秋招简历 Benchmark 最终报告

> 报告状态：**中止后的部分最终报告（Partial Final）**
> Benchmark 日期：2026-09-04
> 中止原因：按用户指令停止真实模型批次；未恢复生成调用，也未补跑缺失的 19 条脚本与分镜实验。
> 结论边界：本报告只聚合已落盘 raw result。`未执行` 不按 0 分处理，也不以局部样本外推全量结果。
>
> Benchmark 后修复（不计入本报告指标）：用户要求根据本轮耗时证据修复生产链路；现已为
> 结构化 LLM 阶段启用 JSON mode，为 OpenAI-compatible 文本调用增加 SSE 进度与调用中
> 尽力取消，并将剧本/分镜 Worker 默认并发从 1 调整为 2。后续又修复了租约 fencing
> 与 completion 幂等：无外部调用的定向复测中，stale write 拒绝从 0/10 变为
> 10/10，并发 duplicate completion 幂等从 0/10 变为 10/10。详见 7.4；这些数字
> 不回写原 Benchmark 基线。未重跑真实 LLM，因此下文
> 12 条样本的时延和质量数字仍是修复前证据，不是 before/after 结论。
> 随后用户授权 Codex 作为跨 Provider 第三方 Judge，对这 12 条已保存的
> `Draft / Final` 同源快照补做剧本 A/B；没有 C 组、没有新增真实生成调用。
> 用户随后确认“齐声”是合法表演指令，不应作为禁词；已从两条错误 gold 中移除，
> 并在保留原始盲评文件不变的前提下，通过 correction artifact 重算确定性指标。

## 执行摘要

本轮仍没有完成 31 条全量或分镜 Reflexion A/B，但已用已保存的 12 条真实
`kimi-k3` 剧本运行补做同源成对评审。它可以支持“这 12 条中观察到质量改善”，
不支持“Reflexion 已被统计证明普遍有效”或“分镜质量已提升”。

已经形成证据的部分如下：

- “31 组离线 Eval”真实含义是 **31 条静态输入与精确字符串/结构合同检查集**，不是 31 次历史运行，也没有历史 raw output。当前仅完成其中 12 条真实 `DashScope / kimi-k3` 脚本流水线运行。
- 修正错误 gold 后，12 条局部样本的精确匹配检查和当前生产脚本合同 Gate 均为 12/12；两者仍不能替代故事质量评价。
- 以同一 Draft 分叉为 A（Draft Only）和 B（Review + Bounded Patch）后，确定性
  Gate 是 12/12 vs 12/12，证明窄合同没有捕捉到这批语义缺陷。
- Codex 在隐藏 arm 标签后给出 **B 4 胜 / A 0 胜 / 8 平**；4 个实际修改样本
  全部选 B，但 95% exact CI 仍为 39.8%–100%，样本不足以外推。
- 非盲的修复审计观察到 must-fix 4/4 解决、14 个受保护元素中越界 0、
  4 个 Patch case 中新回归 0，8 个无 Patch case 为字节级 8/8 保持。这些是小样本事后 Judge 证据，不是人类 gold。
- B 的代价为 440,405 vs 266,645 tokens（+65.17%），单 case 中位时延为
  700.0 vs 527.5 秒（+172.5 秒）。
- Provider Adapter 契约测试 18/18 通过，但其中 15 条是 contract double、3 条是内置 mock，未调用真实外部 Provider。
- 70 次隔离 SQLite + Provider double 故障注入中，场景预期/合法收敛 50/70（71.43%），真正恢复到业务成功 30/70（42.86%）；同一幂等键 10/10 收敛，重复 Provider submit 为 0/70。
- 故障注入同时暴露两个确定性缺口：陈旧 Worker 写入 0/10 被拒绝；重复 completion 10/10 产生重复候选、重复资产和重复终态写入。
- Benchmark 后定向修复并独立复测 70 次：陈旧 Worker 写入 10/10 被拒绝，并发重复
  completion 10/10 复用同一份候选/资产，重复副作用与 false success 均为 0。
- 字幕仅保存了 7 条确定性 token 对齐样本；没有真实 ASR、没有完整 `silencedetect -> 分段 ASR -> 最终字幕时间轴` 验证，不足以形成字幕链路简历指标。

因此，本轮最可信的简历故事是：**建立同源 A/B、跨模型语义 Judge 与故障注入，
发现确定性 Gate 的语义盲区，在 12 条 pilot 中观察到 Reflexion 4 胜 0 负 8 平，
并将租约 fencing 与 completion 幂等的确定性缺陷闭环修复。**

## 1. Test Environment

### 1.1 代码与运行环境

| 项目 | 值 |
|---|---|
| Commit SHA | `35836acab5bfad9a22920e07dbf54ad1e92b0815` |
| Branch | `codex/frontend-task-adaptation` |
| Working tree | Dirty；环境快照列出大量 tracked 与 untracked 文件 |
| Tracked diff SHA-256 | `f5a012e877c67d01996179f01f0117fa83d1a98bffa8c17e46d7216b568cea7f` |
| Python | 3.13.9，macOS 26.4 arm64 |
| 真实质量模型 | `DashScope / kimi-k3` |
| Script capability | `multi-agent-script-v3.1` |
| Storyboard capability | `reflexion-shot-director-v2`（实现已审计，但本轮未实跑 A/B） |
| Benchmark capability | `verba-scene-resume-benchmark-v1` |
| 数据库 | 内容局部批次直接调用流水线；故障注入为每场景独立临时 SQLite |

重要复现限制：结果对应的是“Commit + 当时未提交工作树”，不是该 Commit 的纯净版本。环境快照只对 tracked diff 给出哈希，未完整固定 untracked 生产文件；且部分 harness 源码在产物生成后又增加了字段/样本，当前脚本哈希不等于所有已保存产物的精确生成版本。报告因此以 raw artifact 哈希为主要追溯依据，不宣称一键精确复现。

### 1.2 Real / mock / fault-injection matrix

| 模块 | 已保存样本 | 证据类型 | 外部调用 | 状态 |
|---|---:|---|---|---|
| Existing 31 Eval 局部运行 | 12/31 | 当前脏工作树上的真实 LLM | DashScope `kimi-k3` | 中止 |
| Script Reflexion A/B | 12 对 | 已保存真实 Draft/Final + Codex arm-label-blind Judge | 无新增调用 | 局部完成 |
| Storyboard Reflexion A/B | 0 | 无 | 无 | 未执行 |
| Bounded Patch 事后审计 | 4 个 Patch case / 5 个目标场景 | 已保存 Draft/Final + Codex 非盲修复审计 | 无新增调用 | 局部完成 |
| 完整 Story -> Storyboard Pipeline | 0 | 无 | 无 | 未执行 |
| Provider Adapter Contract | 18 | 15 contract doubles + 3 built-in mocks | 无 | 完成 |
| Fault Injection | 70 | 临时 SQLite + provider doubles | 无 | 完成 |
| Post-fix Task Safety | 70 | 临时 SQLite + provider doubles；completion 并发双投递 | 无 | 完成 |
| Subtitle Alignment | 7 | deterministic timestamp double | 无 | 局部完成 |
| 图片 / 视频 / ASR 真实生成 | 0 | 无 | 无 | 按测试策略未执行 |

`environment.json` 中的 evidence matrix 描述了计划口径，不代表对应实验都已完成；完成状态以上表和实际存在的 raw artifact 为准。

## 2. Existing 31 Eval Audit

### 2.1 来源与数据构成

- 数据集：`apps/api/evals/script_quality_cases.json`
- Evaluator：`apps/api/app/evals/script_quality.py`
- Runner：`apps/api/scripts/run_script_quality_eval.py`
- 固定 case 数：31
- 修正后数据集 SHA-256：`377e6f2a5c7c8f8aacb9d717d61705aae23c58f73dfc9cf095e1bd4dec8c2eac`
- 输入模式：21 条 `ai_brief`，10 条 `imported_script`
- 描述性 focus：55 个标签实例、48 个唯一标签
- 精确断言：7 个 required phrase、76 个 required token、46 个 forbidden token

Gold correction：原数据集曾在 `share-red-ball` 和 `distinct-character-voices` 中把
`齐声`列为 forbidden token。这个词既不能证明“机械问答”，也不能证明“角色声音
没有区分”，属于用词面代理语义质量的错误断言。两处现均已删除；原始 SHA-256
`824f4f935e587b625947cacb45c3c7d03cc377411de4aaa5b2b0534ac4f085b1` 只保留用于
历史 artifact 追溯。

覆盖主题包括指定短语、A1 对白、导入剧本事实、角色关系、视觉兑现、克制叙事、喜剧、Prompt Injection、长输入尾部事实、道具/地点/时间/服装连续性、对白意图、说话人归属、原生音效等。

### 2.2 Gold / assertion 到底是什么

每条 case 的可执行 gold 只有：

1. `required_phrases` 是否逐字出现在 `dialogues[].text`；
2. `required_tokens` 是否出现在序列化后的场景 JSON；
3. `forbidden_tokens` 是否未出现；
4. 当前脚本合同追加的结构检查，例如场景/说话人/指定短语/制作字段语言。

`title` 与 `focus` 只是元数据，Evaluator 并未根据它们评价因果性、人物声音、自然度、情绪兑现、是否真正抵抗 Prompt Injection 等语义质量。数据集没有 gold screenplay、逐字段期望资产 ID、人工评分或成对偏好标注。因此它适合做**内容约束回归**，不等价于完整故事质量 Eval。

仓库存在 blind pairwise packet 的构建函数，但没有找到已填写的人评 packet 或历史 A/B judgement。

### 2.3 历史结果与当前复现

- 未发现 31 条历史 raw output 或 summary。
- 因此不存在可标为 `Archived Reaggregation` 的历史聚合。
- 当前真实运行完成 12/31（38.71%）后被停止；19 条未运行。
- 当前部分结果不能称为“31 组当前版本 Eval 结果”。

### 2.4 12 条真实 LLM 局部结果

| 指标 | 结果 | 解释 |
|---|---:|---|
| Script 子流水线完成 | 12/12 | Blueprint -> Draft -> Review -> 可选 Patch 完成；不含 Storyboard |
| 修正后精确匹配 case pass | 12/12（100%） | 原唯一失败是错误 gold 对 `齐声` 的误判，现已撤销 |
| 当前生产脚本合同 Gate pass | 12/12（100%） | 与修正后精确匹配均通过，但合同仍只覆盖窄结构/字符串约束 |
| 最终结构化输出有效 | 12/12 | 最终结果可解析且合同有效 |
| Provider phase calls | 41 | 41 次阶段调用均返回 succeeded |
| Patch 被采用 | 4/12 | 共覆盖 5 个目标场景 |
| Reviewer must-fix / declared resolved | 4 / 4 | 同一流水线自审自修，不是独立 gold |
| Fallback | 2 个 case / 2 次 | 1 次 Blueprint 解析降级，1 次 Draft structure recovery |
| Token usage | 440,405 | 平均 36,700 tokens/case |
| 单 case 流水线时延 | 365.0–1050.3 秒；中位 700.0 秒 | Provider/模型与多阶段调用混合结果，不是服务 SLA |

修正后，已完成子集包含 3/3 required phrase、29/29 required token，以及 19/19
forbidden-token absence。它们仍不是“全部对白覆盖率”或“实体引用准确率”。

## 3. Reflexion A/B Benchmark（12-case 剧本局部对照）

### 3.1 设计与盲评边界

已保存的每条真实流水线 raw 同时保留 `parsed_output.draft` 和
`parsed_output.final`，因而可以在不重跑模型的前提下构造真正的同源分叉：

- Arm A：同一条 `Blueprint -> Draft` 的 Draft 快照；
- Arm B：该 Draft 继续经过 `Review -> 最多一次 Bounded Patch` 的 Final 快照；
- 没有 C 组，没有新增 Provider 调用；
- 生成模型是 `DashScope / kimi-k3`，语义 Judge 是当前 OpenAI Codex 会话；
- Judge 在成对结论落盘并哈希锁定前只读取匿名 `left/right`，之后才打开
  answer key 做 must-fix 和越界审计。

这不是完全双盲：Judge 先前已参与项目审计并见过 Critic 问题摘要，只隐藏了
arm 身份；并且只有一个模型 Judge、一轮判定，没有人类 gold。所以证据类型必须写成
`post-hoc arm-label-blind cross-provider LLM judgement`。
开答案前落盘的 `blind_judgements.json` SHA-256 为
`bab3814466eda875b5b8fbaf3697cbc7dcc146934008e8fb1e1a31ba1541960b`；后续 fix audit
显式引用该哈希，汇总工具会拒绝哈希不匹配或未声明“先锁定、后开答案”的输入。

### 3.2 A/B 结果

| Metric | Draft Only（A） | Reflexion + Patch（B） | B - A / 观察 |
|---|---:|---:|---:|
| 修正后精确匹配 Gate Pass | 12/12（100%） | 12/12（100%） | 0 |
| Final Structured Output Validity | 12/12 | 12/12 | 0 |
| Required Phrase Coverage | 3/3 | 3/3 | 0 |
| Required Token Coverage | 29/29 | 29/29 | 0 |
| Forbidden Token Absence | 19/19 | 19/19 | 0 |
| 盲评胜 / 负 / 平 | 0 / 4 / 8 | **4 / 0 / 8** | B 在全部 12 对中胜 33.33% |
| 仅看实际发生变化的 4 对 | 0 胜 | **4 胜** | 4/4；95% exact CI 39.76%–100% |
| 平均 Story Coherence 差值 | — | — | +0.33 |
| 平均 Production Readiness 差值 | — | — | +0.58 |
| 平均 Overall 差值 | — | — | **+0.50** |
| 独立审计 Must-Fix Resolution | N/A | 4/4 | 95% exact CI 39.76%–100% |
| 受保护元素越界 | N/A | 0/14 | 95% exact CI 上界 23.16% |
| Patch 新回归 | N/A | 0/4 case | 95% exact CI 上界 60.24% |
| No-op Preservation | N/A | 8/8 | 95% exact CI 63.06%–100% |
| 总 Token | 266,645 | 440,405 | +173,760（+65.17%） |
| 单 case 时延 P50 | 527.5 s | 700.0 s | +172.5 s（2:52.5） |

4 个 B 胜的具体修复为：补齐第 5 场蓝色积木的 `props`、解决 Leo 双手已占满却要按
青蛙和合盖的物理矛盾、让 Leo 离校时带走早先放下的书包，以及把中英混杂的
`并排的 footsteps 声` 修为 `并排的脚步声`。8 个 Tie 不是 Judge 无法区分，而是
Reflexion 未触发 Patch，A/B 字节级相同。

Gold correction 不改变 4/0/8 的相对偏好：涉及 `齐声` 的两个 pair 都是字节级
相同，删除双方共有约束后仍必然为 Tie；每个 B-A 分数差也保持不变。但
`share-red-ball` 的原始绝对分数受到错误禁词影响，因此本报告撤回 A/B 的绝对均分，
只保留对称修正下不变的差值。原盲评文件不重写，错误结论由
`gold_correction_20260904.json` 显式覆盖。

最关键的反常识结论是：**确定性 Gate 提升为 0，但语义 Judge 在 4 个实际修改样本上
全部选 B。** 这不是矛盾，而是说明当前 Gate 主要检查字符串与结构，没有检查
道具持有、双手物理可执行性等语义连续性。

结论仍然要保守：可以说“12 条 pilot 中 Reflexion 观察到 4 胜 0 负 8 平，平均
Overall +0.50/5”；不能说“Reflexion 胜率 100%”或“已显著提升质量”。未执行的
Storyboard A/B 仍不能从这批剧本结果外推。

## 4. Bounded Patch Benchmark

### 4.1 实现审计

剧本路径的边界是**场景级**：Reviewer 的 `must_fix.scene_nos` 决定允许替换的场景；Parser 拒绝未知、重复或不在允许集合中的场景；应用阶段只替换列出的场景。分镜路径同样只允许对 Reflection 授权的 `shot_nos` 执行限定 operation，并在合并后重新解析、校验对白/实体引用、内部镜头数与时长合同。

这不是字段级 Patch。只要目标场景/片段被授权，模型可以重写其中多个字段；当前合同没有用字段 diff 强制保护“目标场景内但未要求修改”的内容。因此简历中宜写“场景/片段级受限修订”，不宜写“字段级精准 Patch”或“零越界修改”。

### 4.2 12-case 事后修复审计

12 条真实脚本样本里共有 4 个 Patch case、5 个目标场景；其余 8 条没有 Patch，
Draft/Final 字节级相同。打开盲评 answer key 后，Codex 对 Critic 提出的 4 个
must-fix 和 Patch diff 做了非盲审计：

| 指标 | 观察结果 | 95% exact CI / 边界 |
|---|---:|---|
| Must-Fix Resolution | 4/4 | 39.76%–100% |
| Protected Element Preservation | 14/14 | 76.84%–100% |
| Out-of-Scope Modification | 0/14 | 上界 23.16% |
| Patch Regression | 0/4 case | 上界 60.24% |
| No-op Preservation | 8/8 | 63.06%–100% |
| 未授权场景变更 | 0/23 | 辅助结构性观察 |

这比只读取 Patch 自报的 `resolved_issue_ids` 更强：第三方 Judge 逐条核对了修复内容，
也确认 4 个 Patch case 均未出现可见的新冲突。不过它仍不是预注册的正式 Bounded
Patch Benchmark：must-fix 来自生成模型自己的 Critic；14 个 protected elements 是
事后根据 Critic 约束抽取的，不是预先冻结的人类 gold；样本仅 4 个 Patch case，
置信区间很宽；目标场景内部也仍没有完整字段级不可变合同。因此只能报告上述
**观测分子/分母与区间**，不能外推成“零越界”或“零回归”的普遍保证。

## 5. Multi-Agent Pipeline Eval

仓库的“Multi-Agent”真实实现是一个显式阶段编排：

- 剧本：Story Blueprint -> Script Draft -> Script Review -> 最多一次 Script Patch -> Deterministic Contract；
- 分镜：Asset Check -> Storyboard Draft -> Reflection -> 最多一次 Shot Patch -> Deterministic Contract；
- 阶段通过 Provider Adapter 调用角色化 Prompt，并以 checkpoint 保存中间状态；它不是一组长期自治、拥有独立记忆并互相自由通信的 Agent。

本轮未执行要求的 20–30 条完整 `Story Planning -> Script -> Review -> Revision -> Storyboard` 任务，因此完整 Pipeline Completion、Stage Contract Validity、Final Gate、Revision Turns、Human Intervention 和 Stage Failure Distribution 均不可计算。

可以报告的只有脚本子流水线 pilot：12/12 完成，41 个阶段调用无 Provider 状态失败，2/12 触发解析降级并最终通过脚本合同。它不包含实体提取、资产解析和 Storyboard，不能写成端到端 Multi-Agent Pipeline 100% 完成。

## 6. Provider Adapter Contract

生产抽象 `ProviderAdapter` 明确定义 `submit`，并为异步能力提供 `poll / cancel / fetch_result`；同步 LLM/图片 Adapter 可以只实现 `submit`，默认 `poll/cancel` 会抛出不支持。由此，合同的准确表述是“统一请求/响应与可选异步生命周期”，而不是“所有 LLM、图片、视频 Provider 都原生支持四个操作”。

### 6.1 结果

| 指标 | 结果 |
|---|---:|
| Case count | 18 |
| Contract pass | 18/18（100%） |
| State transition correctness | 18/18（100%） |
| Unhandled exception | 0/18 |
| Invalid terminal state | 0/18 |
| Operation coverage | `submit / poll / cancel / fetch_result` |
| Evidence | 15 contract doubles + 3 built-in mock adapters |
| Real external calls | 0 |

场景覆盖 queued/running/succeeded、poll/submit timeout、submit 结果不确定、Provider cancelled、运行前/运行中 cancel、缺失结果、重复结果、畸形响应和 unsupported cancel。

结论：该结果证明 harness 中的统一类型和状态语义自洽，且内置 mock 能进入统一入口；它**不证明每个真实 DashScope、MiniMax、Seedance、Wan、LTX 等 Adapter 在真实网络下 100% 合同正确**。

## 7. Fault Injection / Recovery

### 7.1 总体结果

每个场景执行 10 次，共 7 × 10 = 70 次。全部使用隔离临时 SQLite 与 Provider double，无真实外部调用。

| 指标 | 结果 | 口径 |
|---|---:|---|
| Scenario expectation / legal convergence | 50/70（71.43%） | 包含业务成功与安全收敛 |
| Business recovery success | 30/70（42.86%） | 最终恢复为业务成功 |
| Safe containment | 20/70（28.57%） | 未完成业务，但避免危险重试/错误产物 |
| Duplicate Provider submission | 0/70（0%） | 本 harness 的逻辑重复 submit |
| Duplicate resource creation | 10/70（14.29%） | 全部来自 duplicate completion |
| Duplicate terminal write | 10/70（14.29%） | 全部来自 duplicate completion |
| Lost task | 0/70 |
| Unhandled exception | 0/70 |
| False success | 20/70（28.57%） | stale write 与 duplicate completion |
| Idempotency | 10/10（100%） | duplicate submission 专项机会 |
| Stale worker write rejection | 0/10（0%） | stale worker 专项机会 |

`Recovery Success Rate = 71.43%` 容易被误读。它不是“71.43% 最终生成成功”：其中 20 次是安全收敛，真正业务成功只有 42.86%。

### 7.2 分场景结果

| 场景 | 预期通过 | 业务成功 | 安全收敛 | 结论 |
|---|---:|---:|---:|---|
| Duplicate submission | 10/10 | 10/10 | 0/10 | 同幂等键只形成 1 个任务、1 次 submit |
| Crash before submit | 10/10 | 10/10 | 0/10 | 可重新领取并按 harness 完成 |
| Provider accepted, local ack lost | 10/10 | 0/10 | 10/10 | 标为 submission unknown/failed，不盲目重提；没有业务恢复 |
| Worker crash during poll | 10/10 | 10/10 | 0/10 | 持久化远端 ID 后接管轮询，1 个候选 |
| Stale worker write | 0/10 | 0/10 | 0/10 | 旧 Worker 可覆盖新 Lease；缺少 fencing |
| Duplicate completion | 0/10 | 0/10 | 0/10 | 每次均落出 2 个 candidate + 2 个 asset |
| Cancel race | 10/10 | 0/10 | 10/10 | 最终 cancelled，无候选，远端 cancel 1 次 |

### 7.3 根因与工程含义

1. **Lease 只保护 claim/heartbeat，不保护终态写。** `heartbeat/release_lease` 带 owner 条件，但 `TaskRepository.finish()` 直接修改 ORM 对象，没有校验 owner、lease version 或 fencing token；旧 Worker 因而能写入终态。
2. **完成落库没有 source-task 幂等约束。** `Asset` 与 `AssetCandidate.source_task_id` 只有普通索引，没有唯一约束；重复 delivery 会再次创建 candidate/asset，并再次写终态。
3. **Ack lost 选择安全失败而非自动恢复。** 这是合理的防重复策略，但缺少按远端请求键查询/对账的 reconciliation，所以不能称为恢复成功。

正面结论是 Idempotency-Key、活动任务唯一约束和“远端 ID 已持久化后只 poll”在对应 harness 中有效；负面结论是 Lease/Heartbeat 尚不足以构成完整的多 Worker exactly-once 或 fenced completion 语义。

P50 4.9 ms、P95 17.3 ms 仅是进程内 provider double + 临时 SQLite 的故障到持久化观察时延，不代表线上恢复 SLA，不建议写简历。

### 7.4 Benchmark 后定向修复与复测（不回写基线）

修复没有把 `source_task_id` 直接设为唯一，因为宫格分镜等任务会合法产出多个资产。
实现改为两层不变量：

1. 每次 claim 生成唯一 `lease_token`；Handler 事务在 flush/commit 前以
   `task_id + lease_owner + lease_token + 未过期` 做原子条件写和行锁。同一 Worker
   名称重新领取也会获得新 token，旧执行无法冒用。
2. completion 先锁定任务行，已成功时直接复用原 `result_payload`；Asset 和
   AssetCandidate 再按“任务 + 逻辑产物槽位”生成 `completion_key` 并施加唯一约束。

| 定向指标 | 修复前基线 | 修复后复测 |
|---|---:|---:|
| Stale worker write rejection | 0/10 | 10/10 |
| Duplicate completion idempotency | 0/10 | 10/10 |
| Duplicate completion 候选 / 资产计数 | 每次 2 / 2 | 每次 1 / 1 |
| 并发 completion 异常 | 未测 | 0/10 |
| 7 类故障场景预期通过 | 50/70 | 70/70 |
| 业务恢复成功 | 30/70 | 50/70 |
| Lost task | 0/70 | 0/70 |
| Duplicate side effects / false success | 20 / 20 | 0 / 0 |

复测将 duplicate completion 改为两线程并发双投递，仍使用临时 SQLite + Provider
double，真实外部调用为 0。因此这证明了本地事务和数据库约束下的回归修复，
不等于已获得多实例 PostgreSQL 线上 exactly-once 保证。
新 Worker 在拒绝旧 Worker 写入后继续完成任务，因而修复后 lost task 为
0/70；duplicate completion 成功复用唯一产物，因而修复后业务恢复为
50/70（71.43%）。

工程回归另外通过当前工作树的 280 项 Python 全量测试，PostgreSQL
迁移已验证到 `7a8192b3c4d5 (head)`；这是修复验证，不计入 Benchmark case 总数。

复测证据：`apps/api/outputs/verba_scene_resume_benchmark_20260904/post_fix_task_safety/`。

## 8. Subtitle Fallback

已保存 artifact 包含 7 条确定性 token alignment case、10 条目标对白：7 条被对齐，3 条按预期保留给 fallback；case decision 7/7 符合期望，12 个已定义边界样本的 MAE 为 31.7 ms。

但这批数据只直接测试 `align_dialogues_to_tokens`：

- 没有真实 ASR；
- 没有保存 ASR error、纯文本 ASR、真实/模拟 `silencedetect` 分段的完整结果；
- 没有把未对齐对白继续送入最终 deterministic timeline 并验证成品 cue 完整性；
- 因此不能计算 Subtitle Pipeline Completion、Valid Final Timeline 或 Missing Dialogue Rate。

仓库实现中确有 `word timestamp -> token alignment`、纯文本时的 FFmpeg `silencedetect` 分段、失败后确定性回退等路径，但“代码存在”不能替代端到端 Benchmark。当前 7/7 只适合作为 helper-level correctness 记录，不建议写成字幕链路成功率。

## 9. Weak / Misleading Metrics

以下表述虽然部分来自真实数字或现有代码，但不应直接写进简历：

- **“31 组离线 Eval 通过”**：31 是数据集条数，不是已执行通过数；本轮仅完成 12 条。
- **“Reflexion 提升了确定性 Gate Pass”**：修正 gold 后 A/B 均为 12/12，增量仍是 0；改善只被语义 Judge 捕捉到。
- **“Reflexion 胜率 100%”**：只能说 4 个实际修改样本中 B 为 4/4；全部 12 对的结果是 B 4 胜、A 0 胜、8 平，且 4/4 的 95% exact CI 为 39.76%–100%。
- **“Must-Fix 修复率普遍为 100%”**：第三方事后审计观察到 4/4，但问题由生成模型自己的 Critic 提出、没有人类 gold，样本也很小。
- **“越界修改率 / 回归率为 0%”**：只观察到 protected elements 0/14 越界、Patch case 0/4 新回归；其 95% exact 上界仍分别为 23.16% 和 60.24%。
- **“Multi-Agent 端到端完成率 100%”**：12/12 仅是脚本子流水线，不含 Storyboard。
- **“Provider Adapter 线上成功率 100%”**：18/18 全为 doubles/mocks。
- **“任务恢复成功率 71.43%”**：该口径包含 20 次安全失败；业务成功只有 42.86%。
- **不带时间边界的“Lease/Heartbeat 防止陈旧写入”**：原基线实际为 0/10；只能写
  “修复后本地故障注入 10/10”，不能外推线上 SLA。
- **不区分活动任务与完成产物的“资源唯一约束防重”**：原 completion replay 0/10
  幂等；修复后的 10/10 是“任务行锁 + 逻辑产物 completion key”的定向证据。
- **“字幕链路 100% 成功 / ASR 准确率”**：仅有 7 条 deterministic alignment helper case，无真实 ASR 和最终时间轴验证。
- **“P95 恢复 17 ms”**：来自本地临时 SQLite 与 provider double，不是 SLA。
- **“235 项全量测试通过”**：这是旧文档数字，不再使用。修复后当前工作树实际为 280/280，但它是工程回归，不是模型质量 Benchmark 指标。

## 10. Resume-worthy Metrics

### 10.1 证据与岗位匹配分析

主要证据来自 `apps/api/app/agents`、`apps/api/app/production/shot_direction`、`apps/api/app/platform/tasks`、`apps/api/app/providers`、`apps/api/app/exports`、31-case 数据集，以及本轮保存的 JSON/CSV raw result。项目最匹配 **AI/LLM 应用工程 + 后端可靠性**：阶段化 Agent 工作流、确定性合同、Provider 抽象、持久化任务状态与故障注入均有代码证据。

强支持的量化事实是 70 次基线 + 70 次修复后隔离故障注入、stale write 与
duplicate completion 各从 0/10 修复到 10/10、0/70 重复 Provider submit、18/18
double/mock Adapter 合同，以及 12 条真实 LLM pilot 的剧本合同 12/12。Reflexion
A/B 可作为次级证据：跨 Provider、arm-label-blind Judge 给出 B 4 胜 / A 0 胜 /
8 平、平均 Overall +0.50/5，但只有 4 个非相同 pair，且额外消耗 65.17% tokens。
字段级 Bounded Patch、完整 Pipeline 与真实字幕链路仍需保守表述。

### 10.2 最适合写的 3–5 个数据

1. **70 次、7 类故障注入**；注明“隔离 SQLite + Provider double”。
2. **同一 Idempotency-Key 10/10 收敛为同一任务与一次 Provider submit**。
3. **重复 Provider submit 0/70、lost task 0/70、unhandled exception 0/70**；必须与 double/fault-injection 限定词同句出现。
4. **18/18 Provider Adapter 生命周期契约通过**；注明 15 contract doubles + 3 built-in mocks，覆盖四类操作。
5. **stale write 与并发 duplicate completion 各由 0/10 修复到 10/10**；注明临时
   SQLite + Provider double，不写成线上 exactly-once。真实 LLM pilot 数据可作次选补充。

若岗位更偏 LLM 应用而非可靠性，可将第 3 项替换为：**12 条同源剧本 A/B 中，
跨 Provider Judge 给出 Reflexion 4 胜 / 0 负 / 8 平，平均 Overall +0.50/5；代价为
Token +65.17%**。必须保留“12 条 pilot”和成本，不能简写成“胜率 100%”。

同时建议在面试中主动说明基线曾暴露 stale write 0/10 被拒绝、duplicate completion
0/10 幂等，以及后续如何用 claim token、事务行锁和逻辑产物唯一键将两项都修复到
10/10。能讲清失效机制、设计取舍和复测边界，比只报一个最终数字更可信。

### 10.3 唯一推荐的简历版本

**VerbaScene（语境片场）｜AI 英语动画短剧生产工作台**

技术栈：`Multi-Agent / Reflexion、FastAPI、SQLAlchemy、SQLite/PostgreSQL、React + TypeScript、FFmpeg`

- 项目构建故事规划、编剧、审校与场景级受限修订流程，以确定性合同约束结构化输出。
- 在 12 条真实 LLM 同源 pilot 上执行盲标签 A/B，Reflexion 获 4 胜 / 0 负 / 8 平、
  平均 Overall +0.50/5，并量化 Token 开销 +65.17%（单一跨 Provider 模型 Judge）。
- 以 Provider Adapter 和数据库任务状态机统一异步生成，支持幂等键、租约心跳与取消恢复。
- 完成 18 例 Adapter 生命周期契约测试，覆盖 submit/poll/cancel/fetch_result 及异常状态（测试替身）。
- 构建 7 类、140 次基线/回归故障注入，将陈旧 Worker 写入拒绝与并发完成幂等均由
  0/10 修复至 10/10（临时 SQLite + Provider double）。

## 11. A–R 最终汇报

| 项 | 最终结论 |
|---|---|
| A. 现有 31 组 Eval 的真实含义 | 31 条静态脚本约束 case；21 AI brief、10 imported script。可执行 gold 是 required phrase/token、forbidden token 与结构合同，不是 31 次历史结果，也不是完整语义质量评价。 |
| B. 实际测试 case 数 | 基线保存 107 行：12 个真实 LLM、18 个 Adapter contract、70 次 fault、7 个字幕 alignment；另保存 70 次修复后 fault 回归，并对 12 个已保存 Draft/Final pair 补做 A/B Judge。合计 189 个异质证据行，不能混算成一个通过率。 |
| C. Draft Only Gate Pass Rate | 修正错误禁词后为 12/12（100%），基于已保存 Draft 和精确匹配 evaluator。 |
| D. Reflexion + Patch Gate Pass Rate | 修正后为 12/12（100%），相对 A 增量为 0；生产脚本合同同样为 A/B 12/12。 |
| E. Must-Fix Resolution Rate | 第三方事后审计观察到 4/4，95% exact CI 39.76%–100%；非人类 gold，不外推。 |
| F. Out-of-Scope Modification Rate | protected elements 观察到 0/14，95% exact 上界 23.16%；另有未授权场景变更 0/23。 |
| G. Regression Rate | Patch case 观察到 0/4，95% exact 上界 60.24%；样本不足以证明零回归。 |
| H. Dialogue Coverage / Entity Accuracy | 未测。局部仅有 required phrase 3/3、required token 29/29；没有完整对白 gold 或结构化实体/资产 gold。 |
| I. Pipeline Completion Rate | 完整 Story-to-Storyboard 未测；脚本子流水线 pilot 为 12/12。 |
| J. Fault Injection 总运行数 | 基线 70（7 场景 × 10）+ 修复后回归 70，合计 140。 |
| K. Recovery Success Rate | 场景预期/合法收敛 50/70（71.43%）；其中业务恢复 30/70（42.86%）、安全收敛 20/70（28.57%）。 |
| L. Duplicate Provider Submission Rate | 0/70（provider double fault injection）。 |
| M. Duplicate Terminal Write / Resource Rate | 基线各 10/70（14.29%）；修复后回归各 0/70，duplicate-completion 并发专项 10/10 幂等。 |
| N. Idempotency | 提交幂等 10/10；修复后并发 completion 幂等 10/10，候选与资产均保持 1 份。 |
| O. Top Failure Categories | 基线：stale terminal write（10）、duplicate completion（10）、ack lost 无业务恢复（10，安全失败）；前两项已在修复后回归中清零。 |
| P. 最适合写简历的数据 | 140 次基线/回归故障注入；两项确定缺陷各从 0/10 修复至 10/10；0/70 重复 submit；18/18 Adapter double/mock contract；LLM 岗可补充 12-pair pilot 的 4 胜 / 0 负 / 8 平和 +65.17% Token 成本。 |
| Q. 不能写的数据 | 31 组通过、Reflexion“100% 胜率”或统计显著、完整 31-case/Storyboard A/B、字段级零越界/零回归、完整 Pipeline 100%、线上 Provider SLA、真实 ASR/字幕成功率，以及已过时的“235 项全测”数字。 |
| R. 原始结果路径 | `apps/api/outputs/verba_scene_resume_benchmark_20260904/`；真实 LLM raw 位于 `_work/existing31/`，A/B 证据位于 `script_reflexion_ab_codex_judge/`。 |

## 12. Raw Results 与追溯

报告使用以下落盘证据：

- `apps/api/outputs/verba_scene_resume_benchmark_20260904/environment.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/partial_existing_31_summary.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/_work/existing31/*.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/provider_contract.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/provider_contract.csv`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/provider_contract_summary.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/fault_injection.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/fault_injection.csv`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/fault_injection_summary.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/post_fix_task_safety/fault_injection.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/post_fix_task_safety/fault_injection.csv`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/post_fix_task_safety/fault_injection_summary.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/subtitle_eval.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/subtitle_eval.csv`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/subtitle_eval_summary.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/judge_rubric.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/blind_packet.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/blind_answer_key.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/blind_judgements.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/fix_audits.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/deterministic_ab.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/script_reflexion_ab.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/script_reflexion_ab.csv`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/script_reflexion_ab_summary.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/provenance.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/script_reflexion_ab_codex_judge/gold_correction_20260904.json`
- `apps/api/outputs/verba_scene_resume_benchmark_20260904/artifact_manifest.json`
- `apps/api/scripts/run_script_reflexion_judge.py`（A/B prepare / summarize 工具）

明确缺失：`existing_31_eval.json/csv` 全量聚合、完整 31-case 和 Storyboard 的
`reflexion_ab.json/csv`、预注册 `bounded_patch.json/csv`、`pipeline_eval.json/csv`。
当前 `script_reflexion_ab_codex_judge/` 只是 12 条已保存剧本快照的事后补评。
Provider/Fault/Subtitle 旧产物行也没有统一的 `arm` 字段，未完全满足原始结果 schema；
报告不回写或美化原始数据。
`partial_existing_31_summary.json`、原 `blind_judgements.json` 与原 A/B summary 仍保留
错误 gold 存在时的历史内容和哈希；凡涉及 `齐声`、11/12 Gate、20/21 forbidden
absence 或原绝对均分的结论，均由 `gold_correction_20260904.json` 覆盖。
