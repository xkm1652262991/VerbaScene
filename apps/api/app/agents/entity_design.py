import re
from typing import Any


REFERENCE_PRIORITIES = {"core", "supporting", "optional"}
BLOCKING_MUTABLE_STATE_PATTERNS = (
    re.compile(r"受伤|伤口|流血|血迹|骨折|跛腿|昏迷|湿透|浑身湿|康复|痊愈|伤愈|恢复健康"),
    re.compile(r"(?:左|右|双)?(?:翼|翅膀).{0,6}(?:下垂|垂着|无法展开|折断)"),
    re.compile(r"羽(?:毛|尖).{0,4}(?:微乱|凌乱|紊乱|蓬乱)"),
)
REVIEW_MUTABLE_STATE_PATTERNS = (
    re.compile(r"哭泣|流泪|微笑|大笑|愤怒|害怕|疲惫|紧张|惊讶"),
    re.compile(r"蹲着|坐着|躺着|跑动|飞行|试飞|展翅|振翅|鸣唱|挥手|手持|拿着|正在"),
)


def normalize_character_asset_spec(
    value: Any,
    *,
    legacy_appearance: str | None = None,
    default_reference_views: bool = True,
) -> dict[str, Any]:
    source = _mapping(value)
    signature_features = _string_list(source.get("signature_features"))
    if not _has_character_identity(source) and legacy_appearance:
        signature_features = _string_list([*signature_features, legacy_appearance])
    result = {
        "schema_version": 1,
        "autofill": _autofill_info(source.get("autofill")),
        "aliases": _string_list(source.get("aliases")),
        "entity_kind": _string(source.get("entity_kind")),
        "species": _string(source.get("species")),
        "body_type": _string(source.get("body_type")),
        "facial_features": _string(source.get("facial_features")),
        "hair_or_surface": _string(source.get("hair_or_surface")),
        "color_palette": _string_list(source.get("color_palette")),
        "default_outfit": _string(source.get("default_outfit")),
        "signature_features": signature_features,
        "default_accessories": _string_list(source.get("default_accessories")),
        "state_variants": _state_variants(source.get("state_variants")),
        "source_evidence": _source_evidence(source.get("source_evidence")),
        "reference_plan": _reference_plan(
            source.get("reference_plan"),
            required=True,
            priority="core",
            default_views=["正面全身", "侧面全身", "面部近景"] if default_reference_views else [],
        ),
    }
    if isinstance(source.get("image_prompts"), dict):
        result["image_prompts"] = source["image_prompts"]
    return result


def normalize_scene_asset_spec(
    value: Any,
    *,
    legacy_description: str | None = None,
    default_reference_views: bool = True,
) -> dict[str, Any]:
    source = _mapping(value)
    spatial_layout = _string(source.get("spatial_layout")) or _string(legacy_description)
    result = {
        "schema_version": 1,
        "autofill": _autofill_info(source.get("autofill")),
        "aliases": _string_list(source.get("aliases")),
        "location_type": _string(source.get("location_type")),
        "spatial_layout": spatial_layout,
        "fixed_landmarks": _string_list(source.get("fixed_landmarks")),
        "materials": _string_list(source.get("materials")),
        "color_palette": _string_list(source.get("color_palette")),
        "zones": _string_list(source.get("zones")),
        "state_variants": _state_variants(source.get("state_variants")),
        "source_evidence": _source_evidence(source.get("source_evidence")),
        "reference_plan": _reference_plan(
            source.get("reference_plan"),
            required=True,
            priority="supporting",
            default_views=["空间全景", "关键区域视图"] if default_reference_views else [],
        ),
    }
    if isinstance(source.get("image_prompts"), dict):
        result["image_prompts"] = source["image_prompts"]
    return result


def normalize_prop_asset_spec(
    value: Any,
    *,
    legacy_description: str | None = None,
    legacy_story_function: str | None = None,
    default_reference_views: bool = True,
) -> dict[str, Any]:
    source = _mapping(value)
    signature_features = _string_list(source.get("signature_features"))
    if not _has_prop_identity(source) and legacy_description:
        signature_features = _string_list([*signature_features, legacy_description])
    result = {
        "schema_version": 1,
        "autofill": _autofill_info(source.get("autofill")),
        "aliases": _string_list(source.get("aliases")),
        "shape": _string(source.get("shape")),
        "dimensions": _string(source.get("dimensions")),
        "materials": _string_list(source.get("materials")),
        "color_palette": _string_list(source.get("color_palette")),
        "signature_features": signature_features,
        "scale_reference": _string(source.get("scale_reference")),
        "holder_relation": _string(source.get("holder_relation")),
        "story_function": _string(source.get("story_function")) or _string(legacy_story_function),
        "state_variants": _state_variants(source.get("state_variants")),
        "source_evidence": _source_evidence(source.get("source_evidence")),
        "reference_plan": _reference_plan(
            source.get("reference_plan"),
            required=True,
            priority="supporting",
            default_views=["正面三分之四视角", "侧面尺度视图"] if default_reference_views else [],
        ),
    }
    if isinstance(source.get("image_prompts"), dict):
        result["image_prompts"] = source["image_prompts"]
    return result


def compile_character_asset_fields(
    *,
    name: str,
    identity: str | None,
    age: str | None,
    gender: str | None,
    asset_spec: dict[str, Any],
) -> tuple[str | None, str | None]:
    appearance = _join_parts(
        [
            asset_spec.get("species"),
            asset_spec.get("body_type"),
            asset_spec.get("facial_features"),
            asset_spec.get("hair_or_surface"),
            _labeled_list("主色", asset_spec.get("color_palette")),
            asset_spec.get("default_outfit"),
            _labeled_list("标志特征", asset_spec.get("signature_features")),
            _labeled_list("默认配件", asset_spec.get("default_accessories")),
        ]
    )
    fixed_prompt = _join_parts(
        [
            name,
            identity,
            age,
            gender,
            appearance,
            "稳定身份、体型比例、配色、默认服装和标志性特征，多镜头保持一致",
        ]
    )
    return appearance, fixed_prompt


def compile_scene_asset_fields(
    *,
    name: str,
    asset_spec: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    description = _join_parts(
        [
            asset_spec.get("location_type"),
            asset_spec.get("spatial_layout"),
            _labeled_list("固定地标", asset_spec.get("fixed_landmarks")),
            _labeled_list("子区域", asset_spec.get("zones")),
        ]
    )
    visual_style = _join_parts(
        [
            _labeled_list("材质", asset_spec.get("materials")),
            _labeled_list("固定色板", asset_spec.get("color_palette")),
        ]
    )
    # State variants are selected per source scene downstream. Folding every
    # variant into the legacy atmosphere field would apply day/night/weather
    # states to every shot and contaminate the stable scene identity.
    atmosphere = None
    fixed_prompt = _join_parts(
        [
            name,
            description,
            visual_style,
            "固定空间结构、地标位置、材质和色板，不写入临时时间、天气与光线状态",
        ]
    )
    return description, visual_style, atmosphere, fixed_prompt


def compile_prop_asset_fields(
    *,
    name: str,
    asset_spec: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    description = _join_parts(
        [
            asset_spec.get("shape"),
            asset_spec.get("dimensions"),
            _labeled_list("材质", asset_spec.get("materials")),
            _labeled_list("主色", asset_spec.get("color_palette")),
            _labeled_list("标志细节", asset_spec.get("signature_features")),
            asset_spec.get("scale_reference"),
        ]
    )
    visual_prompt = _join_parts(
        [
            name,
            description,
            "固定形状、尺寸比例、材质、配色和标志细节，多镜头保持一致",
        ]
    )
    return description, visual_prompt, _string(asset_spec.get("story_function")) or None


def asset_spec_has_source_evidence(asset_spec: Any) -> bool:
    return bool(_mapping(asset_spec).get("source_evidence"))


def asset_spec_reference_required(asset_spec: Any) -> bool:
    plan = _mapping(_mapping(asset_spec).get("reference_plan"))
    return _boolean(plan.get("required"), True)


def asset_spec_stable_identity_text(asset_spec: Any, entity_type: str) -> str:
    source = _mapping(asset_spec)
    fields = {
        "character": (
            "entity_kind",
            "species",
            "body_type",
            "facial_features",
            "hair_or_surface",
            "color_palette",
            "default_outfit",
            "signature_features",
            "default_accessories",
        ),
        "scene": (
            "location_type",
            "spatial_layout",
            "fixed_landmarks",
            "materials",
            "color_palette",
            "zones",
        ),
        "prop": (
            "shape",
            "dimensions",
            "materials",
            "color_palette",
            "signature_features",
            "scale_reference",
            "holder_relation",
        ),
    }.get(entity_type, ())
    values: list[str] = []
    for field in fields:
        value = source.get(field)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return "；".join(values)


def active_asset_state_text(asset_spec: Any, scene_no: Any) -> str:
    """Return only the state variants explicitly active in one source scene."""
    resolved_scene_no = _positive_int(scene_no)
    if resolved_scene_no is None:
        return ""
    active: list[str] = []
    for item in _state_variants(_mapping(asset_spec).get("state_variants")):
        if resolved_scene_no not in item["scene_nos"]:
            continue
        text = _join_parts([item.get("name"), item.get("description")])
        if text:
            active.append(text)
    return "；".join(active)


def _has_character_identity(source: dict[str, Any]) -> bool:
    return any(
        source.get(field)
        for field in (
            "species",
            "body_type",
            "facial_features",
            "hair_or_surface",
            "color_palette",
            "default_outfit",
            "signature_features",
        )
    )


def _has_prop_identity(source: dict[str, Any]) -> bool:
    return any(
        source.get(field)
        for field in ("shape", "dimensions", "materials", "color_palette", "signature_features")
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _string_list(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else re.split(r"[,，、\n]+", value) if isinstance(value, str) else []
    result: list[str] = []
    for raw_item in raw_items:
        item = _string(raw_item)
        if item and item not in result:
            result.append(item)
    return result


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _state_variants(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    variants: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(value, start=1):
        source = _mapping(item)
        name = _string(source.get("name"))
        description = _string(source.get("description"))
        if not name or not description:
            continue
        key = _variant_key(source.get("key"), fallback=f"state-{index}")
        if key in seen_keys:
            key = f"{key}-{index}"
        seen_keys.add(key)
        variants.append(
            {
                "key": key,
                "name": name,
                "description": description,
                "scene_nos": _positive_int_list(source.get("scene_nos")),
            }
        )
    return variants


def _variant_key(value: Any, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", _string(value).lower()).strip("-_")
    return normalized or fallback


def _positive_int_list(value: Any) -> list[int]:
    raw_items = (
        value
        if isinstance(value, list)
        else re.split(r"[,，、\n]+", value)
        if isinstance(value, str)
        else []
    )
    result: list[int] = []
    for raw_item in raw_items:
        parsed = _positive_int(raw_item)
        if parsed is not None and parsed not in result:
            result.append(parsed)
    return result


def _source_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    evidence: list[dict[str, Any]] = []
    for item in value:
        source = _mapping(item)
        text = _string(source.get("evidence"))
        if not text:
            continue
        evidence.append(
            {
                "scene_no": _positive_int(source.get("scene_no")),
                "evidence": text,
            }
        )
    return evidence


def _reference_plan(
    value: Any,
    *,
    required: bool,
    priority: str,
    default_views: list[str],
) -> dict[str, Any]:
    source = _mapping(value)
    raw_priority = _string(source.get("priority")).lower()
    views = _string_list(source.get("views"))
    resolved_required = _boolean(source.get("required"), required)
    return {
        "required": resolved_required,
        "priority": raw_priority if raw_priority in REFERENCE_PRIORITIES else priority,
        "views": views or (default_views if resolved_required else []),
    }


def _autofill_info(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        "applied": _boolean(source.get("applied"), False),
        "filled_fields": _string_list(source.get("filled_fields")),
        "normalized_fields": _string_list(source.get("normalized_fields")),
        "review_fields": _string_list(source.get("review_fields")),
    }


def _boolean(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _labeled_list(label: str, value: Any) -> str | None:
    items = _string_list(value)
    return f"{label}：{'、'.join(items)}" if items else None


def _join_parts(parts: list[Any]) -> str | None:
    cleaned = [_string(part).strip(" ，。；;") for part in parts if _string(part)]
    return "，".join(cleaned) if cleaned else None
