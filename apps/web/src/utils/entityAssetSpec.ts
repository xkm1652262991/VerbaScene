import type {
  AssetReferencePlan,
  AssetSourceEvidence,
  AssetStateVariant,
  Character,
  CharacterAssetSpec,
  Prop,
  PropAssetSpec,
  Scene,
  SceneAssetSpec,
} from "../types/stageFive";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asText(value: unknown) {
  return value === null || value === undefined ? "" : String(value).trim();
}

export function parseAssetTextList(value: unknown): string[] {
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

export function parseSceneNumbers(value: unknown): number[] {
  return parseAssetTextList(value).reduce<number[]>((result, item) => {
    const number = Number.parseInt(item, 10);
    if (Number.isFinite(number) && number > 0 && !result.includes(number)) result.push(number);
    return result;
  }, []);
}

function normalizeStates(value: unknown): AssetStateVariant[] {
  return Array.isArray(value)
    ? value.map((item, index) => {
      const record = asRecord(item);
      return {
        key: asText(record.key) || `state-${index + 1}`,
        name: asText(record.name),
        description: asText(record.description),
        scene_nos: Array.isArray(record.scene_nos)
          ? record.scene_nos.map(Number).filter((number) => Number.isFinite(number) && number > 0)
          : [],
      };
    }).filter((item) => item.name || item.description)
    : [];
}

function normalizeEvidence(value: unknown): AssetSourceEvidence[] {
  return Array.isArray(value)
    ? value.map((item) => {
      const record = asRecord(item);
      const sceneNo = Number(record.scene_no);
      return {
        scene_no: Number.isFinite(sceneNo) && sceneNo > 0 ? sceneNo : null,
        evidence: asText(record.evidence),
      };
    }).filter((item) => item.evidence)
    : [];
}

function normalizeReferencePlan(
  value: unknown,
  fallback: AssetReferencePlan,
): AssetReferencePlan {
  const record = asRecord(value);
  const priority = record.priority === "core" || record.priority === "optional"
    ? record.priority
    : record.priority === "supporting"
      ? "supporting"
      : fallback.priority;
  const views = parseAssetTextList(record.views);
  const required = typeof record.required === "boolean" ? record.required : fallback.required;
  return {
    required,
    priority,
    views: views.length > 0 ? views : required ? fallback.views : [],
  };
}

function normalizeAutofill(value: unknown) {
  const record = asRecord(value);
  return {
    applied: record.applied === true,
    filled_fields: parseAssetTextList(record.filled_fields),
    normalized_fields: parseAssetTextList(record.normalized_fields),
    review_fields: parseAssetTextList(record.review_fields),
  };
}

export function normalizeCharacterAssetSpec(item: Character): CharacterAssetSpec {
  const source = asRecord(item.asset_spec);
  const signatureFeatures = parseAssetTextList(source.signature_features);
  if (isLegacyAssetSpec(item.asset_spec) && item.appearance) signatureFeatures.push(item.appearance);
  return {
    schema_version: 1,
    autofill: normalizeAutofill(source.autofill),
    aliases: parseAssetTextList(source.aliases),
    entity_kind: asText(source.entity_kind),
    species: asText(source.species),
    body_type: asText(source.body_type),
    facial_features: asText(source.facial_features),
    hair_or_surface: asText(source.hair_or_surface),
    color_palette: parseAssetTextList(source.color_palette),
    default_outfit: asText(source.default_outfit),
    signature_features: [...new Set(signatureFeatures)],
    default_accessories: parseAssetTextList(source.default_accessories),
    state_variants: normalizeStates(source.state_variants),
    source_evidence: normalizeEvidence(source.source_evidence),
    reference_plan: normalizeReferencePlan(source.reference_plan, {
      required: true,
      priority: "core",
      views: ["正面全身", "侧面全身", "面部近景"],
    }),
  };
}

export function normalizeSceneAssetSpec(item: Scene): SceneAssetSpec {
  const source = asRecord(item.asset_spec);
  return {
    schema_version: 1,
    autofill: normalizeAutofill(source.autofill),
    aliases: parseAssetTextList(source.aliases),
    location_type: asText(source.location_type),
    spatial_layout: asText(source.spatial_layout) || item.description || "",
    fixed_landmarks: parseAssetTextList(source.fixed_landmarks),
    materials: parseAssetTextList(source.materials),
    color_palette: parseAssetTextList(source.color_palette),
    zones: parseAssetTextList(source.zones),
    state_variants: normalizeStates(source.state_variants),
    source_evidence: normalizeEvidence(source.source_evidence),
    reference_plan: normalizeReferencePlan(source.reference_plan, {
      required: true,
      priority: "supporting",
      views: ["空间全景", "关键区域视图"],
    }),
  };
}

export function normalizePropAssetSpec(item: Prop): PropAssetSpec {
  const source = asRecord(item.asset_spec);
  const signatureFeatures = parseAssetTextList(source.signature_features);
  if (isLegacyAssetSpec(item.asset_spec) && item.description) signatureFeatures.push(item.description);
  return {
    schema_version: 1,
    autofill: normalizeAutofill(source.autofill),
    aliases: parseAssetTextList(source.aliases),
    shape: asText(source.shape),
    dimensions: asText(source.dimensions),
    materials: parseAssetTextList(source.materials),
    color_palette: parseAssetTextList(source.color_palette),
    signature_features: [...new Set(signatureFeatures)],
    scale_reference: asText(source.scale_reference),
    holder_relation: asText(source.holder_relation),
    story_function: asText(source.story_function) || item.story_function || "",
    state_variants: normalizeStates(source.state_variants),
    source_evidence: normalizeEvidence(source.source_evidence),
    reference_plan: normalizeReferencePlan(source.reference_plan, {
      required: true,
      priority: "supporting",
      views: ["正面三分之四视角", "侧面尺度视图"],
    }),
  };
}

export function isLegacyAssetSpec(value: unknown) {
  return Number(asRecord(value).schema_version) !== 1;
}

function joinParts(parts: Array<string | null | undefined>) {
  return parts.map((item) => item?.trim()).filter(Boolean).join("，");
}

function labeledList(label: string, values: string[]) {
  return values.length > 0 ? `${label}：${values.join("、")}` : "";
}

export function compileCharacterPreview(
  metadata: Pick<Character, "name" | "identity" | "age" | "gender">,
  spec: CharacterAssetSpec,
) {
  const appearance = joinParts([
    spec.species,
    spec.body_type,
    spec.facial_features,
    spec.hair_or_surface,
    labeledList("主色", spec.color_palette),
    spec.default_outfit,
    labeledList("标志特征", spec.signature_features),
    labeledList("默认配件", spec.default_accessories),
  ]);
  return {
    readable: appearance,
    prompt: joinParts([
      metadata.name,
      metadata.identity,
      metadata.age,
      metadata.gender,
      appearance,
      "稳定身份、体型比例、配色、默认服装和标志性特征，多镜头保持一致",
    ]),
  };
}

export function compileScenePreview(name: string, spec: SceneAssetSpec) {
  const description = joinParts([
    spec.location_type,
    spec.spatial_layout,
    labeledList("固定地标", spec.fixed_landmarks),
    labeledList("子区域", spec.zones),
  ]);
  const materialAndColor = joinParts([
    labeledList("材质", spec.materials),
    labeledList("固定色板", spec.color_palette),
  ]);
  return {
    readable: joinParts([description, materialAndColor]),
    prompt: joinParts([
      name,
      description,
      materialAndColor,
      "固定空间结构、地标位置、材质和色板，不写入临时时间、天气与光线状态",
    ]),
  };
}

export function compilePropPreview(name: string, spec: PropAssetSpec) {
  const description = joinParts([
    spec.shape,
    spec.dimensions,
    labeledList("材质", spec.materials),
    labeledList("主色", spec.color_palette),
    labeledList("标志细节", spec.signature_features),
    spec.scale_reference,
    spec.holder_relation,
  ]);
  return {
    readable: description,
    prompt: joinParts([
      name,
      description,
      "固定形状、尺寸比例、材质、配色和标志细节，多镜头保持一致",
    ]),
  };
}
