import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from app.core.config import settings
from app.providers.base import ProviderAdapter
from app.providers.types import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
    ProviderUsage,
)


class DashScopeLLMProvider(ProviderAdapter):
    name = "dashscope"
    type = ProviderType.LLM
    capabilities = ["text_generation", "json_generation", "openai_compatible_chat"]

    def __init__(self) -> None:
        self.model = settings.dashscope_model or settings.llm_model
        self.api_key = settings.dashscope_api_key or settings.llm_api_key
        self.base_url = settings.dashscope_base_url.rstrip("/")

    def validate_config(self) -> None:
        if not self.api_key:
            raise ValueError("DASHSCOPE_API_KEY or LLM_API_KEY is required for DashScope LLM provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:dashscope-llm"))
        try:
            self.validate_config()
            raw_response = self._chat_completion(request)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id=provider_task_id,
                raw_response={},
                error=ProviderError(
                    error_code="dashscope_llm_failed",
                    error_message=str(exc),
                    is_retryable=True,
                ),
            )

        text = _extract_text(raw_response)
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            raw_response={
                "text": text,
                "model": self.model,
                "provider_response": raw_response,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def _chat_completion(self, request: ProviderRequest) -> dict:
        payload = {
            "model": request.model or self.model,
            "messages": [
                {
                    "role": "system",
                    "content": request.system_prompt
                    or "你是专业的动画英语短剧生产助手，输出应稳定、结构化并严格遵守当前任务合同。",
                },
                {
                    "role": "user",
                    "content": request.prompt,
                },
            ],
            "temperature": request.params.get("temperature", 0.7),
        }
        if request.params.get("response_format"):
            payload["response_format"] = request.params["response_format"]
        http_request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))


def _extract_text(raw_response: dict) -> str:
    choices = raw_response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def build_dashscope_providers() -> list[ProviderAdapter]:
    return [DashScopeLLMProvider()]
