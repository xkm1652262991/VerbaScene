import stat
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db import SessionLocal
from app.models import ProviderConfig
from app.providers.defaults import provider_registry
from app.providers.types import ProviderType
from app.providers.types import ProviderRequest
from app.providers.wan_i2v import _local_size, _video_seed
from app.schemas.provider import ProviderConfigUpsert
from app.services.provider_config_service import (
    list_runtime_provider_configs,
    reload_runtime_provider_configs,
    reset_runtime_provider_config,
    upsert_runtime_provider_config,
)
from app.services.provider_secret_store import provider_secret_file_path


class ProviderRuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.secret_directory = tempfile.TemporaryDirectory()
        self.original_provider_secret_file = settings.provider_secret_file
        settings.provider_secret_file = str(Path(self.secret_directory.name) / "provider-secrets.json")
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
        settings.provider_secret_file = self.original_provider_secret_file
        self.secret_directory.cleanup()
        # Runtime settings and adapters are process globals. Restore the real
        # project configuration so this test cannot leak into later cases.
        with SessionLocal() as real_db:
            reload_runtime_provider_configs(real_db)

    def test_saved_config_refreshes_current_process_without_secret_echo(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="mock",
                model_name="runtime-config-test-image",
                default_params={
                    "size": "1024x576",
                    "candidate_count": 2,
                    "steps": 12,
                    "guidance_scale": 3.2,
                },
            ),
        )

        self.assertEqual(saved["source"], "saved")
        self.assertFalse(saved["restart_required"])
        self.assertNotIn("api_key", saved)
        self.assertEqual(settings.image_provider, "mock")
        self.assertEqual(settings.image_model, "runtime-config-test-image")
        self.assertEqual(settings.image_size, "1024x576")
        self.assertEqual(
            provider_registry.get(ProviderType.IMAGE, "mock").model,
            "runtime-config-test-image",
        )

        listed = {
            item["provider_type"]: item
            for item in list_runtime_provider_configs(self.db)
        }
        self.assertEqual(listed["image"]["model_name"], "runtime-config-test-image")
        self.assertEqual(listed["image"]["configuration_status"], "ready")

    def test_image_reference_capability_is_saved_and_refreshes_adapter(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="openai_image",
                model_name="reference-capable-image",
                base_url="http://image.test",
                api_key_mode="none",
                default_params={
                    "size": "1024x1024",
                    "reference_transport": "hybrid",
                    "supports_references": True,
                },
            ),
        )

        provider = provider_registry.get(ProviderType.IMAGE, "openai_image")
        self.assertTrue(settings.image_supports_references)
        self.assertEqual(saved["default_params"]["reference_transport"], "hybrid")
        self.assertTrue(saved["default_params"]["supports_references"])
        self.assertIn("reference_payload", provider.capabilities)

    def test_image_reference_transport_rejects_unknown_mode(self):
        with self.assertRaises(HTTPException) as raised:
            upsert_runtime_provider_config(
                self.db,
                "image",
                ProviderConfigUpsert(
                    provider_name="openai_image",
                    model_name="reference-capable-image",
                    base_url="http://image.test",
                    api_key_mode="none",
                    default_params={"reference_transport": "pretend"},
                ),
            )

        self.assertEqual(raised.exception.status_code, 422)

    def test_image_quality_medium_is_saved_without_forcing_steps(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="openai_image",
                model_name="flux2-klein-base-4b",
                base_url="http://image.test",
                api_key_mode="none",
                default_params={
                    "size": "1280x720",
                    "quality": "medium",
                    "guidance_scale": 4.0,
                },
            ),
        )

        self.assertEqual(saved["default_params"]["quality"], "medium")
        self.assertNotIn("steps", saved["default_params"])
        self.assertEqual(settings.image_quality, "medium")

    def test_qwen_musubi_runtime_config_uses_native_endpoint_without_api_key(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="qwen_image_musubi",
                model_name="qwen-image-fig-lightning8-v2",
                base_url="http://192.0.2.22:18108",
                api_key_mode="none",
                default_params={
                    "size": "1024x576",
                    "steps": 8,
                    "guidance_scale": 1.0,
                    "reference_transport": "prompt",
                    "endpoint": "/generate",
                },
            ),
        )

        provider = provider_registry.get(ProviderType.IMAGE, "qwen_image_musubi")
        self.assertEqual(saved["configuration_status"], "ready")
        self.assertEqual(saved["api_key_mode"], "none")
        self.assertEqual(settings.image_provider, "qwen_image_musubi")
        self.assertEqual(settings.qwen_image_musubi_base_url, "http://192.0.2.22:18108")
        self.assertEqual(provider.base_url, "http://192.0.2.22:18108")
        self.assertEqual(provider.model, "qwen-image-fig-lightning8-v2")
        self.assertNotIn("reference_payload", provider.capabilities)

    def test_dashscope_image_runtime_config_uses_dashscope_key_and_native_adapter(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="dashscope_image",
                model_name="wan2.6-t2i",
                base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
                api_key_mode="direct",
                api_key="dashscope-test-key",
                default_params={
                    "size": "1696x960",
                    "reference_transport": "none",
                    "prompt_extend": True,
                    "watermark": False,
                },
            ),
        )

        provider = provider_registry.get(ProviderType.IMAGE, "dashscope_image")
        self.assertEqual(saved["configuration_status"], "ready")
        self.assertEqual(settings.image_provider, "dashscope_image")
        self.assertEqual(settings.dashscope_image_model, "wan2.6-t2i")
        self.assertEqual(provider.base_url, "https://workspace.cn-beijing.maas.aliyuncs.com")
        self.assertEqual(provider.api_key, "dashscope-test-key")
        self.assertEqual(provider.capabilities, ["text_to_image", "async_api", "poll", "polling"])

    def test_image_quality_rejects_unknown_value(self):
        with self.assertRaises(HTTPException) as raised:
            upsert_runtime_provider_config(
                self.db,
                "image",
                ProviderConfigUpsert(
                    provider_name="openai_image",
                    model_name="flux2-klein-base-4b",
                    base_url="http://image.test",
                    api_key_mode="none",
                    default_params={"quality": "ultra"},
                ),
            )

        self.assertEqual(raised.exception.status_code, 422)

    def test_reset_restores_environment_configuration(self):
        upsert_runtime_provider_config(
            self.db,
            "video",
            ProviderConfigUpsert(
                provider_name="mock",
                model_name="runtime-config-test-video",
                default_params={"frames": 33, "steps": 8, "guidance": 2.5, "fps": 12},
            ),
        )

        reset = reset_runtime_provider_config(self.db, "video")

        self.assertEqual(reset["source"], "environment")
        self.assertNotEqual(settings.video_model, "runtime-config-test-video")

    def test_video_size_and_seed_defaults_are_applied_and_can_be_overridden(self):
        upsert_runtime_provider_config(
            self.db,
            "video",
            ProviderConfigUpsert(
                provider_name="mock",
                model_name="runtime-config-test-video",
                default_params={"size": "1280*720", "seed": 20260720},
            ),
        )
        default_request = ProviderRequest(
            project_id="provider-test",
            task_id="video-defaults",
            model="runtime-config-test-video",
            prompt="test",
        )
        override_request = ProviderRequest(
            project_id="provider-test",
            task_id="video-overrides",
            model="runtime-config-test-video",
            prompt="test",
            params={"size": "960x544", "seed": 42},
        )

        self.assertEqual(settings.wan_i2v_size, "1280*720")
        self.assertEqual(settings.wan_i2v_seed, 20260720)
        self.assertEqual(_local_size(default_request), "1280*720")
        self.assertEqual(_video_seed(default_request), 20260720)
        self.assertEqual(_local_size(override_request), "960*544")
        self.assertEqual(_video_seed(override_request), 42)

    def test_ltx23_runtime_config_uses_provider_specific_defaults_without_api_key(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "video",
            ProviderConfigUpsert(
                provider_name="ltx23_api",
                model_name="LTX-2.3",
                base_url="http://192.0.2.25:18109",
                api_key_mode="none",
                default_params={
                    "size": "1024x576",
                    "frames": 81,
                    "steps": 8,
                    "guidance": 1,
                    "fps": 16,
                    "strength": 0.78,
                },
            ),
        )

        provider = provider_registry.get(ProviderType.VIDEO, "ltx23_api")
        self.assertEqual(saved["api_key_mode"], "none")
        self.assertFalse(saved["api_key_configured"])
        self.assertEqual(settings.video_provider, "ltx23_api")
        self.assertEqual(settings.ltx23_api_base_url, "http://192.0.2.25:18109")
        self.assertEqual(settings.ltx23_size, "1024x576")
        self.assertEqual(settings.ltx23_frames, 81)
        self.assertEqual(settings.ltx23_steps, 8)
        self.assertEqual(settings.ltx23_cfg, 1)
        self.assertEqual(settings.ltx23_strength, 0.78)
        self.assertEqual(provider.model, "LTX-2.3")
        self.assertIn("text_to_video", provider.capabilities)
        self.assertIn("image_to_video", provider.capabilities)

    def test_seedance2_runtime_config_uses_write_only_key_and_native_capabilities(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "video",
            ProviderConfigUpsert(
                provider_name="seedance2_api",
                model_name="doubao-seedance-2-0-260128",
                base_url="https://ark.test/api/v3",
                api_key_mode="direct",
                api_key="unit-test-secret",
                default_params={
                    "resolution": "480p",
                    "generate_audio": True,
                    "watermark": False,
                },
            ),
        )

        provider = provider_registry.get(ProviderType.VIDEO, "seedance2_api")
        self.assertEqual(saved["configuration_status"], "ready")
        self.assertEqual(saved["api_key_mode"], "direct")
        self.assertTrue(saved["api_key_configured"])
        self.assertNotIn("api_key", saved)
        self.assertEqual(settings.video_provider, "seedance2_api")
        self.assertEqual(
            settings.seedance2_model,
            "doubao-seedance-2-0-260128",
        )
        self.assertEqual(settings.seedance2_resolution, "480p")
        self.assertTrue(provider.native_audio)
        self.assertTrue(provider.reference_images)
        self.assertTrue(provider.reference_videos)
        self.assertTrue(provider.reference_audio)
        self.assertTrue(provider.multi_reference)
        self.assertEqual(provider.max_duration_sec, 15)
        self.assertEqual(
            provider.supported_resolutions,
            ["480p", "720p", "1080p"],
        )

    def test_wan_official_runtime_rejects_non_service_parameters(self):
        for default_params in (
            {"protocol": "official", "frames": 80, "steps": 4, "guidance": 1},
            {"protocol": "official", "frames": 81, "steps": 40, "guidance": 1},
            {"protocol": "official", "frames": 81, "steps": 4, "guidance": 3.5},
        ):
            with self.subTest(default_params=default_params), self.assertRaises(HTTPException) as raised:
                upsert_runtime_provider_config(
                    self.db,
                    "video",
                    ProviderConfigUpsert(
                        provider_name="wan2_i2v_api",
                        model_name="Wan-AI/Wan2.2-I2V-A14B",
                        base_url="http://192.0.2.25:18083",
                        api_key_mode="direct",
                        api_key="test-key",
                        default_params=default_params,
                    ),
                )
            self.assertEqual(raised.exception.status_code, 422)

    def test_rejects_params_that_runtime_does_not_apply(self):
        with self.assertRaises(HTTPException) as raised:
            upsert_runtime_provider_config(
                self.db,
                "llm",
                ProviderConfigUpsert(
                    provider_name="mock",
                    model_name="mock-llm",
                    default_params={"pretend_setting": True},
                ),
            )

        self.assertEqual(raised.exception.status_code, 422)

    def test_direct_api_key_is_write_only_persisted_and_applied(self):
        saved = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="openai_image",
                model_name="flux2-klein-base-4b",
                base_url="http://192.0.2.19:8001",
                api_key_mode="direct",
                api_key="direct-secret-for-test",
                default_params={"size": "1536x1024"},
            ),
        )

        self.assertEqual(saved["api_key_mode"], "direct")
        self.assertTrue(saved["api_key_configured"])
        self.assertIsNone(saved["api_key_ref"])
        self.assertNotIn("api_key", saved)
        self.assertEqual(settings.image_api_key, "direct-secret-for-test")
        stored_config = self.db.query(ProviderConfig).filter(ProviderConfig.enabled.is_(True)).one()
        self.assertNotEqual(stored_config.api_key_ref, "direct-secret-for-test")
        self.assertEqual(stat.S_IMODE(provider_secret_file_path().stat().st_mode), 0o600)

        listed = {
            item["provider_type"]: item
            for item in list_runtime_provider_configs(self.db)
        }
        self.assertNotIn("api_key", listed["image"])
        self.assertTrue(listed["image"]["api_key_configured"])

    def test_direct_api_key_can_be_preserved_or_explicitly_cleared(self):
        initial = ProviderConfigUpsert(
            provider_name="openai_image",
            model_name="flux2-klein-base-4b",
            base_url="http://192.0.2.19:8001",
            api_key_mode="direct",
            api_key="direct-secret-for-test",
            default_params={"size": "1536x1024"},
        )
        upsert_runtime_provider_config(self.db, "image", initial)

        preserved = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="openai_image",
                model_name="flux2-klein-base-4b-v2",
                base_url="http://192.0.2.19:8001",
                api_key_mode="direct",
                default_params={"size": "1536x1024"},
            ),
        )
        self.assertTrue(preserved["api_key_configured"])
        self.assertEqual(settings.image_api_key, "direct-secret-for-test")

        cleared = upsert_runtime_provider_config(
            self.db,
            "image",
            ProviderConfigUpsert(
                provider_name="openai_image",
                model_name="flux2-klein-base-4b-v2",
                base_url="http://192.0.2.19:8001",
                api_key_mode="none",
                default_params={"size": "1536x1024"},
            ),
        )
        self.assertEqual(cleared["api_key_mode"], "none")
        self.assertFalse(cleared["api_key_configured"])
        self.assertIsNone(settings.image_api_key)

    def test_direct_api_key_is_required_on_first_save(self):
        with self.assertRaises(HTTPException) as raised:
            upsert_runtime_provider_config(
                self.db,
                "image",
                ProviderConfigUpsert(
                    provider_name="openai_image",
                    model_name="flux2-klein-base-4b",
                    base_url="http://192.0.2.19:8001",
                    api_key_mode="direct",
                    default_params={"size": "1536x1024"},
                ),
            )

        self.assertEqual(raised.exception.status_code, 422)

    def test_provider_config_rejects_unknown_fields_instead_of_silently_ignoring_them(self):
        with self.assertRaises(ValidationError):
            ProviderConfigUpsert.model_validate({
                "provider_name": "openai_image",
                "model_name": "flux2-klein-base-4b",
                "api_key_mode": "direct",
                "api_key": "direct-secret-for-test",
                "unsupported_credential_field": "must-not-be-ignored",
            })


if __name__ == "__main__":
    unittest.main()
