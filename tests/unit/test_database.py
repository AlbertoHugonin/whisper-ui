from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.database import JobDatabase

if TYPE_CHECKING:
    from pathlib import Path


def test_insert_and_get(db: JobDatabase):
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.filename == "test.mp3"
    assert fetched.status == JobStatus.PENDING


def test_vad_boundary_settings_are_independent_job_snapshots(db: JobDatabase):
    job = Job(
        filename="meeting.wav",
        vad_enabled=False,
        vad_threshold=0.15,
        vad_min_speech_ms=80,
        vad_min_silence_ms=600,
        vad_speech_pad_ms=800,
        vad_samples_overlap=0.5,
    )
    db.insert_job(job)
    got = db.get_job(job.id)
    assert (
        got.vad_enabled,
        got.vad_threshold,
        got.vad_min_speech_ms,
        got.vad_min_silence_ms,
        got.vad_speech_pad_ms,
        got.vad_samples_overlap,
    ) == (False, 0.15, 80, 600, 800, 0.5)
    got.vad_enabled = True
    got.vad_speech_pad_ms = 400
    db.update_job(got)
    assert db.get_job(job.id).vad_enabled is True
    assert db.get_job(job.id).vad_speech_pad_ms == 400


def test_update_job_progress_only_touches_progress_fields(db: JobDatabase):
    """The field-level progress mirror must never overwrite status /
    result_path / error — that is what makes it safe to use during a Redis
    outage when generation gating cannot be verified. A COMPLETED job with a
    result must stay COMPLETED with its result even if a stale writer nudges
    its progress.
    """
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    job.status = JobStatus.COMPLETED
    job.progress = 1.0
    job.result_path = "/out/result.json"
    db.insert_job(job)

    db.update_job_progress(job.id, 0.5, "stale heartbeat")

    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.progress == 0.5
    assert fetched.progress_message == "stale heartbeat"
    # Untouched: the fields a stale full-column overwrite would have clobbered.
    assert fetched.status == JobStatus.COMPLETED
    assert fetched.result_path == "/out/result.json"


def test_try_claim_retry_transitions_failed_to_queued_once(db: JobDatabase):
    """The atomic retry claim succeeds exactly once: a second concurrent call
    (job already QUEUED) returns False so two pipelines are never built."""
    job = Job(filename="t.mp3", filepath="/tmp/t.mp3")
    job.status = JobStatus.FAILED
    job.error = "boom"
    job.result_path = "/old.json"
    db.insert_job(job)

    assert db.try_claim_retry(job.id, duration=12.0) is True
    fetched = db.get_job(job.id)
    assert fetched.status == JobStatus.QUEUED
    assert fetched.error is None
    assert fetched.result_path is None
    assert fetched.duration == 12.0

    # Second claim loses: status is no longer FAILED.
    assert db.try_claim_retry(job.id, duration=99.0) is False
    assert db.get_job(job.id).duration == 12.0


def test_delete_terminal_job_refuses_non_terminal(db: JobDatabase):
    """Conditional delete only removes terminal rows, so a job a retry just
    claimed (QUEUED) is never torn out from under the running attempt."""
    job = Job(filename="t.mp3", filepath="/tmp/t.mp3")
    job.status = JobStatus.QUEUED
    db.insert_job(job)

    assert db.delete_terminal_job(job.id) is False
    assert db.get_job(job.id) is not None

    completed = Job(filename="c.mp3", filepath="/tmp/c.mp3")
    completed.status = JobStatus.COMPLETED
    db.insert_job(completed)
    assert db.delete_terminal_job(completed.id) is True
    assert db.get_job(completed.id) is None


def test_legacy_db_without_owner_id_column_upgrades(tmp_dir: Path):
    """A pre-auth deployment's DB does not have owner_id. After init_db
    migrates the schema, the column exists, existing rows have NULL,
    and new inserts respect the owner_id value provided.
    """
    db_path = tmp_dir / "legacy_owner.db"
    legacy_schema = """
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        filepath TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        progress REAL NOT NULL DEFAULT 0.0,
        progress_message TEXT DEFAULT '',
        language TEXT NOT NULL DEFAULT 'zh',
        model_name TEXT NOT NULL DEFAULT 'large-v3',
        num_speakers INTEGER,
        enable_diarization INTEGER NOT NULL DEFAULT 1,
        convert_to_traditional INTEGER NOT NULL DEFAULT 1,
        llm_correction_enabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        error TEXT,
        result_path TEXT,
        duration REAL,
        batch_id TEXT,
        source_url TEXT
    );
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO jobs (id, filename, filepath, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy-2", "old.mp3", "/tmp/old.mp3", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    database = JobDatabase(db_path)
    try:
        # Legacy row still readable and owner_id is None.
        legacy = database.get_job("legacy-2")
        assert legacy is not None
        assert legacy.owner_id is None

        # New inserts with owner_id work.
        owned = Job(filename="new.mp3", filepath="/tmp/new.mp3", owner_id=7)
        database.insert_job(owned)
        fetched = database.get_job(owned.id)
        assert fetched is not None
        assert fetched.owner_id == 7
    finally:
        database.close()


def test_get_job_with_owner_id_returns_none_when_owner_differs(db: JobDatabase):
    job = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    db.insert_job(job)

    assert db.get_job(job.id, owner_id=2) is None


def test_count_jobs_combines_status_and_owner_filter(db: JobDatabase):
    completed = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    completed.status = JobStatus.COMPLETED
    db.insert_job(completed)
    db.insert_job(Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=1))  # PENDING

    assert db.count_jobs(status=JobStatus.COMPLETED.value, owner_id=1) == 1
    assert db.count_jobs(owner_id=1) == 2


def test_recover_stale_jobs_concurrent_workers_dont_double_recover(tmp_path: Path):
    """Two workers calling recover_stale_jobs at the same instant must not
    double-recover the same job. SQLite WAL serializes writers; once the
    first commit lands, the bumped updated_at takes the row out of the
    WHERE clause for every subsequent recovery call.
    """
    import threading

    db_path = tmp_path / "concurrent_recover.db"
    seed = JobDatabase(db_path)
    try:
        # Seed 5 stale PROCESSING jobs.
        ids = []
        for i in range(5):
            job = Job(filename=f"stale{i}.mp3", filepath=f"/tmp/stale{i}.mp3")
            job.status = JobStatus.PROCESSING
            seed.insert_job(job)
            ids.append(job.id)
        seed._conn.execute(
            "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id IN ({})".format(
                ",".join("?" * len(ids))
            ),
            ids,
        )
        seed._conn.commit()
    finally:
        seed.close()

    rowcounts: list[int] = []
    rowcounts_lock = threading.Lock()
    barrier = threading.Barrier(3)

    def worker():
        local = JobDatabase(db_path)
        try:
            barrier.wait()
            count = local.recover_stale_jobs(timeout_seconds=60, error_message="timeout")
            with rowcounts_lock:
                rowcounts.append(count)
        finally:
            local.close()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Sum of recovered rowcounts equals the number of distinct stale jobs;
    # no row gets recovered twice.
    assert sum(rowcounts) == 5

    # All 5 jobs are now FAILED — verify via a fresh connection.
    verify = JobDatabase(db_path)
    try:
        for job_id in ids:
            row = verify.get_job(job_id)
            assert row is not None
            assert row.status == JobStatus.FAILED
    finally:
        verify.close()


def test_list_terminal_job_ids_older_than_defaults_to_completed_only(db: JobDatabase):
    """The default retention sweep must skip FAILED jobs so the retry
    button still works after the upload window ages out."""
    now = datetime.now(UTC)
    old_iso = (now - timedelta(days=30)).isoformat()

    old_completed = Job(filename="oc.mp3", filepath="/tmp/oc.mp3", status=JobStatus.COMPLETED)
    old_failed = Job(filename="of.mp3", filepath="/tmp/of.mp3", status=JobStatus.FAILED)
    recent_completed = Job(filename="rc.mp3", filepath="/tmp/rc.mp3", status=JobStatus.COMPLETED)
    old_processing = Job(filename="op.mp3", filepath="/tmp/op.mp3", status=JobStatus.PROCESSING)
    for job in (old_completed, old_failed, recent_completed, old_processing):
        db.insert_job(job)

    db._conn.execute(
        "UPDATE jobs SET updated_at = ? WHERE id IN (?, ?, ?)",
        (old_iso, old_completed.id, old_failed.id, old_processing.id),
    )
    db._conn.commit()

    threshold = (now - timedelta(days=7)).isoformat()
    expired = set(db.list_terminal_job_ids_older_than(threshold))

    # FAILED is preserved (retry depends on its upload); only old COMPLETED qualifies.
    assert expired == {old_completed.id}


def test_legacy_db_without_source_job_id_column_upgrades(tmp_dir: Path):
    """A deployment predating versioning has no source_job_id column. After
    init_db migrates the schema, the column exists, legacy rows read as None,
    and new inserts persist the value.
    """
    db_path = tmp_dir / "legacy_source.db"
    legacy_schema = """
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        filepath TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        progress REAL NOT NULL DEFAULT 0.0,
        progress_message TEXT DEFAULT '',
        language TEXT NOT NULL DEFAULT 'zh',
        model_name TEXT NOT NULL DEFAULT 'large-v3',
        num_speakers INTEGER,
        enable_diarization INTEGER NOT NULL DEFAULT 1,
        convert_to_traditional INTEGER NOT NULL DEFAULT 1,
        llm_correction_enabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        error TEXT,
        result_path TEXT,
        duration REAL,
        batch_id TEXT,
        source_url TEXT,
        owner_id INTEGER
    );
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO jobs (id, filename, filepath, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy-3", "old.mp3", "/tmp/old.mp3", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    database = JobDatabase(db_path)
    try:
        legacy = database.get_job("legacy-3")
        assert legacy is not None
        assert legacy.source_job_id is None

        version = Job(filename="v.mp3", filepath="/tmp/v.mp3", source_job_id="legacy-3")
        database.insert_job(version)
        fetched = database.get_job(version.id)
        assert fetched is not None
        assert fetched.source_job_id == "legacy-3"
    finally:
        database.close()
