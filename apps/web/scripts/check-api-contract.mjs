import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../../..");
const contractPath = resolve(repoRoot, "docs/api/openapi.json");
const contract = JSON.parse(readFileSync(contractPath, "utf8"));

const requiredOperations = [
  ["get", "/api/projects"],
  ["post", "/api/projects"],
  ["get", "/api/projects/{project_id}"],
  ["patch", "/api/projects/{project_id}"],
  ["get", "/api/projects/{project_id}/workbench"],
  ["get", "/api/projects/{project_id}/readiness"],
  ["get", "/api/projects/{project_id}/workflow/runs"],
  ["post", "/api/workflow/runs/{run_id}/cancel"],
  ["get", "/api/tasks"],
  ["get", "/api/tasks/progress"],
  ["get", "/api/tasks/{task_id}"],
  ["post", "/api/tasks/{task_id}/cancel"],
  ["post", "/api/tasks/{task_id}/retry"],
  ["get", "/api/providers"],
  ["get", "/api/agent-configs"],
  ["post", "/api/agent-configs"],
  ["patch", "/api/agent-configs/{config_id}"],
  ["get", "/api/prompt-versions"],
  ["post", "/api/prompt-versions"],
  ["patch", "/api/prompt-versions/{prompt_id}"],
  ["get", "/api/projects/{project_id}/assets"],
  ["get", "/api/projects/{project_id}/asset-candidates"],
  ["post", "/api/asset-candidates/{candidate_id}/promote"],
  ["post", "/api/asset-candidates/{candidate_id}/reject"],
  ["post", "/api/asset-candidates/{candidate_id}/regenerate"],
  ["delete", "/api/asset-candidates/{candidate_id}"],
  ["post", "/api/projects/{project_id}/reference-images/generate-candidates"],
  ["post", "/api/projects/{project_id}/reference-images/generate-candidate"],
  ["post", "/api/projects/{project_id}/shot-images/generate-candidates"],
  ["post", "/api/shots/{shot_id}/image/generate-candidate"],
  ["post", "/api/assets/{asset_id}/select"],
  ["post", "/api/assets/{asset_id}/regenerate-candidate"],
  ["post", "/api/assets/{asset_id}/regenerate-video-candidate"],
  ["post", "/api/assets/{asset_id}/extract-frame"],
  ["delete", "/api/assets/{asset_id}"],
  ["get", "/api/projects/{project_id}/shots"],
  ["post", "/api/projects/{project_id}/shots/generate"],
  ["post", "/api/projects/{project_id}/shots"],
  ["patch", "/api/shots/{shot_id}"],
  ["put", "/api/shots/{shot_id}/reference-assets"],
  ["delete", "/api/shots/{shot_id}"],
  ["post", "/api/shots/reorder"],
  ["get", "/api/shots/{shot_id}/video-prompt-preview"],
  ["post", "/api/shots/{shot_id}/compile-video-prompt"],
  ["get", "/api/projects/{project_id}/shots/prompt-preview"],
  ["get", "/api/projects/{project_id}/shot-frame-images"],
  ["post", "/api/shots/{shot_id}/video/generate-candidate"],
  ["post", "/api/projects/{project_id}/shot-videos/generate-candidates"],
  ["post", "/api/projects/{project_id}/script/generate"],
  ["get", "/api/projects/{project_id}/dialogues"],
  ["put", "/api/projects/{project_id}/dialogues"],
  ["post", "/api/projects/{project_id}/compose"],
  ["get", "/api/projects/{project_id}/exports"],
];

const pageResponseOperations = [
  ["get", "/api/projects"],
  ["get", "/api/tasks"],
  ["get", "/api/tasks/progress"],
  ["get", "/api/providers"],
  ["get", "/api/agent-configs"],
  ["get", "/api/prompt-versions"],
  ["get", "/api/projects/{project_id}/workflow/runs"],
  ["get", "/api/projects/{project_id}/assets"],
  ["get", "/api/projects/{project_id}/asset-candidates"],
  ["get", "/api/projects/{project_id}/shots"],
  ["get", "/api/projects/{project_id}/shots/prompt-preview"],
  ["get", "/api/projects/{project_id}/shot-frame-images"],
  ["get", "/api/projects/{project_id}/dialogues"],
  ["get", "/api/projects/{project_id}/exports"],
];

const errors = [];

const taskSubmissionPaths = [
  "/api/projects/{project_id}/script/generate",
  "/api/projects/{project_id}/shots/generate",
  "/api/projects/{project_id}/reference-images/generate-candidates",
  "/api/projects/{project_id}/reference-images/generate-candidate",
  "/api/projects/{project_id}/shot-images/generate-candidates",
  "/api/shots/{shot_id}/image/generate-candidate",
  "/api/assets/{asset_id}/regenerate-candidate",
  "/api/asset-candidates/{candidate_id}/regenerate",
  "/api/shots/{shot_id}/video/generate-candidate",
  "/api/projects/{project_id}/shot-videos/generate-candidates",
  "/api/assets/{asset_id}/regenerate-video-candidate",
  "/api/assets/{asset_id}/extract-frame",
  "/api/projects/{project_id}/compose",
  "/api/tasks/{task_id}/cancel",
  "/api/tasks/{task_id}/retry",
];

const idempotentGenerationPaths = [
  ...taskSubmissionPaths.slice(0, -2),
];
const requiredTaskFields = [
  "parent_task_id",
  "retry_of_task_id",
  "resource_key",
  "idempotency_key",
  "status",
  "max_retries",
  "child_summary",
  "heartbeat_at",
  "cancel_requested_at",
];

for (const [method, path] of requiredOperations) {
  const operation = contract.paths?.[path]?.[method];
  if (!operation) {
    errors.push(`Missing frontend API operation: ${method.toUpperCase()} ${path}`);
  }
}

for (const [method, path] of pageResponseOperations) {
  const schema = contract.paths?.[path]?.[method]?.responses?.["200"]?.content?.["application/json"]?.schema;
  if (!usesPageResponse(schema)) {
    errors.push(`Expected PageResponse for ${method.toUpperCase()} ${path}`);
  }
}

for (const path of taskSubmissionPaths) {
  const schema = contract.paths?.[path]?.post?.responses?.["202"]?.content?.["application/json"]?.schema;
  if (typeof schema?.$ref !== "string" || !schema.$ref.includes("ApiResponse_GenerationTaskRead_")) {
    errors.push(`Expected 202 GenerationTask response for POST ${path}`);
  }
}

for (const path of idempotentGenerationPaths) {
  const parameters = contract.paths?.[path]?.post?.parameters ?? [];
  const hasIdempotencyHeader = parameters.some((parameter) => (
    parameter?.in === "header" && parameter?.name === "Idempotency-Key"
  ));
  if (!hasIdempotencyHeader) {
    errors.push(`Expected optional Idempotency-Key header for POST ${path}`);
  }
}

const taskSchema = contract.components?.schemas?.GenerationTaskRead;
const taskRequiredFields = new Set(taskSchema?.required ?? []);
for (const field of requiredTaskFields) {
  if (!taskRequiredFields.has(field)) {
    errors.push(`GenerationTaskRead is missing required field: ${field}`);
  }
}

if (contract.paths?.["/api/tasks/{task_id}/queue"]?.delete) {
  errors.push("Legacy DELETE /api/tasks/{task_id}/queue must not be exposed");
}

if (errors.length > 0) {
  for (const error of errors) {
    console.error(`- ${error}`);
  }
  process.exit(1);
}

console.log(`Frontend API contract checks passed: ${contractPath}`);

function usesPageResponse(schema) {
  return typeof schema?.$ref === "string" && schema.$ref.includes("PageResponse_");
}
