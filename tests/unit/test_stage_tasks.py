from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from whisper_ui.core.exceptions import PipelineError
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.worker.context_store import PipelineContextStore
from whisper_ui.worker.runtime import WorkerRuntime
from whisper_ui.worker.stage_tasks import (
    run_diarize,
    run_postprocess,
    run_preprocess,
)


def _make_runtime(redis_client) -> WorkerRuntime:
    settings = MagicMock()
    settings.ollama_base_url = "http://ollama.internal:11434"
    # Mirror the real Settings.llm_correction_available property (derived from
    # ollama_base_url) so is_llm_active gates on a bool, not a MagicMock.
    settings.llm_correction_available = True
    settings.device = "cpu"
    settings.compute_type = "int8"
    settings.youtube_max_duration = 3600
    settings.diarize_heartbeat_interval = 30
    settings.hf_token = "fake-token-not-real"
    return WorkerRuntime(
        settings=settings,
        redis=redis_client,
        reporter=MagicMock(),
        db=MagicMock(),
        filestore=MagicMock(),
    )


class _RecordingStage:
    name = "fake"

    def __init__(self, new_keys: dict[str, Any]):
        self._new_keys = new_keys
        self.cleanup_called = False

    def execute(self, context: dict, on_progress=None) -> dict:
        if on_progress:
            on_progress(1.0, "done")
        result = dict(context)
        result.update(self._new_keys)
        return result

    def cleanup(self) -> None:
        self.cleanup_called = True


class _ExplodingStage:
    name = "boom"

    def execute(self, context, on_progress=None):
        raise RuntimeError("kaboom")

    def cleanup(self) -> None:
        pass


class _TimingOutStage:
    name = "slow"

    def execute(self, context, on_progress=None):
        from rq.timeouts import JobTimeoutException

        raise JobTimeoutException("Task exceeded maximum timeout value (10 seconds)")

    def cleanup(self) -> None:
        self.cleanup_called = True


class _PipelineErrorStage:
    name = "domain"

    def execute(self, context, on_progress=None):
        raise PipelineError("already a domain error")

    def cleanup(self) -> None:
        pass


def _install_fake_runtime(monkeypatch, fake_redis, job: Job) -> WorkerRuntime:
    runtime = _make_runtime(fake_redis)
    runtime.db.get_job.return_value = job

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        yield runtime

    monkeypatch.setattr("whisper_ui.worker.stage_tasks.build_worker_runtime", _fake_builder)
    return runtime


def test_run_postprocess_persists_transcript_result(monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-p", status=JobStatus.PROCESSING, convert_to_traditional=False)
    _install_fake_runtime(monkeypatch, fake_redis, job)

    PipelineContextStore(fake_redis, "job-p").initialize({"final_result": {"segments": []}})

    fake_stage = _RecordingStage({"transcript_result": {"segments": [], "language": "zh"}})
    with patch("whisper_ui.worker.stage_tasks.PostprocessStage", return_value=fake_stage):
        run_postprocess("job-p")

    stored = PipelineContextStore(fake_redis, "job-p").load()
    assert stored["transcript_result"] == {"segments": [], "language": "zh"}
    # The stage did not flag quality; no stale key may appear in the store.
    assert "quality_warning" not in stored


def test_run_postprocess_persists_quality_warning_when_flagged(monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-qw", status=JobStatus.PROCESSING, convert_to_traditional=False)
    _install_fake_runtime(monkeypatch, fake_redis, job)

    PipelineContextStore(fake_redis, "job-qw").initialize({"final_result": {"segments": []}})

    fake_stage = _RecordingStage(
        {"transcript_result": {"segments": [], "language": "zh"}, "quality_warning": "轉錄結果異常"}
    )
    with patch("whisper_ui.worker.stage_tasks.PostprocessStage", return_value=fake_stage):
        run_postprocess("job-qw")

    stored = PipelineContextStore(fake_redis, "job-qw").load()
    assert stored["quality_warning"] == "轉錄結果異常"


def test_stage_task_transitions_queued_parent_job_to_processing(monkeypatch):
    """Regression for R1: in the DAG path the parent job must flip from
    QUEUED to PROCESSING as soon as any stage actually starts running.
    Without this the stale-job reaper (which only scans PROCESSING) can
    never recover a crashed DAG, and the UI keeps showing the job as
    queued forever.
    """
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-queued", status=JobStatus.QUEUED, filepath="/tmp/a.mp3")
    runtime = _install_fake_runtime(monkeypatch, fake_redis, job)

    fake_stage = _RecordingStage({"audio_path": "/tmp/16k.wav", "duration": 1.0})
    with patch("whisper_ui.worker.stage_tasks.PreprocessStage", return_value=fake_stage):
        run_preprocess("job-queued")

    assert job.status == JobStatus.PROCESSING
    runtime.db.update_job.assert_any_call(job)


def test_run_transcribe_align_also_transitions_queued_to_processing(monkeypatch):
    """run_transcribe_align has its own driver (not _run_single_stage) so
    it needs the same guarded transition. Guards against the common
    mistake of adding the fix in one place and forgetting the other.
    """
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-ta", status=JobStatus.QUEUED, filepath="/tmp/a.mp3")
    _install_fake_runtime(monkeypatch, fake_redis, job)
    PipelineContextStore(fake_redis, "job-ta").initialize({"audio_path": "/tmp/16k.wav"})

    fake_transcribe = _RecordingStage({"transcription_result": {"segments": []}})
    fake_align = _RecordingStage({"aligned_result": {"segments": []}})

    from whisper_ui.worker.stage_tasks import run_transcribe_align

    with (
        patch("whisper_ui.worker.stage_tasks.TranscribeStage", return_value=fake_transcribe),
        patch("whisper_ui.worker.stage_tasks.AlignStage", return_value=fake_align),
    ):
        run_transcribe_align("job-ta")

    assert job.status == JobStatus.PROCESSING


def test_run_diarize_raises_pipeline_error_when_job_missing(monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    runtime = _make_runtime(fake_redis)
    runtime.db.get_job.return_value = None

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        yield runtime

    monkeypatch.setattr("whisper_ui.worker.stage_tasks.build_worker_runtime", _fake_builder)

    with pytest.raises(PipelineError):
        run_diarize("missing-job")
