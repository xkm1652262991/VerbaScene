import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.providers.dashscope_image import DashScopeImageProvider, _api_url
from app.providers.types import ProviderRequest, ProviderStatus


class DashScopeImageProviderTests(unittest.TestCase):
    def test_qwen_image_2_0_uses_synchronous_multimodal_contract(self):
        captured: list[dict] = []

        class FakeResponse:
            def __init__(self, body, content_type="application/json"):
                self.body = body
                self.headers = {"Content-Type": content_type}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                if isinstance(self.body, bytes):
                    return self.body
                return json.dumps(self.body).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured.append(
                {
                    "url": request.full_url if hasattr(request, "full_url") else str(request),
                    "headers": dict(request.header_items()) if hasattr(request, "header_items") else {},
                    "data": request.data if hasattr(request, "data") else None,
                    "timeout": timeout,
                }
            )
            if captured[-1]["url"].endswith("/multimodal-generation/generation"):
                return FakeResponse(
                    {
                        "request_id": "request-123",
                        "output": {
                            "choices": [
                                {
                                    "message": {
                                        "content": [
                                            {"image": "https://result.test/request-123.png"}
                                        ]
                                    }
                                }
                            ]
                        },
                    }
                )
            return FakeResponse(b"png-bytes", "image/png")

        provider = DashScopeImageProvider(
            model="qwen-image-2.0-pro-2026-06-22",
            api_key="test-key",
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
            default_params={"size": "1280x720", "prompt_extend": True, "watermark": False},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            local_reference = Path(temp_dir) / "projects" / "project-id" / "reference.png"
            local_reference.parent.mkdir(parents=True)
            local_reference.write_bytes(b"local-reference")
            request = ProviderRequest(
                project_id="project-id",
                task_id="task-id",
                model=provider.model,
                prompt="赛博漫剧角色设定图",
                negative_prompt="模糊，水印",
                references=[
                    "http://127.0.0.1:8000/storage/projects/project-id/reference.png",
                    "data:image/png;base64,UkVGRVJFTkNFMg==",
                ],
                params={"seed": 20260723},
            )
            with (
                patch("app.providers.dashscope_image.urlopen", fake_urlopen),
                patch.object(settings, "public_storage_base_url", "http://127.0.0.1:8000/storage"),
                patch.object(settings, "storage_root", temp_dir),
            ):
                response = provider.submit(request)

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(response.provider_task_id, "dashscope:request-123")
        self.assertEqual((response.assets[0].width, response.assets[0].height), (1280, 720))
        self.assertEqual(
            provider.capabilities,
            ["text_to_image", "reference_to_image", "reference_payload", "sync_api"],
        )
        submitted_payload = json.loads(captured[0]["data"].decode("utf-8"))
        self.assertEqual(
            captured[0]["url"],
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        )
        self.assertEqual(submitted_payload["model"], "qwen-image-2.0-pro-2026-06-22")
        self.assertEqual(
            submitted_payload["input"]["messages"][0]["content"],
            [
                {"image": "data:image/png;base64,bG9jYWwtcmVmZXJlbmNl"},
                {"image": "data:image/png;base64,UkVGRVJFTkNFMg=="},
                {"text": "赛博漫剧角色设定图"},
            ],
        )
        self.assertEqual(submitted_payload["parameters"]["size"], "1280*720")
        self.assertEqual(submitted_payload["parameters"]["negative_prompt"], "模糊，水印")
        self.assertNotIn("X-dashscope-async", captured[0]["headers"])
        self.assertEqual(captured[1]["url"], "https://result.test/request-123.png")

    def test_submits_modern_async_contract_polls_and_materializes_result(self):
        captured: list[dict] = []

        class FakeResponse:
            def __init__(self, body, content_type="application/json"):
                self.body = body
                self.headers = {"Content-Type": content_type}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                if isinstance(self.body, bytes):
                    return self.body
                return json.dumps(self.body).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured.append(
                {
                    "url": request.full_url if hasattr(request, "full_url") else str(request),
                    "method": request.get_method() if hasattr(request, "get_method") else "GET",
                    "headers": dict(request.header_items()) if hasattr(request, "header_items") else {},
                    "data": request.data if hasattr(request, "data") else None,
                    "timeout": timeout,
                }
            )
            if captured[-1]["url"].endswith("/image-generation/generation"):
                return FakeResponse({"output": {"task_id": "task-123", "task_status": "PENDING"}})
            if captured[-1]["url"].endswith("/api/v1/tasks/task-123"):
                return FakeResponse(
                    {
                        "output": {
                            "task_id": "task-123",
                            "task_status": "SUCCEEDED",
                            "choices": [
                                {
                                    "message": {
                                        "content": [
                                            {"type": "image", "image": "https://result.test/task-123.png"}
                                        ]
                                    }
                                }
                            ],
                        },
                        "usage": {"size": "1696*960", "image_count": 1},
                    }
                )
            return FakeResponse(b"png-bytes", "image/png")

        provider = DashScopeImageProvider(
            model="wan2.6-t2i",
            api_key="test-key",
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
            default_params={"size": "1280x720", "prompt_extend": True, "watermark": False},
        )
        request = ProviderRequest(
            project_id="project-id",
            task_id="task-id",
            model=provider.model,
            prompt="赛博漫剧角色设定图",
            negative_prompt="模糊，水印",
            params={"seed": 20260723},
        )
        with (
            patch("app.providers.dashscope_image.urlopen", fake_urlopen),
            patch.object(settings, "dashscope_image_poll_interval_sec", 0),
        ):
            response = provider.submit(request)

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(response.provider_task_id, "dashscope:task-123")
        self.assertTrue(response.assets[0].uri.startswith("data:image/png;base64,"))
        self.assertEqual((response.assets[0].width, response.assets[0].height), (1696, 960))
        self.assertTrue(response.raw_response["result_materialized"])
        submitted_payload = json.loads(captured[0]["data"].decode("utf-8"))
        self.assertEqual(captured[0]["url"], "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/image-generation/generation")
        self.assertEqual(submitted_payload["input"]["messages"][0]["content"], [{"text": "赛博漫剧角色设定图"}])
        self.assertEqual(submitted_payload["parameters"]["negative_prompt"], "模糊，水印")
        self.assertEqual(submitted_payload["parameters"]["size"], "1696*960")
        self.assertEqual(submitted_payload["parameters"]["seed"], 20260723)
        self.assertEqual(captured[0]["headers"]["X-dashscope-async"], "enable")
        self.assertEqual(captured[1]["url"], "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/tasks/task-123")
        self.assertEqual(captured[2]["url"], "https://result.test/task-123.png")

    def test_legacy_models_use_text2image_payload(self):
        captured = {}

        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"output": {"task_id": "legacy-task"}}).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        provider = DashScopeImageProvider(
            model="wan2.5-t2i-preview",
            api_key="test-key",
            base_url="https://dashscope.aliyuncs.com/api/v1",
            default_params={"size": "1280x720"},
        )
        with patch("app.providers.dashscope_image.urlopen", fake_urlopen):
            response = provider._submit_task(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model=provider.model,
                    prompt="test prompt",
                    negative_prompt="watermark",
                )
            )

        self.assertEqual(captured["url"], "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis")
        self.assertEqual(captured["payload"]["input"], {"prompt": "test prompt", "negative_prompt": "watermark"})
        self.assertEqual(response["output"]["task_id"], "legacy-task")

    def test_rejects_reference_images_in_text_to_image_adapter(self):
        provider = DashScopeImageProvider(
            model="wan2.6-t2i",
            api_key="test-key",
            base_url="https://dashscope.aliyuncs.com",
        )
        response = provider.submit(
            ProviderRequest(
                project_id="project-id",
                task_id="task-id",
                model=provider.model,
                prompt="test",
                references=["data:image/png;base64,UkVGRVJFTkNF"],
            )
        )

        self.assertEqual(response.status, ProviderStatus.FAILED)
        self.assertIn("does not support reference images", response.error.error_message)

    def test_api_url_does_not_duplicate_api_v1(self):
        self.assertEqual(
            _api_url("https://dashscope.aliyuncs.com/api/v1", "/api/v1/tasks/task-id"),
            "https://dashscope.aliyuncs.com/api/v1/tasks/task-id",
        )


if __name__ == "__main__":
    unittest.main()
