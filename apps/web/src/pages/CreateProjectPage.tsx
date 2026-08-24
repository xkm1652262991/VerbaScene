import { FormEvent, useState } from "react";

import { createProject } from "../services/apiClient";
import { DEFAULT_VISUAL_STYLE, DEFAULT_VISUAL_STYLE_HELP } from "../constants/visualStyle";
import type { ProjectInputMode } from "../types/project";

export function CreateProjectPage() {
  const [mode, setMode] = useState<ProjectInputMode>("ai_brief");
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [animationStyle, setAnimationStyle] = useState(DEFAULT_VISUAL_STYLE);
  const [duration, setDuration] = useState(90);
  const [aspectRatio, setAspectRatio] = useState<"16:9" | "9:16">("16:9");
  const [englishLevel, setEnglishLevel] = useState<"A1" | "A2">("A1");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const resolution = aspectRatio === "16:9" ? "854x480" as const : "480x854" as const;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setIsSubmitting(true);
      setError(null);
      const project = await createProject({
        title: title.trim(),
        input_mode: mode,
        outline: mode === "ai_brief" ? source.trim() : "",
        source_text: mode === "imported_script" ? source.trim() : "",
        style: animationStyle.trim(),
        target_duration_sec: duration,
        aspect_ratio: aspectRatio,
        resolution,
        creative_settings: {
          english_level: englishLevel,
          animation_style: animationStyle.trim(),
          dialogue_language: "en",
          translation_language: "zh-CN",
          default_subtitle_mode: "none",
        },
      });
      window.location.hash = `/projects/${project.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "项目创建失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="creation-studio english-drama-create">
      <div className="section-header">
        <div>
          <span className="page-eyebrow">NEW ENGLISH ANIMATION</span>
          <h2>创建动画英语短剧</h2>
        </div>
        <a className="secondary-button" href="#/projects">返回项目列表</a>
      </div>

      <div className="creation-layout">
        <div className="creation-copy">
          <span className="creation-step">ONE EPISODE · A1 FIRST</span>
          <h1>从一个创意，直接进入可制作的英语短剧。</h1>
          <p>英文对白进入视频模型的最终 Prompt，中文释义只服务编辑和可选双语字幕。</p>
          <div className="creation-locks">
            <span>三页自由切换</span>
            <span>原生对白与音效</span>
            <span>默认无字幕导出</span>
          </div>
        </div>

        <form className="project-form creation-form" onSubmit={handleSubmit}>
          <div className="creation-mode-tabs" role="tablist" aria-label="创作入口">
            <button className={mode === "ai_brief" ? "active" : ""} onClick={() => setMode("ai_brief")} type="button">
              AI 生剧本
            </button>
            <button className={mode === "imported_script" ? "active" : ""} onClick={() => setMode("imported_script")} type="button">
              导入已有剧本
            </button>
          </div>

          <label>
            项目名称
            <input placeholder="例如：Mia Learns to Share" required value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>

          <label>
            {mode === "ai_brief" ? "创意描述" : "已有剧本文本"}
            <textarea
              placeholder={mode === "ai_brief" ? "例如：两个孩子一起收拾玩具，练习 Can you help me? 和 Yes, I can." : "粘贴本集故事或剧本文本"}
              required
              rows={7}
              value={source}
              onChange={(event) => setSource(event.target.value)}
            />
          </label>

          <div className="creation-settings-grid">
            <label>
              英语等级
              <select value={englishLevel} onChange={(event) => setEnglishLevel(event.target.value as "A1" | "A2")}>
                <option value="A1">A1（默认）</option>
                <option value="A2">A2</option>
              </select>
            </label>
            <label>
              目标时长
              <input min={15} max={180} step={5} type="number" value={duration} onChange={(event) => setDuration(Number(event.target.value))} />
            </label>
            <label>
              画幅
              <select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value as "16:9" | "9:16")}>
                <option value="16:9">16:9 · 854×480（480p）</option>
                <option value="9:16">9:16 · 480×854</option>
              </select>
            </label>
          </div>

          <label>
            动画风格
            <input required value={animationStyle} onChange={(event) => setAnimationStyle(event.target.value)} />
            <small>{DEFAULT_VISUAL_STYLE_HELP}</small>
          </label>

          <div className="creation-route">
            <span>工作台</span>
            <strong>剧本 → 资产库 → 视频制作（自由进入）</strong>
          </div>
          {error ? <div className="state-box error">{error}</div> : null}
          <button className="primary-button creation-submit" disabled={isSubmitting} type="submit">
            <span>{isSubmitting ? "正在创建..." : "创建并进入剧本页"}</span>
          </button>
        </form>
      </div>
    </section>
  );
}
