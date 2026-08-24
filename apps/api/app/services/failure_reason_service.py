from typing import Any


MEDIA_STAGES = {"images", "videos"}
MEDIA_TASK_KEYWORDS = ("image", "video")


def normalize_failure_reason(
    *,
    code: str | None = None,
    message: str | None = None,
    raw_response: dict[str, Any] | None = None,
    stage: str | None = None,
    task_type: str | None = None,
    provider: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    source_code = code or _string_value(raw_response, "code") or "unknown_error"
    source_message = message or _string_value(raw_response, "message") or source_code
    raw_retryable = raw_response.get("retryable") if raw_response else None
    is_retryable = bool(retryable if retryable is not None else raw_retryable)
    category = _classify_failure(source_code, source_message, raw_response)
    retry_policy = _retry_policy(
        category=category,
        retryable=is_retryable,
        stage=stage,
        task_type=task_type,
    )

    return {
        "category": category,
        "source_code": source_code,
        "message": source_message,
        "provider": provider,
        "stage": stage,
        "task_type": task_type,
        "retryable": is_retryable,
        "retry_policy": retry_policy,
        "user_message": _user_message(category),
        "suggested_action": _suggested_action(category, retry_policy),
    }


def attach_failure_reason(
    payload: dict[str, Any] | None,
    failure_reason: dict[str, Any],
) -> dict[str, Any]:
    result = dict(payload or {})
    result["failure_reason"] = failure_reason
    return result


def _classify_failure(
    code: str,
    message: str,
    raw_response: dict[str, Any] | None,
) -> str:
    text = " ".join([code, message, str(raw_response or {})]).lower()
    if _contains_any(text, ("timeout", "timed out", "408", "504", "gateway time")):
        return "provider_timeout"
    if _contains_any(text, ("rate_limit", "rate limit", "quota", "429", "too many requests")):
        return "provider_rate_limit"
    if _contains_any(text, ("unauthorized", "forbidden", "api key", "apikey", "401", "403", "auth")):
        return "provider_auth_failed"
    if _contains_any(text, ("safety", "policy", "moderation", "content_filter", "reject", "blocked")):
        return "provider_safety_reject"
    if _contains_any(text, ("missing_asset", "missing asset", "did not include", "no asset", "empty asset")):
        return "provider_missing_asset"
    if _contains_any(text, ("approved selected", "required before", "missing selected", "not found", "缺少")):
        return "missing_input_asset"
    if _contains_any(text, ("invalid", "parameter", "bad request", "422", "parse", "json", "format")):
        return "prompt_or_parameter_error"
    if _contains_any(text, ("provider", "502", "503", "failed")):
        return "provider_failed"
    if _contains_any(text, ("ffmpeg", "compose", "workflow", "stage")):
        return "workflow_failed"
    return "unknown"


def _retry_policy(
    *,
    category: str,
    retryable: bool,
    stage: str | None,
    task_type: str | None,
) -> str:
    is_media = stage in MEDIA_STAGES or any(keyword in (task_type or "") for keyword in MEDIA_TASK_KEYWORDS)
    if is_media:
        return "manual_regeneration"
    if category in {"provider_safety_reject", "provider_auth_failed", "missing_input_asset", "prompt_or_parameter_error"}:
        return "manual_fix_required"
    if retryable:
        return "auto_retry_allowed"
    return "manual_review"


def _user_message(category: str) -> str:
    messages = {
        "provider_timeout": "Provider 超时或网关超时。",
        "provider_rate_limit": "Provider 限流、配额或并发不足。",
        "provider_auth_failed": "Provider 鉴权或权限配置失败。",
        "provider_safety_reject": "Provider 内容安全策略拒绝了本次生成。",
        "provider_missing_asset": "Provider 返回中缺少可用媒体资产。",
        "missing_input_asset": "生成前置资产不完整或尚未确认。",
        "prompt_or_parameter_error": "Prompt、参数或返回格式不符合要求。",
        "provider_failed": "Provider 调用失败。",
        "workflow_failed": "本地流程或编排执行失败。",
        "unknown": "生成失败，原因暂未归类。",
    }
    return messages.get(category, messages["unknown"])


def _suggested_action(category: str, retry_policy: str) -> str:
    actions = {
        "provider_timeout": "稍后重试，或切换 Provider/降低并发。",
        "provider_rate_limit": "降低并发、等待配额恢复，或切换 Provider。",
        "provider_auth_failed": "检查 Provider Key、模型权限和环境变量配置。",
        "provider_safety_reject": "调整文本、Prompt 或参考图，避开安全拒绝点。",
        "provider_missing_asset": "检查 Provider 返回格式，必要时切换模型或 Provider。",
        "missing_input_asset": "先补齐并确认上游参考资产。",
        "prompt_or_parameter_error": "修正 Prompt、JSON 或生成参数后再运行。",
        "provider_failed": "查看 Provider 原始错误，必要时切换 Provider。",
        "workflow_failed": "查看本地日志，修复流程配置或输入文件。",
        "unknown": "查看原始错误并人工判断处理方式。",
    }
    if retry_policy == "manual_regeneration" and category not in {
        "provider_auth_failed",
        "provider_safety_reject",
        "missing_input_asset",
        "prompt_or_parameter_error",
    }:
        return "检查失败原因和输入资产后，由用户手动选择单项或批量重新生成。"
    return actions.get(category, actions["unknown"])


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _string_value(payload: dict[str, Any] | None, key: str) -> str | None:
    if not payload:
        return None
    value = payload.get(key)
    return str(value) if value is not None else None
