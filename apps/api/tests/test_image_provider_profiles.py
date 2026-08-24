import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import ProviderConfig
from app.services.image_provider_profile_service import (
    list_image_provider_profiles,
    resolve_image_provider_selection,
)


class ImageProviderProfileTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ProviderConfig.__table__.create(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_profile(
        self,
        *,
        provider_name: str,
        model_name: str,
        base_url: str,
        enabled: bool,
        default_params: dict | None = None,
    ) -> ProviderConfig:
        profile = ProviderConfig(
            workspace_id=None,
            provider_type="image",
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
            api_key_ref="__NO_API_KEY__",
            default_params=default_params or {},
            enabled=enabled,
        )
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def test_lists_saved_profiles_and_reference_capability(self):
        flux = self._add_profile(
            provider_name="openai_image",
            model_name="flux-profile",
            base_url="http://flux.test",
            enabled=True,
            default_params={"supports_references": True, "reference_transport": "hybrid"},
        )

        profiles = list_image_provider_profiles(self.db)
        saved = next(item for item in profiles if item["id"] == flux.id)

        self.assertTrue(saved["is_default"])
        self.assertTrue(saved["supports_references"])
        self.assertEqual(saved["configuration_status"], "ready")

    def test_resolves_two_profiles_without_mutating_global_image_settings(self):
        original_provider = settings.image_provider
        original_model = settings.image_model
        original_base_url = settings.image_base_url
        qwen = self._add_profile(
            provider_name="custom_image_http",
            model_name="qwen-local",
            base_url="http://qwen.test",
            enabled=False,
        )
        flux = self._add_profile(
            provider_name="openai_image",
            model_name="flux-api",
            base_url="http://flux.test",
            enabled=True,
            default_params={"supports_references": True},
        )

        qwen_selection = resolve_image_provider_selection(self.db, qwen.id)
        flux_selection = resolve_image_provider_selection(self.db, flux.id)

        self.assertEqual(qwen_selection.provider.model, "qwen-local")
        self.assertEqual(qwen_selection.provider.base_url, "http://qwen.test")
        self.assertEqual(flux_selection.provider.model, "flux-api")
        self.assertEqual(flux_selection.provider.base_url, "http://flux.test")
        self.assertNotIn("reference_payload", qwen_selection.provider.capabilities)
        self.assertIn("reference_payload", flux_selection.provider.capabilities)
        self.assertEqual(settings.image_provider, original_provider)
        self.assertEqual(settings.image_model, original_model)
        self.assertEqual(settings.image_base_url, original_base_url)

    def test_rejects_unknown_profile(self):
        with self.assertRaises(HTTPException) as raised:
            resolve_image_provider_selection(self.db, "missing-profile")

        self.assertEqual(raised.exception.status_code, 404)

    def test_resolves_native_qwen_musubi_profile(self):
        qwen = self._add_profile(
            provider_name="qwen_image_musubi",
            model_name="qwen-image-fig-lightning8-v2",
            base_url="http://192.0.2.22:18108",
            enabled=False,
            default_params={
                "size": "1024x576",
                "steps": 8,
                "guidance_scale": 1.0,
                "reference_transport": "prompt",
            },
        )

        selection = resolve_image_provider_selection(self.db, qwen.id)

        self.assertEqual(selection.provider.name, "qwen_image_musubi")
        self.assertEqual(selection.provider.model, "qwen-image-fig-lightning8-v2")
        self.assertEqual(selection.provider.base_url, "http://192.0.2.22:18108")
        self.assertEqual(selection.default_params["steps"], 8)
        self.assertNotIn("reference_payload", selection.provider.capabilities)


if __name__ == "__main__":
    unittest.main()
