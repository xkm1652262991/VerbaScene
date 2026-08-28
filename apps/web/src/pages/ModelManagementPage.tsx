import { useEffect, useMemo, useState, type FormEvent } from "react";

import {
  createAgentConfig,
  listAgentConfigs,
  listProviders,
  listRuntimeProviderConfigs,
  resetRuntimeProviderConfig,
  testProvider,
  updateAgentConfig,
  updateRuntimeProviderConfig,
  type AgentConfig,
  type AgentConfigPayload,
  type ApiKeyMode,
  type ProviderDescriptor,
  type ProviderSlot,
  type ProviderTestResult,
  type RuntimeProviderConfig,
} from "../services/apiClient";

type NavigationTarget = ProviderSlot | "agents";

type RuntimeConfigDraft = {
  provider_name: string;
  model_name: string;
  base_url: string;
  api_key_mode: ApiKeyMode;
  api_key_ref: string;
  api_key: string;
  default_params: Record<string, string | boolean>;
};

type ModelConfigDraft = {
  agent_type: string;
  name: string;
  description: string;
  provider: string;
  model: string;
  system_prompt: string;
  temperature: string;
  max_tokens: string;
  max_iterations: string;
  settings: Record<string, unknown>;
  is_active: boolean;
};

const SLOT_ORDER: ProviderSlot[] = ["llm", "image", "video"];

const SLOT_META: Record<ProviderSlot, { label: string; eyebrow: string; description: string }> = {
  llm: { label: "文本模型", eyebrow: "LLM", description: "剧本改编、实体提取与分镜拆解" },
  image: { label: "图像模型", eyebrow: "IMAGE", description: "角色、场景与分镜图生成" },
  video: { label: "视频模型", eyebrow: "VIDEO", description: "分镜图生视频片段" },
};

const DEFAULT_AGENT_DRAFT: ModelConfigDraft = {
  agent_type: "storyboard_breaker",
  name: "",
  description: "",
  provider: "",
  model: "",
  system_prompt: "",
  temperature: "",
  max_tokens: "",
  max_iterations: "",
  settings: {},
  is_active: true,
};

const EMPTY_RUNTIME_DRAFT: RuntimeConfigDraft = {
  provider_name: "",
  model_name: "",
  base_url: "",
  api_key_mode: "environment",
  api_key_ref: "",
  api_key: "",
  default_params: {},
};

const DEFAULT_KEY_REFS: Record<string, string> = {
  dashscope: "DASHSCOPE_API_KEY",
  dashscope_image: "DASHSCOPE_API_KEY",
  openai_compatible: "LLM_API_KEY",
  gemini: "GEMINI_API_KEY",
  openai_image: "IMAGE_API_KEY",
  custom_image_http: "IMAGE_API_KEY",
  wan2_i2v_api: "WAN_I2V_API_KEY",
  seedance2_api: "ARK_API_KEY",
  minimax_h3_gateway: "MINIMAX_H3_GATEWAY_API_KEY",
};

const PROVIDERS_WITHOUT_BASE_URL = new Set(["mock"]);
const PROVIDER_KEY_POLICY: Record<string, "required" | "optional" | "none"> = {
  mock: "none",
  comfyui_flux: "none",
  qwen_image_musubi: "none",
  ltx23_api: "none",
  minimax_h3_gateway: "optional",
  custom_image_http: "optional",
  openai_image: "optional",
  custom: "optional",
};

const IMAGE_REFERENCE_CAPABILITIES = new Set(["reference_to_image", "reference_payload"]);
const MAX_PROVIDER_TEST_IMAGE_BYTES = 10 * 1024 * 1024;
const SUPPORTED_PROVIDER_TEST_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

export function ModelManagementPage() {
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [runtimeConfigs, setRuntimeConfigs] = useState<RuntimeProviderConfig[]>([]);
  const [agentConfigs, setAgentConfigs] = useState<AgentConfig[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<NavigationTarget>("llm");
  const [runtimeDraft, setRuntimeDraft] = useState<RuntimeConfigDraft>(EMPTY_RUNTIME_DRAFT);
  const [agentDraft, setAgentDraft] = useState<ModelConfigDraft>(DEFAULT_AGENT_DRAFT);
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [agentTypeFilter, setAgentTypeFilter] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadModelWorkspace() {
    setIsLoading(true);
    setError(null);
    try {
      const [nextProviders, nextRuntimeConfigs, nextAgentConfigs] = await Promise.all([
        listProviders(),
        listRuntimeProviderConfigs(),
        listAgentConfigs(),
      ]);
      setProviders(nextProviders);
      setRuntimeConfigs(nextRuntimeConfigs);
      setAgentConfigs(nextAgentConfigs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "模型配置加载失败");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadModelWorkspace();
  }, []);

  const selectedRuntimeConfig = selectedTarget === "agents"
    ? undefined
    : runtimeConfigs.find((config) => config.provider_type === selectedTarget);

  useEffect(() => {
    if (selectedRuntimeConfig) {
      setRuntimeDraft(runtimeDraftFromConfig(selectedRuntimeConfig));
    }
  }, [selectedRuntimeConfig]);

  async function runAction(key: string, action: () => Promise<unknown>, successMessage: string) {
    try {
      setBusyKey(key);
      setError(null);
      setMessage(null);
      await action();
      await loadModelWorkspace();
      setMessage(successMessage);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
      return false;
    } finally {
      setBusyKey(null);
    }
  }

  async function handleSaveRuntimeConfig(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedTarget === "agents") {
      return;
    }
    await runAction(
      `runtime:${selectedTarget}`,
      async () => {
        const saved = await updateRuntimeProviderConfig(selectedTarget, {
          provider_name: runtimeDraft.provider_name.trim(),
          model_name: runtimeDraft.model_name.trim(),
          base_url: nullableText(runtimeDraft.base_url),
          api_key_mode: runtimeDraft.api_key_mode,
          api_key_ref: runtimeDraft.api_key_mode === "environment" ? nullableText(runtimeDraft.api_key_ref) : null,
          api_key: runtimeDraft.api_key_mode === "direct" ? nullableText(runtimeDraft.api_key) : null,
          default_params: runtimeParamsPayload(selectedTarget, runtimeDraft.provider_name, runtimeDraft.default_params),
        });
        if (!("api_key_mode" in saved)) {
          throw new Error("后端版本过旧，未接收密钥配置。请重启后端后重试。");
        }
        if (runtimeDraft.api_key_mode === "direct" && !saved.api_key_configured) {
          throw new Error("配置已返回，但密钥未落盘，请重新输入 API Key。");
        }
      },
      `${SLOT_META[selectedTarget].label}配置已保存并立即生效。`,
    );
  }

  async function handleResetRuntimeConfig() {
    if (selectedTarget === "agents" || !selectedRuntimeConfig || selectedRuntimeConfig.source !== "saved") {
      return;
    }
    if (!window.confirm(`确认让${SLOT_META[selectedTarget].label}恢复为后端环境配置吗？`)) {
      return;
    }
    await runAction(
      `reset-runtime:${selectedTarget}`,
      () => resetRuntimeProviderConfig(selectedTarget),
      `${SLOT_META[selectedTarget].label}已恢复为环境配置。`,
    );
  }

  async function handleSubmitAgentConfig(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = agentPayload(agentDraft);
    const isEditing = Boolean(editingAgentId);
    const succeeded = await runAction(
      isEditing ? `edit-agent:${editingAgentId}` : "create-agent",
      () => {
        if (!editingAgentId) {
          return createAgentConfig(payload);
        }
        const { agent_type: _agentType, ...updates } = payload;
        return updateAgentConfig(editingAgentId, updates);
      },
      isEditing ? "Agent 模型覆盖已更新。" : "Agent 模型覆盖已创建。",
    );
    if (succeeded) {
      cancelAgentEdit();
    }
  }

  async function handleToggleAgentConfig(config: AgentConfig) {
    await runAction(
      `toggle:${config.id}`,
      () => updateAgentConfig(config.id, { is_active: !config.is_active }),
      config.is_active ? "配置已停用。" : "配置已启用。",
    );
  }

  function beginAgentEdit(config: AgentConfig) {
    setSelectedTarget("agents");
    setEditingAgentId(config.id);
    setAgentDraft({
      agent_type: config.agent_type,
      name: config.name,
      description: config.description ?? "",
      provider: config.provider ?? "",
      model: config.model ?? "",
      system_prompt: config.system_prompt ?? "",
      temperature: config.temperature ?? "",
      max_tokens: config.max_tokens?.toString() ?? "",
      max_iterations: config.max_iterations?.toString() ?? "",
      settings: config.settings,
      is_active: config.is_active,
    });
    window.requestAnimationFrame(() => document.getElementById("agent-config-form")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function cancelAgentEdit() {
    setEditingAgentId(null);
    setAgentDraft(DEFAULT_AGENT_DRAFT);
  }

  function handleProviderChange(providerName: string) {
    if (selectedTarget === "agents") {
      return;
    }
    const descriptor = providers.find((provider) => provider.type === selectedTarget && provider.name === providerName);
    const qwenDashScope = providerName === "dashscope_image" && Boolean(descriptor?.model.startsWith("qwen-image-2.0"));
    setRuntimeDraft((current) => {
      const providerDefaults: Record<string, string | boolean> = providerName === "dashscope_image"
        ? {
            size: qwenDashScope ? "1280x720" : "1696x960",
            reference_transport: qwenDashScope ? "hybrid" : "none",
            prompt_extend: true,
            watermark: false,
            supports_references: qwenDashScope,
          }
        : providerName === "wan2_i2v_api"
          ? { protocol: "official", frames: "81", steps: "4", guidance: "1", fps: "16", image_transport: "upload", result_variant: "native" }
          : providerName === "seedance2_api"
            ? { resolution: "480p", generate_audio: true, watermark: false }
          : providerName === "ltx23_api"
            ? { size: "1024x576", frames: "81", steps: "8", guidance: "1", fps: "16", strength: "0.78" }
          : providerName === "minimax_h3_gateway"
            ? { landscape_size: "1024x576", portrait_size: "576x1024", steps: "19" }
          : { supports_references: false };
      return {
        ...current,
        provider_name: providerName,
        model_name: descriptor?.model ?? current.model_name,
        base_url: providerName === selectedRuntimeConfig?.provider_name
          ? current.base_url
          : providerName === "ltx23_api"
            ? "http://127.0.0.1:18109"
            : providerName === "seedance2_api"
              ? "https://ark.cn-beijing.volces.com/api/v3"
            : "",
        api_key_mode: providerName === selectedRuntimeConfig?.provider_name
          ? current.api_key_mode
          : providerName === "minimax_h3_gateway"
            ? "none"
          : (PROVIDER_KEY_POLICY[providerName] ?? "required") === "none"
            ? "none"
            : "environment",
        api_key_ref: providerName === selectedRuntimeConfig?.provider_name ? current.api_key_ref : (DEFAULT_KEY_REFS[providerName] ?? ""),
        api_key: "",
        default_params: providerName === current.provider_name
          ? current.default_params
          : selectedTarget === "video" || providerName === "dashscope_image"
            ? providerDefaults
            : { ...current.default_params, ...providerDefaults },
      };
    });
  }

  const slotProviders = selectedTarget === "agents"
    ? []
    : providers.filter((provider) => provider.type === selectedTarget);
  const selectedRuntimeProvider = selectedTarget === "agents" || !selectedRuntimeConfig
    ? undefined
    : providers.find(
      (provider) => provider.type === selectedTarget && provider.name === selectedRuntimeConfig.provider_name,
    );
  const llmProviders = providers.filter((provider) => provider.type === "llm");
  const agentTypes = useMemo(() => Array.from(new Set(agentConfigs.map((config) => config.agent_type))).sort(), [agentConfigs]);
  const visibleAgentConfigs = agentTypeFilter
    ? agentConfigs.filter((config) => config.agent_type === agentTypeFilter)
    : agentConfigs;
  const readyRuntimeCount = runtimeConfigs.filter((config) => config.configuration_status === "ready").length;

  return (
    <section className="management-page model-management-page">
      <header className="studio-topbar compact asset-library-topbar">
        <div className="studio-title-area">
          <a className="icon-link" href="#/projects" aria-label="返回项目列表">←</a>
          <div>
            <div className="studio-kicker">Model Control Room</div>
            <h1>模型管理</h1>
            <div className="studio-meta">
              <span>{readyRuntimeCount}/4 运行槽位就绪</span>
              <span>{providers.length} 个适配器</span>
              <span>{agentConfigs.filter((config) => config.is_active).length} 个 Agent 覆盖启用</span>
            </div>
          </div>
        </div>
        <div className="studio-topbar-actions">
        </div>
      </header>

      {message ? <div className="state-box success" role="status">{message}</div> : null}
      {error ? <div className="state-box error" role="alert">{error}</div> : null}
      {isLoading ? <div className="state-box">正在读取运行时模型配置...</div> : null}

      {!isLoading ? (
        <div className="management-layout model-settings-layout">
          <aside className="management-sidebar model-settings-sidebar">
            <div className="model-sidebar-heading">
              <strong>运行模型</strong>
              <span>保存后立即刷新当前进程</span>
            </div>
            {SLOT_ORDER.map((slot) => {
              const config = runtimeConfigs.find((item) => item.provider_type === slot);
              return (
                <button className={selectedTarget === slot ? "active" : ""} key={slot} onClick={() => setSelectedTarget(slot)} type="button">
                  <span className={`model-status-dot ${config?.configuration_status ?? "incomplete"}`} aria-hidden="true" />
                  <span>
                    <strong>{SLOT_META[slot].label}</strong>
                    <small>{config ? `${config.provider_name} · ${config.model_name}` : "尚未加载"}</small>
                  </span>
                </button>
              );
            })}
            <div className="model-sidebar-divider" />
            <div className="model-sidebar-heading">
              <strong>局部覆盖</strong>
              <span>仅影响指定 Agent</span>
            </div>
            <button className={selectedTarget === "agents" ? "active" : ""} onClick={() => setSelectedTarget("agents")} type="button">
              <span className="model-status-dot ready" aria-hidden="true" />
              <span>
                <strong>Agent 模型覆盖</strong>
                <small>{agentConfigs.filter((config) => config.is_active).length} 个启用中</small>
              </span>
            </button>
            <p className="model-sidebar-note">运行槽位是全局默认；Agent 覆盖只用于文本生产节点，优先级更高。</p>
          </aside>

          <main className="management-main model-settings-main">
            {selectedTarget !== "agents" && selectedRuntimeConfig ? (
              <>
                <RuntimeConfigPanel
                  busy={busyKey !== null}
                  config={selectedRuntimeConfig}
                  draft={runtimeDraft}
                  onDraftChange={setRuntimeDraft}
                  onProviderChange={handleProviderChange}
                  onReset={() => void handleResetRuntimeConfig()}
                  onSubmit={(event) => void handleSaveRuntimeConfig(event)}
                  providers={slotProviders}
                  slot={selectedTarget}
                />
                {selectedTarget === "video" && ["seedance2_api", "ltx23_api", "minimax_h3_gateway", "wan2_i2v_api"].includes(selectedRuntimeConfig.provider_name) ? (
                  <VideoProviderTestPanel
                    config={selectedRuntimeConfig}
                    key={selectedRuntimeConfig.updated_at ?? selectedRuntimeConfig.id ?? selectedRuntimeConfig.provider_name}
                  />
                ) : null}
                {selectedTarget === "image" && selectedRuntimeProvider ? (
                  <ImageProviderTestPanel
                    config={selectedRuntimeConfig}
                    descriptor={selectedRuntimeProvider}
                    key={selectedRuntimeConfig.updated_at ?? selectedRuntimeConfig.id ?? selectedRuntimeConfig.provider_name}
                  />
                ) : null}
              </>
            ) : null}

            {selectedTarget === "agents" ? (
              <AgentConfigPanel
                agentTypes={agentTypes}
                busy={busyKey !== null}
                configs={visibleAgentConfigs}
                draft={agentDraft}
                editingId={editingAgentId}
                filter={agentTypeFilter}
                llmProviders={llmProviders}
                onCancelEdit={cancelAgentEdit}
                onDraftChange={setAgentDraft}
                onEdit={beginAgentEdit}
                onFilterChange={setAgentTypeFilter}
                onSubmit={(event) => void handleSubmitAgentConfig(event)}
                onToggle={(config) => void handleToggleAgentConfig(config)}
              />
            ) : null}
          </main>
        </div>
      ) : null}
    </section>
  );
}

type RuntimePanelProps = {
  slot: ProviderSlot;
  config: RuntimeProviderConfig;
  draft: RuntimeConfigDraft;
  providers: ProviderDescriptor[];
  busy: boolean;
  onDraftChange: (updater: (current: RuntimeConfigDraft) => RuntimeConfigDraft) => void;
  onProviderChange: (providerName: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onReset: () => void;
};

function RuntimeConfigPanel({ slot, config, draft, providers, busy, onDraftChange, onProviderChange, onSubmit, onReset }: RuntimePanelProps) {
  const meta = SLOT_META[slot];
  const needsBaseUrl = !PROVIDERS_WITHOUT_BASE_URL.has(draft.provider_name);
  const keyPolicy = PROVIDER_KEY_POLICY[draft.provider_name] ?? "required";
  const needsApiKey = keyPolicy !== "none";
  const isCurrentProvider = draft.provider_name === config.provider_name;
  const savedDirectSecret = isCurrentProvider && config.api_key_mode === "direct" && config.api_key_configured;
  const savedEnvironmentSecret = isCurrentProvider && config.api_key_mode === "environment" && config.api_key_configured;
  const draftProvider = providers.find((provider) => provider.name === draft.provider_name);

  return (
    <section className="management-card runtime-config-card">
      <div className="model-config-header">
        <div>
          <span>{meta.eyebrow} RUNTIME SLOT</span>
          <h2>{meta.label}</h2>
          <p>{meta.description}</p>
        </div>
        <div className={`model-readiness ${config.configuration_status}`}>
          <span>{config.configuration_status === "ready" ? "配置就绪" : "配置不完整"}</span>
          <small>{config.source === "saved" ? "前端保存配置" : "后端环境配置"}</small>
        </div>
      </div>

      {config.validation_error ? (
        <div className="model-validation-warning">
          <strong>当前配置无法正常调用</strong>
          <span>{config.validation_error}</span>
        </div>
      ) : (
        <div className="model-runtime-notice">
          <strong>当前已生效</strong>
          <span>新任务会使用 {config.provider_name} / {config.model_name}，历史任务和资产不会被改写。</span>
        </div>
      )}

      <form className="model-runtime-form" onSubmit={onSubmit}>
        <fieldset disabled={busy}>
          <legend>基础配置</legend>
          <div className="model-form-grid">
            <label>
              Provider
              <select required value={draft.provider_name} onChange={(event) => onProviderChange(event.target.value)}>
                {providers.map((provider) => (
                  <option key={provider.name} value={provider.name}>{provider.name}</option>
                ))}
              </select>
              <small>选择已经接入后端 Provider Adapter 的服务。</small>
            </label>
            <label>
              模型名称
              <input required value={draft.model_name} onChange={(event) => onDraftChange((current) => ({ ...current, model_name: event.target.value }))} />
              <small>{draft.provider_name === "minimax_h3_gateway"
                ? "推荐填写 auto：首帧走 FL2VA，角色/场景参考走 R2V；也可固定填写一个实际模型 ID。"
                : "必须与上游服务实际暴露的 model ID 一致。"}</small>
            </label>
            {needsBaseUrl ? (
              <label className="model-form-span">
                服务地址
                <input placeholder={draft.provider_name === "comfyui_flux" ? "多个地址用逗号分隔" : draft.provider_name === "minimax_h3_gateway" ? "http://gateway-host:18289/v1" : "https://api.example.com/v1"} value={draft.base_url} onChange={(event) => onDraftChange((current) => ({ ...current, base_url: event.target.value }))} />
                <small>留空时沿用后端环境配置；保存的地址会立即用于新请求。</small>
              </label>
            ) : null}
            {needsApiKey ? (
              <>
                <label>
                  密钥来源
                  <select value={draft.api_key_mode} onChange={(event) => onDraftChange((current) => ({
                    ...current,
                    api_key_mode: event.target.value as ApiKeyMode,
                    api_key: "",
                  }))}>
                    <option value="direct">直接输入密钥</option>
                    <option value="environment">引用环境变量</option>
                    {keyPolicy === "optional" ? <option value="none">不使用密钥</option> : null}
                  </select>
                  <small>直接输入会保存到后端本机私有文件；接口永不回显明文。</small>
                </label>
                {draft.api_key_mode === "direct" ? (
                  <label>
                    API Key
                    <div className="secret-ref-field">
                      <input
                        autoComplete="new-password"
                        placeholder={savedDirectSecret ? "已保存；留空保持原密钥" : "输入实际 API Key"}
                        type="password"
                        value={draft.api_key}
                        onChange={(event) => onDraftChange((current) => ({ ...current, api_key: event.target.value }))}
                      />
                      <span className={draft.api_key.trim() || savedDirectSecret ? "ready" : "incomplete"}>
                        {draft.api_key.trim() ? "待保存" : savedDirectSecret ? "已保存" : "需要输入"}
                      </span>
                    </div>
                    <small>保存后输入框会清空；再次留空保存不会覆盖原密钥。</small>
                  </label>
                ) : draft.api_key_mode === "environment" ? (
                  <label>
                    API Key 环境变量
                    <div className="secret-ref-field">
                      <input placeholder={DEFAULT_KEY_REFS[draft.provider_name] ?? "PROVIDER_API_KEY"} value={draft.api_key_ref} onChange={(event) => onDraftChange((current) => ({ ...current, api_key_ref: event.target.value }))} />
                      <span className={savedEnvironmentSecret ? "ready" : keyPolicy === "optional" ? "optional" : "incomplete"}>
                        {savedEnvironmentSecret ? "已检测到密钥" : keyPolicy === "optional" ? "未配置（可选）" : "未检测到密钥"}
                      </span>
                    </div>
                    <small>这里只保存变量名；实际密钥必须存在于后端进程环境中。</small>
                  </label>
                ) : (
                  <div className="model-inline-note">该配置不会发送 Authorization 密钥。</div>
                )}
              </>
            ) : (
              <div className="model-inline-note model-form-span">该 Provider 不需要 API Key。</div>
            )}
          </div>
        </fieldset>

        <RuntimeParameterFields
          draft={draft}
          onDraftChange={onDraftChange}
          providerCapabilities={draftProvider?.capabilities ?? []}
          providerName={draft.provider_name}
          slot={slot}
        />

        <div className="model-form-actions">
          <button className="primary-button" disabled={busy} type="submit">{busy ? "正在应用..." : "保存并立即应用"}</button>
          {config.source === "saved" ? <button className="secondary-button" disabled={busy} onClick={onReset} type="button">恢复环境配置</button> : null}
          <span>无需重启后端；只影响保存后创建的新任务。</span>
        </div>
      </form>
    </section>
  );
}

function RuntimeParameterFields({
  slot,
  providerName,
  providerCapabilities,
  draft,
  onDraftChange,
}: Pick<RuntimePanelProps, "slot" | "draft" | "onDraftChange"> & { providerName: string; providerCapabilities: string[] }) {
  const setParam = (name: string, value: string | boolean) => onDraftChange((current) => ({
    ...current,
    default_params: { ...current.default_params, [name]: value },
  }));
  const value = (name: string) => draft.default_params[name] ?? "";

  if (slot === "llm") {
    return <div className="model-inline-note">文本生成温度和最大 Token 建议在下方 Agent 覆盖中按节点配置，避免一个全局值影响所有生产阶段。</div>;
  }

  if (slot === "image") {
    const configurableReferenceCapability = providerName === "openai_image" || providerName === "custom_image_http";
    const nativeReferenceCapability = providerCapabilities.some((capability) => IMAGE_REFERENCE_CAPABILITIES.has(capability));
    const lockReferenceTransport = providerName === "dashscope_image" && !nativeReferenceCapability;
    return (
      <fieldset disabled={false}>
        <legend>图像生成默认值</legend>
        <div className="model-form-grid four-columns">
          <ParameterInput label="画布尺寸" name="size" onChange={setParam} value={value("size")} />
          {providerName !== "dashscope_image" ? (
            <>
              <label>
                生成质量
                <select value={String(value("quality") || "medium")} onChange={(event) => setParam("quality", event.target.value)}>
                  <option value="low">low · 20 steps</option>
                  <option value="medium">medium · 35 steps</option>
                  <option value="high">high · 50 steps</option>
                  <option value="auto">auto · 50 steps</option>
                </select>
                <small>未填自定义 Steps 时由 quality 决定扩散步数。</small>
              </label>
              <ParameterInput label="自定义 Steps（可选）" min="1" name="steps" onChange={setParam} type="number" value={value("steps")} />
              <ParameterInput label="引导强度" min="0" name="guidance_scale" onChange={setParam} step="0.1" type="number" value={value("guidance_scale")} />
            </>
          ) : null}
          <label>
            参考图传输
            <select disabled={lockReferenceTransport} value={String(value("reference_transport") || (nativeReferenceCapability ? "hybrid" : providerName === "dashscope_image" ? "none" : "hybrid"))} onChange={(event) => setParam("reference_transport", event.target.value)}>
              <option value="hybrid">hybrid · Prompt + 图片</option>
              <option value="payload">payload · 只传图片</option>
              <option value="prompt">prompt · 仅文字降级</option>
              <option value="none">none · 不使用参考资产</option>
            </select>
            <small>要让分镜图和帧图真正消费资产图，请选择 hybrid 或 payload。</small>
          </label>
          {providerName === "custom_image_http" ? (
            <ParameterInput label="接口路径" name="endpoint" onChange={setParam} value={value("endpoint")} />
          ) : null}
          {configurableReferenceCapability ? (
            <label className="reference-capability-control model-form-span">
              <span className="checkbox-row">
                <input checked={Boolean(value("supports_references"))} onChange={(event) => setParam("supports_references", event.target.checked)} type="checkbox" />
                声明上游接口支持参考图
              </span>
              <small>保存只会开启本项目的参考图传输。只有上游 API 确实接收并使用 references/参考图字节时才启用，并应结合下方调用测试和上游日志确认。</small>
            </label>
          ) : nativeReferenceCapability ? (
            <div className="model-inline-note model-form-span">该 Provider 原生声明参考图能力；分镜图会使用角色/场景/道具资产，帧图会继续使用分镜图和实体资产。</div>
          ) : (
            <div className="model-inline-note model-form-span">该 Provider 当前仅声明文生图能力。涉及角色参考资产的生成请求会提示缺少参考图能力。</div>
          )}
          {providerName === "dashscope_image" ? (
            <>
              <label className="checkbox-row compact-checkbox">
                <input checked={Boolean(value("prompt_extend"))} onChange={(event) => setParam("prompt_extend", event.target.checked)} type="checkbox" />
                启用百炼 Prompt 智能改写
              </label>
              <label className="checkbox-row compact-checkbox">
                <input checked={Boolean(value("watermark"))} onChange={(event) => setParam("watermark", event.target.checked)} type="checkbox" />
                添加百炼水印
              </label>
              <div className="model-inline-note model-form-span">wan2.6 的 16:9 推荐尺寸是 1696×960；返回的短效图片 URL 会由后端立即下载并转存。</div>
            </>
          ) : null}
        </div>
      </fieldset>
    );
  }

  if (slot === "video") {
    const protocol = String(value("protocol") || "official");
    const isWanOfficial = providerName === "wan2_i2v_api" && protocol === "official";
    if (providerName === "seedance2_api") {
      return (
        <fieldset disabled={false}>
          <legend>Seedance 2.0 默认值</legend>
          <div className="model-form-grid four-columns">
            <label>
              输出分辨率
              <select value={String(value("resolution") || "480p")} onChange={(event) => setParam("resolution", event.target.value)}>
                <option value="480p">480p</option>
                <option value="720p">720p</option>
                <option value="1080p">1080p</option>
              </select>
            </label>
            <label className="checkbox-row compact-checkbox">
              <input checked={Boolean(value("generate_audio"))} onChange={(event) => setParam("generate_audio", event.target.checked)} type="checkbox" />
              生成原生对白与音效
            </label>
            <label className="checkbox-row compact-checkbox">
              <input checked={Boolean(value("watermark"))} onChange={(event) => setParam("watermark", event.target.checked)} type="checkbox" />
              添加平台水印
            </label>
            <div className="model-inline-note model-form-span">
              默认使用 Seedance 智能时长，由模型在 4–15 秒内选择实际长度；固定秒数仅作为生成时的人工覆盖。支持图片、视频、音频混合参考。
            </div>
          </div>
        </fieldset>
      );
    }
    if (providerName === "minimax_h3_gateway") {
      return (
        <fieldset disabled={false}>
          <legend>MiniMax H3 默认值</legend>
          <div className="model-form-grid four-columns">
            <ParameterInput label="横版生成尺寸" name="landscape_size" onChange={setParam} value={value("landscape_size")} />
            <ParameterInput label="竖版生成尺寸" name="portrait_size" onChange={setParam} value={value("portrait_size")} />
            <ParameterInput label="采样步数" min="1" name="steps" onChange={setParam} type="number" value={value("steps")} />
            <ParameterInput label="固定 Seed（留空随机）" name="seed" onChange={setParam} type="number" value={value("seed")} />
            <div className="model-inline-note model-form-span">
              auto 会按素材语义路由：明确首帧使用 FL2VA，角色/场景参考使用 R2V，无参考图使用 FL2VA。GPU 在网关内严格串行，queued 表示正常排队。
            </div>
          </div>
        </fieldset>
      );
    }
    return (
      <fieldset disabled={false}>
        <legend>视频生成默认值</legend>
        <div className="model-form-grid four-columns">
          {providerName === "wan2_i2v_api" ? (
            <label>
              接口协议
              <select value={protocol} onChange={(event) => setParam("protocol", event.target.value)}>
                <option value="official">official</option>
                <option value="legacy">legacy</option>
                <option value="local">local</option>
                <option value="persistent">persistent</option>
              </select>
            </label>
          ) : null}
          <ParameterInput label="画布尺寸" name="size" onChange={setParam} value={value("size")} />
          <ParameterInput label="帧数" min="1" name="frames" onChange={setParam} type="number" value={value("frames")} />
          <ParameterInput disabled={isWanOfficial} label="采样步数" min="1" name="steps" onChange={setParam} type="number" value={isWanOfficial ? "4" : value("steps")} />
          <ParameterInput disabled={isWanOfficial} label={providerName === "ltx23_api" ? "CFG" : "引导强度"} min="0" name="guidance" onChange={setParam} step="0.1" type="number" value={isWanOfficial ? "1" : value("guidance")} />
          <ParameterInput label="帧率 FPS" min="1" name="fps" onChange={setParam} type="number" value={value("fps")} />
          <ParameterInput label="固定 Seed（留空由服务决定）" name="seed" onChange={setParam} type="number" value={value("seed")} />
          {providerName === "ltx23_api" ? (
            <>
              <ParameterInput label="首帧保持强度" min="0" name="strength" onChange={setParam} step="0.01" type="number" value={value("strength")} />
              <div className="model-inline-note model-form-span">LTX-2.3 使用 18109 队列 API；有首帧时走图生视频，无参考图时走文生视频。</div>
            </>
          ) : null}
          {providerName === "wan2_i2v_api" ? (
            <>
              <label>
                参考图传输
                <select value={String(value("image_transport") || "auto")} onChange={(event) => setParam("image_transport", event.target.value)}>
                  <option value="auto">auto</option>
                  <option value="upload">upload</option>
                  <option value="url">url</option>
                </select>
              </label>
              <label>
                返回版本
                <select value={String(value("result_variant") || "native")} onChange={(event) => setParam("result_variant", event.target.value)}>
                  <option value="native">native</option>
                  <option value="1080p">1080p</option>
                </select>
              </label>
              {protocol === "local" ? (
                <>
                  <ParameterInput label="运行模式" name="mode" onChange={setParam} value={value("mode")} />
                  <ParameterInput label="GPU 设备" name="gpu_devices" onChange={setParam} value={value("gpu_devices")} />
                </>
              ) : null}
              {protocol === "legacy" ? (
                <>
                  <ParameterInput label="最大像素面积" min="1" name="max_area" onChange={setParam} type="number" value={value("max_area")} />
                  <label className="checkbox-row compact-checkbox">
                    <input checked={Boolean(value("upscale_1080p"))} onChange={(event) => setParam("upscale_1080p", event.target.checked)} type="checkbox" />
                    输出后升到 1080p
                  </label>
                </>
              ) : null}
              {protocol === "official" ? (
                <div className="model-inline-note model-form-span">18083 official 服务固定 steps=4、guidance=1；frames 必须满足 4n+1（如 17、49、81）。</div>
              ) : null}
            </>
          ) : null}
        </div>
      </fieldset>
    );
  }

  return (
    <fieldset disabled={false}>
      <legend>语音生成默认值</legend>
      <div className="model-form-grid four-columns">
        <ParameterInput label="接口路径" name="endpoint" onChange={setParam} value={value("endpoint")} />
        <ParameterInput label="默认音色" name="voice" onChange={setParam} value={value("voice")} />
        <ParameterInput label="语言" name="language" onChange={setParam} value={value("language")} />
        <label>
          音频格式
          <select value={String(value("response_format"))} onChange={(event) => setParam("response_format", event.target.value)}>
            <option value="wav">wav</option>
            <option value="mp3">mp3</option>
            <option value="pcm">pcm</option>
            <option value="opus">opus</option>
          </select>
        </label>
      </div>
    </fieldset>
  );
}

type ImageProviderTestDraft = {
  prompt: string;
  negative_prompt: string;
  reference: string;
  reference_name: string;
  size: string;
  quality: string;
  steps: string;
  guidance_scale: string;
  seed: string;
};

function ImageProviderTestPanel({ config, descriptor }: { config: RuntimeProviderConfig; descriptor: ProviderDescriptor }) {
  const [draft, setDraft] = useState<ImageProviderTestDraft>(() => imageProviderTestDraft(config));
  const [busy, setBusy] = useState(false);
  const [readingReference, setReadingReference] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const supportsReferences = descriptor.capabilities.some((capability) => IMAGE_REFERENCE_CAPABILITIES.has(capability));
  const referenceTransport = String(config.default_params.reference_transport ?? "hybrid").toLowerCase();
  const sendsReferencePayload = referenceTransport === "payload" || referenceTransport === "hybrid";
  const canUploadReference = supportsReferences && sendsReferencePayload;

  const setField = (name: keyof ImageProviderTestDraft, value: string) => {
    setDraft((current) => ({ ...current, [name]: value }));
  };

  async function handleReferenceFile(file: File | undefined) {
    if (!file) {
      return;
    }
    setError(null);
    setResult(null);
    if (!SUPPORTED_PROVIDER_TEST_IMAGE_TYPES.has(file.type)) {
      setError("参考图仅支持 PNG、JPEG 或 WebP。");
      return;
    }
    if (file.size > MAX_PROVIDER_TEST_IMAGE_BYTES) {
      setError("参考图不能超过 10 MB。");
      return;
    }
    setReadingReference(true);
    try {
      const reference = await readFileAsDataUrl(file);
      setDraft((current) => ({ ...current, reference, reference_name: file.name }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "参考图读取失败");
    } finally {
      setReadingReference(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const params: Record<string, unknown> = {
        size: draft.size.trim(),
        quality: draft.quality,
        asset_reference_transport: referenceTransport,
        asset_reference_metadata: {
          reference_mode: draft.reference ? "provider_test_upload" : "none",
        },
      };
      if (draft.steps.trim()) {
        params.steps = requiredFiniteNumber(draft.steps, "采样步数");
      }
      if (draft.guidance_scale.trim()) {
        params.guidance_scale = requiredFiniteNumber(draft.guidance_scale, "引导强度");
      }
      if (draft.seed.trim()) {
        params.seed = requiredFiniteNumber(draft.seed, "Seed");
      }
      const response = await testProvider({
        provider_type: "image",
        provider_name: config.provider_name,
        model: config.model_name,
        prompt: draft.prompt.trim(),
        negative_prompt: nullableText(draft.negative_prompt),
        references: draft.reference ? [draft.reference] : [],
        params,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "图片 Provider 测试失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="management-card runtime-config-card provider-test-card">
      <div className="model-config-header">
        <div>
          <span>IMAGE REFERENCE TEST</span>
          <h2>图片参考链路测试</h2>
          <p>验证本项目会把上传图片真实放进上游请求；模型是否消费图片还需结合上游接口契约、日志和生成结果确认。</p>
        </div>
        <div className={`model-readiness ${supportsReferences ? "ready" : "incomplete"}`}>
          <span>{supportsReferences ? "已声明参考图能力" : "仅文生图"}</span>
          <small>{config.provider_name} · {config.model_name}</small>
        </div>
      </div>
      <div className="model-inline-note provider-test-boundary">
        测试图只保存在当前浏览器内存，并随这一次请求发送；不会写入模型配置，也不会替换项目里的角色资产。
      </div>
      {!supportsReferences ? (
        <div className="state-box error provider-test-state reference-test-warning" role="alert">
          当前适配器没有 reference_to_image/reference_payload 能力。涉及角色资产的生成请求会提示缺少参考图能力。
        </div>
      ) : !sendsReferencePayload ? (
        <div className="state-box error provider-test-state reference-test-warning" role="alert">
          当前“参考图传输”为 {referenceTransport}，不会发送图片字节。请在上方改为 hybrid 或 payload 并保存。
        </div>
      ) : null}
      <form className="model-runtime-form provider-test-form" onSubmit={(event) => void handleSubmit(event)}>
        <fieldset disabled={busy || readingReference}>
          <legend>本次输入</legend>
          <div className="model-form-grid">
            <label className="model-form-span">
              Prompt
              <textarea required rows={4} value={draft.prompt} onChange={(event) => setField("prompt", event.target.value)} />
            </label>
            <label className="model-form-span">
              Negative Prompt
              <textarea rows={3} value={draft.negative_prompt} onChange={(event) => setField("negative_prompt", event.target.value)} />
            </label>
            <div className="model-form-span provider-reference-upload">
              <div>
                <strong>参考图</strong>
                <span>{draft.reference_name || (canUploadReference ? "未选择" : "当前配置不可上传")}</span>
              </div>
              <label className={`secondary-button upload-button ${canUploadReference ? "" : "disabled"}`}>
                {readingReference ? "正在读取..." : draft.reference ? "更换参考图" : "上传参考图"}
                <input
                  accept="image/png,image/jpeg,image/webp"
                  disabled={!canUploadReference || busy || readingReference}
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    event.currentTarget.value = "";
                    void handleReferenceFile(file);
                  }}
                  type="file"
                />
              </label>
              {draft.reference ? (
                <button
                  className="secondary-button"
                  disabled={busy || readingReference}
                  onClick={() => setDraft((current) => ({ ...current, reference: "", reference_name: "" }))}
                  type="button"
                >
                  移除
                </button>
              ) : null}
              {draft.reference ? <img alt="本次测试参考图预览" src={draft.reference} /> : null}
            </div>
          </div>
        </fieldset>
        <fieldset disabled={busy || readingReference}>
          <legend>本次参数</legend>
          <div className="model-form-grid four-columns">
            <ParameterInput label="画布尺寸" name="size" onChange={(_, value) => setField("size", value)} value={draft.size} />
            <label>
              生成质量
              <select value={draft.quality} onChange={(event) => setField("quality", event.target.value)}>
                <option value="low">low · 20 steps</option>
                <option value="medium">medium · 35 steps</option>
                <option value="high">high · 50 steps</option>
                <option value="auto">auto · 50 steps</option>
              </select>
            </label>
            <ParameterInput label="自定义 Steps（可选）" min="1" name="steps" onChange={(_, value) => setField("steps", value)} type="number" value={draft.steps} />
            <ParameterInput label="引导强度（可选）" min="0" name="guidance_scale" onChange={(_, value) => setField("guidance_scale", value)} step="0.1" type="number" value={draft.guidance_scale} />
            <ParameterInput label="Seed（可选）" name="seed" onChange={(_, value) => setField("seed", value)} type="number" value={draft.seed} />
          </div>
        </fieldset>
        <div className="model-form-actions">
          <button className="primary-button" disabled={busy || readingReference} type="submit">{busy ? "生成中，请等待..." : "发起图片链路测试"}</button>
          <span>{draft.reference ? "本次请求将携带 1 张参考图。" : "未上传时按纯文生图测试。"}</span>
        </div>
      </form>

      {error ? <div className="state-box error provider-test-state" role="alert">{error}</div> : null}
      {result ? (
        <div className={`provider-test-result ${result.error ? "failed" : "succeeded"}`}>
          <div className="provider-test-summary">
            <strong>{result.error ? "调用失败" : "调用成功"}</strong>
            <span>Status: {result.status}</span>
            <span>Provider Task: {result.provider_task_id}</span>
          </div>
          {result.error ? <p>{result.error.error_message}</p> : null}
          {result.assets.length ? (
            <div className="provider-test-assets">
              {result.assets.map((asset, index) => (
                <article key={`${asset.uri}:${index}`}>
                  {asset.asset_type === "image" && (asset.uri.startsWith("http") || asset.uri.startsWith("data:image/")) ? (
                    <img alt={`图片测试结果 ${index + 1}`} src={asset.uri} />
                  ) : null}
                  <a href={asset.uri} rel="noreferrer" target="_blank">打开生成结果</a>
                </article>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function imageProviderTestDraft(config: RuntimeProviderConfig): ImageProviderTestDraft {
  const param = (name: string, fallback = "") => {
    const value = config.default_params[name];
    return value === null || value === undefined ? fallback : String(value);
  };
  return {
    prompt: "",
    negative_prompt: "",
    reference: "",
    reference_name: "",
    size: param("size", "1280x720"),
    quality: param("quality", "medium"),
    steps: param("steps"),
    guidance_scale: param("guidance_scale"),
    seed: param("seed"),
  };
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("参考图读取失败，请重新选择文件。"));
    reader.onload = () => {
      if (typeof reader.result !== "string" || !reader.result.startsWith("data:image/")) {
        reject(new Error("参考图格式无法识别。"));
        return;
      }
      resolve(reader.result);
    };
    reader.readAsDataURL(file);
  });
}

type VideoProviderTestDraft = {
  prompt: string;
  negative_prompt: string;
  reference: string;
  size: string;
  frames: string;
  steps: string;
  guidance: string;
  strength: string;
  fps: string;
  seed: string;
  duration: string;
  ratio: string;
  resolution: string;
  generate_audio: boolean;
  watermark: boolean;
};

function VideoProviderTestPanel({ config }: { config: RuntimeProviderConfig }) {
  const [draft, setDraft] = useState<VideoProviderTestDraft>(() => videoProviderTestDraft(config));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProviderTestResult | null>(null);

  const setField = (name: keyof VideoProviderTestDraft, value: string) => {
    setDraft((current) => ({ ...current, [name]: value }));
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const params: Record<string, unknown> = config.provider_name === "seedance2_api"
        ? {
            duration: seedanceDuration(draft.duration),
            ratio: draft.ratio,
            resolution: draft.resolution,
            generate_audio: draft.generate_audio,
            watermark: draft.watermark,
          }
        : config.provider_name === "minimax_h3_gateway"
          ? {
              size: draft.size.trim(),
              duration_sec: h3Duration(draft.duration),
              steps: requiredFiniteNumber(draft.steps, "采样步数"),
            }
        : {
            size: draft.size.trim(),
            frames: requiredFiniteNumber(draft.frames, "帧数"),
            steps: requiredFiniteNumber(draft.steps, "采样步数"),
            fps: requiredFiniteNumber(draft.fps, "FPS"),
          };
      if (config.provider_name !== "seedance2_api") {
        if (draft.guidance.trim()) {
          params.guidance = requiredFiniteNumber(draft.guidance, "引导强度");
        }
        if (draft.seed.trim()) {
          params.seed = requiredFiniteNumber(draft.seed, "Seed");
        }
        if (config.provider_name === "ltx23_api" && draft.strength.trim()) {
          params.strength = requiredFiniteNumber(draft.strength, "首帧保持强度");
        }
      }
      const response = await testProvider({
        provider_type: "video",
        provider_name: config.provider_name,
        model: config.model_name,
        prompt: draft.prompt.trim(),
        negative_prompt: nullableText(draft.negative_prompt),
        references: draft.reference.trim() ? [draft.reference.trim()] : [],
        params,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "视频 Provider 测试失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="management-card runtime-config-card provider-test-card">
      <div className="model-config-header">
        <div>
          <span>VIDEO CALL TEST</span>
          <h2>{["ltx23_api", "seedance2_api", "minimax_h3_gateway"].includes(config.provider_name) ? "文生/参考生视频调用测试" : "图生视频调用测试"}</h2>
          <p>对应 POST /api/providers/test，使用当前已生效的服务地址。</p>
        </div>
        <div className="model-readiness ready">
          <span>{config.provider_name}</span>
          <small>{config.model_name}</small>
        </div>
      </div>
      <div className="model-inline-note provider-test-boundary">
        上方保存连接和默认值；下方的 Prompt、首帧图和 Seed 只属于这一次测试，不会写进全局模型配置。
      </div>
      <form className="model-runtime-form provider-test-form" onSubmit={(event) => void handleSubmit(event)}>
        <fieldset disabled={busy}>
          <legend>本次输入</legend>
          <div className="model-form-grid">
            <label className="model-form-span">
              动作 Prompt
              <textarea required rows={4} value={draft.prompt} onChange={(event) => setField("prompt", event.target.value)} />
              <small>描述主体动作和镜头运动，不要重复首帧图的静态外观。</small>
            </label>
            {config.provider_name !== "seedance2_api" ? (
              <label className="model-form-span">
                Negative Prompt
                <textarea rows={3} value={draft.negative_prompt} onChange={(event) => setField("negative_prompt", event.target.value)} />
              </label>
            ) : null}
            <label className="model-form-span">
              {["seedance2_api", "minimax_h3_gateway"].includes(config.provider_name) ? "参考图片 URL（可选）" : "首帧参考图 URL"}
              <input placeholder="https://files.example.com/first-frame.png" required={config.provider_name === "wan2_i2v_api"} type="url" value={draft.reference} onChange={(event) => setField("reference", event.target.value)} />
              <small>{config.provider_name === "seedance2_api"
                ? "留空测试文生视频；填写时按“图片1”传给方舟。这里只测试一张图，制作工作台会发送片段绑定的全部资产。"
                : config.provider_name === "minimax_h3_gateway"
                  ? "auto 模式下：留空走 FL2VA 文生视频；普通参考图走 R2V。制作工作台能识别明确首帧并自动切换 FL2VA。"
                : config.provider_name === "ltx23_api"
                  ? "留空时测试文生视频；填写后由主后端下载并上传首帧，测试图生视频。"
                  : "必须是后端能读取的图片 URL；主后端会先下载再上传。"}</small>
            </label>
          </div>
        </fieldset>
        <fieldset disabled={busy}>
          <legend>本次参数</legend>
          {config.provider_name === "seedance2_api" ? (
            <div className="model-form-grid four-columns">
              <ParameterInput label="时长（-1 智能，或 4–15 秒）" min="-1" name="duration" onChange={(_, value) => setField("duration", value)} type="number" value={draft.duration} />
              <label>
                画幅
                <select value={draft.ratio} onChange={(event) => setField("ratio", event.target.value)}>
                  {["16:9", "9:16", "4:3", "3:4", "1:1", "21:9", "adaptive"].map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}
                </select>
              </label>
              <label>
                分辨率
                <select value={draft.resolution} onChange={(event) => setField("resolution", event.target.value)}>
                  {["480p", "720p", "1080p"].map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}
                </select>
              </label>
              <label className="checkbox-row compact-checkbox">
                <input checked={draft.generate_audio} onChange={(event) => setDraft((current) => ({ ...current, generate_audio: event.target.checked }))} type="checkbox" />
                生成原生音频
              </label>
              <label className="checkbox-row compact-checkbox">
                <input checked={draft.watermark} onChange={(event) => setDraft((current) => ({ ...current, watermark: event.target.checked }))} type="checkbox" />
                添加水印
              </label>
            </div>
          ) : config.provider_name === "minimax_h3_gateway" ? (
            <div className="model-form-grid four-columns">
              <ParameterInput label="画布尺寸" name="size" onChange={(_, value) => setField("size", value)} value={draft.size} />
              <ParameterInput label="时长（0.2–15 秒）" min="0.2" name="duration" onChange={(_, value) => setField("duration", value)} step="0.1" type="number" value={draft.duration} />
              <ParameterInput label="采样步数" min="1" name="steps" onChange={(_, value) => setField("steps", value)} type="number" value={draft.steps} />
              <ParameterInput label="Seed（可选）" name="seed" onChange={(_, value) => setField("seed", value)} type="number" value={draft.seed} />
            </div>
          ) : (
            <div className="model-form-grid four-columns">
              <ParameterInput label="画布尺寸" name="size" onChange={(_, value) => setField("size", value)} value={draft.size} />
              <ParameterInput label="帧数" min="1" name="frames" onChange={(_, value) => setField("frames", value)} type="number" value={draft.frames} />
              <ParameterInput disabled={config.provider_name === "wan2_i2v_api"} label="采样步数" min="1" name="steps" onChange={(_, value) => setField("steps", value)} type="number" value={config.provider_name === "wan2_i2v_api" ? "4" : draft.steps} />
              <ParameterInput label="FPS" min="1" name="fps" onChange={(_, value) => setField("fps", value)} type="number" value={draft.fps} />
              <ParameterInput disabled={config.provider_name === "wan2_i2v_api"} label={config.provider_name === "ltx23_api" ? "CFG" : "引导强度"} min="0" name="guidance" onChange={(_, value) => setField("guidance", value)} step="0.1" type="number" value={config.provider_name === "wan2_i2v_api" ? "1" : draft.guidance} />
              {config.provider_name === "ltx23_api" ? <ParameterInput label="首帧保持强度" min="0" name="strength" onChange={(_, value) => setField("strength", value)} step="0.01" type="number" value={draft.strength} /> : null}
              <ParameterInput label="Seed（可选）" name="seed" onChange={(_, value) => setField("seed", value)} type="number" value={draft.seed} />
            </div>
          )}
        </fieldset>
        <div className="model-form-actions">
          <button className="primary-button" disabled={busy} type="submit">{busy ? "生成中，请等待..." : draft.reference.trim() ? "发起图生视频测试" : "发起文生视频测试"}</button>
          <span>该操作会真实提交生成；异步 Provider 会先返回任务 ID，制作主链路的进度请在任务中心查看。</span>
        </div>
      </form>

      {error ? <div className="state-box error provider-test-state" role="alert">{error}</div> : null}
      {result ? (
        <div className={`provider-test-result ${result.error ? "failed" : "succeeded"}`}>
          <div className="provider-test-summary">
            <strong>{result.error ? "调用失败" : "调用成功"}</strong>
            <span>Status: {result.status}</span>
            <span>Provider Task: {result.provider_task_id}</span>
          </div>
          {result.error ? <p>{result.error.error_message}</p> : null}
          {result.assets.length ? (
            <div className="provider-test-assets">
              {result.assets.map((asset, index) => (
                <article key={`${asset.uri}:${index}`}>
                  {asset.asset_type === "video" && asset.uri.startsWith("http") ? (
                    <video controls preload="metadata" src={asset.uri} />
                  ) : null}
                  <a href={asset.uri} rel="noreferrer" target="_blank">打开生成结果</a>
                </article>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function videoProviderTestDraft(config: RuntimeProviderConfig): VideoProviderTestDraft {
  const param = (name: string, fallback = "") => {
    const value = config.default_params[name];
    return value === null || value === undefined ? fallback : String(value);
  };
  return {
    prompt: "",
    negative_prompt: "",
    reference: "",
    size: config.provider_name === "minimax_h3_gateway"
      ? param("landscape_size", "1024x576")
      : param("size", "1280*720"),
    frames: param("frames", "49"),
    steps: param("steps", config.provider_name === "ltx23_api" ? "8" : config.provider_name === "minimax_h3_gateway" ? "19" : "4"),
    guidance: param("guidance", "1"),
    strength: param("strength", "0.78"),
    fps: param("fps", "16"),
    seed: param("seed"),
    duration: config.provider_name === "minimax_h3_gateway" ? "5" : "-1",
    ratio: "16:9",
    resolution: param("resolution", "480p"),
    generate_audio: Boolean(config.default_params.generate_audio ?? true),
    watermark: Boolean(config.default_params.watermark ?? false),
  };
}

function requiredFiniteNumber(value: string, label: string) {
  const parsed = Number(value);
  if (!value.trim() || !Number.isFinite(parsed)) {
    throw new Error(`${label}必须是有效数字。`);
  }
  return parsed;
}

function seedanceDuration(value: string) {
  const duration = requiredFiniteNumber(value, "时长");
  if (duration !== -1 && (!Number.isInteger(duration) || duration < 4 || duration > 15)) {
    throw new Error("时长必须是 -1（智能）或 4–15 之间的整数秒。");
  }
  return duration;
}

function h3Duration(value: string) {
  const duration = requiredFiniteNumber(value, "时长");
  if (duration < 0.2 || duration > 15) {
    throw new Error("MiniMax H3 时长必须在 0.2–15 秒之间。");
  }
  return duration;
}

type ParameterInputProps = {
  label: string;
  name: string;
  value: string | boolean;
  type?: "text" | "number";
  min?: string;
  step?: string;
  disabled?: boolean;
  onChange: (name: string, value: string) => void;
};

function ParameterInput({ label, name, value, type = "text", min, step, disabled = false, onChange }: ParameterInputProps) {
  return (
    <label>
      {label}
      <input disabled={disabled} min={min} step={step} type={type} value={String(value)} onChange={(event) => onChange(name, event.target.value)} />
    </label>
  );
}

type AgentPanelProps = {
  configs: AgentConfig[];
  draft: ModelConfigDraft;
  llmProviders: ProviderDescriptor[];
  editingId: string | null;
  busy: boolean;
  agentTypes: string[];
  filter: string;
  onDraftChange: (updater: (current: ModelConfigDraft) => ModelConfigDraft) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onEdit: (config: AgentConfig) => void;
  onToggle: (config: AgentConfig) => void;
  onCancelEdit: () => void;
  onFilterChange: (value: string) => void;
};

function AgentConfigPanel({ configs, draft, llmProviders, editingId, busy, agentTypes, filter, onDraftChange, onSubmit, onEdit, onToggle, onCancelEdit, onFilterChange }: AgentPanelProps) {
  return (
    <div className="agent-config-workspace">
      <section className="management-card">
        <div className="asset-owner-head model-section-heading">
          <div>
            <span>Agent Overrides</span>
            <strong>Agent 模型覆盖</strong>
            <p>只覆盖指定文本生产节点；未配置的节点继续使用全局文本模型。</p>
          </div>
          <label className="model-filter-field">
            类型筛选
            <select value={filter} onChange={(event) => onFilterChange(event.target.value)}>
              <option value="">全部 Agent</option>
              {agentTypes.map((agentType) => <option key={agentType} value={agentType}>{agentType}</option>)}
            </select>
          </label>
        </div>
        {configs.length > 0 ? (
          <div className="config-grid model-agent-grid">
            {configs.map((config) => (
              <article className={config.is_active ? "config-card active" : "config-card"} key={config.id}>
                <div className="config-card-head">
                  <div>
                    <span>{config.agent_type}</span>
                    <strong>{config.name}</strong>
                  </div>
                  <em>{config.is_active ? "启用" : "停用"}</em>
                </div>
                {config.description ? <p>{config.description}</p> : null}
                <dl>
                  <div><dt>Provider</dt><dd>{config.provider ?? "全局默认"}</dd></div>
                  <div><dt>Model</dt><dd>{config.model ?? "全局默认"}</dd></div>
                  <div><dt>Temperature</dt><dd>{config.temperature ?? "默认"}</dd></div>
                </dl>
                <div className="config-card-actions">
                  <button className="secondary-button" disabled={busy} onClick={() => onEdit(config)} type="button">编辑</button>
                  <button className="secondary-button" disabled={busy} onClick={() => onToggle(config)} type="button">{config.is_active ? "停用" : "启用"}</button>
                </div>
              </article>
            ))}
          </div>
        ) : <p className="asset-empty-note">当前筛选下暂无 Agent 模型覆盖。</p>}
      </section>

      <section className="management-card" id="agent-config-form">
        <div className="asset-owner-head model-section-heading">
          <div>
            <span>{editingId ? "Edit Override" : "Create Override"}</span>
            <strong>{editingId ? "修改 Agent 模型覆盖" : "新增 Agent 模型覆盖"}</strong>
            <p>{editingId ? "修改会保留同一条配置记录。" : "启用新配置时，同 Agent 类型的旧配置会自动停用。"}</p>
          </div>
          {editingId ? <button className="secondary-button" onClick={onCancelEdit} type="button">取消编辑</button> : null}
        </div>
        <form className="management-form model-agent-form" onSubmit={onSubmit}>
          <div className="model-form-grid">
            <label>
              Agent 类型
              <input disabled={Boolean(editingId)} required value={draft.agent_type} onChange={(event) => setAgentDraftField(onDraftChange, "agent_type", event.target.value)} />
            </label>
            <label>
              配置名称
              <input required value={draft.name} onChange={(event) => setAgentDraftField(onDraftChange, "name", event.target.value)} />
            </label>
            <label>
              Provider
              <select value={draft.provider} onChange={(event) => setAgentDraftField(onDraftChange, "provider", event.target.value)}>
                <option value="">使用全局文本模型</option>
                {llmProviders.map((provider) => <option key={provider.name} value={provider.name}>{provider.name}</option>)}
              </select>
            </label>
            <label>
              Model
              <input placeholder="留空时使用 Provider 默认模型" value={draft.model} onChange={(event) => setAgentDraftField(onDraftChange, "model", event.target.value)} />
            </label>
            <label>
              Temperature
              <input max="2" min="0" step="0.05" type="number" value={draft.temperature} onChange={(event) => setAgentDraftField(onDraftChange, "temperature", event.target.value)} />
            </label>
            <label>
              Max Tokens
              <input min="1" type="number" value={draft.max_tokens} onChange={(event) => setAgentDraftField(onDraftChange, "max_tokens", event.target.value)} />
            </label>
          </div>
          <label>
            描述
            <textarea rows={2} value={draft.description} onChange={(event) => setAgentDraftField(onDraftChange, "description", event.target.value)} />
          </label>
          <label>
            System Prompt（可选）
            <textarea rows={5} value={draft.system_prompt} onChange={(event) => setAgentDraftField(onDraftChange, "system_prompt", event.target.value)} />
          </label>
          <label className="checkbox-row">
            <input checked={draft.is_active} onChange={(event) => onDraftChange((current) => ({ ...current, is_active: event.target.checked }))} type="checkbox" />
            {editingId ? "保存后保持启用" : "创建后立即启用"}
          </label>
          <button className="primary-button" disabled={busy} type="submit">{editingId ? "保存修改" : "创建覆盖"}</button>
        </form>
      </section>
    </div>
  );
}

function runtimeDraftFromConfig(config: RuntimeProviderConfig): RuntimeConfigDraft {
  return {
    provider_name: config.provider_name,
    model_name: config.model_name,
    base_url: config.base_url ?? "",
    api_key_mode: config.api_key_mode ?? (config.api_key_ref ? "environment" : "none"),
    api_key_ref: config.api_key_ref ?? "",
    api_key: "",
    default_params: Object.fromEntries(Object.entries(config.default_params).map(([key, value]) => [key, typeof value === "boolean" ? value : String(value ?? "")])),
  };
}

function runtimeParamsPayload(slot: ProviderSlot, providerName: string, params: RuntimeConfigDraft["default_params"]) {
  const numericKeys = new Set(slot === "image" ? ["steps", "guidance_scale"] : slot === "video" ? ["frames", "steps", "guidance", "fps", "seed", "max_area", "strength"] : []);
  const imageParamKeys = new Set(["size", "quality", "steps", "guidance_scale", "reference_transport"]);
  if (providerName === "custom_image_http") {
    imageParamKeys.add("endpoint");
  }
  if (providerName === "custom_image_http" || providerName === "openai_image") {
    imageParamKeys.add("supports_references");
  }
  if (providerName === "dashscope_image") {
    imageParamKeys.add("prompt_extend");
    imageParamKeys.add("watermark");
  }
  const videoParamKeys = providerName === "ltx23_api"
    ? new Set(["size", "frames", "steps", "guidance", "fps", "seed", "strength"])
    : providerName === "seedance2_api"
      ? new Set(["resolution", "generate_audio", "watermark"])
    : providerName === "minimax_h3_gateway"
      ? new Set(["landscape_size", "portrait_size", "steps", "seed"])
    : providerName === "wan2_i2v_api"
      ? new Set(["protocol", "mode", "gpu_devices", "size", "seed", "frames", "steps", "guidance", "fps", "max_area", "upscale_1080p", "result_variant", "image_transport"])
      : null;
  const payload = Object.fromEntries(
    Object.entries(params)
      .filter(([key]) => slot !== "image" || imageParamKeys.has(key))
      .filter(([key]) => slot !== "video" || videoParamKeys === null || videoParamKeys.has(key))
      .filter(([, value]) => typeof value === "boolean" || String(value).trim() !== "")
      .map(([key, value]) => [key, numericKeys.has(key) ? Number(value) : value]),
  );
  if (slot === "video" && providerName === "wan2_i2v_api" && payload.protocol === "official") {
    payload.steps = 4;
    payload.guidance = 1;
  }
  return payload;
}

function agentPayload(draft: ModelConfigDraft): AgentConfigPayload {
  return {
    agent_type: draft.agent_type.trim(),
    name: draft.name.trim(),
    description: nullableText(draft.description),
    provider: nullableText(draft.provider),
    model: nullableText(draft.model),
    system_prompt: nullableText(draft.system_prompt),
    temperature: nullableText(draft.temperature),
    max_tokens: nullableNumber(draft.max_tokens),
    max_iterations: nullableNumber(draft.max_iterations),
    settings: draft.settings,
    is_active: draft.is_active,
  };
}

function nullableText(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function nullableNumber(value: string) {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function setAgentDraftField(
  setDraft: (updater: (current: ModelConfigDraft) => ModelConfigDraft) => void,
  key: keyof ModelConfigDraft,
  value: string,
) {
  setDraft((current) => ({ ...current, [key]: value }));
}
