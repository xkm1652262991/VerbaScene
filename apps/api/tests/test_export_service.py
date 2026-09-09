from decimal import Decimal
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import HTTPException
from PIL import Image, ImageDraw
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import create_database_engine, initialize_database
from app.exports.subtitle_alignment import AlignedDialogueTiming
from app.models import Asset, Project, Shot
from app.services.export_service import (
    SubtitleCue,
    SubtitleFontError,
    _build_ffmpeg_args,
    _dialogue_subtitle_cues,
    _font_supports_text,
    _media_input,
    _parse_srt_cues,
    _probe_has_audio,
    _run_ffmpeg,
    _subtitle_font,
    _wrap_subtitle_text,
)
from app.services.asset_service import extract_video_frame_candidate


class ExportServiceTests(unittest.TestCase):
    def test_media_input_resolves_public_storage_uri_to_local_path(self):
        original_storage_root = settings.storage_root
        original_public_base_url = settings.public_storage_base_url

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                settings.storage_root = tmpdir
                settings.public_storage_base_url = "http://testserver/storage"
                media_path = Path(tmpdir) / "projects" / "project-id" / "assets" / "clip.mp4"
                media_path.parent.mkdir(parents=True, exist_ok=True)
                media_path.write_bytes(b"video")

                resolved = _media_input("http://testserver/storage/projects/project-id/assets/clip.mp4")

                self.assertEqual(resolved, str(media_path.resolve()))
        finally:
            settings.storage_root = original_storage_root
            settings.public_storage_base_url = original_public_base_url

    def test_media_input_rejects_mock_uri(self):
        with self.assertRaises(HTTPException) as exc:
            _media_input("mock://exports/demo.mp4")

        self.assertEqual(exc.exception.status_code, 409)
        self.assertIn("Mock media cannot be exported", exc.exception.detail)

    def test_build_ffmpeg_args_preserves_native_audio_and_injects_silence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "with-audio.mp4"
            second = Path(tmpdir) / "silent.mp4"
            first.touch()
            second.touch()
            output_path = Path(tmpdir) / "final.mp4"

            args = _build_ffmpeg_args(
                resolution="1280x720",
                duration_sec=Decimal("5"),
                video_assets=[
                    SimpleNamespace(uri=str(first)),
                    SimpleNamespace(uri=str(second)),
                ],
                video_durations=[Decimal("2"), Decimal("3")],
                video_has_audio=[True, False],
                subtitle_cues=[],
                output_path=output_path,
            )
            filter_complex = args[args.index("-filter_complex") + 1]

            self.assertIn("[0:a]aformat=", filter_complex)
            self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", filter_complex)
            self.assertIn("concat=n=2:v=1:a=1", filter_complex)
            self.assertNotIn("amix=", filter_complex)
            self.assertEqual(args[-1], str(output_path))

    def test_dialogue_subtitle_cues_support_english_and_bilingual_modes(self):
        shot = SimpleNamespace(
            id="shot-1",
            duration_sec=Decimal("4"),
            shot_card={
                "beats": [
                    {"beat_id": "beat-1"},
                    {"beat_id": "beat-2"},
                ]
            },
        )
        dialogue = SimpleNamespace(
            id="dialogue-1",
            shot_id="shot-1",
            beat_id="beat-2",
            start_time=None,
            end_time=None,
            text="Look at the red ball!",
            translation_zh="看这个红色的球！",
        )

        english = _dialogue_subtitle_cues(dialogues=[dialogue], shots=[shot], mode="en")
        bilingual = _dialogue_subtitle_cues(
            dialogues=[dialogue],
            shots=[shot],
            mode="bilingual",
        )

        self.assertEqual(english[0].start_sec, Decimal("2"))
        self.assertEqual(english[0].end_sec, Decimal("3.85"))
        self.assertEqual(english[0].text, "Look at the red ball!")
        self.assertEqual(bilingual[0].start_sec, english[0].start_sec)
        self.assertEqual(bilingual[0].end_sec, english[0].end_sec)
        self.assertEqual(bilingual[0].text, "Look at the red ball!\n看这个红色的球！")

    def test_fallback_subtitle_does_not_fill_a_long_shot(self):
        shot = SimpleNamespace(
            id="shot-1",
            duration_sec=Decimal("8"),
            shot_card={"beats": [{"beat_id": "beat-1"}, {"beat_id": "beat-2"}]},
        )
        dialogue = SimpleNamespace(
            id="dialogue-1",
            shot_id="shot-1",
            beat_id=None,
            sequence_order=0,
            start_time=None,
            end_time=None,
            text="It's stuck.",
            translation_zh="它卡住了。",
        )

        cues = _dialogue_subtitle_cues(
            dialogues=[dialogue],
            shots=[shot],
            mode="bilingual",
        )

        self.assertEqual(cues[0].start_sec, Decimal("0"))
        self.assertEqual(cues[0].end_sec, Decimal("1.5"))
        self.assertLess(cues[0].end_sec, shot.duration_sec)

    def test_missing_and_invalid_beat_ids_share_one_non_overlapping_window(self):
        shot = SimpleNamespace(
            id="shot-1",
            duration_sec=Decimal("10"),
            shot_card={"beats": [{"beat_id": "beat-1"}, {"beat_id": "beat-2"}]},
        )
        dialogues = [
            SimpleNamespace(
                id="dialogue-1",
                shot_id="shot-1",
                beat_id=None,
                sequence_order=0,
                start_time=None,
                end_time=None,
                text="Oh no!",
                translation_zh="哎呀！",
            ),
            SimpleNamespace(
                id="dialogue-2",
                shot_id="shot-1",
                beat_id="missing-beat",
                sequence_order=1,
                start_time=None,
                end_time=None,
                text="The ball!",
                translation_zh="球！",
            ),
        ]

        cues = _dialogue_subtitle_cues(
            dialogues=dialogues,
            shots=[shot],
            mode="en",
        )

        self.assertEqual(
            [(cue.start_sec, cue.end_sec) for cue in cues],
            [(Decimal("0"), Decimal("1.5")), (Decimal("5"), Decimal("6.5"))],
        )
        self.assertLess(cues[0].end_sec, cues[1].start_sec)

    def test_explicit_subtitle_end_time_remains_authoritative(self):
        shot = SimpleNamespace(
            id="shot-1",
            duration_sec=Decimal("10"),
            shot_card={},
        )
        dialogue = SimpleNamespace(
            id="dialogue-1",
            shot_id="shot-1",
            beat_id=None,
            sequence_order=0,
            start_time=Decimal("1"),
            end_time=Decimal("8"),
            text="Hold this subtitle.",
            translation_zh=None,
        )

        cues = _dialogue_subtitle_cues(
            dialogues=[dialogue],
            shots=[shot],
            mode="en",
        )

        self.assertEqual(cues[0].start_sec, Decimal("1"))
        self.assertEqual(cues[0].end_sec, Decimal("8"))

    def test_asr_timing_overrides_heuristic_but_not_subtitle_text(self):
        shot = SimpleNamespace(
            id="shot-1",
            duration_sec=Decimal("10"),
            shot_card={},
        )
        dialogue = SimpleNamespace(
            id="dialogue-1",
            shot_id="shot-1",
            beat_id=None,
            sequence_order=0,
            start_time=None,
            end_time=None,
            text="Slow is fine.",
            translation_zh="慢一点没关系。",
        )

        cues = _dialogue_subtitle_cues(
            dialogues=[dialogue],
            shots=[shot],
            mode="bilingual",
            aligned_timings={
                "dialogue-1": AlignedDialogueTiming(
                    dialogue_id="dialogue-1",
                    start_sec=Decimal("3.2"),
                    end_sec=Decimal("5.6"),
                    confidence=0.98,
                )
            },
        )

        self.assertEqual(cues[0].start_sec, Decimal("3.2"))
        self.assertEqual(cues[0].end_sec, Decimal("5.6"))
        self.assertEqual(cues[0].text, "Slow is fine.\n慢一点没关系。")
        self.assertEqual(cues[0].timing_source, "asr")
        self.assertEqual(cues[0].confidence, 0.98)

    def test_fallback_subtitle_reading_duration_is_capped_at_four_seconds(self):
        shot = SimpleNamespace(
            id="shot-1",
            duration_sec=Decimal("20"),
            shot_card={},
        )
        dialogue = SimpleNamespace(
            id="dialogue-1",
            shot_id="shot-1",
            beat_id=None,
            sequence_order=0,
            start_time=None,
            end_time=None,
            text="This deliberately long subtitle contains far more words than a short cue.",
            translation_zh=None,
        )

        cues = _dialogue_subtitle_cues(
            dialogues=[dialogue],
            shots=[shot],
            mode="en",
        )

        self.assertEqual(cues[0].end_sec - cues[0].start_sec, Decimal("4"))

    def test_subtitle_font_covers_bilingual_text_instead_of_silent_tofu(self):
        text = "Bye, Mimi.\n再见，咪咪。"
        font = _subtitle_font(32, text)

        self.assertTrue(_font_supports_text(font, text))
        self.assertNotIn("Arial.ttf", str(getattr(font, "path", "")))

    def test_invalid_explicit_subtitle_font_fails_clearly(self):
        original_font_path = settings.subtitle_font_path
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                invalid_font = Path(tmpdir) / "invalid-font.ttf"
                invalid_font.write_bytes(b"not-a-font")
                settings.subtitle_font_path = str(invalid_font)

                with self.assertRaises(SubtitleFontError) as raised:
                    _subtitle_font(32, "Hello\n你好")

                self.assertIn("cannot be loaded", str(raised.exception))
        finally:
            settings.subtitle_font_path = original_font_path

    def test_long_chinese_subtitle_wraps_without_spaces(self):
        text = "这是一句需要自动换行的较长中文字幕文本。"
        font = _subtitle_font(28, text)
        draw = ImageDraw.Draw(Image.new("RGBA", (320, 180)))

        lines = _wrap_subtitle_text(text, draw, font, 120, 2)

        self.assertGreater(len(lines), 1)
        for line in lines:
            bounds = draw.textbbox((0, 0), line, font=font, stroke_width=2)
            self.assertLessEqual(bounds[2] - bounds[0], 120)

    def test_parse_srt_cues_remains_compatible_with_legacy_exports(self):
        cues = _parse_srt_cues(
            "1\n"
            "00:00:05,350 --> 00:00:06,374\n"
            "Hello, Mia!\n\n"
            "2\n"
            "00:00:07,850 --> 00:00:09,002\n"
            "你好，米娅！\n"
        )

        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start_sec, Decimal("5.350"))
        self.assertEqual(cues[0].end_sec, Decimal("6.374"))
        self.assertEqual(cues[1].text, "你好，米娅！")

    def test_real_ffmpeg_concat_handles_native_and_missing_audio_for_all_subtitle_modes(self):
        if not _command_available(settings.ffmpeg_path):
            self.skipTest("ffmpeg is unavailable")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clip_with_audio = root / "with-audio.mp4"
            clip_without_audio = root / "without-audio.mp4"
            _make_test_clip(clip_with_audio, color="blue", include_audio=True)
            _make_test_clip(clip_without_audio, color="yellow", include_audio=False)
            self.assertTrue(_probe_has_audio(str(clip_with_audio)))
            self.assertFalse(_probe_has_audio(str(clip_without_audio)))

            cue_sets = {
                "none": [],
                "en": [
                    SubtitleCue(
                        start_sec=Decimal("0.1"),
                        end_sec=Decimal("0.9"),
                        text="Hello!",
                    )
                ],
                "bilingual": [
                    SubtitleCue(
                        start_sec=Decimal("1.1"),
                        end_sec=Decimal("1.9"),
                        text="A red ball!\n一个红色的球！",
                    )
                ],
            }
            for mode, cues in cue_sets.items():
                with self.subTest(mode=mode):
                    output = root / f"{mode}.mp4"
                    args = _build_ffmpeg_args(
                        resolution="320x180",
                        duration_sec=Decimal("2"),
                        video_assets=[
                            SimpleNamespace(uri=str(clip_with_audio)),
                            SimpleNamespace(uri=str(clip_without_audio)),
                        ],
                        video_durations=[Decimal("1"), Decimal("1")],
                        video_has_audio=[True, False],
                        subtitle_cues=cues,
                        output_path=output,
                    )
                    _run_ffmpeg(args, output)
                    probe = _probe_media(output)
                    self.assertEqual(probe["video_streams"], 1)
                    self.assertEqual(probe["audio_streams"], 1)
                    self.assertGreaterEqual(probe["duration"], 1.8)

    def test_extract_video_frame_creates_local_pending_image_candidate(self):
        if not _command_available(settings.ffmpeg_path):
            self.skipTest("ffmpeg is unavailable")
        original_storage_root = settings.storage_root
        original_public_base_url = settings.public_storage_base_url
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                settings.storage_root = str(root / "storage")
                settings.public_storage_base_url = "http://testserver/storage"
                database_path = root / "frame-extraction.sqlite3"
                engine = create_database_engine(
                    f"sqlite+pysqlite:///{database_path.as_posix()}"
                )
                initialize_database(engine)
                session_factory = sessionmaker(
                    bind=engine,
                    autoflush=False,
                    autocommit=False,
                )
                with session_factory() as db:
                    project = Project(title="本地截帧", style="儿童二维动画")
                    db.add(project)
                    db.flush()
                    shot = Shot(
                        project_id=project.id,
                        shot_no=1,
                        description="Mia举起红色小球。",
                        duration_sec=Decimal("1"),
                    )
                    db.add(shot)
                    db.flush()
                    source_path = (
                        Path(settings.storage_root)
                        / "projects"
                        / project.id
                        / "assets"
                        / "clip.mp4"
                    )
                    source_path.parent.mkdir(parents=True, exist_ok=True)
                    _make_test_clip(source_path, color="red", include_audio=True)
                    source = Asset(
                        project_id=project.id,
                        asset_type="video",
                        asset_role="shot_video",
                        entity_type="shot",
                        entity_id=shot.id,
                        version=1,
                        uri=(
                            f"{settings.public_storage_base_url}/projects/"
                            f"{project.id}/assets/clip.mp4"
                        ),
                        mime_type="video/mp4",
                        duration_sec=Decimal("1"),
                        status="approved",
                        is_selected=True,
                    )
                    db.add(source)
                    db.commit()

                    candidate, task = extract_video_frame_candidate(
                        db,
                        source.id,
                        time_sec=Decimal("0.2"),
                    )

                    self.assertEqual(task.status, "succeeded")
                    self.assertEqual(candidate.candidate_type, "extracted_frame")
                    self.assertEqual(candidate.asset_type, "image")
                    self.assertEqual(candidate.asset_role, "shot_storyboard")
                    self.assertEqual(candidate.entity_id, shot.id)
                    self.assertEqual(candidate.status, "pending_review")
                    self.assertTrue(
                        Path(settings.storage_root)
                        .joinpath(
                            candidate.uri.removeprefix(
                                settings.public_storage_base_url.rstrip("/") + "/"
                            )
                        )
                        .is_file()
                    )
                engine.dispose()
        finally:
            settings.storage_root = original_storage_root
            settings.public_storage_base_url = original_public_base_url


def _command_available(command: str) -> bool:
    try:
        return subprocess.run(
            [command, "-version"],
            capture_output=True,
            timeout=10,
        ).returncode == 0
    except OSError:
        return False


def _make_test_clip(path: Path, *, color: str, include_audio: bool) -> None:
    args = [
        settings.ffmpeg_path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=320x180:d=1:r=24",
    ]
    if include_audio:
        args.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=1",
                "-shortest",
                "-c:a",
                "aac",
            ]
        )
    args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-2000:])


def _probe_media(path: Path) -> dict[str, float | int]:
    ffprobe = str(Path(settings.ffmpeg_path).with_name("ffprobe"))
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    payload = json.loads(completed.stdout)
    stream_types = [item.get("codec_type") for item in payload.get("streams", [])]
    return {
        "video_streams": stream_types.count("video"),
        "audio_streams": stream_types.count("audio"),
        "duration": float(payload["format"]["duration"]),
    }


if __name__ == "__main__":
    unittest.main()
