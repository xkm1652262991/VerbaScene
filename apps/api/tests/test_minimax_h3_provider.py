import base64
import json
import tempfile
import unittest
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch

from app.core.config import settings
from app.providers.minimax_h3 import MiniMaxH3GatewayProvider
from app.providers.types import (
    ProviderExecutionMode,
    ProviderRequest,
    ProviderStatus,
    SubmissionState,
)
from app.production.video_request_compiler import shot_video_provider_params


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json"):
        self._body = body
        self._offset = 0
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            result = self._body[self._offset :]
            self._offset = len(self._body)
            return result
        result = self._body[self._offset : self._offset + size]
        self._offset += len(result)
        return result


def _data_image(content: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(content).decode("ascii")


class MiniMaxH3ProviderContractTests(unittest.TestCase):
    def test_auto_routes_entity_references_to_r2v_without_empty_auth_header(self):
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            _ = timeout
            url = request.full_url
            if url.endswith("/v1/videos") and request.method == "POST":
                captured["body"] = request.data
                captured["authorization"] = request.get_header("Authorization")
                return _FakeResponse(
                    json.dumps(
                        {
                            "id": "h3-r2v-job",
                            "object": "video",
                            "model": "minimax-h3-r2v",
                            "status": "queued",
                        }
                    ).encode()
                )
            if url.endswith("/v1/videos/h3-r2v-job/content"):
                return _FakeResponse(b"h3-video", "video/mp4")
            if url.endswith("/v1/videos/h3-r2v-job/cancel"):
                captured["cancelled"] = True
                return _FakeResponse(
                    json.dumps({"id": "h3-r2v-job", "status": "cancelled"}).encode()
                )
            if url.endswith("/v1/videos/h3-r2v-job"):
                return _FakeResponse(
                    json.dumps(
                        {
                            "id": "h3-r2v-job",
                            "model": "minimax-h3-r2v",
                            "status": "completed",
                        }
                    ).encode()
                )
            raise AssertionError(f"Unexpected URL: {url}")

        references = [_data_image(b"character-image"), _data_image(b"scene-image")]
        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_api_key", None),
            patch("app.providers.minimax_h3.urlopen", fake_urlopen),
        ):
            provider = MiniMaxH3GatewayProvider()
            request = ProviderRequest(
                project_id="project-id",
                task_id="task-id",
                model="auto",
                prompt="参考绑定：图片1=旧编号。\nMia waves in the classroom.",
                references=references,
                params={"size": "1024x576", "duration_sec": 4, "steps": 19},
                metadata={
                    "asset_resolution": {
                        "prompt_context": {
                            "characters": [{"id": "mia-id", "name": "Mia"}],
                            "scene": {"id": "room-id", "name": "教室"},
                        },
                        "reference_assets": [
                            {
                                "uri": references[0],
                                "asset_type": "image",
                                "reference_role": "character_main_ref",
                                "entity_type": "character",
                                "entity_id": "mia-id",
                            },
                            {
                                "uri": references[1],
                                "asset_type": "image",
                                "reference_role": "scene_ref",
                                "entity_type": "scene",
                                "entity_id": "room-id",
                            },
                        ],
                    }
                },
            )
            submitted = provider.submit(request)
            polled = provider.poll(submitted.provider_task_id)
            fetched = provider.fetch_result(submitted.provider_task_id, request=request)
            provider.cancel(submitted.provider_task_id)

        body = captured["body"]
        self.assertIsNone(captured["authorization"])
        self.assertIn(b'name="model"\r\n\r\nminimax-h3-r2v', body)
        self.assertEqual(body.count(b'name="input_reference"'), 2)
        self.assertIn("<Picture 1>=角色“Mia”身份参考".encode(), body)
        self.assertIn("<Picture 2>=场景“教室”空间参考".encode(), body)
        self.assertNotIn("旧编号".encode(), body)
        self.assertEqual(submitted.status, ProviderStatus.QUEUED)
        self.assertEqual(submitted.execution_mode, ProviderExecutionMode.ASYNC)
        self.assertEqual(polled.status, ProviderStatus.SUCCEEDED)
        self.assertTrue(captured["cancelled"])
        result_path = Path(fetched.assets[0].uri)
        self.assertEqual(result_path.read_bytes(), b"h3-video")
        self.assertEqual(fetched.assets[0].width, 1024)
        self.assertEqual(fetched.assets[0].height, 576)
        self.assertEqual(fetched.assets[0].duration_sec, Decimal("4.000"))
        result_path.unlink()

    def test_auto_routes_explicit_first_frame_to_fl2va_and_omits_entity_images(self):
        captured: dict[str, bytes] = {}

        def fake_urlopen(request, timeout):
            _ = timeout
            captured["body"] = request.data
            return _FakeResponse(
                json.dumps(
                    {
                        "id": "h3-fl2va-job",
                        "model": "minimax-h3-fl2va",
                        "status": "queued",
                    }
                ).encode()
            )

        first_frame = _data_image(b"first-frame-bytes")
        character = _data_image(b"character-bytes")
        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_api_key", None),
            patch("app.providers.minimax_h3.urlopen", fake_urlopen),
        ):
            response = MiniMaxH3GatewayProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="first-frame-task",
                    model="auto",
                    prompt="Mia opens the door.",
                    references=[first_frame, character],
                    params={"size": "576x1024", "duration_sec": 5},
                    metadata={
                        "asset_resolution": {
                            "reference_assets": [
                                {
                                    "uri": first_frame,
                                    "asset_type": "image",
                                    "reference_role": "video_first_frame",
                                },
                                {
                                    "uri": character,
                                    "asset_type": "image",
                                    "reference_role": "character_main_ref",
                                },
                            ]
                        }
                    },
                )
            )

        body = captured["body"]
        self.assertEqual(response.status, ProviderStatus.QUEUED)
        self.assertEqual(
            response.raw_response["request_contract"]["resolved_model"],
            "minimax-h3-fl2va",
        )
        self.assertEqual(body.count(b'name="input_reference"'), 1)
        self.assertIn(b"first-frame-bytes", body)
        self.assertNotIn(b"character-bytes", body)
        self.assertIn(b'name="model"\r\n\r\nminimax-h3-fl2va', body)
        self.assertIn("严格作为视频首帧".encode(), body)

    def test_outgoing_prompt_uses_h3_audio_structure_and_stable_speakers(self):
        captured: dict[str, bytes] = {}

        def fake_urlopen(request, timeout):
            _ = timeout
            captured["body"] = request.data
            return _FakeResponse(
                json.dumps({"id": "h3-audio-job", "status": "queued"}).encode()
            )

        prompt = "\n".join(
            [
                (
                    "生成连续动画英语短剧片段，"
                    "使用原生英文对白、环境音和动作音效。"
                ),
                "镜头1：中景，固定机位。Mia举起红球。",
                "对白：Mia（开心）说：{Look! A red ball!}；Leo说：{I see it!}",
                "音效：<小球滚动声>",
                "镜头2：近景。Mia点头。",
                "对白：Mia说：{Yes, it is red.}",
            ]
        )
        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_api_key", None),
            patch("app.providers.minimax_h3.urlopen", fake_urlopen),
        ):
            response = MiniMaxH3GatewayProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="audio-task",
                    model="auto",
                    prompt=prompt,
                    params={"size": "1024x576", "duration_sec": 6},
                )
            )

        body = captured["body"].decode("utf-8", errors="ignore")
        self.assertEqual(response.status, ProviderStatus.QUEUED)
        self.assertEqual(
            response.raw_response["request_contract"]["prompt_contract_version"],
            "h3-native-audio-v1",
        )
        self.assertIn("integrated_multimodal_description: [Shot 1]", body)
        self.assertIn("Mia (S1) says with a 开心 delivery", body)
        self.assertIn("Leo (S2) says", body)
        self.assertIn("Mia (S1) says: <d>[English] Yes, it is red.</d>", body)
        self.assertEqual(body.count("<d>[English] Look! A red ball!</d>"), 1)
        self.assertIn("Synchronized diegetic sound: 小球滚动声.", body)
        self.assertIn("Every audible spoken word comes from exactly one scripted <d> block", body)
        self.assertIn("overall_soundscape: A clean, restrained mix", body)
        self.assertIn("non_diegetic_music: N/A", body)
        self.assertNotIn("对白：Mia", body)

    def test_outgoing_prompt_without_dialogue_explicitly_keeps_audio_non_verbal(self):
        captured: dict[str, bytes] = {}

        def fake_urlopen(request, timeout):
            _ = timeout
            captured["body"] = request.data
            return _FakeResponse(
                json.dumps({"id": "h3-silent-cast-job", "status": "queued"}).encode()
            )

        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_api_key", None),
            patch("app.providers.minimax_h3.urlopen", fake_urlopen),
        ):
            MiniMaxH3GatewayProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="non-verbal-task",
                    model="auto",
                    prompt="Two children silently stack wooden blocks in a classroom.",
                    params={"size": "1024x576", "duration_sec": 4},
                )
            )

        body = captured["body"].decode("utf-8", errors="ignore")
        self.assertIn("soundtrack consists exclusively of non-verbal ambience", body)
        self.assertIn("non_diegetic_music: N/A", body)

    def test_existing_h3_structure_is_not_nested_or_duplicated(self):
        captured: dict[str, bytes] = {}

        def fake_urlopen(request, timeout):
            _ = timeout
            captured["body"] = request.data
            return _FakeResponse(
                json.dumps({"id": "h3-structured-job", "status": "queued"}).encode()
            )

        structured = "\n\n".join(
            [
                (
                    "integrated_multimodal_description: [Shot 1] Mia (S1) says: "
                    "<d>[English] Hello.</d>"
                ),
                "overall_soundscape: Quiet classroom room tone.",
                "non_diegetic_music: N/A",
            ]
        )
        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_api_key", None),
            patch("app.providers.minimax_h3.urlopen", fake_urlopen),
        ):
            MiniMaxH3GatewayProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="structured-task",
                    model="auto",
                    prompt=structured,
                    params={"size": "1024x576", "duration_sec": 4},
                )
            )

        body = captured["body"].decode("utf-8", errors="ignore")
        self.assertEqual(body.count("integrated_multimodal_description:"), 1)
        self.assertEqual(body.count("overall_soundscape:"), 1)
        self.assertEqual(body.count("non_diegetic_music:"), 1)
        self.assertEqual(body.count("<d>[English] Hello.</d>"), 1)
        self.assertIn("Every audible spoken word comes from exactly one scripted <d> block", body)

    def test_auto_routes_text_only_request_to_fl2va(self):
        captured: dict[str, bytes] = {}

        def fake_urlopen(request, timeout):
            _ = timeout
            captured["body"] = request.data
            return _FakeResponse(
                json.dumps({"id": "h3-text-job", "status": "queued"}).encode()
            )

        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_api_key", None),
            patch("app.providers.minimax_h3.urlopen", fake_urlopen),
        ):
            response = MiniMaxH3GatewayProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="text-task",
                    model="auto",
                    prompt="A quiet animated classroom at sunrise.",
                    params={"size": "1024x576", "duration_sec": 3},
                )
            )

        self.assertEqual(response.status, ProviderStatus.QUEUED)
        self.assertIn(
            b'name="model"\r\n\r\nminimax-h3-fl2va',
            captured["body"],
        )
        self.assertNotIn(b'name="input_reference"', captured["body"])

    def test_explicit_http_rejection_is_safe_for_runtime_retry_decision(self):
        def fake_urlopen(request, timeout):
            _ = request, timeout
            raise HTTPError(
                "http://h3.test/v1/videos",
                422,
                "Unprocessable Entity",
                {},
                BytesIO(b'{"detail":"invalid prompt"}'),
            )

        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_api_key", None),
            patch("app.providers.minimax_h3.urlopen", fake_urlopen),
        ):
            response = MiniMaxH3GatewayProvider().submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="rejected-task",
                    model="auto",
                    prompt="test",
                    params={"size": "1024x576", "duration_sec": 3},
                )
            )

        self.assertEqual(response.status, ProviderStatus.FAILED)
        self.assertEqual(response.error.error_code, "minimax_h3_http_422")
        self.assertEqual(response.error.submission_state, SubmissionState.NOT_SUBMITTED)
        self.assertFalse(response.error.is_retryable)

    def test_project_aspect_ratio_maps_to_h3_compatible_dimensions(self):
        with (
            patch.object(settings, "minimax_h3_gateway_base_url", "http://h3.test/v1"),
            patch.object(settings, "minimax_h3_gateway_landscape_size", "1024x576"),
            patch.object(settings, "minimax_h3_gateway_portrait_size", "576x1024"),
        ):
            provider = MiniMaxH3GatewayProvider()
            landscape = shot_video_provider_params(
                SimpleNamespace(
                    duration_sec=Decimal("4"),
                    shot_no=1,
                    project=SimpleNamespace(aspect_ratio="16:9"),
                ),
                provider,
            )
            portrait = shot_video_provider_params(
                SimpleNamespace(
                    duration_sec=Decimal("4"),
                    shot_no=2,
                    project=SimpleNamespace(aspect_ratio="9:16"),
                ),
                provider,
            )

        self.assertEqual(landscape["size"], "1024x576")
        self.assertEqual(portrait["size"], "576x1024")


if __name__ == "__main__":
    unittest.main()
