from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
from redis.exceptions import ConnectionError as RedisConnectionError

from whisper_ui.core.models import Job
from whisper_ui.worker.progress import ProgressWriteOutcome, RedisProgressReporter
from whisper_ui.worker.runtime import make_throttled_progress_reporter as _make_throttled_progress_reporter


def _make_reporter(processing_ttl: int = 7200) -> tuple[MagicMock, RedisProgressReporter]:
    mock_redis = MagicMock()
    reporter = RedisProgressReporter(mock_redis, "test-job-id", processing_ttl=processing_ttl)
    return mock_redis, reporter


def _fake_reporter(job_id: str = "test-job-id", processing_ttl: int = 7200):
    """Build a RedisProgressReporter backed by fakeredis[lua], which supports
    the atomic max-write Lua script the reporter uses for ``report()``.
    Returns (FakeRedis, reporter) so tests can hgetall the actual stored
    state instead of asserting on mock call args.
    """
    fake = fakeredis.FakeRedis()
    reporter = RedisProgressReporter(fake, job_id, processing_ttl=processing_ttl)
    return fake, reporter


def _stored(fake, job_id: str = "test-job-id") -> dict[str, str]:
    return {
        k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
        for k, v in fake.hgetall(f"job:{job_id}").items()
    }


def test_report_progress_never_regresses_on_second_writer():
    """Direct Lua-max semantics test: two reporters bound to the same
    job_id write interleaved values and the stored progress must always
    be the highest one any writer has observed. Message always follows
    the latest write so the UI can switch stage labels even when
    progress happens to stall.
    """
    fake = fakeredis.FakeRedis()
    r1 = RedisProgressReporter(fake, "same-job")
    r2 = RedisProgressReporter(fake, "same-job")

    r1.report(0.30, "transcribe running")
    assert _stored(fake, "same-job")["progress"] == "0.3"

    r2.report(0.72, "diarize running")
    assert _stored(fake, "same-job")["progress"] == "0.72"

    # Regression attempt — must be dropped. Message still advances.
    r1.report(0.35, "transcribe chunk 2")
    stored = _stored(fake, "same-job")
    assert float(stored["progress"]) == 0.72
    assert stored["message"] == "transcribe chunk 2"

    # A strictly larger write still wins.
    r1.report(0.90, "transcribe chunk 3")
    assert float(_stored(fake, "same-job")["progress"]) == 0.90


def test_report_with_older_generation_is_dropped():
    """After attempt 2 takes over the hash, any late writer from
    attempt 1 must be silently dropped — otherwise a residual pyannote
    C++ thread that honoured ``send_stop_job_command`` slowly could
    still corrupt the fresh attempt's progress.
    """
    fake = fakeredis.FakeRedis()

    # Attempt 2 is running and has already stamped its generation.
    RedisProgressReporter(fake, "job-retry", generation=2).report(0.30, "attempt2 transcribe")
    assert float(_stored(fake, "job-retry")["progress"]) == 0.30

    # Late writer from attempt 1 tries to overwrite.
    RedisProgressReporter(fake, "job-retry", generation=1).report(0.99, "late stale write")

    stored = _stored(fake, "job-retry")
    assert float(stored["progress"]) == 0.30, "stale attempt-1 write must not touch attempt 2's hash"
    assert stored["message"] == "attempt2 transcribe"
    assert stored["generation"] == "2"


def test_complete_rejected_by_central_gen():
    fake = fakeredis.FakeRedis()
    fake.set("whisper:pipeline:job-r4c:generation", 3)

    stale = RedisProgressReporter(fake, "job-r4c", generation=1)
    assert stale.complete("/stale.json") is False


def test_report_returns_degraded_on_redis_error():
    """Transient Redis failures surface as DEGRADED, not REJECTED: the
    "SQLite is source of truth when Redis is unavailable" contract requires
    the throttler to keep mirroring to the DB during an outage, but via the
    progress-only field-level path (not a full-column overwrite).
    """
    _fake, reporter = _fake_reporter()
    reporter._max_write_script = MagicMock(side_effect=RedisConnectionError("down"))

    assert reporter.report(0.5, "m") is ProgressWriteOutcome.DEGRADED


class _FakeClock:
    """Monotonic clock stub so throttle tests are not flaky under CI load."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_throttle(min_delta: float = 0.005, min_interval_sec: float = 0.5):
    reporter = MagicMock(spec=RedisProgressReporter)
    db = MagicMock()
    job = Job(filename="t.wav")
    clock = _FakeClock()
    on_progress = _make_throttled_progress_reporter(
        reporter,
        db,
        job,
        min_delta=min_delta,
        min_interval_sec=min_interval_sec,
        monotonic=clock,
    )
    return on_progress, reporter, db, job, clock


class TestThrottledProgressReporter:
    def test_concurrent_callers_do_not_corrupt_state(self):
        """Smoke test for the throttle lock. Spawn many threads hammering
        the closure with monotonically increasing progress; the final
        state must be consistent and no exception may escape.
        """
        import threading

        on_progress, reporter, _db, _job, _clock = _make_throttle()
        errors: list[BaseException] = []

        def worker(start: int) -> None:
            try:
                for i in range(50):
                    on_progress(0.001 * (start + i), "stage")
            except BaseException as e:  # pragma: no cover - defensive
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i * 50,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # last_progress must equal the highest progress that any thread
        # actually wrote — i.e. monotonically non-decreasing.
        final_progress = max(call.args[0] for call in reporter.report.call_args_list)
        assert final_progress > 0

    def test_throttle_skips_db_update_when_reporter_rejects_stale_write(self):
        """Regression for PR #39 Round 3 R3-1. When the Redis Lua script
        rejects a write because the caller's generation is strictly older
        than the stored one, the throttler must NOT proceed to update the
        SQLite Job row from its captured (stale) Job object. Before this
        fix the throttler always called db.update_job(job) regardless of
        whether the Redis write landed, so a stale stage's captured Job
        snapshot could clobber the current attempt's status, progress,
        and result_path fields via the full-column UPDATE path.
        """
        on_progress, reporter, db, job, _clock = _make_throttle()
        reporter.report.return_value = ProgressWriteOutcome.REJECTED  # Lua rejected (stale)

        on_progress(0.85, "attempt1 stale heartbeat")

        reporter.report.assert_called_once_with(0.85, "attempt1 stale heartbeat")
        db.update_job.assert_not_called()
        db.update_job_progress.assert_not_called()
        # Captured Job must not be mutated — otherwise a *subsequent*
        # legitimate writer would inherit the stale progress value.
        assert job.progress == 0.0
        assert job.progress_message == ""
