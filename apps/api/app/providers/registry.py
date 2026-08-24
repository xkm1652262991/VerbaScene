from app.providers.base import ProviderAdapter
from app.providers.errors import ProviderNotFoundError
from app.providers.types import ProviderType


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[tuple[ProviderType, str], ProviderAdapter] = {}

    def register(self, provider: ProviderAdapter) -> None:
        self._providers[(provider.type, provider.name)] = provider

    def replace(self, providers: list[ProviderAdapter]) -> None:
        """Atomically replace the runtime provider snapshot.

        Services import the registry object directly, so rebinding the module
        global would leave those imports pointing at a stale registry. Swapping
        the internal mapping keeps every caller on the same live object.
        """
        self._providers = {
            (provider.type, provider.name): provider
            for provider in providers
        }

    def get(self, provider_type: ProviderType, provider_name: str) -> ProviderAdapter:
        provider = self._providers.get((provider_type, provider_name))
        if provider is None:
            raise ProviderNotFoundError(
                f"Provider not found: {provider_type.value}/{provider_name}",
            )
        return provider

    def list(self) -> list[ProviderAdapter]:
        return list(self._providers.values())
