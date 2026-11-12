"""Unit tests for pipeline stages (transcribe, align, diarize, assign_speakers)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from whisper_ui.core.messages import (
    ASSIGN_FAILED,
    ASSIGN_SKIPPED,
    DIARIZE_DONE,
    DIARIZE_SKIPPED,
    DIARIZE_SKIPPED_DISABLED,
)
from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.assign_speakers import AssignSpeakersStage
from whisper_ui.pipeline.diarize import (
    DiarizeStage,
)
from whisper_ui.pipeline.transcribe import TranscribeStage


class TestTranscribeStage:
    def test_execute_sets_context_keys(self):
        stage = TranscribeStage(model_name="base", compute_type="int8", device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_model.return_value = MagicMock(transcribe=MagicMock(return_value={"segments": []}))
        mock_whisperx.load_audio.return_value = "audio_array"

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            context = {"audio_path": "/tmp/test.wav", "language": "zh", "batch_size": 4}
            result = stage.execute(context)

        assert "transcription_result" in result
        assert "whisperx_audio" in result


class TestAlignStage:
    def test_execute_sets_aligned_result(self):
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.return_value = ("model", "metadata")
        mock_whisperx.align.return_value = {"segments": [{"text": "hi"}]}

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            context = {
                "transcription_result": {"segments": [], "language": "zh"},
                "whisperx_audio": "audio",
            }
            result = stage.execute(context)

        assert "aligned_result" in result

    def test_alignment_failure_falls_back_to_transcription_result(self):
        """An align failure must keep speaker assignment possible: the
        unaligned transcription becomes the aligned_result so assign_speakers
        can still attach segment-level speakers instead of skipping entirely
        (which would silently discard diarization too)."""
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.side_effect = ValueError("model not found")
        transcription = {"segments": [{"text": "hi"}], "language": "ja"}

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            context = {
                "transcription_result": transcription,
                "whisperx_audio": "audio",
                "language": "ja",
            }
            result = stage.execute(context)

        assert result["aligned_result"] is transcription


class TestDiarizeStage:
    def test_disabled_skips(self):
        stage = DiarizeStage(enabled=False, device="cpu")
        progress_calls = []
        result = stage.execute({}, on_progress=lambda p, m: progress_calls.append((p, m)))
        assert result.get("diarize_result") is None
        assert progress_calls[-1] == (1.0, DIARIZE_SKIPPED_DISABLED)

    def test_no_token_skips(self):
        stage = DiarizeStage(hf_token="", device="cpu")
        progress_calls = []
        result = stage.execute({}, on_progress=lambda p, m: progress_calls.append((p, m)))
        assert result.get("diarize_result") is None
        assert progress_calls[-1] == (1.0, DIARIZE_SKIPPED)

    def test_execute_success(self):
        stage = DiarizeStage(hf_token="test-token", device="cpu")
        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock(return_value="diarize_segments")
        mock_pipeline_cls.return_value = mock_pipeline_instance

        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        with patch.dict("sys.modules", {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}):
            context = {"audio_path": "/tmp/test.wav"}
            progress_calls = []
            result = stage.execute(context, on_progress=lambda p, m: progress_calls.append((p, m)))

        assert result["diarize_result"] == "diarize_segments"
        assert progress_calls[-1] == (1.0, DIARIZE_DONE)

    def test_rq_timeout_is_not_wrapped_as_diarization_error(self):
        from rq.timeouts import JobTimeoutException

        stage = DiarizeStage(hf_token="test-token", device="cpu")
        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock(
            side_effect=JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")
        )
        mock_pipeline_cls.return_value = mock_pipeline_instance

        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        with (
            patch.dict("sys.modules", {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}),
            pytest.raises(JobTimeoutException, match="Task exceeded"),
        ):
            stage.execute({"audio_path": "/tmp/test.wav"})


class TestAssignSpeakersStage:
    def test_no_diarize_skips(self):
        stage = AssignSpeakersStage()
        progress_calls = []
        result = stage.execute(
            {"diarize_result": None, "aligned_result": {"segments": []}},
            on_progress=lambda p, m: progress_calls.append((p, m)),
        )
        assert "final_result" not in result
        assert progress_calls[-1] == (1.0, ASSIGN_SKIPPED)

    def test_exception_falls_back(self):
        stage = AssignSpeakersStage()
        mock_whisperx = MagicMock()
        mock_whisperx.assign_word_speakers.side_effect = RuntimeError("fail")
        aligned = {"segments": [{"text": "hello"}]}
        progress_calls = []

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            result = stage.execute(
                {"diarize_result": "data", "aligned_result": aligned},
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )

        assert result["final_result"] is aligned
        assert progress_calls[-1] == (1.0, ASSIGN_FAILED)
