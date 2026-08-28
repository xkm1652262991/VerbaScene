import type { GenerationTask, GenerationTaskStatus } from "../types/stageFive";

export const ACTIVE_GENERATION_TASK_STATUSES: readonly GenerationTaskStatus[] = [
  "queued",
  "running",
  "waiting_provider",
  "waiting_children",
  "cancelling",
];

export const TERMINAL_GENERATION_TASK_STATUSES: readonly GenerationTaskStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
];

export function isGenerationTaskActive(taskOrStatus: GenerationTask | GenerationTaskStatus) {
  const status = typeof taskOrStatus === "string" ? taskOrStatus : taskOrStatus.status;
  return ACTIVE_GENERATION_TASK_STATUSES.includes(status);
}

export function isGenerationTaskTerminal(taskOrStatus: GenerationTask | GenerationTaskStatus) {
  const status = typeof taskOrStatus === "string" ? taskOrStatus : taskOrStatus.status;
  return TERMINAL_GENERATION_TASK_STATUSES.includes(status);
}

export function canCancelGenerationTask(task: GenerationTask) {
  return isGenerationTaskActive(task) && task.status !== "cancelling";
}

export function canRetryGenerationTask(task: GenerationTask) {
  return (
    (task.status === "failed" || task.status === "cancelled")
    && (
      task.task_type === "script_generation"
      || task.task_type === "shot_breakdown"
      || task.task_type === "shot_video_candidate_generation"
    )
  );
}

export function generationTaskStatusLabel(status: GenerationTaskStatus | string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    waiting_provider: "等待 Provider",
    waiting_children: "等待子任务",
    cancelling: "取消中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}

export function generationTaskTypeLabel(taskType: string) {
  const labels: Record<string, string> = {
    script_generation: "剧本生成",
    shot_video_candidate_generation: "镜头视频生成",
    project_video_candidate_batch: "项目视频批次",
    single_shot_video_candidate_generation: "历史镜头视频生成",
    shot_video_regeneration_candidate: "历史镜头视频重生成",
    video_frame_extraction: "视频截帧",
    single_reference_image_candidate_generation: "单资产图片生成",
    shot_breakdown: "分镜导演",
    entity_extraction: "实体提取",
    dialogue_audio_generation: "历史对白音频生成",
    speech_script_generation: "历史对白稿生成",
  };
  return labels[taskType] ?? taskType;
}

export function mergeGenerationTask(tasks: GenerationTask[], nextTask: GenerationTask) {
  return [nextTask, ...tasks.filter((task) => task.id !== nextTask.id)]
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
}

export function taskCenterHref(task: GenerationTask) {
  const params = new URLSearchParams({
    project_id: task.project_id,
    task_id: task.id,
  });
  return `#/tasks?${params.toString()}`;
}

export function childTaskCenterHref(task: GenerationTask) {
  const params = new URLSearchParams({
    project_id: task.project_id,
    parent_task_id: task.id,
  });
  return `#/tasks?${params.toString()}`;
}

export function createTaskIdempotencyKey(scope: string) {
  const randomPart = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${scope}:${randomPart}`;
}
