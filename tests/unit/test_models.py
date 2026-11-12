from __future__ import annotations

from whisper_ui.core.models import Segment, TranscriptResult


def test_transcript_result_roundtrip():
    original = TranscriptResult(
        segments=[Segment(start=1.0, end=2.0, text="test", speaker="S1")],
        language="en",
        duration=2.0,
    )
    restored = TranscriptResult.from_dict(original.to_dict())
    assert restored.language == original.language
    assert restored.duration == original.duration
    assert len(restored.segments) == len(original.segments)
    assert restored.segments[0].text == original.segments[0].text
