from app.core.config import settings
from app.providers.comfyui_flux import build_comfyui_flux_providers
from app.providers.custom_image_http import build_custom_image_http_providers
from app.providers.dashscope import build_dashscope_providers
from app.providers.dashscope_image import build_dashscope_image_providers
from app.providers.gemini import build_gemini_providers
from app.providers.ltx23 import build_ltx23_providers
from app.providers.minimax_h3 import build_minimax_h3_providers
from app.providers.mock import build_mock_providers
from app.providers.openai_compatible_llm import build_openai_compatible_llm_providers
from app.providers.openai_image import build_openai_image_providers
from app.providers.qwen_image_musubi import build_qwen_image_musubi_providers
from app.providers.registry import ProviderRegistry
from app.providers.seedance2 import build_seedance2_providers
from app.providers.wan_i2v import build_wan_i2v_providers


def build_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    for provider in build_mock_providers():
        registry.register(provider)
    # Register the complete adapter catalogue. Configuration validation happens
    # when a provider is selected or tested; hiding an unconfigured adapter made
    # it impossible for the frontend to select and configure it.
    for builder in (
        build_dashscope_providers,
        build_dashscope_image_providers,
        build_openai_compatible_llm_providers,
        build_gemini_providers,
        build_openai_image_providers,
        build_custom_image_http_providers,
        build_qwen_image_musubi_providers,
        build_comfyui_flux_providers,
        build_seedance2_providers,
        build_ltx23_providers,
        build_minimax_h3_providers,
        build_wan_i2v_providers,
    ):
        for provider in builder():
            registry.register(provider)
    return registry


provider_registry = build_provider_registry()


def refresh_provider_registry() -> ProviderRegistry:
    """Rebuild adapters while preserving the imported registry identity."""
    next_registry = build_provider_registry()
    provider_registry.replace(next_registry.list())
    return provider_registry
