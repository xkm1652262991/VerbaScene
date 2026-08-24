import type { ProductionDialogue, ProductionScene } from "../../types/stageFive";
import {
  createEmptyProductionScene,
  parseTextList,
  renderProductionScript,
} from "../../utils/productionScript";

type ProductionScriptEditorProps = {
  isLegacy?: boolean;
  onChange: (scenes: ProductionScene[]) => void;
  scenes: ProductionScene[];
};

function renumberScenes(scenes: ProductionScene[]) {
  return scenes.map((scene, index) => ({
    ...scene,
    scene_no: index + 1,
    dialogues: scene.dialogues.map((dialogue) => ({ ...dialogue, scene_no: index + 1 })),
  }));
}

export function ProductionScriptEditor({ isLegacy = false, onChange, scenes }: ProductionScriptEditorProps) {
  const readableContent = renderProductionScript(scenes);

  function updateScene(index: number, updates: Partial<ProductionScene>) {
    onChange(scenes.map((scene, sceneIndex) => sceneIndex === index ? { ...scene, ...updates } : scene));
  }

  function updateDialogue(sceneIndex: number, dialogueIndex: number, updates: Partial<ProductionDialogue>) {
    const scene = scenes[sceneIndex];
    updateScene(sceneIndex, {
      dialogues: scene.dialogues.map((dialogue, index) => index === dialogueIndex ? { ...dialogue, ...updates } : dialogue),
    });
  }

  function addDialogue(sceneIndex: number) {
    const scene = scenes[sceneIndex];
    updateScene(sceneIndex, {
      dialogues: [
        ...scene.dialogues,
        {
          speaker: "",
          text: "",
          translation_zh: "",
          emotion: "",
          source_type: "created",
          sound_cues: [],
          scene_no: scene.scene_no,
        },
      ],
    });
  }

  function removeDialogue(sceneIndex: number, dialogueIndex: number) {
    const scene = scenes[sceneIndex];
    updateScene(sceneIndex, {
      dialogues: scene.dialogues.filter((_, index) => index !== dialogueIndex),
    });
  }

  function addScene() {
    onChange([...scenes, createEmptyProductionScene(scenes.length + 1)]);
  }

  function removeScene(sceneIndex: number) {
    onChange(renumberScenes(scenes.filter((_, index) => index !== sceneIndex)));
  }

  return (
    <div className="production-script-workspace">
      <aside className="production-script-preview">
        <div className="production-script-section-head">
          <div>
            <span>Readable Screenplay</span>
            <h3>可读剧本</h3>
          </div>
          <em>由右侧场景卡自动生成</em>
        </div>
        {isLegacy ? (
          <p className="production-script-migration-note">
            这是旧版剧本。保存后会升级为统一结构；只有正文中真实出现的旧对白会被保留。
          </p>
        ) : null}
        <pre className="screenplay-editor screenplay-preview">{readableContent || "请先添加场景并填写可见动作。"}</pre>
      </aside>

      <section className="production-scene-editor">
        <div className="production-script-section-head">
          <div>
            <span>Production Structure</span>
            <h3>生产场景卡</h3>
          </div>
          <button className="secondary-button inline-action" onClick={addScene} type="button">
            添加场景
          </button>
        </div>
        <p className="production-script-help">
          场景卡是唯一主数据。保存时，左侧正文和后续音频使用的对白会同步更新。
        </p>

        <div className="production-scene-list">
          {scenes.map((scene, sceneIndex) => (
            <article className="production-scene-card" key={`${scene.scene_no}-${sceneIndex}`}>
              <div className="production-scene-card-head">
                <div>
                  <span>Scene {scene.scene_no}</span>
                  <strong>{scene.title || `场景${scene.scene_no}`}</strong>
                </div>
                <button
                  className="secondary-button inline-action danger-action"
                  disabled={scenes.length <= 1}
                  onClick={() => removeScene(sceneIndex)}
                  type="button"
                >
                  删除场景
                </button>
              </div>

              <div className="production-scene-grid four-columns">
                <label>
                  场景标题
                  <input value={scene.title} onChange={(event) => updateScene(sceneIndex, { title: event.target.value })} />
                </label>
                <label>
                  地点
                  <input value={scene.location} onChange={(event) => updateScene(sceneIndex, { location: event.target.value })} />
                </label>
                <label>
                  时间
                  <input value={scene.time_of_day} onChange={(event) => updateScene(sceneIndex, { time_of_day: event.target.value })} />
                </label>
                <label>
                  情绪氛围
                  <input value={scene.mood} onChange={(event) => updateScene(sceneIndex, { mood: event.target.value })} />
                </label>
              </div>

              <label className="production-scene-field">
                可见画面与动作 <em>必填</em>
                <textarea
                  rows={4}
                  value={scene.visible_action}
                  onChange={(event) => updateScene(sceneIndex, { visible_action: event.target.value })}
                />
              </label>

              <div className="production-scene-grid">
                <label>
                  出场人物 <small>用顿号或逗号分隔</small>
                  <input
                    value={scene.characters.join("、")}
                    onChange={(event) => updateScene(sceneIndex, { characters: parseTextList(event.target.value) })}
                  />
                </label>
                <label>
                  关键道具 <small>用顿号或逗号分隔</small>
                  <input
                    value={scene.props.join("、")}
                    onChange={(event) => updateScene(sceneIndex, { props: parseTextList(event.target.value) })}
                  />
                </label>
                <label>
                  入场状态
                  <textarea rows={3} value={scene.start_state} onChange={(event) => updateScene(sceneIndex, { start_state: event.target.value })} />
                </label>
                <label>
                  离场状态
                  <textarea rows={3} value={scene.end_state} onChange={(event) => updateScene(sceneIndex, { end_state: event.target.value })} />
                </label>
              </div>

              <details className="production-scene-details">
                <summary>剧情依据与补充信息</summary>
                <div className="production-scene-grid">
                  <label>
                    剧情作用
                    <textarea rows={3} value={scene.story_purpose} onChange={(event) => updateScene(sceneIndex, { story_purpose: event.target.value })} />
                  </label>
                  <label>
                    原文依据
                    <textarea rows={3} value={scene.source_evidence} onChange={(event) => updateScene(sceneIndex, { source_evidence: event.target.value })} />
                  </label>
                  <label className="full-span">
                    补足的连接元素 <small>用顿号或逗号分隔；没有就留空</small>
                    <input
                      value={scene.inferred_elements.join("、")}
                      onChange={(event) => updateScene(sceneIndex, { inferred_elements: parseTextList(event.target.value) })}
                    />
                  </label>
                </div>
              </details>

              <section className="production-dialogue-section">
                <div className="production-dialogue-head">
                  <div>
                    <strong>场内对白</strong>
                    <span>{scene.dialogues.length > 0 ? `${scene.dialogues.length} 条` : "原文无对白时保持为空"}</span>
                  </div>
                  <button className="secondary-button inline-action" onClick={() => addDialogue(sceneIndex)} type="button">
                    添加对白
                  </button>
                </div>
                {scene.dialogues.map((dialogue, dialogueIndex) => (
                  <div className="production-dialogue-row" key={`${dialogueIndex}-${dialogue.text}`}>
                    <label>
                      说话人
                      <input value={dialogue.speaker} onChange={(event) => updateDialogue(sceneIndex, dialogueIndex, { speaker: event.target.value })} />
                    </label>
                    <label>
                      情绪
                      <input value={dialogue.emotion} onChange={(event) => updateDialogue(sceneIndex, dialogueIndex, { emotion: event.target.value })} />
                    </label>
                    <label>
                      来源
                      <select
                        value={dialogue.source_type}
                        onChange={(event) => updateDialogue(sceneIndex, dialogueIndex, { source_type: event.target.value as ProductionDialogue["source_type"] })}
                      >
                        <option value="source">原文对白</option>
                        <option value="adapted">原话轻量改写</option>
                        <option value="created">为教学目标创作</option>
                      </select>
                    </label>
                    <label className="dialogue-text-field">
                      英文对白
                      <textarea rows={2} value={dialogue.text} onChange={(event) => updateDialogue(sceneIndex, dialogueIndex, { text: event.target.value })} />
                    </label>
                    <label className="dialogue-text-field">
                      中文释义（可选）
                      <textarea rows={2} value={dialogue.translation_zh} onChange={(event) => updateDialogue(sceneIndex, dialogueIndex, { translation_zh: event.target.value })} />
                    </label>
                    <button
                      aria-label="删除对白"
                      className="secondary-button inline-action danger-action"
                      onClick={() => removeDialogue(sceneIndex, dialogueIndex)}
                      type="button"
                    >
                      删除
                    </button>
                  </div>
                ))}
              </section>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
