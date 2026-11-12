from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import authed_test_client, flash_messages
from tests.helpers.store import list_jobs, save_upload
from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.app import create_app


@pytest.fixture
def app(settings, db, filestore, test_user):
    application = create_app()
    application.state.settings = settings
    application.state.db = db
    application.state.filestore = filestore
    application.state.redis = MagicMock()
    # Mock redis.hgetall to return empty dict (no progress data)
    application.state.redis.hgetall.return_value = {}
    # The middleware skips bootstrap mode once an admin exists. Tests
    # create users explicitly via fixtures, so flipping this latch up
    # front avoids /register?bootstrap=1 redirects before the first request.
    application.state.bootstrap_done = True

    # Convenience: existing tests build jobs via db.insert_job(Job(...))
    # without an owner_id, which would make them invisible to the authed
    # `client` (alice) after the owner-gate landed. Patch the insert so a
    # missing owner_id silently defaults to alice — explicit ownership in
    # new tests still wins. test_database.py uses the bare `db` fixture
    # from conftest.py and is unaffected.
    original_insert = db.insert_job

    def insert_with_default_owner(job):
        if job.owner_id is None:
            job.owner_id = test_user.id
        original_insert(job)

    db.insert_job = insert_with_default_owner
    return application


@pytest.fixture
def client(app, test_user):
    return authed_test_client(app, test_user)


@pytest.fixture
def admin_client(app, test_admin):
    return authed_test_client(app, test_admin)


def _create_completed_job(db, filestore) -> Job:
    result = TranscriptResult(
        segments=[],
        language="zh",
        duration=60.0,
    )
    job = Job(filename="test.mp3", status=JobStatus.COMPLETED, language="zh")
    result_path = filestore.save_result(job.id, result)
    job.result_path = str(result_path)
    db.insert_job(job)
    return job


def _create_failed_job(db, *, source_url: str | None = None) -> Job:
    job = Job(filename="fail.mp3", status=JobStatus.FAILED, error="test error", source_url=source_url)
    db.insert_job(job)
    return job


def _completed_upload_job_with_audio(db, filestore, *, filename: str = "meeting.mp3") -> Job:
    """A COMPLETED upload job with both its source audio and result on disk."""
    result = TranscriptResult(segments=[], language="zh", duration=60.0)
    job = Job(filename=filename, status=JobStatus.COMPLETED, language="zh", model_name="large-v3")
    save_upload(filestore, job.id, filename, b"original audio bytes")
    job.filepath = str(filestore.get_upload_path(job.id, filename))
    job.result_path = str(filestore.save_result(job.id, result))
    db.insert_job(job)
    return job


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestSecurityHeaders:
    def test_response_carries_csp_and_base_headers(self, client):
        resp = client.get("/jobs")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'self'" in csp
        assert "https://cdn.jsdelivr.net" in csp
        assert "'nonce-" in csp

    def test_inline_scripts_carry_the_response_nonce(self, client):
        resp = client.get("/jobs")
        csp = resp.headers["Content-Security-Policy"]
        nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
        assert nonce  # non-empty per-request nonce
        # The page's inline <script> blocks must carry the same nonce or the
        # browser would refuse to run them under this CSP.
        assert f'<script nonce="{nonce}">' in resp.text


class TestMetricsEndpoint:
    def test_metrics_requires_bearer_token_when_configured(self, client, app):
        app.state.settings = app.state.settings.model_copy(update={"metrics_token": "scrape-secret"})

        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
        assert ok.status_code == 200
        assert "whisper_jobs_total" in ok.text


class TestDashboardRoutes:
    def test_root_serves_dashboard(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'class="atelier-dashboard-heading"' in resp.text

    def test_dashboard_profile_escapes_username_as_text(self, client, db, test_user):
        db.conn.execute("UPDATE users SET username=? WHERE id=?", ("alice <qa>", test_user.id))
        db.conn.commit()
        resp = client.get("/")
        assert resp.status_code == 200
        assert "alice &lt;qa&gt;" in resp.text
        assert "alice <qa>" not in resp.text
        assert 'const name = "' not in resp.text


class TestUploadRoutes:
    def test_upload_page_selects_tab_from_mode_query(self, client):
        """Dashboard quick-action /upload?mode=folder|url must land the user
        on the matching tab (plan §B Finding F2)."""
        for mode in ("folder", "url"):
            resp = client.get(f"/upload?mode={mode}")
            assert resp.status_code == 200
            assert f"tab: '{mode}'" in resp.text


class TestJobsRoutes:
    def test_jobs_list_filter(self, client, db, filestore):
        _create_completed_job(db, filestore)
        _create_failed_job(db)
        resp = client.get("/jobs/list?status=completed")
        assert resp.status_code == 200
        assert "test.mp3" in resp.text
        assert "fail.mp3" not in resp.text

    def test_delete_job(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.delete(f"/jobs/{job.id}")
        assert resp.status_code == 204
        assert db.get_job(job.id) is None

    def test_delete_active_job_returns_409(self, client, db):
        job = Job(filename="active.mp3", status=JobStatus.PROCESSING, language="zh")
        db.insert_job(job)
        resp = client.delete(f"/jobs/{job.id}")
        assert resp.status_code == 409
        assert db.get_job(job.id) is not None

    def test_retry_is_idempotent_second_call_does_not_reenqueue(self, client, db):
        """A repeated retry must not build a second pipeline: the first call
        claims FAILED -> QUEUED, the second finds the job no longer FAILED (404)
        and enqueues nothing. Enqueue happens exactly once."""
        job = _create_failed_job(db)
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=None),
        ):
            first = client.post(f"/jobs/{job.id}/retry")
            second = client.post(f"/jobs/{job.id}/retry")

        assert first.status_code == 204
        assert second.status_code == 404
        mock_enqueue.assert_called_once()

    def test_retry_enqueue_failure_uses_generic_error(self, client, db):
        job = _create_failed_job(db)
        with (
            patch(
                "whisper_ui.web.routes.jobs.enqueue_pipeline",
                side_effect=Exception("Redis internal: secret/key/path leak"),
            ),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=60.0),
        ):
            client.post(f"/jobs/{job.id}/retry")
        refreshed = db.get_job(job.id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.FAILED
        assert "Redis internal" not in (refreshed.error or "")
        assert "secret" not in (refreshed.error or "")

    def test_re_transcribe_creates_new_version_and_preserves_original(self, client, db, filestore, app):
        # hf_token present so the diarization flag passes through un-clamped.
        app.state.settings = app.state.settings.model_copy(update={"hf_token": "hf-test-not-real"})
        src = _completed_upload_job_with_audio(db, filestore)
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=42.0),
        ):
            resp = client.post(
                f"/jobs/{src.id}/re-transcribe",
                data={
                    "language": "en",
                    "model_name": "medium",
                    "enable_diarization": "true",
                    "vad_threshold": "0.55",
                    "whisper_beam_size": "8",
                    "vad_enabled": "false",
                    "vad_min_speech_ms": "50",
                    "vad_min_silence_ms": "500",
                    "vad_speech_pad_ms": "800",
                    "vad_samples_overlap": "0.50",
                },
            )

        assert resp.status_code == 204
        # Re-transcribe goes to the new jobs list, unlike inline delete/retry.
        assert resp.headers.get("hx-redirect") == "/jobs"
        new_job = mock_enqueue.call_args[0][0]
        # A distinct job carrying the new parameters, linked to the source.
        assert new_job.id != src.id
        assert new_job.language == "en"
        assert new_job.model_name == "medium"
        assert new_job.vad_threshold == 0.55
        assert new_job.vad_enabled is False
        assert new_job.vad_min_speech_ms == 50
        assert new_job.vad_min_silence_ms == 500
        assert new_job.vad_speech_pad_ms == 800
        assert new_job.vad_samples_overlap == 0.50
        assert new_job.whisper_beam_size == 8
        assert new_job.enable_diarization is True
        assert new_job.source_job_id == src.id
        assert new_job.status == JobStatus.QUEUED
        # The original transcript is untouched.
        original = db.get_job(src.id)
        assert original.status == JobStatus.COMPLETED
        assert original.result_path == src.result_path

    def test_re_transcribe_copies_audio_to_new_job_dir(self, client, db, filestore):
        src = _completed_upload_job_with_audio(db, filestore, filename="meeting.mp3")
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=42.0),
        ):
            client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})

        new_job = mock_enqueue.call_args[0][0]
        # New job has its own independent copy; the source copy survives.
        assert filestore.get_upload_path(new_job.id, "meeting.mp3").exists()
        assert filestore.get_upload_path(src.id, "meeting.mp3").exists()

    def test_re_transcribe_source_audio_gone_returns_409_without_new_job(self, client, db, filestore):
        # Completed job whose upload file was reclaimed by retention (never created here).
        result = TranscriptResult(segments=[], language="zh", duration=60.0)
        src = Job(filename="gone.mp3", status=JobStatus.COMPLETED, language="zh")
        src.result_path = str(filestore.save_result(src.id, result))
        db.insert_job(src)
        before = db.count_jobs()

        with patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue:
            resp = client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})

        assert resp.status_code == 409
        mock_enqueue.assert_not_called()
        assert db.count_jobs() == before

    def test_re_transcribe_enqueue_failure_marks_new_job_failed(self, client, db, filestore):
        src = _completed_upload_job_with_audio(db, filestore)
        with (
            patch(
                "whisper_ui.web.routes.jobs.enqueue_pipeline",
                side_effect=Exception("Redis internal: secret/key leak"),
            ),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=42.0),
        ):
            resp = client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})

        assert resp.status_code == 204
        # The original is untouched; only the new version flips to FAILED with a
        # generic, leak-free message.
        assert db.get_job(src.id).status == JobStatus.COMPLETED
        new_versions = [j for j in list_jobs(db) if j.source_job_id == src.id]
        assert len(new_versions) == 1
        assert new_versions[0].status == JobStatus.FAILED
        assert "secret" not in (new_versions[0].error or "")

    def test_bulk_retry_requeues_failed_jobs_and_emits_trigger(self, client, db):
        """Two failed jobs → bulk retry → both transition to QUEUED and the
        response carries HX-Trigger: refreshJobList (plan §5.6)."""
        failed_a = _create_failed_job(db)
        failed_b = _create_failed_job(db)

        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline"),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=None),
        ):
            resp = client.post("/jobs/bulk/retry", data={"job_ids": f"{failed_a.id},{failed_b.id}"})

        assert resp.status_code == 204
        assert resp.headers.get("hx-trigger") == "refreshJobList"
        # After-settle event carries the success summary so the client can
        # surface a single toast for the operation.
        after_settle = resp.headers.get("hx-trigger-after-settle", "")
        assert "bulkComplete" in after_settle
        for original in (failed_a, failed_b):
            refreshed = db.get_job(original.id)
            assert refreshed.status == JobStatus.QUEUED

    def test_bulk_delete_keeps_active_jobs_and_reports_partial_failure(self, client, db, filestore):
        """COMPLETED jobs delete; PROCESSING jobs are reported failed so the
        active worker is not interrupted (status enum is the gate). Partial
        failure surfaces as bulkPartial in HX-Trigger-After-Settle."""
        completed = _create_completed_job(db, filestore)
        active = Job(filename="active.mp3", status=JobStatus.PROCESSING, language="zh")
        db.insert_job(active)

        resp = client.post(
            "/jobs/bulk/delete",
            data={"job_ids": f"{completed.id},{active.id}"},
        )

        assert resp.status_code == 204
        assert resp.headers.get("hx-trigger") == "refreshJobList"
        after_settle = resp.headers.get("hx-trigger-after-settle", "")
        assert "bulkPartial" in after_settle
        assert db.get_job(completed.id) is None
        assert db.get_job(active.id) is not None

    def test_bulk_action_isolates_jobs_owned_by_other_users(self, client, db, filestore, test_admin):
        """Bulk routes must respect owner_filter — passing another user's
        job_id should be reported as failed, not silently succeed."""
        their_job = Job(filename="theirs.mp3", status=JobStatus.FAILED, owner_id=test_admin.id + 999)
        db.insert_job(their_job)

        resp = client.post("/jobs/bulk/retry", data={"job_ids": their_job.id})

        assert resp.status_code == 204
        # No partial-complete trigger because the job was not owned by the
        # current user → the failed counter went up, succeeded stayed at 0.
        after_settle = resp.headers.get("hx-trigger-after-settle", "")
        assert "bulkPartial" in after_settle
        assert "bulkComplete" not in after_settle
        # The stranger's job is unchanged.
        refreshed = db.get_job(their_job.id)
        assert refreshed.status == JobStatus.FAILED

    def test_delete_job_removes_row_and_logs_when_filestore_fails(self, client, db, filestore, caplog):
        """Row-first delete (C5): the row is removed atomically first to close
        the delete/retry TOCTOU. If the subsequent file reclaim fails, the row
        stays gone and the orphan is logged for manual/retention cleanup —
        the deliberate trade of a rare orphan over acting on a live job.
        """
        import logging as _logging

        job = _create_completed_job(db, filestore)

        with (
            patch.object(filestore, "delete_job_files", side_effect=PermissionError("denied")),
            caplog.at_level(_logging.ERROR, logger="whisper_ui.web.routes.jobs"),
        ):
            resp = client.delete(f"/jobs/{job.id}")

        assert resp.status_code == 204
        # Row removed atomically before the file reclaim.
        assert db.get_job(job.id) is None
        # Orphaned files are logged so an operator can reclaim them.
        assert any("filestore reclaim failed" in r.getMessage() for r in caplog.records)


class TestViewerRoutes:
    def test_viewer_with_job(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert "test.mp3" in resp.text

    def test_viewer_renders_segment_text_server_side(self, client, db, filestore):
        """Regression: transcript text must render as server-side element content,
        not only inside a client-side ``x-html`` attribute.

        v2.3.0 rendered text via ``x-html="whisperHighlight({{ seg.text|tojson }},
        ...)"``. ``tojson`` emits a double-quoted JSON string, which closed the
        double-quoted attribute early and left the expression malformed, so the
        text never rendered whenever Alpine evaluated it. The text must appear as
        visible element body, independent of JS."""
        marker = "逐字稿可見內容XYZ"
        result = TranscriptResult(
            segments=[Segment(start=0.0, end=1.0, text=marker, speaker="SPEAKER_00")],
            language="zh",
            duration=1.0,
        )
        job = Job(filename="render.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")

        assert resp.status_code == 200
        assert f">{marker}</span>" in resp.text
        # Search is enabled under the limit, so the highlight path is wired up.
        assert "data-raw=" in resp.text

    def test_processed_audio_is_playable_and_downloadable(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        path = filestore._output_dir / job.id / "processed.wav"
        path.write_bytes(b"RIFFtestWAVE")

        playable = client.get(f"/viewer/{job.id}/processed-audio")
        assert playable.status_code == 200
        assert playable.content == b"RIFFtestWAVE"
        assert playable.headers["content-disposition"].startswith("inline;")

        downloadable = client.get(f"/viewer/{job.id}/processed-audio?download=1")
        assert downloadable.status_code == 200
        assert downloadable.content == b"RIFFtestWAVE"
        assert downloadable.headers["content-disposition"].startswith("attachment;")
        assert "test.processed.wav" in downloadable.headers["content-disposition"]

        viewer = client.get(f"/viewer/{job.id}")
        assert viewer.status_code == 200
        assert f"/viewer/{job.id}/processed-audio?download=1" in viewer.text
        assert "Scarica audio elaborato (WAV)" in viewer.text
        assert f'/viewer/{job.id}/media" download' not in viewer.text

    def test_export_srt(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}/export/srt")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"

    def test_export_json(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}/export/json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_export_invalid_format_returns_400(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}/export/invalid_format")
        assert resp.status_code == 400
        # The fixed error message must not echo the caller-supplied format.
        assert "invalid_format" not in resp.text

    def test_export_non_ascii_filename(self, client, db, filestore):
        result = TranscriptResult(segments=[], language="zh", duration=60.0)
        job = Job(filename="\u6e2c\u8a66\u97f3\u6a94.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)
        resp = client.get(f"/viewer/{job.id}/export/srt")
        assert resp.status_code == 200
        cd = resp.headers["content-disposition"]
        assert "filename*=UTF-8''" in cd
        assert "%E6%B8%AC%E8%A9%A6%E9%9F%B3%E6%AA%94" in cd


class TestUploadPost:
    def _upload(self, client, files=None, **form_data):
        data = {
            "language": form_data.get("language", "zh"),
            "model_name": form_data.get("model_name", "large-v3"),
            "num_speakers": form_data.get("num_speakers", "0"),
        }
        file_list = files or [("files", ("test.mp3", b"fake audio data", "audio/mpeg"))]
        return client.post("/upload", data=data, files=file_list, follow_redirects=False)

    def test_upload_post_redirects_to_clean_jobs_and_flashes(self, client, app):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(client)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        # The success toast rides in the session flash, rendered on the next
        # full-page load rather than via query params.
        page = client.get("/jobs")
        assert flash_messages(page.text) == [ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "1")]

    def test_upload_rejects_pdf_disguised_as_audio(self, client):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(
                client,
                files=[("files", ("evil.mp3", b"%PDF-1.7 not really audio", "audio/mpeg"))],
            )
        assert resp.status_code == 303
        assert "error=invalid_content" in resp.headers["location"]

    def test_upload_skips_invalid_file_but_submits_valid_ones(self, client, db, app):
        """A non-media file in a batch is skipped, not fatal: the valid files
        are still queued and the user is told how many were skipped (rather
        than the whole batch failing and prompting a duplicate re-upload)."""
        files = [
            ("files", ("good.mp3", b"ID3 fake audio payload", "audio/mpeg")),
            ("files", ("evil.mp3", b"%PDF-1.7 not really audio", "audio/mpeg")),
        ]
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as mock_enqueue:
            resp = self._upload(client, files=files)

        # Valid file went through; redirect is the success path to /jobs.
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        assert mock_enqueue.call_count == 1
        jobs = list_jobs(db)
        assert len(jobs) == 1 and jobs[0].filename == "good.mp3"
        # The toast reports the skipped file.
        page = client.get("/jobs")
        expected = ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "1") + ui_labels.TOAST_FILE_SKIPPED.replace(
            "{count}", "1"
        )
        assert flash_messages(page.text) == [expected]

    def test_upload_clamps_diarization_and_llm_when_unavailable(self, client, db, app):
        # Force neither hf_token nor ollama_base_url, so both opt-in flags must
        # be clamped to False at persistence even when posted true.
        app.state.settings = app.state.settings.model_copy(update={"hf_token": "", "ollama_base_url": ""})
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as mock_enqueue:
            resp = client.post(
                "/upload",
                data={
                    "language": "zh",
                    "model_name": "large-v3",
                    "num_speakers": "0",
                    "enable_diarization": "true",
                    "llm_correction_enabled": "true",
                },
                files=[("files", ("test.mp3", b"fake audio data", "audio/mpeg"))],
                follow_redirects=False,
            )
        assert resp.status_code == 303
        job = mock_enqueue.call_args[0][0]
        assert job.enable_diarization is False
        assert job.llm_correction_enabled is False

    def test_upload_all_vad_settings_persist_and_are_not_global(self, client, db):
        controls = {
            "vad_enabled": "false",
            "vad_threshold": "0.15",
            "vad_min_speech_ms": "80",
            "vad_min_silence_ms": "600",
            "vad_speech_pad_ms": "800",
            "vad_samples_overlap": "0.50",
        }
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as enqueue:
            resp = client.post(
                "/upload",
                data=controls,
                files=[("files", ("meeting.wav", b"test audio", "audio/wav"))],
                follow_redirects=False,
            )
        assert resp.status_code == 303
        job = enqueue.call_args[0][0]
        assert (
            job.vad_enabled,
            job.vad_threshold,
            job.vad_min_speech_ms,
            job.vad_min_silence_ms,
            job.vad_speech_pad_ms,
            job.vad_samples_overlap,
        ) == (False, 0.15, 80, 600, 800, 0.5)
        assert db.get_job(job.id).vad_enabled is False
        assert db.get_job(job.id).vad_speech_pad_ms == 800
        page = client.get("/upload")
        assert page.status_code == 200
        for field in controls:
            assert f'name="{field}"' in page.text
        assert "Sovrapposizione" in page.text
        assert "Diretta · senza Silero" in page.text

    def test_upload_invalid_vad_boundary_settings_rejected(self, client, db):
        for field, value in (
            ("vad_min_speech_ms", "-1"),
            ("vad_min_silence_ms", "10001"),
            ("vad_speech_pad_ms", "5001"),
            ("vad_samples_overlap", "1.01"),
            ("vad_samples_overlap", "-0.1"),
            ("vad_enabled", "unknown"),
        ):
            response = client.post("/upload", data={field: value}, files=[("files", ("test.wav", b"raw", "audio/wav"))])
            assert response.status_code == 422, (field, value)
        assert list_jobs(db) == []

    def test_upload_too_large_cleans_up_partial_file(self, client, app, filestore):
        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 5})
        files = [("files", ("big.mp3", b"x" * 10, "audio/mpeg"))]
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        # Verify no partial files remain in upload dir
        upload_files = list(filestore._upload_dir.rglob("big.mp3"))
        assert upload_files == []

    def test_upload_partial_enqueue_failure_reports_failed_count(self, client, app, db):
        # First file succeeds, second raises — simulate a transient Redis error.
        enqueue_side_effects = [None, Exception("Redis hiccup")]
        files = [
            ("files", ("a.mp3", b"data1", "audio/mpeg")),
            ("files", ("b.mp3", b"data2", "audio/mpeg")),
        ]
        with patch(
            "whisper_ui.web.routes.upload.enqueue_pipeline",
            side_effect=enqueue_side_effects,
        ):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        page = client.get("/jobs")
        expected = ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "1") + ui_labels.TOAST_UPLOAD_FAILED.replace(
            "{count}", "1"
        )
        assert flash_messages(page.text) == [expected]
        statuses = sorted(j.status for j in list_jobs(db))
        assert statuses == sorted([JobStatus.QUEUED, JobStatus.FAILED])

    def test_upload_htmx_error_escapes_html(self, client, app):
        """Verify htmx error responses escape user input to prevent XSS."""
        files = [("files", ("test.mp3", b"fake", "audio/mpeg"))]
        resp = client.post(
            "/upload",
            data={"language": "<script>alert(1)</script>", "model_name": "large-v3"},
            files=files,
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert "<script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


class TestUploadURLPost:
    def _post_url(self, client, url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", **form_data):
        data = {
            "url": url,
            "language": form_data.get("language", "zh"),
            "model_name": form_data.get("model_name", "large-v3"),
            "num_speakers": form_data.get("num_speakers", "0"),
        }
        return client.post("/upload/url", data=data, follow_redirects=False)

    def test_upload_url_creates_job_with_source_url(self, client, app, db):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            self._post_url(client)
        jobs = list_jobs(db)
        assert len(jobs) == 1
        assert jobs[0].source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_upload_x_url_creates_job_with_canonical_source_url(self, client, app, db):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            self._post_url(client, url="https://x.com/jack/status/20?s=20")
        jobs = list_jobs(db)
        assert len(jobs) == 1
        assert jobs[0].source_url == "https://x.com/i/status/20"

    def test_upload_invalid_url_redirects(self, client):
        resp = self._post_url(client, url="https://example.com/not-youtube")
        assert resp.status_code == 303
        assert "error=all_invalid_urls" in resp.headers["location"]


class TestUploadPlaylistPost:
    _PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
    _VIDEO_URLS = [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        "https://www.youtube.com/watch?v=ccccccccccc",
    ]

    def _post_url(self, client, url):
        data = {"url": url, "language": "zh", "model_name": "large-v3", "num_speakers": "0"}
        return client.post("/upload/url", data=data, follow_redirects=False)

    def _info(self, video_urls=None, title="Team Meetings 2026Q2", unavailable_count=0):
        from whisper_ui.web.playlist import PlaylistInfo

        return PlaylistInfo(
            title=title, video_urls=video_urls or list(self._VIDEO_URLS), unavailable_count=unavailable_count
        )

    def test_upload_playlist_expands_to_batch_with_shared_id_and_title(self, client, app, db):
        with (
            patch("whisper_ui.web.routes.upload.enqueue_pipeline"),
            patch("whisper_ui.web.routes.upload.expand_playlist", return_value=self._info()),
        ):
            resp = self._post_url(client, url=self._PLAYLIST_URL)

        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        jobs = list_jobs(db)
        assert sorted(j.source_url for j in jobs) == self._VIDEO_URLS
        assert len({j.batch_id for j in jobs}) == 1
        assert jobs[0].batch_id is not None
        assert {j.batch_title for j in jobs} == {"Team Meetings 2026Q2"}

    def test_upload_playlist_overlapping_explicit_video_dedupes(self, client, app, db):
        mixed = f"{self._PLAYLIST_URL}\n{self._VIDEO_URLS[0]}"
        with (
            patch("whisper_ui.web.routes.upload.enqueue_pipeline"),
            patch("whisper_ui.web.routes.upload.expand_playlist", return_value=self._info()),
        ):
            resp = self._post_url(client, url=mixed)

        assert resp.status_code == 303
        assert len(list_jobs(db)) == 3
        page = client.get("/jobs")
        expected = ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "3") + ui_labels.TOAST_URL_DEDUPED.replace(
            "{count}", "1"
        )
        assert flash_messages(page.text) == [expected]

    def test_upload_playlist_pushing_total_over_limit_rejected(self, client, db):
        from whisper_ui.core.constants import MAX_BATCH_SIZE

        full = [f"https://www.youtube.com/watch?v=vid{i:08d}" for i in range(MAX_BATCH_SIZE)]
        mixed = f"{self._PLAYLIST_URL}\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ"
        with patch("whisper_ui.web.routes.upload.expand_playlist", return_value=self._info(video_urls=full)):
            resp = self._post_url(client, url=mixed)

        assert resp.status_code == 303
        assert "error=too_many_urls" in resp.headers["location"]
        assert f"count={MAX_BATCH_SIZE + 1}" in resp.headers["location"]
        assert list_jobs(db) == []


class TestBatchRoutes:
    _BATCH_ID = "a" * 32

    def _create_batch(self, db, filestore, batch_id=None):
        batch_id = batch_id or self._BATCH_ID
        jobs = []
        for i, name in enumerate(["a.mp3", "b.mp3"]):
            result = TranscriptResult(
                segments=[Segment(start=0.0, end=1.0, text=f"Hello {i}")],
                language="zh",
                duration=1.0,
            )
            job = Job(filename=name, status=JobStatus.COMPLETED, language="zh", batch_id=batch_id)
            filestore.save_result(job.id, result)
            job.result_path = "dummy"
            db.insert_job(job)
            jobs.append(job)
        return jobs

    def test_batch_download_success(self, client, db, filestore):
        self._create_batch(db, filestore)
        resp = client.get(f"/jobs/batch/{self._BATCH_ID}/download?format_name=txt")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

    def test_retry_batch(self, client, db, app):
        batch_id = "b" * 32
        job = Job(filename="fail.mp3", status=JobStatus.FAILED, error="err", batch_id=batch_id)
        db.insert_job(job)
        with patch("whisper_ui.web.routes.jobs.enqueue_pipeline"):
            resp = client.post(f"/jobs/batch/{batch_id}/retry")
        assert resp.status_code == 204

    def test_delete_batch(self, client, db, filestore):
        batch_id = "c" * 32
        jobs = self._create_batch(db, filestore, batch_id=batch_id)
        resp = client.delete(f"/jobs/batch/{batch_id}")
        assert resp.status_code == 204
        for job in jobs:
            assert db.get_job(job.id) is None


class TestRetentionSweep:
    """End-to-end checks on _run_retention_sweep's backlog handling.

    Reclaiming an upload dir does not modify the corresponding DB row
    (the row + result.json are kept so the viewer keeps working). That
    means the same id list comes back on every sweep; the loop must
    therefore only count *successful* deletions against the batch limit,
    otherwise a backlog larger than the limit stalls forever on the
    first N already-reclaimed ids.
    """

    def _make_expired_completed_job(self, db, filestore, settings, idx: int, old_iso: str) -> str:
        from whisper_ui.core.models import Job

        job = Job(filename=f"old-{idx}.mp3", status=JobStatus.COMPLETED, language="zh")
        db.insert_job(job)
        # Seed an upload dir so delete_upload_files has something to reclaim.
        (settings.upload_dir / job.id).mkdir(parents=True)
        (settings.upload_dir / job.id / "source.mp3").write_bytes(b"x")
        # Backdate updated_at past the retention threshold.
        db._conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old_iso, job.id))
        db._conn.commit()
        return job.id

    def test_sweep_progresses_past_batch_limit_across_runs(self, db, filestore, settings, tmp_dir):
        """Two-sweep walk: a backlog of (limit + 52) drains in one full
        batch + one partial batch instead of stalling on the first batch.
        Regression for PR #41 followup F-batch-stall."""
        from datetime import UTC, datetime, timedelta

        from whisper_ui.web.app import _run_retention_sweep

        limit = 50  # keep the test fast; algorithm is identical at any limit
        backlog = limit + 20  # one full batch + a partial batch left over
        now = datetime.now(UTC)
        old_iso = (now - timedelta(days=30)).isoformat()
        threshold_iso = (now - timedelta(days=7)).isoformat()

        job_ids = [self._make_expired_completed_job(db, filestore, settings, i, old_iso) for i in range(backlog)]

        # Close the test's fixture-owned connection so the sweep's
        # short-lived connection sees the committed rows.
        db.close()

        # Sweep 1: fills its budget with successful deletions.
        removed_1 = _run_retention_sweep(settings.database_path, filestore, threshold_iso, limit)
        assert removed_1 == limit
        remaining_after_1 = sum(1 for jid in job_ids if (settings.upload_dir / jid).exists())
        assert remaining_after_1 == backlog - limit

        # Sweep 2: must reach the leftover 20 even though the first
        # `limit` ids in the query result are now already-reclaimed.
        removed_2 = _run_retention_sweep(settings.database_path, filestore, threshold_iso, limit)
        assert removed_2 == backlog - limit
        remaining_after_2 = sum(1 for jid in job_ids if (settings.upload_dir / jid).exists())
        assert remaining_after_2 == 0

        # Sweep 3: nothing left to do.
        removed_3 = _run_retention_sweep(settings.database_path, filestore, threshold_iso, limit)
        assert removed_3 == 0


class TestStandaloneLLMCorrectionRoutes:
    def test_completed_job_can_request_correction_without_retranscribing(self, client, app, db, filestore):
        app.state.settings = app.state.settings.model_copy(
            update={
                "ollama_base_url": "http://192.168.0.150:18084",
                "llm_api_mode": "openai-router",
            }
        )
        job = _create_completed_job(db, filestore)
        with (
            patch(
                "whisper_ui.worker.standalone_correction.correction_status",
                return_value={"status": "not_requested"},
            ),
            patch(
                "whisper_ui.worker.standalone_correction.enqueue_correction",
                return_value={"status": "queued"},
            ) as enqueue,
        ):
            response = client.post(
                f"/viewer/{job.id}/correct",
                data={"llm_context_prompt": "Meeting Airdass, API tecniche."},
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"] == f"/viewer/{job.id}?correction=queued"
        assert db.get_job(job.id).llm_context_prompt == "Meeting Airdass, API tecniche."
        enqueue.assert_called_once_with(app.state.redis, job.id)
        assert db.get_job(job.id).status == JobStatus.COMPLETED

    def test_original_and_corrected_are_separate_readable_and_exportable(self, client, db, filestore, app):
        app.state.settings = app.state.settings.model_copy(update={"ollama_base_url": "http://192.168.0.150:18084"})
        original = TranscriptResult(
            segments=[Segment(0, 2, "Airdas e la piattaforma.", "S1"), Segment(2, 4, "Allora iniziamo.", "S1")],
            language="it",
            duration=4,
        )
        corrected = TranscriptResult(
            segments=[Segment(0, 2, "Airdass e la piattaforma.", "S1"), Segment(2, 4, "Allora iniziamo.", "S1")],
            language="it",
            duration=4,
        )
        job = Job(filename="meeting.wav", status=JobStatus.COMPLETED, language="it")
        job.result_path = str(filestore.save_result(job.id, original))
        filestore.save_corrected_result(job.id, corrected)
        db.insert_job(job)
        page = client.get(f"/viewer/{job.id}")
        assert page.status_code == 200
        assert "Whisper originale" in page.text
        assert "Paragrafi" in page.text
        assert "Airdass e la piattaforma" in page.text
        assert "Ricorreggi con LLM" in page.text
        assert "version=corrected" in page.text
        raw = client.get(f"/viewer/{job.id}/export/txt")
        fixed = client.get(f"/viewer/{job.id}/export/txt?version=corrected")
        assert raw.status_code == 200 and fixed.status_code == 200
        assert "Airdas e la piattaforma" in raw.text
        assert "Airdass e la piattaforma" in fixed.text
        assert "meeting-corrected" in fixed.headers["content-disposition"]
        assert filestore.load_result(job.id).segments[0].text == "Airdas e la piattaforma."


class TestSavedTranscriptionPresets:
    """Complete preset CRUD, cloning, isolation and upload integration."""

    def _create(self, client, **overrides):
        fields = {
            "name": "Meeting Airdass",
            "description": "Per i miei meeting",
            "language": "it",
            "model_name": "large-v3",
            "num_speakers": "2",
            "enable_diarization": "true",
            "llm_correction_enabled": "true",
            "whisper_keywords": "Airdass, API, Customer Success",
            "llm_context_prompt": "Correggi i termini Airdass senza inventare nulla.",
            "summary_prompt": "Elenca decisioni e responsabili.",
            "summary_enabled": "true",
            "vad_threshold": "0.15",
            "vad_enabled": "false",
            "vad_min_speech_ms": "80",
            "vad_min_silence_ms": "600",
            "vad_speech_pad_ms": "800",
            "vad_samples_overlap": "0.50",
            "whisper_beam_size": "8",
            "audio_processing": "custom",
            "audio_denoise": "medium",
            "audio_compression": "light",
            "audio_target_lufs": "-18",
            "audio_max_gain_db": "12",
            "audio_highpass_hz": "100",
        }
        fields.update(overrides)
        return client.post("/presets", data=fields, follow_redirects=False)

    def test_save_complete_settings_and_upload_can_select_it(self, client, db, test_user):
        from whisper_ui.storage.presets import PresetRepository

        response = self._create(client)
        assert response.status_code == 303
        assert response.headers["location"] == "/presets"
        preset = PresetRepository(db.conn).list_for_owner(test_user.id)[0]
        assert preset.settings["language"] == "it"
        assert preset.settings["enable_diarization"] is True
        assert preset.settings["llm_correction_enabled"] is True
        assert preset.settings["whisper_keywords"] == "Airdass, API, Customer Success"
        assert preset.settings["llm_context_prompt"].startswith("Correggi")
        assert preset.settings["summary_prompt"].startswith("Elenca")
        assert preset.settings["summary_enabled"] is True
        assert preset.settings["vad_threshold"] == 0.15
        assert preset.settings["vad_enabled"] is False
        assert preset.settings["vad_speech_pad_ms"] == 800
        assert preset.settings["vad_samples_overlap"] == 0.50
        assert preset.settings["audio_target_lufs"] == -18
        assert preset.settings["audio_denoise"] == "medium"
        uploaded = client.get(f"/upload?preset={preset.id}")
        assert uploaded.status_code == 200
        assert "Meeting Airdass" in uploaded.text
        assert f'"{preset.id}"' in uploaded.text
        assert '"vad_threshold": 0.15' in uploaded.text
        assert '"audio_denoise": "medium"' in uploaded.text
        assert '"llm_context_prompt": "Correggi' in uploaded.text
        assert f"/presets/{preset.id}/edit" in client.get("/presets").text

    def test_preset_settings_are_snapshotted_in_real_upload_job(self, client, app, db, test_user):
        from whisper_ui.storage.presets import PresetRepository

        self._create(client)
        config = PresetRepository(db.conn).list_for_owner(test_user.id)[0].settings
        app.state.settings = app.state.settings.model_copy(
            update={
                "ollama_base_url": "http://ai-router:18084",
                "hf_token": "test-token-not-real",
            }
        )
        # These are the complete controls populated by applyPreset() before
        # the browser sends its regular POST. No special pipeline or model path.
        form = {k: str(v) for k, v in config.items() if not isinstance(v, bool)}
        form.update({k: "true" for k, v in config.items() if isinstance(v, bool) and v})
        form["vad_enabled"] = "true" if config["vad_enabled"] else "false"
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as enqueue:
            resp = client.post(
                "/upload",
                data=form,
                files=[("files", ("meeting.wav", b"test audio", "audio/wav"))],
                follow_redirects=False,
            )
        assert resp.status_code == 303
        job = enqueue.call_args[0][0]
        assert job.language == "it"
        assert job.model_name == "large-v3"
        assert job.num_speakers == 2
        assert job.enable_diarization is True
        assert job.llm_correction_enabled is True
        assert job.whisper_keywords == "Airdass, API, Customer Success"
        assert job.llm_context_prompt.startswith("Correggi")
        assert job.summary_prompt == "Elenca decisioni e responsabili."
        assert job.summary_enabled is True
        assert job.vad_threshold == 0.15
        assert job.vad_enabled is False
        assert job.vad_min_speech_ms == 80
        assert job.vad_min_silence_ms == 600
        assert job.vad_speech_pad_ms == 800
        assert job.vad_samples_overlap == 0.50
        assert job.whisper_beam_size == 8
        assert job.audio_processing == "custom"
        assert job.audio_denoise == "medium"
        assert job.audio_compression == "light"
        assert job.audio_target_lufs == -18
        assert job.audio_max_gain_db == 12
        assert job.audio_highpass_hz == 100
        assert db.get_job(job.id).llm_context_prompt == job.llm_context_prompt
        # Deleting the saved preset must not change settings stored in a job.
        preset = PresetRepository(db.conn).list_for_owner(test_user.id)[0]
        PresetRepository(db.conn).delete(preset.id, test_user.id)
        assert db.get_job(job.id).vad_threshold == 0.15

    def test_clone_saved_preset_and_edit_without_mutating_original(self, client, db, test_user):
        from whisper_ui.storage.presets import PresetRepository

        self._create(client)
        repo = PresetRepository(db.conn)
        first = repo.list_for_owner(test_user.id)[0]
        clone = client.get(f"/presets/new?source={first.id}")
        assert clone.status_code == 200
        assert "Customer Success" in clone.text
        assert "Correggi i termini" in clone.text
        response = self._create(client, name="Meeting duplicato", vad_threshold="0.35")
        assert response.status_code == 303
        latest = {p.name: p for p in repo.list_for_owner(test_user.id)}
        assert latest["Meeting Airdass"].settings["vad_threshold"] == 0.15
        assert latest["Meeting duplicato"].settings["vad_threshold"] == 0.35
        updated = client.post(
            f"/presets/{first.id}/edit",
            data={"name": "Airdass aggiornato", "language": "it", "vad_threshold": "0.30"},
            follow_redirects=False,
        )
        assert updated.status_code == 303
        assert repo.get(first.id, test_user.id).name == "Airdass aggiornato"
        assert repo.get(first.id, test_user.id).settings["vad_threshold"] == 0.30
        assert client.post(f"/presets/{first.id}/delete", follow_redirects=False).status_code == 303
        assert repo.get(first.id, test_user.id) is None
        assert len(repo.list_for_owner(test_user.id)) == 1

    def test_owner_isolation_and_no_cross_user_preset_lookups(self, client, admin_client, db, test_user):
        from whisper_ui.storage.presets import PresetRepository

        self._create(client)
        stored = PresetRepository(db.conn).list_for_owner(test_user.id)[0]
        assert admin_client.get(f"/presets/{stored.id}/edit").status_code == 404
        assert admin_client.get(f"/presets/new?source={stored.id}").status_code == 404
        assert admin_client.get(f"/upload?preset={stored.id}").status_code == 404
        assert admin_client.post(f"/presets/{stored.id}/delete", follow_redirects=False).status_code == 404
        assert PresetRepository(db.conn).get(stored.id, test_user.id) is not None

    def test_validation_duplicates_and_prompt_escaping(self, client, db, test_user):
        from whisper_ui.storage.presets import PresetRepository

        assert self._create(client, vad_threshold="0.01").status_code == 200
        assert PresetRepository(db.conn).list_for_owner(test_user.id) == []
        assert self._create(client, name="").status_code == 200
        assert self._create(client, name="<script>alert(1)</script>").status_code == 303
        page = client.get("/presets")
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text
        assert self._create(client, name="<SCRIPT>ALERT(1)</SCRIPT>").status_code == 200


class TestReviewStudio:
    def _make(self, db, filestore, *, source_job_id=None, text="Buongiorno Airdas"):
        result = TranscriptResult(
            segments=[Segment(1, 4, text, "S1"), Segment(5, 7, "Grazie a tutti.", "S2")],
            language="it",
            duration=8,
        )
        job = Job(filename="meeting.wav", status=JobStatus.COMPLETED, source_job_id=source_job_id)
        job.result_path = str(filestore.save_result(job.id, result))
        db.insert_job(job)
        return job

    def test_three_column_editor_and_families_render(self, client, db, filestore):
        original = self._make(db, filestore)
        save_upload(filestore, original.id, "meeting.wav", b"fake audio")
        original.filepath = str(filestore.get_upload_path(original.id, "meeting.wav"))
        db.update_job(original)
        second = self._make(db, filestore, source_job_id=original.id, text="Buongiorno Airdass")
        corrected = TranscriptResult(
            segments=[Segment(1, 4, "Buongiorno Airdass", "S1"), Segment(5, 7, "Grazie a tutti.", "S2")],
            language="it",
            duration=8,
        )
        filestore.save_corrected_result(second.id, corrected)
        response = client.get(f"/studio/{second.id}")
        assert response.status_code == 200
        assert "at2-review-shell" in response.text
        assert "Versione di confronto" in response.text
        assert "La mia versione" in response.text
        assert "Riassunto AI" in response.text
        assert f"{original.id}~original" in response.text
        assert f"{second.id}~corrected" in response.text
        assert 'id="studio-timeline-seek"' in response.text
        assert 'id="studio-timeline-overview"' in response.text
        assert "onAudioTimeUpdate" in response.text
        assert "Segui testo: attivo" in response.text
        assert "data-studio-index" in response.text
        assert "<canvas" not in response.text
        assert "/waveform?" not in response.text
        assert client.get(f"/viewer/{second.id}").status_code == 200
        assert f"/studio/{second.id}" in client.get(f"/viewer/{second.id}").text

    def test_version_endpoint_and_only_same_family(self, client, db, filestore):
        original = self._make(db, filestore)
        reprocessed = self._make(db, filestore, source_job_id=original.id)
        foreign_recording = self._make(db, filestore)
        got = client.get(f"/studio/{reprocessed.id}/version/{original.id}~original")
        assert got.status_code == 200
        assert got.json()["segments"][0]["text"] == "Buongiorno Airdas"
        denied = client.get(f"/studio/{reprocessed.id}/version/{foreign_recording.id}~original")
        assert denied.status_code == 404

    def test_custom_text_and_speaker_saved_separately_preserving_timing(self, client, db, filestore):
        job = self._make(db, filestore)
        source = f"{job.id}~original"
        response = client.post(
            f"/studio/{job.id}/custom",
            json={
                "source": source,
                "segments": [
                    {"text": "Buongiorno Airdass", "speaker": "Responsabile"},
                    {"text": "Grazie a tutti.", "speaker": "S2"},
                ],
            },
        )
        assert response.status_code == 200
        assert response.json()["version"] == f"{job.id}~custom"
        original = filestore.load_result(job.id)
        edited = filestore.load_custom_result(job.id)
        assert original.segments[0].text == "Buongiorno Airdas"
        assert edited.segments[0].text == "Buongiorno Airdass"
        assert edited.segments[0].speaker == "Responsabile"
        assert (edited.segments[0].start, edited.segments[0].end) == (1, 4)
        assert client.get(f"/studio/{job.id}/version/{job.id}~custom").status_code == 200
        exported = client.get(f"/studio/{job.id}/custom-export?token={job.id}~custom&format_name=txt")
        assert exported.status_code == 200
        assert "Airdass" in exported.text
        assert "personalizzata" in exported.headers["content-disposition"]

    def test_editor_rejects_invalid_sources_and_timing_changes(self, client, db, filestore):
        job = self._make(db, filestore)
        url = f"/studio/{job.id}/custom"
        for data in (
            {"source": f"{job.id}~original", "segments": [{"text": "Too short"}]},
            {
                "source": f"{job.id}~original",
                "segments": [
                    {"text": "A", "start": 999, "speaker": "S1"},
                    {"text": 456, "speaker": "S2"},
                ],
            },
            {"source": "invalid", "segments": [{"text": "A"}, {"text": "B"}]},
        ):
            assert client.post(url, json=data).status_code in (400, 422)
        assert filestore.load_custom_result(job.id) is None

    def test_studio_rejects_unauthorized_user(self, client, app, db, filestore):
        from whisper_ui.storage import users_repo

        bob = users_repo.create_user(db.conn, "other-reviewer", "password123", is_admin=False)
        other_client = authed_test_client(app, bob)
        job = self._make(db, filestore)
        assert other_client.get(f"/studio/{job.id}").status_code == 404
        assert other_client.get(f"/studio/{job.id}/version/{job.id}~original").status_code == 404
        assert (
            other_client.post(
                f"/studio/{job.id}/custom",
                json={
                    "source": f"{job.id}~original",
                    "segments": [
                        {"text": "A", "speaker": "S1"},
                        {"text": "B", "speaker": "S2"},
                    ],
                },
            ).status_code
            == 404
        )

    def test_summary_prompt_is_queued_without_changing_original(self, client, app, db, filestore):
        job = self._make(db, filestore)
        app.state.settings = app.state.settings.model_copy(
            update={
                "ollama_base_url": "http://router:18084",
            }
        )
        with patch("whisper_ui.worker.standalone_summary.enqueue_summary", return_value={"status": "queued"}) as enq:
            response = client.post(
                f"/studio/{job.id}/summary",
                json={
                    "source": f"{job.id}~original",
                    "prompt": "Riassumi decisioni e rischi.",
                },
            )
        assert response.status_code == 200
        enq.assert_called_once_with(app.state.redis, job.id, "Riassumi decisioni e rischi.", "original")
        assert db.get_job(job.id).summary_prompt == "Riassumi decisioni e rischi."
        assert filestore.load_result(job.id).segments[0].text == "Buongiorno Airdas"
        status = client.get(f"/studio/{job.id}/summary-status?source_job={job.id}")
        assert status.status_code == 200
        assert status.json()["summary"] is None


class TestJoinedAudioUploads:
    """Merge mode is explicitly opt-in and produces exactly one auditable job."""

    def test_upload_ordered_files_yields_one_job_and_manifest(self, client, db, filestore):
        import json

        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as enqueue:
            response = client.post(
                "/upload",
                data={
                    "merge_mode": "merge",
                    "merge_order": "2,0,1",
                    "language": "it",
                    "vad_threshold": "0.15",
                    "vad_speech_pad_ms": "800",
                },
                files=[
                    ("files", ("one.mp3", b"FILE_ONE", "audio/mpeg")),
                    ("files", ("two.wav", b"FILE_TWO", "audio/wav")),
                    ("files", ("three.m4a", b"FILE_THREE", "audio/mp4")),
                ],
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"
        assert len(list_jobs(db)) == 1
        job = enqueue.call_args.args[0]
        assert job.filename == "registrazione-unita.wav"
        assert job.duration is None
        assert job.vad_threshold == 0.15
        assert job.vad_speech_pad_ms == 800
        assert not filestore.get_upload_path(job.id, job.filename).exists()
        manifest = json.loads(filestore.get_upload_path(job.id, "merge-manifest.json").read_text())
        assert [x["original_name"] for x in manifest["parts"]] == ["three.m4a", "one.mp3", "two.wav"]
        assert [x["filename"] for x in manifest["parts"]] == ["part-0001.m4a", "part-0002.mp3", "part-0003.wav"]
        assert [filestore.get_upload_path(job.id, x["filename"]).read_bytes() for x in manifest["parts"]] == [
            b"FILE_THREE",
            b"FILE_ONE",
            b"FILE_TWO",
        ]

    def test_invalid_merge_order_rejects_before_any_job(self, client, db):
        for order in ("0,0", "2,0", "-1,0", "abc,0", "0,1,2"):
            with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as enqueue:
                response = client.post(
                    "/upload",
                    data={"merge_mode": "merge", "merge_order": order},
                    files=[
                        ("files", ("a.mp3", b"FILE_A", "audio/mpeg")),
                        ("files", ("b.mp3", b"FILE_B", "audio/mpeg")),
                    ],
                )
            assert response.status_code == 422, order
            enqueue.assert_not_called()
        assert list_jobs(db) == []

    def test_merge_rejects_any_unsupported_or_nonmedia_part_atomically(self, client, db, filestore):
        for bad_filename, bad_bytes in [("wrong.pdf", b"doc"), ("disguised.mp3", b"%PDF bad")]:
            with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as enqueue:
                response = client.post(
                    "/upload",
                    data={"merge_mode": "merge"},
                    files=[
                        ("files", ("valid.mp3", b"FILE_A", "audio/mpeg")),
                        ("files", (bad_filename, bad_bytes, "application/octet-stream")),
                    ],
                    follow_redirects=False,
                )
            assert response.status_code == 303
            assert "error=merge_invalid" in response.headers["location"]
            enqueue.assert_not_called()
        assert list_jobs(db) == []
        assert not list(filestore._upload_dir.glob("*/merge-manifest.json"))

    def test_merge_checks_total_upload_size_and_cleans_parts(self, client, app, db, filestore):
        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 15})
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as enqueue:
            response = client.post(
                "/upload",
                data={"merge_mode": "merge"},
                files=[
                    ("files", ("first.mp3", b"1234567890", "audio/mpeg")),
                    ("files", ("second.wav", b"1234567890", "audio/wav")),
                ],
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert "error=merge_invalid" in response.headers["location"]
        assert list_jobs(db) == []
        enqueue.assert_not_called()
        assert not list(filestore._upload_dir.glob("*/part-*"))

    def test_separate_mode_remains_the_default_for_batches(self, client, db):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as enqueue:
            response = client.post(
                "/upload",
                files=[
                    ("files", ("part1.mp3", b"FILE_ONE", "audio/mpeg")),
                    ("files", ("part2.wav", b"FILE_TWO", "audio/wav")),
                ],
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert len(list_jobs(db)) == 2
        assert enqueue.call_count == 2


class TestPosthocTranscriptJoin:
    """Only owned, finished jobs can be joined; order and variants are frozen."""

    def _completed(self, db, filestore, text="Ciao", *, owner_id=None, has_audio=True):
        job = Job(filename=f"{text}.wav", status=JobStatus.COMPLETED, owner_id=owner_id, duration=4)
        filestore.save_result(job.id, TranscriptResult([Segment(0.1, 1, text, "S1")], "it", 4))
        if has_audio:
            (filestore._output_dir / job.id / "processed.wav").write_bytes(b"RIFF" + b"\0" * 80)
        db.insert_job(job)
        return job

    def test_options_return_owned_jobs_and_transcript_variants(self, client, db, filestore):
        first = self._completed(db, filestore, "Prima")
        second = self._completed(db, filestore, "Seconda", has_audio=False)
        filestore.save_corrected_result(second.id, TranscriptResult([Segment(0, 1, "Corretto")], "it", 4))
        filestore.save_custom_result(second.id, TranscriptResult([Segment(0, 1, "Manuale")], "it", 4))
        resp = client.get("/jobs/merge/options", params={"job_ids": f"{second.id},{first.id}"})
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert [j["id"] for j in jobs] == [second.id, first.id]
        assert [v["value"] for v in jobs[0]["versions"]] == ["original", "corrected", "custom"]
        assert jobs[0]["has_audio"] is False
        assert jobs[1]["has_audio"] is True
        page = client.get("/jobs")
        assert "Unisci trascrizioni" in page.text
        assert "mergeCompletedJobsDialog()" in page.text
        assert "Crea trascrizione unita" in page.text

    def test_join_creates_new_job_manifest_and_enqueues_io_not_gpu(self, client, db, filestore):
        first = self._completed(db, filestore, "Prima")
        second = self._completed(db, filestore, "Seconda")
        with patch("whisper_ui.web.routes.merge_jobs.enqueue_join") as enqueue:
            resp = client.post(
                "/jobs/merge",
                data={
                    "job_ids": f"{second.id},{first.id}",
                    "versions": "original,original",
                    "title": "Riunione completa",
                },
            )
        assert resp.status_code == 202, resp.text
        jid = resp.json()["job_id"]
        job = db.get_job(jid)
        assert job.status == JobStatus.QUEUED
        assert job.filename == "Riunione completa.wav"
        assert job.owner_id == first.owner_id
        assert job.duration is None
        import json

        manifest = json.loads(filestore.get_upload_path(jid, "joined-sources.json").read_text())
        assert manifest["sources"] == [
            {"job_id": second.id, "transcript": "original"},
            {"job_id": first.id, "transcript": "original"},
        ]
        enqueue.assert_called_once_with(client.app.state.redis, jid)
        assert db.get_job(first.id).status == JobStatus.COMPLETED
        assert db.get_job(second.id).status == JobStatus.COMPLETED

    @pytest.mark.parametrize("kind", ("duplicate", "single", "invalid-version", "overlong-title"))
    def test_bad_join_request_rejected_without_creating_job(self, client, db, filestore, kind):
        a = self._completed(db, filestore, "Prima")
        b = self._completed(db, filestore, "Seconda")
        data = {"job_ids": f"{a.id},{b.id}", "versions": "original,original", "title": "Unione"}
        if kind == "duplicate":
            data["job_ids"] = f"{a.id},{a.id}"
        if kind == "single":
            data["job_ids"] = a.id
        if kind == "invalid-version":
            data["versions"] = "custom,original"
        if kind == "overlong-title":
            data["title"] = "X" * 120
        resp = client.post("/jobs/merge", data=data)
        assert resp.status_code == 422
        assert len(list_jobs(db)) == 2

    def test_other_user_source_not_mergeable(self, client, db, filestore, test_user):
        first = self._completed(db, filestore, "Prima")
        outside = self._completed(db, filestore, "Seconda", owner_id=test_user.id + 42)
        assert client.get("/jobs/merge/options", params={"job_ids": f"{first.id},{outside.id}"}).status_code == 404
        assert client.post("/jobs/merge", data={"job_ids": f"{first.id},{outside.id}"}).status_code == 404
        assert len(list_jobs(db)) == 2
