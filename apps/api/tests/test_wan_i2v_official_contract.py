import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.video_generation import shot_video_params
from app.core.config import settings
from app.providers.types import ProviderRequest
from app.providers.wan_i2v import Wan2I2VApiProvider, _WanImageInput, _official_generation_params


class WanI2VOfficialContractTests(unittest.TestCase):
    def test_four_step_runtime_is_preserved_by_shot_parameter_compiler(self):
        shot = SimpleNamespace(shot_card={}, duration_sec=3, camera_movement="固定机位")

        params = shot_video_params(shot, fps=16, max_frames=81, default_steps=4, default_guidance=1)

        self.assertEqual(params["frames"], 49)
        self.assertEqual(params["steps"], 4)
        self.assertEqual(params["guidance"], 1)

    def test_official_contract_accepts_only_fixed_steps_guidance_and_4n_plus_1_frames(self):
        request = ProviderRequest(
            project_id="project-id",
            task_id="task-id",
            model="Wan-AI/Wan2.2-I2V-A14B",
            prompt="test",
            params={"frames": 81, "steps": 4, "guidance": 1},
        )

        self.assertEqual(_official_generation_params(request), (81, 4, 1.0))

        for params, message in (
            ({"frames": 80, "steps": 4, "guidance": 1}, r"frames=4n\+1"),
            ({"frames": 81, "steps": 40, "guidance": 1}, "steps=4"),
            ({"frames": 81, "steps": 4, "guidance": 3.5}, "guidance=1"),
        ):
            with self.subTest(params=params), self.assertRaisesRegex(ValueError, message):
                _official_generation_params(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="Wan-AI/Wan2.2-I2V-A14B",
                        prompt="test",
                        params=params,
                    )
                )

    def test_official_submission_uses_x_api_key_and_fixed_fields(self):
        captured = {}

        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"id": "job-id"}).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = request.data
            captured["timeout"] = timeout
            return FakeResponse()

        with (
            patch.object(settings, "wan_i2v_api_base_url", "http://192.0.2.25:18083"),
            patch.object(settings, "wan_i2v_api_key", "test-key"),
            patch.object(settings, "wan_i2v_api_protocol", "official"),
            patch("app.providers.wan_i2v.urlopen", fake_urlopen),
        ):
            provider = Wan2I2VApiProvider()
            response = provider._submit_official_job(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model=provider.model,
                    prompt="test prompt",
                    negative_prompt="blur",
                    params={"frames": 49, "steps": 4, "guidance": 1, "fps": 16, "size": "832*480"},
                ),
                client_job_id="client-job-id",
                image_input=_WanImageInput(
                    image_name="input.png",
                    image_bytes=b"png-bytes",
                    image_mime="image/png",
                ),
            )

        body = captured["body"]
        self.assertEqual(response["id"], "job-id")
        self.assertEqual(captured["url"], "http://192.0.2.25:18083/v1/i2v/jobs")
        self.assertEqual(captured["headers"]["X-api-key"], "test-key")
        self.assertIn(b'name="frames"\r\n\r\n49\r\n', body)
        self.assertIn(b'name="steps"\r\n\r\n4\r\n', body)
        self.assertIn(b'name="guidance"\r\n\r\n1.0\r\n', body)
        self.assertIn(b'name="size"\r\n\r\n832*480\r\n', body)


if __name__ == "__main__":
    unittest.main()
