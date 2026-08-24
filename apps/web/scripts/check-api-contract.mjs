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
  ["get", "/api/tasks/{task_id}"],
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
  ["post", "/api/assets/{asset_id}/select"],
  ["post", "/api/assets/{asset_id}/regenerate-candidate"],
  ["post", "/api/assets/{asset_id}/regenerate-video-candidate"],
  ["post", "/api/assets/{asset_id}/extract-frame"],
  ["delete", "/api/assets/{asset_id}"],
  ["get", "/api/projects/{project_id}/shots"],
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
  ["get", "/api/projects/{project_id}/dialogues"],
  ["put", "/api/projects/{project_id}/dialogues"],
  ["post", "/api/projects/{project_id}/compose"],
  ["get", "/api/projects/{project_id}/exports"],
];

const pageResponseOperations = [
  ["get", "/api/projects"],
  ["get", "/api/tasks"],
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
