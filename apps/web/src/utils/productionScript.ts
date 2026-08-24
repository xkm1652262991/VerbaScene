import type { ProductionDialogue, ProductionScene, Script } from "../types/stageFive";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asText(value: unknown, fallback = "") {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  return text || fallback;
}

function asPositiveInt(value: unknown, fallback: number) {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function parseTextList(value: unknown): string[] {
  const values = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(/[,，、\n]+/)
      : [];
  return values.reduce<string[]>((result, item) => {
    const text = asText(item);
    if (text && !result.includes(text)) result.push(text);
    return result;
  }, []);
}

function normalizeDialogue(value: unknown, sceneNo: number): ProductionDialogue | null {
  const item = asRecord(value);
  const text = asText(item.text);
  if (!text) return null;
  return {
    id: asText(item.id) || undefined,
    speaker: asText(item.speaker, "角色"),
    text,
    translation_zh: asText(item.translation_zh),
    emotion: asText(item.emotion),
    source_type: item.source_type === "adapted" || item.source_type === "created"
      ? item.source_type
      : "source",
    sequence_order: Number(item.sequence_order ?? 0),
    beat_id: asText(item.beat_id),
    sound_cues: parseTextList(item.sound_cues),
    scene_no: sceneNo,
  };
}

function inferSceneNo(content: string, dialogueText: string) {
  const position = content.indexOf(dialogueText);
  if (position < 0) return 1;
  const prefix = content.slice(0, position);
  const markers = prefix.match(/场景(?:[零一二三四五六七八九十百]+|\d+)[：:]/g);
  return Math.max(1, markers?.length ?? 1);
}

export function normalizeProductionScenes(script: Script | null): ProductionScene[] {
  const rawScenes = Array.isArray(script?.scenes) ? script.scenes : [];
  const scenes = rawScenes.map((value, index) => {
    const item = asRecord(value);
    const sceneNo = asPositiveInt(item.scene_no, index + 1);
    const visibleAction = asText(item.visible_action, asText(item.summary));
    const rawDialogues = Array.isArray(item.dialogues) ? item.dialogues : [];
    return {
      scene_no: sceneNo,
      title: asText(item.title, `场景${index + 1}`),
      location: asText(item.location),
      time_of_day: asText(item.time_of_day),
      characters: parseTextList(item.characters),
      props: parseTextList(item.props),
      visible_action: visibleAction,
      story_purpose: asText(item.story_purpose, visibleAction),
      start_state: asText(item.start_state),
      end_state: asText(item.end_state),
      mood: asText(item.mood),
      source_evidence: asText(item.source_evidence, visibleAction),
      inferred_elements: parseTextList(item.inferred_elements),
      sound_cues: parseTextList(item.sound_cues),
      dialogues: rawDialogues
        .map((dialogue) => normalizeDialogue(dialogue, sceneNo))
        .filter((dialogue): dialogue is ProductionDialogue => dialogue !== null),
    };
  });

  if (!script || !Array.isArray(script.dialogues) || !script.content) return scenes;
  const existing = new Set(
    scenes.flatMap((scene) => scene.dialogues.map((dialogue) => `${dialogue.speaker}\u0000${dialogue.text}`)),
  );
  script.dialogues.forEach((value) => {
    const item = asRecord(value);
    const text = asText(item.text);
    const speaker = asText(item.speaker, "角色");
    const key = `${speaker}\u0000${text}`;
    if (!text || !script.content.includes(text) || existing.has(key)) return;
    const explicitSceneNo = asPositiveInt(item.scene_no, 0);
    const sceneNo = explicitSceneNo || inferSceneNo(script.content, text);
    const target = scenes.find((scene) => scene.scene_no === sceneNo) ?? scenes[0];
    if (!target) return;
    const dialogue = normalizeDialogue(item, target.scene_no);
    if (!dialogue) return;
    target.dialogues.push(dialogue);
    existing.add(key);
  });
  return scenes;
}

const chineseNumbers = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];

export function renderProductionScript(scenes: ProductionScene[]) {
  return scenes.map((scene, index) => {
    const sceneNo = scene.scene_no || index + 1;
    const label = chineseNumbers[sceneNo] ?? String(sceneNo);
    const lines = [`场景${label}：${scene.title || `场景${index + 1}`}`];
    const placeAndTime = [
      scene.location ? `地点：${scene.location}` : "",
      scene.time_of_day ? `时间：${scene.time_of_day}` : "",
    ].filter(Boolean);
    if (placeAndTime.length > 0) lines.push(placeAndTime.join("｜"));
    if (scene.characters.length > 0) lines.push(`出场：${scene.characters.join("、")}`);
    if (scene.props.length > 0) lines.push(`关键道具：${scene.props.join("、")}`);
    if (scene.start_state || scene.end_state) {
      lines.push(`状态变化：${scene.start_state || "未说明"} → ${scene.end_state || "未说明"}`);
    }
    lines.push(`画面：${scene.visible_action}`);
    scene.dialogues.forEach((dialogue) => {
      const emotion = dialogue.emotion ? `（${dialogue.emotion}）` : "";
      lines.push(`${dialogue.speaker}${emotion}：${dialogue.text}`);
    });
    return lines.join("\n");
  }).join("\n\n");
}

export function renderReadableReviewScript(scenes: ProductionScene[]) {
  return scenes.map((scene, index) => {
    const sceneNo = scene.scene_no || index + 1;
    const label = chineseNumbers[sceneNo] ?? String(sceneNo);
    const lines = [`场景${label}：${scene.title || `场景${index + 1}`}`];
    if (scene.visible_action) lines.push("", scene.visible_action);
    scene.dialogues.forEach((dialogue) => {
      const emotion = dialogue.emotion ? `（${dialogue.emotion}）` : "";
      lines.push("", `${dialogue.speaker}${emotion}：${dialogue.text}`);
      if (dialogue.translation_zh) lines.push(`中文释义：${dialogue.translation_zh}`);
    });
    return lines.join("\n");
  }).join("\n\n");
}

export function hasLegacySceneShape(script: Script | null) {
  if (!script || !Array.isArray(script.scenes)) return false;
  return script.scenes.some((value) => !("visible_action" in asRecord(value)));
}

export function createEmptyProductionScene(sceneNo: number): ProductionScene {
  return {
    scene_no: sceneNo,
    title: `场景${sceneNo}`,
    location: "",
    time_of_day: "",
    characters: [],
    props: [],
    visible_action: "",
    story_purpose: "",
    start_state: "",
    end_state: "",
    mood: "",
    source_evidence: "",
    inferred_elements: [],
    sound_cues: [],
    dialogues: [],
  };
}
