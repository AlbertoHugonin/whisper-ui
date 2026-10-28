"""Standalone LLM correction must never mutate the completed Whisper transcript."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.storage.filestore import FileStore
from whisper_ui.worker.standalone_correction import run_standalone_correction


def test_standalone_correction_keeps_original_result(tmp_path):
    storage = FileStore(tmp_path / 'uploads', tmp_path / 'outputs', None)
    job = Job(status=JobStatus.COMPLETED, filename='meeting.wav', llm_context_prompt='Airdass')
    original = TranscriptResult([Segment(0, 2, 'Airdas', 'S1')], language='it', duration=2)
    storage.save_result(job.id, original)
    db = MagicMock()
    db.get_job.return_value = job
    settings = SimpleNamespace(
        llm_correction_available=True,
        ollama_base_url='http://model-router:18084', ollama_model='auto-resident',
        ollama_keep_alive='30m', llm_chunk_size=50, llm_chunk_context=3,
        llm_temperature=0.0, ollama_request_timeout=1950, ollama_think=False,
        llm_api_mode='openai-router',
    )
    runtime = SimpleNamespace(db=db, filestore=storage, settings=settings)

    @contextmanager
    def fake_runtime(job_id):
        assert job_id == job.id
        yield runtime

    class FakeStage:
        def __init__(self, **kwargs):
            assert kwargs['model'] == 'auto-resident'
            assert kwargs['backend'] == 'openai-router'
            assert kwargs['context_prompt'] == 'Airdass'

        def execute(self, context):
            context['transcript_result'].segments[0].text = 'Airdass'
            context['llm_total_chunks'] = 1
            context['llm_failed_chunks'] = 0

        def cleanup(self):
            pass

    with patch('whisper_ui.worker.standalone_correction.build_worker_runtime', side_effect=fake_runtime), patch(
        'whisper_ui.pipeline.llm_correction.LLMCorrectionStage', FakeStage,
    ):
        output = run_standalone_correction(job.id)
    assert output['corrected'] is True
    assert storage.load_result(job.id).segments[0].text == 'Airdas'
    assert storage.load_corrected_result(job.id).segments[0].text == 'Airdass'
