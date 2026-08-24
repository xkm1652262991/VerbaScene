import base64
import json
import unittest
from unittest.mock import patch

from app.providers.qwen_image_musubi import QwenImageMusubiProvider
from app.providers.types import ProviderRequest, ProviderStatus


class QwenImageMusubiProviderTests(unittest.TestCase):
    def test_sends_native_generate_contract_and_reads_nested_base64(self):
        generated = base64.b64encode(b"test-png-bytes").decode("ascii")
        captured: dict = {}

        class FakeResponse:
            headers = {"Content-Type": "application/json; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "status": "ok",
                        "id": "server-request-id",
                        "result": {
                            "image_base64": generated,
                            "width": 1024,
                            "height": 576,
                        },
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        provider = QwenImageMusubiProvider(
            model="qwen-image-fig-lightning8-v2",
            base_url="http://qwen.test:18108",
            default_params={
                "size": "1024x576",
                "steps": 8,
                "guidance_scale": 1.0,
            },
        )
        with patch("app.providers.qwen_image_musubi.urlopen", fake_urlopen):
            response = provider.submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model=provider.model,
                    prompt="apple, flat children picture book illustration",
                    negative_prompt="watermark",
                    references=["data:image/png;base64,UkVGRVJFTkNF"],
                    params={"seed": 12345},
                )
            )

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(captured["url"], "http://qwen.test:18108/generate")
        self.assertEqual(
            set(captured["payload"]),
            {"id", "prompt", "seed", "width", "height", "steps", "cfg"},
        )
        self.assertEqual(captured["payload"]["seed"], 12345)
        self.assertEqual(captured["payload"]["width"], 1024)
        self.assertEqual(captured["payload"]["height"], 576)
        self.assertEqual(captured["payload"]["steps"], 8)
        self.assertEqual(captured["payload"]["cfg"], 1.0)
        self.assertNotIn("model", captured["payload"])
        self.assertNotIn("negative_prompt", captured["payload"])
        self.assertNotIn("references", captured["payload"])
        self.assertTrue(response.assets[0].uri.startswith("data:image/png;base64,"))
        self.assertEqual(response.assets[0].width, 1024)
        self.assertEqual(response.assets[0].height, 576)
        self.assertEqual(response.assets[0].metadata["musubi_request_id"], "server-request-id")

    def test_treats_json_error_status_as_provider_failure(self):
        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "status": "error",
                        "error": "insufficient free GPU memory",
                        "details": None,
                    }
                ).encode("utf-8")

        provider = QwenImageMusubiProvider(
            model="qwen-image-fig-lightning8-v2",
            base_url="http://qwen.test:18108",
            default_params={"size": "1024x576", "steps": 8, "guidance_scale": 1.0},
        )
        with patch("app.providers.qwen_image_musubi.urlopen", return_value=FakeResponse()):
            response = provider.submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model=provider.model,
                    prompt="test prompt",
                    params={"seed": 12345},
                )
            )

        self.assertEqual(response.status, ProviderStatus.FAILED)
        self.assertEqual(response.error.error_code, "qwen_image_musubi_generation_failed")
        self.assertIn("insufficient free GPU memory", response.error.error_message)

    def test_resolves_server_relative_output_url(self):
        class GenerateResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "status": "ok",
                        "id": "request-id",
                        "url": "/outputs/images/request-id/result.png",
                        "image_path": "/srv/outputs/images/request-id/result.png",
                        "width": 1024,
                        "height": 576,
                    }
                ).encode("utf-8")

        class ImageResponse:
            headers = {"Content-Type": "image/png"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"downloaded-png-bytes"

        requested_urls: list[str] = []

        def fake_urlopen(request, timeout):
            del timeout
            requested_urls.append(request.full_url)
            return GenerateResponse() if request.get_method() == "POST" else ImageResponse()

        provider = QwenImageMusubiProvider(
            model="qwen-image-fig-lightning8-v2",
            base_url="http://qwen.test:18108",
            default_params={"size": "1024x576", "steps": 8, "guidance_scale": 1.0},
        )
        with patch("app.providers.qwen_image_musubi.urlopen", fake_urlopen):
            response = provider.submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model=provider.model,
                    prompt="test prompt",
                    params={"seed": 12345},
                )
            )

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(
            requested_urls,
            [
                "http://qwen.test:18108/generate",
                "http://qwen.test:18108/outputs/images/request-id/result.png",
            ],
        )
        self.assertTrue(response.assets[0].uri.startswith("data:image/png;base64,"))
        self.assertEqual(
            response.assets[0].metadata["remote_url"],
            "http://qwen.test:18108/outputs/images/request-id/result.png",
        )


if __name__ == "__main__":
    unittest.main()
