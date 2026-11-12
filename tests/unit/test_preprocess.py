from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from whisper_ui.core.exceptions import PreprocessError
from whisper_ui.pipeline.preprocess import PreprocessStage, _resolved_filters


@patch("whisper_ui.pipeline.preprocess.subprocess.run")
def test_ffmpeg_failure_removes_partial_wav(mock_run, tmp_path):
    src = tmp_path / "test.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 100)
    partial = tmp_path / "test.16k.wav"

    def run_then_fail(cmd, **kwargs):
        partial.write_bytes(b"half-written")
        return MagicMock(returncode=1, stderr="boom")

    mock_run.side_effect = run_then_fail
    stage = PreprocessStage()
    with pytest.raises(PreprocessError, match="FFmpeg failed"):
        stage.execute({"input_path": str(src)})
    assert not partial.exists()


def test_auto_processing_boosts_quiet_dynamic_speech_conservatively():
    filters, resolved = _resolved_filters(
        {
            "audio_processing": "auto",
            "audio_target_lufs": -16,
            "audio_max_gain_db": 12,
            "audio_highpass_hz": 80,
            "audio_denoise": "auto",
            "audio_compression": "auto",
        },
        {"integrated_lufs": -34.0, "loudness_range_lu": 14.0, "true_peak_dbfs": -12.0},
    )
    assert resolved["denoise"] == "light"
    assert resolved["compression"] == "strong"
    assert resolved["applied_target_lufs"] == -22.0
    assert any("loudnorm=I=-22.0" in f for f in filters)


def test_processing_off_only_converts():
    filters, resolved = _resolved_filters({"audio_processing": "off"}, {"integrated_lufs": -30.0})
    assert filters == []
    assert resolved["mode"] == "off"
