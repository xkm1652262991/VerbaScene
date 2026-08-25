export type ProductionDialogue = {
  id?: string;
  speaker: string;
  text: string;
  translation_zh: string;
  emotion: string;
  source_type: "source" | "adapted" | "created";
  sequence_order?: number;
  beat_id?: string;
  sound_cues?: string[];
  scene_no?: number;
};

export type ProductionScene = {
  scene_no: number;
  title: string;
  location: string;
  time_of_day: string;
  characters: string[];
  props: string[];
  visible_action: string;
  story_purpose: string;
  start_state: string;
  end_state: string;
  mood: string;
  source_evidence: string;
  inferred_elements: string[];
  sound_cues: string[];
  dialogues: ProductionDialogue[];
};

export type Script = {
  id: string;
  project_id: string;
  chapter_id: string;
  version: number;
  content: string;
  scenes: ProductionScene[] | Array<Record<string, unknown>> | Record<string, unknown>;
  dialogues: ProductionDialogue[] | Array<Record<string, unknown>> | Record<string, unknown>;
  status: string;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Character = {
  id: string;
  project_id: string;
  name: string;
  role_type: string | null;
  age: string | null;
  gender: string | null;
  identity: string | null;
  personality: string | null;
  appearance: string | null;
  fixed_prompt: string | null;
  asset_spec: CharacterAssetSpec;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Scene = {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  visual_style: string | null;
  atmosphere: string | null;
  fixed_prompt: string | null;
  asset_spec: SceneAssetSpec;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Prop = {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  visual_prompt: string | null;
  story_function: string | null;
  asset_spec: PropAssetSpec;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AssetStateVariant = {
  key: string;
  name: string;
  description: string;
  scene_nos: number[];
};

export type AssetSourceEvidence = {
  scene_no: number | null;
  evidence: string;
};

export type AssetReferencePlan = {
  required: boolean;
  priority: "core" | "supporting" | "optional";
  views: string[];
};

export type AssetAutofillInfo = {
  applied: boolean;
  filled_fields: string[];
  normalized_fields: string[];
  review_fields: string[];
};

export type StoredImagePrompt = {
  positive_prompt?: string;
  negative_prompt?: string;
};

export type CharacterAssetSpec = {
  schema_version: number;
  autofill: AssetAutofillInfo;
  aliases: string[];
  entity_kind: string;
  species: string;
  body_type: string;
  facial_features: string;
  hair_or_surface: string;
  color_palette: string[];
  default_outfit: string;
  signature_features: string[];
  default_accessories: string[];
  state_variants: AssetStateVariant[];
  source_evidence: AssetSourceEvidence[];
  reference_plan: AssetReferencePlan;
  image_prompts?: Record<string, StoredImagePrompt>;
};

export type SceneAssetSpec = {
  schema_version: number;
  autofill: AssetAutofillInfo;
  aliases: string[];
  location_type: string;
  spatial_layout: string;
  fixed_landmarks: string[];
  materials: string[];
  color_palette: string[];
  zones: string[];
  state_variants: AssetStateVariant[];
  source_evidence: AssetSourceEvidence[];
  reference_plan: AssetReferencePlan;
  image_prompts?: Record<string, StoredImagePrompt>;
};

export type PropAssetSpec = {
  schema_version: number;
  autofill: AssetAutofillInfo;
  aliases: string[];
  shape: string;
  dimensions: string;
  materials: string[];
  color_palette: string[];
  signature_features: string[];
  scale_reference: string;
  holder_relation: string;
  story_function: string;
  state_variants: AssetStateVariant[];
  source_evidence: AssetSourceEvidence[];
  reference_plan: AssetReferencePlan;
  image_prompts?: Record<string, StoredImagePrompt>;
};

export type EntityBundle = {
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
};

export type Shot = {
  id: string;
  project_id: string;
  script_id: string | null;
  scene_id: string | null;
  shot_no: number;
  shot_batch_id: string | null;
  is_current: boolean;
  description: string;
  camera_shot: string | null;
  camera_movement: string | null;
  duration_sec: string | number | null;
  dialogue_ids: unknown[];
  character_ids: unknown[];
  prop_ids: unknown[];
  shot_card: Record<string, unknown>;
  image_prompt: string | null;
  video_prompt: string | null;
  negative_prompt: string | null;
  generation_mode: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ShotPromptPreview = {
  shot_id: string;
  shot_no: number;
  description: string;
  image_prompt: string | null;
  negative_prompt: string | null;
  provider_profile: string;
  reference_assets: Array<Record<string, unknown>>;
  compile_notes: string[];
  blockers: string[];
  warnings: string[];
  can_generate: boolean;
};

export type ShotVideoPromptPreview = {
  shot_id: string;
  prompt: string;
  input_fingerprint: string;
  current_fingerprint: string | null;
  stale: boolean;
  reference_tokens: string[];
  reference_assets: ShotReferenceAsset[];
};

export type ShotReferenceAsset = {
  asset_id: string;
  token: string;
  media_label: string;
  entity_type: "shot" | "character" | "scene" | "prop";
  entity_id: string;
  variant_key: string;
  reference_role: string;
  uri: string;
};

export type ReadinessCheck = {
  key: string;
  label: string;
  ok: boolean;
  message: string;
  severity: string;
};

export type ShotReadiness = {
  shot_id: string;
  shot_no: number;
  ready_for_image: boolean;
  ready_for_video: boolean;
  checks: ReadinessCheck[];
  image_blockers: string[];
  video_blockers: string[];
};

export type ProjectReadiness = {
  project_id: string;
  summary: {
    shot_count: number;
    image_ready_count: number;
    video_ready_count: number;
    image_blocker_count: number;
    video_blocker_count: number;
  };
  shots: ShotReadiness[];
};

export type QualityIssue = {
  severity: "error" | "warning" | string;
  code: string;
  message: string;
};

export type QualityCheck = {
  id: string;
  project_id: string;
  stage: string;
  target_type: string;
  target_id: string | null;
  shot_id: string | null;
  asset_id: string | null;
  method: string;
  status: "pass" | "warning" | "fail" | string;
  score: string | number;
  summary: string;
  issues: QualityIssue[];
  suggestions: string[];
  raw_response: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type Asset = {
  id: string;
  project_id: string;
  asset_type: string;
  asset_role: string | null;
  entity_type: string | null;
  entity_id: string | null;
  variant_key: string | null;
  source_task_id: string | null;
  source_stage_run_id: string | null;
  source_script_id: string | null;
  source_shot_batch_id: string | null;
  version: number;
  uri: string;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  duration_sec: string | number | null;
  provider: string | null;
  model: string | null;
  prompt: string | null;
  negative_prompt: string | null;
  raw_response: Record<string, unknown>;
  status: string;
  is_selected: boolean;
  created_at: string;
  updated_at: string;
};

export type AssetCandidate = {
  id: string;
  project_id: string;
  asset_id: string | null;
  source_task_id: string | null;
  source_stage_run_id: string | null;
  source_script_id: string | null;
  source_shot_batch_id: string | null;
  candidate_type: string;
  asset_type: string;
  asset_role: string | null;
  entity_type: string | null;
  entity_id: string | null;
  variant_key: string | null;
  version: number;
  uri: string;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  duration_sec: string | number | null;
  provider: string | null;
  model: string | null;
  prompt: string | null;
  negative_prompt: string | null;
  raw_response: Record<string, unknown>;
  status: string;
  review_note: string | null;
  promoted_asset_id: string | null;
  rejected_at: string | null;
  promoted_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ShotFramePrompt = {
  id: string;
  project_id: string;
  shot_id: string;
  frame_type: string;
  version: number;
  prompt: string;
  description: string | null;
  layout: string | null;
  source: string;
  provider: string | null;
  model: string | null;
  raw_response: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ShotFrameImage = {
  id: string;
  project_id: string;
  shot_id: string;
  frame_prompt_id: string | null;
  asset_id: string | null;
  frame_type: string;
  image_type: string;
  version: number;
  provider: string | null;
  model: string | null;
  prompt: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Dialogue = {
  id: string;
  project_id: string;
  script_id: string | null;
  character_id: string | null;
  shot_id: string | null;
  speaker_name: string;
  text: string;
  translation_zh: string | null;
  emotion: string | null;
  sequence_order: number;
  beat_id: string | null;
  sound_cues: string[];
  start_time: string | number | null;
  end_time: string | number | null;
  created_at: string;
  updated_at: string;
};

export type DialogueInput = {
  id?: string | null;
  shot_id?: string | null;
  character_id: string | null;
  speaker_name: string;
  text: string;
  translation_zh?: string | null;
  emotion?: string | null;
  sequence_order: number;
  beat_id?: string | null;
  sound_cues?: string[];
  start_time?: string | number | null;
  end_time?: string | number | null;
};

export type ExportRecord = {
  id: string;
  project_id: string;
  asset_id: string | null;
  resolution: string;
  duration_sec: string | number | null;
  format: string;
  subtitle_mode: "none" | "en" | "bilingual";
  ffmpeg_command: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type GenerationTaskStatus =
  | "queued"
  | "running"
  | "waiting_provider"
  | "waiting_children"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled";

export type TaskContextLink = {
  target_type: string;
  target_id: string;
  role: string;
  label: string | null;
};

export type GenerationTask = {
  id: string;
  project_id: string;
  task_type: string;
  parent_task_id: string | null;
  retry_of_task_id: string | null;
  resource_key: string | null;
  idempotency_key: string | null;
  provider: string | null;
  model: string | null;
  provider_task_id: string | null;
  input_payload: Record<string, unknown>;
  output_asset_ids: string[];
  result_payload: Record<string, unknown>;
  status: GenerationTaskStatus;
  progress: number;
  progress_label: string | null;
  retry_count: number;
  max_retries: number;
  error_code: string | null;
  error_message: string | null;
  cost_estimate: string | number | null;
  raw_response: Record<string, unknown>;
  child_summary: Record<string, number>;
  context_links: TaskContextLink[];
  heartbeat_at: string | null;
  cancel_requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};
