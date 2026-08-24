import base64
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from app.core.config import settings
from app.providers.custom_image_http import CustomImageHTTPProvider
from app.providers.image_reference_payload import materialize_reference_data_uris
from app.providers.openai_image import OpenAICompatibleImageProvider
from app.providers.types import ProviderRequest, ProviderStatus
from app.services.image_consistency_service import evaluate_image_consistency


def _image_data_uri(color: tuple[int, int, int], size: tuple[int, int] = (160, 90)) -> str:
    image = Image.new("RGB", size, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


class ImageReferencePayloadTests(unittest.TestCase):
    def test_local_storage_reference_is_embedded_as_data_uri_for_remote_provider(self):
        original_storage_root = settings.storage_root
        original_public_base_url = settings.public_storage_base_url
        source_uri = _image_data_uri((220, 130, 40))
        with tempfile.TemporaryDirectory() as directory:
            reference_path = Path(directory) / "projects" / "project-id" / "reference.png"
            reference_path.parent.mkdir(parents=True)
            reference_path.write_bytes(base64.b64decode(source_uri.split(",", 1)[1]))
            try:
                settings.storage_root = directory
                settings.public_storage_base_url = "http://127.0.0.1:8000/storage"
                materialized = materialize_reference_data_uris(
                    ["http://127.0.0.1:8000/storage/projects/project-id/reference.png"]
                )
            finally:
                settings.storage_root = original_storage_root
                settings.public_storage_base_url = original_public_base_url

        self.assertEqual(len(materialized), 1)
        self.assertTrue(materialized[0].startswith("data:image/png;base64,"))
        self.assertNotIn("127.0.0.1", materialized[0])

    def test_openai_image_omits_reference_fields_without_declared_capability(self):
        original_base_url = settings.image_base_url
        original_model = settings.image_model
        original_supports_references = settings.image_supports_references
        original_quality = settings.image_quality
        original_steps = settings.image_num_inference_steps
        reference_uri = _image_data_uri((220, 130, 40))
        generated_b64 = _image_data_uri((210, 125, 45)).split(",", 1)[1]
        captured_payload: dict = {}
        captured_url = ""

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"data": [{"b64_json": generated_b64}]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            nonlocal captured_url
            del timeout
            captured_url = request.full_url
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        try:
            settings.image_base_url = "http://image.test"
            settings.image_model = "test-image"
            settings.image_supports_references = False
            settings.image_quality = "medium"
            settings.image_num_inference_steps = None
            provider = OpenAICompatibleImageProvider()
            with patch("app.providers.openai_image.urlopen", fake_urlopen):
                response = provider.submit(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="test-image",
                        prompt="橘猫在清晨卧室",
                        references=[reference_uri],
                        params={
                            "asset_reference_transport": "hybrid",
                            "asset_reference_metadata": {"reference_mode": "entity_reference"},
                        },
                    )
                )
        finally:
            settings.image_base_url = original_base_url
            settings.image_model = original_model
            settings.image_supports_references = original_supports_references
            settings.image_quality = original_quality
            settings.image_num_inference_steps = original_steps

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertTrue(captured_url.endswith("/v1/images/generations"))
        self.assertEqual(captured_payload["quality"], "medium")
        self.assertNotIn("steps", captured_payload)
        self.assertNotIn("num_inference_steps", captured_payload)
        self.assertNotIn("references", captured_payload)
        self.assertNotIn("reference_metadata", captured_payload)

    def test_openai_image_uses_multipart_edits_when_capability_is_enabled(self):
        original_base_url = settings.image_base_url
        original_model = settings.image_model
        original_supports_references = settings.image_supports_references
        original_quality = settings.image_quality
        original_steps = settings.image_num_inference_steps
        reference_uri = _image_data_uri((220, 130, 40))
        generated_b64 = _image_data_uri((210, 125, 45)).split(",", 1)[1]
        captured_request: dict = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"data": [{"b64_json": generated_b64}]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            del timeout
            captured_request.update(
                {
                    "url": request.full_url,
                    "content_type": request.headers.get("Content-type"),
                    "body": request.data,
                }
            )
            return FakeResponse()

        try:
            settings.image_base_url = "http://image.test"
            settings.image_model = "test-image"
            settings.image_supports_references = True
            settings.image_quality = "medium"
            settings.image_num_inference_steps = None
            provider = OpenAICompatibleImageProvider()
            with patch("app.providers.openai_image.urlopen", fake_urlopen):
                response = provider.submit(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="test-image",
                        prompt="橘猫在清晨卧室",
                        references=[reference_uri],
                        params={
                            "asset_reference_transport": "hybrid",
                            "asset_reference_metadata": {"reference_mode": "entity_reference"},
                        },
                    )
                )
        finally:
            settings.image_base_url = original_base_url
            settings.image_model = original_model
            settings.image_supports_references = original_supports_references
            settings.image_quality = original_quality
            settings.image_num_inference_steps = original_steps

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertIn("reference_payload", provider.capabilities)
        self.assertTrue(captured_request["url"].endswith("/v1/images/edits"))
        self.assertTrue(str(captured_request["content_type"]).startswith("multipart/form-data; boundary="))
        multipart_body = captured_request["body"].decode("latin-1")
        self.assertIn('name="image[]"; filename="reference-1.png"', multipart_body)
        self.assertIn('name="quality"\r\n\r\nmedium', multipart_body)
        self.assertNotIn('name="steps"', multipart_body)
        self.assertEqual(response.raw_response["request_reference_summary"]["payload_format"], "multipart/form-data")
        self.assertEqual(response.raw_response["request_reference_summary"]["reference_image_count"], 1)

    def test_openai_image_folds_bounded_negative_constraints_into_prompt(self):
        generated_b64 = _image_data_uri((210, 125, 45)).split(",", 1)[1]
        captured_payload: dict = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"data": [{"b64_json": generated_b64}]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            del timeout
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        provider = OpenAICompatibleImageProvider(
            model="test-image",
            base_url="http://image.test",
            default_params={"negative_prompt_transport": "prompt"},
        )
        with patch("app.providers.openai_image.urlopen", fake_urlopen):
            response = provider.submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id-negative",
                    model="test-image",
                    prompt="特写：只显示手指和羽毛，无字幕",
                    negative_prompt="字幕，红色书包，完整人物，远景",
                    params={"negative_prompt_transport": "prompt"},
                    metadata={"entity_type": "shot"},
                )
            )

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertNotIn("negative_prompt", captured_payload)
        self.assertIn("补充排除（低于镜头内容优先级）", captured_payload["prompt"])
        self.assertIn("红色书包", captured_payload["prompt"])
        self.assertIn("完整人物", captured_payload["prompt"])
        self.assertNotIn("：字幕、", captured_payload["prompt"])

    def test_openai_image_explicit_steps_override_quality_mapping(self):
        original_base_url = settings.image_base_url
        original_model = settings.image_model
        original_quality = settings.image_quality
        original_steps = settings.image_num_inference_steps
        generated_b64 = _image_data_uri((210, 125, 45)).split(",", 1)[1]
        captured_payload: dict = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"data": [{"b64_json": generated_b64}]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            del timeout
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        try:
            settings.image_base_url = "http://image.test"
            settings.image_model = "test-image"
            settings.image_quality = "medium"
            settings.image_num_inference_steps = None
            provider = OpenAICompatibleImageProvider()
            with patch("app.providers.openai_image.urlopen", fake_urlopen):
                response = provider.submit(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="test-image",
                        prompt="橘猫在清晨卧室",
                        params={"quality": "medium", "steps": 50},
                    )
                )
        finally:
            settings.image_base_url = original_base_url
            settings.image_model = original_model
            settings.image_quality = original_quality
            settings.image_num_inference_steps = original_steps

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(captured_payload["quality"], "medium")
        self.assertEqual(captured_payload["steps"], 50)
        self.assertNotIn("num_inference_steps", captured_payload)

    def test_custom_image_http_sends_reference_images_in_hybrid_mode(self):
        original_base_url = settings.image_base_url
        original_model = settings.image_model
        original_response_format = settings.image_response_format
        original_supports_references = settings.image_supports_references
        reference_uri = _image_data_uri((220, 130, 40))
        generated_uri = _image_data_uri((210, 125, 45))
        generated_b64 = generated_uri.split(",", 1)[1]
        captured_payload: dict = {}

        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"b64_json": generated_b64, "width": 160, "height": 90}).encode("utf-8")

        def fake_urlopen(request, timeout):
            del timeout
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        try:
            settings.image_base_url = "http://image.test"
            settings.image_model = "test-image"
            settings.image_response_format = "b64_json"
            settings.image_supports_references = True
            provider = CustomImageHTTPProvider()
            with patch("app.providers.custom_image_http.urlopen", fake_urlopen):
                response = provider.submit(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="test-image",
                        prompt="橘猫在清晨卧室",
                        references=[reference_uri],
                        params={
                            "asset_reference_transport": "hybrid",
                            "asset_reference_metadata": {"reference_mode": "entity_reference"},
                        },
                        metadata={
                            "asset_resolution": {
                                "reference_assets": [
                                    {
                                        "uri": reference_uri,
                                        "asset_id": "ref-1",
                                        "reference_role": "character_main_ref",
                                        "asset_role": "character_main_ref",
                                        "entity_type": "character",
                                    }
                                ]
                            }
                        },
                    )
                )
        finally:
            settings.image_base_url = original_base_url
            settings.image_model = original_model
            settings.image_response_format = original_response_format
            settings.image_supports_references = original_supports_references

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(captured_payload["references"], [reference_uri])
        self.assertEqual(captured_payload["reference_metadata"]["reference_mode"], "entity_reference")
        self.assertEqual(captured_payload["reference_images"][0]["reference_role"], "character_main_ref")
        self.assertTrue(captured_payload["reference_images"][0]["b64_json"])
        self.assertEqual(response.raw_response["request_reference_summary"]["reference_payload_count"], 1)
        self.assertEqual(response.raw_response["request_reference_summary"]["reference_image_count"], 1)

    def test_custom_image_http_reference_summary_counts_only_readable_images(self):
        original_base_url = settings.image_base_url
        original_model = settings.image_model
        original_response_format = settings.image_response_format
        original_supports_references = settings.image_supports_references
        reference_uri = _image_data_uri((220, 130, 40))
        generated_uri = _image_data_uri((210, 125, 45))
        generated_b64 = generated_uri.split(",", 1)[1]

        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"b64_json": generated_b64, "width": 160, "height": 90}).encode("utf-8")

        try:
            settings.image_base_url = "http://image.test"
            settings.image_model = "test-image"
            settings.image_response_format = "b64_json"
            settings.image_supports_references = True
            provider = CustomImageHTTPProvider()
            with patch("app.providers.custom_image_http.urlopen", return_value=FakeResponse()):
                response = provider.submit(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="test-image",
                        prompt="橘猫在清晨卧室",
                        references=[reference_uri, "bad://reference"],
                        params={"asset_reference_transport": "hybrid"},
                        metadata={
                            "asset_resolution": {
                                "reference_assets": [
                                    {
                                        "uri": reference_uri,
                                        "reference_role": "character_main_ref",
                                    },
                                    {
                                        "uri": "bad://reference",
                                        "reference_role": "scene_ref",
                                    },
                                ]
                            }
                        },
                    )
                )
        finally:
            settings.image_base_url = original_base_url
            settings.image_model = original_model
            settings.image_response_format = original_response_format
            settings.image_supports_references = original_supports_references

        summary = response.raw_response["request_reference_summary"]
        self.assertEqual(summary["reference_count"], 2)
        self.assertEqual(summary["reference_payload_count"], 2)
        self.assertEqual(summary["reference_image_count"], 1)
        self.assertEqual(len(summary["reference_warnings"]), 1)

    def test_custom_image_http_omits_reference_payload_when_support_disabled(self):
        original_base_url = settings.image_base_url
        original_model = settings.image_model
        original_response_format = settings.image_response_format
        original_supports_references = settings.image_supports_references
        reference_uri = _image_data_uri((220, 130, 40))
        generated_uri = _image_data_uri((210, 125, 45))
        generated_b64 = generated_uri.split(",", 1)[1]
        captured_payload: dict = {}

        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"b64_json": generated_b64, "width": 160, "height": 90}).encode("utf-8")

        def fake_urlopen(request, timeout):
            del timeout
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        try:
            settings.image_base_url = "http://image.test"
            settings.image_model = "test-image"
            settings.image_response_format = "b64_json"
            settings.image_supports_references = False
            provider = CustomImageHTTPProvider()
            with patch("app.providers.custom_image_http.urlopen", fake_urlopen):
                response = provider.submit(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="test-image",
                        prompt="橘猫在清晨卧室",
                        references=[reference_uri],
                        params={"asset_reference_transport": "hybrid"},
                    )
                )
        finally:
            settings.image_base_url = original_base_url
            settings.image_model = original_model
            settings.image_response_format = original_response_format
            settings.image_supports_references = original_supports_references

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertNotIn("references", captured_payload)
        self.assertNotIn("reference_images", captured_payload)
        self.assertEqual(response.raw_response["request_reference_summary"]["reference_count"], 0)

    def test_orange_cat_consistency_flags_missing_orange_anchor(self):
        reference_uri = _image_data_uri((220, 130, 40))
        candidate_uri = _image_data_uri((80, 90, 180))
        result = evaluate_image_consistency(
            image_uri=candidate_uri,
            asset_resolution={
                "reference_assets": [
                    {
                        "uri": reference_uri,
                        "reference_role": "character_main_ref",
                        "asset_role": "character_main_ref",
                    }
                ],
                "prompt_context": {
                    "characters": [
                        {
                            "name": "橘猫",
                            "fixed_prompt": "真实短毛橘猫，圆脸，橘白虎斑纹稳定",
                        }
                    ]
                },
            },
        )

        issue_codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("orange_cat_color_weak", issue_codes)
        self.assertLess(result["score"], 100)


if __name__ == "__main__":
    unittest.main()
