"""Unit tests for JSON export."""

from __future__ import annotations

import json

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.export.json_export import JsonExporter


class TestJsonExporter:
    def setup_method(self):
        self.exporter = JsonExporter()

    def test_export_structure(self):
        result = TranscriptResult(
            segments=[
                Segment(start=0.0, end=1.5, text="Hello", speaker="S1"),
                Segment(start=1.5, end=3.0, text="World"),
            ],
            language="en",
            duration=3.0,
        )
        data = json.loads(self.exporter.export(result))

        assert data["language"] == "en"
        assert data["duration"] == 3.0
        assert len(data["segments"]) == 2
        assert data["segments"][0]["start"] == 0.0
        assert data["segments"][0]["end"] == 1.5
        assert data["segments"][0]["text"] == "Hello"
        assert data["segments"][0]["speaker"] == "S1"
        assert data["segments"][1]["speaker"] is None
