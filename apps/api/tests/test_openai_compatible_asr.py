from __future__ import annotations

from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app.providers.openai_compatible_asr import OpenAICompatibleASRAdapter


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class OpenAICompatibleASRTests(unittest.TestCase):
    def test_verbose_response_normalizes_word_timestamps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "clip.wav"
            audio_path.write_bytes(b"wav")

            with (
                patch("app.core.config.settings.asr_api_key", "test-key"),
                patch("app.core.config.settings.asr_base_url", "http://asr.test/v1"),
                patch("app.core.config.settings.asr_model", "qwen3-asr"),
                patch("app.core.config.settings.asr_response_format", "auto"),
                patch(
                    "app.providers.openai_compatible_asr.urlopen",
                    return_value=_FakeResponse(
                        {
                            "text": "Hello Tiny",
                            "language": "en",
                            "words": [
                                {"word": "Hello", "start": 0.2, "end": 0.8},
                                {"word": "Tiny", "start": 0.9, "end": 1.4},
                            ],
                        }
                    ),
                ),
            ):
                transcript = OpenAICompatibleASRAdapter().transcribe(
                    audio_path,
                    duration_sec=Decimal("2"),
                )

        self.assertEqual(transcript.response_format, "verbose_json")
        self.assertEqual(transcript.text, "Hello Tiny")
        self.assertEqual(len(transcript.tokens), 2)
        self.assertEqual(transcript.tokens[0].start_sec, Decimal("0.2"))
        self.assertEqual(transcript.tokens[-1].end_sec, Decimal("1.4"))

    def test_unsupported_verbose_json_retries_plain_json_once(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=BytesIO(
                        json.dumps(
                            {
                                "error": {
                                    "message": "Currently do not support verbose_json",
                                    "code": "400",
                                }
                            }
                        ).encode("utf-8")
                    ),
                )
            return _FakeResponse({"text": "Slow is fine."})

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "clip.wav"
            audio_path.write_bytes(b"wav")
            with (
                patch("app.core.config.settings.asr_api_key", "test-key"),
                patch("app.core.config.settings.asr_base_url", "http://asr.test/v1"),
                patch("app.core.config.settings.asr_model", "qwen3-asr"),
                patch("app.core.config.settings.asr_response_format", "auto"),
                patch(
                    "app.providers.openai_compatible_asr.urlopen",
                    side_effect=fake_urlopen,
                ),
            ):
                adapter = OpenAICompatibleASRAdapter()
                first = adapter.transcribe(audio_path, duration_sec=Decimal("2"))
                second = adapter.transcribe(audio_path, duration_sec=Decimal("2"))

        self.assertEqual(len(calls), 3)
        self.assertEqual(first.response_format, "json")
        self.assertEqual(second.response_format, "json")
        self.assertEqual(first.text, "Slow is fine.")
        self.assertEqual(first.tokens, ())
        self.assertIn(b'verbose_json', calls[0][0].data)
        self.assertNotIn(b'verbose_json', calls[1][0].data)
        self.assertNotIn(b'verbose_json', calls[2][0].data)


if __name__ == "__main__":
    unittest.main()
