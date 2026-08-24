import type { AssetCandidate } from "../../types/stageFive";

type JsonObject = Record<string, unknown>;

export function AssetCandidateDiagnostics({ candidate }: { candidate: AssetCandidate }) {
  if (candidate.asset_type !== "image") {
    return null;
  }

  const raw = objectValue(candidate.raw_response);
  const requestMetadata = objectValue(raw?.request_metadata);
  const consistency = objectValue(raw?.consistency_check);
  const providerResponse = objectValue(raw?.provider_response);
  const referenceSummary = objectValue(providerResponse?.request_reference_summary);
  const generationSeed = numberValue(requestMetadata?.generation_seed);
  const issues = arrayValue(consistency?.issues)
    .filter((item): item is JsonObject => objectValue(item) !== null)
    .slice(0, 2);

  if (!consistency && !referenceSummary && generationSeed === null) {
    return null;
  }

  const status = textValue(consistency?.status) ?? "unknown";
  const score = numberValue(consistency?.score);
  const referenceCount = numberValue(referenceSummary?.reference_count);
  const payloadCount = numberValue(referenceSummary?.reference_payload_count);
  const imageCount = numberValue(referenceSummary?.reference_image_count);
  const readableCount = numberValue(consistency?.readable_reference_count);
  const roles = arrayValue(referenceSummary?.reference_roles).map(String).filter(Boolean).slice(0, 4);
  const hasRisk = consistency !== null && (status !== "pass" || issues.length > 0);

  return (
    <div className={["candidate-diagnostics", hasRisk ? "has-risk" : "is-pass"].join(" ")}>
      <div className="candidate-diagnostic-row">
        {generationSeed !== null ? <span className="diagnostic-chip">Seed {generationSeed}</span> : null}
        {score !== null ? <span className={`diagnostic-chip ${hasRisk ? "warning" : "pass"}`}>一致性 {score}</span> : null}
        {imageCount !== null || referenceCount !== null ? (
          <span className="diagnostic-chip">
            参考图 {imageCount ?? 0}/{referenceCount ?? payloadCount ?? 0}
          </span>
        ) : null}
        {readableCount !== null && referenceCount !== null ? (
          <span className="diagnostic-chip">可读 {readableCount}/{referenceCount}</span>
        ) : null}
      </div>
      {roles.length > 0 ? <p className="candidate-diagnostic-roles">{roles.join(" / ")}</p> : null}
      {issues.length > 0 ? (
        <ul className="candidate-diagnostic-issues">
          {issues.map((issue, index) => (
            <li key={`${textValue(issue.code) ?? "issue"}-${index}`}>{textValue(issue.message) ?? "一致性检查发现风险"}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function objectValue(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}
