import { type DragEvent, useEffect, useMemo, useState } from "react";

import type { ImageProviderProfile, ProviderDescriptor } from "../../services/apiClient";
import type {
  Asset,
  AssetCandidate,
  Dialogue,
  EntityBundle,
  Shot,
  ShotVideoPromptPreview,
} from "../../types/stageFive";
import { AssetPreview } from "./AssetPreview";

type AssetFilter = "character" | "scene" | "prop" | "material";
type DurationMode = "provider_auto" | "fixed";
type PreviewChoice =
  | { kind: "asset"; id: string }
  | { kind: "candidate"; id: string }
  | null;

export type ShotDraftPayload = {
  description: string;
  shotCard: Record<string, unknown>;
  videoPrompt: string;
  dialogues: Dialogue[];
};

type DirectorWorkbenchProps = {
  assets: Asset[];
  candidates: AssetCandidate[];
  dialogues: Dialogue[];
  entities: EntityBundle;
  imageProfileId: string;
  imageProfiles: ImageProviderProfile[];
  onAdopt: (candidate: AssetCandidate) => void;
  onBindAssets: (assetIds: string[]) => Promise<boolean>;
  onCompilePrompt: () => void;
  onCreateShot: () => void;
  onDeleteShot: (shot: Shot) => void;
  onExtractFrame: (asset: Asset) => void;
  onGenerateImage: () => void;
  onGenerateShots: () => void;
  onGenerateVideo: (videoPrompt: string, durationMode: DurationMode, durationSec: number | null) => void;
  onLocalRegenerate: (asset: Asset) => void;
  onOpenAssetLibrary: () => void;
  onPreviewPrompt: () => void;
  onReject: (candidate: AssetCandidate) => void;
  onReorderShots: (shotIds: string[]) => void;
  onSaveDraft: (payload: ShotDraftPayload) => Promise<boolean>;
  onSelectAsset: (asset: Asset) => void;
  onSelectImageProfile: (id: string) => void;
  onSelectShot: (id: string) => void;
  onSetFirstFrameReference: (assetId: string | null) => void;
  promptPreview: ShotVideoPromptPreview | null;
  selectedShot: Shot;
  selectedVideoProvider?: ProviderDescriptor;
  shots: Shot[];
};

export function DirectorWorkbench({
  assets,
  candidates,
  dialogues,
  entities,
  imageProfileId,
  imageProfiles,
  onAdopt,
  onBindAssets,
  onCompilePrompt,
  onCreateShot,
  onDeleteShot,
  onExtractFrame,
  onGenerateImage,
  onGenerateShots,
  onGenerateVideo,
  onLocalRegenerate,
  onOpenAssetLibrary,
  onPreviewPrompt,
  onReject,
  onReorderShots,
  onSaveDraft,
  onSelectAsset,
  onSelectImageProfile,
  onSelectShot,
  onSetFirstFrameReference,
  promptPreview,
  selectedShot,
  selectedVideoProvider,
  shots,
}: DirectorWorkbenchProps) {
  const [assetFilter, setAssetFilter] = useState<AssetFilter>("character");
  const [assetSearch, setAssetSearch] = useState("");
  const [inspectedAssetId, setInspectedAssetId] = useState("");
  const [referencePickerAssetId, setReferencePickerAssetId] = useState("");
  const [referencePickerEntityId, setReferencePickerEntityId] = useState("");
  const [referencePickerSelectionId, setReferencePickerSelectionId] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [draftDescription, setDraftDescription] = useState(selectedShot.description);
  const [draftCard, setDraftCard] = useState<Record<string, unknown>>(cloneCard(selectedShot.shot_card));
  const [draftPrompt, setDraftPrompt] = useState(selectedShot.video_prompt ?? "");
  const [durationMode, setDurationMode] = useState<DurationMode>(
    selectedVideoProvider?.smart_duration ? "provider_auto" : "fixed",
  );
  const [fixedDuration, setFixedDuration] = useState(
    Number(selectedShot.duration_sec) || selectedVideoProvider?.min_duration_sec || 5,
  );
  const [draftDialogues, setDraftDialogues] = useState<Dialogue[]>([]);
  const [dirty, setDirty] = useState(false);
  const [pendingShotId, setPendingShotId] = useState("");
  const [previewChoice, setPreviewChoice] = useState<PreviewChoice>(null);
  const [draggedShotId, setDraggedShotId] = useState("");

  const entityMap = useMemo(() => buildEntityMap(entities), [entities]);
  const imageAssets = useMemo(
    () => assets.filter((asset) => asset.asset_type === "image"),
    [assets],
  );
  const adoptedImages = useMemo(
    () => imageAssets.filter((asset) => asset.is_selected && isDefaultVideoImageAsset(asset)),
    [imageAssets],
  );
  const explicitReferenceIds = readStringList(selectedShot.shot_card.reference_asset_ids);
  const referenceAssets = useMemo(
    () => resolveReferenceAssets(selectedShot, imageAssets, adoptedImages, explicitReferenceIds),
    [adoptedImages, explicitReferenceIds.join("|"), imageAssets, selectedShot],
  );
  const referenceAssetIds = referenceAssets.map((asset) => asset.id);
  const shotDialogues = useMemo(
    () => dialogues
      .filter((dialogue) => selectedShot.dialogue_ids.map(String).includes(dialogue.id) || dialogue.shot_id === selectedShot.id)
      .sort((left, right) => left.sequence_order - right.sequence_order),
    [dialogues, selectedShot],
  );

  useEffect(() => {
    setDraftDescription(selectedShot.description);
    setDraftCard(cloneCard(selectedShot.shot_card));
    setDraftPrompt(selectedShot.video_prompt ?? "");
    setDraftDialogues(shotDialogues.map((dialogue) => ({ ...dialogue, sound_cues: [...dialogue.sound_cues] })));
    setDirty(false);
    setIsEditing(false);
    setPendingShotId("");
    setPreviewChoice(null);
    setReferencePickerAssetId("");
    setReferencePickerEntityId("");
    setReferencePickerSelectionId("");
  }, [selectedShot.id, selectedShot.updated_at]);

  useEffect(() => {
    setDurationMode(selectedVideoProvider?.smart_duration ? "provider_auto" : "fixed");
    setFixedDuration(Number(selectedShot.duration_sec) || selectedVideoProvider?.min_duration_sec || 5);
  }, [
    selectedShot.id,
    selectedShot.duration_sec,
    selectedVideoProvider?.name,
    selectedVideoProvider?.smart_duration,
    selectedVideoProvider?.min_duration_sec,
  ]);

  const assetItems = adoptedImages
    .filter((asset) => {
      const matchesFilter = (assetFilter === "material" && !["character", "scene", "prop"].includes(asset.entity_type ?? ""))
        || asset.entity_type === assetFilter;
      const searchText = `${assetLabel(asset, entityMap)} ${asset.variant_key ?? "base"} ${asset.asset_role ?? ""}`.toLowerCase();
      return matchesFilter && searchText.includes(assetSearch.trim().toLowerCase());
    })
    .sort((left, right) => (
      assetSortPriority(left, referenceAssetIds, selectedShot.id)
      - assetSortPriority(right, referenceAssetIds, selectedShot.id)
    ));

  const selectedShotAssets = assets
    .filter((asset) => asset.entity_type === "shot" && asset.entity_id === selectedShot.id)
    .sort((left, right) => right.version - left.version);
  const currentImage = selectedShotAssets.find(
    (asset) => asset.asset_type === "image" && asset.asset_role === "shot_storyboard" && asset.is_selected,
  ) ?? null;
  const currentVideo = selectedShotAssets.find(
    (asset) => asset.asset_type === "video" && asset.is_selected,
  ) ?? null;
  const explicitFirstFrameId = typeof selectedShot.shot_card.video_reference_asset_id === "string"
    ? selectedShot.shot_card.video_reference_asset_id
    : "";
  const explicitFirstFrame = explicitFirstFrameId
    ? imageAssets.find((asset) => asset.id === explicitFirstFrameId) ?? null
    : null;
  const hasExplicitFirstFrame = Boolean(explicitFirstFrameId);
  const pendingCandidates = candidates
    .filter((candidate) => candidate.entity_type === "shot" && candidate.entity_id === selectedShot.id && candidate.status === "pending_review")
    .sort((left, right) => right.version - left.version);
  const inspectedAsset = adoptedImages.find((asset) => asset.id === inspectedAssetId) ?? null;
  const previewAsset = previewChoice?.kind === "asset"
    ? selectedShotAssets.find((asset) => asset.id === previewChoice.id) ?? null
    : null;
  const previewCandidate = previewChoice?.kind === "candidate"
    ? pendingCandidates.find((candidate) => candidate.id === previewChoice.id) ?? null
    : null;
  const visibleMedia = previewCandidate ?? previewAsset ?? currentVideo ?? currentImage;
  const beats = shotBeats(draftCard);
  const usesSmartDuration = Boolean(selectedVideoProvider?.smart_duration) && durationMode === "provider_auto";
  const draftDuration = usesSmartDuration ? 0 : Math.max(0, Number(fixedDuration) || 0);
  const providerDurationMinimum = selectedVideoProvider?.min_duration_sec ?? null;
  const providerDurationLimit = selectedVideoProvider?.max_duration_sec ?? null;
  const providerDurationExceeded = (
    !usesSmartDuration
    && (
      draftDuration <= 0
      || (providerDurationMinimum !== null && draftDuration < providerDurationMinimum)
      || (providerDurationLimit !== null && draftDuration > providerDurationLimit)
    )
  );
  const promptStale = Boolean(selectedShot.shot_card.prompt_stale) || Boolean(promptPreview?.stale);
  const referencePickerAsset = referenceAssets.find((asset) => asset.id === referencePickerAssetId) ?? null;
  const referencePickerOptions = referencePickerAsset
    ? imageAssets
      .filter((asset) => (
        isDefaultVideoImageAsset(asset)
        && asset.entity_type === referencePickerAsset.entity_type
        && Boolean(asset.entity_id)
        && (
          asset.status === "approved"
          || (asset.is_selected && asset.status === "ready_for_review")
          || asset.id === referencePickerAsset.id
        )
        && (
          entityMap.has(`${asset.entity_type}:${asset.entity_id}`)
          || asset.entity_id === referencePickerAsset.entity_id
        )
      ))
      .sort(compareReferencePickerAssets)
    : [];
  const referencePickerEntities = referencePickerOptions.reduce<Array<{ id: string; label: string; thumbnail: Asset; count: number }>>(
    (items, asset) => {
      const entityId = asset.entity_id ?? "";
      const existing = items.find((item) => item.id === entityId);
      if (existing) {
        existing.count += 1;
      } else {
        items.push({
          id: entityId,
          label: assetLabel(asset, entityMap),
          thumbnail: asset,
          count: 1,
        });
      }
      return items;
    },
    [],
  );
  const referencePickerVersions = referencePickerOptions.filter(
    (asset) => asset.entity_id === referencePickerEntityId,
  );
  const referencePickerSelection = referencePickerOptions.find(
    (asset) => asset.id === referencePickerSelectionId,
  ) ?? referencePickerAsset;

  function markDirty() {
    setDirty(true);
  }

  function updateBeat(index: number, updates: Record<string, unknown>) {
    setDraftCard((current) => withBeat(current, index, updates));
    markDirty();
  }

  function updateDialogue(index: number, updates: Partial<Dialogue>) {
    setDraftDialogues((current) => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...updates } : item
    )));
    markDirty();
  }

  async function saveDraft() {
    const saved = await onSaveDraft({
      description: draftDescription,
      shotCard: draftCard,
      videoPrompt: draftPrompt,
      dialogues: draftDialogues,
    });
    if (saved) {
      setDirty(false);
      setIsEditing(false);
    }
    return saved;
  }

  function requestShotSelection(shotId: string) {
    if (shotId === selectedShot.id) return;
    if (dirty) {
      setPendingShotId(shotId);
      return;
    }
    onSelectShot(shotId);
  }

  async function bindAsset(asset: Asset) {
    const next = referenceAssets.filter((item) => {
      if (asset.entity_type === "scene") return item.entity_type !== "scene";
      return !(item.entity_type === asset.entity_type && item.entity_id === asset.entity_id);
    });
    next.push(asset);
    await onBindAssets(next.map((item) => item.id));
    setInspectedAssetId("");
  }

  function openReferencePicker(asset: Asset) {
    setReferencePickerAssetId(asset.id);
    setReferencePickerEntityId(asset.entity_id ?? "");
    setReferencePickerSelectionId(asset.id);
  }

  function closeReferencePicker() {
    setReferencePickerAssetId("");
    setReferencePickerEntityId("");
    setReferencePickerSelectionId("");
  }

  function selectReferencePickerEntity(entityId: string) {
    setReferencePickerEntityId(entityId);
    const preferred = preferredReferenceAsset(
      referencePickerOptions.filter((asset) => asset.entity_id === entityId),
    );
    setReferencePickerSelectionId(preferred?.id ?? "");
  }

  async function applyReferenceReplacement() {
    if (!referencePickerAsset || !referencePickerSelection) return;
    if (referencePickerAsset.id === referencePickerSelection.id) {
      closeReferencePicker();
      return;
    }
    const sourceIndex = referenceAssets.findIndex((asset) => asset.id === referencePickerAsset.id);
    const next = referenceAssets.filter((asset) => (
      asset.id !== referencePickerAsset.id
      && !(
        asset.entity_type === referencePickerSelection.entity_type
        && asset.entity_id === referencePickerSelection.entity_id
      )
      && !(referencePickerSelection.entity_type === "scene" && asset.entity_type === "scene")
    ));
    next.splice(Math.min(Math.max(sourceIndex, 0), next.length), 0, referencePickerSelection);
    const saved = await onBindAssets(next.map((asset) => asset.id));
    if (saved) {
      closeReferencePicker();
    }
  }

  async function removeReference(assetId: string) {
    const saved = await onBindAssets(referenceAssetIds.filter((id) => id !== assetId));
    if (saved && assetId === referencePickerAssetId) {
      closeReferencePicker();
    }
  }

  function handleDropAsset(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    const assetId = event.dataTransfer.getData("application/x-animation-asset");
    const asset = adoptedImages.find((item) => item.id === assetId);
    if (asset) void bindAsset(asset);
  }

  function handleShotDrop(targetShotId: string) {
    if (!draggedShotId || draggedShotId === targetShotId) return;
    const ids = shots.map((shot) => shot.id);
    const fromIndex = ids.indexOf(draggedShotId);
    const targetIndex = ids.indexOf(targetShotId);
    if (fromIndex < 0 || targetIndex < 0) return;
    const [moved] = ids.splice(fromIndex, 1);
    ids.splice(targetIndex, 0, moved);
    setDraggedShotId("");
    onReorderShots(ids);
  }

  return (
    <section className="director-workbench">
      <aside className="director-assets">
        <header>
          <div>
            <span>ASSET LIBRARY</span>
            <h2>资产</h2>
          </div>
          <button className="director-icon-button" onClick={onOpenAssetLibrary} title="打开完整资产库" type="button">管理</button>
        </header>
        <label className="director-asset-search">
          <span>⌕</span>
          <input
            aria-label="搜索资产"
            onChange={(event) => setAssetSearch(event.target.value)}
            placeholder="搜索角色、场景、道具"
            value={assetSearch}
          />
        </label>
        <nav className="director-asset-filters" aria-label="资产筛选">
          {([
            ["character", "角色"],
            ["scene", "场景"],
            ["prop", "道具"],
            ["material", "素材"],
          ] as Array<[AssetFilter, string]>).map(([value, label]) => (
            <button className={assetFilter === value ? "active" : ""} key={value} onClick={() => setAssetFilter(value)} type="button">
              {label}
            </button>
          ))}
        </nav>
        <div className="director-asset-grid">
          {assetItems.map((asset) => (
            <article
              className={referenceAssetIds.includes(asset.id) ? "director-asset-card referenced" : "director-asset-card"}
              draggable={asset.entity_type !== "prop"}
              key={asset.id}
              onDragStart={(event) => event.dataTransfer.setData("application/x-animation-asset", asset.id)}
            >
              <button aria-label={`预览 ${assetLabel(asset, entityMap)}`} onClick={() => setInspectedAssetId(asset.id)} type="button">
                <AssetThumbnail asset={asset} />
              </button>
              <div>
                <strong>{assetLabel(asset, entityMap)}</strong>
                <span>{variantLabel(asset.variant_key)} · v{asset.version}</span>
              </div>
              <button className="director-reference-button" onClick={() => void bindAsset(asset)} type="button">
                {referenceAssetIds.includes(asset.id) ? "已引用" : "＋ 引用"}
              </button>
            </article>
          ))}
          {!assetItems.length ? <div className="director-empty compact">没有匹配的已采用资产</div> : null}
        </div>
        <label className="director-image-profile">
          <span>图片模型</span>
          <select value={imageProfileId} onChange={(event) => onSelectImageProfile(event.target.value)}>
            {imageProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>{profile.label}</option>
            ))}
          </select>
        </label>
      </aside>

      <main
        className="director-script"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDropAsset}
      >
        <header className="director-script-head">
          <div>
            <span>片段 {String(selectedShot.shot_no).padStart(2, "0")}</span>
            <h2>{selectedShot.description || "未命名片段"}</h2>
          </div>
          <div className="director-script-actions">
            {dirty ? <em>有未保存修改</em> : null}
            <button className="secondary-button" onClick={() => setIsEditing((value) => !value)} type="button">
              {isEditing ? "返回阅读" : "编辑"}
            </button>
            {dirty ? <button className="primary-button" onClick={() => void saveDraft()} type="button">保存片段</button> : null}
          </div>
        </header>

        <section className="director-reference-strip" aria-label="当前片段引用资产">
          <span>引用资产</span>
          {referenceAssets.map((asset) => (
            <article key={asset.id}>
              <button
                aria-label={`更换 ${assetLabel(asset, entityMap)} 的引用版本`}
                className="director-reference-chip-main"
                onClick={() => openReferencePicker(asset)}
                type="button"
              >
                <span><AssetThumbnail asset={asset} /></span>
                <span>
                  <strong>{assetLabel(asset, entityMap)}</strong>
                  <small>
                    {variantLabel(asset.variant_key)} · v{asset.version}
                  </small>
                </span>
              </button>
              <button
                aria-label={`移除 ${assetLabel(asset, entityMap)}`}
                className="director-reference-chip-remove"
                onClick={() => void removeReference(asset.id)}
                type="button"
              >
                ×
              </button>
            </article>
          ))}
          {!referenceAssets.length ? <p>从左侧点击“引用”，或把资产拖到这里。</p> : null}
        </section>

        <div className="director-script-scroll">
          {isEditing ? (
            <section className="director-structured-editor">
              <label>
                <span>片段目标</span>
                <textarea rows={3} value={draftDescription} onChange={(event) => { setDraftDescription(event.target.value); markDirty(); }} />
              </label>
              {beats.map((beat, index) => (
                <article className="director-beat-edit" key={String(beat.beat_id ?? index)}>
                  <header><strong>分镜 {index + 1}</strong><span>由模型安排节奏</span></header>
                  <div className="director-beat-fields">
                    <label>镜头语言<input value={String(beat.camera ?? "")} onChange={(event) => updateBeat(index, { camera: event.target.value })} /></label>
                    <label className="wide">动作<textarea rows={3} value={String(beat.action ?? "")} onChange={(event) => updateBeat(index, { action: event.target.value })} /></label>
                    <label className="wide">音效提示<input value={readStringList(beat.sound_cues).join("、")} onChange={(event) => updateBeat(index, { sound_cues: splitList(event.target.value) })} /></label>
                  </div>
                </article>
              ))}
              <section className="director-dialogue-editor">
                <header><strong>英文对白与字幕稿</strong><span>{draftDialogues.length} 句</span></header>
                {draftDialogues.map((dialogue, index) => (
                  <article key={dialogue.id}>
                    <label>说话人<input value={dialogue.speaker_name} onChange={(event) => updateDialogue(index, { speaker_name: event.target.value })} /></label>
                    <label>情绪<input value={dialogue.emotion ?? ""} onChange={(event) => updateDialogue(index, { emotion: event.target.value })} /></label>
                    <label className="wide">英文对白<textarea rows={2} value={dialogue.text} onChange={(event) => updateDialogue(index, { text: event.target.value })} /></label>
                    <label className="wide">中文释义（可选）<input value={dialogue.translation_zh ?? ""} onChange={(event) => updateDialogue(index, { translation_zh: event.target.value })} /></label>
                    <label className="wide">音效提示<input value={dialogue.sound_cues.join("、")} onChange={(event) => updateDialogue(index, { sound_cues: splitList(event.target.value) })} /></label>
                  </article>
                ))}
              </section>
            </section>
          ) : (
            <section className="director-readable-script">
              <p className="director-scene-line">
                本片段场景设定在：
                {referenceAssets.filter((asset) => asset.entity_type === "scene").map((asset) => (
                  <InlineAsset key={asset.id} asset={asset} entityMap={entityMap} />
                ))}
                {!referenceAssets.some((asset) => asset.entity_type === "scene") ? <em>未绑定场景</em> : null}
              </p>
              <p className="director-shot-goal">{draftDescription}</p>
              {beats.map((beat, index) => {
                const beatDialogueIds = new Set(readStringList(beat.dialogue_ids));
                const matchedDialogues = draftDialogues.filter((dialogue) => (
                  beatDialogueIds.has(dialogue.id) || dialogue.beat_id === beat.beat_id
                ));
                return (
                  <article className="director-readable-beat" key={String(beat.beat_id ?? index)}>
                    <header>
                      <strong>分镜 {index + 1}</strong>
                      <em>{String(beat.camera ?? "镜头语言待补充")}</em>
                    </header>
                    <p>{String(beat.action ?? draftDescription)}</p>
                    {matchedDialogues.map((dialogue) => (
                      <blockquote key={dialogue.id}>
                        <strong>{dialogue.speaker_name}</strong>
                        <span>“{dialogue.text}”</span>
                        {dialogue.translation_zh ? <small>{dialogue.translation_zh}</small> : null}
                        {dialogue.emotion ? <em>{dialogue.emotion}</em> : null}
                      </blockquote>
                    ))}
                    {readStringList(beat.sound_cues).length ? (
                      <div className="director-sound-cues">音效 · {readStringList(beat.sound_cues).join(" · ")}</div>
                    ) : null}
                  </article>
                );
              })}
              {!beats.length ? <div className="director-empty">当前片段还没有内部镜头节拍。</div> : null}
            </section>
          )}

          <details className="director-advanced-prompt">
            <summary>
              <span>高级 Prompt</span>
              <em className={promptStale ? "stale" : "fresh"}>{promptStale ? "可能过期" : "与输入一致"}</em>
            </summary>
            <textarea rows={12} value={draftPrompt} onChange={(event) => { setDraftPrompt(event.target.value); markDirty(); }} />
            <div className="director-advanced-actions">
              <button className="secondary-button" disabled={dirty} onClick={onPreviewPrompt} type="button">预览编译结果</button>
              <button className="primary-button" disabled={dirty} onClick={onCompilePrompt} type="button">重新编译 Prompt</button>
            </div>
            {dirty ? <p>先保存片段，再根据最新结构重新编译。</p> : null}
            {promptPreview ? (
              <div className="director-video-reference-summary">
                <strong>实际发送给视频模型的素材</strong>
                <div>
                  {promptPreview.reference_assets.map((item) => (
                    <span key={item.asset_id}>
                      {item.media_label} · {
                        item.reference_role === "video_first_frame"
                          ? "片段首帧"
                          : entityMap.get(`${item.entity_type}:${item.entity_id}`) ?? entityTypeLabel(item.entity_type)
                      }
                    </span>
                  ))}
                  {!promptPreview.reference_assets.length ? <span>当前没有参考图</span> : null}
                </div>
                <small>
                  {hasExplicitFirstFrame
                    ? "已明确启用首帧；取消后视频只使用角色、场景参考图和文本 Prompt。"
                    : "当前未使用首帧。默认只发送角色、场景参考图和文本 Prompt；道具不生成独立资产图。"}
                </small>
              </div>
            ) : null}
            {promptPreview ? <pre>{promptPreview.prompt}</pre> : null}
          </details>
        </div>
      </main>

      <aside className="director-preview">
        <header>
          <div><span>PREVIEW</span><h2>视频预览</h2></div>
          <span className="director-model-badge">{selectedVideoProvider?.model ?? "视频模型未配置"}</span>
        </header>
        <div className="director-preview-canvas">
          {visibleMedia ? <MediaPreview media={visibleMedia} /> : <div className="director-empty">尚无首帧或视频</div>}
          <div className="director-preview-status">
            <span>{
              previewCandidate
                ? "待采用候选"
                : previewAsset
                  ? `${previewAsset.is_selected ? "当前" : "历史"}${previewAsset.asset_type === "video" ? "视频" : "首帧"} v${previewAsset.version}`
                  : currentVideo
                    ? `当前视频 v${currentVideo.version}`
                    : currentImage
                      ? `当前首帧 v${currentImage.version}`
                      : "未生成"
            }</span>
          </div>
        </div>
        <div className="director-preview-primary-actions">
          <button className="secondary-button" onClick={onGenerateImage} type="button">{currentImage ? "重生成首帧" : "生成首帧"}</button>
          <button
            className="secondary-button"
            disabled={!currentImage && !hasExplicitFirstFrame}
            onClick={() => onSetFirstFrameReference(hasExplicitFirstFrame ? null : currentImage?.id ?? null)}
            type="button"
          >
            {hasExplicitFirstFrame ? "取消使用首帧" : "使用首帧"}
          </button>
          <button
            className="primary-button"
            disabled={providerDurationExceeded}
            onClick={() => onGenerateVideo(
              draftPrompt,
              usesSmartDuration ? "provider_auto" : "fixed",
              usesSmartDuration ? null : draftDuration,
            )}
            title={providerDurationExceeded ? "当前模型不支持这个片段总时长，请切换视频模型或调整片段方案" : undefined}
            type="button"
          >
            生成视频
          </button>
        </div>
        <p className="director-first-frame-mode">
          {hasExplicitFirstFrame
            ? `视频会使用已明确选择的首帧${explicitFirstFrame ? ` v${explicitFirstFrame.version}` : ""}。`
            : "默认不使用首帧；生成首帧不会自动改变视频输入。"}
        </p>
        <details className="director-duration-settings">
          <summary>时长 · {usesSmartDuration ? "智能选择" : `${formatSeconds(draftDuration)} 秒`}</summary>
          <div>
            <label>
              <span>模式</span>
              <select
                onChange={(event) => setDurationMode(event.target.value as DurationMode)}
                value={durationMode}
              >
                {selectedVideoProvider?.smart_duration ? <option value="provider_auto">智能时长（默认）</option> : null}
                <option value="fixed">固定时长</option>
              </select>
            </label>
            {durationMode === "fixed" ? (
              <label>
                <span>秒数</span>
                <input
                  max={providerDurationLimit ?? 30}
                  min={providerDurationMinimum ?? 1}
                  onChange={(event) => setFixedDuration(Number(event.target.value))}
                  step="1"
                  type="number"
                  value={fixedDuration}
                />
              </label>
            ) : null}
          </div>
        </details>
        {usesSmartDuration ? (
          <p className="director-first-frame-mode">
            智能时长已开启：模型会按完整动作和对白决定片段长度，生成后按实际秒数计费和合成。
          </p>
        ) : null}
        {providerDurationExceeded ? (
          <p className="director-duration-warning">
            固定时长 {formatSeconds(draftDuration)} 秒不符合 {selectedVideoProvider?.model ?? "视频模型"}
            {providerDurationMinimum !== null && providerDurationLimit !== null
              ? ` 的 ${formatSeconds(providerDurationMinimum)}-${formatSeconds(providerDurationLimit)} 秒范围。`
              : " 的时长范围。"}
            系统不会自动截断。
          </p>
        ) : null}
        {previewCandidate ? (
          <div className="director-candidate-actions">
            <button className="secondary-button" onClick={() => onReject(previewCandidate)} type="button">拒绝</button>
            <button className="primary-button" onClick={() => onAdopt(previewCandidate)} type="button">采用此版本</button>
          </div>
        ) : null}
        {currentVideo ? (
          <div className="director-preview-tools">
            <a download href={currentVideo.uri}>下载</a>
            <button onClick={() => onExtractFrame(currentVideo)} type="button">截帧</button>
            <button onClick={() => onLocalRegenerate(currentVideo)} type="button">重新生成本片段</button>
          </div>
        ) : null}
        <section className="director-version-strip">
          <header><strong>候选与版本</strong><span>{pendingCandidates.length + selectedShotAssets.length}</span></header>
          <div>
            {pendingCandidates.map((candidate) => (
              <button className={previewChoice?.kind === "candidate" && previewChoice.id === candidate.id ? "active pending" : "pending"} key={candidate.id} onClick={() => setPreviewChoice({ kind: "candidate", id: candidate.id })} type="button">
                <MediaThumbnail media={candidate} />
                <span>候选 v{candidate.version}</span>
              </button>
            ))}
            {selectedShotAssets.map((asset) => (
              <button className={previewChoice?.kind === "asset" && previewChoice.id === asset.id ? "active" : asset.is_selected ? "current" : ""} key={asset.id} onClick={() => setPreviewChoice({ kind: "asset", id: asset.id })} type="button">
                <MediaThumbnail media={asset} />
                <span>{asset.asset_type === "video" ? "视频" : "首帧"} v{asset.version}</span>
                {!asset.is_selected ? <small onClick={(event) => { event.stopPropagation(); onSelectAsset(asset); }}>设为当前</small> : null}
              </button>
            ))}
          </div>
        </section>
      </aside>

      <section className="director-timeline" aria-label="片段时间轴">
        <header>
          <div><strong>片段时间轴</strong><span>{shots.length} 个片段</span></div>
          <button className="secondary-button" onClick={onGenerateShots} type="button">重新生成方案</button>
        </header>
        <div className="director-timeline-scroll">
          {shots.map((shot, index) => {
            const thumbnail = shotThumbnail(shot, assets);
            const status = shotStatus(shot, assets, candidates);
            return (
              <article
                aria-current={shot.id === selectedShot.id ? "true" : undefined}
                className={shot.id === selectedShot.id ? "director-shot-card active" : "director-shot-card"}
                draggable
                key={shot.id}
                onClick={() => requestShotSelection(shot.id)}
                onDragOver={(event) => event.preventDefault()}
                onDragStart={() => setDraggedShotId(shot.id)}
                onDrop={() => handleShotDrop(shot.id)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowLeft" && index > 0) requestShotSelection(shots[index - 1].id);
                  if (event.key === "ArrowRight" && index < shots.length - 1) requestShotSelection(shots[index + 1].id);
                }}
                role="button"
                tabIndex={0}
              >
                <MediaThumbnail media={thumbnail} />
                <div>
                  <strong>片段 {String(shot.shot_no).padStart(2, "0")}</strong>
                  <span>{shot.duration_sec == null ? "智能" : `${Number(shot.duration_sec)}s`}</span>
                </div>
                <small className={`status-${status.tone}`}><i />{status.label}</small>
                <button aria-label={`删除片段 ${shot.shot_no}`} onClick={(event) => { event.stopPropagation(); onDeleteShot(shot); }} title="删除片段" type="button">×</button>
              </article>
            );
          })}
          <button className="director-add-shot" onClick={onCreateShot} type="button"><strong>＋</strong><span>新增片段</span></button>
        </div>
      </section>

      {inspectedAsset ? (
        <div className="director-modal-backdrop" onClick={() => setInspectedAssetId("")} role="presentation">
          <section aria-label="资产预览" aria-modal="true" className="director-asset-inspector" onClick={(event) => event.stopPropagation()} role="dialog">
            <header><div><strong>{assetLabel(inspectedAsset, entityMap)}</strong><span>{variantLabel(inspectedAsset.variant_key)} · v{inspectedAsset.version}</span></div><button onClick={() => setInspectedAssetId("")} type="button">×</button></header>
            <AssetPreview asset={inspectedAsset} />
            <footer>
              <button className="secondary-button" onClick={() => setInspectedAssetId("")} type="button">关闭</button>
              <button className="primary-button" onClick={() => void bindAsset(inspectedAsset)} type="button">引用到当前片段</button>
            </footer>
          </section>
        </div>
      ) : null}

      {referencePickerAsset ? (
        <div className="director-modal-backdrop" onClick={closeReferencePicker} role="presentation">
          <section
            aria-label={`更换${entityTypeLabel(referencePickerAsset.entity_type)}引用`}
            aria-modal="true"
            className="director-reference-picker"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
          >
            <header>
              <div>
                <span>精确引用版本</span>
                <h2>更换{entityTypeLabel(referencePickerAsset.entity_type)}</h2>
              </div>
              <button aria-label="关闭资产替换器" onClick={closeReferencePicker} type="button">×</button>
            </header>
            <div className="director-reference-picker-token">
              <span>视频模型使用的精确参考版本</span>
              <code>{referencePickerSelection ? referenceToken(referencePickerSelection, entityMap) : "请选择资产版本"}</code>
              <small>
                系统内部绑定精确资产版本；实际 Prompt 使用与 Provider 请求一致的“图片N”指代。
              </small>
            </div>
            <div className="director-reference-picker-body">
              <nav aria-label={`${entityTypeLabel(referencePickerAsset.entity_type)}列表`}>
                <strong>选择{entityTypeLabel(referencePickerAsset.entity_type)}</strong>
                {referencePickerEntities.map((item) => (
                  <button
                    className={item.id === referencePickerEntityId ? "active" : ""}
                    key={item.id}
                    onClick={() => selectReferencePickerEntity(item.id)}
                    type="button"
                  >
                    <span><AssetThumbnail asset={item.thumbnail} /></span>
                    <span><b>{item.label}</b><small>{item.count} 个可用版本</small></span>
                  </button>
                ))}
              </nav>
              <section className="director-reference-version-panel">
                <header>
                  <div>
                    <strong>状态变体与历史版本</strong>
                    <span>点击缩略图选择本片段要使用的精确版本</span>
                  </div>
                </header>
                <div className="director-reference-version-grid">
                  {referencePickerVersions.map((asset) => (
                    <button
                      aria-pressed={referencePickerSelection?.id === asset.id}
                      className={referencePickerSelection?.id === asset.id ? "active" : ""}
                      key={asset.id}
                      onClick={() => setReferencePickerSelectionId(asset.id)}
                      type="button"
                    >
                      <span className="director-reference-version-image"><AssetThumbnail asset={asset} /></span>
                      <span className="director-reference-version-copy">
                        <strong>{variantLabel(asset.variant_key)}</strong>
                        <small>版本 v{asset.version}</small>
                        <em>
                          {asset.id === referencePickerAsset.id
                            ? "片段当前绑定"
                            : asset.is_selected
                              ? "资产当前版本"
                              : "已采用历史版本"}
                        </em>
                      </span>
                    </button>
                  ))}
                  {!referencePickerVersions.length ? <div className="director-empty compact">该资产没有可绑定版本</div> : null}
                </div>
              </section>
            </div>
            <footer>
              <button className="danger-button" onClick={() => void removeReference(referencePickerAsset.id)} type="button">从片段移除</button>
              <span />
              <button className="secondary-button" onClick={closeReferencePicker} type="button">取消</button>
              <button
                className="primary-button"
                disabled={!referencePickerSelection}
                onClick={() => void applyReferenceReplacement()}
                type="button"
              >
                {referencePickerSelection?.id === referencePickerAsset.id ? "保持当前版本" : "应用到当前片段"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {pendingShotId ? (
        <div className="director-modal-backdrop" role="presentation">
          <section aria-label="未保存修改" aria-modal="true" className="director-unsaved-dialog" role="dialog">
            <h2>当前片段有未保存修改</h2>
            <p>保存后切换，或放弃本次修改。</p>
            <footer>
              <button className="secondary-button" onClick={() => setPendingShotId("")} type="button">取消</button>
              <button className="secondary-button" onClick={() => { const next = pendingShotId; setDirty(false); setPendingShotId(""); onSelectShot(next); }} type="button">放弃修改</button>
              <button className="primary-button" onClick={() => { const next = pendingShotId; void saveDraft().then((saved) => { if (saved) onSelectShot(next); }); }} type="button">保存并切换</button>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}

function InlineAsset({ asset, entityMap }: { asset: Asset; entityMap: Map<string, string> }) {
  return (
    <span className="director-inline-asset">
      <AssetThumbnail asset={asset} />
      {assetLabel(asset, entityMap)}
      <small>{variantLabel(asset.variant_key)}</small>
    </span>
  );
}

function AssetThumbnail({ asset }: { asset: Asset }) {
  return asset.uri.startsWith("http") || asset.uri.startsWith("data:")
    ? <img alt="" src={asset.uri} />
    : <span className="director-media-placeholder">{asset.entity_type?.slice(0, 1).toUpperCase() ?? "A"}</span>;
}

function MediaThumbnail({ media }: { media: Asset | AssetCandidate | null }) {
  if (!media) return <span className="director-media-placeholder">＋</span>;
  if (media.asset_type === "image" && (media.uri.startsWith("http") || media.uri.startsWith("data:"))) {
    return <img alt="" src={media.uri} />;
  }
  if (media.asset_type === "video" && media.uri.startsWith("http")) {
    return (
      <video
        aria-label="视频缩略图"
        muted
        playsInline
        preload="metadata"
        src={media.uri}
      />
    );
  }
  return <span className={`director-media-placeholder ${media.asset_type}`}>{media.asset_type === "video" ? "▶" : "IMG"}</span>;
}

function MediaPreview({ media }: { media: Asset | AssetCandidate }) {
  if (media.asset_type === "video" && media.uri.startsWith("http")) {
    return <video controls preload="metadata" src={media.uri}><track kind="captions" /></video>;
  }
  if (media.asset_type === "image" && (media.uri.startsWith("http") || media.uri.startsWith("data:"))) {
    return <img alt="当前片段预览" src={media.uri} />;
  }
  return <div className="director-empty">当前媒体只有记录，没有可预览文件。</div>;
}

function resolveReferenceAssets(shot: Shot, allImages: Asset[], adoptedImages: Asset[], explicitIds: string[]) {
  if (Object.prototype.hasOwnProperty.call(shot.shot_card, "reference_asset_ids")) {
    return explicitIds
      .map((id) => allImages.find((asset) => asset.id === id))
      .filter((asset): asset is Asset => Boolean(asset && isDefaultVideoImageAsset(asset)));
  }
  const characterIds = new Set(shot.character_ids.map(String));
  return adoptedImages.filter((asset) => (
    (asset.entity_type === "scene" && asset.entity_id === shot.scene_id)
    || (asset.entity_type === "character" && characterIds.has(asset.entity_id ?? ""))
  ));
}

function isDefaultVideoImageAsset(asset: Asset) {
  if (asset.entity_type === "prop") return false;
  if (asset.entity_type === "character") return (asset.variant_key ?? "base") === "base";
  return true;
}

function shotThumbnail(shot: Shot, assets: Asset[]) {
  return assets.find((asset) => (
    asset.asset_type === "image"
    && asset.asset_role === "shot_storyboard"
    && asset.entity_id === shot.id
    && asset.is_selected
  )) ?? assets.find((asset) => (
    asset.asset_type === "video"
    && asset.entity_type === "shot"
    && asset.entity_id === shot.id
    && asset.is_selected
  )) ?? null;
}

function shotStatus(shot: Shot, assets: Asset[], candidates: AssetCandidate[]) {
  const pending = candidates.some((candidate) => candidate.entity_id === shot.id && candidate.status === "pending_review");
  const video = assets.find((asset) => asset.entity_id === shot.id && asset.asset_type === "video" && asset.is_selected);
  const image = assets.find((asset) => asset.entity_id === shot.id && asset.asset_type === "image" && asset.is_selected);
  if (shot.status === "failed") return { label: "失败", tone: "error" };
  if (shot.shot_card.prompt_stale) return { label: "Prompt 过期", tone: "warning" };
  if (pending) return { label: "候选待采用", tone: "warning" };
  if (video) return { label: `视频 v${video.version}`, tone: "ready" };
  if (image) return { label: `首帧 v${image.version}`, tone: "image" };
  return { label: "未生成", tone: "empty" };
}

function buildEntityMap(entities: EntityBundle): Map<string, string> {
  return new Map([
    ...entities.characters.map((item) => [`character:${item.id}`, item.name] as const),
    ...entities.scenes.map((item) => [`scene:${item.id}`, item.name] as const),
    ...entities.props.map((item) => [`prop:${item.id}`, item.name] as const),
  ]);
}

function assetLabel(asset: Asset, entityMap: Map<string, string>) {
  const key = `${asset.entity_type}:${asset.entity_id}`;
  if (entityMap.has(key)) return entityMap.get(key)!;
  if (asset.entity_type === "character") return "角色形象";
  if (asset.entity_type === "scene") return "场景形象";
  if (asset.entity_type === "prop") return "道具形象";
  if (asset.entity_type === "shot") return "片段首帧";
  if (asset.entity_type === "shot_frame") {
    return {
      shot_frame_first: "片段首帧素材",
      shot_frame_key: "片段关键帧",
      shot_frame_last: "片段尾帧素材",
    }[asset.asset_role ?? ""] ?? "片段帧素材";
  }
  return asset.asset_role ?? "素材";
}

function variantLabel(value: string | null) {
  return !value || value === "base" ? "基础形象" : value.replace(/_/g, " ");
}

function formatSeconds(value: number | null) {
  return value === null ? "未知" : String(Number(value.toFixed(1)));
}

function entityTypeLabel(value: string | null) {
  return {
    character: "角色",
    scene: "场景",
    prop: "道具",
  }[value ?? ""] ?? "资产";
}

function referenceToken(asset: Asset, entityMap: Map<string, string>) {
  const name = assetLabel(asset, entityMap).trim().replace(/ /g, "_");
  return `@${entityTypeLabel(asset.entity_type)}_${name}_${asset.variant_key || "base"}`;
}

function compareReferencePickerAssets(left: Asset, right: Asset) {
  const entityOrder = String(left.entity_id ?? "").localeCompare(String(right.entity_id ?? ""));
  if (entityOrder !== 0) return entityOrder;
  const variantOrder = String(left.variant_key ?? "base").localeCompare(String(right.variant_key ?? "base"));
  if (variantOrder !== 0) return variantOrder;
  if (left.is_selected !== right.is_selected) return left.is_selected ? -1 : 1;
  return right.version - left.version;
}

function preferredReferenceAsset(assets: Asset[]) {
  return assets.find((asset) => asset.is_selected)
    ?? [...assets].sort((left, right) => right.version - left.version)[0]
    ?? null;
}

function assetSortPriority(asset: Asset, referenceIds: string[], shotId: string) {
  if (referenceIds.includes(asset.id)) return 0;
  if (asset.entity_type === "character") return 10;
  if (asset.entity_type === "scene") return 20;
  if (asset.entity_type === "prop") return 30;
  if (["shot", "shot_frame"].includes(asset.entity_type ?? "") && asset.entity_id === shotId) return 40;
  return 50;
}

function shotBeats(card: Record<string, unknown>) {
  return (Array.isArray(card.beats) ? card.beats : []).filter(isRecord);
}

function withBeat(card: Record<string, unknown>, index: number, updates: Record<string, unknown>) {
  const beats = shotBeats(card).map((beat) => ({ ...beat }));
  beats[index] = { ...beats[index], ...updates };
  return {
    ...card,
    beats,
    motion_timing: {
      ...(isRecord(card.motion_timing) ? card.motion_timing : {}),
      beats,
    },
  };
}

function cloneCard(card: Record<string, unknown>) {
  return JSON.parse(JSON.stringify(card)) as Record<string, unknown>;
}

function splitList(value: string) {
  return value.split(/[,，、\n]+/).map((item) => item.trim()).filter(Boolean);
}

function readStringList(value: unknown) {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
