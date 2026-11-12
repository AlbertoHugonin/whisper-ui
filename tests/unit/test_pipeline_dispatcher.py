from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
from rq.job import Job as RQJob

from whisper_ui.core.constants import (
    WORKER_QUEUE_IO,
)
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.worker.context_store import PipelineContextStore
from whisper_ui.worker.pipeline_dispatcher import (
    _current_generation,
    _load_subjob_ids,
    enqueue_pipeline,
)
from whisper_ui.worker.stage_tasks import (
    run_assign_speakers,
    run_diarize,
    run_download,
    run_llm_correction,
    run_postprocess,
    run_preprocess,
    run_transcribe_align,
)

STAGE_TASK_NAMES = {
    run_download.__name__,
    run_preprocess.__name__,
    run_transcribe_align.__name__,
    run_diarize.__name__,
    run_assign_speakers.__name__,
    run_postprocess.__name__,
    run_llm_correction.__name__,
}


def _build_settings(*, ollama: str = "http://ollama.internal:11434") -> MagicMock:
    settings = MagicMock()
    settings.batch_size = 16
    settings.ollama_base_url = ollama
    # Mirror the real Settings.llm_correction_available property so is_llm_active
    # gates on a concrete bool rather than a truthy MagicMock attribute.
    settings.llm_correction_available = bool(ollama)
    settings.job_timeout_default = 3600
    settings.job_timeout_floor = 300
    settings.job_timeout_max = 14_400
    settings.job_timeout_audio_multiplier = 2.0
    settings.redis_processing_expiry = 7200
    return settings


def _build_filestore(tmp_path) -> MagicMock:
    fs = MagicMock()
    fs.prepare_upload_path.return_value = tmp_path / "subdir" / "file.mp3"
    return fs


def _load_subjobs(redis, parent_id, generation: int | None = None) -> list[RQJob]:
    """Fetch every sub-job enqueued under ``parent_id``. When ``generation``
    is None, auto-resolve to the current generation counter so test call
    sites that predate the per-generation set layout keep working.
    """
    if generation is None:
        generation = _current_generation(redis, parent_id)
        assert generation is not None, f"no generation counter found for {parent_id}"
    return [RQJob.fetch(sub_id, connection=redis) for sub_id in _load_subjob_ids(redis, parent_id, generation)]


def _stage_func(sub: RQJob) -> str:
    return sub.func_name.rsplit(".", 1)[-1]


def _by_stage(subs: list[RQJob]) -> dict[str, RQJob]:
    return {_stage_func(s): s for s in subs}


def test_file_upload_dag_includes_preprocess_through_postprocess(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-file",
        filename="meeting.mp3",
        filepath=str(tmp_path / "meeting.mp3"),
        status=JobStatus.QUEUED,
        duration=120.0,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))

    assert set(subs.keys()) == {
        "run_preprocess",
        "run_transcribe_align",
        "run_diarize",
        "run_assign_speakers",
        "run_postprocess",
    }


def test_ordered_audio_merge_dag_runs_merge_before_preprocess(tmp_path):
    from whisper_ui.pipeline.merge_audio import MANIFEST_NAME

    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    manifest = tmp_path / "uploads" / MANIFEST_NAME
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"version":1,"parts":[]}')
    filestore.get_upload_path.return_value = manifest
    job = Job(
        id="job-merged",
        filename="registrazione-unita.wav",
        filepath=str(manifest.parent / "registrazione-unita.wav"),
        status=JobStatus.QUEUED,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))
    assert "run_merge_audio" in subs
    assert subs["run_merge_audio"].origin == WORKER_QUEUE_IO
    assert set(subs["run_preprocess"].dependency_ids) == {subs["run_merge_audio"].id}
    assert set(subs["run_transcribe_align"].dependency_ids) == {subs["run_preprocess"].id}
    ctx = PipelineContextStore(redis, job.id).load()
    assert ctx["merge_manifest_path"] == str(manifest)
    assert ctx["input_path"] == job.filepath


def test_url_upload_dag_prepends_download(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-url",
        filename="video",
        source_url="https://www.youtube.com/watch?v=abc",
        status=JobStatus.QUEUED,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))

    assert "run_download" in subs
    assert subs["run_preprocess"]._dependency_ids == [subs["run_download"].id]


def test_llm_opt_in_completes_original_before_separate_correction(tmp_path):
    """LLM is queued by finalize_success after completion, never in the DAG."""
    redis = fakeredis.FakeRedis()
    settings = _build_settings()
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-llm",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        llm_correction_enabled=True,
        enable_diarization=True,
    )
    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))
    assert "run_llm_correction" not in subs
    assert subs["run_postprocess"]._success_callback_name is not None
    assert len(subs) == 5


def test_assign_speakers_fans_in_transcribe_and_diarize(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-fanin",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))
    deps = set(subs["run_assign_speakers"]._dependency_ids)

    assert deps == {subs["run_transcribe_align"].id, subs["run_diarize"].id}


def test_finalize_success_does_not_resurrect_failed_job(monkeypatch, tmp_path):
    """A late/zombie success callback for a job the stale reaper already FAILED
    must not flip it back to COMPLETED, even when its meta generation is absent
    (the ungated path the Python stale-callback guard cannot catch)."""
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    job = Job(
        id="job-already-failed",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.FAILED,
    )

    runtime = MagicMock()
    runtime.redis = redis
    runtime.db.get_job.return_value = job
    runtime.settings.redis_processing_expiry = 7200

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        runtime.reporter = MagicMock()
        yield runtime

    monkeypatch.setattr(pd, "build_worker_runtime", _fake_builder)

    fake_rq_job = MagicMock()
    fake_rq_job.meta = {"parent_job_id": job.id}  # no generation -> ungated path

    pd.finalize_success(fake_rq_job, redis, None)

    runtime.filestore.save_result.assert_not_called()
    runtime.db.update_job.assert_not_called()
    assert job.status == JobStatus.FAILED


def test_finalize_failure_marks_job_failed_and_cancels_siblings(monkeypatch, tmp_path):
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-fail",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.PROCESSING,
        enable_diarization=True,
    )
    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)

    # Pick one sub-job to "fail". The rest should get cancelled.
    subs = _load_subjobs(redis, job.id)
    failing = next(s for s in subs if _stage_func(s) == "run_transcribe_align")
    others = [s for s in subs if s.id != failing.id]

    runtime = MagicMock()
    runtime.redis = redis
    runtime.db.get_job.return_value = job
    runtime.settings.redis_processing_expiry = 7200

    from contextlib import contextmanager

    from whisper_ui.worker.progress import RedisProgressReporter

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        runtime.reporter = RedisProgressReporter(redis, job_id, processing_ttl=7200, generation=generation)
        yield runtime

    monkeypatch.setattr(pd, "build_worker_runtime", _fake_builder)

    # The sub-job meta already carries parent_job_id + generation from the
    # enqueue_pipeline call above; leave it alone so the callback sees the
    # same generation that the dispatcher wrote, otherwise the generation
    # check short-circuits the test against its own setup.
    pd.finalize_failure(failing, redis, RuntimeError, RuntimeError("kaboom"), None)

    from whisper_ui.ui import labels as ui_labels

    assert job.status == JobStatus.FAILED
    # User-facing error is the generic message; the raw "kaboom" detail only
    # goes to the server log, never job.error.
    assert job.error == ui_labels.JOBS_STAGE_FAILED_GENERIC
    assert "kaboom" not in (job.error or "")
    # Observable terminal state — finalize_failure now builds its own
    # generation-aware reporter instead of reusing runtime.reporter.
    stored = {
        k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
        for k, v in redis.hgetall(f"job:{job.id}").items()
    }
    assert stored["status"] == "failed"
    assert "kaboom" not in stored["error"]

    for other in others:
        refreshed = RQJob.fetch(other.id, connection=redis)
        assert refreshed.is_canceled, f"sub-job {_stage_func(other)} should be cancelled"
    # context store should be wiped
    assert PipelineContextStore(redis, job.id).load() == {}


def test_finalize_failure_bumps_generation_to_fence_zombie_writes(monkeypatch, tmp_path):
    """A sibling that survives the best-effort cancel (SimpleWorker cannot
    stop a running job) must have its gated writes rejected once the parent
    is FAILED, not resurrect the deleted context hash as an orphan key."""
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-fence",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.PROCESSING,
        enable_diarization=True,
    )
    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    generation_at_enqueue = int(redis.get(f"whisper:pipeline:{job.id}:generation"))

    subs = _load_subjobs(redis, job.id)
    failing = next(s for s in subs if _stage_func(s) == "run_transcribe_align")

    runtime = MagicMock()
    runtime.redis = redis
    runtime.db.get_job.return_value = job
    runtime.settings.redis_processing_expiry = 7200

    from contextlib import contextmanager

    from whisper_ui.worker.progress import RedisProgressReporter

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        runtime.reporter = RedisProgressReporter(redis, job_id, processing_ttl=7200, generation=generation)
        yield runtime

    monkeypatch.setattr(pd, "build_worker_runtime", _fake_builder)

    pd.finalize_failure(failing, redis, RuntimeError, RuntimeError("kaboom"), None)

    assert int(redis.get(f"whisper:pipeline:{job.id}:generation")) == generation_at_enqueue + 1
    # A zombie sibling still holding the failed attempt's generation must be
    # rejected by the gated write instead of recreating the context hash.
    ctx = PipelineContextStore(redis, job.id)
    assert ctx.update_if_generation_matches({"diarization_result": "zombie"}, generation_at_enqueue) is False
    assert ctx.load() == {}


def test_stale_finalize_failure_short_circuits_after_retry(monkeypatch, tmp_path):
    """Regression for PR #39 Round 2 R2-1. An attempt-1 sub-job that failed
    and fires its ``finalize_failure`` callback AFTER the user retried the
    job must NOT touch any attempt-2 state: the parent Job row stays at
    PROCESSING and none of attempt 2's live sub-jobs get cancelled.

    Before the fix, ``_clear_subjob_set`` at attempt 2 enqueue time
    replaced the parent-scoped tracking set with attempt 2's ids, so the
    stale attempt-1 callback would read attempt 2's sub-jobs out of it
    and cancel all of them while also flipping the Job row to FAILED.
    """
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-stale-fail",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        enable_diarization=True,
    )

    # Attempt 1 — bumps generation to 1, creates its own subjobs set.
    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    attempt1_subs = _load_subjobs(redis, job.id, generation=1)
    attempt1_failing = next(s for s in attempt1_subs if _stage_func(s) == "run_transcribe_align")
    assert attempt1_failing.meta.get("generation") == 1

    # Attempt 2 — simulates the user retrying. Dispatcher bumps generation
    # to 2 and allocates a fresh subjobs set under gen=2. Attempt 2's Job
    # row transitions back to PROCESSING as the first stage would have done.
    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    job.status = JobStatus.PROCESSING
    attempt2_subs = _load_subjobs(redis, job.id, generation=2)
    attempt2_sub_ids = {s.id for s in attempt2_subs}

    runtime = MagicMock()
    runtime.redis = redis
    runtime.db.get_job.return_value = job
    runtime.reporter = MagicMock()

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        yield runtime

    monkeypatch.setattr(pd, "build_worker_runtime", _fake_builder)

    # The stale attempt-1 callback fires AFTER attempt 2 is already running.
    pd.finalize_failure(attempt1_failing, redis, RuntimeError, RuntimeError("attempt1-boom"), None)

    # Parent Job row must still be in attempt 2's state.
    assert job.status == JobStatus.PROCESSING, (
        f"stale attempt-1 finalize_failure must not flip the job to FAILED; got {job.status}"
    )
    runtime.reporter.fail.assert_not_called()
    runtime.db.update_job.assert_not_called()

    # None of attempt 2's sub-jobs may be cancelled by the stale callback.
    for sub_id in attempt2_sub_ids:
        refreshed = RQJob.fetch(sub_id, connection=redis)
        assert not refreshed.is_canceled, f"attempt-2 sub-job {sub_id} was cancelled by a stale attempt-1 callback"


def _build_completion_runtime(redis, job, tmp_path, *, save_side_effect=None):
    """Build a MagicMock WorkerRuntime + a real generation-aware reporter for
    the persist-failure tests. ``save_side_effect`` (when set) makes
    ``filestore.save_result`` raise that exception."""
    from whisper_ui.worker.progress import RedisProgressReporter

    runtime = MagicMock()
    runtime.redis = redis
    runtime.db.get_job.return_value = job
    if save_side_effect is not None:
        runtime.filestore.save_result.side_effect = save_side_effect
    else:
        runtime.filestore.save_result.return_value = tmp_path / "result.json"
    runtime.settings.redis_processing_expiry = 7200

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        runtime.reporter = RedisProgressReporter(redis, job_id, processing_ttl=7200, generation=generation)
        yield runtime

    return runtime, _fake_builder


def test_finalize_success_save_failure_marks_failed(monkeypatch, tmp_path):
    """The same guard protects the normal completion path: a save_result
    failure in finalize_success marks FAILED instead of leaving PROCESSING
    (pre-existing latent bug, now fixed via the shared helper)."""
    from whisper_ui.core.messages import RESULT_PERSIST_FAILED
    from whisper_ui.core.models import TranscriptResult
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    job = Job(id="job-succ-savefail", filename="m.mp3", filepath=str(tmp_path / "m.mp3"), status=JobStatus.PROCESSING)
    PipelineContextStore(redis, job.id).initialize(
        {"transcript_result": TranscriptResult(language="zh", duration=10.0)}
    )

    _, builder = _build_completion_runtime(redis, job, tmp_path, save_side_effect=OSError("disk full"))
    monkeypatch.setattr(pd, "build_worker_runtime", builder)

    rq_job = MagicMock()
    rq_job.meta = {"parent_job_id": job.id}
    pd.finalize_success(rq_job, redis, None)

    assert job.status == JobStatus.FAILED
    assert RESULT_PERSIST_FAILED in (job.error or "")


def test_recover_stale_pipeline_jobs_propagates_redis_error_without_reaping(tmp_path, monkeypatch):
    """PR #95 review #1: a transient RedisError during the liveness probe must
    PROPAGATE (so the stale checker logs it and skips the round), not be
    swallowed as 'dead' and used to reap a live job."""
    from redis.exceptions import RedisError

    from whisper_ui.storage.database import JobDatabase
    from whisper_ui.ui.labels import JOBS_STALE_ERROR
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    db = JobDatabase(tmp_path / "stale.db")
    try:
        job = Job(id="live-candidate", filename="m.mp3", filepath=str(tmp_path / "m.mp3"), status=JobStatus.PROCESSING)
        db.insert_job(job)
        enqueue_pipeline(job, redis=redis, settings=_build_settings(ollama=""), filestore=_build_filestore(tmp_path))
        db._conn.execute(
            "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE status = ?",
            (JobStatus.PROCESSING.value,),
        )
        db._conn.commit()

        def _raise_redis(*args, **kwargs):
            raise RedisError("connection reset")

        monkeypatch.setattr("rq.job.Job.fetch", _raise_redis)

        with pytest.raises(RedisError):
            pd.recover_stale_pipeline_jobs(db, redis, timeout_seconds=60, error_message=JOBS_STALE_ERROR)

        # The candidate must be left untouched for a later round, not reaped.
        assert db.get_job(job.id).status == JobStatus.PROCESSING
    finally:
        db.close()
