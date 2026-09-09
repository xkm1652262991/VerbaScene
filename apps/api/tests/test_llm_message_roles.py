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


class _FakeStreamResponse:
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self):
        chunks = [
            {
                "id": "chat-stream-1",
                "object": "chat.completion.chunk",
                "model": "kimi-k3",
                "choices": [{"delta": {"reasoning_content": "check JSON"}}],
            },
            {
                "id": "chat-stream-1",
                "object": "chat.completion.chunk",
                "model": "kimi-k3",
                "choices": [{"delta": {"content": '{"ok":'}}],
            },
            {
                "id": "chat-stream-1",
                "object": "chat.completion.chunk",
                "model": "kimi-k3",
                "choices": [
                    {"delta": {"content": " true}"}, "finish_reason": "stop"}
                ],
            },
            {
                "id": "chat-stream-1",
                "object": "chat.completion.chunk",
                "model": "kimi-k3",
                "choices": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        ]
        self._lines = iter(
            [
                *(f"data: {json.dumps(chunk)}\n\n".encode("utf-8") for chunk in chunks),
                b"data: [DONE]\n\n",
            ]
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def readline(self) -> bytes:
        return next(self._lines, b"")

    def read(self) -> bytes:
        return b""


class _FakeTruncatedStreamResponse(_FakeStreamResponse):
    def __init__(self):
        self._lines = iter(
            [
                b'data: {"id":"partial","choices":[{"delta":{"content":"{\\"ok\\":"}}]}\n\n'
            ]
        )


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

    def test_kimi_k3_omits_unsupported_temperature(self):
        providers = (
            (OpenAICompatibleLLMProvider(), "app.providers.openai_compatible_llm.urlopen"),
            (DashScopeLLMProvider(), "app.providers.dashscope.urlopen"),
        )
        for provider, patch_target in providers:
            with self.subTest(provider=provider.name):
                captured = self._submit_and_capture(
                    provider,
                    patch_target,
                    model="kimi-k3",
                )
                self.assertNotIn("temperature", captured)
                self.assertEqual(captured["response_format"], {"type": "json_object"})

    def test_openai_compatible_adapters_stream_and_aggregate_progress(self):
        providers = (
            (OpenAICompatibleLLMProvider(), "app.providers.openai_compatible_llm.urlopen"),
            (DashScopeLLMProvider(), "app.providers.dashscope.urlopen"),
        )
        for provider, patch_target in providers:
            with self.subTest(provider=provider.name):
                provider.base_url = "https://llm.test/v1"
                provider.api_key = "test-key"
                captured: dict = {}
                progress_events: list[dict] = []

                def fake_urlopen(request, timeout):
                    _ = timeout
                    captured.update(json.loads(request.data.decode("utf-8")))
                    return _FakeStreamResponse()

                with patch(patch_target, fake_urlopen):
                    response = provider.submit_streaming(
                        ProviderRequest(
                            project_id="project-id",
                            task_id="task-id",
                            model="kimi-k3",
                            system_prompt="Return JSON",
                            prompt='{"input": "story"}',
                            params={"response_format": {"type": "json_object"}},
                        ),
                        on_progress=progress_events.append,
                    )

                self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
                self.assertEqual(response.raw_response["text"], '{"ok": true}')
                provider_response = response.raw_response["provider_response"]
                self.assertEqual(
                    provider_response["choices"][0]["message"]["reasoning_content"],
                    "check JSON",
                )
                self.assertEqual(provider_response["usage"]["completion_tokens"], 7)
                self.assertTrue(captured["stream"])
                self.assertEqual(captured["stream_options"], {"include_usage": True})
                self.assertEqual(progress_events[0]["event"], "provider_accepted")
                self.assertEqual(progress_events[-1]["event"], "stream_complete")
                self.assertEqual(progress_events[-1]["content_chars"], 12)

    def test_stream_progress_exception_aborts_the_active_read(self):
        provider = OpenAICompatibleLLMProvider()
        provider.base_url = "https://llm.test/v1"
        provider.api_key = "test-key"

        class StopStream(RuntimeError):
            pass

        def stop_on_first_progress(_event):
            raise StopStream("cancelled")

        with patch("app.providers.openai_compatible_llm.urlopen", return_value=_FakeStreamResponse()):
            with self.assertRaises(StopStream):
                provider.submit_streaming(
                    ProviderRequest(
                        project_id="project-id",
                        task_id="task-id",
                        model="kimi-k3",
                        system_prompt="Return JSON",
                        prompt='{"input": "story"}',
                    ),
                    on_progress=stop_on_first_progress,
                )

    def test_truncated_stream_is_not_treated_as_a_retryable_unsubmitted_call(self):
        provider = OpenAICompatibleLLMProvider()
        provider.base_url = "https://llm.test/v1"
        provider.api_key = "test-key"

        with patch(
            "app.providers.openai_compatible_llm.urlopen",
            return_value=_FakeTruncatedStreamResponse(),
        ):
            response = provider.submit_streaming(
                ProviderRequest(
                    project_id="project-id",
                    task_id="task-id",
                    model="kimi-k3",
                    system_prompt="Return JSON",
                    prompt='{"input": "story"}',
                )
            )

        self.assertEqual(response.status, ProviderStatus.FAILED)
        self.assertIsNotNone(response.error)
        self.assertEqual(response.error.submission_state.value, "unknown")

    def _assert_message_roles(self, provider, patch_target: str):
        captured = self._submit_and_capture(provider, patch_target, model="test-model")

        self.assertEqual(captured["messages"][0], {"role": "system", "content": "SYSTEM QUALITY POLICY"})
        self.assertEqual(
            captured["messages"][1],
            {"role": "user", "content": '{"imported_script": "USER STORY DATA"}'},
        )
        self.assertEqual(captured["temperature"], 0.2)
        self.assertEqual(captured["response_format"], {"type": "json_object"})

    def _submit_and_capture(self, provider, patch_target: str, *, model: str) -> dict:
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
                    model=model,
                    system_prompt="SYSTEM QUALITY POLICY",
                    prompt='{"imported_script": "USER STORY DATA"}',
                    params={
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
            )

        self.assertEqual(response.status, ProviderStatus.SUCCEEDED)
        return captured


if __name__ == "__main__":
    unittest.main()
