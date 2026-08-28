import { useEffect, useMemo, useState } from "react";

import { AssetPreview } from "../components/studio/AssetPreview";
import { DirectorWorkbench, type ShotDraftPayload } from "../components/studio/DirectorWorkbench";
import { GenerationTaskBanner } from "../components/studio/GenerationTaskBanner";
import { ScriptCreationTrace } from "../components/studio/ScriptCreationTrace";
import { StageRunSummary } from "../components/studio/StageRunSummary";
import {
  compileShotVideoPrompt,
  cancelGenerationTask,
  cancelProjectStageRun,
  composeProject,
  createProjectShot,
  deleteShot,
  extractVideoFrameCandidate,
  generateProjectEntities,
  generateProjectScript,
  generateProjectShots,
  generateReferenceImageCandidates,
  generateSingleReferenceImageCandidate,
  generateSingleShotImageCandidate,
  generateSingleShotVideoCandidate,
  generateShotVideoCandidates,
  getProjectWorkbench,
  getShotVideoPromptPreview,
  listImageProviderProfiles,
  listGenerationTaskProgress,
  listGenerationTasks,
  listProviders,
  promoteAssetCandidate,
  rejectAssetCandidate,
  regenerateVideoAssetCandidate,
  retryGenerationTask,
  reorderShots,
  saveProjectDialogues,
  selectAsset,
  updateCharacter,
  updateProp,
  updateScene,
  updateShot,
  updateShotReferenceAssets,
  updateShotVideoReference,
  updateProject,
  uploadImageAsset,
  upsertProjectChapter,
} from "../services/apiClient";
import type {
  ImageProviderProfile,
  ProjectWorkbenchPayload,
  ProviderDescriptor,
} from "../services/apiClient";
import type { Chapter, ProjectStageRun, SubtitleMode } from "../types/project";
import type {
  Asset,
  AssetCandidate,
  Character,
  Dialogue,
  DialogueInput,
  EntityBundle,
  ExportRecord,
  GenerationTask,
  Prop,
  Scene,
  Shot,
  ShotVideoPromptPreview,
} from "../types/stageFive";
import { normalizeProductionScenes, renderReadableReviewScript } from "../utils/productionScript";
import {
  createTaskIdempotencyKey,
  isGenerationTaskActive,
  isGenerationTaskTerminal,
  mergeGenerationTask,
} from "../utils/generationTask";

type ManagementDrawer = "script" | "asset-generation" | "assets" | null;
type EntityKind = "character" | "scene" | "prop";
type EntityItem = {
  id: string;
  kind: EntityKind;
  name: string;
  summary: string;
  promptFallback: string;
  assetSpec: Record<string, unknown>;
};

type ProjectDetailPageProps = {
  initialShotId?: string;
  initialWorkspace?: "asset-generation" | "script";
  projectId: string;
};

export function ProjectDetailPage({ initialShotId, initialWorkspace, projectId }: ProjectDetailPageProps) {
  const [snapshot, setSnapshot] = useState<ProjectWorkbenchPayload | null>(null);
  const [managementDrawer, setManagementDrawer] = useState<ManagementDrawer>(
    initialWorkspace === "asset-generation" || initialWorkspace === "script" ? initialWorkspace : null,
  );
  const [chapterDraft, setChapterDraft] = useState("");
  const [dialogueDrafts, setDialogueDrafts] = useState<Dialogue[]>([]);
  const [selectedEntityKey, setSelectedEntityKey] = useState("");
  const [selectedVariantKey, setSelectedVariantKey] = useState("base");
  const [selectedShotId, setSelectedShotId] = useState(initialShotId ?? "");
  const [promptPreview, setPromptPreview] = useState<ShotVideoPromptPreview | null>(null);
  const [imageProfiles, setImageProfiles] = useState<ImageProviderProfile[]>([]);
  const [imageProfileId, setImageProfileId] = useState("");
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [projectTasks, setProjectTasks] = useState<GenerationTask[]>([]);
  const [taskRefreshVersion, setTaskRefreshVersion] = useState(0);
  const [exportOpen, setExportOpen] = useState(false);
  const [subtitleMode, setSubtitleMode] = useState<SubtitleMode>("none");
  const [styleDraft, setStyleDraft] = useState("");
  const [busyLabel, setBusyLabel] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    const next = await getProjectWorkbench(projectId);
    setSnapshot(next);
    setChapterDraft(chapterInputText(next.project.chapters[0]));
    setStyleDraft(next.project.style);
    setDialogueDrafts(next.dialogues);
    const firstEntity = entityItems(next.entities)[0];
    setSelectedEntityKey((current) => (
      current && entityItems(next.entities).some((item) => entityKey(item) === current)
        ? current
        : firstEntity ? entityKey(firstEntity) : ""
    ));
    setSelectedShotId((current) => (
      current && next.shots.some((shot) => shot.id === current)
        ? current
        : next.shots[0]?.id ?? ""
    ));
    return next;
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getProjectWorkbench(projectId),
      listImageProviderProfiles().catch(() => []),
      listProviders().catch(() => []),
    ])
      .then(([next, profiles, providerItems]) => {
        if (cancelled) return;
        setSnapshot(next);
        setChapterDraft(chapterInputText(next.project.chapters[0]));
        setStyleDraft(next.project.style);
        setDialogueDrafts(next.dialogues);
        setImageProfiles(profiles);
        setImageProfileId(profiles.find((item) => item.is_default)?.id ?? profiles[0]?.id ?? "");
        setProviders(providerItems);
        const firstEntity = entityItems(next.entities)[0];
        setSelectedEntityKey(firstEntity ? entityKey(firstEntity) : "");
        setSelectedShotId(initialShotId && next.shots.some((shot) => shot.id === initialShotId)
          ? initialShotId
          : next.shots[0]?.id ?? "");
        if (initialWorkspace === "asset-generation") {
          setManagementDrawer("asset-generation");
        } else if (initialWorkspace === "script") {
          setManagementDrawer("script");
        } else if (!next.shots.length) {
          setManagementDrawer(next.script ? "asset-generation" : "script");
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "项目加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initialShotId, initialWorkspace, projectId]);

  // Video/image tasks may finish outside this page (for example from the task
  // center or a direct API call). Keep the workbench snapshot in sync without
  // touching the local chapter, style, dialogue, or shot drafts. A focus and
  // visibility refresh makes the result appear as soon as the user returns to
  // the studio; the low-frequency timer covers tasks that finish while the
  // page remains visible.
  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let timer: number | undefined;

    const schedule = (delay = 5000) => {
      if (cancelled) return;
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = undefined;
        void syncWorkbench();
      }, delay);
    };

    const syncWorkbench = async () => {
      if (cancelled) return;
      if (inFlight || document.visibilityState !== "visible") {
        schedule();
        return;
      }
      inFlight = true;
      try {
        const next = await getProjectWorkbench(projectId);
        if (!cancelled) {
          setSnapshot(next);
          setSelectedShotId((current) => (
            current && next.shots.some((shot) => shot.id === current)
              ? current
              : next.shots[0]?.id ?? ""
          ));
        }
      } catch {
        // The initial load and explicit actions surface errors. A background
        // refresh is best-effort and must not interrupt an in-progress edit.
      } finally {
        inFlight = false;
        schedule();
      }
    };

    const refreshWhenFocused = () => { void syncWorkbench(); };
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void syncWorkbench();
    };

    window.addEventListener("focus", refreshWhenFocused);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    schedule(3000);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener("focus", refreshWhenFocused);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [projectId]);

  const selectedShot = snapshot?.shots.find((shot) => shot.id === selectedShotId) ?? null;
  useEffect(() => setPromptPreview(null), [selectedShot?.id, selectedShot?.video_prompt]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let lastFullSyncAt = 0;
    let knownTaskIds = new Set<string>();
    let knownTaskStatuses = new Map<string, GenerationTask["status"]>();

    async function syncTerminalTaskResults(tasks: Array<Pick<GenerationTask, "status" | "task_type">>) {
      if (!tasks.length) return;
      const next = await getProjectWorkbench(projectId);
      if (cancelled) return;
      setSnapshot(next);
      if (tasks.some((task) => task.task_type === "script_generation" && task.status === "succeeded")) {
        setDialogueDrafts(next.dialogues);
      }
      if (tasks.some((task) => task.task_type === "shot_breakdown" && task.status === "succeeded")) {
        setSelectedShotId(next.shots[0]?.id ?? "");
      }
    }

    async function syncTasks() {
      let nextDelay = 8000;
      try {
        if (Date.now() - lastFullSyncAt >= 15000) {
          const response = await listGenerationTasks({ projectId, limit: 200 });
          if (!cancelled) {
            const reachedTerminalTasks = response.items.filter((task) => {
              const previousStatus = knownTaskStatuses.get(task.id);
              return Boolean(
                previousStatus
                && isGenerationTaskActive(previousStatus)
                && isGenerationTaskTerminal(task.status),
              );
            });
            setProjectTasks(response.items);
            knownTaskIds = new Set(response.items.map((task) => task.id));
            knownTaskStatuses = new Map(response.items.map((task) => [task.id, task.status]));
            lastFullSyncAt = Date.now();
            nextDelay = response.items.some((task) => isGenerationTaskActive(task.status)) ? 2000 : 8000;
            await syncTerminalTaskResults(reachedTerminalTasks);
          }
        } else {
          const response = await listGenerationTaskProgress({ projectId, limit: 200 });
          if (!cancelled) {
            const progressById = new Map(response.items.map((task) => [task.id, task]));
            const reachedTerminalTasks = response.items.filter((task) => {
              const previousStatus = knownTaskStatuses.get(task.id);
              return Boolean(
                previousStatus
                && isGenerationTaskActive(previousStatus)
                && isGenerationTaskTerminal(task.status),
              );
            });
            response.items.forEach((task) => knownTaskStatuses.set(task.id, task.status));
            setProjectTasks((current) => current.map((task) => ({
              ...task,
              ...(progressById.get(task.id) ?? {}),
            })));
            if (response.items.some((task) => !knownTaskIds.has(task.id))) {
              lastFullSyncAt = 0;
            }
            nextDelay = response.items.some((task) => isGenerationTaskActive(task.status)) ? 2000 : 8000;
            await syncTerminalTaskResults(reachedTerminalTasks);
            if (reachedTerminalTasks.length) {
              lastFullSyncAt = 0;
              nextDelay = 250;
            }
          }
        }
      } catch {
        // Workbench actions surface request failures. Background task polling
        // stays non-blocking so it cannot interrupt a local edit.
      } finally {
        if (!cancelled) timer = window.setTimeout(() => void syncTasks(), nextDelay);
      }
    }

    void syncTasks();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [projectId, taskRefreshVersion]);

  const scriptGenerationTask = projectTasks.find((task) => task.task_type === "script_generation") ?? null;
  const shotDirectionTask = projectTasks.find((task) => task.task_type === "shot_breakdown") ?? null;
  const currentShotDirectionTask = selectedShot?.shot_batch_id
    ? projectTasks.find((task) => task.id === selectedShot.shot_batch_id && task.task_type === "shot_breakdown") ?? null
    : shotDirectionTask?.status === "succeeded" ? shotDirectionTask : null;
  const selectedShotVideoTask = selectedShot
    ? projectTasks.find((task) => task.resource_key === `shot:${selectedShot.id}:video`) ?? null
    : null;
  const projectVideoBatchTask = projectTasks.find((task) => task.task_type === "project_video_candidate_batch") ?? null;
  const hasActiveVideoTask = projectTasks.some((task) => (
    task.task_type === "shot_video_candidate_generation" && isGenerationTaskActive(task)
  ));

  async function run(actionLabel: string, action: () => Promise<unknown>, successMessage: string) {
    try {
      setBusyLabel(actionLabel);
      setError("");
      setMessage("");
      await action();
      await refresh();
      setMessage(successMessage);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${actionLabel}失败`);
      return false;
    } finally {
      setBusyLabel("");
    }
  }

  async function submitTask(
    actionLabel: string,
    action: () => Promise<GenerationTask>,
    acceptedMessage: string,
  ) {
    try {
      setBusyLabel(actionLabel);
      setError("");
      setMessage("");
      const task = await action();
      setProjectTasks((current) => mergeGenerationTask(current, task));
      setTaskRefreshVersion((current) => current + 1);
      setMessage(`${acceptedMessage}（任务 ${task.id.slice(0, 8)}）`);
      return task;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${actionLabel}失败`);
      return null;
    } finally {
      setBusyLabel("");
    }
  }

  async function cancelTask(task: GenerationTask) {
    await submitTask(
      "取消生成任务",
      () => cancelGenerationTask(task.id),
      task.status === "queued" ? "排队任务已取消" : "取消请求已提交，将在下一检查点停止",
    );
  }

  async function retryTask(task: GenerationTask) {
    await submitTask(
      "重试生成任务",
      () => retryGenerationTask(task.id),
      "已使用原始输入快照创建重试任务",
    );
  }

  function handleUploadImage(entity: EntityItem, variantKey: string, file: File) {
    void run(
      "上传资产图",
      () => uploadImageAsset(projectId, {
        file,
        entity_type: entity.kind,
        entity_id: entity.id,
        asset_role: entityRole(entity.kind),
        variant_key: variantKey,
        prompt: entityPrompt(entity),
      }),
      "资产图已上传并设为当前版本。",
    );
  }

  function openAssetGeneration() {
    setManagementDrawer("asset-generation");
    window.history.replaceState(null, "", `#/projects/${projectId}/assets/generate`);
  }

  function openAssetLibrary() {
    setManagementDrawer("assets");
    window.history.replaceState(null, "", `#/projects/${projectId}`);
  }

  function openScript() {
    setManagementDrawer("script");
    window.history.replaceState(null, "", `#/projects/${projectId}/chapter`);
  }

  function openDirector() {
    setManagementDrawer(null);
    const firstShotId = selectedShotId || snapshot?.shots[0]?.id;
    window.history.replaceState(
      null,
      "",
      firstShotId ? `#/projects/${projectId}/shots/${firstShotId}` : `#/projects/${projectId}`,
    );
  }

  if (loading) {
    return <section className="panel"><div className="state-box">正在加载英语短剧工作台…</div></section>;
  }
  if (!snapshot) {
    return <section className="panel"><div className="state-box error">{error || "项目不存在"}</div></section>;
  }

  const project = snapshot.project;
  const hasRequiredEntities = snapshot.entities.characters.length > 0 && snapshot.entities.scenes.length > 0;
  const selectedVideoProvider = providers.find((provider) => provider.type === "video" && provider.selected)
    ?? providers.find((provider) => provider.type === "video");
  const latestExportDownload = findLatestExportDownload(
    snapshot.exports,
    snapshot.historical_assets ?? snapshot.assets,
  );

  async function downloadLatestExport() {
    if (!latestExportDownload) return;
    try {
      setBusyLabel("下载最新成片");
      setError("");
      setMessage("");
      await downloadMediaFile(
        latestExportDownload.asset.uri,
        `VerbaScene-${project.id.slice(0, 8)}-v${latestExportDownload.asset.version}.mp4`,
      );
      setMessage(`成片 v${latestExportDownload.asset.version} 已开始下载。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "下载最新成片失败");
    } finally {
      setBusyLabel("");
    }
  }

  return (
    <div className="director-page">
      <header className="director-topbar">
        <div className="director-project-title">
          <a aria-label="返回项目列表" href="#/projects">←</a>
          <div>
            <span>ANIMATED ENGLISH · DIRECTOR DESK</span>
            <h1>{project.title}</h1>
          </div>
        </div>
        <div className="director-project-meta">
          <span>{project.creative_settings.english_level}</span>
          <span>{project.aspect_ratio} · {project.resolution}</span>
          <span>{project.target_duration_sec ?? 90} 秒</span>
        </div>
        <nav className="director-project-actions" aria-label="项目工具">
          <button onClick={openScript} type="button">
            剧本 <small>{snapshot.script ? `v${snapshot.script.version}` : project.chapters[0] ? "原稿" : "未生成"}</small>
          </button>
          <button onClick={openAssetLibrary} type="button">资产库 <small>{entityItems(snapshot.entities).length} 项</small></button>
          <details>
            <summary>运行记录{busyLabel ? " · 运行中" : ""}</summary>
            <StageRunSummary
              isBusy={Boolean(busyLabel)}
              onCancel={(runRecord: ProjectStageRun) => {
                void run("取消运行", () => cancelProjectStageRun(runRecord.id), "运行已取消。");
              }}
              runs={snapshot.stage_runs}
            />
          </details>
          <details>
            <summary>视觉风格</summary>
            <div className="director-style-editor">
              <label>
                项目视觉风格
                <textarea
                  onChange={(event) => setStyleDraft(event.target.value)}
                  rows={4}
                  value={styleDraft}
                />
              </label>
              <small>
                保存后，实体和分镜主体 Prompt 保持不变；图片生成会使用新风格，已有视频 Prompt 会标记为需要重新编译。
              </small>
              <button
                disabled={!styleDraft.trim() || styleDraft.trim() === project.style || Boolean(busyLabel)}
                onClick={() => {
                  const nextStyle = styleDraft.trim();
                  void run(
                    "更新视觉风格",
                    () => updateProject(projectId, {
                      style: nextStyle,
                      creative_settings: {
                        ...project.creative_settings,
                        animation_style: nextStyle,
                      },
                    }),
                    "视觉风格已更新；历史媒体保持不变，视频 Prompt 已标记为需要重新编译。",
                  );
                }}
                type="button"
              >
                保存视觉风格
              </button>
            </div>
          </details>
          <button className="director-provider-chip" type="button">
            <span>{selectedVideoProvider?.model ?? "视频模型未配置"}</span>
            <small>{selectedVideoProvider?.native_audio ? "原生音频" : "原生音频待适配"}</small>
          </button>
          <button className="primary-button" onClick={() => setExportOpen(true)} type="button">导出本集</button>
          {latestExportDownload ? (
            <button
              aria-label={`下载最新成片 v${latestExportDownload.asset.version} · ${subtitleModeLabel(latestExportDownload.record.subtitle_mode)}`}
              className="director-export-download"
              disabled={Boolean(busyLabel)}
              onClick={() => { void downloadLatestExport(); }}
              type="button"
            >
              下载最新成片
              <small>
                v{latestExportDownload.asset.version} · {subtitleModeLabel(latestExportDownload.record.subtitle_mode)}
              </small>
            </button>
          ) : null}
        </nav>
      </header>

      <div className="director-notices" aria-live="polite">
        {message ? <div className="state-box">{message}</div> : null}
        {error ? <div className="state-box error">{error}</div> : null}
        {busyLabel ? <div className="state-box">正在{busyLabel}…</div> : null}
      </div>

      {selectedShot ? (
        <DirectorWorkbench
          actionBusy={Boolean(busyLabel)}
          assets={snapshot.historical_assets ?? snapshot.assets}
          batchTask={projectVideoBatchTask}
          candidates={snapshot.asset_candidates}
          dialogues={dialogueDrafts}
          directorReportTask={currentShotDirectionTask}
          entities={snapshot.entities}
          imageProfileId={imageProfileId}
          imageProfiles={imageProfiles}
          hasActiveVideoTask={hasActiveVideoTask}
          onAdopt={(candidate) => run(
            "采用片段版本",
            () => promoteAssetCandidate(candidate.id, "从视频制作页采用"),
            "当前版本已切换。",
          )}
          onBindAssets={(assetIds) => run(
            "更新引用资产",
            () => updateShotReferenceAssets(selectedShot.id, assetIds),
            "当前片段的引用资产已更新，Prompt 已标记可能过期。",
          )}
          onCompilePrompt={() => selectedShot && run(
            "重新编译 Prompt",
            async () => {
              await compileShotVideoPrompt(selectedShot.id);
              const preview = await getShotVideoPromptPreview(selectedShot.id);
              setPromptPreview(preview);
            },
            "最终 Prompt 已按当前对白、音效和资产引用重新编译。",
          )}
          onCancelTask={(task) => { void cancelTask(task); }}
          onCreateShot={() => {
            void run(
              "新增片段",
              () => createProjectShot(projectId, {
                shot_no: snapshot.shots.length + 1,
                description: "新片段：请补充场景、动作和英文对白。",
                duration_sec: null,
                shot_card: {
                  schema_version: 3,
                  segment_plan: {
                    duration_mode: "provider_auto",
                    expected_duration_range: { min_sec: 4, max_sec: 15 },
                    actual_duration_sec: null,
                    internal_shot_count: 1,
                  },
                  beats: [{
                    beat_id: `beat-${snapshot.shots.length + 1}-1`,
                    camera: "中景，平视，固定机位",
                    action: "请补充当前片段的主要动作。",
                    dialogue_ids: [],
                    sound_cues: [],
                  }],
                  prompt_stale: true,
                },
              }),
              "新片段已添加到时间轴末尾。",
            );
          }}
          onDeleteShot={(shot) => {
            if (!window.confirm(`确定删除片段 ${String(shot.shot_no).padStart(2, "0")}？现有媒体不会被物理删除。`)) return;
            const nextShot = snapshot.shots.find((item) => item.id !== shot.id);
            void run("删除片段", () => deleteShot(shot.id), "片段已从当前时间轴移除。").then((success) => {
              if (success && nextShot) commitShotSelection(nextShot.id);
            });
          }}
          onGenerateImage={() => selectedShot && run(
            "生成片段首帧候选",
            () => generateSingleShotImageCandidate(selectedShot.id, imageProfileId || null),
            "首帧候选已生成。",
          )}
          onGenerateShots={() => {
            if (snapshot.shots.length && !window.confirm("重新生成会在任务成功后把当前片段批次移入历史，失败或取消不会影响当前方案。是否继续？")) return;
            void submitTask(
              snapshot.shots.length ? "提交分镜重新生成" : "提交分镜生成",
              () => generateProjectShots(projectId, {
                idempotencyKey: createTaskIdempotencyKey(`project:${projectId}:shots`),
              }),
              "分镜导演任务已受理，可继续编辑和切换工作区",
            );
          }}
          onGenerateAllVideos={() => {
            if (!window.confirm(`将为当前项目的 ${snapshot.shots.length} 个片段创建视频任务，是否继续？`)) return;
            void submitTask(
              "提交项目视频批次",
              () => generateShotVideoCandidates(projectId, {
                idempotencyKey: createTaskIdempotencyKey(`project:${projectId}:videos`),
              }),
              "项目视频批次已受理，可继续编辑其他内容",
            );
          }}
          onGenerateVideo={(videoPrompt, durationMode, durationSec) => selectedShot && submitTask(
            "提交视频候选",
            () => generateSingleShotVideoCandidate(
              selectedShot.id,
              {
                duration_mode: durationMode,
                duration_sec: durationSec,
                video_prompt: videoPrompt,
              },
              { idempotencyKey: createTaskIdempotencyKey(`shot:${selectedShot.id}:video`) },
            ),
            "视频候选任务已受理，可继续编辑其他片段",
          )}
          onExtractFrame={(asset) => run(
            "截取片段首帧",
            () => extractVideoFrameCandidate(asset.id, 0),
            "已从当前视频截取首帧候选，采用后才会切换片段首帧。",
          )}
          onLocalRegenerate={(asset) => submitTask(
            "重新生成本片段",
            () => regenerateVideoAssetCandidate(asset.id, {
              idempotencyKey: createTaskIdempotencyKey(`asset:${asset.id}:regenerate-video`),
            }),
            "局部重生成任务已受理，当前版本保持不变",
          )}
          onOpenAssetLibrary={openAssetLibrary}
          onPreviewPrompt={async () => {
            if (!selectedShot) return;
            try {
              setPromptPreview(await getShotVideoPromptPreview(selectedShot.id));
            } catch (reason) {
              setError(reason instanceof Error ? reason.message : "Prompt 预览失败");
            }
          }}
          onReject={(candidate) => run(
            "拒绝片段候选",
            () => rejectAssetCandidate(candidate.id, "从导演台拒绝"),
            "候选已拒绝，当前版本未改变。",
          )}
          onRetryTask={(task) => { void retryTask(task); }}
          onReorderShots={(shotIds) => {
            void run(
              "调整片段顺序",
              () => reorderShots(shotIds.map((id, index) => ({ id, shot_no: index + 1 }))),
              "片段顺序已更新。",
            );
          }}
          onSaveDraft={(payload: ShotDraftPayload) => {
            const changedDialogues = new Map(payload.dialogues.map((dialogue) => [dialogue.id, dialogue]));
            const mergedDialogues = dialogueDrafts.map((dialogue) => changedDialogues.get(dialogue.id) ?? dialogue);
            setDialogueDrafts(mergedDialogues);
            return run(
              "保存片段",
              async () => {
                await updateShot(selectedShot.id, {
                  description: payload.description,
                  shot_card: payload.shotCard,
                  video_prompt: payload.videoPrompt,
                });
                await saveProjectDialogues(projectId, dialogueInputs(mergedDialogues));
              },
              "当前片段、对白和高级 Prompt 已保存。",
            );
          }}
          onSelectAsset={(asset) => run(
            "切换片段版本",
            () => selectAsset(asset.id),
            `已采用 v${asset.version}。`,
          )}
          onSelectImageProfile={setImageProfileId}
          onSelectShot={commitShotSelection}
          onSetFirstFrameReference={(assetId) => selectedShot && run(
            assetId ? "启用片段首帧" : "取消片段首帧",
            async () => {
              await updateShotVideoReference(selectedShot.id, assetId);
              setPromptPreview(await getShotVideoPromptPreview(selectedShot.id));
            },
            assetId
              ? "当前首帧已明确加入视频输入。"
              : "已恢复默认无首帧模式，视频只使用角色、场景和文本 Prompt。",
          )}
          promptPreview={promptPreview}
          selectedShot={selectedShot}
          selectedShotTask={selectedShotVideoTask}
          shotDirectionTask={shotDirectionTask}
          selectedVideoProvider={selectedVideoProvider}
          shots={snapshot.shots}
          videoTasks={projectTasks.filter((task) => task.task_type === "shot_video_candidate_generation")}
        />
      ) : (
        <section className="director-bootstrap">
          <span>START WITH STORY</span>
          {!snapshot.script ? (
            <>
              <h2>先生成或整理剧本</h2>
              <p>打开剧本检查已导入的原稿或创意描述，再生成可用于制作的英语剧本。</p>
              <div>
                <button className="primary-button" onClick={() => setManagementDrawer("script")} type="button">打开剧本</button>
              </div>
            </>
          ) : !hasRequiredEntities ? (
            <>
              <h2>下一步：生成资产图</h2>
              <p>剧本已经就绪。进入集中页面提取资产设定，并生成角色、场景基础图。</p>
              <div>
                <button className="secondary-button" onClick={() => setManagementDrawer("script")} type="button">查看剧本</button>
                <button className="primary-button" onClick={openAssetGeneration} type="button">进入资产图生成页</button>
              </div>
            </>
          ) : (
            <>
              <h2>检查并生成资产图</h2>
              <p>角色和场景设定已经就绪。先集中生成、采用基础图，再进入视频制作。</p>
              <div>
                <button className="secondary-button" onClick={openAssetLibrary} type="button">查看资产设定</button>
                <button className="primary-button" onClick={openAssetGeneration} type="button">进入资产图生成页</button>
              </div>
            </>
          )}
        </section>
      )}

      {managementDrawer ? (
        <div className="director-drawer-backdrop" onClick={openDirector} role="presentation">
          <section
            aria-label={managementDrawer === "script" ? "剧本管理" : managementDrawer === "asset-generation" ? "资产图生成" : "资产库管理"}
            aria-modal="true"
            className={`director-management-drawer${managementDrawer === "asset-generation" ? " asset-generation-page" : ""}`}
            onClick={(event) => event.stopPropagation()}
            role="dialog"
          >
            <header>
              <div>
                <span>{managementDrawer === "asset-generation" ? "PRODUCTION STEP 02" : "PROJECT MANAGEMENT"}</span>
                <h2>{managementDrawer === "script" ? "剧本" : managementDrawer === "asset-generation" ? "生成资产图" : "资产库"}</h2>
              </div>
              <div className="director-management-header-actions">
                {managementDrawer === "asset-generation" ? (
                  <button className="director-management-back-button" onClick={openScript} type="button">← 返回剧本</button>
                ) : null}
                <button aria-label="关闭当前页面" onClick={openDirector} type="button">×</button>
              </div>
            </header>
            <div className="director-management-scroll">
              {managementDrawer === "script" ? (
                <ScriptWorkspace
                  chapter={project.chapters[0] ?? null}
                  chapterDraft={chapterDraft}
                  onChapterDraftChange={setChapterDraft}
                  onCancelTask={(task) => { void cancelTask(task); }}
                  onContinue={openAssetGeneration}
                  onGenerate={() => {
                    void submitTask(
                      snapshot.script ? "重新生成剧本" : "生成剧本",
                      async () => {
                        const chapter = project.chapters[0];
                        if (chapter && chapterDraft !== chapterInputText(chapter)) {
                          await upsertProjectChapter(projectId, chapterUpdatePayload(chapter, chapterDraft));
                        }
                        return generateProjectScript(projectId, {
                          idempotencyKey: createTaskIdempotencyKey(`project:${projectId}:script`),
                        });
                      },
                      "剧本任务已受理，完成前仍可留在当前工作区编辑",
                    );
                  }}
                  onRetryTask={(task) => { void retryTask(task); }}
                  onSaveChapter={() => {
                    const chapter = project.chapters[0];
                    if (!chapter) return;
                    void run(
                      chapter.input_mode === "imported_script" ? "保存导入原稿" : "保存创意描述",
                      () => upsertProjectChapter(projectId, chapterUpdatePayload(chapter, chapterDraft)),
                      chapter.input_mode === "imported_script" ? "导入原稿已保存。" : "创意描述已保存。",
                    );
                  }}
                  script={snapshot.script}
                  scriptTask={scriptGenerationTask}
                  taskActionBusy={Boolean(busyLabel)}
                />
              ) : managementDrawer === "asset-generation" ? (
                <AssetGenerationWorkspace
                  assets={snapshot.historical_assets ?? snapshot.assets}
                  candidates={snapshot.asset_candidates}
                  entities={snapshot.entities}
                  imageProfileId={imageProfileId}
                  imageProfiles={imageProfiles}
                  isBusy={Boolean(busyLabel)}
                  onAdopt={(candidate) => void run("采用资产图", () => promoteAssetCandidate(candidate.id, "从资产图生成页采用"), "资产图已采用。")}
                  onCancelTask={(task) => { void cancelTask(task); }}
                  onContinue={() => {
                    if (snapshot.shots.length) {
                      openDirector();
                      return;
                    }
                    void submitTask(
                      "提交分镜生成",
                      () => generateProjectShots(projectId, {
                        idempotencyKey: createTaskIdempotencyKey(`project:${projectId}:shots`),
                      }),
                      "分镜导演任务已受理，完成前仍可管理资产",
                    );
                  }}
                  onGenerateAll={() => void run(
                    "生成角色和场景资产图",
                    () => generateReferenceImageCandidates(projectId, imageProfileId || null),
                    "角色和场景图片候选已生成，请逐项采用。",
                  )}
                  onGenerateImage={(entity, variantKey) => void run(
                    "生成单个资产图",
                    () => generateSingleReferenceImageCandidate(projectId, {
                      entity_type: entity.kind,
                      entity_id: entity.id,
                      asset_role: entityRole(entity.kind),
                      variant_key: variantKey,
                    }, imageProfileId || null),
                    "单个资产图候选已生成。",
                  )}
                  onGenerateEntities={() => void run(
                    entityItems(snapshot.entities).length ? "重新提取资产设定" : "提取资产设定",
                    () => generateProjectEntities(projectId),
                    "角色、场景和道具结构已提取；现在可以生成角色和场景图片。",
                  )}
                  onManage={openAssetLibrary}
                  onRetryTask={(task) => { void retryTask(task); }}
                  onUploadImage={handleUploadImage}
                  onSelectProfile={setImageProfileId}
                  projectTitle={project.title}
                  shotCount={snapshot.shots.length}
                  shotDirectionTask={shotDirectionTask}
                />
              ) : (
                <AssetLibraryWorkspace
                  assets={snapshot.historical_assets ?? snapshot.assets}
                  candidates={snapshot.asset_candidates}
                  entities={snapshot.entities}
                  imageProfileId={imageProfileId}
                  imageProfiles={imageProfiles}
                  isBusy={Boolean(busyLabel)}
                  onAdopt={(candidate) => void run("采用资产版本", () => promoteAssetCandidate(candidate.id, "从资产库采用"), "当前资产版本已切换。")}
                  onGenerateEntities={() => void run(
                    entityItems(snapshot.entities).length ? "重新提取资产" : "提取资产",
                    () => generateProjectEntities(projectId),
                    "资产设定已更新，旧产物仍然保留。",
                  )}
                  onGenerateImage={(entity, variantKey) => void run(
                    "生成资产候选",
                    () => generateSingleReferenceImageCandidate(projectId, {
                      entity_type: entity.kind,
                      entity_id: entity.id,
                      asset_role: entityRole(entity.kind),
                      variant_key: variantKey,
                    }, imageProfileId || null),
                    "新图片候选已生成。",
                  )}
                  onSavePrompt={(entity, prompt) => void run("保存图片 Prompt", () => updateEntityPrompt(entity, prompt), "图片 Prompt 已保存。")}
                  onSelectAsset={(asset) => void run("切换资产版本", () => selectAsset(asset.id), `已切换到 v${asset.version}。`)}
                  onSelectEntity={(key) => { setSelectedEntityKey(key); setSelectedVariantKey("base"); }}
                  onSelectProfile={setImageProfileId}
                  onSelectVariant={setSelectedVariantKey}
                  onUploadImage={handleUploadImage}
                  selectedEntityKey={selectedEntityKey}
                  selectedVariantKey={selectedVariantKey}
                />
              )}
            </div>
          </section>
        </div>
      ) : null}

      {exportOpen ? (
        <ExportDialog
          assets={snapshot.assets}
          mode={subtitleMode}
          onClose={() => setExportOpen(false)}
          onModeChange={setSubtitleMode}
          onSubmit={() => void run(
              "导出本集",
              () => composeProject(projectId, subtitleMode),
              subtitleMode === "none" ? "无字幕成片已导出。" : "字幕已在导出时烧录。",
          ).then((success) => { if (success) setExportOpen(false); })}
          shots={snapshot.shots}
        />
      ) : null}
    </div>
  );

  function commitShotSelection(shotId: string) {
    setSelectedShotId(shotId);
    window.history.replaceState(null, "", `#/projects/${projectId}/shots/${shotId}`);
  }

  async function updateEntityPrompt(entity: EntityItem, prompt: string) {
    const nextSpec = withEntityPrompt(entity, prompt);
    if (entity.kind === "character") {
      await updateCharacter(entity.id, { asset_spec: nextSpec as Character["asset_spec"] });
    } else if (entity.kind === "scene") {
      await updateScene(entity.id, { asset_spec: nextSpec as Scene["asset_spec"] });
    } else {
      await updateProp(entity.id, { asset_spec: nextSpec as Prop["asset_spec"] });
    }
  }

}

function ScriptWorkspace({
  chapter,
  chapterDraft,
  onChapterDraftChange,
  onCancelTask,
  onContinue,
  onGenerate,
  onRetryTask,
  onSaveChapter,
  script,
  scriptTask,
  taskActionBusy,
}: {
  chapter: Chapter | null;
  chapterDraft: string;
  onChapterDraftChange: (value: string) => void;
  onCancelTask: (task: GenerationTask) => void;
  onContinue: () => void;
  onGenerate: () => void;
  onRetryTask: (task: GenerationTask) => void;
  onSaveChapter: () => void;
  script: ProjectWorkbenchPayload["script"];
  scriptTask: GenerationTask | null;
  taskActionBusy: boolean;
}) {
  const readableContent = script
    ? renderReadableReviewScript(normalizeProductionScenes(script)) || script.content
    : "";
  const scriptTaskActive = Boolean(scriptTask && isGenerationTaskActive(scriptTask));

  return (
    <section className="workflow-panel english-script-workspace">
      <header className="workflow-panel-head">
        <div>
          <span className="workflow-panel-kicker">Readable Screenplay</span>
          <h2>英语剧本</h2>
          <p>这里只审阅可读剧本；制作结构、对白和音效会在后台及对应片段中维护。</p>
        </div>
        <div className="shot-overview-actions">
          <button className="secondary-button" disabled={scriptTaskActive || taskActionBusy} onClick={onGenerate} type="button">
            {scriptTaskActive
              ? "剧本任务进行中"
              : script
                ? "重新生成"
                : chapter?.input_mode === "imported_script"
                  ? "AI 整理为结构化剧本"
                  : "AI 生成剧本"}
          </button>
        </div>
      </header>
      {scriptTask && scriptTask.status !== "succeeded" ? (
        <GenerationTaskBanner
          actionBusy={taskActionBusy}
          onCancel={onCancelTask}
          onRetry={onRetryTask}
          task={scriptTask}
          title="剧本生成任务"
        />
      ) : null}
      {script ? (
        <>
          <section className="readable-script-workspace">
            <div className="production-script-section-head">
              <div><span>Current Script · v{script.version}</span><h3>可读剧本</h3></div>
              <em>场景结构已在后台同步</em>
            </div>
            <pre className="readable-script-document">{readableContent || "当前版本没有可读正文。"}</pre>
          </section>
          <ScriptCreationTrace task={scriptTask?.status === "succeeded" ? scriptTask : null} />
          <footer className="script-next-step">
            <div><strong>剧本确认完成？</strong><span>下一步集中生成角色和场景资产图。</span></div>
            <button className="primary-button" onClick={onContinue} type="button">下一步：生成资产图 →</button>
          </footer>
        </>
      ) : chapter ? (
        <section className="chapter-source-workspace">
          <div className="production-script-section-head">
            <div>
              <span>{chapter.input_mode === "imported_script" ? "Imported Source" : "Creative Brief"}</span>
              <h3>{chapter.input_mode === "imported_script" ? "导入原稿" : "创意描述"}</h3>
            </div>
            <button className="primary-button" onClick={onSaveChapter} type="button">
              {chapter.input_mode === "imported_script" ? "保存原稿" : "保存描述"}
            </button>
          </div>
          <p>
            {chapter.input_mode === "imported_script"
              ? "原稿已经完整保存。你可以先校对修改，再让 AI 整理成场景、对白和制作结构。"
              : "创意描述已经保存。你可以继续修改，再生成结构化英语剧本。"}
          </p>
          <textarea
            aria-label={chapter.input_mode === "imported_script" ? "导入原稿" : "创意描述"}
            rows={28}
            value={chapterDraft}
            onChange={(event) => onChapterDraftChange(event.target.value)}
          />
          <footer className="script-next-step script-source-next-step">
            <div>
              <strong>原稿校对完成？</strong>
              <span>
                {chapter.input_mode === "imported_script"
                  ? "下一步让 AI 整理为可用于制作的英语剧本。"
                  : "下一步让 AI 生成可用于制作的英语剧本。"}
              </span>
            </div>
            <button className="primary-button" onClick={onGenerate} type="button">
              {chapter.input_mode === "imported_script"
                ? "下一步：AI 整理为结构化剧本 →"
                : "下一步：AI 生成剧本 →"}
            </button>
          </footer>
        </section>
      ) : (
        <div className="slot-empty">当前项目还没有创作输入，请返回项目列表重新创建。</div>
      )}
    </section>
  );
}

function AssetGenerationWorkspace({
  assets,
  candidates,
  entities,
  imageProfileId,
  imageProfiles,
  isBusy,
  onAdopt,
  onCancelTask,
  onContinue,
  onGenerateAll,
  onGenerateEntities,
  onGenerateImage,
  onManage,
  onRetryTask,
  onUploadImage,
  onSelectProfile,
  projectTitle,
  shotCount,
  shotDirectionTask,
}: {
  assets: Asset[];
  candidates: AssetCandidate[];
  entities: EntityBundle;
  imageProfileId: string;
  imageProfiles: ImageProviderProfile[];
  isBusy: boolean;
  onAdopt: (candidate: AssetCandidate) => void;
  onCancelTask: (task: GenerationTask) => void;
  onContinue: () => void;
  onGenerateAll: () => void;
  onGenerateEntities: () => void;
  onGenerateImage: (entity: EntityItem, variantKey: string) => void;
  onManage: () => void;
  onRetryTask: (task: GenerationTask) => void;
  onUploadImage: (entity: EntityItem, variantKey: string, file: File) => void;
  onSelectProfile: (id: string) => void;
  projectTitle: string;
  shotCount: number;
  shotDirectionTask: GenerationTask | null;
}) {
  const visualEntities = entityItems(entities).filter((item) => item.kind !== "prop");
  const adoptedByEntity = new Map(
    assets
      .filter((asset) => (
        asset.asset_type === "image"
        && asset.is_selected
        && (asset.entity_type === "character" || asset.entity_type === "scene")
        && (asset.variant_key ?? "base") === "base"
      ))
      .map((asset) => [`${asset.entity_type}:${asset.entity_id}`, asset]),
  );
  const pendingByEntity = new Map<string, AssetCandidate[]>();
  candidates
    .filter((candidate) => (
      candidate.asset_type === "image"
      && candidate.status === "pending_review"
      && (candidate.entity_type === "character" || candidate.entity_type === "scene")
      && (candidate.variant_key ?? "base") === "base"
    ))
    .forEach((candidate) => {
      const key = `${candidate.entity_type}:${candidate.entity_id}`;
      pendingByEntity.set(key, [...(pendingByEntity.get(key) ?? []), candidate]);
    });
  const adoptedCount = visualEntities.filter((entity) => adoptedByEntity.has(entityKey(entity))).length;
  const pendingCount = Array.from(pendingByEntity.values()).reduce((total, items) => total + items.length, 0);
  const hasEntities = visualEntities.length > 0;

  return (
    <section className="asset-generation-workspace">
      <header className="asset-generation-hero">
        <div>
          <span>STORY → ASSET IMAGES → VIDEO</span>
          <h2>{projectTitle} · 生成资产图</h2>
          <p>集中生成并确认所有角色和场景的基础图。道具保留为剧情结构，不进入默认生图队列。</p>
        </div>
        <div className="asset-generation-progress">
          <strong>{adoptedCount}/{visualEntities.length || 0}</strong>
          <span>基础图已采用{pendingCount ? ` · ${pendingCount} 个候选待处理` : ""}</span>
        </div>
      </header>

      {shotDirectionTask && shotDirectionTask.status !== "succeeded" ? (
        <GenerationTaskBanner
          actionBusy={isBusy}
          onCancel={onCancelTask}
          onRetry={onRetryTask}
          task={shotDirectionTask}
          title="分镜导演任务"
        />
      ) : null}

      {!hasEntities ? (
        <section className="asset-generation-empty">
          <span>STEP 01</span>
          <h3>先从剧本提取资产设定</h3>
          <p>系统会建立角色、场景和道具结构；只有角色和场景会进入下一步生图。</p>
          <button className="primary-button" onClick={onGenerateEntities} type="button">提取角色、场景和道具</button>
        </section>
      ) : (
        <>
          <section className="asset-generation-toolbar">
            <label>
              图片模型
              <select value={imageProfileId} onChange={(event) => onSelectProfile(event.target.value)}>
                {imageProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.label} · {profile.model_name}</option>
                ))}
              </select>
            </label>
            <div>
              <button className="secondary-button" onClick={onGenerateEntities} type="button">重新提取设定</button>
              <button className="primary-button" disabled={!imageProfileId} onClick={onGenerateAll} type="button">
                {adoptedCount || pendingCount ? "重新生成全部角色和场景" : "生成全部角色和场景"}
              </button>
            </div>
          </section>

          <div className="asset-generation-groups">
            {(["character", "scene"] as const).map((kind) => {
              const groupItems = visualEntities.filter((item) => item.kind === kind);
              return (
                <section key={kind}>
                  <header>
                    <div><span>{kind === "character" ? "CHARACTERS" : "SCENES"}</span><h3>{kindLabel(kind)}</h3></div>
                    <small>{groupItems.filter((item) => adoptedByEntity.has(entityKey(item))).length}/{groupItems.length} 已采用</small>
                  </header>
                  <div className="asset-generation-grid">
                    {groupItems.map((entity) => {
                      const key = entityKey(entity);
                      const adopted = adoptedByEntity.get(key);
                      const pending = (pendingByEntity.get(key) ?? []).sort((left, right) => right.version - left.version);
                      return (
                        <article className={adopted ? "complete" : pending.length ? "pending" : ""} key={key}>
                          <div className="asset-generation-preview">
                            {adopted ? (
                              <AssetPreview asset={adopted} />
                            ) : pending[0] && canPreviewCandidate(pending[0]) ? (
                              <img alt={`${entity.name} 待采用候选`} src={pending[0].uri} />
                            ) : (
                              <span>{kind === "character" ? "角色图" : "场景图"}<small>尚未生成</small></span>
                            )}
                          </div>
                          <div className="asset-generation-card-copy">
                            <span>{adopted ? "已采用" : pending.length ? "待采用" : "未生成"}</span>
                            <h4>{entity.name}</h4>
                            <p>{entity.summary}</p>
                          </div>
                          <EntityPromptPreview entity={entity} />
                          <div className="asset-generation-card-actions">
                            <button className="primary-button" disabled={!imageProfileId || isBusy} onClick={() => onGenerateImage(entity, "base")} type="button">
                              {adopted || pending.length ? `重新生成${kind === "character" ? "角色" : "场景"}候选` : `生成${kind === "character" ? "角色" : "场景"}图`}
                            </button>
                            {pending.length ? (
                              <div className="asset-generation-candidates">
                                {pending.map((candidate) => (
                                  <button className="primary-button" key={candidate.id} onClick={() => onAdopt(candidate)} type="button">
                                    采用候选 v{candidate.version}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                            <label className={`secondary-button upload-button ${isBusy ? "disabled" : ""}`}>
                              {adopted ? "上传新版本" : `上传${kind === "character" ? "角色" : "场景"}图`}
                              <input
                                accept="image/*"
                                disabled={isBusy}
                                onChange={(event) => {
                                  const file = event.currentTarget.files?.[0];
                                  event.currentTarget.value = "";
                                  if (file) onUploadImage(entity, "base", file);
                                }}
                                type="file"
                              />
                            </label>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        </>
      )}

      <footer className="asset-generation-footer">
        <button className="secondary-button" onClick={onManage} type="button">打开资产库精细管理</button>
        <div>
          <span>{adoptedCount < visualEntities.length ? "仍可继续，但缺少采用图的角色或场景不会作为视频参考。" : "角色和场景基础图已准备完成。"}</span>
          <button
            className="primary-button"
            disabled={!hasEntities || Boolean(shotDirectionTask && isGenerationTaskActive(shotDirectionTask))}
            onClick={onContinue}
            type="button"
          >
            {shotDirectionTask && isGenerationTaskActive(shotDirectionTask)
              ? "分镜导演任务进行中"
              : shotCount ? "进入视频制作 →" : "提交分镜导演任务 →"}
          </button>
        </div>
      </footer>
    </section>
  );
}

function AssetLibraryWorkspace({
  assets,
  candidates,
  entities,
  imageProfileId,
  imageProfiles,
  isBusy,
  onAdopt,
  onGenerateEntities,
  onGenerateImage,
  onSavePrompt,
  onSelectAsset,
  onSelectEntity,
  onSelectProfile,
  onSelectVariant,
  onUploadImage,
  selectedEntityKey,
  selectedVariantKey,
}: {
  assets: Asset[];
  candidates: AssetCandidate[];
  entities: EntityBundle;
  imageProfileId: string;
  imageProfiles: ImageProviderProfile[];
  isBusy: boolean;
  onAdopt: (candidate: AssetCandidate) => void;
  onGenerateEntities: () => void;
  onGenerateImage: (entity: EntityItem, variantKey: string) => void;
  onSavePrompt: (entity: EntityItem, prompt: string) => void;
  onSelectAsset: (asset: Asset) => void;
  onSelectEntity: (key: string) => void;
  onSelectProfile: (id: string) => void;
  onSelectVariant: (key: string) => void;
  onUploadImage: (entity: EntityItem, variantKey: string, file: File) => void;
  selectedEntityKey: string;
  selectedVariantKey: string;
}) {
  const items = entityItems(entities);
  const selected = items.find((item) => entityKey(item) === selectedEntityKey) ?? items[0] ?? null;
  const variants = selected ? entityVariants(selected) : [];
  const variantKey = variants.some((item) => item.key === selectedVariantKey) ? selectedVariantKey : "base";
  const versions = selected
    ? assets.filter((asset) => asset.asset_type === "image" && asset.entity_type === selected.kind && asset.entity_id === selected.id && (asset.variant_key ?? "base") === variantKey)
      .sort((left, right) => right.version - left.version)
    : [];
  const pending = selected
    ? candidates.filter((candidate) => candidate.asset_type === "image" && candidate.entity_type === selected.kind && candidate.entity_id === selected.id && (candidate.variant_key ?? "base") === variantKey && candidate.status === "pending_review")
    : [];
  const prompt = selected ? entityPrompt(selected) : "";
  const [promptDraft, setPromptDraft] = useState(prompt);
  useEffect(() => setPromptDraft(prompt), [prompt, selected?.id]);

  return (
    <section className="workflow-panel asset-library-workspace">
      <header className="workflow-panel-head">
        <div><span className="workflow-panel-kicker">Structured Asset Desk</span><h2>资产库</h2><p>每个角色只维护一张多视角设定图；剧情状态用文本进入片段 Prompt。道具保留为结构化数据。</p></div>
        <div className="shot-overview-actions">
          <label>图片模型<select value={imageProfileId} onChange={(event) => onSelectProfile(event.target.value)}>{imageProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label} · {profile.model_name}</option>)}</select></label>
          <button className="secondary-button" onClick={onGenerateEntities} type="button">{items.length ? "重新提取资产" : "提取资产"}</button>
        </div>
      </header>
      <div className="asset-desk-layout">
        <nav className="asset-desk-directory">
          {(["character", "scene", "prop"] as EntityKind[]).map((kind) => (
            <section key={kind}>
              <strong>{kindLabel(kind)}</strong>
              {items.filter((item) => item.kind === kind).map((item) => (
                <button className={entityKey(item) === selectedEntityKey ? "active" : ""} key={item.id} onClick={() => onSelectEntity(entityKey(item))} type="button">
                  <span>{item.name}</span><small>{item.summary}</small>
                </button>
              ))}
            </section>
          ))}
        </nav>
        {selected ? (
          <main className="asset-desk-main">
            <div className="asset-variant-tabs">
              {variants.map((variant) => (
                <button className={variant.key === variantKey ? "active" : ""} key={variant.key} onClick={() => onSelectVariant(variant.key)} type="button">
                  {variant.name}<small>{variant.key}</small>
                </button>
              ))}
            </div>
            <section className="asset-version-stage">
              <div className="asset-version-preview">
                {versions.find((asset) => asset.is_selected) ? <AssetPreview asset={versions.find((asset) => asset.is_selected)!} /> : <span>{selected.kind === "character" ? "这个角色还没有已采用的多视角图" : "这个状态还没有已采用图片"}</span>}
              </div>
              <div>
                <h3>{selected.name} · {variants.find((item) => item.key === variantKey)?.name}</h3>
                <p>{variants.find((item) => item.key === variantKey)?.description || selected.summary}</p>
                <p>出现位置：{variantSceneText(selected, variantKey)}</p>
                {selected.kind === "prop" ? (
                  <p className="asset-nonvisual-note">道具默认不生成独立资产图，由片段剧情、动作和空间关系描述控制。</p>
                ) : (
                  <div className="asset-version-actions">
                    <button className="primary-button" disabled={!imageProfileId || isBusy} onClick={() => onGenerateImage(selected, variantKey)} type="button">
                      {selected.kind === "character" ? (versions.length ? "重生成角色多视角图" : "生成角色多视角图") : "生成候选"}
                    </button>
                    <label className={`secondary-button upload-button ${isBusy ? "disabled" : ""}`}>
                      {versions.length ? "上传新版本" : "上传本地图片"}
                      <input
                        accept="image/*"
                        disabled={isBusy}
                        onChange={(event) => {
                          const file = event.currentTarget.files?.[0];
                          event.currentTarget.value = "";
                          if (file) onUploadImage(selected, variantKey, file);
                        }}
                        type="file"
                      />
                    </label>
                  </div>
                )}
              </div>
            </section>
            {selected.kind === "character" && characterStateVariants(selected).length ? (
              <section className="character-prompt-states">
                <div><strong>剧情状态</strong><span>只编译进对应视频 Prompt，不生成独立角色图</span></div>
                <div>{characterStateVariants(selected).map((variant) => <span key={variant.key}>{variant.name}</span>)}</div>
              </section>
            ) : null}
            {selected.kind !== "prop" ? (
              <label className="asset-prompt-editor">图片 Prompt<textarea rows={7} value={promptDraft} onChange={(event) => setPromptDraft(event.target.value)} /><button className="secondary-button" onClick={() => onSavePrompt(selected, promptDraft)} type="button">保存 Prompt</button></label>
            ) : null}
            {pending.length ? <section className="asset-candidate-strip"><h3>待采用候选</h3>{pending.map((candidate) => <article key={candidate.id}><span>v{candidate.version} · {candidate.model}</span><button className="primary-button" onClick={() => onAdopt(candidate)} type="button">采用此版本</button></article>)}</section> : null}
            <section className="asset-version-history"><h3>历史版本</h3>{versions.map((asset) => <button className={asset.is_selected ? "active" : ""} key={asset.id} onClick={() => onSelectAsset(asset)} type="button">v{asset.version} · {asset.model ?? asset.provider}{asset.is_selected ? " · 当前" : ""}</button>)}</section>
          </main>
        ) : <div className="slot-empty">先从当前剧本提取角色、场景和道具。</div>}
      </div>
    </section>
  );
}

function ExportDialog({
  assets,
  mode,
  onClose,
  onModeChange,
  onSubmit,
  shots,
}: {
  assets: Asset[];
  mode: SubtitleMode;
  onClose: () => void;
  onModeChange: (mode: SubtitleMode) => void;
  onSubmit: () => void;
  shots: Shot[];
}) {
  const selected = selectedVideoAssets(assets);
  return (
    <div className="asset-lightbox" role="presentation">
      <section aria-modal="true" className="export-dialog" role="dialog">
        <header><div><span>EXPORT</span><h2>导出本集</h2></div><button onClick={onClose} type="button">×</button></header>
        <label>字幕模式<select value={mode} onChange={(event) => onModeChange(event.target.value as SubtitleMode)}><option value="none">无字幕（默认）</option><option value="en">英文字幕</option><option value="bilingual">中英双语字幕</option></select></label>
        <p>视频片段原生音轨会被保留；没有音轨的片段会自动补静音。</p>
        <div className="export-version-list">{shots.map((shot) => { const asset = selected.find((item) => item.entity_id === shot.id); return <span key={shot.id}>片段 {shot.shot_no} · {asset ? `v${asset.version}` : "缺少已采用视频"}</span>; })}</div>
        <footer><button className="secondary-button" onClick={onClose} type="button">取消</button><button className="primary-button" disabled={selected.length !== shots.length || shots.length === 0} onClick={onSubmit} type="button">开始导出</button></footer>
      </section>
    </div>
  );
}

function dialogueInputs(items: Dialogue[]): DialogueInput[] {
  return items.map((item) => ({
    id: item.id,
    shot_id: item.shot_id,
    character_id: item.character_id,
    speaker_name: item.speaker_name,
    text: item.text,
    translation_zh: item.translation_zh,
    emotion: item.emotion,
    sequence_order: item.sequence_order,
    beat_id: item.beat_id,
    sound_cues: item.sound_cues,
    start_time: item.start_time,
    end_time: item.end_time,
  }));
}

function chapterInputText(chapter: Chapter | null | undefined) {
  if (!chapter) return "";
  return chapter.input_mode === "imported_script" ? chapter.source_text : chapter.outline;
}

function chapterUpdatePayload(chapter: Chapter, value: string) {
  return {
    input_mode: chapter.input_mode,
    outline: chapter.input_mode === "ai_brief" ? value : chapter.outline,
    source_text: chapter.input_mode === "imported_script" ? value : chapter.source_text,
  };
}

function entityItems(entities: EntityBundle): EntityItem[] {
  return [
    ...entities.characters.map((item: Character) => ({
      id: item.id,
      kind: "character" as const,
      name: item.name,
      summary: item.identity || item.appearance || "角色资产",
      promptFallback: item.fixed_prompt || item.appearance || item.name,
      assetSpec: item.asset_spec as unknown as Record<string, unknown>,
    })),
    ...entities.scenes.map((item: Scene) => ({
      id: item.id,
      kind: "scene" as const,
      name: item.name,
      summary: item.description || item.atmosphere || "场景资产",
      promptFallback: item.fixed_prompt || item.description || item.name,
      assetSpec: item.asset_spec as unknown as Record<string, unknown>,
    })),
    ...entities.props.map((item: Prop) => ({
      id: item.id,
      kind: "prop" as const,
      name: item.name,
      summary: item.description || item.story_function || "道具资产",
      promptFallback: item.visual_prompt || item.description || item.name,
      assetSpec: item.asset_spec as unknown as Record<string, unknown>,
    })),
  ];
}

function entityKey(item: EntityItem) {
  return `${item.kind}:${item.id}`;
}

function kindLabel(kind: EntityKind) {
  return { character: "角色", scene: "场景", prop: "道具" }[kind];
}

function entityRole(kind: EntityKind) {
  return { character: "character_main_ref", scene: "scene_ref", prop: "prop_ref" }[kind];
}

function entityVariants(item: EntityItem) {
  const raw = Array.isArray(item.assetSpec.state_variants) ? item.assetSpec.state_variants : [];
  const base = {
    key: "base",
    name: item.kind === "character" ? "多视角设定图" : "基础形象",
    description: item.summary,
    scene_nos: [] as number[],
  };
  if (item.kind === "character") return [base];
  return [
    base,
    ...raw.filter(isRecord).map((variant) => ({
      key: String(variant.key || ""),
      name: String(variant.name || variant.key || "状态变体"),
      description: String(variant.description || ""),
      scene_nos: Array.isArray(variant.scene_nos) ? variant.scene_nos.map(Number) : [],
    })).filter((variant) => variant.key && variant.key !== "base"),
  ];
}

function characterStateVariants(item: EntityItem) {
  if (item.kind !== "character") return [];
  const raw = Array.isArray(item.assetSpec.state_variants) ? item.assetSpec.state_variants : [];
  return raw.filter(isRecord).map((variant) => ({
    key: String(variant.key || ""),
    name: String(variant.name || variant.key || "剧情状态"),
  })).filter((variant) => variant.key && variant.key !== "base");
}

function entityPrompt(item: EntityItem) {
  const prompts = isRecord(item.assetSpec.image_prompts) ? item.assetSpec.image_prompts : {};
  const rawValue = prompts[entityRole(item.kind)];
  const value: Record<string, unknown> = isRecord(rawValue) ? rawValue : {};
  if (typeof value.positive_prompt === "string") return value.positive_prompt;
  const reference = isRecord(item.assetSpec.reference_prompt) ? item.assetSpec.reference_prompt : {};
  return typeof reference.positive_prompt === "string" ? reference.positive_prompt : item.promptFallback;
}

function EntityPromptPreview({ entity }: { entity: EntityItem }) {
  const prompt = entityPrompt(entity);
  return (
    <details className="asset-generation-prompt-preview">
      <summary>
        <span>预览 Prompt</span>
        <small>{prompt ? "查看实际生图描述" : "暂无已保存 Prompt"}</small>
      </summary>
      <div className="asset-generation-prompt-preview-body">
        <div>
          <strong>正向 Prompt</strong>
          <pre>{prompt || "当前实体还没有可预览的 Prompt。"}</pre>
        </div>
      </div>
    </details>
  );
}

function withEntityPrompt(item: EntityItem, prompt: string) {
  const spec = { ...item.assetSpec };
  const role = entityRole(item.kind);
  const prompts = isRecord(spec.image_prompts) ? { ...spec.image_prompts } : {};
  const current = isRecord(prompts[role]) ? prompts[role] : {};
  prompts[role] = { ...current, positive_prompt: prompt };
  spec.image_prompts = prompts;
  if (item.kind !== "character") {
    const reference = isRecord(spec.reference_prompt) ? { ...spec.reference_prompt } : {};
    reference.positive_prompt = prompt;
    spec.reference_prompt = reference;
  }
  return spec;
}

function variantSceneText(item: EntityItem, variantKey: string) {
  if (variantKey === "base") {
    const evidence = Array.isArray(item.assetSpec.source_evidence) ? item.assetSpec.source_evidence : [];
    const scenes = evidence.filter(isRecord).map((entry) => Number(entry.scene_no)).filter((value) => value > 0);
    return scenes.length ? scenes.join("、") : "全局";
  }
  return entityVariants(item).find((variant) => variant.key === variantKey)?.scene_nos.join("、") || "未指定";
}

function selectedVideoAssets(assets: Asset[]) {
  return assets.filter((asset) => asset.asset_type === "video" && asset.entity_type === "shot" && asset.is_selected);
}

function findLatestExportDownload(exports: ExportRecord[], assets: Asset[]) {
  const assetsById = new Map(assets.map((asset) => [asset.id, asset]));
  let latest: { asset: Asset; record: ExportRecord } | null = null;
  for (const record of exports) {
    if (record.status !== "completed" || !record.asset_id) continue;
    const asset = assetsById.get(record.asset_id);
    if (!asset || asset.asset_type !== "final_video" || !asset.uri) continue;
    if (!latest || Date.parse(record.created_at) > Date.parse(latest.record.created_at)) {
      latest = { asset, record };
    }
  }
  return latest;
}

function subtitleModeLabel(mode: ExportRecord["subtitle_mode"]) {
  if (mode === "bilingual") return "中英双语";
  if (mode === "en") return "英文字幕";
  return "无字幕";
}

async function downloadMediaFile(uri: string, filename: string) {
  const response = await fetch(uri);
  if (!response.ok) {
    throw new Error(`下载成片失败（HTTP ${response.status}）`);
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function canPreviewCandidate(candidate: AssetCandidate) {
  return candidate.asset_type === "image" && (candidate.uri.startsWith("http") || candidate.uri.startsWith("data:"));
}

function asStringList(value: unknown) {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
