import type { GenerationTask } from "../../types/stageFive";

type JsonObject = Record<string, unknown>;

const QUALITY_LABELS: Record<string, string> = {
  pass: "导演检查通过",
  needs_attention: "建议人工检查",
  review_unavailable: "审稿本次不可用",
  legacy_record: "历史任务记录",
};

const CATEGORY_LABELS: Record<string, string> = {
  coverage: "剧情覆盖",
  continuity: "连续性",
  dialogue: "对白",
  cinematography: "镜头语言",
  asset_feasibility: "资产可实现性",
  production: "生产合同",
};

export function DirectorCheckPanel({ task }: { task: GenerationTask }) {
  const payload = objectValue(task.result_payload);
  const report = objectValue(payload.director_report);
  if (!Object.keys(report).length) {
    return (
      <section className="director-check-panel legacy">
        <header><div><span>DIRECTOR CHECK</span><strong>导演检查</strong></div><em>历史记录</em></header>
        <p>这个批次生成于规范化导演报告上线之前，仍可正常编辑；界面不会解析 Provider 原始响应。</p>
      </section>
    );
  }

  const qualityGate = stringValue(report.quality_gate) || "needs_attention";
  const readiness = objectValue(report.asset_readiness);
  const reflection = objectValue(report.reflection);
  const patch = objectValue(report.patch);
  const issues = objectArray(reflection.issues);
  const missingReferences = objectArray(readiness.missing_references);
  const conflicts = objectArray(readiness.conflicts);
  const patchedShotNos = numberArray(patch.patched_shot_nos);
  const unresolvedCodes = stringArray(patch.unresolved_issue_codes);
  const planningReady = readiness.planning_ready === true;
  const generationReady = readiness.generation_ready === true;

  return (
    <section className={`director-check-panel ${qualityGate}`}>
      <header>
        <div><span>DIRECTOR CHECK · METADATA ONLY</span><strong>导演检查</strong></div>
        <em>{QUALITY_LABELS[qualityGate] ?? qualityGate}</em>
      </header>
      <div className="director-check-readiness">
        <span className={planningReady ? "ready" : "warning"}>分镜规划 {planningReady ? "可用" : "缺少结构"}</span>
        <span className={generationReady ? "ready" : "warning"}>默认参考资产 {generationReady ? "齐全" : "建议补充"}</span>
      </div>
      {stringValue(reflection.summary) ? <p>{stringValue(reflection.summary)}</p> : null}
      {patchedShotNos.length ? <p>本次实际定点修订：片段 {patchedShotNos.join("、")}。</p> : null}
      {issues.length ? (
        <details>
          <summary>审稿问题 · {issues.length}</summary>
          <ul>
            {issues.map((issue, index) => {
              const severity = stringValue(issue.severity);
              const category = stringValue(issue.category);
              const shotNos = numberArray(issue.shot_nos);
              return (
                <li key={`${stringValue(issue.code) || "issue"}-${index}`}>
                  <strong>{severity === "must_fix" ? "必须修复" : "编辑建议"} · {CATEGORY_LABELS[category] ?? category}</strong>
                  <span>{stringValue(issue.message) || stringValue(issue.code)}</span>
                  {shotNos.length ? <small>片段 {shotNos.join("、")}</small> : null}
                </li>
              );
            })}
          </ul>
        </details>
      ) : <p>审稿没有制造额外修改项。</p>}
      {missingReferences.length || conflicts.length ? (
        <details open={!generationReady}>
          <summary>资产提醒 · {missingReferences.length + conflicts.length}</summary>
          <ul>
            {missingReferences.map((item, index) => (
              <li key={`missing-${stringValue(item.entity_id)}-${index}`}>
                <strong>建议补充参考图</strong>
                <span>{stringValue(item.name) || stringValue(item.entity_id)}</span>
              </li>
            ))}
            {conflicts.map((item, index) => (
              <li key={`conflict-${stringValue(item.code)}-${index}`}>
                <strong>元数据冲突</strong>
                <span>{stringValue(item.message) || stringValue(item.code)}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {unresolvedCodes.length ? <small>未解决：{unresolvedCodes.join("、")}</small> : null}
      {!generationReady ? <p className="director-check-hint">这不会锁住编辑；补充并采用参考图后，再提交视频即可。</p> : null}
    </section>
  );
}

function objectValue(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function objectArray(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : [];
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((item): item is number => typeof item === "number" && Number.isFinite(item)) : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
