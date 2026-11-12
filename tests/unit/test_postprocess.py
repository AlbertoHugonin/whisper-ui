from __future__ import annotations

from whisper_ui.pipeline.postprocess import PostprocessStage


def test_postprocess_builds_segments():
    stage = PostprocessStage()
    raw = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": " Hello ", "speaker": "SPEAKER_00"},
            {"start": 1.0, "end": 2.0, "text": "World"},
        ]
    }
    context = {"final_result": raw, "language": "en", "duration": 2.0}
    result = stage.execute(context)
    transcript = result["transcript_result"]
    assert len(transcript.segments) == 2
    assert transcript.segments[0].text == "Hello"
    assert transcript.segments[0].speaker == "SPEAKER_00"
    assert transcript.segments[1].speaker is None
    assert transcript.language == "en"
    assert transcript.duration == 2.0


def test_postprocess_prefers_detected_language_over_context():
    """With language=auto the context holds the sentinel; the language the
    model actually detected (carried by the transcription result) must win.
    """
    stage = PostprocessStage()
    raw = {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}]}
    context = {"transcription_result": raw, "language": "auto", "duration": 1.0}
    result = stage.execute(context)
    assert result["transcript_result"].language == "en"


def _raw_with_texts(texts: list[str]) -> dict:
    return {"segments": [{"start": float(i), "end": float(i + 1), "text": t} for i, t in enumerate(texts)]}


def test_quality_gate_flags_repetitive_transcript():
    stage = PostprocessStage()
    context = {
        "final_result": _raw_with_texts(["歡迎訂閱"] * 28 + ["真實內容", "另一句"]),
        "language": "zh",
        "duration": 30.0,
        "parent_job_id": "job-q",
    }
    result = stage.execute(context)
    warning = result["quality_warning"]
    assert "93%" in warning
    assert "30" in warning


def test_quality_gate_skips_normal_transcript():
    stage = PostprocessStage()
    texts = [f"第 {i} 句不同的內容" for i in range(30)]
    result = stage.execute({"final_result": _raw_with_texts(texts), "language": "zh", "duration": 30.0})
    assert "quality_warning" not in result
