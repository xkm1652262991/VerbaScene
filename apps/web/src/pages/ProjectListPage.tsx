import { useEffect, useRef, useState } from "react";

import { ProjectCreationLauncher } from "../components/studio/ProjectCreationLauncher";
import { deleteProject, getProjectDeletionPreview, listProjects } from "../services/apiClient";
import type { Project, ProjectDeletionPreview } from "../types/project";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  if (value < 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    draft: "草稿",
    in_progress: "生产中",
    ready_to_export: "可导出",
    exported: "已导出",
    archived: "已归档",
  };

  return labels[status] ?? status;
}

export function ProjectListPage({ autoFocusCreation = false }: { autoFocusCreation?: boolean }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [deletePreview, setDeletePreview] = useState<ProjectDeletionPreview | null>(null);
  const [confirmationTitle, setConfirmationTitle] = useState("");
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const previewRequestId = useRef(0);

  useEffect(() => {
    let ignore = false;

    async function loadProjects() {
      try {
        setIsLoading(true);
        setError(null);
        const data = await listProjects();
        if (!ignore) {
          setProjects(data);
        }
      } catch (err) {
        if (!ignore) {
          setError(err instanceof Error ? err.message : "项目列表加载失败");
        }
      } finally {
        if (!ignore) {
          setIsLoading(false);
        }
      }
    }

    void loadProjects();

    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!deleteTarget) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isDeleting) {
        closeDeleteDialog();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [deleteTarget, isDeleting]);

  async function openDeleteDialog(project: Project) {
    const requestId = ++previewRequestId.current;
    setDeleteTarget(project);
    setDeletePreview(null);
    setConfirmationTitle("");
    setDeleteError(null);
    setIsPreviewLoading(true);
    try {
      const preview = await getProjectDeletionPreview(project.id);
      if (previewRequestId.current === requestId) {
        setDeletePreview(preview);
      }
    } catch (err) {
      if (previewRequestId.current === requestId) {
        setDeleteError(err instanceof Error ? err.message : "删除影响读取失败");
      }
    } finally {
      if (previewRequestId.current === requestId) {
        setIsPreviewLoading(false);
      }
    }
  }

  function closeDeleteDialog() {
    if (isDeleting) {
      return;
    }
    previewRequestId.current += 1;
    setDeleteTarget(null);
    setDeletePreview(null);
    setConfirmationTitle("");
    setDeleteError(null);
    setIsPreviewLoading(false);
  }

  async function confirmDeleteProject() {
    if (!deleteTarget || !deletePreview || !deletePreview.can_delete) {
      return;
    }
    if (confirmationTitle.trim() !== deleteTarget.title.trim()) {
      setDeleteError("请输入完整项目名称后再删除。");
      return;
    }
    try {
      setIsDeleting(true);
      setDeleteError(null);
      const result = await deleteProject(deleteTarget.id, confirmationTitle);
      setProjects((current) => current.filter((project) => project.id !== deleteTarget.id));
      setNotice(
        result.storage_action === "moved_to_trash"
          ? `项目“${result.title}”已删除，${result.deleted_records} 条数据库记录已移除，本地媒体已移入回收目录。`
          : `项目“${result.title}”已删除，${result.deleted_records} 条数据库记录已移除。`,
      );
      setDeleteTarget(null);
      setDeletePreview(null);
      setConfirmationTitle("");
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "项目删除失败");
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <div className="project-home">
      <ProjectCreationLauncher autoFocus={autoFocusCreation} />

      <section className="panel project-library">
        <div className="section-header">
          <div>
            <span className="page-eyebrow">MY PRODUCTIONS</span>
            <h1>我的项目</h1>
            <p>继续最近的动画英语短剧，或查看制作和导出状态。</p>
          </div>
          <div className="section-actions">
            <a className="secondary-button" href="#/tasks">任务中心</a>
            <a className="secondary-button" href="#/models">模型管理</a>
          </div>
        </div>

        {notice ? <div className="state-box success" role="status">{notice}</div> : null}
        {isLoading ? <div className="state-box">正在加载项目...</div> : null}
        {error ? <div className="state-box error">{error}</div> : null}

        {!isLoading && !error && projects.length === 0 ? (
          <div className="empty-state">
            <h2>还没有项目</h2>
            <p>从上方导入剧本或描述一个故事想法即可开始。</p>
          </div>
        ) : null}

        {!isLoading && !error && projects.length > 0 ? (
          <div className="project-grid">
            {projects.map((project, index) => (
              <article className="project-card" data-status={project.status} data-tone={index % 4} key={project.id}>
                <a className="project-card-link" href={`#/projects/${project.id}`}>
                  <div className="project-card-visual">
                    <span className="project-status-chip">{statusLabel(project.status)}</span>
                    <span className="project-format-chip">{project.aspect_ratio} · {project.resolution}</span>
                    <strong aria-hidden="true">{project.title.trim().slice(0, 2) || "AI"}</strong>
                  </div>
                  <div className="project-card-body">
                    <div className="project-card-head">
                      <strong>{project.title}</strong>
                      <time dateTime={project.updated_at}>{formatTime(project.updated_at)}</time>
                    </div>
                    <div className="project-card-meta">
                      <span>{project.creative_settings.english_level} · {project.target_duration_sec ?? 90} 秒</span>
                      <span>{statusLabel(project.status)}</span>
                    </div>
                  </div>
                </a>
                <div className="project-card-actions">
                  <a href={`#/projects/${project.id}`}>进入项目</a>
                  <button aria-label={`删除项目 ${project.title}`} onClick={() => void openDeleteDialog(project)} type="button">删除</button>
                </div>
              </article>
            ))}
          </div>
        ) : null}

        {deleteTarget ? (
          <div className="project-delete-overlay" onMouseDown={(event) => event.target === event.currentTarget && closeDeleteDialog()} role="presentation">
            <section aria-describedby="project-delete-description" aria-labelledby="project-delete-title" aria-modal="true" className="project-delete-dialog" role="dialog">
            <div className="project-delete-dialog-head">
              <div>
                <span>DANGER ZONE</span>
                <h2 id="project-delete-title">删除项目</h2>
              </div>
              <button aria-label="关闭删除确认" disabled={isDeleting} onClick={closeDeleteDialog} type="button">×</button>
            </div>

            <p id="project-delete-description">
              将删除项目“<strong>{deleteTarget.title}</strong>”的全部生产数据。数据库记录无法恢复；项目媒体会先移入本地回收目录，不会直接永久擦除文件。
            </p>

            {isPreviewLoading ? <div className="state-box">正在统计项目数据和文件...</div> : null}
            {deleteError ? <div className="state-box error" role="alert">{deleteError}</div> : null}

            {deletePreview ? (
              <>
                <dl className="project-delete-stats">
                  <div><dt>关联数据</dt><dd>{deletePreview.total_related_records} 条</dd></div>
                  <div><dt>本地文件</dt><dd>{deletePreview.storage_file_count} 个</dd></div>
                  <div><dt>文件体积</dt><dd>{formatBytes(deletePreview.storage_bytes)}</dd></div>
                </dl>

                {deletePreview.blocker_reasons.length > 0 ? (
                  <div className="project-delete-blockers">
                    <strong>当前不能删除</strong>
                    {deletePreview.blocker_reasons.map((reason) => <span key={reason}>{reason}</span>)}
                  </div>
                ) : null}

                <label className="project-delete-confirmation">
                  输入项目名称以确认
                  <input
                    autoComplete="off"
                    autoFocus
                    disabled={isDeleting || !deletePreview.can_delete}
                    placeholder={deleteTarget.title}
                    value={confirmationTitle}
                    onChange={(event) => setConfirmationTitle(event.target.value)}
                  />
                  <small>请输入：{deleteTarget.title}</small>
                </label>
              </>
            ) : null}

            <div className="project-delete-dialog-actions">
              <button className="secondary-button" disabled={isDeleting} onClick={closeDeleteDialog} type="button">取消</button>
              <button
                className="danger-button"
                disabled={
                  isDeleting
                  || !deletePreview?.can_delete
                  || confirmationTitle.trim() !== deleteTarget.title.trim()
                }
                onClick={() => void confirmDeleteProject()}
                type="button"
              >
                {isDeleting ? "正在删除..." : "删除项目及全部数据"}
              </button>
            </div>
            </section>
          </div>
        ) : null}
      </section>
    </div>
  );
}
