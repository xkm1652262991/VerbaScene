import type { GenerationTask } from "../../types/stageFive";

type JsonObject = Record<string, unknown>;

type IssueStatus = "resolved" | "editorial" | "pending" | "unresolved";

const SCENE_COMPARISON_FIELDS = [
  ["title", "标题"],
  ["location", "地点"],
  ["time_of_day", "时间"],
  ["characters", "出场角色"],
  ["props", "道具"],
  ["visible_action", "可见动作"],
  ["story_purpose", "剧情作用"],
  ["start_state", "开始状态"],
  ["end_state", "结束状态"],
  ["mood", "情绪"],
  ["sound_cues", "音效"],
  ["dialogues", "对白"],
] as const;

function objectValue(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function numberList(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is number => typeof item === "number") : [];
}

function sceneMap(value: unknown) {
  const artifact = objectValue(value);
  const scenes = Array.isArray(artifact?.scenes) ? artifact.scenes : [];
  const result = new Map<number, JsonObject>();
  scenes.map(objectValue).filter((scene): scene is JsonObject => scene !== null).forEach((scene) => {
    if (typeof scene.scene_no === "number") {
      result.set(scene.scene_no, scene);
    }
  });
  return result;
}

function displaySceneValue(field: string, value: unknown) {
  if (field === "dialogues" && Array.isArray(value)) {
    return value
      .map(objectValue)
      .filter((item): item is JsonObject => item !== null)
      .map((item) => `${stringValue(item.speaker) || "角色"}：${stringValue(item.text)}`)
      .filter(Boolean)
      .join("\n") || "—";
  }
  if (Array.isArray(value)) {
    return value.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join("、") || "—";
  }
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

function changedSceneFields(before: JsonObject | undefined, after: JsonObject | undefined) {
  if (!before || !after) {
    return [];
  }
  return SCENE_COMPARISON_FIELDS.flatMap(([field, label]) => {
    if (JSON.stringify(before[field]) === JSON.stringify(after[field])) {
      return [];
    }
    return [{
      field,
      label,
      before: displaySceneValue(field, before[field]),
      after: displaySceneValue(field, after[field]),
    }];
  });
}

function issueStatus(
  issue: JsonObject,
  taskStatus: string,
  resolvedCodes: Set<string>,
  unresolvedCodes: Set<string>,
): { key: IssueStatus; label: string } {
  if (issue.severity !== "must_fix") {
    return { key: "editorial", label: "仅供参考" };
  }
  const code = stringValue(issue.code);
  if (resolvedCodes.has(code)) {
    return { key: "resolved", label: "已自动修复" };
  }
  if (unresolvedCodes.has(code)) {
    return { key: "unresolved", label: "待人工处理" };
  }
  if (taskStatus === "queued" || taskStatus === "running") {
    return { key: "pending", label: "等待定点修订" };
  }
  return { key: "unresolved", label: "待人工处理" };
}

function qualityGate(task: GenerationTask) {
  const value = stringValue(task.result_payload.quality_gate);
  return value || "legacy_record";
}

function qualityLabel(gate: string) {
  if (gate === "review_unavailable") return "审稿不可用";
  if (gate === "needs_attention") return "需要人工检查";
  if (gate === "legacy_record") return "历史任务记录";
  return "协作检查通过";
}

function issueLabel(issue: JsonObject) {
  const categoryLabels: Record<string, string> = {
    causality: "因果",
    character: "人物",
    dialogue: "对白与英语",
    continuity: "连续性",
    production: "生产可执行性",
  };
  return categoryLabels[stringValue(issue.category)] ?? "综合问题";
}

export function ScriptCreationTrace({ task }: { task: GenerationTask | null }) {
  if (!task) {
    return null;
  }
  const checkpoint = objectValue(task.raw_response.checkpoint);
  const blueprint = objectValue(checkpoint?.blueprint);
  const review = objectValue(checkpoint?.review);
  const patch = objectValue(checkpoint?.patch);
  const contract = objectValue(checkpoint?.contract_report);
  const draftScenes = sceneMap(checkpoint?.draft);
  const finalScenes = sceneMap(checkpoint?.final);
  const issues = Array.isArray(review?.issues)
    ? review.issues.map(objectValue).filter((item): item is JsonObject => item !== null)
    : [];
  const patchedSceneNos = numberList(task.result_payload.patched_scene_nos);
  const resolvedCodes = stringList(task.result_payload.resolved_issue_codes);
  const unresolvedCodeList = stringList(task.result_payload.unresolved_issue_codes);
  const resolvedCodeSet = new Set(resolvedCodes);
  const unresolvedCodeSet = new Set(unresolvedCodeList);
  const resolvedIssueCount = issues.filter((issue) => resolvedCodeSet.has(stringValue(issue.code))).length;
  const editorialIssueCount = issues.filter((issue) => issue.severity !== "must_fix").length;
  const pendingIssueCount = issues.length - resolvedIssueCount - editorialIssueCount;
  const gate = qualityGate(task);

  return (
    <details className="script-creation-trace">
      <summary>
        <span>
          <strong>多 Agent 创作过程</strong>
          <small>故事架构 → 主创编剧 → 综合审稿 → 定点修订 → 合同校验</small>
        </span>
        <em className={`script-quality-gate ${gate}`}>{qualityLabel(gate)}</em>
      </summary>

      <div className="script-creation-trace-grid">
        <section>
          <span>STORY ARCHITECT</span>
          <h4>故事蓝图</h4>
          <p>{stringValue(blueprint?.premise) || "本次没有可展示的蓝图摘要。"}</p>
          {stringValue(blueprint?.dramatic_question) ? (
            <dl><dt>核心问题</dt><dd>{stringValue(blueprint?.dramatic_question)}</dd></dl>
          ) : null}
          {stringValue(blueprint?.emotional_payoff) ? (
            <dl><dt>结尾兑现</dt><dd>{stringValue(blueprint?.emotional_payoff)}</dd></dl>
          ) : null}
        </section>

        <section>
          <span>COMPREHENSIVE REVIEWER</span>
          <h4>综合审稿 · {issues.length} 项</h4>
          <p>
            {issues.length
              ? `已自动修复 ${resolvedIssueCount} 项，仅供参考 ${editorialIssueCount} 项${pendingIssueCount ? `，待处理 ${pendingIssueCount} 项` : ""}。`
              : "本次没有需要记录的定点问题。"}
          </p>
          {issues.length ? (
            <ul className="script-review-issue-list">
              {issues.map((issue, index) => {
                const status = issueStatus(issue, task.status, resolvedCodeSet, unresolvedCodeSet);
                const issueSceneNos = numberList(issue.scene_nos);
                const comparisons = issueSceneNos
                  .map((sceneNo) => ({
                    sceneNo,
                    changes: changedSceneFields(draftScenes.get(sceneNo), finalScenes.get(sceneNo)),
                  }))
                  .filter((item) => item.changes.length > 0);
                return (
                  <li className={`script-review-issue ${status.key}`} key={`${stringValue(issue.code)}-${index}`}>
                    <div>
                      <strong>{issueLabel(issue)}</strong>
                      <em className="script-issue-severity">{issue.severity === "must_fix" ? "必须修复" : "编辑建议"}</em>
                      <b className={`script-issue-status ${status.key}`}>{status.label}</b>
                      {issueSceneNos.length ? <small>场景 {issueSceneNos.join("、")}</small> : null}
                    </div>
                    <p>{stringValue(issue.problem) || stringValue(issue.repair_instruction)}</p>
                    {status.key === "resolved" && comparisons.length ? (
                      <details className="script-issue-comparison">
                        <summary>查看修改前后</summary>
                        {comparisons.map(({ sceneNo, changes }) => (
                          <section key={sceneNo}>
                            <h5>场景 {sceneNo}</h5>
                            {changes.map((change) => (
                              <div className="script-scene-change" key={change.field}>
                                <strong>{change.label}</strong>
                                <div><span>初稿</span><p>{change.before}</p></div>
                                <div><span>修订稿</span><p>{change.after}</p></div>
                              </div>
                            ))}
                          </section>
                        ))}
                      </details>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </section>

        <section>
          <span>TARGETED REVISER</span>
          <h4>定点修订</h4>
          {patch ? (
            <p>
              {patchedSceneNos.length ? `已替换场景 ${patchedSceneNos.join("、")}。` : "修订器没有替换场景。"}
              {unresolvedCodeList.length ? ` 未解决：${unresolvedCodeList.join("、")}。` : " 没有遗留审稿问题。"}
            </p>
          ) : (
            <p>{issues.some((issue) => issue.severity === "must_fix") ? "定点修订未成功应用，当前保留结构有效的初稿。" : "本次没有必须修复项，因此未调用修订 Agent。"}</p>
          )}
        </section>

        <section>
          <span>CONTRACT VALIDATOR</span>
          <h4>确定性合同</h4>
          <p>
            {contract?.valid === true
              ? "场景、英文对白、说话人和明确表达合同有效。"
              : "仍有生产合同问题，请结合审稿记录人工检查。"}
          </p>
          {Array.isArray(contract?.errors) && contract.errors.length ? (
            <ul>
              {contract.errors.map(objectValue).filter((item): item is JsonObject => item !== null).map((item, index) => (
                <li key={`${stringValue(item.code)}-${index}`}>{stringValue(item.message) || stringValue(item.code)}</li>
              ))}
            </ul>
          ) : null}
        </section>
      </div>
    </details>
  );
}
