"""The summary model runs separately, preserving the original transcript."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.storage.filestore import FileStore
from whisper_ui.worker.standalone_summary import _split_transcript, run_standalone_summary


def test_long_transcript_split_is_bounded():
    chunks = _split_transcript(('line\n' * 20000), max_chars=1000)
    assert chunks and all(len(c) <= 1000 for c in chunks)
    assert len(chunks) > 5


def test_summary_is_saved_separately(tmp_path):
    filestore = FileStore(tmp_path / 'uploads', tmp_path / 'outputs')
    job = Job(filename='meeting.wav', status=JobStatus.COMPLETED, summary_prompt='Decisioni Airdass')
    filestore.save_result(job.id, TranscriptResult(
        segments=[Segment(0, 2, 'Buongiorno Airdas', 'S1')], language='it', duration=2,
    ))
    db = MagicMock()
    db.get_job.return_value = job
    settings = SimpleNamespace(
        llm_correction_available=True, llm_api_mode='openai-router',
        ollama_base_url='http://router:18084', ollama_request_timeout=60,
        ollama_model='auto-resident', ollama_keep_alive='10m',
    )
    runtime = SimpleNamespace(db=db, filestore=filestore, settings=settings)

    @contextmanager
    def fake_runtime(job_id):
        assert job_id == job.id
        yield runtime

    class FakeClient:
        def __init__(self, url, timeout):
            assert url == 'http://router:18084'

        def chat_json(self, **kwargs):
            assert kwargs['model'] == 'auto-resident'
            assert 'Decisioni Airdass' in kwargs['user']
            return 'Discussa la piattaforma Airdass.'

        def close(self):
            pass

    with patch('whisper_ui.worker.standalone_summary.build_worker_runtime', side_effect=fake_runtime), patch(
        'whisper_ui.pipeline.llm_correction.HttpxRouterClient', FakeClient,
    ):
        output = run_standalone_summary(job.id, '', 'original')
    assert output['chunks'] == 1
    summary = filestore.load_summary(job.id)
    assert summary['source'] == 'original'
    assert summary['text'] == 'Discussa la piattaforma Airdass.'
    assert filestore.load_result(job.id).segments[0].text == 'Buongiorno Airdas'
