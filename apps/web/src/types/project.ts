export type SubtitleMode = "none" | "en" | "bilingual";
export type ProjectInputMode = "ai_brief" | "imported_script";

export type CreativeSettings = {
  english_level: "A1" | "A2";
  animation_style: string;
  dialogue_language: "en";
  translation_language: "zh-CN" | null;
  default_subtitle_mode: SubtitleMode;
};

export type Chapter = {
  id: string;
  input_mode: ProjectInputMode;
  outline: string;
  source_text: string;
  source_word_count: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Project = {
  id: string;
  workspace_id: string | null;
  user_id: string | null;
  title: string;
  style: string;
  target_duration_sec: number | null;
  resolution: "854x480" | "480x854" | "1280x720" | "720x1280";
  aspect_ratio: "16:9" | "9:16";
  creative_settings: CreativeSettings;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ProjectStageRun = {
  id: string;
  project_id: string;
  stage: "script" | "assets" | "production" | "export" | string;
  action: string;
  status: string;
  task_id: string | null;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  run_metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ProjectDetail = Project & {
  chapters: Chapter[];
};

export type ProjectDeletionPreview = {
  project_id: string;
  title: string;
  record_counts: Record<string, number>;
  total_related_records: number;
  active_task_count: number;
  active_stage_run_count: number;
  storage_present: boolean;
  storage_file_count: number;
  storage_bytes: number;
  can_delete: boolean;
  blocker_reasons: string[];
};

export type ProjectDeleteResult = {
  project_id: string;
  title: string;
  deleted_records: number;
  storage_action: "moved_to_trash" | "not_found";
  storage_trash_path: string | null;
};

export type CreateProjectPayload = {
  title: string;
  input_mode: ProjectInputMode;
  outline?: string;
  source_text?: string;
  style?: string;
  target_duration_sec?: number | null;
  aspect_ratio: "16:9" | "9:16";
  resolution: "854x480" | "480x854";
  creative_settings: CreativeSettings;
};

export type UpsertChapterPayload = {
  input_mode: ProjectInputMode;
  outline: string;
  source_text: string;
};
