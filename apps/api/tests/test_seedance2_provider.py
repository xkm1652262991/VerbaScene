import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.providers.seedance2 import Seedance2ApiProvider
from app.providers.types import (
    ProviderExecutionMode,
    ProviderRequest,
    ProviderStatus,
)


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class Seedance2ProviderContractTests(unittest.TestCase):
    def test_multimodal_request_polls_and_materializes_video(self):
        captured: dict[str, object] = {}
        poll_count = 0

        def fake_urlopen(request, timeout):
            nonlocal poll_count
            url = request.full_url
            if (
                url
                == "https://ark.test/api/v3/contents/generations/tasks"
                and request.method == "POST"
            ):
                captured["authorization"] = request.headers["Authorization"]
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                return _FakeResponse(
                    json.dumps(
                        {"id": "cgt-seedance-contract", "status": "queued"}
                    ).encode()
                )
            if url.endswith(
                "/contents/generations/tasks/cgt-seedance-contract"
            ):
                poll_count += 1
                if poll_count == 1:
                    return _FakeResponse(
                        json.dumps(
                            {
                                "id": "cgt-seedance-contract",
                                "status": "running",
                            }
                        ).encode()
                    )
                return _FakeResponse(
                    json.dumps(
                        {
                            "id": "cgt-seedance-contract",
                            "model": "doubao-seedance-2-0-260128",
                            "status": "succeeded",
                            "content": {
                                "video_url": "https://result.test/video.mp4"
                            },
                            "resolution": "720p",
                            "ratio": "16:9",
                            "duration": 11,
                            "usage": {"completion_tokens": 1234},
                        }
                    ).encode()
                )
            if url == "https://result.test/video.mp4":
                return _FakeResponse(b"seedance-video", "video/mp4")
            raise AssertionError(f"Unexpected URL: {url}")

        references = [
            "https://assets.test/character.png",
            "https://assets.test/motion.mp4",
            "https://assets.test/voice.mp3",
        ]
        metadata_references = [
            {
                "asset_id": "image-asset",
                "uri": references[0],
                "asset_type": "image",
                "reference_role": "character_main_ref",
            },
            {
                "asset_id": "video-asset",
                "uri": references[1],
                "asset_type": "video",
                "reference_role": "motion_reference",
            },
            {
                "asset_id": "audio-asset",
                "uri": references[2],
                "asset_type": "audio",
                "reference_role": "voice_reference",
            },
        ]

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                settings,
                "seedance2_api_base_url",
                "https://ark.test/api/v3",
            ),
            patch.object(settings, "seedance2_api_key", "unit-test-secret"),
            patch.object(settings, "seedance2_api_poll_interval_sec", 0),
            patch.object(settings, "storage_root", temp_dir),
            patch.object(
                settings,
                "public_storage_base_url",
                "http://api.test/storage",
            ),
            patch("app.providers.seedance2.urlopen", fake_urlopen),
        ):
            provider = Seedance2ApiProvider()
            request = ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model="doubao-seedance-2-0-260128",
                    prompt=(
                        "图片1（@角色_Benny_base）保持形象；"
                        "视频1控制镜头；音频1控制背景音乐。"
                    ),
                    references=references,
                    params={
                        "duration": 11,
                        "resolution": "720p",
                        "ratio": "16:9",
                        "generate_audio": True,
                        "watermark": False,
                    },
                    metadata={
                        "asset_resolution": {
                            "reference_assets": metadata_references,
                        }
                    },
                )
            submitted = provider.submit(request)
            self.assertEqual(submitted.status, ProviderStatus.QUEUED)
            self.assertEqual(poll_count, 0)
            running = provider.poll(submitted.provider_task_id)
            self.assertEqual(running.status, ProviderStatus.RUNNING)
            response = provider.fetch_result(submitted.provider_task_id, request=request)
            result_path = Path(response.assets[0].uri)
            self.assertEqual(result_path.read_bytes(), b"seedance-video")
            result_path.unlink()

        payload = captured["payload"]
        self.assertEqual(captured["authorization"], "Bearer unit-test-secret")
        self.assertEqual(payload["model"], "doubao-seedance-2-0-260128")
        self.assertEqual(payload["duration"], 11)
        self.assertEqual(payload["resolution"], "720p")
        self.assertEqual(payload["ratio"], "16:9")
        self.assertTrue(payload["generate_audio"])
        self.assertFalse(payload["watermark"])
        self.assertEqual(
            [item["type"] for item in payload["content"]],
            ["text", "image_url", "video_url", "audio_url"],
        )
        self.assertEqual(
            [item.get("role") for item in payload["content"][1:]],
            ["reference_image", "reference_video", "reference_audio"],
        )
        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(response.execution_mode, ProviderExecutionMode.ASYNC)
        self.assertEqual(response.provider_task_id, "cgt-seedance-contract")
        self.assertEqual(response.assets[0].width, 1280)
        self.assertEqual(response.assets[0].height, 720)
        self.assertEqual(float(response.assets[0].duration_sec), 11)
        self.assertNotIn(
            "unit-test-secret",
            json.dumps(response.raw_response, ensure_ascii=False),
        )
        self.assertEqual(
            submitted.raw_response["request_contract"]["reference_counts"],
            {"image": 1, "video": 1, "audio": 1},
        )

    def test_local_project_image_is_encoded_as_data_url(self):
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            url = request.full_url
            if request.method == "POST":
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                return _FakeResponse(
                    json.dumps(
                        {"id": "cgt-local-image", "status": "queued"}
                    ).encode()
                )
            if url.endswith("/cgt-local-image"):
                return _FakeResponse(
                    json.dumps(
                        {
                            "id": "cgt-local-image",
                            "status": "succeeded",
                            "content": {
                                "video_url": "https://result.test/local.mp4"
                            },
                            "duration": 8,
                            "resolution": "720p",
                            "ratio": "9:16",
                        }
                    ).encode()
                )
            if url == "https://result.test/local.mp4":
                return _FakeResponse(b"local-video", "video/mp4")
            raise AssertionError(f"Unexpected URL: {url}")

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = (
                Path(temp_dir)
                / "projects"
                / "project-id"
                / "assets"
                / "frame.png"
            )
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"local-image")
            local_uri = (
                "http://localhost:8000/storage/"
                "projects/project-id/assets/frame.png"
            )
            with (
                patch.object(
                    settings,
                    "seedance2_api_base_url",
                    "https://ark.test/api/v3",
                ),
                patch.object(settings, "seedance2_api_key", "unit-test-secret"),
                patch.object(settings, "storage_root", temp_dir),
                patch.object(
                    settings,
                    "public_storage_base_url",
                    "http://localhost:8000/storage",
                ),
                patch("app.providers.seedance2.urlopen", fake_urlopen),
            ):
                provider = Seedance2ApiProvider()
                request = ProviderRequest(
                        project_id="project-id",
                        task_id="local-image",
                        model="doubao-seedance-2-0-260128",
                        prompt="图片1中的角色挥手。",
                        references=[local_uri],
                        params={
                            "duration_sec": 8,
                            "resolution": "720p",
                            "ratio": "9:16",
                        },
                    )
                submitted = provider.submit(request)
                self.assertEqual(submitted.status, ProviderStatus.QUEUED)
                response = provider.fetch_result(submitted.provider_task_id, request=request)
                Path(response.assets[0].uri).unlink()

        encoded = captured["payload"]["content"][1]["image_url"]["url"]
        self.assertTrue(encoded.startswith("data:image/png;base64,"))
        self.assertEqual(
            base64.b64decode(encoded.split(",", 1)[1]),
            b"local-image",
        )
        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(response.assets[0].width, 720)
        self.assertEqual(response.assets[0].height, 1280)

    def test_failed_remote_task_preserves_error_code_without_retrying_submit(self):
        submit_count = 0

        def fake_urlopen(request, timeout):
            nonlocal submit_count
            if request.method == "POST":
                submit_count += 1
                return _FakeResponse(
                    json.dumps({"id": "cgt-failed", "status": "queued"}).encode()
                )
            return _FakeResponse(
                json.dumps(
                    {
                        "id": "cgt-failed",
                        "status": "failed",
                        "error": {
                            "code": "InputTextSensitiveContentDetected",
                            "message": "input text rejected",
                        },
                    }
                ).encode()
            )

        with (
            patch.object(
                settings,
                "seedance2_api_base_url",
                "https://ark.test/api/v3",
            ),
            patch.object(settings, "seedance2_api_key", "unit-test-secret"),
            patch("app.providers.seedance2.urlopen", fake_urlopen),
        ):
            provider = Seedance2ApiProvider()
            submitted = provider.submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="failed-task",
                    model="doubao-seedance-2-0-260128",
                    prompt="test",
                    params={"duration": 8},
                )
            )
            response = provider.poll(submitted.provider_task_id)

        self.assertEqual(submit_count, 1)
        self.assertEqual(response.status, ProviderStatus.FAILED)
        self.assertEqual(
            response.error.error_code,
            "InputTextSensitiveContentDetected",
        )
        self.assertFalse(response.error.is_retryable)

    def test_duration_over_15_seconds_fails_before_network(self):
        with (
            patch.object(
                settings,
                "seedance2_api_base_url",
                "https://ark.test/api/v3",
            ),
            patch.object(settings, "seedance2_api_key", "unit-test-secret"),
            patch("app.providers.seedance2.urlopen") as mocked_urlopen,
        ):
            response = Seedance2ApiProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="too-long",
                    model="doubao-seedance-2-0-260128",
                    prompt="test",
                    params={"duration": 16},
                )
            )

        mocked_urlopen.assert_not_called()
        self.assertEqual(response.status, ProviderStatus.FAILED)
        self.assertEqual(response.error.error_code, "seedance2_invalid_request")

    def test_default_duration_uses_provider_auto_and_preserves_actual_duration(self):
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            if request.method == "POST":
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                return _FakeResponse(json.dumps({"id": "cgt-smart", "status": "queued"}).encode())
            if request.full_url.endswith("/cgt-smart"):
                return _FakeResponse(
                    json.dumps(
                        {
                            "id": "cgt-smart",
                            "status": "succeeded",
                            "content": {"video_url": "https://result.test/smart.mp4"},
                            "duration": 7,
                            "resolution": "720p",
                            "ratio": "16:9",
                        }
                    ).encode()
                )
            if request.full_url == "https://result.test/smart.mp4":
                return _FakeResponse(b"smart-video", "video/mp4")
            raise AssertionError(f"Unexpected URL: {request.full_url}")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "seedance2_api_base_url", "https://ark.test/api/v3"),
            patch.object(settings, "seedance2_api_key", "unit-test-secret"),
            patch.object(settings, "seedance2_api_poll_interval_sec", 0),
            patch.object(settings, "storage_root", temp_dir),
            patch("app.providers.seedance2.urlopen", fake_urlopen),
        ):
            provider = Seedance2ApiProvider()
            request = ProviderRequest(
                    project_id="project-id",
                    task_id="smart-duration",
                    model="doubao-seedance-2-0-260128",
                    prompt="角色完成一个自然挥手动作。",
                )
            submitted = provider.submit(request)
            response = provider.fetch_result(submitted.provider_task_id, request=request)
            Path(response.assets[0].uri).unlink()

        self.assertTrue(provider.descriptor()["smart_duration"])
        self.assertEqual(provider.descriptor()["min_duration_sec"], 4)
        self.assertEqual(captured["payload"]["duration"], -1)
        self.assertEqual(submitted.raw_response["request_contract"]["duration_mode"], "provider_auto")
        self.assertEqual(float(response.assets[0].duration_sec), 7)

    def test_fixed_duration_below_four_seconds_fails_before_network(self):
        with (
            patch.object(settings, "seedance2_api_base_url", "https://ark.test/api/v3"),
            patch.object(settings, "seedance2_api_key", "unit-test-secret"),
            patch("app.providers.seedance2.urlopen") as mocked_urlopen,
        ):
            response = Seedance2ApiProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="too-short",
                    model="doubao-seedance-2-0-260128",
                    prompt="test",
                    params={"duration": 3},
                )
            )

        mocked_urlopen.assert_not_called()
        self.assertEqual(response.status, ProviderStatus.FAILED)
        self.assertEqual(response.error.error_code, "seedance2_invalid_request")


if __name__ == "__main__":
    unittest.main()
