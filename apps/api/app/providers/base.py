from abc import ABC, abstractmethod
from decimal import Decimal

from app.providers.types import ProviderRequest, ProviderResponse, ProviderType


class ProviderAdapter(ABC):
    name: str
    type: ProviderType
    model: str
    capabilities: list[str]
    native_audio: bool = False
    reference_images: bool = False
    reference_videos: bool = False
    reference_audio: bool = False
    multi_reference: bool = False
    smart_duration: bool = False
    min_duration_sec: float | None = None
    max_duration_sec: float | None = None
    supported_resolutions: list[str] = []

    def validate_config(self) -> None:
        return None

    def estimate_cost(self, request: ProviderRequest) -> Decimal:
        return Decimal("0")

    @abstractmethod
    def submit(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError

    def poll(self, provider_task_id: str) -> ProviderResponse:
        raise NotImplementedError("Provider does not support polling")

    def supports_polling(self) -> bool:
        return "poll" in self.capabilities or "async" in self.capabilities

    def cancel(self, provider_task_id: str) -> None:
        raise NotImplementedError("Provider does not support cancellation")

    def normalize_response(self, raw_response: dict) -> ProviderResponse:
        raise NotImplementedError("Provider does not expose raw response normalization")

    def descriptor(self) -> dict:
        return {
            "name": self.name,
            "type": self.type.value,
            "model": self.model,
            "capabilities": self.capabilities,
            "supports_polling": self.supports_polling(),
            "native_audio": self.native_audio,
            "reference_images": self.reference_images,
            "reference_videos": self.reference_videos,
            "reference_audio": self.reference_audio,
            "multi_reference": self.multi_reference,
            "smart_duration": self.smart_duration,
            "min_duration_sec": self.min_duration_sec,
            "max_duration_sec": self.max_duration_sec,
            "supported_resolutions": list(self.supported_resolutions),
        }
