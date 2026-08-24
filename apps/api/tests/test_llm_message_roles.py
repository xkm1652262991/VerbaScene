import json
import unittest
from unittest.mock import patch

from app.providers.dashscope import DashScopeLLMProvider
from app.providers.openai_compatible_llm import OpenAICompatibleLLMProvider
from app.providers.types import ProviderRequest, ProviderStatus


class _FakeResponse:
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {"choices": [{"message": {"content": '{"ok": true}'}}]}
        ).encode("utf-8")


class LLMMessageRoleTests(unittest.TestCase):
    def test_openai_compatible_keeps_system_policy_separate_from_user_data(self):
        self._assert_message_roles(
            OpenAICompatibleLLMProvider(),
            "app.providers.openai_compatible_llm.urlopen",
        )

    def test_dashscope_keeps_system_policy_separate_from_user_data(self):
        self._assert_message_roles(
            DashScopeLLMProvider(),
            "app.providers.dashscope.urlopen",
        )

    def _assert_message_roles(self, provider, patch_target: str):
        provider.base_url = "https://llm.test/v1"
        provider.api_key = "test-key"
        captured: dict = {}

        def fake_urlopen(request, timeout):
            _ = timeout
            captured.update(json.loads(request.data.decode("utf-8")))
            return _FakeResponse()

        with patch(patch_target, fake_urlopen):
            response = provider.submit(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model="test-model",
                    system_prompt="SYSTEM QUALITY POLICY",
                    prompt='{"imported_script": "USER STORY DATA"}',
                    params={
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
            )

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        self.assertEqual(captured["messages"][0], {"role": "system", "content": "SYSTEM QUALITY POLICY"})
        self.assertEqual(
            captured["messages"][1],
            {"role": "user", "content": '{"imported_script": "USER STORY DATA"}'},
        )
        self.assertEqual(captured["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
