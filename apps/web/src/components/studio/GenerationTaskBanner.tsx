import type { GenerationTask, GenerationTaskStatus } from "../../types/stageFive";
import {
  canCancelGenerationTask,
  canRetryGenerationTask,
  childTaskCenterHref,
  generationTaskStatusLabel,
  taskCenterHref,
} from "../../utils/generationTask";

const CHILD_STATUS_ORDER: GenerationTaskStatus[] = [
  "queued",
  "running",
  "waiting_provider",
  "waiting_children",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
];

type GenerationTaskBannerProps = {
  actionBusy?: boolean;
  compact?: boolean;
  onCancel?: (task: GenerationTask) => void;
  onRetry?: (task: GenerationTask) => void;
  task: GenerationTask;
  title: string;
};

export function GenerationTaskBanner({
  actionBusy = false,
  compact = false,
  onCancel,
  onRetry,
  task,
  title,
}: GenerationTaskBannerProps) {
  const progress = Math.max(0, Math.min(100, task.progress));
  const childSummary = task.child_summary ?? {};
  const childCounts = CHILD_STATUS_ORDER
    .map((status) => [status, childSummary[status] ?? 0] as const)
    .filter(([, count]) => count > 0);

  return (
    <section className={`generation-task-banner ${task.status}${compact ? " compact" : ""}`}>
      <header>
        <div>
          <strong>{title}</strong>
          <span>{task.progress_label ?? generationTaskStatusLabel(task.status)}</span>
        </div>
        <em>{generationTaskStatusLabel(task.status)}</em>
      </header>
      <div
        aria-label={`${title}进度 ${progress}%`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={progress}
        className="generation-task-progress"
        role="progressbar"
      >
        <i style={{ width: `${progress}%` }} />
      </div>
      {childCounts.length ? (
        <div className="generation-task-children">
          {childCounts.map(([status, count]) => (
            <span key={status}>{generationTaskStatusLabel(status)} {count}</span>
          ))}
        </div>
      ) : null}
      {task.error_message ? <p>{task.error_message}</p> : null}
      <footer>
        <a href={taskCenterHref(task)}>任务 {task.id.slice(0, 8)}</a>
        {task.task_type === "project_video_candidate_batch" ? (
          <a href={childTaskCenterHref(task)}>查看镜头子任务</a>
        ) : null}
        <span />
        {onCancel && canCancelGenerationTask(task) ? (
          <button disabled={actionBusy} onClick={() => onCancel(task)} type="button">取消任务</button>
        ) : null}
        {onRetry && canRetryGenerationTask(task) ? (
          <button disabled={actionBusy} onClick={() => onRetry(task)} type="button">重新尝试</button>
        ) : null}
      </footer>
    </section>
  );
}
