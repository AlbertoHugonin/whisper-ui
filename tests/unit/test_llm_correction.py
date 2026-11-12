from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from whisper_ui.core.messages import (
    LLM_CORRECTION_DEGRADED,
    LLM_CORRECTION_SKIPPED,
)
from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.pipeline.llm_correction import LLMCorrectionStage


@dataclass
class RecordedCall:
    model: str
    system: str
    user: str
    temperature: float
    keep_alive: str
    think: bool


@dataclass
class FakeOllamaClient:
    """Protocol-shaped fake that records calls and returns scripted responses."""

    responses: list[Any] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)
    closed: bool = False

    def chat_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        keep_alive: str,
        think: bool,
    ) -> str:
        self.calls.append(
            RecordedCall(
                model=model, system=system, user=user, temperature=temperature, keep_alive=keep_alive, think=think
            )
        )
        if not self.responses:
            return '{"segments": []}'
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


def _make_transcript(texts: list[str], language: str = "zh") -> TranscriptResult:
    return TranscriptResult(
        segments=[
            Segment(start=float(i), end=float(i + 1), text=text, speaker=f"SPK_{i % 2}") for i, text in enumerate(texts)
        ],
        language=language,
        duration=float(len(texts)),
    )


def _make_stage(**overrides: Any) -> tuple[LLMCorrectionStage, FakeOllamaClient]:
    client = FakeOllamaClient()
    defaults: dict[str, Any] = {
        "base_url": "http://ollama:11434",
        "model": "gemma4:e2b",
        "keep_alive": "30m",
        "chunk_size": 5,
        "chunk_context": 2,
        "temperature": 0.1,
        "request_timeout": 60.0,
        "client": client,
    }
    defaults.update(overrides)
    return LLMCorrectionStage(**defaults), client


def _capture_progress() -> tuple[list[tuple[float, str]], Any]:
    calls: list[tuple[float, str]] = []

    def on_progress(progress: float, message: str) -> None:
        calls.append((progress, message))

    return calls, on_progress


def _valid_response_for(indices: list[int], corrected: list[str]) -> str:
    return json.dumps({"segments": [{"idx": idx, "text": text} for idx, text in zip(indices, corrected, strict=True)]})


def test_stage_uses_detected_transcript_language_not_configured_language():
    """language=auto or a mismatched job setting must defer to Whisper detection."""
    stage, client = _make_stage()
    transcript = _make_transcript(["hello world"], language="en")
    client.responses = [_valid_response_for([0], ["Hello world."])]

    stage.execute({"transcript_result": transcript, "language": "zh"})

    assert len(client.calls) == 1
    assert "language code: en" in client.calls[0].system
    assert "Transcript language code: en" in client.calls[0].user


def _zh_context(transcript: TranscriptResult, **extra: Any) -> dict[str, Any]:
    return {"transcript_result": transcript, "language": "zh", **extra}


def test_applies_corrected_text_and_preserves_timings():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["原文一", "原文二"])
    original_starts = [s.start for s in transcript.segments]
    original_ends = [s.end for s in transcript.segments]
    original_speakers = [s.speaker for s in transcript.segments]

    client.responses = [_valid_response_for([0, 1], ["校正一", "校正二"])]
    stage.execute({"transcript_result": transcript, "language": "zh"})

    assert [s.text for s in transcript.segments] == ["校正一", "校正二"]
    assert [s.start for s in transcript.segments] == original_starts
    assert [s.end for s in transcript.segments] == original_ends
    assert [s.speaker for s in transcript.segments] == original_speakers


def test_fallback_on_malformed_json():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = ["not json at all"]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙"]
    assert progress[-1][0] == 1.0
    assert progress[-1][1] == LLM_CORRECTION_SKIPPED  # all chunks failed


def test_rq_timeout_propagates_from_llm_correction():
    """RQ death-penalty exceptions must NOT be swallowed by the per-chunk
    fallback. The broad ``except Exception`` catches everything up to but
    not including ``BaseTimeoutException``, which has to propagate so the
    worker task layer can classify the job as timed out. This guards the
    same class of bug that PR #34 Finding 1 had to fix for other stages.
    """
    from rq.timeouts import JobTimeoutException

    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = [JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")]

    with pytest.raises(JobTimeoutException):
        stage.execute({"transcript_result": transcript, "language": "zh"})


def test_fallback_on_http_error_never_raises():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = [httpx.ConnectError("boom")]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙"]
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_partial_5xx_keeps_succeeded_chunks_and_reports_degraded():
    """When some chunks 500 and others succeed, succeeded chunks stay
    corrected and the final progress message reflects degraded mode.
    """
    stage, client = _make_stage(chunk_size=2, chunk_context=0)
    transcript = _make_transcript(["甲", "乙", "丙", "丁"])
    fake_response = httpx.Response(500, request=httpx.Request("POST", "http://ollama/api/chat"))
    client.responses = [
        httpx.HTTPStatusError("upstream down", request=fake_response.request, response=fake_response),
        _valid_response_for([2, 3], ["丙校", "丁校"]),
    ]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙", "丙校", "丁校"]
    assert progress[-1][1].startswith(LLM_CORRECTION_DEGRADED)
