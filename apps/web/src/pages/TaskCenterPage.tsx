import { useEffect, useState } from "react";

import { getGenerationTask, listGenerationTasks } from "../services/apiClient";
import type { GenerationTask } from "../types/stageFive";

const TASK_PAGE_SIZE = 25;
const TASK_STATUS_FILTERS = [
  { id: "all", label: "全部" },
  { id: "failed", label: "失败" },
  { id: "running", label: "运行中" },
  { id: "queued", label: "排队中" },
  { id: "succeeded", label: "已完成" },
  { id: "cancelled", label: "已取消" },
] as const;

type TaskStatusFilter = (typeof TASK_STATUS_FILTERS)[number]["id"];

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

function taskStatusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
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
    return "这是 V3.1 之前的历史任务记录；旧链路不会再运行。";
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

export function TaskCenterPage({
  initialProjectId,
  initialTaskId,
}: {
  initialProjectId?: string;
  initialTaskId?: string;
}) {
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const [focusedTask, setFocusedTask] = useState<GenerationTask | null>(null);
  const [totalTasks, setTotalTasks] = useState(0);
  const [statusFilter, setStatusFilter] = useState<TaskStatusFilter>("all");
  const [projectIdFilter, setProjectIdFilter] = useState<string | undefined>(initialProjectId);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingFocusedTask, setIsLoadingFocusedTask] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentPage = Math.floor(offset / TASK_PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(totalTasks / TASK_PAGE_SIZE));
  const canGoPrev = offset > 0;
  const canGoNext = offset + TASK_PAGE_SIZE < totalTasks;

  useEffect(() => {
    let ignore = false;

    async function loadTasks() {
      try {
        setIsLoading(true);
        setError(null);
        const data = await listGenerationTasks({
          projectId: projectIdFilter,
          status: statusFilter === "all" ? undefined : statusFilter,
          offset,
          limit: TASK_PAGE_SIZE,
        });
        if (!ignore) {
          setTasks(data.items);
          setTotalTasks(data.meta.total);
        }
      } catch (err) {
        if (!ignore) {
          setError(err instanceof Error ? err.message : "任务列表加载失败");
        }
      } finally {
        if (!ignore) {
          setIsLoading(false);
        }
      }
    }

    void loadTasks();

    return () => {
      ignore = true;
    };
  }, [offset, projectIdFilter, statusFilter]);

  useEffect(() => {
    let ignore = false;

    async function loadFocusedTask() {
      if (!initialTaskId) {
        setFocusedTask(null);
        return;
      }
      try {
        setIsLoadingFocusedTask(true);
        const task = await getGenerationTask(initialTaskId);
        if (!ignore) {
          setFocusedTask(task);
          setProjectIdFilter(task.project_id);
          setOffset(0);
        }
      } catch (err) {
        if (!ignore) {
          setError(err instanceof Error ? err.message : "任务详情加载失败");
        }
      } finally {
        if (!ignore) {
          setIsLoadingFocusedTask(false);
        }
      }
    }

    void loadFocusedTask();

    return () => {
      ignore = true;
    };
  }, [initialTaskId]);

  useEffect(() => {
    if (!initialTaskId) {
      setProjectIdFilter(initialProjectId);
      setOffset(0);
    }
  }, [initialProjectId, initialTaskId]);

  function changeStatusFilter(nextStatus: TaskStatusFilter) {
    setStatusFilter(nextStatus);
    setOffset(0);
  }

  const focusedTaskInList = focusedTask ? tasks.some((task) => task.id === focusedTask.id) : false;
  const visibleTasks = focusedTask && !focusedTaskInList ? [focusedTask, ...tasks] : tasks;

  return (
    <section className="panel task-center-page">
      <div className="section-header">
        <div>
          <h1>任务中心</h1>
          <p>
            查看生成任务、失败原因和可回跳的上下文。当前显示第 {currentPage}/{totalPages} 页，{tasks.length}/{totalTasks} 个任务。
            {projectIdFilter ? ` 已限定项目 ${projectIdFilter.slice(0, 8)}。` : ""}
          </p>
        </div>
        <a className="secondary-button" href="#/projects">
          返回项目列表
        </a>
      </div>

      <div className="task-center-toolbar">
        <div className="segmented-control">
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
        <div className="task-pagination">
          {projectIdFilter ? (
            <button className="secondary-button" disabled={isLoading} onClick={() => setProjectIdFilter(undefined)} type="button">
              查看全部项目
            </button>
          ) : null}
          <button className="secondary-button" disabled={!canGoPrev || isLoading} onClick={() => setOffset(Math.max(0, offset - TASK_PAGE_SIZE))} type="button">
            上一页
          </button>
          <span>{currentPage} / {totalPages}</span>
          <button className="secondary-button" disabled={!canGoNext || isLoading} onClick={() => setOffset(offset + TASK_PAGE_SIZE)} type="button">
            下一页
          </button>
        </div>
      </div>

      {isLoading ? <div className="state-box">正在加载任务...</div> : null}
      {isLoadingFocusedTask ? <div className="state-box">正在定位来源任务...</div> : null}
      {error ? <div className="state-box error">{error}</div> : null}

      {focusedTask ? (
        <div className="state-box success">
          已定位任务 {focusedTask.id.slice(0, 8)}，可从下方上下文链接回到项目、镜头、资产或候选。
        </div>
      ) : null}

      {!isLoading && !error && visibleTasks.length === 0 ? (
        <div className="empty-state">
          <h2>暂无任务</h2>
          <p>生成剧本、资产、分镜图或视频后，任务会出现在这里。</p>
        </div>
      ) : null}

      {!isLoading && !error && visibleTasks.length > 0 ? (
        <div className="task-list">
          {visibleTasks.map((task) => (
            <article className={`task-row ${task.status} ${focusedTask?.id === task.id ? "focused" : ""}`} key={task.id}>
              <div className="task-row-main">
                <div>
                  <strong>{task.task_type}</strong>
                  <span>{task.provider ?? "provider"} · {task.model ?? "model"}</span>
                </div>
                <em>{taskStatusLabel(task.status)}</em>
              </div>
              <div className="task-progress-line">
                <div style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }} />
              </div>
              <div className="task-row-meta">
                <span>{task.progress_label ?? "无进度说明"}</span>
                <time dateTime={task.updated_at}>{formatTime(task.updated_at)}</time>
              </div>
              {task.error_message ? <p className="task-error">{task.error_message}</p> : null}
              {scriptQualityGate(task) ? (
                <span className={`script-quality-gate ${scriptQualityGate(task)?.gate}`}>
                  {scriptQualityGate(task)?.label}
                </span>
              ) : null}
              {scriptQualitySummary(task) ? <p className="task-quality-note">{scriptQualitySummary(task)}</p> : null}
              <div className="task-context-links">
                {task.context_links.map((link) => {
                  const href = contextLinkHref(task, link.target_type, link.target_id);
                  const label = `${link.label ?? linkTypeLabel(link.target_type)} · ${link.role}`;
                  return href ? (
                    <a href={href} key={`${link.target_type}:${link.target_id}:${link.role}`}>
                      {label}
                    </a>
                  ) : (
                    <span key={`${link.target_type}:${link.target_id}:${link.role}`}>{label}</span>
                  );
                })}
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
