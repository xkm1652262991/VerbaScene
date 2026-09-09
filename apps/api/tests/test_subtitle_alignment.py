from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from app.exports.subtitle_alignment import (
    align_dialogues_to_tokens,
    align_export_subtitles,
)
from app.platform.media.subprocess_runner import MediaProcessResult
from app.providers.openai_compatible_asr import ASRTimedToken, ASRTranscript


class _TextOnlyTranscriber:
    name = "fake_asr"
    model = "qwen3-asr"

    def transcribe(self, audio_path: Path, *, duration_sec=None):
        if "region-01" in audio_path.name:
            text = "You are much too slow to race me."
        elif "region-02" in audio_path.name:
            text = "Slow is fine. I will not stop moving."
        else:
            text = (
                "You are much too slow to race me. "
                "Slow is fine. I will not stop moving."
            )
        return ASRTranscript(
            text=text,
            language="en",
            tokens=(),
            response_format="json",
        )


class SubtitleAlignmentTests(unittest.TestCase):
    def test_dialogues_match_monotonically_to_timed_tokens(self):
        words = [
            "You",
            "are",
            "much",
            "too",
            "slow",
            "to",
            "race",
            "me",
            "Slow",
            "is",
            "fine",
            "I",
            "will",
            "not",
            "stop",
            "moving",
        ]
        tokens = [
            ASRTimedToken(
                text=word,
                start_sec=Decimal("0.25") + Decimal(index) * Decimal("0.4"),
                end_sec=Decimal("0.55") + Decimal(index) * Decimal("0.4"),
            )
            for index, word in enumerate(words)
        ]
        dialogues = [
            {
                "id": "dialogue-1",
                "sequence_order": 0,
                "text": "You are much too slow to race me!",
            },
            {
                "id": "dialogue-2",
                "sequence_order": 1,
                "text": "Slow is fine. I will not stop moving.",
            },
        ]

        aligned = align_dialogues_to_tokens(
            dialogues,
            tokens,
            clip_duration=Decimal("8"),
            min_match_score=0.55,
        )

        self.assertEqual(set(aligned), {"dialogue-1", "dialogue-2"})
        self.assertGreaterEqual(aligned["dialogue-1"].confidence, 0.99)
        self.assertLess(
            aligned["dialogue-1"].end_sec,
            aligned["dialogue-2"].start_sec,
        )

    def test_low_confidence_transcript_is_not_forced_onto_dialogue(self):
        aligned = align_dialogues_to_tokens(
            [
                {
                    "id": "dialogue-1",
                    "sequence_order": 0,
                    "text": "Slow is fine.",
                }
            ],
            [
                ASRTimedToken(
                    text="completely",
                    start_sec=Decimal("1"),
                    end_sec=Decimal("2"),
                ),
                ASRTimedToken(
                    text="different",
                    start_sec=Decimal("2"),
                    end_sec=Decimal("3"),
                ),
            ],
            clip_duration=Decimal("4"),
            min_match_score=0.55,
        )

        self.assertEqual(aligned, {})

    def test_text_only_asr_uses_vad_regions_then_returns_global_timings(self):
        snapshot = {
            "subtitle_mode": "bilingual",
            "shots": [
                {
                    "id": "shot-1",
                    "duration_sec": "8",
                    "shot_no": 1,
                    "shot_card": {},
                }
            ],
            "video_assets": [{"id": "asset-1", "uri": "/tmp/source.mp4"}],
            "dialogues": [
                {
                    "id": "dialogue-1",
                    "shot_id": "shot-1",
                    "sequence_order": 0,
                    "start_time": None,
                    "text": "You are much too slow to race me!",
                },
                {
                    "id": "dialogue-2",
                    "shot_id": "shot-1",
                    "sequence_order": 1,
                    "start_time": None,
                    "text": "Slow is fine. I will not stop moving.",
                },
            ],
        }

        def fake_runner(args, **_kwargs):
            if any("silencedetect=" in value for value in args):
                return MediaProcessResult(
                    returncode=0,
                    output_tail=(
                        "silence_start: 0\n"
                        "silence_end: 0.2\n"
                        "silence_start: 2.9\n"
                        "silence_end: 3.3\n"
                        "silence_start: 6.6\n"
                        "silence_end: 8.0\n"
                    ),
                )
            output_path = Path(args[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"wav")
            return MediaProcessResult(returncode=0, output_tail="")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = align_export_subtitles(
                snapshot,
                Path(tmpdir),
                cancel_requested=lambda: False,
                transcriber=_TextOnlyTranscriber(),
                media_runner=fake_runner,
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.vad_clip_count, 1)
        self.assertEqual(result.aligned_count, 2)
        self.assertLess(
            result.timings["dialogue-1"].end_sec,
            result.timings["dialogue-2"].start_sec,
        )
        self.assertLess(result.timings["dialogue-2"].end_sec, Decimal("7"))


if __name__ == "__main__":
    unittest.main()
