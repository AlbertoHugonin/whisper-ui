"""Post-completion merge: correct time offsets, speaker isolation and safety."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.storage.filestore import FileStore
from whisper_ui.worker.join_completed import (
    SOURCES_MANIFEST,
    _build_joined_transcript,
    join_completed_jobs,
)


def test_timestamp_offsets_speaker_namespace_and_immutable_input():
    first = TranscriptResult([Segment(.25, 1.5, 'Prima frase', 'SPEAKER_00')], 'it', 5.0)
    second = TranscriptResult([Segment(.2, 2.0, 'Seconda frase', 'SPEAKER_00')], 'it', 3.0)
    joined = _build_joined_transcript([(first, 5.0), (second, 3.0)])
    assert joined.duration == 8.0
    assert [(x.start, x.end, x.text, x.speaker) for x in joined.segments] == [
        (.25, 1.5, 'Prima frase', 'File 1 · SPEAKER_00'),
        (5.2, 7.0, 'Seconda frase', 'File 2 · SPEAKER_00'),
    ]
    assert first.segments[0].speaker == 'SPEAKER_00'
    assert second.segments[0].start == .2


def test_small_whisperx_end_overshoot_is_clamped_without_reverse_timestamps():
    first = TranscriptResult([Segment(2.5, 3.3, 'Ultima parola')], 'it', 3)
    second = TranscriptResult([Segment(0, 1, 'Nuovo audio')], 'it', 2)
    merged = _build_joined_transcript([(first, 3), (second, 2)])
    assert [(s.start, s.end) for s in merged.segments] == [(2.5, 3.0), (3.0, 4.0)]


def test_joined_transcript_handles_different_languages_and_invalid_time():
    a = TranscriptResult([Segment(0, 1, 'Hello')], 'en', 2)
    b = TranscriptResult([Segment(0, 2, 'Ciao')], 'it', 3)
    assert _build_joined_transcript([(a, 2), (b, 3)]).language == 'unknown'
    a.segments[0].end = 15
    with pytest.raises(ValueError, match='Invalid source segment'):
        _build_joined_transcript([(a, 2), (b, 3)])


def _setup(tmp_path, with_audio=True):
    fs = FileStore(tmp_path / 'uploads', tmp_path / 'outputs')
    jobs = [Job(filename=f'meeting-{n}.wav', status=JobStatus.COMPLETED, owner_id=12,
                duration=3.0) for n in (1, 2)]
    for i, job in enumerate(jobs):
        fs.save_result(job.id, TranscriptResult([
            Segment(.2, 1.0, f'Frase {i}', 'Speaker 1')
        ], 'it', duration=3))
        if with_audio:
            (fs._output_dir / job.id / 'processed.wav').write_bytes(b'RIFF' + bytes([i]) * 100)
    merged = Job(filename='unione.wav', status=JobStatus.QUEUED, owner_id=12)
    directory = fs.prepare_upload_dir(merged.id)
    (directory / SOURCES_MANIFEST).write_text(json.dumps({'version': 1, 'sources': [
        {'job_id': x.id, 'transcript': 'original'} for x in jobs
    ]}))
    db = SimpleNamespace(rows={x.id:x for x in [*jobs, merged]})
    def get_job(jid):return db.rows.get(jid)
    def update_job(job):db.rows[job.id] = job
    db.get_job = get_job
    db.update_job = update_job
    runtime = SimpleNamespace(db=db, filestore=fs)
    @contextmanager
    def fake_runtime(jid):
        assert jid==merged.id
        yield runtime
    return fs, jobs, merged, fake_runtime


def test_background_join_creates_new_transcript_and_processed_audio(tmp_path):
    fs, originals, merged, fake_runtime = _setup(tmp_path)
    def fake_merge(self, ctx, on_progress=None):
        path = fs.get_upload_path(merged.id, 'registrazione-unita.wav')
        path.write_bytes(b'RIFF' + b'12345' * 100)
        return ctx
    with (patch('whisper_ui.worker.join_completed.build_worker_runtime', side_effect=fake_runtime),
          patch('whisper_ui.worker.join_completed.get_audio_duration_seconds',return_value=3),
          patch('whisper_ui.worker.join_completed.AudioMergeStage.execute',fake_merge)):
        result = join_completed_jobs(merged.id)
    assert result == {'job_id': merged.id, 'duration': 6.0, 'segments': 2, 'audio_available': True}
    assert merged.status == JobStatus.COMPLETED
    assert merged.result_path
    assert fs.get_output_artifact(merged.id, 'processed.wav').exists()
    new = fs.load_result(merged.id)
    assert [s.start for s in new.segments] == [.2, 3.2]
    assert new.segments[0].speaker != new.segments[1].speaker
    assert fs.load_result(originals[0].id).segments[0].text == 'Frase 0'
    assert not list(fs.get_upload_path(merged.id, 'part-*.wav').parent.glob('part-*.wav'))


def test_join_with_missing_audio_preserves_combined_text_only(tmp_path):
    fs, _originals, merged, fake_runtime = _setup(tmp_path,with_audio=False)
    with patch('whisper_ui.worker.join_completed.build_worker_runtime',side_effect=fake_runtime):
        result = join_completed_jobs(merged.id)
    assert result['audio_available'] is False
    assert merged.status == JobStatus.COMPLETED
    assert fs.load_result(merged.id).duration == 6
    assert fs.get_output_artifact(merged.id, 'processed.wav') is None


def test_deleted_or_cross_owner_source_marks_new_job_failed(tmp_path):
    fs, originals, merged, fake_runtime = _setup(tmp_path)
    originals[0].owner_id=99
    with (
        patch('whisper_ui.worker.join_completed.build_worker_runtime', side_effect=fake_runtime),
        pytest.raises(ValueError),
    ):
        join_completed_jobs(merged.id)
    assert merged.status == JobStatus.FAILED
    assert fs.load_result(merged.id) is None
