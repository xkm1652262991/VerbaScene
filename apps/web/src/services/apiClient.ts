import type {
  CreateProjectPayload,
  Project,
  ProjectDeleteResult,
  ProjectDeletionPreview,
  ProjectDetail,
  ProjectStageRun,
  SubtitleMode,
  UpsertChapterPayload,
} from "../types/project";
import type {
  Asset,
  AssetCandidate,
  Character,
  Dialogue,
  DialogueInput,
  EntityBundle,
  ExportRecord,
  GenerationTask,
  ProjectReadiness,
  QualityCheck,
  Prop,
  Scene,
  Script,
  Shot,
  ShotFrameImage,
  ShotFramePrompt,
  ShotPromptPreview,
  ShotVideoPromptPreview,
} from "../types/stageFive";

export type ProjectWorkbenchPayload = {
  project: ProjectDetail;
  script: Script | null;
  entities: EntityBundle;
  current_entities?: EntityBundle;
  historical_entities?: EntityBundle;
  shots: Shot[];
  prompt_previews: ShotPromptPreview[];
  assets: Asset[];
  current_assets?: Asset[];
  historical_assets?: Asset[];
  asset_candidates: AssetCandidate[];
  dialogues: Dialogue[];
  exports: ExportRecord[];
  frame_prompts: ShotFramePrompt[];
  frame_images: ShotFrameImage[];
  quality_checks: QualityCheck[];
  readiness: ProjectReadiness;
  stage_runs: ProjectStageRun[];
};

export type GenerationTaskProgress = Pick<
  GenerationTask,
  | "id"
  | "project_id"
  | "task_type"
  | "provider"
  | "model"
  | "status"
  | "progress"
  | "progress_label"
  | "started_at"
  | "finished_at"
  | "created_at"
  | "updated_at"
>;

export type PatchImpactPayload = {
  target_type: string;
  target_id?: string | null;
  field?: string | null;
  change_summary?: string | null;
};

export type PatchImpactReport = {
  engine: string;
  project_id: string;
  target: Record<string, unknown>;
  affected_shots: Array<{
    id: string;
    shot_no: number;
    scene_id: string | null;
    character_ids: string[];
    prop_ids: string[];
    reason: string;
  }>;
  affected_assets: Array<{
    id: string;
    asset_type: string;
    asset_role: string | null;
    entity_type: string | null;
    entity_id: string | null;
    version: number;
    is_selected: boolean;
    status: string;
    reason: string;
  }>;
  affected_stages: string[];
  recommended_actions: string[];
  requires_human_review: boolean;
};

export type PageResponse<T> = {
  success: boolean;
  items: T[];
  meta: {
    total: number;
    offset: number;
    limit: number;
  };
};

export type ApiResponse<T> = {
  success: boolean;
  data: T;
};

export type ProviderDescriptor = {
  name: string;
  type: string;
  model: string;
  capabilities: string[];
  supports_polling: boolean;
  selected: boolean;
  configuration_status: "ready" | "incomplete";
  validation_error: string | null;
  native_audio: boolean;
  reference_images: boolean;
  reference_videos: boolean;
  reference_audio: boolean;
  multi_reference: boolean;
  smart_duration: boolean;
  min_duration_sec: number | null;
  max_duration_sec: number | null;
  supported_resolutions: string[];
};

export type ProviderSlot = "llm" | "image" | "video";
export type ApiKeyMode = "environment" | "direct" | "none";

export type RuntimeProviderConfig = {
  id: string | null;
  provider_type: ProviderSlot;
  provider_name: string;
  model_name: string;
  base_url: string | null;
  api_key_ref: string | null;
  api_key_mode: ApiKeyMode;
  api_key_configured: boolean;
  default_params: Record<string, unknown>;
  source: "environment" | "saved";
  configuration_status: "ready" | "incomplete";
  validation_error: string | null;
  restart_required: boolean;
  capabilities: {
    native_audio?: boolean;
    reference_images?: boolean;
    reference_videos?: boolean;
    reference_audio?: boolean;
    multi_reference?: boolean;
    smart_duration?: boolean;
    min_duration_sec?: number | null;
    max_duration_sec?: number | null;
    supported_resolutions?: string[];
  };
  created_at: string | null;
  updated_at: string | null;
};

export type RuntimeProviderConfigPayload = {
  provider_name: string;
  model_name: string;
  base_url?: string | null;
  api_key_ref?: string | null;
  api_key_mode?: ApiKeyMode;
  api_key?: string | null;
  default_params?: Record<string, unknown>;
};

export type ImageProviderProfile = {
  id: string;
  label: string;
  provider_name: string;
  model_name: string;
  base_url: string | null;
  capabilities: string[];
  default_params: Record<string, unknown>;
  source: "environment" | "saved";
  is_default: boolean;
  supports_references: boolean;
  configuration_status: "ready" | "incomplete";
  validation_error: string | null;
};

export type ProviderTestPayload = {
  provider_type: ProviderSlot;
  provider_name: string;
  model?: string | null;
  prompt: string;
  negative_prompt?: string | null;
  references?: string[];
  params?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
};

export type ProviderTestResult = {
  status: string;
  provider_task_id: string;
  execution_mode: string;
  poll_after_sec: number | null;
  task_id: string | null;
  assets: Array<{
    asset_type: string;
    uri: string;
    mime_type: string | null;
    width: number | null;
    height: number | null;
    duration_sec: string | number | null;
    metadata: Record<string, unknown>;
  }>;
  raw_response: Record<string, unknown>;
  usage: { cost: string | number; unit: string };
  error: {
    error_code: string;
    error_message: string;
    is_retryable: boolean;
    raw_error: Record<string, unknown>;
  } | null;
  created_at: string | null;
};

export type AgentConfig = {
  id: string;
  agent_type: string;
  name: string;
  description: string | null;
  provider: string | null;
  model: string | null;
  system_prompt: string | null;
  temperature: string | null;
  max_tokens: number | null;
  max_iterations: number | null;
  settings: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type AgentConfigPayload = {
  agent_type: string;
  name: string;
  description?: string | null;
  provider?: string | null;
  model?: string | null;
  system_prompt?: string | null;
  temperature?: string | null;
  max_tokens?: number | null;
  max_iterations?: number | null;
  settings?: Record<string, unknown>;
  is_active?: boolean;
};

export type PromptVersion = {
  id: string;
  agent_type: string;
  name: string;
  content: string;
  version: number;
  source: string;
  metadata: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type PromptVersionPayload = {
  agent_type: string;
  name: string;
  content: string;
  version?: number;
  source?: string;
  metadata?: Record<string, unknown>;
  is_active?: boolean;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message || `Request failed: ${response.status}`);
  }

  const payload = await response.json();
  return normalizeApiPayload<T>(payload);
}

function normalizeApiPayload<T>(payload: unknown): T {
  if (!isRecord(payload)) {
    return payload as T;
  }
  if (payload.success === false) {
    const error = isRecord(payload.error) ? payload.error : {};
    const message = typeof error.message === "string" ? error.message : "请求失败";
    throw new Error(message);
  }
  if (payload.success === true && "data" in payload) {
    return payload.data as T;
  }
  return payload as T;
}

async function parseErrorMessage(response: Response) {
  const text = await response.text();
  if (!text) {
    return "";
  }
  try {
    const payload = JSON.parse(text) as {
      error?: { message?: string; code?: string; detail?: unknown };
      detail?: unknown;
    };
    if (payload.error?.message) {
      return payload.error.message;
    }
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (payload.error?.code) {
      return payload.error.code;
    }
  } catch {
    return text;
  }
  return text;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export async function getHealth() {
  return request<{
    status: string;
    service: string;
    version: string;
    environment: string;
    dependencies: Record<string, { ok: boolean; error: string | null }>;
  }>("/health");
}

export async function listProjects() {
  const response = await request<PageResponse<Project>>("/api/projects");
  return response.items;
}

export function getProjectDeletionPreview(projectId: string) {
  return request<ProjectDeletionPreview>(`/api/projects/${projectId}/deletion-preview`);
}

export function deleteProject(projectId: string, confirmationTitle: string) {
  return request<ProjectDeleteResult>(`/api/projects/${projectId}`, {
    method: "DELETE",
    body: JSON.stringify({ confirmation_title: confirmationTitle }),
  });
}

export async function listProviders() {
  const response = await request<PageResponse<ProviderDescriptor>>("/api/providers");
  return response.items;
}

export async function listRuntimeProviderConfigs() {
  const response = await request<PageResponse<RuntimeProviderConfig>>("/api/providers/configs");
  return response.items;
}

export async function listImageProviderProfiles() {
  const response = await request<PageResponse<ImageProviderProfile>>("/api/providers/image-profiles");
  return response.items;
}

export function updateRuntimeProviderConfig(providerType: ProviderSlot, payload: RuntimeProviderConfigPayload) {
  return request<RuntimeProviderConfig>(`/api/providers/configs/${providerType}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function resetRuntimeProviderConfig(providerType: ProviderSlot) {
  return request<RuntimeProviderConfig>(`/api/providers/configs/${providerType}`, {
    method: "DELETE",
  });
}

export function testProvider(payload: ProviderTestPayload) {
  return request<ProviderTestResult>("/api/providers/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listAgentConfigs(agentType?: string) {
  const query = agentType ? `?agent_type=${encodeURIComponent(agentType)}` : "";
  const response = await request<PageResponse<AgentConfig>>(`/api/agent-configs${query}`);
  return response.items;
}

export function createAgentConfig(payload: AgentConfigPayload) {
  return request<AgentConfig>("/api/agent-configs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAgentConfig(configId: string, payload: Partial<AgentConfigPayload>) {
  return request<AgentConfig>(`/api/agent-configs/${configId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function listPromptVersions(agentType?: string) {
  const query = agentType ? `?agent_type=${encodeURIComponent(agentType)}` : "";
  const response = await request<PageResponse<PromptVersion>>(`/api/prompt-versions${query}`);
  return response.items;
}

export function createPromptVersion(payload: PromptVersionPayload) {
  return request<PromptVersion>("/api/prompt-versions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updatePromptVersion(promptId: string, payload: Partial<PromptVersionPayload>) {
  return request<PromptVersion>(`/api/prompt-versions/${promptId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function seedAvdPromptPack(activate = false) {
  const query = activate ? "?activate=true" : "?activate=false";
  return request<PromptVersion[]>(`/api/prompt-versions/avd/seed${query}`, {
    method: "POST",
  });
}

export function getProject(projectId: string) {
  return request<ProjectDetail>(`/api/projects/${projectId}`);
}

export function getProjectWorkbench(projectId: string) {
  return request<ProjectWorkbenchPayload>(`/api/projects/${projectId}/workbench`);
}

export function getProjectEventsUrl(projectId: string) {
  return `${API_BASE_URL}/api/projects/${projectId}/events`;
}

export async function getProjectWorkflowRuns(projectId: string, limit = 20) {
  const response = await request<PageResponse<ProjectStageRun>>(`/api/projects/${projectId}/workflow/runs?limit=${limit}`);
  return response.items;
}

export function getProjectReadiness(projectId: string) {
  return request<ProjectReadiness>(`/api/projects/${projectId}/readiness`);
}

export function cancelProjectStageRun(runId: string) {
  return request<ProjectStageRun>(`/api/workflow/runs/${runId}/cancel`, {
    method: "POST",
  });
}

export function createProject(payload: CreateProjectPayload) {
  return request<ProjectDetail>("/api/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateProject(projectId: string, payload: Partial<Project>) {
  return request<ProjectDetail>(`/api/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function analyzePatchImpact(projectId: string, payload: PatchImpactPayload) {
  return request<PatchImpactReport>(`/api/projects/${projectId}/patch-impact`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function upsertProjectChapter(projectId: string, payload: UpsertChapterPayload) {
  return request<ProjectDetail>(`/api/projects/${projectId}/chapter`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getProjectScript(projectId: string) {
  return request<Script>(`/api/projects/${projectId}/script`);
}

export async function generateProjectScript(projectId: string) {
  const accepted = await request<GenerationTask>(`/api/projects/${projectId}/script/generate`, {
    method: "POST",
  });
  const deadline = Date.now() + 15 * 60 * 1000;
  let task = accepted;
  while (task.status === "queued" || task.status === "running") {
    if (Date.now() >= deadline) {
      throw new Error(`剧本任务仍在后台执行，可在任务中心继续查看：${task.id}`);
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    task = await request<GenerationTask>(`/api/tasks/${task.id}`);
  }
  if (task.status !== "succeeded") {
    throw new Error(task.error_message || `剧本任务未完成：${task.status}`);
  }
  return {
    script: await getProjectScript(projectId),
    task_id: task.id,
  };
}

export function updateScript(scriptId: string, payload: Pick<Script, "scenes">) {
  return request<Script>(`/api/scripts/${scriptId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function getProjectEntities(projectId: string) {
  return request<EntityBundle>(`/api/projects/${projectId}/entities`);
}

export function generateProjectEntities(projectId: string) {
  return request<EntityBundle & { task_id: string }>(`/api/projects/${projectId}/entities/generate`, {
    method: "POST",
  });
}

export function updateCharacter(characterId: string, payload: Partial<Character>) {
  return request<Character>(`/api/characters/${characterId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function updateScene(sceneId: string, payload: Partial<Scene>) {
  return request<Scene>(`/api/scenes/${sceneId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function updateProp(propId: string, payload: Partial<Prop>) {
  return request<Prop>(`/api/props/${propId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function getProjectShots(projectId: string) {
  const response = await request<PageResponse<Shot>>(`/api/projects/${projectId}/shots`);
  return response.items;
}

export async function getProjectShotPromptPreviews(projectId: string) {
  const response = await request<PageResponse<ShotPromptPreview>>(`/api/projects/${projectId}/shots/prompt-preview`);
  return response.items;
}

export function generateProjectShots(projectId: string) {
  return request<{ shots: Shot[]; task_id: string }>(`/api/projects/${projectId}/shots/generate`, {
    method: "POST",
  });
}

export function updateShot(shotId: string, payload: Partial<Shot>) {
  return request<Shot>(`/api/shots/${shotId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function updateShotVideoReference(shotId: string, assetId: string | null) {
  return request<Shot>(`/api/shots/${shotId}/video-reference`, {
    method: "PATCH",
    body: JSON.stringify({ asset_id: assetId }),
  });
}

export function updateShotReferenceAssets(shotId: string, assetIds: string[]) {
  return request<Shot>(`/api/shots/${shotId}/reference-assets`, {
    method: "PUT",
    body: JSON.stringify({ asset_ids: assetIds }),
  });
}

export function createProjectShot(
  projectId: string,
  payload: Pick<Shot, "shot_no" | "description"> & Partial<Shot>,
) {
  return request<Shot>(`/api/projects/${projectId}/shots`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteShot(shotId: string) {
  const response = await fetch(`${API_BASE_URL}/api/shots/${shotId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message || `Request failed: ${response.status}`);
  }
}

export function reorderShots(shots: Array<{ id: string; shot_no: number }>) {
  return request<Shot[]>("/api/shots/reorder", {
    method: "POST",
    body: JSON.stringify({ shots }),
  });
}

export function getShotVideoPromptPreview(shotId: string) {
  return request<ShotVideoPromptPreview>(`/api/shots/${shotId}/video-prompt-preview`);
}

export function compileShotVideoPrompt(shotId: string) {
  return request<Shot>(`/api/shots/${shotId}/compile-video-prompt`, {
    method: "POST",
  });
}

export async function getProjectQualityChecks(projectId: string, stage?: string) {
  const query = stage ? `?stage=${encodeURIComponent(stage)}` : "";
  const response = await request<PageResponse<QualityCheck>>(`/api/projects/${projectId}/quality-checks${query}`);
  return response.items;
}

export function runProjectQualityChecks(projectId: string, stage: string) {
  return request<{ checks: QualityCheck[] }>(
    `/api/projects/${projectId}/quality-checks/run?stage=${encodeURIComponent(stage)}`,
    {
      method: "POST",
    },
  );
}

export async function getProjectAssets(projectId: string) {
  const response = await request<PageResponse<Asset>>(`/api/projects/${projectId}/assets`);
  return response.items;
}

export async function getProjectAssetCandidates(projectId: string, status?: string) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await request<PageResponse<AssetCandidate>>(`/api/projects/${projectId}/asset-candidates${query}`);
  return response.items;
}

export function promoteAssetCandidate(candidateId: string, reviewNote?: string) {
  return request<Asset>(`/api/asset-candidates/${candidateId}/promote`, {
    method: "POST",
    body: JSON.stringify({ review_note: reviewNote ?? null }),
  });
}

export function rejectAssetCandidate(candidateId: string, reviewNote?: string) {
  return request<AssetCandidate>(`/api/asset-candidates/${candidateId}/reject`, {
    method: "POST",
    body: JSON.stringify({ review_note: reviewNote ?? null }),
  });
}

export function regenerateAssetCandidate(candidateId: string, imageProviderProfileId?: string | null) {
  const params = new URLSearchParams();
  if (imageProviderProfileId) params.set("image_provider_profile_id", imageProviderProfileId);
  const query = params.toString();
  return request<{ candidates: AssetCandidate[]; task_id: string }>(
    `/api/asset-candidates/${candidateId}/regenerate${query ? `?${query}` : ""}`,
    { method: "POST" },
  );
}

export async function deleteAssetCandidate(candidateId: string) {
  const response = await fetch(`${API_BASE_URL}/api/asset-candidates/${candidateId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message || `Request failed: ${response.status}`);
  }
}

export function generateReferenceImageCandidates(projectId: string, imageProviderProfileId?: string | null) {
  const params = new URLSearchParams();
  if (imageProviderProfileId) params.set("image_provider_profile_id", imageProviderProfileId);
  const query = params.toString();
  return request<{ candidates: AssetCandidate[]; task_id: string }>(
    `/api/projects/${projectId}/reference-images/generate-candidates${query ? `?${query}` : ""}`,
    {
      method: "POST",
    },
  );
}

export function generateSingleReferenceImageCandidate(
  projectId: string,
  target: { entity_type: string; entity_id: string; asset_role: string; variant_key?: string },
  imageProviderProfileId?: string | null,
) {
  const params = new URLSearchParams({
    entity_type: target.entity_type,
    entity_id: target.entity_id,
    asset_role: target.asset_role,
    variant_key: target.variant_key ?? "base",
  });
  if (imageProviderProfileId) params.set("image_provider_profile_id", imageProviderProfileId);
  return request<{ candidates: AssetCandidate[]; task_id: string }>(
    `/api/projects/${projectId}/reference-images/generate-candidate?${params.toString()}`,
    { method: "POST" },
  );
}

export function generateShotImageCandidates(projectId: string, imageProviderProfileId?: string | null) {
  const params = new URLSearchParams();
  if (imageProviderProfileId) params.set("image_provider_profile_id", imageProviderProfileId);
  const query = params.toString();
  return request<{ candidates: AssetCandidate[]; task_id: string }>(
    `/api/projects/${projectId}/shot-images/generate-candidates${query ? `?${query}` : ""}`,
    {
      method: "POST",
    },
  );
}

export function generateSingleShotImageCandidate(shotId: string, imageProviderProfileId?: string | null) {
  const params = new URLSearchParams();
  if (imageProviderProfileId) params.set("image_provider_profile_id", imageProviderProfileId);
  const query = params.toString();
  return request<{ candidates: AssetCandidate[]; task_id: string }>(
    `/api/shots/${shotId}/image/generate-candidate${query ? `?${query}` : ""}`,
    {
      method: "POST",
    },
  );
}

export function generateShotImageGrid(projectId: string, rows = 2, cols = 2) {
  return request<{ assets: Asset[]; task_id: string }>(
    `/api/projects/${projectId}/shot-image-grid/generate?rows=${rows}&cols=${cols}`,
    {
      method: "POST",
    },
  );
}

export function selectAsset(assetId: string) {
  return request<Asset>(`/api/assets/${assetId}/select`, {
    method: "POST",
  });
}

export function regenerateImageAssetCandidate(assetId: string, imageProviderProfileId?: string | null) {
  const params = new URLSearchParams();
  if (imageProviderProfileId) params.set("image_provider_profile_id", imageProviderProfileId);
  const query = params.toString();
  return request<{ candidates: AssetCandidate[]; task_id: string }>(`/api/assets/${assetId}/regenerate-candidate${query ? `?${query}` : ""}`, {
    method: "POST",
  });
}

export function regenerateVideoAssetCandidate(assetId: string) {
  return request<{ candidates: AssetCandidate[]; task_id: string }>(`/api/assets/${assetId}/regenerate-video-candidate`, {
    method: "POST",
  });
}

export function extractVideoFrameCandidate(assetId: string, timeSec = 0) {
  return request<{ candidates: AssetCandidate[]; task_id: string }>(
    `/api/assets/${assetId}/extract-frame?time_sec=${encodeURIComponent(timeSec)}`,
    { method: "POST" },
  );
}

export async function deleteAsset(assetId: string) {
  const response = await fetch(`${API_BASE_URL}/api/assets/${assetId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message || `Request failed: ${response.status}`);
  }
}

export async function uploadImageAsset(
  projectId: string,
  payload: {
    file: File;
    entity_type: string;
    entity_id: string;
    asset_role?: string;
    variant_key?: string;
    prompt?: string;
  },
) {
  const formData = new FormData();
  formData.append("file", payload.file);
  formData.append("entity_type", payload.entity_type);
  formData.append("entity_id", payload.entity_id);
  if (payload.asset_role) {
    formData.append("asset_role", payload.asset_role);
  }
  formData.append("variant_key", payload.variant_key ?? "base");
  if (payload.prompt) {
    formData.append("prompt", payload.prompt);
  }
  const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/assets/images/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  const responsePayload = await response.json();
  return normalizeApiPayload<Asset>(responsePayload);
}

export function generateShotVideoCandidates(projectId: string) {
  return request<{ candidates: AssetCandidate[]; task_id: string }>(
    `/api/projects/${projectId}/shot-videos/generate-candidates`,
    {
      method: "POST",
    },
  );
}

export function generateSingleShotVideoCandidate(
  shotId: string,
  payload: {
    duration_mode?: "provider_auto" | "fixed" | null;
    duration_sec?: number | string | null;
    video_prompt?: string | null;
  },
) {
  return request<GenerationTask>(
    `/api/shots/${shotId}/video/generate-candidate`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function cancelQueuedGenerationTask(taskId: string) {
  return request<GenerationTask>(`/api/tasks/${taskId}/queue`, {
    method: "DELETE",
  });
}

export async function getProjectDialogues(projectId: string) {
  const response = await request<PageResponse<Dialogue>>(`/api/projects/${projectId}/dialogues`);
  return response.items;
}

export function saveProjectDialogues(projectId: string, dialogues: DialogueInput[]) {
  return request<{
    dialogues: Dialogue[];
    stale_shot_ids: string[];
    metadata: Record<string, unknown>;
  }>(`/api/projects/${projectId}/dialogues`, {
    method: "PUT",
    body: JSON.stringify({ dialogues }),
  });
}

export async function getProjectExports(projectId: string) {
  const response = await request<PageResponse<ExportRecord>>(`/api/projects/${projectId}/exports`);
  return response.items;
}

export function composeProject(projectId: string, subtitleMode: SubtitleMode = "none") {
  return request<{ export: ExportRecord; asset: Asset }>(`/api/projects/${projectId}/compose`, {
    method: "POST",
    body: JSON.stringify({ subtitle_mode: subtitleMode }),
  });
}

export function getGenerationTask(taskId: string) {
  return request<GenerationTask>(`/api/tasks/${taskId}`);
}

export function listGenerationTasks(options: { projectId?: string; status?: string; offset?: number; limit?: number } = {}) {
  const params = new URLSearchParams();
  if (options.projectId) {
    params.set("project_id", options.projectId);
  }
  if (options.status) {
    params.set("status", options.status);
  }
  if (typeof options.offset === "number") {
    params.set("offset", String(options.offset));
  }
  if (options.limit) {
    params.set("limit", String(options.limit));
  }
  const query = params.toString();
  return request<PageResponse<GenerationTask>>(`/api/tasks${query ? `?${query}` : ""}`);
}

export function listGenerationTaskProgress(
  options: { projectId?: string; status?: string; offset?: number; limit?: number } = {},
) {
  const params = new URLSearchParams();
  if (options.projectId) {
    params.set("project_id", options.projectId);
  }
  if (options.status) {
    params.set("status", options.status);
  }
  if (typeof options.offset === "number") {
    params.set("offset", String(options.offset));
  }
  if (options.limit) {
    params.set("limit", String(options.limit));
  }
  const query = params.toString();
  return request<PageResponse<GenerationTaskProgress>>(`/api/tasks/progress${query ? `?${query}` : ""}`);
}
