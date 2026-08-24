import type { ProjectStageRun } from "../../types/project";

type StageRunSummaryProps = {
  isBusy: boolean;
  onCancel: (run: ProjectStageRun) => void;
  runs: ProjectStageRun[];
};

export function StageRunSummary({ isBusy, onCancel, runs }: StageRunSummaryProps) {
  if (runs.length === 0) {
    return (
      <div className="stage-run-summary">
        <span>运行记录</span>
        <p>暂无运行记录</p>
      </div>
    );
  }

  return (
    <div className="stage-run-summary">
      <span>运行记录</span>
      <div className="stage-run-list">
        {runs.map((run) => {
          const failureReason = getStageRunFailureReason(run);
          return (
            <article className={`stage-run-item ${run.status}`} key={run.id}>
              <strong>{stageRunStatusLabel(run.status)}</strong>
              <em>{formatDateTime(run.started_at ?? run.created_at)}</em>
              {run.task_id ? <code>{run.task_id.slice(0, 8)}</code> : null}
              {run.error_message ? <p>{run.error_message}</p> : null}
              {failureReason?.user_message ? <p>{failureReason.user_message}</p> : null}
              {failureReason?.suggested_action ? <p>{failureReason.suggested_action}</p> : null}
              {run.run_metadata.retry_policy === "manual_regeneration" ? (
                <p>图片/视频失败后不自动重试，请按需手动重新生成。</p>
              ) : null}
              {run.status === "running" || run.status === "queued" ? (
                <button className="text-button" disabled={isBusy} onClick={() => onCancel(run)} type="button">
                  取消运行
                </button>
              ) : null}
            </article>
          );
        })}
      </div>
    </div>
  );
}

function stageRunStatusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };

  return labels[status] ?? status;
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function getStageRunFailureReason(run: ProjectStageRun) {
  const failureReason = run.run_metadata.failure_reason;
  if (!failureReason || typeof failureReason !== "object" || Array.isArray(failureReason)) {
    return null;
  }
  return failureReason as {
    category?: string;
    code?: string;
    retryable?: boolean;
    suggested_action?: string;
    user_message?: string;
  };
}
