import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.providers.types import ProviderExecutionMode, ProviderRequest, ProviderStatus
from app.providers.wan_i2v import Wan2I2VApiProvider


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class WanI2VAsyncProviderContractTests(unittest.TestCase):
    def test_submit_poll_fetch_and_cancel_are_separate_calls(self):
        calls: list[tuple[str, str]] = []

        def fake_urlopen(request, timeout):
            _ = timeout
            calls.append((request.method, request.full_url))
            if request.method == "POST":
                return _FakeResponse(
                    json.dumps({"job_id": "wan-job-1", "status": "queued"}).encode()
                )
            if request.method == "DELETE":
                return _FakeResponse(b"{}")
            if request.full_url.endswith("/v1/i2v/jobs/wan-job-1"):
                return _FakeResponse(
                    json.dumps(
                        {
                            "job_id": "wan-job-1",
                            "status": "succeeded",
                            "duration_sec": 5,
                            "width": 1280,
                            "height": 720,
                        }
                    ).encode()
                )
            if "/v1/i2v/jobs/wan-job-1/result?" in request.full_url:
                return _FakeResponse(b"wan-video", "video/mp4")
            raise AssertionError(request.full_url)

        request = ProviderRequest(
            project_id="project-id",
            task_id="task-id",
            model="wan2.2-i2v-a14b",
            prompt="The character waves.",
            references=[
                "data:image/png;base64," + base64.b64encode(b"image").decode("ascii")
            ],
            params={
                "frames": 81,
                "steps": 4,
                "guidance": 1,
                "fps": 16,
                "size": "1280*720",
            },
        )
        with (
            patch.object(settings, "wan_i2v_api_base_url", "http://wan.test"),
            patch.object(settings, "wan_i2v_api_protocol", "official"),
            patch.object(settings, "wan_i2v_api_key", "secret"),
            patch("app.providers.wan_i2v.urlopen", fake_urlopen),
        ):
            provider = Wan2I2VApiProvider()
            submitted = provider.submit(request)
            self.assertEqual(submitted.status, ProviderStatus.QUEUED)
            self.assertEqual(submitted.execution_mode, ProviderExecutionMode.ASYNC)
            self.assertEqual(submitted.provider_task_id, "wan-job-1")
            self.assertEqual([method for method, _url in calls], ["POST"])

            polled = provider.poll(submitted.provider_task_id)
            self.assertEqual(polled.status, ProviderStatus.SUCCEEDED)
            fetched = provider.fetch_result(submitted.provider_task_id, request=request)
            result_path = Path(fetched.assets[0].uri)
            self.assertEqual(result_path.read_bytes(), b"wan-video")
            result_path.unlink()
            provider.cancel(submitted.provider_task_id)

        self.assertEqual(sum(method == "POST" for method, _url in calls), 1)
        self.assertEqual(sum(method == "DELETE" for method, _url in calls), 1)


if __name__ == "__main__":
    unittest.main()
