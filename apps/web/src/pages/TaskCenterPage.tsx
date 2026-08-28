import { useEffect, useState } from "react";

import {
  cancelGenerationTask,
  getGenerationTask,
  listGenerationTasks,
  retryGenerationTask,
} from "../services/apiClient";
import type { GenerationTask, GenerationTaskStatus } from "../types/stageFive";
import {
  ACTIVE_GENERATION_TASK_STATUSES,
  TERMINAL_GENERATION_TASK_STATUSES,
  canCancelGenerationTask,
  canRetryGenerationTask,
  generationTaskStatusLabel,
  generationTaskTypeLabel,
  isGenerationTaskActive,
  mergeGenerationTask,
} from "../utils/generationTask";

const TASK_PAGE_SIZE = 25;
const TASK_STATUS_FILTERS: Array<{ id: "all" | GenerationTaskStatus; label: string }> = [
  { id: "all", label: "全部" },
  { id: "queued", label: "排队" },
  { id: "running", label: "运行" },
  { id: "waiting_provider", label: "等 Provider" },
  { id: "waiting_children", label: "等子任务" },
  { id: "cancelling", label: "取消中" },
  { id: "failed", label: "失败" },
  { id: "succeeded", label: "完成" },
  { id: "cancelled", label: "已取消" },
];
const TASK_TYPE_FILTERS = [
  { id: "all", label: "全部类型" },
  { id: "script_generation", label: "剧本生成" },
  { id: "shot_breakdown", label: "分镜导演" },
  { id: "image_candidate_generation", label: "图片生成" },
  { id: "reference_image_candidate_batch", label: "角色场景图片批次" },
  { id: "shot_image_candidate_batch", label: "分镜图片批次" },
  { id: "shot_video_candidate_generation", label: "镜头视频" },
  { id: "project_video_candidate_batch", label: "视频批次" },
  { id: "video_frame_extraction", label: "视频截帧" },
  { id: "project_export", label: "成片导出" },
] as const;

type TaskStatusFilter = (typeof TASK_STATUS_FILTERS)[number]["id"];
type TaskTypeFilter = (typeof TASK_TYPE_FILTERS)[number]["id"];

type TaskCenterPageProps = {
  initialParentTaskId?: string;
  initialProjectId?: string;
  initialResourceKey?: string;
  initialTaskId?: string;
  initialTaskType?: string;
};

function formatTime(value: string | null) {
  if (!value) {
    return "未记录";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function linkTypeLabel(type: string) {
  const labels: Record<string, string> = {
    project: "项目",
    chapter: "章节",
    script: "剧本",
    shot: "镜头",
    asset: "资产",
    asset_candidate: "候选",
    character: "角色",
    scene: "场景",
    prop: "道具",
  };
  return labels[type] ?? type;
}

function scriptQualitySummary(task: GenerationTask) {
  if (task.task_type !== "script_generation" || task.status !== "succeeded") {
    return null;
  }
  const qualityGate = typeof task.result_payload.quality_gate === "string"
    ? task.result_payload.quality_gate
    : "legacy_record";
  const issueCounts = task.result_payload.issue_counts && typeof task.result_payload.issue_counts === "object"
    ? task.result_payload.issue_counts as Record<string, unknown>
    : {};
  const mustFixCount = typeof issueCounts.must_fix === "number" ? issueCounts.must_fix : 0;
  const patchApplied = task.result_payload.patch_applied === true;
  const patchedSceneNos = Array.isArray(task.result_payload.patched_scene_nos)
    ? task.result_payload.patched_scene_nos.filter((value): value is number => typeof value === "number")
    : [];
  const unresolvedCodes = Array.isArray(task.result_payload.unresolved_issue_codes)
    ? task.result_payload.unresolved_issue_codes.filter((value): value is string => typeof value === "string")
    : [];
  if (qualityGate === "legacy_record") {
    return "这是统一任务运行时升级前的历史记录。";
  }
  if (qualityGate === "review_unavailable") {
    return "剧本结构有效，但综合审稿本次不可用；系统如实保留了可编辑初稿。";
  }
  if (qualityGate === "needs_attention") {
    const patchSummary = patchedSceneNos.length ? `已定点替换场景 ${patchedSceneNos.join("、")}；` : "未应用场景替换；";
    const unresolvedSummary = unresolvedCodes.length ? `待处理 ${unresolvedCodes.join("、")}。` : "仍有生产合同问题。";
    return `多 Agent 协作完成，${patchSummary}${unresolvedSummary}`;
  }
  if (patchApplied) {
    return `综合审稿发现 ${mustFixCount} 个必须修复项，已执行一次定点修订；确定性生产合同已校验，叙事修复仍由创作者最终判断。`;
  }
  return "故事架构、生产写作和综合审稿已完成；没有自动制造修改项。";
}

function scriptQualityGate(task: GenerationTask) {
  if (task.task_type !== "script_generation" || task.status !== "succeeded") {
    return null;
  }
  const gate = typeof task.result_payload.quality_gate === "string"
    ? task.result_payload.quality_gate
    : "legacy_record";
  const labels: Record<string, string> = {
    pass: "协作检查通过",
    needs_attention: "需要人工检查",
    review_unavailable: "审稿不可用",
    legacy_record: "历史任务记录",
  };
  return { gate, label: labels[gate] ?? gate };
}

function shotDirectionQualitySummary(task: GenerationTask) {
  if (task.task_type !== "shot_breakdown" || task.status !== "succeeded") {
    return null;
  }
  const report = task.result_payload.director_report;
  if (!report || typeof report !== "object" || Array.isArray(report)) {
    return "这是反思式分镜导演上线前的历史记录。";
  }
  const qualityGate = typeof task.result_payload.quality_gate === "string"
    ? task.result_payload.quality_gate
    : "legacy_record";
  const patchApplied = task.result_payload.patch_applied === true;
  const patchedShotNos = Array.isArray(task.result_payload.patched_shot_nos)
    ? task.result_payload.patched_shot_nos.filter((value): value is number => typeof value === "number")
    : [];
  const missingAssets = Array.isArray(task.result_payload.asset_gaps)
    ? task.result_payload.asset_gaps.length
    : 0;
  const unresolvedCodes = Array.isArray(task.result_payload.unresolved_issue_codes)
    ? task.result_payload.unresolved_issue_codes.filter((value): value is string => typeof value === "string")
    : [];
  const readiness = task.result_payload.generation_ready === true
    ? "默认参考资产已齐全"
    : `建议补充 ${missingAssets} 项角色或场景参考图`;
  if (qualityGate === "review_unavailable") {
    return `草案生产合同有效，但分镜审稿本次不可用；${readiness}。`;
  }
  if (qualityGate === "needs_attention") {
    const patchSummary = patchApplied
      ? `已定点修订片段 ${patchedShotNos.join("、")}`
      : "未应用定点修订";
    return `${patchSummary}；${unresolvedCodes.length ? `待检查 ${unresolvedCodes.join("、")}` : "仍建议人工检查"}；${readiness}。`;
  }
  return `草案、独立审稿和确定性合同已完成；${readiness}。`;
}

function shotDirectionQualityGate(task: GenerationTask) {
  if (task.task_type !== "shot_breakdown" || task.status !== "succeeded") {
    return null;
  }
  const gate = typeof task.result_payload.quality_gate === "string"
    ? task.result_payload.quality_gate
    : "legacy_record";
  const labels: Record<string, string> = {
    pass: "导演检查通过",
    needs_attention: "需要人工检查",
    review_unavailable: "审稿不可用",
    legacy_record: "历史任务记录",
  };
  return { gate, label: labels[gate] ?? gate };
}

function taskQualitySummary(task: GenerationTask) {
  return shotDirectionQualitySummary(task) ?? scriptQualitySummary(task);
}

function taskQualityGate(task: GenerationTask) {
  return shotDirectionQualityGate(task) ?? scriptQualityGate(task);
}

function contextLinkHref(task: GenerationTask, targetType: string, targetId: string) {
  if (targetType === "project") {
    return `#/projects/${targetId}`;
  }
  const projectLink = task.context_links.find((link) => link.target_type === "project");
  if (projectLink && (targetType === "chapter" || targetType === "script")) {
    return `#/projects/${projectLink.target_id}/chapter`;
  }
  if (projectLink && targetType === "shot") {
    return `#/projects/${projectLink.target_id}/shots/${targetId}`;
  }
  if (projectLink && targetType === "asset") {
    return `#/projects/${projectLink.target_id}/assets?asset_id=${encodeURIComponent(targetId)}`;
  }
  if (projectLink && targetType === "asset_candidate") {
    return `#/projects/${projectLink.target_id}/assets?candidate_id=${encodeURIComponent(targetId)}`;
  }
  if (projectLink && (targetType === "character" || targetType === "scene" || targetType === "prop")) {
    const params = new URLSearchParams({ owner_type: targetType, owner_id: targetId });
    return `#/projects/${projectLink.target_id}/assets?${params.toString()}`;
  }
  return null;
}

function taskLink(taskId: string, projectId: string) {
  const params = new URLSearchParams({ project_id: projectId, task_id: taskId });
  return `#/tasks?${params.toString()}`;
}

function childTasksLink(task: GenerationTask) {
  const params = new URLSearchParams({ project_id: task.project_id, parent_task_id: task.id });
  return `#/tasks?${params.toString()}`;
}

export function TaskCenterPage({
  initialParentTaskId,
  initialProjectId,
  initialResourceKey,
  initialTaskId,
  initialTaskType,
}: TaskCenterPageProps) {
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const [focusedTask, setFocusedTask] = useState<GenerationTask | null>(null);
  const [focusedTaskId, setFocusedTaskId] = useState<string | undefined>(initialTaskId);
  const [totalTasks, setTotalTasks] = useState(0);
  const [statusFilter, setStatusFilter] = useState<TaskStatusFilter>("all");
  const [taskTypeFilter, setTaskTypeFilter] = useState<TaskTypeFilter>(
    TASK_TYPE_FILTERS.some((item) => item.id === initialTaskType)
      ? initialTaskType as TaskTypeFilter
      : "all",
  );
  const [projectIdFilter, setProjectIdFilter] = useState<string | undefined>(initialProjectId);
  const [parentTaskIdFilter, setParentTaskIdFilter] = useState<string | undefined>(initialParentTaskId);
  const [resourceKeyFilter, setResourceKeyFilter] = useState<string | undefined>(initialResourceKey);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingFocusedTask, setIsLoadingFocusedTask] = useState(false);
  const [actionTaskId, setActionTaskId] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const currentPage = Math.floor(offset / TASK_PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(totalTasks / TASK_PAGE_SIZE));
  const canGoPrev = offset > 0;
  const canGoNext = offset + TASK_PAGE_SIZE < totalTasks;

  useEffect(() => {
    let ignore = false;
    let timer: number | undefined;
    let hasLoaded = false;

    async function loadTasks(initialLoad = false) {
      let nextDelay = 8000;
      try {
        if (initialLoad) setIsLoading(true);
        const data = await listGenerationTasks({
          projectId: projectIdFilter,
          status: statusFilter === "all" ? undefined : statusFilter,
          parentTaskId: parentTaskIdFilter,
          taskType: taskTypeFilter === "all" ? undefined : taskTypeFilter,
          resourceKey: resourceKeyFilter,
          offset,
          limit: TASK_PAGE_SIZE,
        });
        if (!ignore) {
          hasLoaded = true;
          setError(null);
          setTasks(data.items);
          setTotalTasks(data.meta.total);
          setFocusedTask((current) => (
            current ? data.items.find((task) => task.id === current.id) ?? current : null
          ));
          nextDelay = data.items.some(isGenerationTaskActive) ? 2000 : 8000;
        }
      } catch (err) {
        if (!ignore && !hasLoaded) {
          setError(err instanceof Error ? err.message : "任务列表加载失败");
        }
      } finally {
        if (!ignore) {
          setIsLoading(false);
          timer = window.setTimeout(() => void loadTasks(), nextDelay);
        }
      }
    }

    void loadTasks(true);
    return () => {
      ignore = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [offset, parentTaskIdFilter, projectIdFilter, refreshVersion, resourceKeyFilter, statusFilter, taskTypeFilter]);

  useEffect(() => {
    let ignore = false;
    let timer: number | undefined;

    async function loadFocusedTask(initialLoad = false) {
      if (!focusedTaskId) {
        setFocusedTask(null);
        return;
      }
      try {
        if (initialLoad) setIsLoadingFocusedTask(true);
        const task = await getGenerationTask(focusedTaskId);
        if (!ignore) {
          setFocusedTask(task);
          setProjectIdFilter(task.project_id);
          setOffset(0);
          if (isGenerationTaskActive(task)) {
            timer = window.setTimeout(() => void loadFocusedTask(), 2000);
          }
        }
      } catch (err) {
        if (!ignore) {
          setError(err instanceof Error ? err.message : "任务详情加载失败");
        }
      } finally {
        if (!ignore) setIsLoadingFocusedTask(false);
      }
    }

    void loadFocusedTask(true);
    return () => {
      ignore = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [focusedTaskId, refreshVersion]);

  useEffect(() => {
    setFocusedTaskId(initialTaskId);
    if (!initialTaskId) {
      setProjectIdFilter(initialProjectId);
      setOffset(0);
    }
    setParentTaskIdFilter(initialParentTaskId);
    setResourceKeyFilter(initialResourceKey);
    setTaskTypeFilter(
      TASK_TYPE_FILTERS.some((item) => item.id === initialTaskType)
        ? initialTaskType as TaskTypeFilter
        : "all",
    );
  }, [initialParentTaskId, initialProjectId, initialResourceKey, initialTaskId, initialTaskType]);

  function changeStatusFilter(nextStatus: TaskStatusFilter) {
    setStatusFilter(nextStatus);
    setOffset(0);
  }

  function replaceTask(nextTask: GenerationTask) {
    setTasks((current) => mergeGenerationTask(current, nextTask));
    setFocusedTask((current) => current?.id === nextTask.id ? nextTask : current);
    setRefreshVersion((current) => current + 1);
  }

  async function cancelTask(task: GenerationTask) {
    try {
      setActionTaskId(task.id);
      setError(null);
      setNotice(null);
      const updated = await cancelGenerationTask(task.id);
      replaceTask(updated);
      setNotice(updated.status === "cancelled" ? "排队任务已取消。" : "取消请求已提交，任务会在下一检查点停止。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "取消任务失败");
    } finally {
      setActionTaskId("");
    }
  }

  async function retryTask(task: GenerationTask) {
    try {
      setActionTaskId(task.id);
      setError(null);
      setNotice(null);
      const retried = await retryGenerationTask(task.id);
      setTasks((current) => mergeGenerationTask(current, retried));
      setFocusedTask(retried);
      setFocusedTaskId(retried.id);
      setProjectIdFilter(retried.project_id);
      setRefreshVersion((current) => current + 1);
      setNotice(`已创建重试任务 ${retried.id.slice(0, 8)}，原始输入快照保持不变。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "重试任务失败");
    } finally {
      setActionTaskId("");
    }
  }

  const focusedTaskInList = focusedTask ? tasks.some((task) => task.id === focusedTask.id) : false;
  const visibleTasks = focusedTask && !focusedTaskInList ? [focusedTask, ...tasks] : tasks;

  return (
    <section className="panel task-center-page">
      <div className="section-header">
        <div>
          <h1>任务中心</h1>
          <p>
            查看真实后台状态、父子批次、失败原因和资源上下文。当前第 {currentPage}/{totalPages} 页，{tasks.length}/{totalTasks} 个任务。
            {projectIdFilter ? ` 已限定项目 ${projectIdFilter.slice(0, 8)}。` : ""}
          </p>
        </div>
        <a className="secondary-button" href="#/projects">返回项目列表</a>
      </div>

      <div className="task-center-toolbar">
        <div className="segmented-control task-status-filters">
          {TASK_STATUS_FILTERS.map((filter) => (
            <button
              className={statusFilter === filter.id ? "active" : ""}
              key={filter.id}
              onClick={() => changeStatusFilter(filter.id)}
              type="button"
            >
              {filter.label}
            </button>
          ))}
        </div>
        <div className="task-filter-controls">
          <select
            aria-label="任务类型"
            onChange={(event) => { setTaskTypeFilter(event.target.value as TaskTypeFilter); setOffset(0); }}
            value={taskTypeFilter}
          >
            {TASK_TYPE_FILTERS.map((filter) => <option key={filter.id} value={filter.id}>{filter.label}</option>)}
          </select>
          {parentTaskIdFilter ? (
            <button className="secondary-button" onClick={() => { setParentTaskIdFilter(undefined); setOffset(0); }} type="button">
              清除批次子任务筛选
            </button>
          ) : null}
          {resourceKeyFilter ? (
            <button className="secondary-button" onClick={() => { setResourceKeyFilter(undefined); setOffset(0); }} type="button">
              清除资源筛选
            </button>
          ) : null}
          {projectIdFilter ? (
            <button className="secondary-button" disabled={isLoading} onClick={() => { setProjectIdFilter(undefined); setOffset(0); }} type="button">
              查看全部项目
            </button>
          ) : null}
        </div>
        <div className="task-pagination">
          <button className="secondary-button" disabled={!canGoPrev || isLoading} onClick={() => setOffset(Math.max(0, offset - TASK_PAGE_SIZE))} type="button">上一页</button>
          <span>{currentPage} / {totalPages}</span>
          <button className="secondary-button" disabled={!canGoNext || isLoading} onClick={() => setOffset(offset + TASK_PAGE_SIZE)} type="button">下一页</button>
        </div>
      </div>

      {isLoading ? <div className="state-box">正在加载任务...</div> : null}
      {isLoadingFocusedTask ? <div className="state-box">正在定位来源任务...</div> : null}
      {notice ? <div className="state-box success">{notice}</div> : null}
      {error ? <div className="state-box error">{error}</div> : null}

      {focusedTask ? (
        <div className="state-box success">
          已定位任务 {focusedTask.id.slice(0, 8)}，状态会自动刷新；下方可返回项目、镜头、资产或候选。
        </div>
      ) : null}

      {!isLoading && !error && visibleTasks.length === 0 ? (
        <div className="empty-state"><h2>暂无任务</h2><p>当前筛选条件下没有生成任务。</p></div>
      ) : null}

      {!isLoading && visibleTasks.length > 0 ? (
        <div className="task-list">
          {visibleTasks.map((task) => {
            const qualityGate = taskQualityGate(task);
            const childSummary = task.child_summary ?? {};
            const childCounts = [...ACTIVE_GENERATION_TASK_STATUSES, ...TERMINAL_GENERATION_TASK_STATUSES]
              .map((status) => [status, childSummary[status] ?? 0] as const)
              .filter(([, count]) => count > 0);
            return (
              <article className={`task-row ${task.status} ${focusedTask?.id === task.id ? "focused" : ""}`} key={task.id}>
                <div className="task-row-main">
                  <div>
                    <strong>{generationTaskTypeLabel(task.task_type)}</strong>
                    <span>{task.provider ?? "本地协调器"} · {task.model ?? "无模型"}</span>
                  </div>
                  <em>{generationTaskStatusLabel(task.status)}</em>
                </div>
                <div className="task-progress-line" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={task.progress}>
                  <div style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }} />
                </div>
                <div className="task-row-meta">
                  <span>{task.progress_label ?? "无进度说明"}</span>
                  <span>
                    {typeof task.max_retries === "number"
                      ? `重试 ${task.retry_count ?? 0}/${task.max_retries}`
                      : `已重试 ${task.retry_count ?? 0} 次`}
                  </span>
                  <time dateTime={task.updated_at}>{formatTime(task.updated_at)}</time>
                </div>
                <div className="task-identity-grid">
                  <span><b>任务</b><code>{task.id}</code></span>
                  {task.resource_key ? <span><b>资源</b><code>{task.resource_key}</code></span> : null}
                  {task.provider_task_id ? <span><b>Provider</b><code>{task.provider_task_id}</code></span> : null}
                  {task.heartbeat_at && isGenerationTaskActive(task) ? <span><b>心跳</b>{formatTime(task.heartbeat_at)}</span> : null}
                </div>
                {childCounts.length ? (
                  <div className="task-child-summary">
                    <strong>镜头子任务</strong>
                    {childCounts.map(([status, count]) => <span key={status}>{generationTaskStatusLabel(status)} {count}</span>)}
                    <a href={childTasksLink(task)}>查看全部</a>
                  </div>
                ) : null}
                {task.parent_task_id || task.retry_of_task_id ? (
                  <div className="task-lineage-links">
                    {task.parent_task_id ? <a href={taskLink(task.parent_task_id, task.project_id)}>父任务 {task.parent_task_id.slice(0, 8)}</a> : null}
                    {task.retry_of_task_id ? <a href={taskLink(task.retry_of_task_id, task.project_id)}>来源任务 {task.retry_of_task_id.slice(0, 8)}</a> : null}
                  </div>
                ) : null}
                {task.error_message ? <p className="task-error"><strong>{task.error_code ?? "task_failed"}</strong> · {task.error_message}</p> : null}
                {qualityGate ? <span className={`script-quality-gate ${qualityGate.gate}`}>{qualityGate.label}</span> : null}
                {taskQualitySummary(task) ? <p className="task-quality-note">{taskQualitySummary(task)}</p> : null}
                <div className="task-context-links">
                  {task.context_links.map((link) => {
                    const href = contextLinkHref(task, link.target_type, link.target_id);
                    const label = `${link.label ?? linkTypeLabel(link.target_type)} · ${link.role}`;
                    return href
                      ? <a href={href} key={`${link.target_type}:${link.target_id}:${link.role}`}>{label}</a>
                      : <span key={`${link.target_type}:${link.target_id}:${link.role}`}>{label}</span>;
                  })}
                </div>
                <div className="task-row-actions">
                  {canCancelGenerationTask(task) ? (
                    <button className="danger-button" disabled={Boolean(actionTaskId)} onClick={() => void cancelTask(task)} type="button">
                      {actionTaskId === task.id ? "正在提交…" : "取消任务"}
                    </button>
                  ) : null}
                  {canRetryGenerationTask(task) ? (
                    <button className="secondary-button" disabled={Boolean(actionTaskId)} onClick={() => void retryTask(task)} type="button">
                      {actionTaskId === task.id ? "正在创建…" : "使用原输入重试"}
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
