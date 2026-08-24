import { ChangeEvent, FormEvent, useState } from "react";

import { createProject } from "../../services/apiClient";
import { DEFAULT_VISUAL_STYLE, DEFAULT_VISUAL_STYLE_HELP } from "../../constants/visualStyle";
import type { ProjectInputMode } from "../../types/project";

type ProjectCreationLauncherProps = {
  autoFocus?: boolean;
};

export function ProjectCreationLauncher({ autoFocus = false }: ProjectCreationLauncherProps) {
  const [mode, setMode] = useState<ProjectInputMode>("imported_script");
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [animationStyle, setAnimationStyle] = useState(DEFAULT_VISUAL_STYLE);
  const [duration, setDuration] = useState(90);
  const [aspectRatio, setAspectRatio] = useState<"16:9" | "9:16">("16:9");
  const [englishLevel, setEnglishLevel] = useState<"A1" | "A2">("A1");
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const resolution = aspectRatio === "16:9" ? "854x480" as const : "480x854" as const;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedSource = source.trim();
    if (!trimmedSource) {
      setError(mode === "imported_script" ? "请粘贴或选择剧本文本。" : "请描述你想创作的故事。");
      return;
    }
    try {
      setIsSubmitting(true);
      setError(null);
      const project = await createProject({
        title: title.trim() || deriveProjectTitle(trimmedSource),
        input_mode: mode,
        outline: mode === "ai_brief" ? trimmedSource : "",
        source_text: mode === "imported_script" ? trimmedSource : "",
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目创建失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!/\.(txt|md)$/i.test(file.name)) {
      setError("当前支持 .txt 和 .md 文本文件；DOCX/PDF 解析尚未接入。");
      return;
    }
    try {
      setSource(await file.text());
      setFileName(file.name);
      setError(null);
    } catch {
      setError("文件读取失败，请改用粘贴文本。");
    }
  }

  return (
    <section className="project-launcher" aria-labelledby="project-launcher-title">
      <div className="project-launcher-intro">
        <span>ANIMATED ENGLISH STUDIO</span>
        <h1 id="project-launcher-title">今天想制作什么英语短剧？</h1>
        <p>导入已有剧本，或从一个创意开始。后续资产、片段和 Seedance Prompt 会在同一个项目中继续完成。</p>
      </div>

      <form className="project-launcher-card" onSubmit={handleSubmit}>
        <div className="project-launcher-tabs" role="tablist" aria-label="剧本来源">
          <button
            aria-selected={mode === "imported_script"}
            className={mode === "imported_script" ? "active" : ""}
            onClick={() => { setMode("imported_script"); setError(null); }}
            role="tab"
            type="button"
          >
            导入剧本
          </button>
          <button
            aria-selected={mode === "ai_brief"}
            className={mode === "ai_brief" ? "active" : ""}
            onClick={() => { setMode("ai_brief"); setError(null); setFileName(""); }}
            role="tab"
            type="button"
          >
            AI 生剧本
          </button>
        </div>

        <label className="project-launcher-input">
          <span>{mode === "imported_script" ? "把剧本交给我们" : "描述你的故事想法"}</span>
          <textarea
            autoFocus={autoFocus}
            placeholder={mode === "imported_script"
              ? "在这里粘贴完整剧本、故事正文或分场文本……"
              : "例如：两个孩子一起搭积木，练习 Can you help me? 和 Yes, I can."}
            rows={9}
            value={source}
            onChange={(event) => { setSource(event.target.value); setFileName(""); }}
          />
        </label>

        {mode === "imported_script" ? (
          <div className="project-launcher-file-row">
            <label className="secondary-button">
              选择 .txt / .md
              <input accept=".txt,.md,text/plain,text/markdown" onChange={(event) => void handleFile(event)} type="file" />
            </label>
            <span>{fileName || "也可以直接粘贴文本；原稿会完整保留。"}</span>
          </div>
        ) : null}

        <div className="project-launcher-toolbar">
          <div className="project-launcher-chips" aria-label="当前制作设置">
            <span>{englishLevel}</span>
            <span>{aspectRatio} · 480p</span>
            <span>{duration} 秒</span>
          </div>
          <button className="primary-button project-launcher-submit" disabled={isSubmitting} type="submit">
            {isSubmitting ? "正在创建…" : mode === "imported_script" ? "导入并开始制作" : "创建并生成剧本"}
          </button>
        </div>

        <details className="project-launcher-settings">
          <summary>制作设置</summary>
          <div className="project-launcher-settings-grid">
            <label>
              项目名称（可选）
              <input placeholder="留空时从正文自动生成" value={title} onChange={(event) => setTitle(event.target.value)} />
            </label>
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
                <option value="16:9">16:9 · 854×480</option>
                <option value="9:16">9:16 · 480×854</option>
              </select>
            </label>
            <label className="project-launcher-style">
              动画风格
              <input value={animationStyle} onChange={(event) => setAnimationStyle(event.target.value)} />
              <small>{DEFAULT_VISUAL_STYLE_HELP}</small>
            </label>
          </div>
        </details>

        {error ? <div className="state-box error" role="alert">{error}</div> : null}
      </form>
    </section>
  );
}

function deriveProjectTitle(source: string) {
  const firstLine = source.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? "";
  const bookTitle = firstLine.match(/《([^》]+)》/)?.[1];
  const cleaned = (bookTitle || firstLine)
    .replace(/^#{1,6}\s*/, "")
    .replace(/[*_`]/g, "")
    .replace(/[：:|\-—]+$/g, "")
    .trim();
  return (cleaned || "未命名英语短剧").slice(0, 60);
}
