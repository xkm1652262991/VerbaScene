import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.providers.ltx23 import LTX23ApiProvider
from app.providers.types import ProviderRequest, ProviderStatus


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class LTX23ProviderContractTests(unittest.TestCase):
    def test_image_request_uses_queue_contract_and_materializes_result(self):
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            url = request if isinstance(request, str) else request.full_url
            if url == "http://storage.test/first-frame.png":
                return _FakeResponse(b"image-bytes", "image/png")
            if url.endswith("/v1/ltx23/videos/image"):
                captured["submit_url"] = url
                captured["submit_body"] = request.data
                return _FakeResponse(
                    json.dumps({"job_id": "ltx-job-image", "status": "queued"}).encode(),
                    "application/json",
                )
            if url.endswith("/v1/ltx23/jobs/ltx-job-image"):
                return _FakeResponse(
                    json.dumps(
                        {
                            "job_id": "ltx-job-image",
                            "status": "succeeded",
                            "width": 1024,
                            "height": 576,
                        }
                    ).encode(),
                    "application/json",
                )
            if url.endswith("/v1/ltx23/jobs/ltx-job-image/result"):
                return _FakeResponse(b"mp4-result", "video/mp4")
            raise AssertionError(f"Unexpected URL: {url}")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "ltx23_api_base_url", "http://ltx.test"),
            patch.object(settings, "storage_root", temp_dir),
            patch.object(settings, "public_storage_base_url", "http://api.test/storage"),
            patch("app.providers.ltx23.urlopen", fake_urlopen),
        ):
            response = LTX23ApiProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model="LTX-2.3",
                    prompt="The character gently waves.",
                    negative_prompt="flicker",
                    references=["http://storage.test/first-frame.png"],
                    params={
                        "size": "1024x576",
                        "frames": 81,
                        "fps": 16,
                        "steps": 8,
                        "guidance": 1,
                        "strength": 0.78,
                        "seed": 42,
                    },
                )
            )

            result_path = (
                Path(temp_dir)
                / "projects"
                / "project-id"
                / "provider-results"
                / "ltx23"
                / "ltx-job-image.mp4"
            )
            self.assertEqual(result_path.read_bytes(), b"mp4-result")

        body = captured["submit_body"]
        self.assertEqual(
            captured["submit_url"],
            "http://ltx.test/v1/ltx23/videos/image",
        )
        self.assertIn(b'name="image"; filename="first-frame.png"', body)
        self.assertIn(b'name="prompt"\r\n\r\nThe character gently waves.', body)
        self.assertIn(b'name="width"\r\n\r\n1024\r\n', body)
        self.assertIn(b'name="height"\r\n\r\n576\r\n', body)
        self.assertIn(b'name="frames"\r\n\r\n81\r\n', body)
        self.assertIn(b'name="steps"\r\n\r\n8\r\n', body)
        self.assertIn(b'name="cfg"\r\n\r\n1.0\r\n', body)
        self.assertIn(b'name="strength"\r\n\r\n0.78\r\n', body)
        self.assertIn(b'name="negative"\r\n\r\nflicker\r\n', body)
        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(response.assets[0].width, 1024)
        self.assertEqual(response.assets[0].height, 576)
        self.assertEqual(response.raw_response["generation_mode"], "image_to_video")

    def test_request_without_reference_uses_text_endpoint(self):
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            url = request.full_url
            if url.endswith("/v1/ltx23/videos/text"):
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                return _FakeResponse(
                    json.dumps({"id": "ltx-job-text", "status": "queued"}).encode(),
                    "application/json",
                )
            if url.endswith("/v1/ltx23/jobs/ltx-job-text"):
                return _FakeResponse(
                    json.dumps({"id": "ltx-job-text", "status": "done"}).encode(),
                    "application/json",
                )
            if url.endswith("/v1/ltx23/jobs/ltx-job-text/result"):
                return _FakeResponse(b"text-video", "application/octet-stream")
            raise AssertionError(f"Unexpected URL: {url}")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "ltx23_api_base_url", "http://ltx.test"),
            patch.object(settings, "storage_root", temp_dir),
            patch.object(settings, "public_storage_base_url", "http://api.test/storage"),
            patch("app.providers.ltx23.urlopen", fake_urlopen),
        ):
            response = LTX23ApiProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="text-task",
                    model="LTX-2.3",
                    prompt="A frog waves from a lily pad.",
                    params={
                        "size": "1024*576",
                        "frames": 81,
                        "fps": 16,
                        "steps": 8,
                        "guidance": 1,
                    },
                )
            )

        payload = captured["payload"]
        self.assertEqual(payload["prompt"], "A frog waves from a lily pad.")
        self.assertEqual(payload["width"], 1024)
        self.assertEqual(payload["height"], 576)
        self.assertEqual(payload["cfg"], 1.0)
        self.assertIn("client_job_id", payload)
        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(response.assets[0].mime_type, "video/mp4")
        self.assertEqual(response.raw_response["generation_mode"], "text_to_video")


if __name__ == "__main__":
    unittest.main()
