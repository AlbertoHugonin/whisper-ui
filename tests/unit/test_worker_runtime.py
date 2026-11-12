from __future__ import annotations

from unittest.mock import MagicMock, patch

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.worker.progress import RedisProgressReporter
from whisper_ui.worker.runtime import (
    WorkerRuntime,
    build_worker_runtime,
)


def _fake_settings() -> MagicMock:
    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    settings.database_path = ":memory:"
    settings.upload_dir = "/tmp/whisper-test-upload"
    settings.output_dir = "/tmp/whisper-test-output"
    settings.redis_processing_expiry = 1234
    return settings


def test_build_worker_runtime_wires_shared_resources_and_closes_db():
    fake_settings = _fake_settings()
    fake_db = MagicMock()
    fake_filestore = MagicMock()
    fake_redis = MagicMock()

    with (
        patch("whisper_ui.worker.runtime.get_settings", return_value=fake_settings),
        patch("whisper_ui.worker.runtime.create_redis", return_value=fake_redis) as create_redis_mock,
        patch("whisper_ui.worker.runtime.JobDatabase", return_value=fake_db) as db_ctor,
        patch("whisper_ui.worker.runtime.FileStore", return_value=fake_filestore) as fs_ctor,
    ):
        with build_worker_runtime("job-xyz") as runtime:
            assert isinstance(runtime, WorkerRuntime)
            assert runtime.settings is fake_settings
            assert runtime.redis is fake_redis
            assert runtime.db is fake_db
            assert runtime.filestore is fake_filestore
            assert isinstance(runtime.reporter, RedisProgressReporter)

        create_redis_mock.assert_called_once_with(fake_settings)
        db_ctor.assert_called_once_with(fake_settings.database_path)
        fs_ctor.assert_called_once_with(fake_settings.upload_dir, fake_settings.output_dir, fake_settings.archive_dir)
        fake_db.close.assert_called_once()


def test_build_worker_runtime_closes_db_even_on_error():
    fake_settings = _fake_settings()
    fake_db = MagicMock()

    with (
        patch("whisper_ui.worker.runtime.get_settings", return_value=fake_settings),
        patch("whisper_ui.worker.runtime.create_redis", return_value=MagicMock()),
        patch("whisper_ui.worker.runtime.JobDatabase", return_value=fake_db),
        patch("whisper_ui.worker.runtime.FileStore", return_value=MagicMock()),
    ):
        try:
            with build_worker_runtime("job-err"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        fake_db.close.assert_called_once()


def _make_job() -> Job:
    return Job(
        id="throttle-job",
        filename="example.mp3",
        filepath="/tmp/example.mp3",
        status=JobStatus.PROCESSING,
        progress=0.0,
        progress_message="",
    )
